"""Fase 9: Mijn kluis in een echte browser (Chromium via Playwright).

Kluis activeren, tweestapsverificatie instellen (QR-code, eerste code, herstelcode),
het master password wijzigen naar Argon2id (WebAssembly onder de strikte CSP),
daarna notities ontgrendelen met het nieuwe wachtwoord. Tot slot logt een
"Bitwarden-client" (Python, onafhankelijke crypto uit backend/tests) in met het
nieuwe wachtwoord en een TOTP-code.

    cd frontend && npm ci && npm run build && cd ..
    VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_e2e \\
      python e2e/vault_ui_e2e.py [map-voor-screenshots]
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "e2e"))

from notes_e2e import BACKEND, DB, FRONTEND, free_port, step, wait_http  # noqa: E402

from app.services import totp  # noqa: E402
from tests.bw_crypto import argon2_master_key, master_password_hash  # noqa: E402
from tests.fake_oidc import FakeOIDCProvider  # noqa: E402

EMAIL = "jonas@example.com"
OLD_PW = "mijn-eerste-master-password"
NEW_PW = "mijn-nieuwe-langere-master-password"


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
    ui = f"http://127.0.0.1:{ui_port}"
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
        env={**os.environ, "VAULTX_BACKEND_URL": f"http://127.0.0.1:{api_port}"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_http(f"http://127.0.0.1:{api_port}/health", "backend")
        wait_http(ui, "webinterface")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            errors: list[str] = []
            ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="nl-BE")
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
            page.get_by_label("Master password", exact=True).fill(OLD_PW)
            page.get_by_label("Herhaal master password").fill(OLD_PW)
            page.get_by_label("Ik begrijp").check()
            page.get_by_role("button", name="Kluis activeren").click()
            page.get_by_role("link", name="Apps, apparaten en beveiliging").click(timeout=30_000)
            expect(page.get_by_text("Verbinden met een Bitwarden-app")).to_be_visible()

            step("Tweestapsverificatie instellen")
            card = page.locator(".card", has=page.get_by_role("heading", name="Tweestapsverificatie"))
            card.get_by_label("Master password").fill(OLD_PW)
            card.get_by_role("button", name="Instellen met een authenticator-app").click()
            expect(card.locator("img.qr")).to_be_visible(timeout=30_000)
            secret = card.locator("code").first.inner_text().strip()
            assert re.fullmatch(r"[A-Z2-7]{32}", secret), secret
            shot(page, "01-tweestap-qr", card)
            card.get_by_label("Code uit de app").fill(totp.code_at(secret))
            card.get_by_role("button", name="Aanzetten").click()
            recovery = card.locator(".recovery-code")
            expect(recovery).to_be_visible()
            assert re.fullmatch(r"([A-Z2-7]{4}-){7}[A-Z2-7]{4}", recovery.inner_text().strip())
            expect(card.get_by_text("aan", exact=True)).to_be_visible()
            shot(page, "02-tweestap-herstelcode", card)

            step("Master password wijzigen naar Argon2id")
            pw_card = page.locator(".card", has=page.get_by_role("heading", name="Master password wijzigen"))
            pw_card.get_by_label("Huidig master password").fill(OLD_PW)
            pw_card.get_by_label("Nieuw master password", exact=True).fill(NEW_PW)
            pw_card.get_by_label("Herhaal nieuw master password").fill(NEW_PW)
            pw_card.get_by_label("Sleutelafleiding").select_option("1")
            shot(page, "03-master-password-wijzigen", pw_card)
            pw_card.get_by_role("button", name="Wijzigen en alle apparaten afmelden").click()
            expect(pw_card.get_by_text("Master password gewijzigd")).to_be_visible(timeout=60_000)
            expect(page.get_by_text("Argon2id, 3 iteraties, 64 MiB, parallelisme 4")).to_be_visible()

            step("Notities ontgrendelen met het nieuwe wachtwoord (Argon2id in de browser)")
            page.goto(f"{ui}/notes")
            page.get_by_label(re.compile("Master password")).fill(NEW_PW)
            page.get_by_role("button", name="Ontgrendelen").click()
            expect(page.get_by_role("button", name="Ontgrendelen")).to_be_hidden(timeout=60_000)
            assert not errors, errors
            browser.close()

        step("Bitwarden-client logt in met het nieuwe wachtwoord en een code")
        api = f"http://127.0.0.1:{api_port}"
        pre = httpx.post(f"{api}/identity/accounts/prelogin/password", json={"email": EMAIL}).json()
        assert pre["kdfSettings"]["kdfType"] == 1, pre
        mkey = argon2_master_key(NEW_PW, EMAIL, 3, 64, 4)
        form = {
            "grant_type": "password",
            "username": EMAIL,
            "password": master_password_hash(mkey, NEW_PW),
            "client_id": "cli",
            "deviceType": "25",
            "deviceIdentifier": "e2e-device",
            "deviceName": "e2e",
        }
        r = httpx.post(f"{api}/identity/connect/token", data=form)
        assert r.status_code == 400 and "TwoFactorProviders2" in r.json(), r.text
        code = totp.code_at(secret, time.time() + 30)
        r = httpx.post(
            f"{api}/identity/connect/token", data={**form, "twoFactorToken": code, "twoFactorProvider": "0"}
        )
        assert r.status_code == 200, r.text
        step("OK: tweestapsverificatie en master password wijzigen werken in de browser")
    finally:
        backend.terminate()
        vite.terminate()
        backend.wait(timeout=10)
        vite.wait(timeout=10)


if __name__ == "__main__":
    main()
