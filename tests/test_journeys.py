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

    def test_permanent_public_and_internal_origins(self):
        for public in (
            "https://docs.onesa.eu",
            "https://DOCS.ONESA.EU.",
            "https://docs.onesa.eu:443",
        ):
            with self.assertRaisesRegex(ValueError, "public Developer Docs"):
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
        with self.assertRaisesRegex(ValueError, "redirect-only"):
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
