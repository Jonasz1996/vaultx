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
        # NPM-beheer: de andere soorten objecten, zoals NPM ze teruggeeft.
        self.objects: dict[str, list[dict[str, Any]]] = {
            "redirection-hosts": [],
            "dead-hosts": [],
            "streams": [],
            "access-lists": [],
        }
        self.uploads: list[int] = []
        self.renewed: list[int] = []
        self.fail_upload = False
        self.letsencrypt_error: str | None = None
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
            if "bad_directive" in cfg:
                return 'nginx: [emerg] unknown directive "bad_directive"'
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
        redirect = next(
            (
                h
                for h in self.objects["redirection-hosts"]
                if t.domain in h["domain_names"]
                and h.get("enabled", True)
                and (h.get("meta") or {}).get("nginx_online") is not False
            ),
            None,
        )
        if redirect is not None:
            scheme = "https" if redirect.get("forward_scheme") == "https" else "http"
            return ProbeResult(
                url=url,
                status=int(redirect.get("forward_http_code") or 301),
                location=f"{scheme}://{redirect['forward_domain_name']}/",
            )
        host = next((h for h in self.hosts if t.domain in h["domain_names"]), None)
        if host is not None and host.get("forward_host") in self.unreachable_upstreams:
            return ProbeResult(url=url, status=502)
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

    # ------------------------------------------------------------ NPM-beheer (andere objecten)

    def _stamp(self, obj: dict[str, Any]) -> None:
        self._modified += 1
        obj["modified_on"] = f"2026-10-05 12:{self._modified // 60:02d}:{self._modified % 60:02d}"

    def _test_object(self, kind: str, obj: dict[str, Any]) -> str | None:
        if kind == "streams":
            for other in self.objects["streams"]:
                if (
                    other is not obj
                    and other.get("incoming_port") == obj.get("incoming_port")
                    and other.get("enabled", True)
                ):
                    return f"nginx: [emerg] duplicate listen options for 0.0.0.0:{obj.get('incoming_port')}"
            return None
        if kind == "access-lists":
            return None
        cfg = obj.get("advanced_config") or ""
        if cfg.count("{") != cfg.count("}"):
            return "nginx: [emerg] unexpected end of file"
        if "bad_directive" in cfg:
            return 'nginx: [emerg] unknown directive "bad_directive"'
        return None

    def _generic(self, request: httpx.Request, kind: str, rest: str) -> httpx.Response:
        items = self.hosts if kind == "proxy-hosts" else self.objects[kind]
        method = request.method
        not_found = httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})
        if rest == "" and method == "GET":
            return httpx.Response(200, json=items)
        if rest == "" and method == "POST":
            body = json.loads(request.content)
            if kind != "access-lists" and kind != "streams":
                for d in body.get("domain_names") or []:
                    for k in ("proxy-hosts", "redirection-hosts", "dead-hosts"):
                        pool = self.hosts if k == "proxy-hosts" else self.objects[k]
                        if any(d in h.get("domain_names", []) for h in pool):
                            return httpx.Response(
                                400, json={"error": {"code": 400, "message": f"{d} is already in use"}}
                            )
            self.next_id += 1
            obj = {"id": self.next_id, "created_on": "2026-10-05 12:00:00", "enabled": True, **body}
            if kind == "access-lists":
                obj["items"] = [{"username": i["username"], "password": ""} for i in body.get("items") or []]
                obj["passwords"] = {i["username"]: i.get("password", "") for i in body.get("items") or []}
            self._stamp(obj)
            items.append(obj)
            err = self._test_object(kind, obj)
            response = {**obj, "meta": {}}
            obj["meta"] = {"nginx_online": err is None, "nginx_err": err}
            return httpx.Response(201, json=response)
        m = re.fullmatch(r"/(\d+)(/enable|/disable)?", rest)
        if not m:
            return not_found
        obj = next((o for o in items if o["id"] == int(m.group(1))), None)
        if obj is None:
            return not_found
        if m.group(2):
            if method != "POST":
                return not_found
            enable = m.group(2) == "/enable"
            if bool(obj.get("enabled", True)) == enable:
                word = "enabled" if enable else "disabled"
                return httpx.Response(
                    400, json={"error": {"code": 400, "message": f"Host is already {word}"}}
                )
            obj["enabled"] = enable
            self._stamp(obj)
            err = self._test_object(kind, obj) if enable else None
            obj["meta"] = {"nginx_online": err is None, "nginx_err": err}
            return httpx.Response(200, json=True)
        if method == "GET":
            return httpx.Response(200, json=obj)
        if method == "PUT":
            body = json.loads(request.content)
            if kind == "access-lists" and "items" in body:
                old = obj.get("passwords", {})
                obj["passwords"] = {
                    i["username"]: i.get("password") or old.get(i["username"], "") for i in body["items"]
                }
                body["items"] = [{"username": i["username"], "password": ""} for i in body["items"]]
            obj.update(body)
            self._stamp(obj)
            err = self._test_object(kind, obj)
            obj["meta"] = {"nginx_online": err is None, "nginx_err": err}
            return httpx.Response(200, json=obj)
        if method == "DELETE":
            items.remove(obj)
            self.deleted.append(obj["id"])
            if kind == "access-lists":  # zoals NPM: hosts met deze lijst staan daarna open
                for h in self.hosts:
                    if h.get("access_list_id") == obj["id"]:
                        h["access_list_id"] = 0
            return httpx.Response(200, json=True)
        return not_found

    def _certificates(self, request: httpx.Request, rest: str) -> httpx.Response:
        method = request.method
        if rest == "/dns-providers" and method == "GET":
            return httpx.Response(
                200, json=[{"id": "cloudflare", "name": "Cloudflare", "credentials": "x = y"}]
            )
        if rest == "/validate" and method == "POST":
            body = request.content.decode(errors="replace")
            if "BEGIN CERTIFICATE" not in body:
                return httpx.Response(
                    400, json={"error": {"code": 400, "message": "Certificate is not valid"}}
                )
            return httpx.Response(
                200,
                json={
                    "certificate": {
                        "cn": "eigen.domain.be",
                        "issuer": "CN=test",
                        "dates": {"from": 1, "to": 4102444800},
                    },
                    "certificate_key": "WRONG KEY" not in body,
                },
            )
        if rest == "" and method == "POST":
            body = json.loads(request.content)
            if body.get("provider") == "letsencrypt" and self.letsencrypt_error:
                return httpx.Response(500, json={"error": {"code": 500, "message": self.letsencrypt_error}})
            self.next_id += 1
            cert = {
                "id": self.next_id,
                "provider": body["provider"],
                "nice_name": body.get("nice_name") or ", ".join(body.get("domain_names") or []),
                "domain_names": body.get("domain_names") or [],
                "expires_on": "2027-01-01 00:00:00",
                "meta": {"certificate_key": "-----BEGIN PRIVATE KEY-----geheim"},
            }
            self._stamp(cert)
            self.certificates.append(cert)
            return httpx.Response(201, json=cert)
        m = re.fullmatch(r"/(\d+)(/upload|/renew)?", rest)
        cert = next((c for c in self.certificates if m and c["id"] == int(m.group(1))), None)
        if cert is None:
            return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})
        assert m is not None
        if m.group(2) == "/upload" and method == "POST":
            if self.fail_upload:
                return httpx.Response(
                    400, json={"error": {"code": 400, "message": "Certificate is not valid"}}
                )
            self.uploads.append(cert["id"])
            cert["domain_names"] = ["eigen.domain.be"]
            return httpx.Response(200, json={"certificate": "x"})
        if m.group(2) == "/renew" and method == "POST":
            self.renewed.append(cert["id"])
            cert["expires_on"] = "2027-03-01 00:00:00"
            return httpx.Response(200, json=cert)
        if method == "GET":
            return httpx.Response(200, json=cert)
        if method == "DELETE":
            self.certificates.remove(cert)
            self.deleted.append(cert["id"])
            return httpx.Response(200, json=True)
        return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})

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
        m = re.fullmatch(r"/api/nginx/certificates(/.*)?", path)
        if m:
            return self._certificates(request, m.group(1) or "")
        m = re.fullmatch(r"/api/nginx/(redirection-hosts|dead-hosts|streams|access-lists)(/.*)?", path)
        if m:
            return self._generic(request, m.group(1), m.group(2) or "")
        m = re.fullmatch(r"/api/nginx/proxy-hosts/(\d+)/(enable|disable)", path)
        if m:
            return self._generic(request, "proxy-hosts", f"/{m.group(1)}/{m.group(2)}")
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
