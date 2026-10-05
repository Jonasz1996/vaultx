"""End-to-end test: VaultX zet Authentik-bescherming op een echte Nginx Proxy Manager.

Start een nep-Authentik-outpost (die ook als applicatie achter NPM dient), een
nep-OIDC-provider en de VaultX-backend, en doet dan via de VaultX-API:

1. NPM koppelen met schrijfrechten, synchroniseren.
2. Bescherming zetten op een HTTP-host en op een HTTPS-host (eigen certificaat):
   een bezoeker zonder sessie wordt naar Authentik gestuurd, een bezoeker met
   sessie komt bij de applicatie, met de X-authentik-*-headers van de outpost.
3. Een outpost-URL die nginx niet kan resolven: NPM zet de host offline, VaultX
   zet terug, de host is weer online en werkt zoals ervoor.
4. Een outpost die niet antwoordt (500): VaultX zet terug.
5. Een host met een access list op "Satisfy Any": VaultX weigert, NPM ongemoeid.
6. Bescherming weghalen: de oude config staat er weer.

Raakt enkel hosts onder .vaultx-write-e2e.test aan. Zie e2e/README.md.

    cd backend && python ../e2e/npm_write_e2e.py http://localhost:81 admin@example.com wachtwoord \\
        --proxy 127.0.0.1 --http-port 8080 --https-port 8443
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "e2e"))

from bitwarden_e2e import free_port, login, step  # noqa: E402

from tests.fake_oidc import FakeOIDCProvider  # noqa: E402

DB = os.environ.get("VAULTX_DATABASE_URL", "postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_test")
SUFFIX = ".vaultx-write-e2e.test"
SESSION_COOKIE = "authentik_proxy_e2e=ok"
CSRF = {"X-VaultX-CSRF": "1"}


class OutpostAndApp(BaseHTTPRequestHandler):
    """Gedraagt zich als de Authentik-proxy-outpost (auth/nginx, start) én als de app erachter."""

    def log_message(self, *args: object) -> None:  # stil
        pass

    def _send(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/outpost.goauthentik.io/auth/nginx"):
            if SESSION_COOKIE in (self.headers.get("Cookie") or ""):
                self._send(
                    200,
                    headers={
                        "X-authentik-username": "jonas",
                        "X-authentik-email": "jonas@example.be",
                        "X-authentik-groups": "vaultx-admins",
                    },
                )
            else:
                self._send(401)
        elif self.path.startswith("/outpost.goauthentik.io/start"):
            self._send(
                302, headers={"Location": "https://auth.example.be/if/flow/default-authentication-flow/"}
            )
        else:
            body = json.dumps(
                {
                    "app": "ok",
                    "user": self.headers.get("X-authentik-username"),
                    "email": self.headers.get("X-authentik-email"),
                }
            ).encode()
            self._send(200, body, {"Content-Type": "application/json"})


def self_signed(domain: str) -> tuple[bytes, bytes]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), False)
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
    )


def seed(npm: httpx.Client, app_host: str, app_port: int) -> dict[str, int]:
    for h in npm.get("/nginx/proxy-hosts").raise_for_status().json():
        if any(d.endswith(SUFFIX) for d in h["domain_names"]):
            npm.delete(f"/nginx/proxy-hosts/{h['id']}").raise_for_status()
    acls = npm.get("/nginx/access-lists").raise_for_status().json()
    acl = next((a for a in acls if a["name"] == "VaultX write-e2e LAN"), None) or (
        npm.post(
            "/nginx/access-lists",
            json={
                "name": "VaultX write-e2e LAN",
                "satisfy_any": True,
                "pass_auth": False,
                "items": [],
                "clients": [{"address": "10.0.0.0/8", "directive": "allow"}],
            },
        )
        .raise_for_status()
        .json()
    )
    tls_domain = f"secure{SUFFIX}"
    cert = npm.post("/nginx/certificates", json={"provider": "other", "nice_name": "VaultX write-e2e"})
    cert_id = cert.raise_for_status().json()["id"]
    pem, key = self_signed(tls_domain)
    npm.post(
        f"/nginx/certificates/{cert_id}/upload",
        files={"certificate": ("cert.pem", pem), "certificate_key": ("key.pem", key)},
    ).raise_for_status()

    base = {
        "forward_scheme": "http",
        "forward_host": app_host,
        "forward_port": app_port,
        "block_exploits": True,
    }
    hosts = {
        "app": {**base, "domain_names": [f"app{SUFFIX}"], "advanced_config": "# vaultx.tags = e2e\n"},
        "secure": {**base, "domain_names": [tls_domain], "certificate_id": cert_id, "ssl_forced": True},
        "multi": {
            **base,
            "domain_names": [f"multi{SUFFIX}"],
            "locations": [
                {"path": "/api", "forward_scheme": "http", "forward_host": app_host, "forward_port": app_port}
            ],
        },
        "lan": {**base, "domain_names": [f"lan{SUFFIX}"], "access_list_id": acl["id"]},
    }
    ids = {}
    for name, body in hosts.items():
        r = npm.post("/nginx/proxy-hosts", json=body)
        if r.status_code >= 400:
            raise SystemExit(f"Aanmaken van {name} mislukt: {r.status_code} {r.text}")
        ids[name] = r.json()["id"]
    return ids


def norm(locations: list[dict] | None) -> list[dict]:
    """NPM bewaart "geen locations" als null of [], en een lege advanced_config soms niet."""
    return [{"advanced_config": "", **loc} for loc in locations or []]


def visit(
    proxy: str,
    port: int,
    domain: str,
    *,
    https: bool = False,
    cookie: bool = False,
    path: str = "/",
    want=None,
):
    """GET zoals een browser; met want= opnieuw proberen tot die status (nginx herlaadt asynchroon)."""
    for _ in range(10):
        r = _visit(proxy, port, domain, https=https, cookie=cookie, path=path)
        if want is None or (r.status_code == want and (want != 200 or r.text.startswith("{"))):
            return r
        time.sleep(0.5)
    return r


def _visit(proxy: str, port: int, domain: str, *, https: bool, cookie: bool, path: str):
    headers = {"Host": domain}
    if cookie:
        headers["Cookie"] = SESSION_COOKIE
    scheme = "https" if https else "http"
    with httpx.Client(verify=False, trust_env=False, follow_redirects=False, timeout=10) as c:  # noqa: S501
        return c.get(
            f"{scheme}://{proxy}:{port}{path}",
            headers=headers,
            extensions={"sni_hostname": domain} if https else {},
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("npm_url")
    ap.add_argument("identity")
    ap.add_argument("secret")
    ap.add_argument("--proxy", default="127.0.0.1", help="adres waarop NPM poort 80/443 aanbiedt")
    ap.add_argument("--http-port", type=int, default=80)
    ap.add_argument("--https-port", type=int, default=443)
    ap.add_argument("--docker-host", default="172.17.0.1", help="hoe de NPM-container deze machine bereikt")
    args = ap.parse_args()

    outpost_port = free_port()
    server = ThreadingHTTPServer(("0.0.0.0", outpost_port), OutpostAndApp)  # noqa: S104
    threading.Thread(target=server.serve_forever, daemon=True).start()
    outpost = f"http://{args.docker_host}:{outpost_port}"

    npm = httpx.Client(base_url=f"{args.npm_url.rstrip('/')}/api", timeout=60, trust_env=False)
    token = npm.post("/tokens", json={"identity": args.identity, "secret": args.secret}).raise_for_status()
    npm.headers["Authorization"] = f"Bearer {token.json()['token']}"
    step(f"NPM vullen met e2e-hosts (app achter {outpost})")
    ids = seed(npm, args.docker_host, outpost_port)
    time.sleep(1)
    before = {name: npm.get(f"/nginx/proxy-hosts/{i}").json() for name, i in ids.items()}
    r = visit(args.proxy, args.http_port, f"app{SUFFIX}", want=200)
    assert r.status_code == 200 and r.json()["user"] is None, (r.status_code, r.text)

    fake = FakeOIDCProvider()
    fake.start()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "VAULTX_DATABASE_URL": DB,
        "VAULTX_SECRET_KEY": "e2e-secret-key-e2e-secret-key-0123456789",
        "VAULTX_PUBLIC_URL": base,
        "VAULTX_OIDC_ISSUER": fake.issuer,
        "VAULTX_OIDC_CLIENT_ID": fake.client_id,
        "VAULTX_OIDC_CLIENT_SECRET": fake.client_secret,
        "VAULTX_COOKIE_SECURE": "false",
        "VAULTX_NPM_SYNC_INTERVAL_MINUTES": "0",
        "VAULTX_LOG_LEVEL": "WARNING",
    }
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True)
    backend = subprocess.Popen(  # noqa: S603 - vaste argumenten
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
        cwd=BACKEND,
        env=env,
    )
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{base}/health", trust_env=False).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.2)
        else:
            raise SystemExit("backend start niet")

        step("VaultX: inloggen, organisatie en NPM-koppeling met schrijfrechten")
        admin = httpx.Client(base_url=base, trust_env=False, timeout=60, headers=CSRF)
        login(admin, fake, "admin-npm-e2e", "admin-npm-e2e@example.com", ["vaultx-admins"])
        slug = f"npm-e2e-{int(time.time())}"
        org = admin.post("/api/v1/organizations", json={"slug": slug, "name": "NPM e2e"}).raise_for_status()
        org_id = org.json()["id"]
        conn = admin.post(
            f"/api/v1/organizations/{org_id}/npm-connections",
            json={
                "name": "NPM e2e",
                "base_url": args.npm_url,
                "identity": args.identity,
                "secret": args.secret,
                "write_enabled": True,
                "authentik_outpost_url": outpost,
                "probe_host": args.proxy,
                "probe_http_port": args.http_port,
                "probe_https_port": args.https_port,
            },
        ).raise_for_status()
        cbase = f"/api/v1/organizations/{org_id}/npm-connections/{conn.json()['id']}"
        admin.post(f"{cbase}/sync").raise_for_status()
        hosts = {h["npm_id"]: h for h in admin.get(f"{cbase}/hosts").raise_for_status().json()}
        hid = {name: hosts[i]["id"] for name, i in ids.items()}

        def protect(name: str, action: str = "protect") -> dict:
            plan = admin.get(f"{cbase}/hosts/{hid[name]}/protection", params={"action": action}).json()
            r = admin.post(
                f"{cbase}/hosts/{hid[name]}/protection",
                json={"action": action, "expected_modified_on": plan["modified_on"]},
            )
            assert r.status_code == 200, r.text
            change = r.json()
            print(f"    {name:7} {action:9} -> {change['status']}: {change['message']}")
            return change

        step("Bescherming zetten op app (HTTP), secure (HTTPS) en multi (extra custom location)")
        for name in ("app", "secure", "multi"):
            change = protect(name)
            assert change["status"] == "applied", change
            assert change["probe_after"]["status"] == 302, change
        r = visit(args.proxy, args.http_port, f"app{SUFFIX}")
        assert r.status_code == 302 and "/outpost.goauthentik.io/start" in r.headers["location"], (
            r.status_code
        )
        r = visit(args.proxy, args.https_port, f"secure{SUFFIX}", https=True)
        assert r.status_code == 302 and "/outpost.goauthentik.io/start" in r.headers["location"], (
            r.status_code
        )
        r = visit(args.proxy, args.http_port, f"multi{SUFFIX}", path="/api/x")
        assert r.status_code == 302, f"custom location /api niet beschermd: {r.status_code}"
        step("Met Authentik-sessie: de app krijgt de identiteit van de outpost")
        for domain, https, p in (
            (f"app{SUFFIX}", False, args.http_port),
            (f"secure{SUFFIX}", True, args.https_port),
        ):
            r = visit(args.proxy, p, domain, https=https, cookie=True)
            assert r.status_code == 200 and r.json()["user"] == "jonas", (r.status_code, r.text)
        catalog = admin.get("/api/v1/catalog", params={"organization_id": org_id}).json()
        app = next(a for a in catalog if a["url"] == f"http://app{SUFFIX}")
        assert (app["auth_method"], app["status"]) == ("forward_auth", "protected"), app

        step("Bescherming weghalen: oude config terug")
        for name in ("app", "multi"):
            assert protect(name, "unprotect")["status"] == "applied"
            now = npm.get(f"/nginx/proxy-hosts/{ids[name]}").json()
            assert norm(now["locations"]) == norm(before[name]["locations"]), now["locations"]
            assert now["advanced_config"].rstrip() == before[name]["advanced_config"].rstrip()
        r = visit(args.proxy, args.http_port, f"app{SUFFIX}", want=200)
        assert r.status_code == 200 and r.json()["user"] is None

        step("Outpost-URL die nginx niet kan resolven: NPM zet de host offline, VaultX zet terug")
        admin.patch(
            cbase, json={"authentik_outpost_url": "http://outpost-bestaat-niet:9000"}
        ).raise_for_status()
        change = protect("app")
        assert change["status"] == "rolled_back", change
        assert "host not found" in (change["nginx_error"] or ""), change
        now = npm.get(f"/nginx/proxy-hosts/{ids['app']}").json()
        assert now["meta"]["nginx_online"] is True, now["meta"]
        assert now["advanced_config"].rstrip() == before["app"]["advanced_config"].rstrip()
        r = visit(args.proxy, args.http_port, f"app{SUFFIX}", want=200)
        assert r.status_code == 200 and r.json()["app"] == "ok", "host werkt niet meer na terugzetten"

        step("Outpost die niet antwoordt: VaultX ziet een 500 en zet terug")
        admin.patch(cbase, json={"authentik_outpost_url": f"http://{args.docker_host}:9"}).raise_for_status()
        change = protect("app")
        assert change["status"] == "rolled_back" and change["probe_after"]["status"] >= 500, change
        r = visit(args.proxy, args.http_port, f"app{SUFFIX}", want=200)
        assert r.status_code == 200 and r.json()["app"] == "ok"

        step("Access list op 'Satisfy Any': VaultX weigert")
        admin.patch(cbase, json={"authentik_outpost_url": outpost}).raise_for_status()
        modified = npm.get(f"/nginx/proxy-hosts/{ids['lan']}").json()["modified_on"]
        change = protect("lan")
        assert change["status"] == "refused" and "Satisfy Any" in change["message"], change
        assert npm.get(f"/nginx/proxy-hosts/{ids['lan']}").json()["modified_on"] == modified

        journal = admin.get(f"{cbase}/changes").json()
        print("    journaal:", [(c["domain"].split(".")[0], c["action"], c["status"]) for c in journal])
        audit = admin.get("/api/v1/audit", params={"action": "npm_host.", "limit": 50}).json()["items"]
        assert {a["outcome"] for a in audit} == {"success", "failure"}
        step("OK: Authentik-bescherming via VaultX werkt tegen echte NPM, met rollback")
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=10)
        except subprocess.TimeoutExpired:
            backend.kill()
        server.shutdown()


if __name__ == "__main__":
    main()
