"""Nep-Nginx Proxy Manager voor tests, via een httpx MockTransport.

Bootst de stukken van de NPM 2.16-API na die VaultX gebruikt, met dezelfde
vormen als de echte API (zie e2e/npm_e2e.py voor de test tegen echte NPM).
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.services.npm_probe import ProbeResult, ProbeTarget

LOCATION_KEYS = {
    "id",
    "path",
    "forward_scheme",
    "forward_host",
    "forward_port",
    "forward_path",
    "advanced_config",
    "access_list_id",
}
PUT_KEYS = {
    "domain_names",
    "forward_scheme",
    "forward_host",
    "forward_port",
    "certificate_id",
    "ssl_forced",
    "hsts_enabled",
    "hsts_subdomains",
    "trust_forwarded_proto",
    "http2_support",
    "block_exploits",
    "caching_enabled",
    "allow_websocket_upgrade",
    "access_list_id",
    "advanced_config",
    "enabled",
    "meta",
    "locations",
}


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
        self._modified = 0
        self.requires_2fa = False
        self.down = False
        self.requests: list[httpx.Request] = []
        self.puts: list[tuple[int, dict[str, Any]]] = []
        # Hostnamen die nginx in NPM niet kan resolven: een literal proxy_pass ernaar faalt bij nginx -t.
        self.unresolvable = {"does-not-exist"}
        # Outposts die wel resolven maar niet antwoorden: auth_request geeft dan 500.
        self.dead_outposts = {"dead-outpost"}
        # Tijdens een PUT naar NPM laten mislukken (bv. om een mislukte rollback na te bootsen).
        self.fail_puts_after: int | None = None
        self.probe_down = False
        # Fase 4: met een nep-Authentik antwoordt de outpost enkel voor domeinen die hij kent.
        self.authentik: Any = None
        # Fase 6: certificaten (zoals NPM ze geeft, met de sleutel in meta), nieuwe hosts, upstreams.
        self.certificates: list[dict[str, Any]] = []
        self.next_id = 100
        self.fail_create = False
        self.fail_deletes = 0
        self.deleted: list[int] = []
        self.unreachable_upstreams: set[str] = set()
        self.transport = httpx.MockTransport(self._handle)

    def host(self, npm_id: int) -> dict[str, Any] | None:
        return next((h for h in self.hosts if h["id"] == npm_id), None)

    def _nginx_test(self, host: dict[str, Any]) -> str | None:
        """Bootst `nginx -t` na voor wat de tests nodig hebben."""
        configs = [host.get("advanced_config") or ""] + [
            loc.get("advanced_config") or "" for loc in host.get("locations") or []
        ]
        for cfg in configs:
            for name in re.findall(r"proxy_pass\s+https?://([^:/;\s]+)", cfg):
                if name in self.unresolvable:
                    return f'nginx: [emerg] host not found in upstream "{name}"'
            if cfg.count("{") != cfg.count("}"):
                return "nginx: [emerg] unexpected end of file"
            if len(re.findall(r"^\s*auth_request\s", cfg, re.M)) > 1:
                return 'nginx: [emerg] "auth_request" directive is duplicate'
        return None

    def _put(self, npm_id: int, body: dict[str, Any]) -> httpx.Response:
        host = self.host(npm_id)
        if host is None:
            return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})
        if self.fail_puts_after is not None and len(self.puts) >= self.fail_puts_after:
            return httpx.Response(500, json={"error": {"code": 500, "message": "Internal Error"}})
        unknown = set(body) - PUT_KEYS
        for loc in body.get("locations") or []:
            unknown |= set(loc) - LOCATION_KEYS
        if unknown or not body:
            return httpx.Response(
                400,
                json={
                    "error": {"code": 400, "message": f"data must NOT have additional properties {unknown}"}
                },
            )
        self.puts.append((npm_id, body))
        host.update(body)
        self._modified += 1
        host["modified_on"] = f"2026-10-05 11:{self._modified // 60:02d}:{self._modified % 60:02d}"
        err = self._nginx_test(host)
        host["meta"] = {"nginx_online": err is None, "nginx_err": err}
        return httpx.Response(200, json=host)

    def _create(self, body: dict[str, Any]) -> httpx.Response:
        if self.fail_create:
            return httpx.Response(500, json={"error": {"code": 500, "message": "Internal Error"}})
        unknown = set(body) - PUT_KEYS
        for loc in body.get("locations") or []:
            unknown |= set(loc) - LOCATION_KEYS
        if unknown:
            return httpx.Response(
                400, json={"error": {"code": 400, "message": f"additional properties {unknown}"}}
            )
        for d in body.get("domain_names") or []:
            if any(d in h["domain_names"] for h in self.hosts):
                return httpx.Response(400, json={"error": {"code": 400, "message": f"{d} is already in use"}})
        self.next_id += 1
        host = proxy_host(self.next_id, list(body["domain_names"]))
        host.update({k: v for k, v in body.items() if k != "meta"})
        host["access_list"] = None
        self.hosts.append(host)
        err = self._nginx_test(host)
        # Zoals NPM 2.16: het antwoord op POST heeft nog geen nginx_online, de volgende GET wel.
        response = {**host, "meta": {"letsencrypt_agree": False, "dns_challenge": False}}
        host["meta"] = {"nginx_online": err is None, "nginx_err": err}
        return httpx.Response(201, json=response)

    def _delete(self, npm_id: int) -> httpx.Response:
        if self.fail_deletes:
            self.fail_deletes -= 1
            return httpx.Response(500, json={"error": {"code": 500, "message": "Internal Error"}})
        host = self.host(npm_id)
        if host is None:
            return httpx.Response(404, json={"error": {"code": 404, "message": f"Not Found - {npm_id}"}})
        self.hosts.remove(host)
        self.deleted.append(npm_id)
        return httpx.Response(200, json=True)

    async def check_upstream(self, host: str, port: int) -> str | None:
        return "Connection refused" if host in self.unreachable_upstreams else None

    async def probe(self, t: ProbeTarget) -> ProbeResult:
        """Wat een bezoeker zonder sessie krijgt, afgeleid uit de config van de host."""
        url = t.url
        if self.probe_down:
            return ProbeResult(url=url, error="ConnectError")
        host = next((h for h in self.hosts if t.domain in h["domain_names"]), None)
        if (
            host is None
            or not host.get("enabled", True)
            or (host.get("meta") or {}).get("nginx_online") is False
        ):
            return ProbeResult(url=url, status=404)
        configs = [host.get("advanced_config") or ""] + [
            loc.get("advanced_config") or "" for loc in host.get("locations") or []
        ]
        if not any(re.search(r"^\s*auth_request\s+/outpost", c, re.M) for c in configs):
            return ProbeResult(url=url, status=200)
        outpost = re.search(
            r"location /outpost\.goauthentik\.io \{\s*proxy_pass\s+https?://([^:/;\s]+)", configs[0]
        )
        if outpost is None or outpost.group(1) in self.dead_outposts:
            return ProbeResult(url=url, status=500)
        if self.authentik is not None and not self.authentik.serves(t.domain):
            # Outpost kent het domein niet: auth_request krijgt 404, nginx geeft 500.
            return ProbeResult(url=url, status=500)
        return ProbeResult(
            url=url, status=302, location=f"/outpost.goauthentik.io/start?rd=https://{t.domain}/"
        )

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
        if path == "/api/nginx/proxy-hosts" and request.method == "POST":
            return self._create(json.loads(request.content))
        if path == "/api/nginx/certificates" and request.method == "GET":
            return httpx.Response(200, json=self.certificates)
        m = re.fullmatch(r"/api/nginx/proxy-hosts/(\d+)", path)
        if m and request.method == "GET":
            host = self.host(int(m.group(1)))
            if host is None:
                return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})
            return httpx.Response(200, json=host)
        if m and request.method == "PUT":
            return self._put(int(m.group(1)), json.loads(request.content))
        if m and request.method == "DELETE":
            return self._delete(int(m.group(1)))
        return httpx.Response(404, json={"error": {"code": 404, "message": f"Not Found - {path}"}})
