"""Nep-Nginx Proxy Manager voor tests, via een httpx MockTransport.

Bootst de stukken van de NPM 2.16-API na die VaultX gebruikt, met dezelfde
vormen als de echte API (zie e2e/npm_e2e.py voor de test tegen echte NPM).
"""

from __future__ import annotations

import json
from typing import Any

import httpx


def proxy_host(
    npm_id: int,
    domains: list[str],
    *,
    forward_host: str = "app",
    forward_port: int = 80,
    advanced_config: str = "",
    locations: list[dict[str, Any]] | None = None,
    certificate_id: int = 0,
    ssl_forced: bool = False,
    access_list: dict[str, Any] | None = None,
    enabled: bool = True,
    nginx_online: bool = True,
) -> dict[str, Any]:
    meta: dict[str, Any] = {"nginx_online": nginx_online}
    if not nginx_online:
        meta["nginx_err"] = "nginx: [emerg] host not found in upstream"
    return {
        "id": npm_id,
        "created_on": "2026-10-05 10:00:00",
        "modified_on": "2026-10-05 10:00:00",
        "owner_user_id": 1,
        "domain_names": domains,
        "forward_host": forward_host,
        "forward_port": forward_port,
        "access_list_id": access_list["id"] if access_list else 0,
        "certificate_id": certificate_id,
        "ssl_forced": ssl_forced,
        "caching_enabled": False,
        "block_exploits": True,
        "advanced_config": advanced_config,
        "meta": meta,
        "allow_websocket_upgrade": True,
        "http2_support": False,
        "forward_scheme": "http",
        "enabled": enabled,
        "locations": locations or [],
        "hsts_enabled": False,
        "hsts_subdomains": False,
        "trust_forwarded_proto": False,
        "access_list": access_list,
    }


class FakeNPM:
    def __init__(self, identity: str = "admin@example.com", secret: str = "npm-secret") -> None:
        self.identity = identity
        self.secret = secret
        self.hosts: list[dict[str, Any]] = []
        self.version = {"major": 2, "minor": 16, "revision": 0}
        self.requires_2fa = False
        self.down = False
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == "/api/tokens" and request.method == "POST":
            body = json.loads(request.content)
            if body.get("identity") != self.identity or body.get("secret") != self.secret:
                return httpx.Response(
                    400, json={"error": {"code": 400, "message": "Invalid email or password"}}
                )
            if self.requires_2fa:
                return httpx.Response(200, json={"requires_2fa": True, "challenge_token": "c"})
            return httpx.Response(200, json={"token": "npm-token", "expires": "2026-10-06T10:00:00Z"})
        if path == "/api/" and request.method == "GET":
            return httpx.Response(200, json={"status": "OK", "setup": True, "version": self.version})
        if request.headers.get("authorization") != "Bearer npm-token":
            return httpx.Response(401, json={"error": {"code": 401, "message": "Unauthorized"}})
        if path == "/api/nginx/proxy-hosts" and request.method == "GET":
            return httpx.Response(200, json=self.hosts)
        return httpx.Response(404, json={"error": {"code": 404, "message": f"Not Found - {path}"}})
