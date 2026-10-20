"""Webkluis: items bekijken en bewerken in de browser (Chromium via Playwright).

Een "Bitwarden-client" (Python, onafhankelijke crypto uit backend/tests) zet een item klaar
met een eigen itemsleutel, zoals de nieuwste clients dat doen. De browser ontgrendelt de
kluis, toont het item met een live TOTP-code, wijzigt het wachtwoord, maakt een nieuw item
met een map, eigen veld en bijlage, en zet het in en uit de prullenbak. Daarna controleert
de Python-client dat alles correct versleuteld bij de server staat. Tot slot staat
tweestapsverificatie aan en vraagt de webkluis bij ontgrendelen om de code.

    cd frontend && npm ci && npm run build && cd ..
    VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_e2e \\
      python e2e/web_vault_e2e.py [map-voor-screenshots]
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "e2e"))

from notes_e2e import BACKEND, DB, FRONTEND, free_port, step, wait_http  # noqa: E402

from app.services import totp  # noqa: E402
from tests.bw_crypto import decrypt, encrypt, master_key, master_password_hash, unwrap_user_key  # noqa: E402

EMAIL = "jonas@example.com"
PASSWORD = "webkluis-master-password"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"
ATTACHMENT = b"Dit is een geheim bijlagebestand.\n" * 50


class PyClient:
    """Minimale Bitwarden-client: inloggen, sync, items aanmaken en ontsleutelen."""

    def __init__(self, api: str) -> None:
        self.api = api
        mkey = master_key(PASSWORD, EMAIL)
        r = httpx.post(
            f"{api}/identity/connect/token",
            data={
                "grant_type": "password",
                "username": EMAIL,
                "password": master_password_hash(mkey, PASSWORD),
                "client_id": "cli",
                "deviceType": "25",
                "deviceIdentifier": "e2e-python",
                "deviceName": "e2e",
            },
        )
        assert r.status_code == 200, r.text
        self.headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        self.user_key = unwrap_user_key(mkey, r.json()["Key"])

    def sync(self) -> dict:
        r = httpx.get(f"{self.api}/api/sync", headers=self.headers)
        assert r.status_code == 200, r.text
        return r.json()

    def key_for(self, cipher: dict) -> bytes:
        if cipher.get("key"):
            return decrypt(cipher["key"], self.user_key[:32], self.user_key[32:])
        return self.user_key

    def dec(self, value: str | None, key: bytes) -> str | None:
        return decrypt(value, key[:32], key[32:]).decode() if value else None

    def create_login_with_item_key(self) -> None:
        item_key = os.urandom(64)
        uk = self.user_key

        def e(text: str) -> str:
            return encrypt(text.encode(), item_key[:32], item_key[32:])

        body = {
            "type": 1,
            "key": encrypt(item_key, uk[:32], uk[32:]),
            "name": e("Grafana"),
            "notes": e("Dashboard van het homelab"),
            "favorite": True,
            "login": {
                "username": e("admin"),
                "password": e("eerste-wachtwoord"),
                "totp": e(f"otpauth://totp/Grafana:admin?secret={TOTP_SECRET}&issuer=Grafana"),
                "uris": [{"uri": e("https://grafana.example.com"), "match": None}],
                "fido2Credentials": [{"credentialId": e("passkey-blijft-staan")}],
            },
        }
        r = httpx.post(f"{self.api}/api/ciphers", headers=self.headers, json=body)
        assert r.status_code == 200, r.text

    def by_name(self, name: str) -> tuple[dict, bytes]:
        for c in self.sync()["ciphers"]:
            key = self.key_for(c)
            if self.dec(c["name"], key) == name:
                return c, key
        raise AssertionError(f"item {name} niet gevonden")


def main() -> None:
    shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    def shot(page: Page, name: str, locator=None) -> None:
        if shots:
            (locator or page).screenshot(path=str(shots / f"{name}.png"))

    if not (FRONTEND / "dist" / "index.html").exists():
        raise SystemExit("Bouw eerst de webinterface: cd frontend && npm run build")

    from tests.fake_oidc import FakeOIDCProvider

    fake = FakeOIDCProvider()
    fake.start()
    api_port, ui_port = free_port(), free_port()
    ui = f"http://127.0.0.1:{ui_port}"
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
        "VAULTX_VAULT_ICONS": "false",
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
        env={**os.environ, "VAULTX_BACKEND_URL": api},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_http(f"{api}/health", "backend")
        wait_http(ui, "webinterface")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            errors: list[str] = []
            ctx = browser.new_context(viewport={"width": 1400, "height": 900}, locale="nl-BE")
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on(
                "console", lambda m: errors.append(m.text) if "Content Security Policy" in m.text else None
            )
            page.on("dialog", lambda d: d.accept())

            step("Inloggen via (nep-)Authentik en kluis activeren")
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
            page.get_by_label("Master password", exact=True).fill(PASSWORD)
            page.get_by_label("Herhaal master password").fill(PASSWORD)
            page.get_by_label("Ik begrijp").check()
            page.get_by_role("button", name="Kluis activeren").click()
            expect(page.get_by_role("heading", name="Kluis ontgrendelen")).to_be_visible(timeout=30_000)

            step("Een Bitwarden-client zet een item klaar (met eigen itemsleutel en passkey)")
            client = PyClient(api)
            client.create_login_with_item_key()

            step("Webkluis ontgrendelen")
            page.get_by_label(re.compile("Master password voor")).fill("fout-wachtwoord")
            page.get_by_role("button", name="Ontgrendelen").click()
            expect(page.locator(".error-box")).to_contain_text("onjuist", timeout=30_000)
            page.get_by_label(re.compile("Master password voor")).fill(PASSWORD)
            page.get_by_role("button", name="Ontgrendelen").click()
            item = page.locator(".wv-items button", has_text="Grafana")
            expect(item).to_be_visible(timeout=30_000)
            expect(item).to_contain_text("admin")

            step("Item bekijken: velden, website-link en live TOTP-code")
            item.click()
            detail = page.locator(".wv-detail")
            expect(detail.get_by_role("heading", name="Grafana")).to_be_visible()
            expect(detail.get_by_text("admin", exact=True)).to_be_visible()
            expect(detail.get_by_text("eerste-wachtwoord")).to_be_hidden()
            detail.get_by_role("button", name="Tonen").first.click()
            expect(detail.get_by_text("eerste-wachtwoord")).to_be_visible()
            link = detail.get_by_role("link", name="https://grafana.example.com")
            expect(link).to_have_attribute("href", "https://grafana.example.com/")
            shown = detail.locator(".wv-totp").inner_text().replace(" ", "")
            valid = {totp.code_at(TOTP_SECRET, time.time() + d) for d in (-30, 0, 30)}
            assert shown in valid, (shown, valid)
            shot(page, "01-item-bekijken")

            step("Wachtwoord wijzigen met de generator")
            detail.get_by_role("button", name="Bewerken").click()
            page.get_by_role("button", name="Genereren").click()
            new_pw = page.get_by_label("Wachtwoord", exact=True).input_value()
            assert len(new_pw) == 20, new_pw
            shot(page, "02-bewerken")
            page.get_by_role("button", name="Opslaan").click()
            expect(detail.get_by_role("button", name="Bewerken")).to_be_visible(timeout=30_000)
            expect(detail.get_by_role("button", name=re.compile("Wachtwoordgeschiedenis"))).to_be_visible()

            c, key = client.by_name("Grafana")
            assert client.dec(c["login"]["password"], key) == new_pw
            assert client.dec(c["passwordHistory"][0]["password"], key) == "eerste-wachtwoord"
            assert client.dec(c["notes"], key) == "Dashboard van het homelab"
            assert c["login"]["fido2Credentials"], "passkey ging verloren bij opslaan"
            assert c["favorite"] is True

            step("Map maken")
            page.get_by_role("button", name="+ map").click()
            dialog = page.locator("dialog[open]")
            dialog.get_by_label("Naam").fill("Thuis")
            dialog.get_by_role("button", name="Opslaan").click()
            expect(page.locator(".wv-nav button", has_text="Thuis")).to_be_visible()

            step("Nieuw login-item met map en verborgen eigen veld")
            page.get_by_role("button", name="+ Nieuw").click()
            page.get_by_role("menuitem", name="Login").click()
            page.get_by_label("Naam", exact=True).fill("NAS")
            page.get_by_label("Map", exact=True).select_option(label="Thuis")
            page.get_by_label("Gebruikersnaam").fill("root")
            page.get_by_label("Wachtwoord", exact=True).fill("nas-geheim-123")
            page.get_by_label("Website 1", exact=True).fill("nas.lan")
            page.get_by_role("button", name="+ veld").click()
            page.get_by_label("Soort veld 1").select_option("1")
            page.get_by_label("Naam veld 1").fill("PIN")
            page.get_by_label("Waarde veld 1").fill("1234")
            page.get_by_role("button", name="Opslaan").click()
            expect(detail.get_by_role("heading", name="NAS")).to_be_visible(timeout=30_000)
            expect(detail.get_by_text("Thuis")).to_be_visible()

            c, key = client.by_name("NAS")
            assert c["key"] is None
            folders = {f["id"]: client.dec(f["name"], client.user_key) for f in client.sync()["folders"]}
            assert folders[c["folderId"]] == "Thuis", folders
            assert client.dec(c["login"]["username"], key) == "root"
            assert client.dec(c["login"]["password"], key) == "nas-geheim-123"
            uri = c["login"]["uris"][0]
            assert client.dec(uri["uri"], key) == "nas.lan"
            checksum = base64.b64encode(hashlib.sha256(b"nas.lan").digest()).decode()
            assert client.dec(uri["uriChecksum"], key) == checksum
            assert c["fields"][0]["type"] == 1 and client.dec(c["fields"][0]["value"], key) == "1234"

            step("Bijlage uploaden en weer downloaden")
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / "router-backup.txt"
                src.write_bytes(ATTACHMENT)
                detail.get_by_label("Bijlage toevoegen").set_input_files(str(src))
                expect(detail.get_by_text("router-backup.txt")).to_be_visible(timeout=30_000)
                with page.expect_download() as dl:
                    detail.get_by_role("button", name="Downloaden").click()
                assert Path(dl.value.path()).read_bytes() == ATTACHMENT
            c, key = client.by_name("NAS")
            att = c["attachments"][0]
            assert client.dec(att["fileName"], key) == "router-backup.txt"
            blob = httpx.get(att["url"]).content
            assert ATTACHMENT[:20] not in blob, "bijlage staat onversleuteld op de server"
            shot(page, "03-nieuw-item-met-bijlage")

            step("Naar de prullenbak en terug")
            detail.get_by_role("button", name="Naar prullenbak").click()
            expect(page.locator(".wv-items button", has_text="NAS")).to_be_hidden()
            page.locator(".wv-nav button", has_text="Prullenbak").click()
            page.locator(".wv-items button", has_text="NAS").click()
            detail.get_by_role("button", name="Terugzetten").click()
            page.locator(".wv-nav button", has_text="Alle items").click()
            expect(page.locator(".wv-items button", has_text="NAS")).to_be_visible()

            step("Zoeken")
            page.get_by_label("Zoeken in de kluis").fill("grafana.example")
            expect(page.locator(".wv-items button")).to_have_count(1)
            page.get_by_label("Zoeken in de kluis").fill("")
            shot(page, "04-overzicht")

            step("De webkluis staat in de apparatenlijst")
            page.get_by_role("link", name="Apps, apparaten en beveiliging").click()
            expect(page.get_by_text("VaultX-webkluis (Chrome)")).to_be_visible()

            step("Tweestapsverificatie aan: de webkluis vraagt de code bij ontgrendelen")
            card = page.locator(".card", has=page.get_by_role("heading", name="Tweestapsverificatie"))
            card.get_by_label("Master password").fill(PASSWORD)
            card.get_by_role("button", name="Instellen met een authenticator-app").click()
            expect(card.locator("img.qr")).to_be_visible(timeout=30_000)
            secret = card.locator("code").first.inner_text().strip()
            card.get_by_label("Code uit de app").fill(totp.code_at(secret))
            card.get_by_role("button", name="Aanzetten").click()
            expect(card.locator(".recovery-code")).to_be_visible()

            page.get_by_role("link", name="Items", exact=True).click()
            page.get_by_role("button", name="Vergrendelen").click()
            page.get_by_label(re.compile("Master password voor")).fill(PASSWORD)
            page.get_by_role("button", name="Ontgrendelen").click()
            code_field = page.get_by_label(re.compile("Code uit je authenticator-app"))
            expect(code_field).to_be_visible(timeout=30_000)
            shot(page, "05-tweestap-bij-ontgrendelen")
            code_field.fill(totp.code_at(secret, time.time() + 30))
            page.get_by_role("button", name="Ontgrendelen").click()
            expect(page.locator(".wv-items button", has_text="Grafana")).to_be_visible(timeout=30_000)

            assert not errors, errors
            browser.close()
        step("OK: webkluis bekijkt en bewerkt items, versleuteld zoals de Bitwarden-clients")
    finally:
        backend.terminate()
        vite.terminate()
        backend.wait(timeout=10)
        vite.wait(timeout=10)


if __name__ == "__main__":
    main()
