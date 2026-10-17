"""Fase 8: inloggen met een gewoon VaultX-account in een echte browser (Chromium via Playwright).

Start een nep-Authentik (OIDC), de VaultX-backend en de gebouwde webinterface en doorloopt:
een beheerder (via Authentik) maakt een gewoon account aan en geeft zichzelf een wachtwoord,
de nieuwe gebruiker logt in met gebruikersnaam en wachtwoord (eerst fout), wijzigt zijn
wachtwoord en logt uit zonder langs Authentik te gaan; de beheerder logt daarna met zijn
wachtwoord in en komt in hetzelfde account als via Authentik.

    cd frontend && npm ci && npm run build && cd ..
    VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_e2e \\
      python e2e/password_login_e2e.py [map-voor-screenshots]
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "e2e"))

from notes_e2e import BACKEND, DB, FRONTEND, free_port, step, wait_http  # noqa: E402
from tests.fake_oidc import FakeOIDCProvider  # noqa: E402

CAROL_PW = "carol-haar-eerste-wachtwoord"
CAROL_PW2 = "carol-haar-tweede-wachtwoord"
JONAS_PW = "jonas-zijn-vaultx-wachtwoord"


def password_login(page: Page, ui: str, login: str, password: str) -> None:
    page.goto(f"{ui}/login")
    page.get_by_label("Gebruikersnaam of e-mail").fill(login)
    page.get_by_label("Wachtwoord", exact=True).fill(password)
    page.get_by_role("button", name="Inloggen", exact=True).click()


def main() -> None:
    shots = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    def shot(page: Page, name: str) -> None:
        if shots:
            page.screenshot(path=str(shots / f"{name}.png"))

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
        [str(FRONTEND / "node_modules" / ".bin" / "vite"), "preview", "--host", "127.0.0.1",
         "--port", str(ui_port), "--strictPort"],
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

            def new_page() -> Page:
                ctx = browser.new_context(viewport={"width": 1280, "height": 860}, locale="nl-BE")
                page = ctx.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on(
                    "console",
                    lambda m: errors.append(m.text) if "Content Security Policy" in m.text else None,
                )
                page.on("dialog", lambda d: d.accept())
                return page

            step("Beheerder logt in via (nep-)Authentik")
            admin = new_page()
            fake.next_claims = {
                "sub": "jonas",
                "email": "jonas@example.com",
                "email_verified": True,
                "preferred_username": "jonas",
                "name": "Jonas",
                "groups": ["vaultx-admins"],
            }
            admin.goto(f"{ui}/auth/login?next=/users")
            expect(admin.get_by_role("heading", name="Gebruikers")).to_be_visible()
            jonas_id = admin.request.get(f"{ui}/api/v1/me").json()["id"]

            step("Gewoon account aanmaken")
            admin.get_by_role("button", name="Account aanmaken").click()
            dialog = admin.locator("dialog[open]")
            dialog.get_by_label("Gebruikersnaam").fill("carol")
            dialog.get_by_label("E-mail").fill("carol@example.com")
            dialog.get_by_label("Weergavenaam").fill("Carol")
            dialog.get_by_label("Nieuw wachtwoord").fill(CAROL_PW)
            dialog.get_by_label("Nogmaals").fill(CAROL_PW)
            shot(admin, "01-account-aanmaken")
            dialog.get_by_role("button", name="Account aanmaken").click()
            expect(admin.get_by_role("heading", name="Carol")).to_be_visible()
            expect(admin.get_by_text("Kan inloggen met gebruikersnaam")).to_be_visible()
            shot(admin, "02-account-carol")

            step("Beheerder geeft zichzelf ook een wachtwoord")
            admin.locator(".whoami-name").click()
            expect(admin.get_by_role("heading", name="Jonas")).to_be_visible()
            admin.get_by_role("button", name="Wachtwoord instellen").click()
            dialog = admin.locator("dialog[open]")
            expect(dialog.get_by_text("Het blijft hetzelfde account")).to_be_visible()
            dialog.get_by_label("Nieuw wachtwoord").fill(JONAS_PW)
            dialog.get_by_label("Nogmaals").fill(JONAS_PW)
            dialog.get_by_role("button", name="Opslaan").click()
            expect(admin.get_by_text("Je wachtwoord is ingesteld")).to_be_visible()
            expect(admin.get_by_role("button", name="Wachtwoordlogin weghalen")).to_be_visible()
            shot(admin, "03-authentik-account-met-wachtwoord")

            step("Carol: fout wachtwoord, daarna juist")
            carol = new_page()
            carol.goto(f"{ui}/vault")
            expect(carol).to_have_url(f"{ui}/login?next=%2Fvault")
            expect(carol.get_by_role("link", name="Inloggen met Authentik")).to_be_visible()
            shot(carol, "04-loginpagina")
            carol.get_by_label("Gebruikersnaam of e-mail").fill("carol")
            carol.get_by_label("Wachtwoord", exact=True).fill("helemaal-fout-wachtwoord")
            carol.get_by_role("button", name="Inloggen", exact=True).click()
            expect(carol.get_by_text("Gebruikersnaam of wachtwoord is onjuist")).to_be_visible()
            shot(carol, "05-fout-wachtwoord")
            carol.get_by_label("Wachtwoord", exact=True).fill(CAROL_PW)
            carol.get_by_role("button", name="Inloggen", exact=True).click()
            expect(carol.get_by_role("heading", name="Mijn kluis")).to_be_visible()
            expect(carol.get_by_role("link", name="Gebruikers")).to_have_count(0)

            step("Carol wijzigt haar wachtwoord")
            carol.locator(".whoami-name").click()
            carol.get_by_role("button", name="Mijn wachtwoord wijzigen").click()
            dialog = carol.locator("dialog[open]")
            dialog.get_by_label("Huidig wachtwoord").fill(CAROL_PW)
            dialog.get_by_label("Nieuw wachtwoord").fill(CAROL_PW2)
            dialog.get_by_label("Nogmaals").fill(CAROL_PW2)
            shot(carol, "06-wachtwoord-wijzigen")
            dialog.get_by_role("button", name="Wijzigen").click()
            expect(carol.get_by_text("Je wachtwoord is gewijzigd")).to_be_visible()

            step("Uitloggen zonder Authentik, opnieuw inloggen met het nieuwe wachtwoord")
            carol.get_by_role("button", name="Uitloggen").click()
            expect(carol).to_have_url(f"{ui}/login?logged_out=1")
            password_login(carol, ui, "carol@example.com", CAROL_PW2)
            expect(carol.get_by_role("heading", name="Dashboard")).to_be_visible()

            step("Jonas logt in met wachtwoord en komt in hetzelfde account")
            jonas = new_page()
            password_login(jonas, ui, "jonas", JONAS_PW)
            expect(jonas.get_by_role("heading", name="Dashboard")).to_be_visible()
            me = jonas.request.get(f"{ui}/api/v1/me").json()
            assert me["id"] == jonas_id and me["is_admin"], me
            jonas.goto(f"{ui}/users/{jonas_id}")
            expect(jonas.get_by_text("wachtwoord", exact=True).first).to_be_visible()
            shot(jonas, "07-sessies-authentik-en-wachtwoord")

            assert not errors, errors
            browser.close()
        step("OK")
    finally:
        backend.terminate()
        vite.terminate()
        fake.stop()


if __name__ == "__main__":
    main()
