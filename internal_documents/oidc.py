"""Confidential web-client PKCE and audience-bound access-token verification."""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import jwt

PRODUCT = "internal-documents"
LEGACY_PRODUCT = "engineering-documents"
UID = "https://onesa.eu/claims/user_id"
PRODUCTS = "https://onesa.eu/claims/products"


class KeysUnavailable(Exception):
    pass


class Keys:
    def __init__(self, issuer, directory, fetch=None, clock=time.time):
        self.issuer = issuer
        self.directory = Path(directory)
        self.file = self.directory / (
            "jwks-" + hashlib.sha256(issuer.encode()).hexdigest() + ".json"
        )
        self.fetch = fetch or self.remote
        self.clock = clock
        self.last_attempt = -1e20
        self.lock = Lock()

    def remote(self):
        response = httpx.get(
            self.issuer + ".well-known/jwks.json", timeout=4, follow_redirects=False
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def decode_signing_key(value):
        # A partly written or corrupted JSON cache can contain a matching kid
        # with an invalid value. Never let the JWT library's AttributeError or
        # TypeError escape as a 500 from a protected document request.
        try:
            if not isinstance(value, dict) or value.get("kty") != "RSA":
                raise ValueError("Invalid cached RSA signing key")
            return jwt.PyJWK.from_dict(value, algorithm="RS256").key
        except (AttributeError, TypeError, ValueError, KeyError, jwt.PyJWTError) as exc:
            raise KeysUnavailable() from exc

    def key(self, kid):
        with self.lock:
            now = self.clock()
            try:
                cached = json.loads(self.file.read_bytes())
                if (
                    not isinstance(cached, dict)
                    or not isinstance(cached.get("time"), (int, float))
                    or not isinstance(cached.get("keys"), dict)
                ):
                    raise ValueError("Invalid local JWKS cache")
            except (OSError, ValueError):
                cached = {"time": 0, "keys": {}}
            age = now - cached["time"]
            if 0 <= age < 300 and kid in cached["keys"]:
                try:
                    return self.decode_signing_key(cached["keys"][kid])
                except KeysUnavailable:
                    # A malformed cache is not valid signing evidence. Try the
                    # upstream JWKS again rather than throwing a server error.
                    cached, age = {"time": 0, "keys": {}}, now
            if now - self.last_attempt >= 30:
                self.last_attempt = now
                try:
                    jwks = self.fetch()
                    keys = {
                        j["kid"]: j
                        for j in jwks["keys"]
                        if j.get("kty") == "RSA"
                        and j.get("use", "sig") == "sig"
                        and j.get("alg", "RS256") == "RS256"
                    }
                    for j in keys.values():
                        jwt.PyJWK.from_dict(j, algorithm="RS256")
                    record = {"time": now, "keys": keys}
                    with tempfile.NamedTemporaryFile(
                        dir=self.directory, mode="w", delete=False
                    ) as f:
                        json.dump(record, f)
                    os.replace(f.name, self.file)
                    cached, age = record, 0
                except (httpx.HTTPError, OSError, ValueError, TypeError, AttributeError, KeyError, jwt.PyJWTError):
                    if not (0 <= age < 86400 and kid in cached["keys"]):
                        raise KeysUnavailable()
            if not (0 <= age < 86400):
                raise KeysUnavailable()
            if kid not in cached["keys"]:
                raise jwt.InvalidTokenError("Unrecognized signing key")
            return self.decode_signing_key(cached["keys"][kid])

    def verify(self, token, audience):
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if (
            header.get("alg") != "RS256"
            or not isinstance(kid, str)
            or not 0 < len(kid) <= 256
        ):
            raise jwt.InvalidTokenError("Invalid signing parameters")
        return jwt.decode(
            token,
            self.key(kid),
            algorithms=["RS256"],
            issuer=self.issuer,
            audience=audience,
            options={"require": ["iss", "aud", "sub", "iat", "exp"]},
        )


def account(claims):
    if not isinstance(claims.get(PRODUCTS), list) or not (
        {PRODUCT, LEGACY_PRODUCT} & {p for p in claims[PRODUCTS] if isinstance(p, str)}
    ):
        raise PermissionError("No Internal Documents grant")
    try:
        return str(UUID(claims[UID]))
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        raise PermissionError("No stable Onesa ID") from exc


def https_origin(url):
    p = urlsplit(url)
    if (
        p.scheme != "https"
        or not p.netloc
        or p.username
        or p.password
        or p.query
        or p.fragment
        or p.path not in ("", "/")
    ):
        raise ValueError("A fixed HTTPS origin is required")
    return url.rstrip("/")
