"""Minimale OIDC-provider voor tests (gedraagt zich zoals Authentik voor VaultX).

Draait als echte HTTP-server in een achtergrondthread, zodat VaultX exact
dezelfde code gebruikt als tegen Authentik: discovery, JWKS, token endpoint.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
import threading
import time
from typing import Any
from urllib.parse import urlencode

import uvicorn
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route


class FakeOIDCProvider:
    def __init__(self, client_id: str = "vaultx", client_secret: str = "test-secret") -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.key = RSAKey.generate_key(2048, parameters={"kid": "key-1", "alg": "RS256", "use": "sig"})
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.issuer = f"{self.base}/application/o/vaultx/"
        self.codes: dict[str, dict[str, Any]] = {}
        self.next_claims: dict[str, Any] = {}
        self.sid_counter = 0
        self._server: uvicorn.Server | None = None

    # -------------------------------------------------------------- tokens

    def sign(self, claims: dict[str, Any], key: RSAKey | None = None) -> str:
        k = key or self.key
        return jwt.encode({"alg": "RS256", "kid": k.kid, "typ": "JWT"}, claims, k)

    def access_token(self, sub: str, **extra: Any) -> str:
        now = int(time.time())
        return self.sign(
            {"iss": self.issuer, "aud": self.client_id, "sub": sub, "iat": now, "exp": now + 300, **extra}
        )

    def logout_token(self, *, sid: str | None = None, sub: str | None = None, **extra: Any) -> str:
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.client_id,
            "iat": int(time.time()),
            "jti": secrets.token_hex(8),
            "events": {"http://schemas.openid.net/event/backchannel-logout": {}},
        }
        if sid:
            claims["sid"] = sid
        if sub:
            claims["sub"] = sub
        claims.update(extra)
        return self.sign(claims)

    # -------------------------------------------------------------- endpoints

    async def discovery(self, request: Request) -> JSONResponse:
        p = f"{self.base}/application/o"
        return JSONResponse(
            {
                "issuer": self.issuer,
                "authorization_endpoint": f"{p}/authorize/",
                "token_endpoint": f"{p}/token/",
                "userinfo_endpoint": f"{p}/userinfo/",
                "end_session_endpoint": f"{p}/vaultx/end-session/",
                "jwks_uri": f"{p}/vaultx/jwks/",
                "id_token_signing_alg_values_supported": ["RS256"],
                "code_challenge_methods_supported": ["S256"],
                "backchannel_logout_supported": True,
                "backchannel_logout_session_supported": True,
            }
        )

    async def jwks(self, request: Request) -> JSONResponse:
        return JSONResponse(KeySet([self.key]).as_dict(private=False))

    async def authorize(self, request: Request) -> RedirectResponse:
        q = request.query_params
        assert q["client_id"] == self.client_id
        assert q["code_challenge_method"] == "S256"
        code = secrets.token_urlsafe(16)
        self.sid_counter += 1
        self.codes[code] = {
            "claims": dict(self.next_claims),
            "nonce": q["nonce"],
            "challenge": q["code_challenge"],
            "redirect_uri": q["redirect_uri"],
            "sid": f"sid-{self.sid_counter}",
        }
        return RedirectResponse(f"{q['redirect_uri']}?{urlencode({'code': code, 'state': q['state']})}", 302)

    async def token(self, request: Request) -> JSONResponse:
        auth = request.headers.get("authorization", "")
        expected = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        if auth != f"Basic {expected}":
            return JSONResponse({"error": "invalid_client"}, 401)
        form = await request.form()
        entry = self.codes.pop(str(form["code"]), None)
        if entry is None:
            return JSONResponse({"error": "invalid_grant"}, 400)
        verifier = str(form["code_verifier"]).encode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b"=").decode()
        if challenge != entry["challenge"] or form["redirect_uri"] != entry["redirect_uri"]:
            return JSONResponse({"error": "invalid_grant"}, 400)
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": self.client_id,
            "iat": now,
            "exp": now + 300,
            "nonce": entry["nonce"],
            "sid": entry["sid"],
            **entry["claims"],
        }
        return JSONResponse(
            {
                "access_token": self.access_token(claims["sub"]),
                "token_type": "Bearer",
                "expires_in": 300,
                "id_token": self.sign(claims),
            }
        )

    async def userinfo(self, request: Request) -> JSONResponse:
        return JSONResponse({})

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/application/o/vaultx/.well-known/openid-configuration", self.discovery),
                Route("/application/o/vaultx/jwks/", self.jwks),
                Route("/application/o/authorize/", self.authorize),
                Route("/application/o/token/", self.token, methods=["POST"]),
                Route("/application/o/userinfo/", self.userinfo),
            ]
        )

    # -------------------------------------------------------------- server

    def start(self) -> None:
        config = uvicorn.Config(self.app(), host="127.0.0.1", port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        thread = threading.Thread(target=self._server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if self._server.started:
                return
            time.sleep(0.05)
        raise RuntimeError("Fake OIDC-provider startte niet")

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
