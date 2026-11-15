import base64
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from internal_documents import catalog
from internal_documents.app import create
from internal_documents.oidc import PRODUCT, PRODUCTS, UID, Keys

ISS = "https://identity.example.test/"
AUD = "https://api.docs.onesa.eu"
WEB = "https://docs.example.test"
EDITOR = "12345678-0000-4000-b000-000000000001"
PORTAL = "12345678-0000-4000-b000-000000000002"
MEMBER = "12345678-0000-4000-b000-000000000003"
ADMIN = "12345678-0000-4000-b000-000000000004"
BOOKMARK = "/docs/architecture/ONESA_DESIGN_SYSTEM"


class Journeys(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.signer = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(self.signer.public_key())
        )
        self.jwk.update(kid="fixture", alg="RS256", use="sig")
        self.online = True
        self.cfg = {
            "DOCS_ISSUER": ISS,
            "DOCS_ORIGIN": WEB,
            "DOCS_AUDIENCE": AUD,
            "DOCS_CLIENT_ID": "docs-web",
            "DOCS_CLIENT_SECRET": "in-memory-fixture",
            "DOCS_DATA": self.tmp.name,
            "DOCS_ENVIRONMENT": "staging",
        }
        self.client = self.start()
        self.database = self.root / "documents.sqlite"
        self.attachment = b"Drawing bytes"
        sha = hashlib.sha256(self.attachment).hexdigest()
        (self.root / "objects").mkdir()
        (self.root / "objects" / sha).write_bytes(self.attachment)
        rev = {
            "id": "02",
            "title": "Test pump drawing",
            "body": "Approved content",
            "source_url": "https://github.com/DruHustle/example/blob/"
            + "a" * 40
            + "/drawing",
            "created": "2026-10-15T08:00:00Z",
            "author": EDITOR,
            "approved": True,
            "attachments": [{"id": "drawing42", "sha256": sha, "name": "drawing.pdf"}],
        }
        self.batch = {
            "schema": 1,
            "expected_counts": {
                "documents": 1,
                "revisions": 1,
                "grants": 2,
                "attachments": 1,
            },
            "documents": [
                {
                    "id": "old-42",
                    "product": "farmer",
                    "discipline": "drawings",
                    "author": EDITOR,
                    "bookmark": BOOKMARK,
                    "current": "02",
                    "revisions": [rev],
                    "grants": [
                        {"user_id": EDITOR, "permission": "edit"},
                        {"user_id": MEMBER, "permission": "read"},
                    ],
                }
            ],
        }
        self.upload()

    def upload(self):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps(self.batch))
        return catalog.import_snapshot(
            self.database, manifest, hashlib.sha256(manifest.read_bytes()).hexdigest()
        )

    def remote(self):
        if not self.online:
            raise httpx.ConnectError("fixture offline")
        return {"keys": [self.jwk]}

    def token(
        self, uid=MEMBER, aud=AUD, sub="provider|member", products=None, **overrides
    ):
        now = int(time.time())
        claims = {
            "iss": ISS,
            "sub": sub,
            "iat": now,
            "exp": now + 300,
            "aud": aud,
            UID: uid,
            "https://onesa.eu/claims/roles": ["editor" if uid == EDITOR else "viewer"],
            PRODUCTS: [PRODUCT] if products is None else products,
        }
        claims.update(overrides)
        return jwt.encode(
            claims, self.signer, algorithm="RS256", headers={"kid": "fixture"}
        )

    def exchange(self, code, verifier):
        if not self.online:
            raise httpx.ConnectError("fixture offline")
        got = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self.assertEqual(got, self.auth["code_challenge"][0])
        self.assertEqual(code, "fixture-code")
        return {
            "access_token": self.token(),
            "id_token": self.token(aud="docs-web", nonce=self.auth["nonce"][0]),
        }

    def start(self):
        self.keys = Keys(ISS, self.root, fetch=self.remote)
        client = TestClient(
            create(self.cfg, keys=self.keys, exchange=self.exchange),
            base_url=WEB,
            follow_redirects=False,
        )
        self.addCleanup(client.close)
        return client

    def bearer(self, uid=MEMBER, **kwargs):
        return {"Authorization": "Bearer " + self.token(uid, **kwargs)}

    def initiate(self, destination):
        entry = self.client.get(destination)
        self.assertEqual(entry.status_code, 303)
        start = self.client.get(entry.headers["location"])
        self.assertEqual(
            urlsplit(start.headers["location"]).netloc, "identity.example.test"
        )
        self.auth = parse_qs(urlsplit(start.headers["location"]).query)
        self.assertEqual(self.auth["code_challenge_method"], ["S256"])
        return "/callback?code=fixture-code&state=" + self.auth["state"][0]

    def test_readiness_fails_closed_when_private_database_volume_disappears(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/readyz").status_code, 200)
        self.database.unlink()
        # Liveness is a process check; readiness must not create a blank
        # SQLite database if the persistent volume has disappeared.
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.database.exists())

    def test_search_bounds_query_length_on_api_and_html_surfaces(self):
        headers = self.bearer(MEMBER)
        query = "x" * 257
        api = self.client.get("/api/documents", params={"q": query}, headers=headers)
        self.assertEqual(api.status_code, 422)
        self.assertEqual(api.json()["detail"], "Search query too long")
        page = self.client.get("/", params={"q": query}, headers=headers)
        self.assertEqual(page.status_code, 422)

    def test_portal_offline_bookmark_and_session(self):
        target = BOOKMARK + "?view=spec"
        cb = self.initiate(target)
        other = self.start()
        self.assertEqual(other.get(cb).status_code, 400)
        result = self.client.get(cb)
        self.assertEqual(
            (result.status_code, result.headers["location"]), (303, target)
        )
        self.assertIn("Approved content", self.client.get(target).text)
        self.assertEqual(self.client.get(cb).status_code, 400)
        self.assertEqual(
            self.client.get("/api/attachments/drawing42").content, self.attachment
        )
        self.assertIn('<form method="post" action="/logout">', self.client.get("/").text)
        self.assertEqual(
            self.client.post("/logout", headers={"origin": WEB}).status_code, 200
        )
        self.assertEqual(self.client.get("/api/documents").status_code, 401)

    def test_acl_consistent_on_all_surfaces(self):
        for uid in [PORTAL, ADMIN]:
            headers = self.bearer(uid)
            self.assertEqual(
                self.client.get("/api/documents?q=pump", headers=headers).json(), []
            )
            for route in [
                BOOKMARK,
                "/api/documents/old-42",
                "/api/attachments/drawing42",
            ]:
                self.assertEqual(
                    self.client.get(route, headers=headers).status_code, 404
                )
        row = self.client.get("/api/documents/old-42", headers=self.bearer()).json()
        self.assertEqual(
            (row["id"], row["revision"], row["author"], row["revision_author"]),
            ("old-42", "02", EDITOR, EDITOR),
        )
        self.assertEqual(
            self.client.get(
                "/api/documents", headers=self.bearer(products=[])
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                "/api/documents", headers=self.bearer(aud="farmer-api")
            ).status_code,
            401,
        )

    def test_readers_can_review_approved_revision_history_with_current_permissions(self):
        with catalog.connection(self.database) as db:
            db.executemany(
                "INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)",
                [
                    ("old-42", "01", EDITOR, "2026-09-01", "Former title", "Retired text", "", 1),
                    ("old-42", "draft-secret", EDITOR, "2026-11-01", "Draft", "Private draft", "", 0),
                ],
            )
        url = "/api/documents/old-42/revisions"
        expected = [
            {"id": "02", "author": EDITOR, "created": "2026-10-15T08:00:00Z"},
            {"id": "01", "author": EDITOR, "created": "2026-09-01"},
        ]
        response = self.client.get(url, headers=self.bearer())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        preview = self.client.get(BOOKMARK, headers=self.bearer()).text
        self.assertIn("Approved revision history", preview)
        self.assertIn("2026-09-01", preview)
        self.assertNotIn("draft-secret", preview)
        self.assertNotIn("Retired text", preview)
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers=self.bearer(PORTAL)).status_code, 404)
        with catalog.connection(self.database) as db:
            db.execute("UPDATE documents SET collection='hr' WHERE id='old-42'")
        self.assertEqual(self.client.get(url, headers=self.bearer(products=["engineering-documents"])).status_code, 404)
        self.assertEqual(self.client.get(url, headers=self.bearer()).json(), expected)
        with catalog.connection(self.database) as db:
            db.execute("DELETE FROM grants WHERE principal=?", (MEMBER,))
        self.assertEqual(self.client.get(url, headers=self.bearer()).status_code, 404)

    def test_superseded_revision_attachment_is_not_downloadable(self):
        # The current document's readers must not gain access to objects
        # withdrawn from an earlier approved revision. Preserve the object
        # and provenance for audit; deny the public download route.
        with catalog.connection(self.database) as db:
            db.execute(
                "INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)",
                (
                    "old-42",
                    "03",
                    EDITOR,
                    "2026-11-15T00:00:00Z",
                    "Redacted drawing",
                    "Current approved content without the old attachment",
                    "",
                    1,
                ),
            )
            db.execute(
                "UPDATE documents SET current='03' WHERE id='old-42'"
            )
        for uid in (MEMBER, EDITOR):
            headers = self.bearer(uid)
            metadata = self.client.get("/api/documents/old-42", headers=headers)
            self.assertEqual(metadata.status_code, 200)
            self.assertEqual(metadata.json()["revision"], "03")
            self.assertEqual(metadata.json()["attachments"], [])
            old = self.client.get("/api/attachments/drawing42", headers=headers)
            self.assertEqual(old.status_code, 404)
            self.assertNotIn(self.attachment, old.content)
        with catalog.connection(self.database) as db:
            # Historical bytes and revision attribution remain in private storage.
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM attachments WHERE id='drawing42'"
                ).fetchone()[0],
                1,
            )

    def test_null_permission_grant_never_authorizes_and_import_rolls_back(self):
        from copy import deepcopy

        # Older SQLite schema accepted NULL through CHECK(permission IN ...).
        # Merely joining on principal previously granted read access.
        with catalog.connection(self.database) as db:
            db.execute("UPDATE grants SET permission=NULL WHERE principal=?", (MEMBER,))
        reader = self.bearer(MEMBER)
        self.assertEqual(self.client.get("/api/documents", headers=reader).json(), [])
        self.assertEqual(self.client.get("/api/documents/old-42", headers=reader).status_code, 404)
        self.assertEqual(self.client.get(BOOKMARK, headers=reader).status_code, 404)
        self.assertEqual(self.client.get("/api/attachments/drawing42", headers=reader).status_code, 404)
        self.assertEqual(self.client.get("/api/documents/old-42", headers=self.bearer(EDITOR)).status_code, 200)

        # An owner-approved import must reject invalid permissions atomically,
        # rather than trusting SQLite's CHECK constraint with NULL.
        new = deepcopy(self.batch["documents"][0])
        new["id"] = "bad-grant-copy"
        new["bookmark"] = "/docs/permissions/check"
        new["revisions"][0]["attachments"][0]["id"] = "grant-check-attachment"
        new["grants"][1]["permission"] = None
        self.batch["documents"] = [new]
        with self.assertRaisesRegex(ValueError, "Invalid document permission"):
            self.upload()
        with catalog.connection(self.database) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM documents").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM imports").fetchone()[0], 1)

    def test_editor_draft_vs_reader(self):
        payload = {
            "base_revision": "02",
            "body": "Pending changes",
            "title": "New drawing",
        }
        for uid, expect in [(MEMBER, 403), (EDITOR, 201)]:
            res = self.client.post(
                "/api/documents/old-42/drafts", json=payload, headers=self.bearer(uid)
            )
            self.assertEqual(res.status_code, expect)
        self.assertEqual(
            self.client.get("/api/documents/old-42", headers=self.bearer()).json()[
                "body"
            ],
            "Approved content",
        )
        payload["base_revision"] = "01"
        self.assertEqual(
            self.client.post(
                "/api/documents/old-42/drafts",
                json=payload,
                headers=self.bearer(EDITOR),
            ).status_code,
            409,
        )

    def test_draft_json_authentication_and_body_limit_precede_parse(self):
        route = "/api/documents/old-42/drafts"
        large = b" " * 2_100_001
        anonymous = self.client.post(
            route,
            content=large,
            headers={"Content-Type": "application/json", "Origin": WEB},
        )
        self.assertEqual(anonymous.status_code, 401)
        signed = self.client.post(
            route,
            content=large,
            headers={**self.bearer(EDITOR), "Content-Type": "application/json"},
        )
        self.assertEqual(signed.status_code, 413)
        malformed = self.client.post(
            route,
            content=b"{not valid json}",
            headers={**self.bearer(EDITOR), "Content-Type": "application/json"},
        )
        self.assertEqual(malformed.status_code, 422)
        unsupported = self.client.post(
            route,
            content=b"plain",
            headers={**self.bearer(EDITOR), "Content-Type": "text/plain"},
        )
        self.assertEqual(unsupported.status_code, 415)

    def test_issuer_outage_restart_expiry_return(self):
        headers = self.bearer()
        self.assertEqual(
            self.client.get("/api/documents", headers=headers).status_code, 200
        )
        self.online = False
        new = self.start()
        self.assertEqual(new.get("/api/documents", headers=headers).status_code, 200)
        self.assertEqual(
            new.get(
                "/api/documents", headers=self.bearer(exp=int(time.time()) - 1)
            ).status_code,
            401,
        )
        cb = self.initiate(BOOKMARK)
        fail = self.client.get(cb)
        self.assertEqual(fail.status_code, 503)
        self.assertIn("Retry sign-in for this document", fail.text)
        self.online = True
        result = self.client.get(self.initiate(BOOKMARK))
        self.assertEqual(result.headers["location"], BOOKMARK)
        self.online = False
        self.keys.file.unlink()
        self.assertEqual(
            self.start().get("/api/documents", headers=headers).status_code, 503
        )

    def test_corrupt_jwks_cache_and_invalid_provider_reply_fail_closed(self):
        # Corrupt but valid JSON on disk must never yield a 500 error or
        # authorize a token, even when the identity provider is unavailable.
        self.online = False
        self.keys.file.write_text('{"time":"not-a-number","keys":[]}')
        response = self.client.get("/api/documents", headers=self.bearer())
        self.assertEqual(response.status_code, 503)

        # A malformed JWKS provider response is also an unavailable-key
        # condition, not an unhandled exception or an accepted signature.
        self.keys.last_attempt = -1e20
        self.keys.fetch = lambda: {"keys": None}
        response = self.client.get("/api/documents", headers=self.bearer())
        self.assertEqual(response.status_code, 503)

    def test_malformed_matching_cached_jwk_fails_closed_and_recovers(self):
        # A matching kid with an invalid value previously raised unhandled
        # AttributeError/TypeError instead of treating a damaged cache as an
        # upstream key outage. An available issuer must repair the cache.
        headers = self.bearer()
        for invalid in (None, {"kty": "RSA", "n": None, "e": "AQAB"}):
            with self.subTest(key=invalid):
                self.keys.file.write_text(json.dumps({
                    "time": time.time(), "keys": {"fixture": invalid}
                }))
                self.keys.last_attempt = -1e20
                self.online = False
                self.assertEqual(
                    self.client.get("/api/documents", headers=headers).status_code, 503
                )
                self.keys.last_attempt = -1e20
                self.online = True
                self.assertEqual(
                    self.client.get("/api/documents", headers=headers).status_code, 200
                )
                repaired = json.loads(self.keys.file.read_text())
                self.assertEqual(repaired["keys"]["fixture"]["kty"], "RSA")

    def test_import_replay_and_atomic_conflict(self):
        self.assertEqual(self.upload(), {"already_imported": True})
        self.batch["documents"][0]["id"] = "another"
        self.batch["documents"][0]["bookmark"] = "/docs/new/page"
        self.batch["documents"][0]["revisions"][0]["attachments"][0]["sha256"] = (
            "1" * 64
        )
        with self.assertRaises(ValueError):
            self.upload()
        with catalog.connection(self.database) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM documents").fetchone()[0], 1
            )
            self.assertEqual(
                db.execute("SELECT count(*) FROM imports").fetchone()[0], 1
            )

    def test_provenance_rejects_mutable_github_fqdn_and_www_urls(self):
        from copy import deepcopy

        # DNS resolves github.com. to the same host as github.com;
        # www.github.com redirects there. Neither may bypass the
        # immutable-commit rule for approved source attribution.
        second = deepcopy(self.batch["documents"][0])
        second["id"] = "github-provenance-check"
        second["bookmark"] = "/docs/source/check"
        second["revisions"][0]["attachments"][0]["id"] = "source-check-attachment"
        self.batch["documents"] = [second]
        for url in (
            "https://github.com./DruHustle/example/blob/main/drawing",
            "https://www.github.com/DruHustle/example/blob/main/drawing",
        ):
            with self.subTest(url=url):
                second["revisions"][0]["source_url"] = url
                with self.assertRaisesRegex(ValueError, "Git provenance must name an immutable commit"):
                    self.upload()
                with catalog.connection(self.database) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM documents").fetchone()[0], 1)
        second["revisions"][0]["source_url"] = (
            "https://github.com./DruHustle/example/blob/" + "a" * 40 + "/drawing"
        )
        self.assertEqual(self.upload()["documents"], 1)

    def test_snapshot_import_rejects_corrupt_existing_attachment_object(self):
        from copy import deepcopy

        # A second reconciled document may reference bytes already stored.
        # A matching digest key is insufficient if stored object bytes differ.
        second = deepcopy(self.batch["documents"][0])
        second["id"] = "second-doc"
        second["bookmark"] = "/docs/new/copy"
        second["revisions"][0]["attachments"][0]["id"] = "drawing-copy"
        self.batch["documents"] = [second]
        with catalog.connection(self.database) as db:
            db.execute("UPDATE objects SET bytes=?", (b"corrupted bytes",))
        with self.assertRaisesRegex(ValueError, "Existing attachment object checksum mismatch"):
            self.upload()
        with catalog.connection(self.database) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM documents").fetchone()[0], 1
            )
            self.assertEqual(
                db.execute("SELECT count(*) FROM imports").fetchone()[0], 1
            )

    def test_draft_form_rejects_invalid_encoding_and_ambiguous_fields(self):
        headers = {
            **self.bearer(EDITOR),
            "Origin": WEB,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        fields = b"base_revision=02&title=Drawing&body="
        for body in [fields + b"draft&extra=value", fields + b"draft&body=other",
                     fields + b"\xff", fields + b"%FF"]:
            with self.subTest(body=body):
                response = self.client.post("/edit/old-42", content=body, headers=headers)
                self.assertEqual(response.status_code, 422)
        valid = self.client.post("/edit/old-42", content=fields + b"na%C3%AFve",
                                 headers=headers)
        self.assertEqual(valid.status_code, 200)
        with catalog.connection(self.database) as db:
            drafts = db.execute("SELECT body FROM revisions WHERE approved=0").fetchall()
        self.assertEqual([row["body"] for row in drafts], ["naïve"])

    def test_expired_editor_preserves_unsaved_form_and_role_downgrade(self):
        payload = {
            "base_revision": "02",
            "title": "Draft <edit>",
            "body": "Customer changes",
        }
        response = self.client.post(
            "/edit/old-42", data=payload, headers={"Origin": WEB}
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Customer changes", response.text)
        self.assertIn("Draft &lt;edit&gt;", response.text)
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(response.headers["referrer-policy"], "same-origin")
        headers = self.bearer(EDITOR, **{"https://onesa.eu/claims/roles": ["viewer"]})
        self.assertEqual(
            self.client.post(
                "/api/documents/old-42/drafts", json=payload, headers=headers
            ).status_code,
            403,
        )

    def test_environment_guard_fails_closed_for_unknown_or_missing_values(self):
        # Without an explicit staging opt-in, a forgotten/mistyped deployment
        # flag must never relax the canonical production hostname restriction.
        for env in (None, "", "Production", "prod", "development"):
            config = dict(self.cfg)
            if env is None:
                config.pop("DOCS_ENVIRONMENT")
            else:
                config["DOCS_ENVIRONMENT"] = env
            with self.subTest(environment=env):
                with self.assertRaisesRegex(ValueError, "DOCS_ENVIRONMENT|dedicated internal origin"):
                    create(config)

        # The guard also applies when an unknown mode carries apparently valid
        # production hostnames, to avoid silent policy bypass via typos.
        canonical = dict(
            self.cfg, DOCS_ORIGIN="https://docs.internal.onesa.eu",
            DOCS_ISSUER="https://auth.onesa.eu", DOCS_ENVIRONMENT="prod"
        )
        with self.assertRaisesRegex(ValueError, "DOCS_ENVIRONMENT"):
            create(canonical)

        # An explicit staging flag must not allow a staging issuer/client to
        # occupy the canonical production hostname and intercept real users.
        staging_on_production = dict(
            self.cfg,
            DOCS_ORIGIN="https://docs.internal.onesa.eu",
            DOCS_ISSUER="https://auth.onesa.eu",
            DOCS_ENVIRONMENT="staging",
        )
        with self.assertRaisesRegex(ValueError, "separate nonproduction hostname"):
            create(staging_on_production)

    def test_permanent_public_and_internal_origins(self):
        for public in (
            "https://docs.onesa.eu",
            "https://DOCS.ONESA.EU.",
            "https://docs.onesa.eu:443",
        ):
            with self.assertRaisesRegex(ValueError, "public documents"):
                create(dict(self.cfg, DOCS_ORIGIN=public))
        with self.assertRaisesRegex(ValueError, "dedicated internal origin"):
            create(dict(self.cfg, DOCS_ENVIRONMENT="production"))
        create(
            dict(
                self.cfg,
                DOCS_ORIGIN="https://docs.internal.onesa.eu",
                DOCS_ISSUER="https://auth.onesa.eu",
                DOCS_ENVIRONMENT="production",
            )
        )

    def test_legacy_engineering_grant_never_reads_hr_even_with_document_acl(self):
        from copy import deepcopy

        hr = deepcopy(self.batch["documents"][0])
        hr.update(
            id="hr-manual",
            collection="hr",
            product="company",
            bookmark="/docs/hr/handbook",
        )
        hr["revisions"][0]["attachments"][0]["id"] = "hr-attachment"
        self.batch["documents"] = [hr]
        self.upload()
        legacy = self.bearer(EDITOR, products=["engineering-documents"])
        self.assertEqual(
            len(self.client.get("/api/documents", headers=legacy).json()), 1
        )
        for route in [
            "/docs/hr/handbook",
            "/api/documents/hr-manual",
            "/api/attachments/hr-attachment",
        ]:
            self.assertEqual(self.client.get(route, headers=legacy).status_code, 404)
            self.assertEqual(
                self.client.get(route, headers=self.bearer(EDITOR)).status_code, 200
            )
        self.assertEqual(
            self.client.post(
                "/api/documents/hr-manual/drafts",
                headers=legacy,
                json={"base_revision": "02", "title": "Test", "body": "Draft"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                "/docs/hr/handbook", headers=self.bearer(PORTAL)
            ).status_code,
            404,
        )
        with self.assertRaisesRegex(ValueError, "hostname is retired"):
            create(dict(self.cfg, DOCS_ORIGIN="https://docs.engineering.onesa.eu"))

    def test_unsafe_return_and_unsigned_input(self):
        for val in [
            "https://other.test/a",
            "//other.test/a",
            "/docs/%2f%2fexternal",
            "/docs/a\\b",
            "/admin",
        ]:
            self.assertEqual(
                self.client.get("/sign-in", params={"next": val}).status_code, 400
            )
        self.assertEqual(
            self.client.get(
                "/api/documents", headers={"Authorization": "Bearer broken"}
            ).status_code,
            401,
        )


if __name__ == "__main__":
    unittest.main()
