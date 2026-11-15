import base64
import hashlib
import html
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from uuid import uuid4

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from . import catalog
from .oidc import PRODUCT, PRODUCTS, Keys, KeysUnavailable, account, https_origin

SESSION = "__Host-docs-session"
STATE = "__Host-docs-challenge"


def hash_value(value):
    return hashlib.sha256(value.encode()).hexdigest()


def safe_destination(value):
    u = urlsplit(value)
    if (
        u.scheme
        or u.netloc
        or value.startswith("//")
        or not value.startswith("/")
        or any(c in value for c in ("\\", "\r", "\n", "#"))
        or "%" in u.path
        or (u.path != "/" and not u.path.startswith("/docs/"))
        or len(value) > 4096
    ):
        raise HTTPException(400, "Invalid document return address")
    return value


def page(title, body):
    return HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link rel="icon" href="/brand/onesa-docs.svg"><title>'
        + html.escape(title)
        + "</title><style>:root{color-scheme:light dark}body{max-width:66rem;margin:3rem auto;padding:0 1.25rem;font:1rem/1.6 system-ui}h1{line-height:1.15}pre{white-space:pre-wrap;overflow-wrap:anywhere}article,li{margin:1rem 0}small{display:block}input{font:inherit}</style>"
        '<header><a href="/">Onesa Internal Documents</a></header><main>'
        + body
        + "</main></html>"
    )


class Draft(BaseModel):
    base_revision: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(max_length=2_000_000)


def create(settings=None, *, keys=None, exchange=None, clock=time.time):
    cfg = os.environ if settings is None else settings
    issuer = https_origin(cfg["DOCS_ISSUER"]) + "/"
    origin = https_origin(cfg["DOCS_ORIGIN"])
    if urlsplit(origin).hostname.rstrip(".").lower() == "docs.onesa.eu":
        raise ValueError("docs.onesa.eu is reserved for public documents")
    if urlsplit(origin).hostname.rstrip(".").lower() == "docs.engineering.onesa.eu":
        raise ValueError("The legacy engineering hostname is retired")
    # An unset or mistyped environment must not silently disable production
    # origin/issuer restrictions. Only explicit staging enables nonproduction URLs.
    environment = cfg.get("DOCS_ENVIRONMENT", "production")
    if environment not in ("production", "staging"):
        raise ValueError("DOCS_ENVIRONMENT must be production or staging")
    if environment == "production" and (
        origin != "https://docs.internal.onesa.eu" or issuer != "https://auth.onesa.eu/"
    ):
        raise ValueError(
            "Production Internal Documents requires the dedicated internal origin and Onesa Identity issuer"
        )
    if environment == "staging" and urlsplit(origin).hostname.rstrip(".").lower() == "docs.internal.onesa.eu":
        raise ValueError(
            "Staging Internal Documents must use a separate nonproduction hostname"
        )
    audience = cfg["DOCS_AUDIENCE"]
    client = cfg["DOCS_CLIENT_ID"]
    secret = cfg["DOCS_CLIENT_SECRET"]
    if not all([audience, client, secret]) or audience == client:
        raise ValueError(
            "Configure a distinct Docs API audience and confidential web client"
        )
    directory = Path(cfg["DOCS_DATA"]).resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # DOCS_DATA can be an existing mounted volume; mkdir(mode=...) does not
    # tighten its permissions when it already exists. This directory holds
    # access tokens, ACLs, drafts and issuer signing-key cache files.
    try:
        os.chmod(directory, 0o700)
        if directory.stat().st_mode & 0o077:
            raise OSError("private directory remains accessible to group/other")
    except OSError as exc:
        raise RuntimeError("Cannot secure private Documents data directory") from exc
    database = directory / "documents.sqlite"
    if database.is_symlink():
        raise RuntimeError("Private Documents database must not be a symlink")
    # The private mount may disappear across a production restart. Never
    # silently bootstrap a replacement empty catalog on the container disk:
    # an empty store could look healthy while documents and ACLs are gone.
    if environment == "production":
        if not database.is_file() or database.stat().st_size == 0:
            raise RuntimeError("Production Documents store missing; restore the private volume")
        try:
            with catalog.connection(database) as db:
                existing = {
                    row[0] for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if not {
                    "documents", "revisions", "grants",
                    "objects", "attachments", "imports",
                }.issubset(existing):
                    raise RuntimeError("Production Documents schema missing; restore the private volume")
        except sqlite3.Error as exc:
            raise RuntimeError("Production Documents store invalid; restore the private volume") from exc
    catalog.initialize(database)
    # Also restrict an existing database file. The containing directory is
    # private, but file mode is defense in depth for backups and mount changes.
    try:
        os.chmod(database, 0o600)
        if database.stat().st_mode & 0o077:
            raise OSError("private database remains accessible to group/other")
    except OSError as exc:
        raise RuntimeError("Cannot secure private Documents database") from exc
    keys = keys or Keys(issuer, directory, clock=clock)
    with catalog.connection(database) as db:
        db.executescript("""CREATE TABLE IF NOT EXISTS challenges(
            state TEXT PRIMARY KEY,binding TEXT NOT NULL,verifier TEXT NOT NULL, nonce TEXT NOT NULL,
            destination TEXT NOT NULL,expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,access_token TEXT NOT NULL,expires INTEGER NOT NULL);""")
        try:
            catalog.verify_schema(db)
        except sqlite3.DatabaseError as exc:
            raise RuntimeError("Private Documents schema invalid; restore the private volume") from exc
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "same-origin",
                "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            }
        )
        return response

    def identity(request):
        auth = request.headers.get("authorization", "")
        token = None
        if auth:
            if not auth.startswith("Bearer "):
                raise HTTPException(401, "Bearer token required")
            token = auth[7:]
        else:
            with catalog.connection(database) as db:
                s = db.execute(
                    "SELECT access_token FROM sessions WHERE id=? AND expires>?",
                    (hash_value(request.cookies.get(SESSION, "")), clock()),
                ).fetchone()
                if s:
                    token = s[0]
        if not token:
            raise HTTPException(401, "Sign in to Internal Documents")
        try:
            claims = keys.verify(token, audience)
            request.state.engineering_only = (
                PRODUCT not in claims.get(PRODUCTS, [])
                if isinstance(claims.get(PRODUCTS), list)
                else True
            )
            request.state.docs_roles = claims.get("https://onesa.eu/claims/roles", [])
            return account(claims)
        except jwt.PyJWTError:
            raise HTTPException(401, "Session expired or invalid")
        except PermissionError:
            raise HTTPException(403, "Product access required")
        except KeysUnavailable:
            raise HTTPException(503, "Signing keys temporarily unavailable")

    def web_identity(request):
        try:
            return identity(request)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            target = request.url.path + (
                "?" + request.url.query if request.url.query else ""
            )
            return RedirectResponse(
                "/sign-in?" + urlencode({"next": safe_destination(target)}), 303
            )

    @app.get("/brand/onesa-docs.svg")
    def icon():
        return Response(
            Path(__file__).with_name("onesa-docs.svg").read_bytes(),
            media_type="image/svg+xml",
        )

    @app.get("/healthz")
    def health():
        return {"service": "internal-documents"}

    @app.get("/readyz")
    def readiness():
        # Liveness must stay independent from the private store. Readiness,
        # however, must fail if a persistent volume disappears or SQLite is
        # unavailable; never let SQLite silently create an empty replacement.
        if database.is_symlink() or not database.is_file():
            raise HTTPException(503, "Document store unavailable")
        try:
            with catalog.connection(database, timeout=1) as db:
                catalog.verify_schema(db)
                db.execute("BEGIN IMMEDIATE")
                db.rollback()
        except sqlite3.Error:
            raise HTTPException(503, "Document store unavailable")
        return {"service": "internal-documents", "ready": True}

    @app.get("/sign-in")
    def sign_in(next: str = "/"):
        target = safe_destination(next)
        state, binding, verifier, nonce = [secrets.token_urlsafe(36) for _ in range(4)]
        with catalog.connection(database) as db:
            db.execute("DELETE FROM challenges WHERE expires<=?", (clock(),))
            db.execute("DELETE FROM sessions WHERE expires<=?", (clock(),))
            db.execute(
                "INSERT INTO challenges VALUES(?,?,?,?,?,?)",
                (
                    hash_value(state),
                    hash_value(binding),
                    verifier,
                    nonce,
                    target,
                    clock() + 600,
                ),
            )
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        url = (
            issuer
            + "authorize?"
            + urlencode(
                {
                    "client_id": client,
                    "response_type": "code",
                    "redirect_uri": origin + "/callback",
                    "scope": "openid",
                    "audience": audience,
                    "state": state,
                    "nonce": nonce,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )
        response = RedirectResponse(url, 303)
        response.set_cookie(
            STATE, binding, secure=True, httponly=True, samesite="lax", max_age=600
        )
        return response

    def get_tokens(code, verifier):
        data = {
            "grant_type": "authorization_code",
            "client_id": client,
            "client_secret": secret,
            "redirect_uri": origin + "/callback",
            "code": code,
            "code_verifier": verifier,
        }
        res = httpx.post(
            issuer + "oauth/token", data=data, timeout=8, follow_redirects=False
        )
        res.raise_for_status()
        return res.json()

    @app.get("/callback")
    def callback(request: Request, state: str, code: str = ""):
        with catalog.connection(database) as db:
            tx = db.execute(
                "DELETE FROM challenges WHERE state=? AND binding=? AND expires>? RETURNING *",
                (
                    hash_value(state),
                    hash_value(request.cookies.get(STATE, "")),
                    clock(),
                ),
            ).fetchone()
        if not tx:
            raise HTTPException(400, "Return to the document to start sign-in again")
        retry = (
            '<p>Sign-in could not finish.</p><a href="/sign-in?'
            + html.escape(urlencode({"next": tx["destination"]}))
            + '">Retry sign-in for this document</a>'
        )
        try:
            if not code:
                return page("Sign-in interrupted", retry)
            tokens = (exchange or get_tokens)(code, tx["verifier"])
            # Treat malformed upstream JSON exactly like an unavailable token
            # response. A non-object (or non-string token fields) previously
            # raised TypeError here and escaped the outage response as HTTP 500.
            if (
                not isinstance(tokens, dict)
                or not isinstance(tokens.get("id_token"), str)
                or not tokens["id_token"]
                or not isinstance(tokens.get("access_token"), str)
                or not tokens["access_token"]
            ):
                raise ValueError("Invalid OIDC token response")
            profile = keys.verify(tokens["id_token"], client)
            access = keys.verify(tokens["access_token"], audience)
            if (
                profile.get("nonce") != tx["nonce"]
                or profile["sub"] != access["sub"]
                or profile.get("azp", client) != client
            ):
                raise jwt.InvalidTokenError("OIDC response mismatch")
            if (
                isinstance(profile["aud"], list)
                and len(profile["aud"]) > 1
                and profile.get("azp") != client
            ):
                raise jwt.InvalidTokenError("OIDC authorized party missing")
            account(access)
        except PermissionError:
            raise HTTPException(403, "Internal Documents access required")
        except (KeyError, ValueError, httpx.HTTPError, jwt.PyJWTError, KeysUnavailable):
            result = page("Retry sign-in", retry)
            result.status_code = 503
            return result
        session = secrets.token_urlsafe(48)
        with catalog.connection(database) as db:
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?)",
                (hash_value(session), tokens["access_token"], access["exp"]),
            )
        result = RedirectResponse(tx["destination"], 303)
        result.delete_cookie(STATE, secure=True, httponly=True, samesite="lax")
        result.set_cookie(
            SESSION,
            session,
            secure=True,
            httponly=True,
            samesite="lax",
            max_age=max(0, int(access["exp"] - clock())),
        )
        return result

    def bounded_search_query(query):
        if len(query) > 256:
            raise HTTPException(422, "Search query too long")
        return query

    @app.get("/api/documents")
    def find(request: Request, q: str = ""):
        uid = identity(request)
        q = bounded_search_query(q)
        with catalog.connection(database) as db:
            return catalog.search(db, uid, q, request.state.engineering_only)

    def load_doc(request, uid, k, bookmark=False):
        with catalog.connection(database) as db:
            db.execute("BEGIN")
            result = catalog.document(
                db, uid, k, bookmark, request.state.engineering_only
            )
        if not result:
            raise HTTPException(404, "Document unavailable")
        return result

    @app.get("/api/documents/{key}")
    def metadata(request: Request, key: str):
        return load_doc(request, identity(request), key)

    @app.get("/api/documents/{key}/revisions")
    def revisions(request: Request, key: str):
        return load_doc(request, identity(request), key)["revisions"]

    @app.get("/api/attachments/{key}")
    def attachment(request: Request, key: str):
        uid = identity(request)
        with catalog.connection(database) as db:
            row = catalog.attachment(db, uid, key, request.state.engineering_only)
        if not row:
            raise HTTPException(404, "Attachment unavailable")
        return Response(
            row["bytes"],
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(row["name"], safe="")
            },
        )

    def may_edit(request):
        roles = request.state.docs_roles
        return isinstance(roles, list) and any(r in ("editor", "admin") for r in roles)

    def store_draft(request, key, payload):
        if (
            not request.headers.get("authorization")
            and request.headers.get("origin") != origin
        ):
            raise HTTPException(403, "Invalid Origin")
        uid = identity(request)
        with catalog.connection(database) as db:
            db.execute("BEGIN IMMEDIATE")
            rec = catalog.document(
                db, uid, key, engineering_only=request.state.engineering_only
            )
            if not rec:
                raise HTTPException(404, "Document unavailable")
            if rec["permission"] != "edit" or not may_edit(request):
                raise HTTPException(403, "Document editor required")
            if rec["revision"] != payload.base_revision:
                raise HTTPException(409, "Published revision changed")
            rid = str(uuid4())
            db.execute(
                """INSERT INTO revisions VALUES(?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),?,?,?,0)""",
                (key, rid, uid, payload.title, payload.body, ""),
            )
            return {"id": rid, "document": key, "approved": False}

    @app.post("/api/documents/{key}/drafts", status_code=201)
    async def draft(request: Request, key: str):
        # Authenticate before reading untrusted JSON; FastAPI's automatic body
        # parsing would otherwise buffer an unlimited anonymous request.
        if (
            not request.headers.get("authorization")
            and request.headers.get("origin") != origin
        ):
            raise HTTPException(403, "Invalid Origin")
        identity(request)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(415, "Expected JSON")
        chunks = bytearray()
        async for chunk in request.stream():
            if len(chunks) + len(chunk) > 2_100_000:
                raise HTTPException(413, "Draft too large")
            chunks.extend(chunk)
        try:
            data = json.loads(bytes(chunks))
            if not isinstance(data, dict):
                raise ValueError("Draft must be an object")
            payload = Draft(**data)
        except (ValueError, TypeError):
            raise HTTPException(422, "Invalid draft")
        return store_draft(request, key, payload)

    @app.post("/edit/{key}")
    async def save_form(request: Request, key: str):
        if request.headers.get("origin") != origin:
            raise HTTPException(403, "Invalid Origin")
        if (
            request.headers.get("content-type", "").split(";")[0]
            != "application/x-www-form-urlencoded"
        ):
            raise HTTPException(415, "Expected form data")
        chunks = bytearray()
        async for chunk in request.stream():
            if len(chunks) + len(chunk) > 2_100_000:
                raise HTTPException(413, "Draft too large")
            chunks.extend(chunk)
        try:
            form = parse_qs(
                chunks.decode("utf-8"), max_num_fields=3, keep_blank_values=True,
                errors="strict",
            )
            payload = Draft(
                **{k: form[k][0] for k in ("base_revision", "title", "body")}
            )
        except (KeyError, ValueError):
            raise HTTPException(422, "Invalid draft")
        try:
            result = store_draft(request, key, payload)
        except HTTPException as failure:
            if failure.status_code not in (401, 503):
                raise
            # Keep submitted work only in this private no-store response. A new
            # tab can establish a fresh host-only session before resubmission.
            response = page(
                "Draft awaiting sign-in",
                "<h1>Draft awaiting sign-in</h1>"
                '<p>Your changes are still in this tab. <a target="_blank" rel="noopener" href="/sign-in">Sign in in another tab</a>, then save again.</p>'
                '<form method="post" action="/edit/'
                + quote(key, safe="")
                + '"><input type="hidden" name="base_revision" value="'
                + html.escape(payload.base_revision, quote=True)
                + '">'
                '<label>Title <input name="title" value="'
                + html.escape(payload.title, quote=True)
                + '"></label>'
                '<label>Document text <textarea name="body">'
                + html.escape(payload.body)
                + "</textarea></label><button>Save draft</button></form>",
            )
            response.status_code = failure.status_code
            return response
        return page(
            "Draft saved",
            "<h1>Draft saved</h1><p>Revision "
            + html.escape(result["id"])
            + " is awaiting review.</p>",
        )

    @app.post("/logout")
    def logout(request: Request):
        if request.headers.get("origin") != origin:
            raise HTTPException(403, "Invalid Origin")
        with catalog.connection(database) as db:
            db.execute(
                "DELETE FROM sessions WHERE id=?",
                (hash_value(request.cookies.get(SESSION, "")),),
            )
        result = page("Signed out", "<h1>Signed out of Internal Documents</h1>")
        result.delete_cookie(SESSION, secure=True, httponly=True, samesite="lax")
        return result

    @app.get("/")
    def index(request: Request, q: str = ""):
        uid = web_identity(request)
        if isinstance(uid, Response):
            return uid
        q = bounded_search_query(q)
        with catalog.connection(database) as db:
            rows = catalog.search(db, uid, q, request.state.engineering_only)
        entries = "".join(
            '<li><a href="'
            + html.escape(row["bookmark"])
            + '">'
            + html.escape(row["title"])
            + "</a><small>"
            + html.escape(
                row["collection"] + " · " + row["product"] + " · " + row["discipline"]
            )
            + "</small></li>"
            for row in rows
        )
        return page(
            "Internal Documents",
            '<h1>Internal Documents</h1><form method="post" action="/logout"><button>Sign out</button></form>'
            '<form><label>Search documents <input name="q" value="'
            + html.escape(q, quote=True)
            + '"></label><button>Search</button></form>'
            + (
                "<ul>" + entries + "</ul>"
                if entries
                else "<p>No matching documents.</p>"
            ),
        )

    @app.get("/docs/{bookmark:path}")
    def preview(request: Request, bookmark: str):
        uid = web_identity(request)
        if isinstance(uid, Response):
            return uid
        rec = load_doc(request, uid, "/docs/" + bookmark, True)
        history = "".join(
            "<li>Revision " + html.escape(revision["id"])
            + (" (current)" if revision["id"] == rec["revision"] else "")
            + "<small>" + html.escape(revision["created"]) + " · "
            + html.escape(revision["author"]) + "</small></li>"
            for revision in rec["revisions"]
        )
        links = "".join(
            '<li><a href="/api/attachments/'
            + quote(a["id"], safe="")
            + '">'
            + html.escape(a["name"])
            + "</a></li>"
            for a in rec["attachments"]
        )
        provenance = (
            (
                '<p><a rel="noreferrer" href="'
                + html.escape(rec["source"], quote=True)
                + '">Approved source revision</a></p>'
            )
            if rec["source"]
            else ""
        )
        editor = ""
        if rec["permission"] == "edit" and may_edit(request):
            editor = (
                '<form method="post" action="/edit/'
                + quote(rec["id"], safe="")
                + '"><h2>Edit a draft</h2>'
                '<input type="hidden" name="base_revision" value="'
                + html.escape(rec["revision"], quote=True)
                + '">'
                '<label>Title <input name="title" required value="'
                + html.escape(rec["title"], quote=True)
                + '"></label>'
                '<label>Document text <textarea name="body" rows="12">'
                + html.escape(rec["body"])
                + "</textarea></label>"
                "<button>Save draft</button></form>"
            )
        return page(
            rec["title"],
            "<article><small>"
            + html.escape(
                rec["collection"] + " · " + rec["product"] + " · " + rec["discipline"]
            )
            + "</small><h1>"
            + html.escape(rec["title"])
            + "</h1><small>Revision "
            + html.escape(rec["revision"])
            + "</small><pre>"
            + html.escape(rec["body"])
            + "</pre>"
            + provenance
            + "<ul>"
            + links
            + "</ul><details><summary>Approved revision history</summary><ol>"
            + history
            + "</ol></details></article>"
            + editor,
        )

    return app
