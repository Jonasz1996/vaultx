"""End-to-end test fase 6: een app publiceren via VaultX, met echte Authentik én echte NPM.

Wat het doet:

1. Authentik voorbereiden zoals in authentik_npm_e2e.py: serviceaccount met enkel
   de rechten uit docs/npm.md, groep ``vaultx:<org>`` met alice, eve zonder groep.
2. In NPM enkel een wildcardcertificaat ``*.vaultx-pub-e2e.test`` (self-signed).
   Er staan geen hosts voor de e2e-domeinen: VaultX maakt ze zelf aan.
3. Via de VaultX-API (backend met het token van het serviceaccount):
   - certificaten opvragen: de private key komt niet mee;
   - "app" publiceren over https, met Authentik (toegang: de organisatie), thema en
     beveiligingsheaders: VaultX maakt provider, applicatie, binding, outpost-
     toewijzing en de proxy host aan, controleert met de echte outpost, en de app
     staat in de catalogus;
   - een bezoeker zonder sessie gaat naar Authentik; met --browser: alice komt
     binnen en ziet de app met het thema erin, eve wordt geweigerd;
   - "open" publiceren zonder Authentik over http: de app antwoordt meteen, met het
     thema en de headers;
   - hetzelfde domein opnieuw: geweigerd, niets gewijzigd;
   - een outpost-URL die niet antwoordt: de nieuwe host is weer weg uit NPM en de
     Authentik-objecten ook;
   - beide apps depubliceren: hosts weg uit NPM, Authentik opgeruimd, catalogus leeg.

    cd backend && python ../e2e/publish_e2e.py http://localhost:81 admin@example.com wachtwoord \\
        --authentik http://localhost:9000 --authentik-token <beheertoken> --browser

NPM moet poort 80 en 443 aanbieden op --proxy en Authentik bereiken op
--outpost-url. Zie e2e/README.md.
"""

from __future__ import annotations

import argparse
import datetime
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

from authentik_npm_e2e import PASSWORD, Authentik  # noqa: E402
from bitwarden_e2e import free_port, login, step  # noqa: E402

from tests.fake_oidc import FakeOIDCProvider  # noqa: E402

DB = os.environ.get("VAULTX_DATABASE_URL", "postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_test")
SUFFIX = ".vaultx-pub-e2e.test"
CSRF = {"X-VaultX-CSRF": "1"}
THEME = "https://css.example.be/{app}.css"


class App(BaseHTTPRequestHandler):
    """De app achter NPM: een HTML-pagina met de gebruiker die de outpost doorgaf."""

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        user = self.headers.get("X-authentik-username") or "-"
        body = (
            f"<html><head><title>pub-e2e</title></head><body><p id='user'>{user}</p>"
            f"<p id='enc'>{self.headers.get('Accept-Encoding') or '-'}</p></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def wildcard_cert(npm: httpx.Client) -> int:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    name = f"*{SUFFIX}"
    for c in npm.get("/nginx/certificates").raise_for_status().json():
        if c.get("nice_name") == "VaultX publish-e2e":
            return c["id"]
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), False)
        .sign(key, hashes.SHA256())
    )
    created = npm.post("/nginx/certificates", json={"provider": "other", "nice_name": "VaultX publish-e2e"})
    cert_id = created.raise_for_status().json()["id"]
    npm.post(
        f"/nginx/certificates/{cert_id}/upload",
        files={
            "certificate": ("cert.pem", cert.public_bytes(serialization.Encoding.PEM)),
            "certificate_key": (
                "key.pem",
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ),
            ),
        },
    ).raise_for_status()
    return cert_id


def clean_npm(npm: httpx.Client) -> None:
    for h in npm.get("/nginx/proxy-hosts").raise_for_status().json():
        if any(d.endswith(SUFFIX) for d in h["domain_names"]):
            npm.delete(f"/nginx/proxy-hosts/{h['id']}").raise_for_status()


def npm_hosts(npm: httpx.Client) -> dict[str, dict]:
    return {
        h["domain_names"][0]: h
        for h in npm.get("/nginx/proxy-hosts").raise_for_status().json()
        if any(d.endswith(SUFFIX) for d in h["domain_names"])
    }


def visit(
    proxy: str, port: int, domain: str, *, https: bool, want: int | None = None, text: str | None = None
) -> httpx.Response:
    """Host aanspreken via NPM; nginx herlaadt asynchroon, dus even herhalen tot het antwoord klopt."""
    scheme = "https" if https else "http"
    ext = {"sni_hostname": domain} if https else {}
    for _ in range(30):
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=10, verify=False) as c:  # noqa: S501
            r = c.get(f"{scheme}://{proxy}:{port}/", headers={"Host": domain}, extensions=ext)
        if (want is None or r.status_code == want) and (text is None or text in r.text):
            return r
        time.sleep(0.5)
    return r


def browser_check(proxy: str, shots: Path | None) -> None:
    """alice komt via Authentik bij de app en ziet het thema; eve wordt geweigerd."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=[f"--host-resolver-rules=MAP *{SUFFIX} {proxy}", "--no-proxy-server"]
        )
        for user, allowed in (("alice-ak-e2e", True), ("eve-ak-e2e", False)):
            ctx = browser.new_context(ignore_https_errors=True, locale="en-US")
            page = ctx.new_page()
            page.goto(f"https://app{SUFFIX}/")
            page.wait_for_selector("input[name=uidField]", timeout=60000)
            if shots and allowed:
                page.screenshot(path=str(shots / "browser-aanmelden.png"))
            page.fill("input[name=uidField]", user)
            page.keyboard.press("Enter")
            page.get_by_text("Not you?").wait_for(timeout=30000)
            page.wait_for_timeout(1000)
            page.fill("input[name=password]", PASSWORD)
            page.get_by_role("button", name="Continue").click()
            if allowed:
                page.wait_for_url(f"https://app{SUFFIX}/**", timeout=60000)
                page.wait_for_selector("#user")
                assert page.inner_text("#user") == user, page.content()
                href = page.eval_on_selector("link[rel=stylesheet]", "e => e.getAttribute('href')")
                assert href == "https://css.example.be/app.css", href
                print(f"    {user}: binnen, X-authentik-username={user}, thema {href}")
            else:
                page.wait_for_function(
                    "() => document.body.innerText.match(/Permission denied|denied/i) "
                    "|| [...document.querySelectorAll('*')].some(e => e.shadowRoot && "
                    "/Permission denied|denied/i.test(e.shadowRoot.textContent))",
                    timeout=60000,
                )
                assert not page.url.startswith(f"https://app{SUFFIX}/"), page.url
                print(f"    {user}: geweigerd door Authentik")
            if shots:
                page.screenshot(path=str(shots / f"browser-{user.split('-')[0]}.png"))
            ctx.close()
        browser.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("npm_url")
    ap.add_argument("identity")
    ap.add_argument("secret")
    ap.add_argument("--authentik", default="http://localhost:9000", help="Authentik vanaf deze machine")
    ap.add_argument("--authentik-token", required=True, help="beheertoken (enkel voor de voorbereiding)")
    ap.add_argument("--proxy", default="127.0.0.1", help="adres waarop NPM poort 80/443 aanbiedt")
    ap.add_argument("--http-port", type=int, default=80)
    ap.add_argument("--https-port", type=int, default=443)
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

    slug = f"pub-e2e-{int(time.time())}"
    ak = Authentik(args.authentik, args.authentik_token)
    step("Authentik voorbereiden: serviceaccount met beperkte rechten, groep en gebruikers")
    ak.wait_ready("default-provider-authorization-implicit-consent")
    for p in ak.get("/providers/proxy/", search=SUFFIX):
        if p["assigned_application_slug"]:
            ak.c.delete(f"/core/applications/{p['assigned_application_slug']}/")
        ak.c.delete(f"/providers/proxy/{p['pk']}/")
    sa_token = ak.service_account_token("vaultx-ak-e2e")
    org_group = ak.ensure_group(f"vaultx:{slug}")
    ak.ensure_user("alice-ak-e2e", [org_group["pk"]])
    ak.ensure_user("eve-ak-e2e", [])
    embedded = ak.embedded_outpost(args.authentik)

    npm = httpx.Client(base_url=f"{args.npm_url.rstrip('/')}/api", timeout=60, trust_env=False)
    token = npm.post("/tokens", json={"identity": args.identity, "secret": args.secret}).raise_for_status()
    npm.headers["Authorization"] = f"Bearer {token.json()['token']}"
    step("NPM: e2e-hosts weg, wildcardcertificaat klaarzetten")
    clean_npm(npm)
    cert_id = wildcard_cert(npm)

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

        step("VaultX: organisatie en NPM-koppeling met outpost en thema-sjabloon")
        admin = httpx.Client(base_url=base, trust_env=False, timeout=120, headers=CSRF)
        login(admin, fake, "admin-pub-e2e", "admin-pub-e2e@example.com", ["vaultx-admins"])
        org = admin.post(
            "/api/v1/organizations", json={"slug": slug, "name": "Publish e2e"}
        ).raise_for_status()
        org_id = org.json()["id"]
        conn = admin.post(
            f"/api/v1/organizations/{org_id}/npm-connections",
            json={
                "name": "NPM publish-e2e",
                "base_url": args.npm_url,
                "identity": args.identity,
                "secret": args.secret,
                "write_enabled": True,
                "authentik_outpost_url": outpost_url,
                "authentik_outpost_pk": embedded["pk"],
                "probe_host": args.proxy,
                "probe_http_port": args.http_port,
                "probe_https_port": args.https_port,
                "theme_css_template": THEME,
            },
        ).raise_for_status()
        cbase = f"/api/v1/organizations/{org_id}/npm-connections/{conn.json()['id']}"

        certs = admin.get(f"{cbase}/certificates").raise_for_status().json()
        mine = next(c for c in certs if c["id"] == cert_id)
        assert mine["domain_names"] == [f"*{SUFFIX}"], mine
        assert "PRIVATE KEY" not in str(certs) and all("meta" not in c for c in certs)
        print(f"    {len(certs)} certificaat/certificaten, zonder sleutels")

        def publish(name: str, **extra) -> dict:
            body = {
                "name": name.title(),
                "domain": f"{name}{SUFFIX}",
                "forward_scheme": "http",
                "forward_host": args.docker_host,
                "forward_port": app_port,
                "theme_css_url": THEME.replace("{app}", name),
                **extra,
            }
            plan = admin.post(f"{cbase}/publish/preview", json=body).raise_for_status().json()
            for s in plan["steps"]:
                print(f"      - {s}")
            started = time.monotonic()
            r = admin.post(f"{cbase}/publish", json=body)
            assert r.status_code == 200, r.text
            change = r.json()
            took = time.monotonic() - started
            print(f"    {name:5} publiceren -> {change['status']} ({took:.1f}s): {change['message']}")
            return {"plan": plan, "change": change}

        step("'app' publiceren over https, met Authentik (de organisatie), thema en headers")
        res = publish("app", certificate_id=cert_id)
        assert res["plan"]["can_apply"], res["plan"]["checks"]
        change = res["change"]
        assert change["status"] == "applied", change
        assert change["probe_after"]["status"] == 302, change["probe_after"]
        state = change["authentik"]["state"]
        assert state["provider_created"] and state["application_created"] and state["outpost_assigned"], state
        provider = ak.one("/providers/proxy/", search=f"app{SUFFIX}")
        assert provider and provider["external_host"] == f"https://app{SUFFIX}", provider
        app = ak.one("/core/applications/", slug=state["application_slug"], superuser_full_list="true")
        assert app is not None
        binding = ak.get("/policies/bindings/", target=app["pk"])
        assert [b["group_obj"]["name"] for b in binding] == [f"vaultx:{slug}"], binding
        created = npm_hosts(npm)[f"app{SUFFIX}"]
        assert created["certificate_id"] == cert_id and created["ssl_forced"] and created["hsts_enabled"]
        r = visit(args.proxy, args.https_port, f"app{SUFFIX}", https=True)
        assert r.status_code == 302 and "/outpost.goauthentik.io/start" in r.headers["location"], (
            r.status_code
        )
        r = visit(args.proxy, args.http_port, f"app{SUFFIX}", https=False)
        assert r.status_code == 301 and r.headers["location"].startswith("https://"), r.status_code
        hosts = {h["domain_names"][0]: h for h in admin.get(f"{cbase}/hosts").raise_for_status().json()}
        assert hosts[f"app{SUFFIX}"]["vaultx_published"] and hosts[f"app{SUFFIX}"]["forward_auth"]
        catalog = admin.get("/api/v1/catalog", params={"organization_id": org_id}).raise_for_status().json()
        item = next(a for a in catalog if a["name"] == "App")
        assert item["status"] == "protected" and item["url"] == f"https://app{SUFFIX}", item
        print(f"    catalogus: '{item['name']}' ({item['status']}), {item['url']}")
        if args.browser:
            step("Browser: alice komt binnen en ziet het thema, eve wordt geweigerd")
            browser_check(args.proxy, args.screenshots)

        step("'open' publiceren zonder Authentik, over http")
        res = publish("open", protect=False)
        change = res["change"]
        assert change["status"] == "applied", change
        r = visit(args.proxy, args.http_port, f"open{SUFFIX}", https=False, want=200, text="pub-e2e")
        assert r.status_code == 200, r.status_code
        assert (
            '<link rel="stylesheet" type="text/css" href="https://css.example.be/open.css"></head>' in r.text
        )
        assert "<p id='enc'>-</p>" in r.text, "Accept-Encoding gaat niet naar de app (sub_filter)"
        assert r.headers.get("x-frame-options") == "SAMEORIGIN" and r.headers.get("x-content-type-options")
        print("    open: 200, thema ingevoegd, beveiligingsheaders aanwezig")

        step("Hetzelfde domein nog eens: geweigerd")
        res = publish("open", protect=False)
        assert res["change"]["status"] == "refused" and "staat al in NPM" in res["change"]["message"]

        step("Outpost-URL die niet antwoordt: nieuwe host en Authentik-objecten weer weg")
        admin.patch(cbase, json={"authentik_outpost_url": f"http://{args.docker_host}:9"}).raise_for_status()
        res = publish("kapot", certificate_id=cert_id)
        change = res["change"]
        assert change["status"] == "rolled_back", change
        assert change["authentik"].get("undone"), change["authentik"]
        assert f"kapot{SUFFIX}" not in npm_hosts(npm)
        assert ak.one("/providers/proxy/", search=f"kapot{SUFFIX}") is None
        admin.patch(cbase, json={"authentik_outpost_url": outpost_url}).raise_for_status()

        step("Beide apps depubliceren: weg uit NPM, Authentik en de catalogus")
        hosts = {h["domain_names"][0]: h for h in admin.get(f"{cbase}/hosts").raise_for_status().json()}
        for name in ("app", "open"):
            hid = hosts[f"{name}{SUFFIX}"]["id"]
            plan = admin.get(f"{cbase}/hosts/{hid}/unpublish").raise_for_status().json()
            assert plan["can_apply"], plan["checks"]
            for s in plan["steps"]:
                print(f"      - {s}")
            change = admin.post(f"{cbase}/hosts/{hid}/unpublish").raise_for_status().json()
            print(f"    {name:5} depubliceren -> {change['status']}: {change['message']}")
            assert change["status"] == "applied", change
        assert not npm_hosts(npm)
        assert ak.one("/providers/proxy/", search=SUFFIX) is None
        outpost_now = ak.c.get(f"/outposts/instances/{embedded['pk']}/").json()
        assert provider["pk"] not in outpost_now["providers"]
        catalog = admin.get("/api/v1/catalog", params={"organization_id": org_id}).raise_for_status().json()
        assert not catalog, catalog
        r = visit(args.proxy, args.http_port, f"app{SUFFIX}", https=False)
        assert "pub-e2e" not in r.text, "de app is nog bereikbaar"

        audit = admin.get("/api/v1/audit", params={"action": "npm_host.", "limit": 50}).json()["items"]
        actions = {a["action"] for a in audit}
        assert {"npm_host.publish", "npm_host.unpublish"} <= actions, actions
        step("OK: app publiceren en depubliceren met echte NPM en Authentik, inclusief rollback")
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=10)
        except subprocess.TimeoutExpired:
            backend.kill()
        server.shutdown()


if __name__ == "__main__":
    main()
