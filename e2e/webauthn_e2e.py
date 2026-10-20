"""Beveiligingssleutels en passkeys (WebAuthn) in een echte browser (Chromium via Playwright).

Chromium krijgt een virtuele FIDO2-sleutel (DevTools-protocol, zoals een YubiKey die meteen
"aangeraakt" wordt). Getest:
1. een sleutel koppelen aan de kluis op Mijn kluis (met herstelcode);
2. een Bitwarden-client logt in met die sleutel via de connectorpagina die de Bitwarden-
   extensie opent (webauthn-fallback-connector.html), met precies de parameters van de extensie;
3. de webkluis ontgrendelen met master password + sleutel;
4. een sleutel voor het VaultX-wachtwoordlogin koppelen en daarmee inloggen.

WebAuthn wil een veilige context, dus de browser opent VaultX als http://localhost.

    cd frontend && npm ci && npm run build && cd ..
    VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_e2e \\
      python e2e/webauthn_e2e.py [map-voor-screenshots]
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from playwright.sync_api import BrowserContext, Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "e2e"))

from notes_e2e import BACKEND, DB, FRONTEND, free_port, step, wait_http  # noqa: E402

from tests.bw_crypto import master_key, master_password_hash  # noqa: E402
from tests.fake_oidc import FakeOIDCProvider  # noqa: E402

EMAIL = "jonas@example.com"
MASTER = "mijn-master-password-met-yubikey"
CAROL_PW = "carol-haar-wachtwoord"
CSRF = {"X-VaultX-CSRF": "1"}


def virtual_key(ctx: BrowserContext, page: Page) -> None:
    """Virtuele USB-sleutel zonder pincode die elke vraag meteen bevestigt."""
    cdp = ctx.new_cdp_session(page)
    cdp.send("WebAuthn.enable")
    cdp.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "usb",
                "hasResidentKey": False,
                "hasUserVerification": False,
                "automaticPresenceSimulation": True,
            }
        },
    )


def b64_like_extension(text: str) -> str:
    # btoa(encodeURIComponent(str).replace(/%([0-9A-F]{2})/g, ...)) uit webauthn-iframe.ts
    return base64.b64encode(text.encode()).decode()


def main() -> None:
    shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    def shot(page: Page, name: str, locator=None) -> None:
        if shots:
            (locator or page).screenshot(path=str(shots / f"{name}.png"))

    if not (FRONTEND / "dist" / "index.html").exists():
        raise SystemExit("Bouw eerst de webinterface: cd frontend && npm run build")

    fake = FakeOIDCProvider()
    fake.start()
    api_port, ui_port = free_port(), free_port()
    ui = f"http://localhost:{ui_port}"
    api = f"http://127.0.0.1:{api_port}"
    env = {
        **os.environ,
        "VAULTX_DATABASE_URL": DB,
        "VAULTX_SECRET_KEY": "e2e-secret-key-e2e-secret-key-0123456789",
        "VAULTX_PUBLIC_URL": ui,
        "VAULTX_OIDC_ISSUER": fake.issuer,
        "VAULTX_OIDC_CLIENT_ID": fake.client_id,
        "VAULTX_OIDC_CLIENT_SECRET": fake.client_secret,
        "VAULTX_OIDC_ADMIN_GROUPS": "vaultx-admins",
        "VAULTX_COOKIE_SECURE": "false",
        "VAULTX_NPM_SYNC_INTERVAL_MINUTES": "0",
        "VAULTX_LOG_LEVEL": "WARNING",
    }
    subprocess.run([sys.executable, "-m", "alembic", "downgrade", "base"], cwd=BACKEND, env=env, check=True)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True)
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(api_port), "--log-level", "warning"],
        cwd=BACKEND,
        env=env,
    )
    vite = subprocess.Popen(
        [
            str(FRONTEND / "node_modules" / ".bin" / "vite"),
            "preview",
            "--host",
            "127.0.0.1",
            "--port",
            str(ui_port),
            "--strictPort",
        ],
        cwd=FRONTEND,
        # Ook de backend als localhost: de proxy van vite zet die host door, en VaultX start de
        # Authentik-login enkel op de host van VAULTX_PUBLIC_URL.
        env={**os.environ, "VAULTX_BACKEND_URL": f"http://localhost:{api_port}"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_http(f"{api}/health", "backend")
        wait_http(f"http://127.0.0.1:{ui_port}", "webinterface")
        with sync_playwright() as pw:
            # localhost altijd naar 127.0.0.1 (vite luistert enkel op IPv4).
            browser = pw.chromium.launch(args=["--host-resolver-rules=MAP localhost 127.0.0.1"])
            errors: list[str] = []

            def new_page() -> tuple[BrowserContext, Page]:
                ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="nl-BE")
                page = ctx.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on(
                    "console",
                    lambda m: errors.append(m.text) if "Content Security Policy" in m.text else None,
                )
                page.on("dialog", lambda d: d.accept())
                virtual_key(ctx, page)
                return ctx, page

            step("Inloggen via (nep-)Authentik en kluis activeren")
            _, page = new_page()
            fake.next_claims = {
                "sub": "jonas",
                "email": EMAIL,
                "email_verified": True,
                "preferred_username": "jonas",
                "name": "Jonas",
                "groups": ["vaultx-admins"],
            }
            page.goto(f"{ui}/auth/login?next=/vault")
            expect(page.get_by_role("heading", name="Mijn kluis")).to_be_visible()
            page.get_by_label("Master password", exact=True).fill(MASTER)
            page.get_by_label("Herhaal master password").fill(MASTER)
            page.get_by_label("Ik begrijp").check()
            page.get_by_role("button", name="Kluis activeren").click()
            page.get_by_role("link", name="Apps, apparaten en beveiliging").click(timeout=30_000)

            step("YubiKey koppelen aan de kluis")
            card = page.locator(".card", has=page.get_by_role("heading", name="Tweestapsverificatie"))
            card.get_by_role("button", name="Sleutel of passkey toevoegen").click()
            card.get_by_label("Naam").fill("YubiKey 5C NFC")
            card.locator("form").filter(has=page.get_by_role("button", name="Koppelen")).get_by_label(
                "Master password"
            ).fill(MASTER)
            card.get_by_role("button", name="Koppelen").click()
            expect(card.locator(".recovery-code")).to_be_visible(timeout=60_000)
            expect(card.get_by_role("cell", name="YubiKey 5C NFC")).to_be_visible()
            expect(card.get_by_text("aan", exact=True)).to_be_visible()
            shot(page, "01-sleutel-gekoppeld", card)

            step("Bitwarden-client: provider 7 en de connectorpagina van de extensie")
            mkey = master_key(MASTER, EMAIL)
            form = {
                "grant_type": "password",
                "username": EMAIL,
                "password": master_password_hash(mkey, MASTER),
                "client_id": "browser",
                "deviceType": "5",
                "deviceIdentifier": "e2e-edge",
                "deviceName": "edge",
            }
            r = httpx.post(f"{api}/identity/connect/token", data=form)
            assert r.status_code == 400, r.text
            providers = r.json()["TwoFactorProviders2"]
            assert list(providers) == ["7"], providers
            # Zoals WebAuthnIFrame.init() in de extensie de URL opbouwt (v1, nieuw tabblad).
            params = urlencode(
                {
                    "data": b64_like_extension(json.dumps(providers["7"])),
                    "parent": quote("chrome-extension://abc/popup/index.html#/2fa", safe=""),
                    "btnText": quote("Beveiligingssleutel lezen"),
                    "btnAwaitingInteractionText": quote("Wachten op interactie"),
                    "v": "1",
                    "locale": "nl",
                }
            )
            page.goto(f"{ui}/webauthn-fallback-connector.html?{params}")
            # De content script van de extensie luistert naar window.postMessage op deze pagina.
            page.evaluate(
                """() => { window.__result = new Promise((resolve) => window.addEventListener('message',
                  (e) => e.data && e.data.command === 'webAuthnResult' && resolve(e.data))); }"""
            )
            page.get_by_label("Dit apparaat 30 dagen onthouden").check()
            page.get_by_role("button", name="Beveiligingssleutel gebruiken").click()
            result = page.evaluate("() => window.__result")
            expect(page.get_by_text("Gelukt")).to_be_visible()
            shot(page, "02-connector-extensie")
            assert result["remember"] is True
            r = httpx.post(
                f"{api}/identity/connect/token",
                data={
                    **form,
                    "twoFactorToken": result["data"],
                    "twoFactorProvider": "7",
                    "twoFactorRemember": "1",
                },
            )
            assert r.status_code == 200, r.text
            assert r.json()["TwoFactorToken"]

            step("Webkluis ontgrendelen met master password en sleutel")
            page.goto(f"{ui}/vault")
            page.get_by_label(re.compile("Master password voor")).fill(MASTER)
            page.get_by_role("button", name="Ontgrendelen").click()
            button = page.get_by_role("button", name="Beveiligingssleutel gebruiken")
            expect(button).to_be_visible(timeout=60_000)
            shot(page, "03-webkluis-sleutel")
            button.click()
            expect(page.get_by_role("button", name="Vergrendelen")).to_be_visible(timeout=60_000)

            step("Gewoon account met wachtwoord, sleutel voor het wachtwoordlogin koppelen")
            r = page.request.post(
                f"{ui}/api/v1/users",
                data={"username": "carol", "email": "carol@example.com", "password": CAROL_PW},
                headers=CSRF,
            )
            assert r.ok, r.text()
            _, carol = new_page()
            carol.goto(f"{ui}/login")
            carol.get_by_label("Gebruikersnaam of e-mail").fill("carol")
            carol.get_by_label("Wachtwoord").fill(CAROL_PW)
            carol.get_by_role("button", name="Inloggen", exact=True).click()
            expect(carol.get_by_role("button", name="Uitloggen")).to_be_visible(timeout=30_000)
            carol_id = carol.request.get(f"{ui}/api/v1/me").json()["id"]
            carol.goto(f"{ui}/users/{carol_id}")
            keys = carol.locator(
                ".card", has=carol.get_by_role("heading", name="Beveiligingssleutels voor het wachtwoordlogin")
            )
            keys.get_by_role("button", name="Sleutel of passkey toevoegen").click()
            keys.get_by_label("Je VaultX-wachtwoord").fill(CAROL_PW)
            keys.get_by_role("button", name="Koppelen").click()
            expect(keys.locator(".recovery-code")).to_be_visible(timeout=60_000)
            expect(keys.get_by_role("cell", name="YubiKey")).to_be_visible()
            shot(carol, "04-sleutel-wachtwoordlogin", keys)

            step("Opnieuw inloggen: wachtwoord en dan de sleutel")
            carol.get_by_role("button", name="Uitloggen").click()
            expect(carol.get_by_label("Gebruikersnaam of e-mail")).to_be_visible(timeout=30_000)
            carol.get_by_label("Gebruikersnaam of e-mail").fill("carol")
            carol.get_by_label("Wachtwoord").fill(CAROL_PW)
            carol.get_by_role("button", name="Inloggen", exact=True).click()
            # De virtuele sleutel bevestigt meteen, dus de loginpagina gaat zelf verder.
            expect(carol.get_by_role("button", name="Uitloggen")).to_be_visible(timeout=30_000)
            sessions = carol.request.get(f"{ui}/api/v1/me/sessions").json()
            assert any(s["auth_method"] == "password" for s in sessions), sessions

            assert not errors, errors
            browser.close()
        step("OK: beveiligingssleutels werken voor de kluis, de Bitwarden-connector en het wachtwoordlogin")
    finally:
        backend.terminate()
        vite.terminate()
        backend.wait(timeout=10)
        vite.wait(timeout=10)


if __name__ == "__main__":
    main()
