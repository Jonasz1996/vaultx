"""End-to-end test fase 5: automatische login voor Grafana, met echte Authentik én echte Grafana.

Wat het doet:

1. In Authentik (met het beheertoken): een serviceaccount met enkel de rechten
   uit docs/autologin.md (die van fase 4 plus vijf voor OIDC-providers), een
   API-token daarvoor, de groepen ``vaultx:<org>`` en ``vaultx:<org>:admin``, en
   gebruikers alice (lid), carol (beheerder van de organisatie) en eve (geen lid).
2. De VaultX-backend start met het token van het serviceaccount. Via de API:
   - een app "Grafana" in de catalogus met de URL van Grafana;
   - voorbeeld en inrichten mét Grafana-beheerder: VaultX maakt in Authentik een
     OAuth2/OpenID-provider, applicatie en groepsbinding, zet de generic
     OAuth-login in Grafana (SSO settings API, zonder herstart) en controleert
     dat Grafana een bezoeker zonder sessie naar Authentik stuurt;
   - met --browser: carol meldt zich eerst bij Authentik aan en opent dan
     Grafana: ze zit er meteen in, als Admin, zonder tweede login (het
     hoofdscenario). alice komt via de Authentik-aanmelding binnen als Viewer,
     eve wordt door Authentik geweigerd;
   - de config met client secret opvragen;
   - weghalen: Grafana toont weer zijn eigen aanmeldformulier en de
     Authentik-objecten zijn weg;
   - het generieke OIDC-sjabloon met een eigen redirect URI.

    cd backend && python ../e2e/autologin_e2e.py --authentik http://localhost:9000 \\
        --authentik-token <beheertoken> --grafana http://localhost:3000 \\
        --grafana-password <wachtwoord> --browser

Grafana moet Authentik bereiken op --authentik (token- en userinfo-URL) en zijn
root_url moet --grafana zijn. Zie e2e/README.md.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "e2e"))

import authentik_npm_e2e  # noqa: E402
from authentik_npm_e2e import PASSWORD, Authentik  # noqa: E402
from bitwarden_e2e import free_port, login, step  # noqa: E402

from tests.fake_oidc import FakeOIDCProvider  # noqa: E402

DB = os.environ.get("VAULTX_DATABASE_URL", "postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_test")
CSRF = {"X-VaultX-CSRF": "1"}
# Rechten van het serviceaccount: exact de lijst uit docs/autologin.md (fase 4 + fase 5).
LOGIN_PERMISSIONS = [
    "authentik_providers_oauth2.view_oauth2provider",
    "authentik_providers_oauth2.add_oauth2provider",
    "authentik_providers_oauth2.delete_oauth2provider",
    "authentik_providers_oauth2.view_scopemapping",
    "authentik_crypto.view_certificatekeypair",
]
authentik_npm_e2e.PERMISSIONS = [*authentik_npm_e2e.PERMISSIONS, *LOGIN_PERMISSIONS]


def grafana_client(url: str, password: str) -> httpx.Client:
    return httpx.Client(
        base_url=f"{url.rstrip('/')}/api", auth=("admin", password), timeout=30, trust_env=False
    )


def grafana_login_status(url: str) -> tuple[int, str]:
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as c:
        r = c.get(f"{url.rstrip('/')}/login")
    return r.status_code, r.headers.get("location", "")


def authentik_login(page, user: str) -> None:
    page.wait_for_selector("input[name=uidField]", timeout=60000)
    page.fill("input[name=uidField]", user)
    page.keyboard.press("Enter")
    # Zoals in authentik_e2e.py: de wachtwoordstap is pas klaar als "Not you?" er staat.
    page.get_by_text("Not you?").wait_for(timeout=30000)
    page.wait_for_timeout(1000)
    page.fill("input[name=password]", PASSWORD)
    page.get_by_role("button", name="Continue").click()


def grafana_user(page, grafana: str) -> dict:
    return page.evaluate(
        """async (base) => {
            const u = await (await fetch(base + "/api/user")).json();
            const orgs = await (await fetch(base + "/api/user/orgs")).json();
            return {login: u.login, email: u.email, role: orgs[0] && orgs[0].role};
        }""",
        grafana.rstrip("/"),
    )


def browser_check(authentik: str, grafana: str, shots: Path | None) -> None:
    from playwright.sync_api import sync_playwright

    def shot(page, name: str) -> None:
        if shots:
            page.screenshot(path=str(shots / f"{name}.png"))

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # Hoofdscenario: carol is al bij Authentik aangemeld en opent dan Grafana.
        page = browser.new_context(locale="en-US").new_page()
        page.goto(f"{authentik}/if/user/")
        authentik_login(page, "carol-login-e2e")
        page.wait_for_url(f"{authentik}/if/user/**", timeout=60000)
        print("    carol: aangemeld bij Authentik")
        started = time.monotonic()
        page.goto(f"{grafana}/")
        try:
            page.wait_for_url(f"{grafana}/**", timeout=60000)
            page.wait_for_function("() => !location.pathname.startsWith('/login')", timeout=60000)
        except Exception:
            print("    niet in Grafana:", page.url)
            shot(page, "fout")
            raise
        assert page.locator("input[name=uidField]").count() == 0
        me = grafana_user(page, grafana)
        took = time.monotonic() - started
        assert me["login"] == "carol-login-e2e" and me["role"] == "Admin", me
        page.wait_for_timeout(1500)
        shot(page, "carol-grafana")
        print(f"    carol: meteen in Grafana ({took:.1f}s, geen tweede login), rol {me['role']}")
        page.context.close()

        # alice: nog geen sessie, dus eerst de Authentik-aanmelding, dan terug in Grafana.
        page = browser.new_context(locale="en-US").new_page()
        page.goto(f"{grafana}/")
        page.wait_for_url(f"{authentik}/**", timeout=60000)
        shot(page, "alice-authentik")
        authentik_login(page, "alice-login-e2e")
        page.wait_for_url(f"{grafana}/**", timeout=60000)
        page.wait_for_function("() => !location.pathname.startsWith('/login')", timeout=60000)
        me = grafana_user(page, grafana)
        assert me["login"] == "alice-login-e2e" and me["role"] == "Viewer", me
        page.wait_for_timeout(1500)
        shot(page, "alice-grafana")
        print(f"    alice: via Authentik in Grafana, rol {me['role']}")
        page.context.close()

        # eve: geen lid van de organisatie, Authentik weigert.
        page = browser.new_context(locale="en-US").new_page()
        page.goto(f"{grafana}/")
        page.wait_for_url(f"{authentik}/**", timeout=60000)
        authentik_login(page, "eve-login-e2e")
        page.wait_for_function(
            "() => document.body.innerText.match(/Permission denied|denied/i) "
            "|| [...document.querySelectorAll('*')].some(e => e.shadowRoot && "
            "/Permission denied|denied/i.test(e.shadowRoot.textContent))",
            timeout=60000,
        )
        assert not page.url.startswith(grafana), page.url
        shot(page, "eve-geweigerd")
        print(f"    eve: geweigerd door Authentik ({page.url.split('?')[0]})")
        page.context.close()
        browser.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--authentik", default="http://localhost:9000", help="Authentik, voor VaultX én de browser"
    )
    ap.add_argument("--authentik-token", required=True, help="beheertoken (enkel voor de voorbereiding)")
    ap.add_argument("--grafana", default="http://localhost:3000", help="Grafana (ook zijn root_url)")
    ap.add_argument("--grafana-password", required=True, help="wachtwoord van de Grafana-gebruiker admin")
    ap.add_argument("--browser", action="store_true", help="ook aanmelden in Chromium (playwright)")
    ap.add_argument("--screenshots", type=Path)
    args = ap.parse_args()
    authentik, grafana = args.authentik.rstrip("/"), args.grafana.rstrip("/")
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)

    slug = f"login-e2e-{int(time.time())}"
    ak = Authentik(authentik, args.authentik_token)
    step("Authentik voorbereiden: serviceaccount met beperkte rechten, groepen en gebruikers")
    ak.wait_ready("default-provider-authorization-implicit-consent")
    for prov in ak.get("/providers/oauth2/", search="VaultX login: "):
        if prov["assigned_application_slug"]:
            ak.c.delete(f"/core/applications/{prov['assigned_application_slug']}/")
        ak.c.delete(f"/providers/oauth2/{prov['pk']}/")
    sa_token = ak.service_account_token("vaultx-login-e2e")
    member = ak.ensure_group(f"vaultx:{slug}")
    admins = ak.ensure_group(f"vaultx:{slug}:admin")
    for user, groups in (("alice", [member["pk"]]), ("carol", [admins["pk"]]), ("eve", [])):
        ak.ensure_user(f"{user}-login-e2e", groups)
        # Grafana weigert een aanmelding zonder e-mailadres (zie docs/autologin.md).
        pk = ak.one("/core/users/", username=f"{user}-login-e2e")["pk"]
        ak.c.patch(f"/core/users/{pk}/", json={"email": f"{user}@login-e2e.test"}).raise_for_status()

    step("Grafana voorbereiden: geen OAuth-login, eigen aanmeldformulier")
    g = grafana_client(grafana, args.grafana_password)
    for _ in range(60):
        try:
            if g.get("/health").status_code == 200:
                break
        except httpx.TransportError:
            pass
        time.sleep(2)
    r = g.delete("/v1/sso-settings/generic_oauth")
    assert r.status_code in (204, 404), r.text  # 404: stond niet in de database
    assert grafana_login_status(grafana)[0] == 200

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
        "VAULTX_AUTHENTIK_API_URL": authentik,
        "VAULTX_AUTHENTIK_API_TOKEN": sa_token,
        "VAULTX_AUTHENTIK_PUBLIC_URL": authentik,
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

        step("VaultX: organisatie en app 'Grafana' in de catalogus")
        admin = httpx.Client(base_url=base, trust_env=False, timeout=120, headers=CSRF)
        login(admin, fake, "admin-login-e2e", "admin-login-e2e@example.com", ["vaultx-admins"])
        org = admin.post("/api/v1/organizations", json={"slug": slug, "name": "Login e2e"}).raise_for_status()
        org_id = org.json()["id"]
        app = admin.post(
            f"/api/v1/organizations/{org_id}/applications",
            json={"name": "Grafana", "app_type": "grafana", "url": grafana},
        ).raise_for_status()
        lbase = f"/api/v1/organizations/{org_id}/applications/{app.json()['id']}/login"
        state = admin.get(lbase).raise_for_status().json()
        assert state["configured"] and state["authentik_url"] == authentik, state
        assert [t["key"] for t in state["templates"]] == ["grafana", "oidc"], state

        step("Voorbeeld met Grafana-beheerder: nog niets gewijzigd")
        body = {
            "template": "grafana",
            "access": "organization",
            "grafana": {"url": grafana, "username": "admin", "password": args.grafana_password},
        }
        plan = admin.post(f"{lbase}/preview", json=body).raise_for_status().json()
        blocks = [c["message"] for c in plan["checks"] if c["level"] == "block"]
        assert not blocks and plan["can_apply"] and plan["configure_app"], plan
        for s in plan["steps"]:
            print(f"      - {s}")
        assert plan["groups"] == [f"vaultx:{slug}", f"vaultx:{slug}:admin"], plan["groups"]
        assert not ak.get("/providers/oauth2/", search="VaultX login: ")
        assert grafana_login_status(grafana)[0] == 200

        step("Inrichten: Authentik-provider, applicatie, groepsbinding en de login in Grafana")
        started = time.monotonic()
        r = admin.post(lbase, json=body)
        assert r.status_code == 201, r.text
        login_row = r.json()
        print(f"    klaar in {time.monotonic() - started:.1f}s; controle: {login_row['last_check_message']}")
        assert login_row["app_configured"] and login_row["last_check_status"] == "ok", login_row
        provider = ak.one("/providers/oauth2/", search="VaultX login: ")
        assert provider and provider["client_id"] == login_row["client_id"], provider
        assert [u["url"] for u in provider["redirect_uris"]] == [f"{grafana}/login/generic_oauth"], provider
        assert provider["signing_key"], "ID-tokens horen RS256 getekend te zijn"
        akapp = ak.one("/core/applications/", slug=login_row["application_slug"], superuser_full_list="true")
        assert akapp is not None
        bindings = ak.get("/policies/bindings/", target=akapp["pk"])
        assert sorted(b["group_obj"]["name"] for b in bindings) == plan["groups"], bindings
        settings = g.get("/v1/sso-settings/generic_oauth").raise_for_status().json()["settings"]
        assert settings["enabled"] and settings["autoLogin"] and settings["usePkce"], settings
        status, location = grafana_login_status(grafana)
        assert status in (302, 307) and "/login/generic_oauth" in location, (status, location)

        if args.browser:
            step("Browser: carol (al aangemeld bij Authentik), alice (lid) en eve (geen lid)")
            browser_check(authentik, grafana, args.screenshots)

        step("Config met client secret opvragen (staat in de auditlog)")
        cfg = admin.get(f"{lbase}/config").raise_for_status().json()
        assert cfg["client_secret"] and cfg["client_id"] == provider["client_id"]
        assert any(f["name"] == "grafana.ini" for f in cfg["files"])
        audit = admin.get("/api/v1/audit", params={"action": "app_login.", "limit": 20}).json()["items"]
        assert {"app_login.configure", "app_login.config_viewed"} <= {a["action"] for a in audit}, audit

        step("Weghalen: Grafana terug naar zijn eigen aanmelding, Authentik opgeruimd")
        r = admin.post(f"{lbase}/remove", json={"grafana": body["grafana"]})
        assert r.status_code == 200 and r.json() is None, r.text
        assert grafana_login_status(grafana)[0] == 200
        assert not ak.get("/providers/oauth2/", search="VaultX login: ")
        assert (
            ak.one("/core/applications/", slug=login_row["application_slug"], superuser_full_list="true")
            is None
        )

        step("Generiek OIDC-sjabloon met eigen redirect URI")
        oidc = {"template": "oidc", "access": "all", "redirect_uris": [f"{grafana}/oidc/callback"]}
        r = admin.post(lbase, json=oidc)
        assert r.status_code == 201, r.text
        cfg = admin.get(f"{lbase}/config").raise_for_status().json()
        well_known = httpx.get(cfg["discovery_url"], trust_env=False, timeout=10).raise_for_status().json()
        assert well_known["issuer"] == cfg["issuer"], (well_known["issuer"], cfg["issuer"])
        print(f"    issuer {cfg['issuer']} antwoordt")
        r = admin.post(f"{lbase}/remove", json={})
        assert r.status_code == 200 and r.json() is None, r.text
        assert not ak.get("/providers/oauth2/", search="VaultX login: ")
        step("OK: automatische login voor Grafana via Authentik, ingericht en opgeruimd door VaultX")
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=10)
        except subprocess.TimeoutExpired:
            backend.kill()


if __name__ == "__main__":
    main()
