"""VaultX Invuller (extension/) in een echte Chromium, tegen de echte VaultX-backend.

Een Python-"Bitwarden-client" zet testitems klaar in de kluis. De extensie wordt geladen,
aangemeld via het popupvenster (e-mail + master password, zoals de Bitwarden-API dat vraagt) en
bezoekt dan testpagina's op verschillende hosts (*.localhost wijst in Chromium altijd naar deze
machine). Gecontroleerd:

- gewone login: gebruikersnaam en wachtwoord ingevuld, Enter, binnen;
- login in twee stappen (eerst gebruikersnaam, dan wachtwoord);
- TOTP-stap na het wachtwoord: de code uit het item;
- een app die enkel op input-events en Enter reageert (zoals React, zonder <form>);
- meerdere passende items: keuzemenu, pas na een klik ingevuld;
- vx:enter = nee: ingevuld, niet verstuurd;
- een nagemaakt domein (p1ain.localhost), dezelfde pagina in een iframe van een vreemd domein
  en http bij een https-item: niets ingevuld;
- fout wachtwoord: precies één poging en de melding "wachtwoord klopt niet meer?".

    VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_e2e \\
      python e2e/invuller_e2e.py [map-voor-screenshots]
"""

from __future__ import annotations

import html
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "e2e"))
sys.path.insert(0, str(ROOT / "backend"))

from bitwarden_e2e import login  # noqa: E402
from notes_e2e import BACKEND, DB, free_port, step, wait_http  # noqa: E402

from app.services import totp  # noqa: E402
from tests.bw_crypto import encrypt, new_enrollment  # noqa: E402
from tests.fake_oidc import FakeOIDCProvider  # noqa: E402

EXTENSION = ROOT / "extension"
EMAIL = "jonas@example.com"
MASTER = "invuller-master-password"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"
GOOD = {
    "plain": "plain-pw",
    "twostep": "twostep-pw",
    "mfa": "mfa-pw",
    "spa": "spa-pw",
    "multi": "multi-pw-b",
    "noenter": "noenter-pw",
    "wrong": "het-nieuwe-wachtwoord",
    "secure": "secure-pw",
    "p1ain": "plain-pw",
}


# ---------------------------------------------------------------------------- testsites

PAGE = """<!doctype html><html><head><meta charset=utf-8><title>{title}</title>
<style>body{{font:15px system-ui;margin:3em}}input{{display:block;margin:.4em 0 1em;padding:.4em}}</style>
</head><body><h1>{title}</h1>{body}</body></html>"""


class Sites:
    """Eén HTTP-server; de host (plain.localhost, twostep.localhost …) bepaalt de site."""

    def __init__(self) -> None:
        self.port = free_port()
        self.posts: list[tuple[str, str, dict[str, str]]] = []  # (site, pad, formulier)
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), self._handler())

    def url(self, site: str, path: str = "/", scheme: str = "http") -> str:
        return f"{scheme}://{site}.localhost:{self.port}{path}"

    def posted(self, site: str) -> list[dict[str, str]]:
        return [f for s, _, f in self.posts if s == site]

    def start(self) -> None:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handler(self):
        sites = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:
                pass

            @property
            def site(self) -> str:
                return (self.headers.get("Host") or "").split(".localhost")[0]

            def page(self, title: str, body: str, status: int = 200) -> None:
                data = PAGE.format(title=html.escape(title), body=body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def login_form(self, error: str = "") -> None:
                err = f"<p id=error style='color:#b42318'>{error}</p>" if error else ""
                self.page(
                    f"Aanmelden bij {self.site}",
                    f"""{err}<form method=post action=/login>
                    <label>Gebruikersnaam<input name=username autocomplete=username></label>
                    <label>Wachtwoord<input name=password type=password autocomplete=current-password></label>
                    <button type=submit>Aanmelden</button></form>""",
                )

            def welcome(self) -> None:
                self.page("Welkom", f"<p id=ok>Ingelogd op {html.escape(self.site)}</p>")

            def do_GET(self) -> None:  # noqa: N802
                site, path = self.site, self.path.split("?")[0]
                if site == "evil" and path == "/":
                    return self.page(
                        "Gewone website",
                        f"<p>Een andere site met een iframe.</p><iframe id=frame src='{sites.url('plain')}' "
                        "width=600 height=400></iframe>",
                    )
                if site == "twostep" and path == "/":
                    return self.page(
                        "Twee stappen",
                        """<form method=post action=/step1>
                        <label>E-mail of gebruikersnaam<input name=uid autocomplete=username></label>
                        <button type=submit>Volgende</button></form>""",
                    )
                if site == "spa" and path == "/":
                    return self.page("SPA", SPA)
                if path == "/":
                    return self.login_form()
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode()
                if self.headers.get("Content-Type", "").startswith("application/json"):
                    import json

                    form = json.loads(body)
                else:
                    form = {k: v[0] for k, v in parse_qs(body).items()}
                site, path = self.site, self.path
                sites.posts.append((site, path, form))
                if site == "twostep" and path == "/step1":
                    if form.get("uid") != "tweestaps":
                        return self.page("Fout", "<p>onbekende gebruiker</p>", 400)
                    return self.page(
                        "Twee stappen: wachtwoord",
                        """<form method=post action=/step2><p>Aanmelden als tweestaps</p>
                        <label>Wachtwoord<input name=password type=password></label>
                        <button type=submit>Aanmelden</button></form>""",
                    )
                if site == "twostep" and path == "/step2":
                    return (
                        self.welcome()
                        if form.get("password") == GOOD["twostep"]
                        else self.page("Fout", "", 401)
                    )
                if site == "mfa" and path == "/login":
                    if form.get("password") != GOOD["mfa"]:
                        return self.login_form("Fout wachtwoord")
                    return self.page(
                        "Tweestapsverificatie",
                        """<form method=post action=/otp>
                        <label>Code uit je app<input name=otp inputmode=numeric autocomplete=one-time-code
                        maxlength=6></label><button type=submit>Bevestigen</button></form>""",
                    )
                if site == "mfa" and path == "/otp":
                    ok = totp.verify(TOTP_SECRET, form.get("otp", ""), None) is not None
                    return self.welcome() if ok else self.page("Fout", "<p>foute code</p>", 401)
                if site == "spa" and path == "/api/login":
                    ok = form.get("user") == "spa-user" and form.get("pass") == GOOD["spa"]
                    data = b'{"ok":true}' if ok else b'{"ok":false}'
                    self.send_response(200 if ok else 401)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return None
                if path == "/login":
                    if form.get("password") == GOOD.get(site):
                        return self.welcome()
                    return self.login_form("Gebruikersnaam of wachtwoord klopt niet")
                self.send_error(404)

        return H


# Zoals een React-app: de toestand komt enkel uit input-events, versturen met Enter via fetch.
SPA = """<div id=root></div><script>
const state = {user: "", pass: ""};
const root = document.getElementById("root");
function field(label, key, type) {
  const l = document.createElement("label"); l.textContent = label;
  const i = document.createElement("input"); i.type = type;
  i.autocomplete = key === "user" ? "username" : "current-password";
  i.addEventListener("input", (e) => { state[key] = e.target.value; });
  i.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const r = await fetch("/api/login", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(state)});
    root.innerHTML = r.ok ? "<p id=ok>Ingelogd in de app</p>" : "<p id=error>Fout</p>";
  });
  l.append(i); root.append(l);
}
setTimeout(() => { field("Gebruiker", "user", "text"); field("Wachtwoord", "pass", "password"); }, 300);
</script>"""


# ---------------------------------------------------------------------------- kluis vullen


def seed_vault(api: str, fake: FakeOIDCProvider, sites: Sites) -> None:
    web = httpx.Client(base_url=api, trust_env=False, timeout=60, headers={"X-VaultX-CSRF": "1"})
    login(web, fake, "jonas", EMAIL, ["vaultx-admins"])
    account, payload = new_enrollment(EMAIL, MASTER)
    web.post("/api/v1/me/vault", json=payload).raise_for_status()
    r = httpx.post(
        f"{api}/identity/connect/token",
        data={
            "grant_type": "password",
            "username": EMAIL,
            "password": account.password_hash,
            "client_id": "cli",
            "deviceType": "25",
            "deviceIdentifier": "e2e-seed",
            "deviceName": "e2e",
        },
    )
    r.raise_for_status()
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    def item(
        name: str,
        uri: str,
        username: str,
        password: str,
        *,
        totp_uri: str | None = None,
        fields: dict[str, str] | None = None,
        reprompt: int = 0,
    ) -> None:
        item_key = os.urandom(64)
        uk = account.user_key

        def e(text: str) -> str:
            return encrypt(text.encode(), item_key[:32], item_key[32:])

        body = {
            "type": 1,
            "key": encrypt(item_key, uk[:32], uk[32:]),
            "name": e(name),
            "reprompt": reprompt,
            "login": {
                "username": e(username),
                "password": e(password),
                "totp": e(totp_uri) if totp_uri else None,
                "uris": [{"uri": e(uri), "match": None}],
            },
            "fields": [{"name": e(k), "value": e(v), "type": 0} for k, v in (fields or {}).items()],
        }
        httpx.post(f"{api}/api/ciphers", headers=headers, json=body).raise_for_status()

    item("Plain", sites.url("plain"), "plain-user", GOOD["plain"])
    item("Twee stappen", sites.url("twostep"), "tweestaps", GOOD["twostep"])
    item(
        "Met MFA",
        sites.url("mfa"),
        "mfa-user",
        GOOD["mfa"],
        totp_uri=f"otpauth://totp/Mfa:mfa-user?secret={TOTP_SECRET}&issuer=Mfa",
    )
    item("SPA", sites.url("spa"), "spa-user", GOOD["spa"])
    item("Multi A", sites.url("multi"), "multi-a", "multi-pw-a")
    item("Multi B", sites.url("multi"), "multi-b", GOOD["multi"])
    item("Zonder Enter", sites.url("noenter"), "noenter-user", GOOD["noenter"], fields={"vx:enter": "nee"})
    item("Oud wachtwoord", sites.url("wrong"), "wrong-user", "het-oude-wachtwoord")
    item("Enkel https", sites.url("secure", scheme="https"), "secure-user", GOOD["secure"])
    item("Opnieuw vragen", sites.url("reprompt"), "rp-user", "rp-pw", reprompt=1)


# ---------------------------------------------------------------------------- test


def main() -> None:
    shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    def shot(page: Page, name: str) -> None:
        if shots:
            page.screenshot(path=str(shots / f"{name}.png"))

    fake = FakeOIDCProvider()
    fake.start()
    sites = Sites()
    sites.start()
    port = free_port()
    api = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "VAULTX_DATABASE_URL": DB,
        "VAULTX_SECRET_KEY": "e2e-secret-key-e2e-secret-key-0123456789",
        "VAULTX_PUBLIC_URL": api,
        "VAULTX_OIDC_ISSUER": fake.issuer,
        "VAULTX_OIDC_CLIENT_ID": fake.client_id,
        "VAULTX_OIDC_CLIENT_SECRET": fake.client_secret,
        "VAULTX_OIDC_ADMIN_GROUPS": "vaultx-admins",
        "VAULTX_COOKIE_SECURE": "false",
        "VAULTX_NPM_SYNC_INTERVAL_MINUTES": "0",
        "VAULTX_VAULT_ICONS": "false",
        "VAULTX_LOG_LEVEL": "WARNING",
    }
    subprocess.run([sys.executable, "-m", "alembic", "downgrade", "base"], cwd=BACKEND, env=env, check=True)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True)
    backend = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
        cwd=BACKEND,
        env=env,
    )
    try:
        wait_http(f"{api}/health", "backend")
        step("Kluis met testitems (Python-client, itemsleutels zoals de nieuwste Bitwarden-clients)")
        seed_vault(api, fake, sites)

        with sync_playwright() as pw, tempfile.TemporaryDirectory() as profile:
            ctx = pw.chromium.launch_persistent_context(
                profile,
                channel="chromium",
                headless=True,
                locale="nl-BE",
                viewport={"width": 1100, "height": 760},
                args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"],
            )
            worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
            ext_id = worker.url.split("/")[2]
            errors: list[str] = []

            step("Extensie instellen: adres, e-mail en master password (Bitwarden-API, client_id browser)")
            popup = ctx.new_page()
            popup.set_viewport_size({"width": 360, "height": 520})
            popup.on("pageerror", lambda e: errors.append(str(e)))
            popup.goto(f"chrome-extension://{ext_id}/popup.html")
            popup.get_by_label("Adres van VaultX").fill(api)
            popup.get_by_label("E-mail").fill(EMAIL)
            popup.get_by_label("Master password").fill(MASTER)
            shot(popup, "01-instellen")
            popup.get_by_role("button", name="Aanmelden").click()
            expect(popup.get_by_text("Ontgrendeld")).to_be_visible(timeout=60000)
            expect(popup.get_by_text("10 logins")).to_be_visible()
            shot(popup, "02-ontgrendeld")
            popup.close()

            def visit(url: str) -> Page:
                p = ctx.new_page()
                p.on("pageerror", lambda e: errors.append(str(e)))
                p.goto(url)
                return p

            step("Gewone login: invullen en Enter")
            p = visit(sites.url("plain"))
            expect(p.locator("#ok")).to_have_text("Ingelogd op plain", timeout=15000)
            assert sites.posted("plain") == [{"username": "plain-user", "password": GOOD["plain"]}], (
                sites.posts
            )
            shot(p, "03-gewone-login-binnen")
            p.close()

            step("Login in twee stappen: eerst de gebruikersnaam, dan het wachtwoord")
            p = visit(sites.url("twostep"))
            expect(p.locator("#ok")).to_have_text("Ingelogd op twostep", timeout=15000)
            assert [f.get("uid") or f.get("password") for f in sites.posted("twostep")] == [
                "tweestaps",
                GOOD["twostep"],
            ]
            p.close()

            step("Wachtwoord en daarna de TOTP-code uit het item")
            p = visit(sites.url("mfa"))
            expect(p.locator("#ok")).to_have_text("Ingelogd op mfa", timeout=20000)
            codes = [f["otp"] for f in sites.posted("mfa") if "otp" in f]
            assert len(codes) == 1 and len(codes[0]) == 6, sites.posted("mfa")
            p.close()

            step("App zonder formulier die enkel op input-events en Enter let (zoals React)")
            p = visit(sites.url("spa"))
            expect(p.locator("#ok")).to_have_text("Ingelogd in de app", timeout=15000)
            p.close()

            step("Meerdere passende items: keuzemenu, pas na een klik invullen")
            p = visit(sites.url("multi"))
            menu = p.locator("vaultx-invuller")
            expect(menu).to_have_count(1, timeout=15000)
            p.wait_for_timeout(500)
            shot(p, "04-keuzemenu")
            assert not sites.posted("multi")
            assert p.locator("input[name=password]").input_value() == ""
            # Het menu zit in een gesloten shadow root (de pagina kan het niet lezen): met het toetsenbord.
            p.locator("input[name=password]").focus()
            p.keyboard.press("ArrowDown")
            p.keyboard.press("ArrowDown")
            p.keyboard.press("Enter")
            expect(p.locator("#ok")).to_have_text("Ingelogd op multi", timeout=15000)
            assert sites.posted("multi") == [{"username": "multi-b", "password": GOOD["multi"]}]
            p.close()

            step("vx:enter = nee: ingevuld, niet verstuurd")
            p = visit(sites.url("noenter"))
            expect(p.locator("input[name=password]")).to_have_value(GOOD["noenter"], timeout=15000)
            expect(p.locator("input[name=username]")).to_have_value("noenter-user")
            p.wait_for_timeout(2000)
            assert not sites.posted("noenter")
            p.close()

            step("Niet invullen: nagemaakt domein, vreemd iframe, http bij een https-item, opnieuw vragen")
            for site, url in (
                ("p1ain", sites.url("p1ain")),
                ("secure", sites.url("secure")),
                ("reprompt", sites.url("reprompt")),
            ):
                p = visit(url)
                p.wait_for_timeout(2500)
                assert p.locator("input[name=password]").input_value() == "", site
                assert p.locator("input[name=username]").input_value() == "", site
                assert not sites.posted(site), (site, sites.posts)
                if site == "p1ain":
                    shot(p, "05-nagemaakt-domein")
                p.close()
            p = visit(sites.url("evil"))
            frame = p.frame_locator("#frame")
            expect(frame.locator("input[name=password]")).to_be_visible()
            p.wait_for_timeout(2500)
            assert frame.locator("input[name=password]").input_value() == ""
            assert not sites.posted("plain")[1:], sites.posts  # enkel de eerste, gewone login
            shot(p, "06-vreemd-iframe")
            p.close()

            step("Fout wachtwoord: één poging en een melding")
            p = visit(sites.url("wrong"))
            expect(p.locator("#error")).to_be_visible(timeout=15000)
            banner = p.locator("vaultx-invuller")
            expect(banner).to_have_count(1, timeout=15000)
            expect(banner).to_have_attribute(
                "aria-label", "VaultX: inloggen lukte niet. Wachtwoord klopt niet meer?"
            )
            p.wait_for_timeout(3000)
            assert len(sites.posted("wrong")) == 1, sites.posted("wrong")
            shot(p, "07-fout-wachtwoord")
            p.close()

            assert not errors, errors
            ctx.close()
        step("OK: VaultX Invuller vult in en verstuurt waar het mag, en nergens anders")
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=10)
        except subprocess.TimeoutExpired:
            backend.kill()
        time.sleep(0.1)


if __name__ == "__main__":
    main()
