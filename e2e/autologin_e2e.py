"""End-to-end test fase 5: automatische login via Authentik, met echte Authentik.

VaultX koppelt enkel met Authentik (en NPM); de app zelf vult de beheerder in.
Deze test speelt die app zelf: een kleine OIDC-client ("testapp") die, net als
een app met automatisch doorsturen, een bezoeker zonder sessie meteen naar
Authentik stuurt en de code met client ID en secret inwisselt.

Wat het doet:

1. In Authentik (met het beheertoken): een serviceaccount met enkel de rechten
   uit docs/autologin.md (die van fase 4 plus vijf voor OIDC-providers), een
   API-token daarvoor, de groepen ``vaultx:<org>`` en ``vaultx:<org>:admin``, en
   gebruikers alice (lid), carol (beheerder van de organisatie) en eve (geen lid).
2. De VaultX-backend start met het token van het serviceaccount. Via de API:
   - een app "Testapp" in de catalogus;
   - voorbeeld en inrichten: VaultX maakt in Authentik een OAuth2/OpenID-provider,
     applicatie en groepsbinding;
   - de instellingen met client secret opvragen en in de testapp zetten (wat de
     beheerder met de hand in de app doet);
   - met --browser: carol meldt zich eerst bij Authentik aan en opent dan de
     testapp: ze zit er meteen in, zonder tweede login (het hoofdscenario).
     alice komt via de Authentik-aanmelding binnen, eve wordt door Authentik
     geweigerd. De testapp controleert het ID-token (handtekening, issuer,
     audience) en krijgt een refresh token;
   - weghalen: de provider en applicatie in Authentik zijn weg.

    cd backend && python ../e2e/autologin_e2e.py --authentik http://localhost:9000 \\
        --authentik-token <beheertoken> --browser

Zie e2e/README.md.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import html
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "e2e"))

import authentik_npm_e2e  # noqa: E402
from authentik_npm_e2e import PASSWORD, Authentik  # noqa: E402
from bitwarden_e2e import free_port, login, step  # noqa: E402

from tests.fake_oidc import FakeOIDCProvider  # noqa: E402

# De test-app draait op localhost, dus heet de provider die VaultX aanmaakt altijd zo. Opruimen en
# controles blijven daartoe beperkt: andere automatische logins in dezelfde Authentik blijven staan.
TEST_PROVIDER = "VaultX login: localhost"
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


class TestApp:
    """Een app die via OpenID Connect bij Authentik aanmeldt, met automatisch doorsturen.

    Krijgt de instellingen uit "Instellingen tonen" van VaultX (``configure``), net zoals een
    beheerder ze in een echte app invult. Elke geslaagde aanmelding komt in ``logins``.
    """

    def __init__(self) -> None:
        self.port = free_port()
        self.url = f"http://localhost:{self.port}"
        self.redirect_uri = f"{self.url}/callback"
        self.cfg: dict[str, Any] = {}
        self.pending: dict[str, str] = {}  # state -> PKCE verifier
        self.sessions: dict[str, dict[str, Any]] = {}
        self.logins: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), self._handler())

    def configure(self, cfg: dict[str, Any]) -> None:
        with httpx.Client(trust_env=False, timeout=15) as c:
            disco = c.get(cfg["discovery_url"]).raise_for_status().json()
            jwks = c.get(disco["jwks_uri"]).raise_for_status().json()
        self.cfg = {**cfg, "discovery": disco, "keys": KeySet.import_key_set(jwks)}

    def start(self) -> None:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()

    def _authorize_url(self) -> str:
        state = secrets.token_urlsafe(16)
        verifier = secrets.token_urlsafe(48)
        self.pending[state] = verifier
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        query = {
            "response_type": "code",
            "client_id": self.cfg["client_id"],
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.cfg["scopes"]),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.cfg['authorization_url']}?{urlencode(query)}"

    def _exchange(self, code: str, state: str) -> dict[str, Any]:
        verifier = self.pending.pop(state)
        with httpx.Client(trust_env=False, timeout=15) as c:
            r = c.post(
                self.cfg["token_url"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "code_verifier": verifier,
                },
                auth=(self.cfg["client_id"], self.cfg["client_secret"]),
            )
        if r.status_code != 200:
            raise RuntimeError(f"token: {r.status_code} {r.text[:300]}")
        tokens = r.json()
        token = jwt.decode(tokens["id_token"], self.cfg["keys"])
        claims = token.claims
        jwt.JWTClaimsRegistry(
            iss={"essential": True, "value": self.cfg["issuer"]},
            aud={"essential": True, "value": self.cfg["client_id"]},
        ).validate(claims)
        return {
            "username": claims.get("preferred_username"),
            "email": claims.get("email"),
            "groups": claims.get("groups") or [],
            "alg": token.header.get("alg"),
            "refresh_token": bool(tokens.get("refresh_token")),
        }

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # stil
                pass

            def _send(self, status: int, body: str = "", headers: dict[str, str] | None = None) -> None:
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode())

            def do_GET(self) -> None:  # noqa: N802
                parts = urlsplit(self.path)
                cookie = dict(
                    c.strip().split("=", 1) for c in (self.headers.get("Cookie") or "").split(";") if "=" in c
                )
                if parts.path == "/callback":
                    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
                    if "error" in q or q.get("state") not in outer.pending:
                        outer.errors.append(json.dumps(q))
                        return self._send(400, f"<h1>Aanmelding mislukt</h1><pre>{html.escape(str(q))}</pre>")
                    try:
                        user = outer._exchange(q["code"], q["state"])
                    except Exception as exc:  # noqa: BLE001 - komt in de test terecht
                        outer.errors.append(str(exc))
                        return self._send(500, f"<h1>Fout</h1><pre>{html.escape(str(exc))}</pre>")
                    sid = secrets.token_urlsafe(16)
                    outer.sessions[sid] = user
                    outer.logins.append(user)
                    return self._send(302, headers={"Location": "/", "Set-Cookie": f"testapp={sid}; Path=/"})
                user = outer.sessions.get(cookie.get("testapp", ""))
                if user is None:
                    # Zoals een app met automatisch doorsturen: geen eigen aanmeldformulier.
                    return self._send(302, headers={"Location": outer._authorize_url()})
                groups = "".join(f"<li>{html.escape(g)}</li>" for g in user["groups"])
                self._send(
                    200,
                    "<!doctype html><title>Testapp</title><body style='font-family:sans-serif;margin:3em'>"
                    f"<h1 id=who>Aangemeld als {html.escape(user['username'])}</h1>"
                    f"<p>Via Authentik (OpenID Connect), e-mail {html.escape(user['email'] or '')}, "
                    f"ID-token {html.escape(user['alg'] or '')}.</p><p>Groepen:</p><ul>{groups}</ul></body>",
                )

        return Handler


def authentik_login(page, user: str) -> None:
    page.wait_for_selector("input[name=uidField]", timeout=60000)
    page.fill("input[name=uidField]", user)
    page.keyboard.press("Enter")
    # Zoals in authentik_e2e.py: de wachtwoordstap is pas klaar als "Not you?" er staat.
    page.get_by_text("Not you?").wait_for(timeout=30000)
    page.wait_for_timeout(1000)
    page.fill("input[name=password]", PASSWORD)
    page.get_by_role("button", name="Continue").click()


def watch_stages(page) -> list[str]:
    """Welke stappen Authentik in deze pagina toont; een aanmelding is ak-stage-identification."""
    stages: list[str] = []

    def on_response(response) -> None:
        if "/api/v3/flows/executor/" in response.url:
            with contextlib.suppress(Exception):  # geen JSON
                stages.append(response.json().get("component", "?"))

    page.on("response", on_response)
    return stages


def browser_check(authentik: str, app: TestApp, slug: str, shots: Path | None) -> None:
    from playwright.sync_api import sync_playwright

    def shot(page, name: str) -> None:
        if shots:
            page.screenshot(path=str(shots / f"{name}.png"))

    def logged_in(page, user: str) -> None:
        try:
            page.wait_for_url(f"{app.url}/", timeout=60000)
            page.wait_for_selector("#who", timeout=30000)
        except Exception:
            print("    niet in de testapp:", page.url, app.errors)
            shot(page, "fout")
            raise
        assert page.inner_text("#who") == f"Aangemeld als {user}", page.inner_text("#who")

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # Hoofdscenario: carol is al bij Authentik aangemeld en opent dan de app.
        page = browser.new_context(locale="en-US").new_page()
        page.goto(f"{authentik}/if/user/")
        authentik_login(page, "carol-login-e2e")
        page.wait_for_url(f"{authentik}/if/user/**", timeout=60000)
        print("    carol: aangemeld bij Authentik")
        stages = watch_stages(page)
        started = time.monotonic()
        page.goto(f"{app.url}/")
        logged_in(page, "carol-login-e2e")
        took = time.monotonic() - started
        assert not [s for s in stages if s in ("ak-stage-identification", "ak-stage-password")], stages
        me = app.logins[-1]
        assert me["email"] == "carol@login-e2e.test", me
        assert f"vaultx:{slug}:admin" in me["groups"], me
        assert me["alg"] == "RS256" and me["refresh_token"], me
        shot(page, "carol-meteen-in-app")
        print(
            f"    carol: meteen in de app ({took:.1f}s, geen tweede login; Authentik-stappen: "
            f"{', '.join(stages) or 'geen'}), ID-token {me['alg']}"
        )
        page.context.close()

        # alice: nog geen sessie, dus eerst de Authentik-aanmelding, dan terug in de app.
        page = browser.new_context(locale="en-US").new_page()
        alice_stages = watch_stages(page)
        page.goto(f"{app.url}/")
        page.wait_for_url(f"{authentik}/**", timeout=60000)
        shot(page, "alice-authentik")
        authentik_login(page, "alice-login-e2e")
        logged_in(page, "alice-login-e2e")
        # Controle op de controle: zonder sessie ziet de browser wel de aanmeldstap.
        assert "ak-stage-identification" in alice_stages, alice_stages
        assert f"vaultx:{slug}" in app.logins[-1]["groups"], app.logins[-1]
        print("    alice: via de Authentik-aanmelding in de app")
        page.context.close()

        # eve: geen lid van de organisatie, Authentik weigert.
        page = browser.new_context(locale="en-US").new_page()
        page.goto(f"{app.url}/")
        page.wait_for_url(f"{authentik}/**", timeout=60000)
        authentik_login(page, "eve-login-e2e")
        page.wait_for_function(
            "() => document.body.innerText.match(/Permission denied|denied/i) "
            "|| [...document.querySelectorAll('*')].some(e => e.shadowRoot && "
            "/Permission denied|denied/i.test(e.shadowRoot.textContent))",
            timeout=60000,
        )
        assert not page.url.startswith(app.url), page.url
        assert all(u["username"] != "eve-login-e2e" for u in app.logins), app.logins
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
    ap.add_argument("--browser", action="store_true", help="ook aanmelden in Chromium (playwright)")
    ap.add_argument("--screenshots", type=Path)
    args = ap.parse_args()
    authentik = args.authentik.rstrip("/")
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)

    slug = f"login-e2e-{int(time.time())}"
    ak = Authentik(authentik, args.authentik_token)
    step("Authentik voorbereiden: serviceaccount met beperkte rechten, groepen en gebruikers")
    ak.wait_ready("default-provider-authorization-implicit-consent")
    for prov in ak.get("/providers/oauth2/", search=TEST_PROVIDER):
        if not prov["name"].startswith(TEST_PROVIDER):
            continue
        if prov["assigned_application_slug"]:
            ak.c.delete(f"/core/applications/{prov['assigned_application_slug']}/")
        ak.c.delete(f"/providers/oauth2/{prov['pk']}/")
    sa_token = ak.service_account_token("vaultx-login-e2e")
    member = ak.ensure_group(f"vaultx:{slug}")
    admins = ak.ensure_group(f"vaultx:{slug}:admin")
    for user, groups in (("alice", [member["pk"]]), ("carol", [admins["pk"]]), ("eve", [])):
        ak.ensure_user(f"{user}-login-e2e", groups)
        pk = ak.one("/core/users/", username=f"{user}-login-e2e")["pk"]
        ak.c.patch(f"/core/users/{pk}/", json={"email": f"{user}@login-e2e.test"}).raise_for_status()

    testapp = TestApp()
    testapp.start()
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

        step("VaultX: organisatie en app 'Testapp' in de catalogus")
        admin = httpx.Client(base_url=base, trust_env=False, timeout=120, headers=CSRF)
        login(admin, fake, "admin-login-e2e", "admin-login-e2e@example.com", ["vaultx-admins"])
        org = admin.post("/api/v1/organizations", json={"slug": slug, "name": "Login e2e"}).raise_for_status()
        org_id = org.json()["id"]
        app = admin.post(
            f"/api/v1/organizations/{org_id}/applications",
            json={"name": "Testapp", "url": testapp.url},
        ).raise_for_status()
        lbase = f"/api/v1/organizations/{org_id}/applications/{app.json()['id']}/login"
        state = admin.get(lbase).raise_for_status().json()
        assert state["configured"] and state["authentik_url"] == authentik, state
        assert state["suggested_app_url"] == testapp.url, state

        step("Voorbeeld: nog niets gewijzigd")
        body = {"access": "organization", "redirect_uris": [testapp.redirect_uri]}
        plan = admin.post(f"{lbase}/preview", json=body).raise_for_status().json()
        blocks = [c["message"] for c in plan["checks"] if c["level"] == "block"]
        assert not blocks and plan["can_apply"], plan
        for s in plan["steps"]:
            print(f"      - {s}")
        assert plan["groups"] == [f"vaultx:{slug}", f"vaultx:{slug}:admin"], plan["groups"]
        assert not ak.get("/providers/oauth2/", search=TEST_PROVIDER)

        step("Inrichten: Authentik-provider, applicatie en groepsbinding")
        started = time.monotonic()
        r = admin.post(lbase, json=body)
        assert r.status_code == 201, r.text
        login_row = r.json()
        print(f"    klaar in {time.monotonic() - started:.1f}s")
        provider = ak.one("/providers/oauth2/", search=TEST_PROVIDER)
        assert provider and provider["client_id"] == login_row["client_id"], provider
        assert [u["url"] for u in provider["redirect_uris"]] == [testapp.redirect_uri], provider
        assert provider["signing_key"], "ID-tokens horen RS256 getekend te zijn"
        akapp = ak.one("/core/applications/", slug=login_row["application_slug"], superuser_full_list="true")
        assert akapp is not None and akapp["meta_launch_url"] == testapp.url, akapp
        bindings = ak.get("/policies/bindings/", target=akapp["pk"])
        assert sorted(b["group_obj"]["name"] for b in bindings) == plan["groups"], bindings

        step("Instellingen met client secret opvragen en in de app zetten (staat in de auditlog)")
        cfg = admin.get(f"{lbase}/config").raise_for_status().json()
        assert cfg["client_secret"] and cfg["client_id"] == provider["client_id"]
        assert cfg["client_secret"] in cfg["text"]
        testapp.configure(cfg)
        assert testapp.cfg["discovery"]["issuer"] == cfg["issuer"], testapp.cfg["discovery"]["issuer"]
        print(f"    issuer {cfg['issuer']} antwoordt")
        audit = admin.get("/api/v1/audit", params={"action": "app_login.", "limit": 20}).json()["items"]
        assert {"app_login.configure", "app_login.config_viewed"} <= {a["action"] for a in audit}, audit

        if args.browser:
            step("Browser: carol (al aangemeld bij Authentik), alice (lid) en eve (geen lid)")
            browser_check(authentik, testapp, slug, args.screenshots)

        step("Weghalen: provider en applicatie in Authentik opgeruimd")
        r = admin.post(f"{lbase}/remove", json={})
        assert r.status_code == 200 and r.json() is None, r.text
        assert not ak.get("/providers/oauth2/", search=TEST_PROVIDER)
        assert (
            ak.one("/core/applications/", slug=login_row["application_slug"], superuser_full_list="true")
            is None
        )
        gone = httpx.get(cfg["discovery_url"], trust_env=False, timeout=10)
        assert gone.status_code == 404, gone.status_code
        step("OK: automatische login via Authentik, ingericht en opgeruimd door VaultX")
    finally:
        backend.terminate()
        testapp.stop()
        try:
            backend.wait(timeout=10)
        except subprocess.TimeoutExpired:
            backend.kill()


if __name__ == "__main__":
    main()
