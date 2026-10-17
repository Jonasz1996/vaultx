"""End-to-end test fase 4: VaultX beschermt een host in één stap, met echte Authentik én echte NPM.

Wat het doet:

1. In Authentik (met het beheertoken): een serviceaccount met enkel de rechten
   uit docs/npm.md, een API-token daarvoor, een groep ``vaultx:<org>`` met
   gebruiker alice, en gebruiker eve zonder die groep. Bij de ingebouwde outpost
   wordt ``authentik_host`` ingevuld (nodig voor de aanmelding in de browser).
2. In NPM: twee proxy hosts onder .vaultx-ak-e2e.test naar een kleine app die
   de X-authentik-*-headers terugtoont.
3. De VaultX-backend start met het token van het serviceaccount (niet het
   beheertoken). Via de VaultX-API:
   - outposts opvragen en de ingebouwde outpost op de NPM-koppeling zetten;
   - "app" beschermen met toegang voor de organisatie: VaultX maakt provider,
     applicatie, groepsbinding en outpost-toewijzing aan, zet de config in NPM
     en controleert met de echte outpost dat een bezoeker zonder sessie naar
     Authentik gaat;
   - met --browser: alice meldt zich aan en ziet de app met haar naam in de
     X-authentik-username-header, eve wordt door Authentik geweigerd;
   - "open" beschermen met toegang voor iedereen (geen groepsbinding);
   - bescherming weghalen: de Authentik-objecten zijn weg, de host is weer open;
   - een outpost-URL die niet antwoordt: VaultX zet NPM terug en verwijdert
     wat het in Authentik aanmaakte.

    cd backend && python ../e2e/authentik_npm_e2e.py http://localhost:81 admin@example.com wachtwoord \\
        --authentik http://localhost:9000 --authentik-token <beheertoken> --browser

NPM moet poort 80 aanbieden op --proxy (de browser gebruikt gewone http-URL's)
en de Authentik-server bereiken op --outpost-url. Zie e2e/README.md.
"""

from __future__ import annotations

import argparse
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
SUFFIX = ".vaultx-ak-e2e.test"
CSRF = {"X-VaultX-CSRF": "1"}
# Rechten van het serviceaccount: exact de lijst uit docs/npm.md.
PERMISSIONS = [
    "authentik_providers_proxy.view_proxyprovider",
    "authentik_providers_proxy.add_proxyprovider",
    "authentik_providers_proxy.delete_proxyprovider",
    "authentik_core.view_application",
    "authentik_core.add_application",
    "authentik_core.delete_application",
    "authentik_core.view_group",
    "authentik_policies.add_policybinding",
    "authentik_outposts.view_outpost",
    "authentik_outposts.change_outpost",
    "authentik_flows.view_flow",
]
PASSWORD = "ak-e2e-Wachtwoord-123"


class App(BaseHTTPRequestHandler):
    """De applicatie achter NPM: toont wie de outpost doorgaf."""

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps(
            {
                "app": "ok",
                "user": self.headers.get("X-authentik-username"),
                "groups": self.headers.get("X-authentik-groups"),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Authentik:
    """Beheer-API van Authentik, enkel voor de voorbereiding en de controles van deze test."""

    def __init__(self, url: str, token: str) -> None:
        self.c = httpx.Client(
            base_url=f"{url.rstrip('/')}/api/v3",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            trust_env=False,
        )

    def wait_ready(self, flow: str, timeout: float = 600) -> None:
        """Wacht tot de worker de standaardblueprints toepaste (flows bestaan)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.one("/flows/instances/", slug=flow):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(5)
        raise SystemExit(f"Authentik is niet klaar: flow {flow} bestaat niet")

    def get(self, path: str, **params) -> list[dict]:
        return self.c.get(path, params={**params, "page_size": 100}).raise_for_status().json()["results"]

    def one(self, path: str, **params) -> dict | None:
        found = self.get(path, **params)
        return found[0] if found else None

    def ensure_user(self, username: str, groups: list[str]) -> None:
        user = self.one("/core/users/", username=username)
        body = {"username": username, "name": username.title(), "groups": groups, "is_active": True}
        if user is None:
            user = self.c.post("/core/users/", json=body).raise_for_status().json()
        else:
            self.c.patch(f"/core/users/{user['pk']}/", json=body).raise_for_status()
        self.c.post(f"/core/users/{user['pk']}/set_password/", json={"password": PASSWORD}).raise_for_status()

    def ensure_group(self, name: str, **extra) -> dict:
        g = next((g for g in self.get("/core/groups/", search=name) if g["name"] == name), None)
        return g or self.c.post("/core/groups/", json={"name": name, **extra}).raise_for_status().json()

    def service_account_token(self, slug: str) -> str:
        """Serviceaccount met enkel PERMISSIONS (via rol en groep) en een API-token."""
        role = self.one("/rbac/roles/", name=f"{slug}-role") or (
            self.c.post("/rbac/roles/", json={"name": f"{slug}-role"}).raise_for_status().json()
        )
        self.c.post(
            f"/rbac/permissions/assigned_by_roles/{role['pk']}/assign/", json={"permissions": PERMISSIONS}
        ).raise_for_status()
        group = self.ensure_group(f"{slug}-group", roles=[role["pk"]])
        user = self.one("/core/users/", username=slug)
        if user is None:
            self.c.post(
                "/core/users/service_account/", json={"name": slug, "create_group": False, "expiring": False}
            ).raise_for_status()
            user = self.one("/core/users/", username=slug)
        assert user is not None
        self.c.post(f"/core/groups/{group['pk']}/add_user/", json={"pk": user["pk"]}).raise_for_status()
        ident = f"{slug}-api"
        if self.one("/core/tokens/", identifier=ident) is None:
            body = {"identifier": ident, "intent": "api", "user": user["pk"], "expiring": False}
            self.c.post("/core/tokens/", json=body).raise_for_status()
        return self.c.get(f"/core/tokens/{ident}/view_key/").raise_for_status().json()["key"]

    def embedded_outpost(self, browser_url: str) -> dict:
        embedded = "goauthentik.io/outposts/embedded"
        outpost = next(o for o in self.get("/outposts/instances/") if o.get("managed") == embedded)
        config = {**outpost["config"], "authentik_host": browser_url}
        self.c.patch(f"/outposts/instances/{outpost['pk']}/", json={"config": config}).raise_for_status()
        return outpost

    def cleanup(self) -> None:
        for p in self.get("/providers/proxy/", search=SUFFIX):
            if p["assigned_application_slug"]:
                self.c.delete(f"/core/applications/{p['assigned_application_slug']}/")
            self.c.delete(f"/providers/proxy/{p['pk']}/")


def seed(npm: httpx.Client, app_host: str, app_port: int) -> dict[str, int]:
    for h in npm.get("/nginx/proxy-hosts").raise_for_status().json():
        if any(d.endswith(SUFFIX) for d in h["domain_names"]):
            npm.delete(f"/nginx/proxy-hosts/{h['id']}").raise_for_status()
    ids = {}
    for name in ("app", "open"):
        body = {
            "domain_names": [f"{name}{SUFFIX}"],
            "forward_scheme": "http",
            "forward_host": app_host,
            "forward_port": app_port,
            # Over http blokkeert "Block Common Exploits" de aanmeldredirect (?rd=http://...); VaultX
            # waarschuwt daarvoor (block_exploits_http).
            "block_exploits": False,
        }
        ids[name] = npm.post("/nginx/proxy-hosts", json=body).raise_for_status().json()["id"]
    return ids


def visit(proxy: str, port: int, domain: str, want: int | None = None) -> httpx.Response:
    for _ in range(20):
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as c:
            r = c.get(f"http://{proxy}:{port}/", headers={"Host": domain})
        if want is None or r.status_code == want:
            return r
        time.sleep(0.5)
    return r


def browser_check(proxy: str, shots: Path | None) -> None:
    """alice komt via de echte Authentik-aanmelding bij de app; eve wordt geweigerd."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=[f"--host-resolver-rules=MAP *{SUFFIX} {proxy}"])
        for user, allowed in (("alice-ak-e2e", True), ("eve-ak-e2e", False)):
            page = browser.new_context(ignore_https_errors=True).new_page()
            page.goto(f"http://app{SUFFIX}/")
            try:
                page.wait_for_selector("input[name=uidField]", timeout=60000)
            except Exception:
                print("    aanmeldpagina niet gevonden op", page.url, page.content()[:500])
                if shots:
                    page.screenshot(path=str(shots / "fout.png"))
                raise
            page.fill("input[name=uidField]", user)
            page.keyboard.press("Enter")
            # Zoals in authentik_e2e.py: de wachtwoordstap is pas klaar als "Not you?" er staat.
            page.get_by_text("Not you?").wait_for(timeout=30000)
            page.wait_for_timeout(1000)
            page.fill("input[name=password]", PASSWORD)
            page.get_by_role("button", name="Continue").click()
            if allowed:
                try:
                    page.wait_for_url(f"http://app{SUFFIX}/**", timeout=60000)
                except Exception:
                    print("    niet terug bij de app:", page.url)
                    if shots:
                        page.screenshot(path=str(shots / "fout.png"))
                    raise
                data = json.loads(page.inner_text("body"))
                assert data["app"] == "ok" and data["user"] == user, data
                print(f"    {user}: binnen, app ziet X-authentik-username={data['user']}")
            else:
                page.wait_for_function(
                    "() => document.body.innerText.match(/Permission denied|Toegang geweigerd|denied/i) "
                    "|| [...document.querySelectorAll('*')].some(e => e.shadowRoot && "
                    "/Permission denied|denied/i.test(e.shadowRoot.textContent))",
                    timeout=60000,
                )
                assert not page.url.startswith(f"http://app{SUFFIX}/"), page.url
                print(f"    {user}: geweigerd door Authentik ({page.url.split('?')[0]})")
            if shots:
                page.screenshot(path=str(shots / f"browser-{user.split('-')[0]}.png"))
            page.context.close()
        browser.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("npm_url")
    ap.add_argument("identity")
    ap.add_argument("secret")
    ap.add_argument("--authentik", default="http://localhost:9000", help="Authentik vanaf deze machine")
    ap.add_argument("--authentik-token", required=True, help="beheertoken (enkel voor de voorbereiding)")
    ap.add_argument("--proxy", default="127.0.0.1", help="adres waarop NPM poort 80 aanbiedt")
    ap.add_argument("--http-port", type=int, default=80)
    ap.add_argument("--docker-host", default="172.17.0.1", help="hoe de NPM-container deze machine bereikt")
    ap.add_argument("--outpost-url", help="hoe nginx in NPM Authentik bereikt (standaard docker-host:9000)")
    ap.add_argument("--browser", action="store_true", help="ook aanmelden in Chromium (playwright)")
    ap.add_argument("--screenshots", type=Path)
    args = ap.parse_args()
    outpost_url = args.outpost_url or f"http://{args.docker_host}:9000"
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)

    app_port = free_port()
    server = ThreadingHTTPServer(("0.0.0.0", app_port), App)  # noqa: S104
    threading.Thread(target=server.serve_forever, daemon=True).start()

    slug = f"ak-e2e-{int(time.time())}"
    ak = Authentik(args.authentik, args.authentik_token)
    step("Authentik voorbereiden: serviceaccount met beperkte rechten, groep en gebruikers")
    ak.wait_ready("default-provider-authorization-implicit-consent")
    ak.cleanup()
    sa_token = ak.service_account_token("vaultx-ak-e2e")
    org_group = ak.ensure_group(f"vaultx:{slug}")
    ak.ensure_user("alice-ak-e2e", [org_group["pk"]])
    ak.ensure_user("eve-ak-e2e", [])
    embedded = ak.embedded_outpost(args.authentik)

    npm = httpx.Client(base_url=f"{args.npm_url.rstrip('/')}/api", timeout=60, trust_env=False)
    token = npm.post("/tokens", json={"identity": args.identity, "secret": args.secret}).raise_for_status()
    npm.headers["Authorization"] = f"Bearer {token.json()['token']}"
    step("NPM vullen met e2e-hosts")
    ids = seed(npm, args.docker_host, app_port)
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
        "VAULTX_AUTHENTIK_API_URL": args.authentik,
        "VAULTX_AUTHENTIK_API_TOKEN": sa_token,
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

        step("VaultX: organisatie, outposts opvragen, NPM-koppeling met de ingebouwde outpost")
        admin = httpx.Client(base_url=base, trust_env=False, timeout=120, headers=CSRF)
        login(admin, fake, "admin-ak-e2e", "admin-ak-e2e@example.com", ["vaultx-admins"])
        org = admin.post("/api/v1/organizations", json={"slug": slug, "name": "AK e2e"}).raise_for_status()
        org_id = org.json()["id"]
        outposts = admin.get(f"/api/v1/organizations/{org_id}/authentik/outposts").raise_for_status().json()
        assert outposts["configured"] and not outposts.get("error"), outposts
        assert embedded["pk"] in [o["pk"] for o in outposts["outposts"]], outposts
        conn = admin.post(
            f"/api/v1/organizations/{org_id}/npm-connections",
            json={
                "name": "NPM ak-e2e",
                "base_url": args.npm_url,
                "identity": args.identity,
                "secret": args.secret,
                "write_enabled": True,
                "authentik_outpost_url": outpost_url,
                "authentik_outpost_pk": embedded["pk"],
                "probe_host": args.proxy,
                "probe_http_port": args.http_port,
            },
        ).raise_for_status()
        cbase = f"/api/v1/organizations/{org_id}/npm-connections/{conn.json()['id']}"
        admin.post(f"{cbase}/sync").raise_for_status()
        hosts = {h["npm_id"]: h for h in admin.get(f"{cbase}/hosts").raise_for_status().json()}
        hid = {name: hosts[i]["id"] for name, i in ids.items()}

        def protect(name: str, action: str = "protect", access: str = "organization") -> dict:
            plan = admin.get(
                f"{cbase}/hosts/{hid[name]}/protection", params={"action": action, "access": access}
            ).json()
            blocks = [c["message"] for c in plan["checks"] if c["level"] == "block"]
            assert not blocks, blocks
            for s in plan["steps"]:
                print(f"      - {s}")
            started = time.monotonic()
            r = admin.post(
                f"{cbase}/hosts/{hid[name]}/protection",
                json={"action": action, "expected_modified_on": plan["modified_on"], "access": access},
            )
            assert r.status_code == 200, r.text
            change = r.json()
            took = time.monotonic() - started
            print(f"    {name:5} {action:9} -> {change['status']} ({took:.1f}s): {change['message']}")
            return change

        step("'app' beschermen, toegang voor de organisatie: Authentik en NPM in één stap")
        change = protect("app")
        assert change["status"] == "applied", change
        assert change["probe_after"]["status"] == 302, change["probe_after"]
        state = change["authentik"]["state"]
        assert state["provider_created"] and state["application_created"] and state["outpost_assigned"], state
        provider = ak.one("/providers/proxy/", search=f"app{SUFFIX}")
        assert provider and provider["mode"] == "forward_single", provider
        assert provider["external_host"] == f"http://app{SUFFIX}", provider
        app = ak.one("/core/applications/", slug=state["application_slug"], superuser_full_list="true")
        assert app is not None, state
        binding = ak.get("/policies/bindings/", target=app["pk"])
        assert [b["group_obj"]["name"] for b in binding] == [f"vaultx:{slug}"], binding
        outpost_now = ak.c.get(f"/outposts/instances/{embedded['pk']}/").json()
        assert provider["pk"] in outpost_now["providers"], outpost_now["providers"]
        r = visit(args.proxy, args.http_port, f"app{SUFFIX}")
        assert r.status_code == 302 and "/outpost.goauthentik.io/start" in r.headers["location"], (
            r.status_code
        )
        if args.browser:
            step("Browser: alice (lid van de organisatie) en eve (geen lid) melden zich aan")
            browser_check(args.proxy, args.screenshots)

        step("'open' beschermen, toegang voor iedereen: geen groepsbinding")
        change = protect("open", access="all")
        assert change["status"] == "applied", change
        assert change["authentik"]["state"]["groups"] == [], change["authentik"]

        step("Bescherming weghalen: Authentik-objecten weg, hosts weer open")
        for name in ("app", "open"):
            change = protect(name, "unprotect")
            assert change["status"] == "applied" and "In Authentik verwijderd" in change["message"], change
            assert ak.one("/providers/proxy/", search=f"{name}{SUFFIX}") is None
            r = visit(args.proxy, args.http_port, f"{name}{SUFFIX}", want=200)
            assert r.status_code == 200 and r.json()["user"] is None, r.status_code
        outpost_now = ak.c.get(f"/outposts/instances/{embedded['pk']}/").json()
        assert provider["pk"] not in outpost_now["providers"]

        step("Outpost-URL die niet antwoordt: NPM teruggezet, Authentik-objecten weer weg")
        admin.patch(cbase, json={"authentik_outpost_url": f"http://{args.docker_host}:9"}).raise_for_status()
        change = protect("app")
        assert change["status"] == "rolled_back", change
        assert change["authentik"].get("undone"), change["authentik"]
        assert ak.one("/providers/proxy/", search=f"app{SUFFIX}") is None
        r = visit(args.proxy, args.http_port, f"app{SUFFIX}", want=200)
        assert r.status_code == 200 and r.json()["app"] == "ok"

        audit = admin.get("/api/v1/audit", params={"action": "npm_host.", "limit": 50}).json()["items"]
        assert all("authentik" in a["details"] for a in audit if a["outcome"] != "denied"), audit[0]
        step("OK: VaultX maakt de Authentik-kant zelf aan, met echte Authentik en NPM, inclusief rollback")
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=10)
        except subprocess.TimeoutExpired:
            backend.kill()
        server.shutdown()


if __name__ == "__main__":
    main()
