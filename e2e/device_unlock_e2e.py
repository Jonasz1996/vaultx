"""Ontgrendelen met Authentik (apparaatsleutel), met de echte extensie en een echte Authentik.

VaultX logt in via een echte Authentik met een OAuth2/OpenID-provider (zoals in sso_e2e.py).
``jonas`` heeft een authenticator-app (TOTP) in Authentik, ``zonder-mfa`` niet. Gecontroleerd:

1. Aanmelden bij Authentik met MFA, kluis activeren, extensie instellen met het master password:
   de extensie registreert zichzelf (blob = user key versleuteld met haar apparaatsleutel).
2. Vergrendeld: een loginpagina bezoeken opent de kluis via Authentik, zonder master password,
   en vult in. In de auditlog staat de opening.
3. Zonder MFA in Authentik: VaultX weigert het registreren en de extensie zegt waarom.
4. Apparaat ingetrokken in VaultX: de extensie vult niets meer in tot het master password.
5. Authentik plat (en geen VaultX-sessie meer): het master password werkt gewoon.

    cd backend && VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_e2e \\
      python ../e2e/device_unlock_e2e.py --authentik http://localhost:9000 --authentik-token <token> [shots]
"""

from __future__ import annotations

import argparse
import base64
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from playwright.sync_api import BrowserContext, Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "e2e"))
sys.path.insert(0, str(ROOT / "backend"))

from authentik_npm_e2e import Authentik  # noqa: E402
from autologin_e2e import authentik_login  # noqa: E402
from invuller_e2e import EXTENSION, GOOD, Sites  # noqa: E402
from notes_e2e import BACKEND, DB, free_port, step, wait_http  # noqa: E402
from sso_e2e import vaultx_provider  # noqa: E402

from app.services import totp  # noqa: E402
from tests.bw_crypto import encrypt, new_enrollment  # noqa: E402

MASTER = "apparaat-master-password"
CSRF = {"X-VaultX-CSRF": "1"}


def add_totp_device(container: str, username: str) -> str:
    """Authenticator-app in Authentik voor deze gebruiker (de API laat geen eigen sleutel toe)."""
    key = secrets.token_hex(20)
    code = (
        "from authentik.stages.authenticator_totp.models import TOTPDevice;"
        "from authentik.core.models import User;"
        f"u=User.objects.get(username='{username}');"
        "TOTPDevice.objects.filter(user=u).delete();"
        f"TOTPDevice.objects.create(user=u, name='e2e', key='{key}', confirmed=True)"
    )
    subprocess.run(["docker", "exec", container, "ak", "shell", "-c", code], check=True, capture_output=True)
    return base64.b32encode(bytes.fromhex(key)).decode()


def login_vaultx(page: Page, api: str, user: str, totp_secret: str | None) -> None:
    """Aanmelden bij VaultX via Authentik (met de TOTP-stap als de gebruiker die heeft)."""
    page.goto(f"{api}/auth/login?next=/api/v1/me")
    authentik_login(page, user)
    if totp_secret:
        page.wait_for_selector("input[name=code]", timeout=30000)
        page.fill("input[name=code]", totp.code_at(totp_secret))
        page.keyboard.press("Enter")
    page.wait_for_url(f"{api}/api/v1/me", timeout=60000)


def enroll_and_seed(page: Page, api: str, email: str, sites: Sites) -> None:
    account, payload = new_enrollment(email, MASTER)
    r = page.request.post(f"{api}/api/v1/me/vault", data=payload, headers=CSRF)
    assert r.status == 201, r.text()
    t = httpx.post(
        f"{api}/identity/connect/token",
        data={
            "grant_type": "password",
            "username": email,
            "password": account.password_hash,
            "client_id": "cli",
            "deviceType": "25",
            "deviceIdentifier": f"seed-{email}",
            "deviceName": "e2e",
        },
    ).raise_for_status()
    uk = account.user_key

    def e(text: str) -> str:
        return encrypt(text.encode(), uk[:32], uk[32:])

    body = {
        "type": 1,
        "name": e("Plain"),
        "login": {"username": e("plain-user"), "password": e(GOOD["plain"]), "uris": [{"uri": e(sites.url("plain"))}]},
    }
    httpx.post(
        f"{api}/api/ciphers", json=body, headers={"Authorization": f"Bearer {t.json()['access_token']}"}
    ).raise_for_status()


def extension(pw, profile: str) -> tuple[BrowserContext, str]:
    ctx = pw.chromium.launch_persistent_context(
        profile,
        channel="chromium",
        headless=True,
        locale="en-US",
        viewport={"width": 1100, "height": 760},
        args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"],
    )
    worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
    return ctx, worker.url.split("/")[2]


def popup(ctx: BrowserContext, ext_id: str) -> Page:
    p = ctx.new_page()
    p.set_viewport_size({"width": 360, "height": 520})
    p.goto(f"chrome-extension://{ext_id}/popup.html")
    return p


def setup_extension(ctx: BrowserContext, ext_id: str, api: str, email: str) -> Page:
    p = popup(ctx, ext_id)
    p.get_by_label("Adres van VaultX").fill(api)
    p.get_by_label("E-mail").fill(email)
    p.get_by_label("Master password").fill(MASTER)
    p.get_by_role("button", name="Aanmelden").click()
    expect(p.get_by_text("Ontgrendeld")).to_be_visible(timeout=60000)
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--authentik", default="http://localhost:9000")
    ap.add_argument("--authentik-token", required=True)
    ap.add_argument("--ak-container", default="ak-server", help="Docker-container van de Authentik-server")
    ap.add_argument("screenshots", nargs="?", type=Path)
    args = ap.parse_args()
    shots: Path | None = args.screenshots
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    def shot(page: Page, name: str) -> None:
        if shots:
            page.screenshot(path=str(shots / f"{name}.png"))

    step("Authentik: provider voor VaultX, jonas met authenticator-app, zonder-mfa zonder")
    ak = Authentik(args.authentik, args.authentik_token)
    ak.wait_ready("default-provider-authorization-implicit-consent")
    users = {"jonas": "jonas-du-e2e", "nomfa": "zondermfa-du-e2e"}
    for u in users.values():
        ak.ensure_user(u, [])
        pk = ak.one("/core/users/", username=u)["pk"]
        ak.c.patch(f"/core/users/{pk}/", json={"email": f"{u}@du-e2e.test"}).raise_for_status()
    secret = add_totp_device(args.ak_container, users["jonas"])

    port = free_port()
    api = f"http://127.0.0.1:{port}"
    client_id, client_secret, issuer = vaultx_provider(ak, f"{api}/auth/callback")
    env = {
        **os.environ,
        "VAULTX_DATABASE_URL": DB,
        "VAULTX_SECRET_KEY": "e2e-secret-key-e2e-secret-key-0123456789",
        "VAULTX_PUBLIC_URL": api,
        "VAULTX_OIDC_ISSUER": issuer,
        "VAULTX_OIDC_CLIENT_ID": client_id,
        "VAULTX_OIDC_CLIENT_SECRET": client_secret,
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
    sites = Sites()
    sites.start()
    stopped = False
    try:
        wait_http(f"{api}/health", "backend")
        with sync_playwright() as pw, tempfile.TemporaryDirectory() as prof1, tempfile.TemporaryDirectory() as prof2:
            step("Jonas: aanmelden bij Authentik met MFA, kluis activeren, extensie instellen")
            ctx, ext_id = extension(pw, prof1)
            page = ctx.new_page()
            login_vaultx(page, api, users["jonas"], secret)
            email = f"{users['jonas']}@du-e2e.test"
            enroll_and_seed(page, api, email, sites)
            p = setup_extension(ctx, ext_id, api, email)
            # De registratie loopt op de achtergrond, meteen na het ontgrendelen.
            for _ in range(30):
                st = page.request.get(f"{api}/api/v1/vault/device-unlock").json()
                if st["devices"]:
                    break
                page.wait_for_timeout(500)
            assert st["eligible"] and [d["name"] for d in st["devices"]] == ["VaultX Invuller (Chrome)"], st
            p.reload()
            expect(p.get_by_text("Ontgrendelen met Authentik staat aan op deze browser.")).to_be_visible()
            shot(p, "01-authentik-aan")

            step("Zelf vergrendeld: blijft dicht tot je zelf ontgrendelt")
            p.get_by_role("button", name="Vergrendelen").click()
            expect(p.get_by_role("button", name="Ontgrendelen met Authentik")).to_be_visible()
            shot(p, "02-zelf-vergrendeld")
            p.close()
            before = len(sites.posted("plain"))
            site = ctx.new_page()
            site.goto(sites.url("plain"))
            site.wait_for_timeout(3000)
            assert site.locator("input[name=password]").input_value() == ""
            assert len(sites.posted("plain")) == before
            site.close()

            step("Na 12 uur vergrendeld, zonder VaultX-sessie: stil via Authentik, kluis open, invullen")
            sw = ctx.service_workers[0]
            # Het slot van 12 uur nabootsen (zonder "zelf vergrendeld"), en enkel de VaultX-cookie
            # weg (de Authentik-sessie blijft): de extensie meldt zich stil aan.
            sw.evaluate("ext.storage.session.remove('manualLock').then(() => lock('Na 12 uur vergrendeld.'))")
            ctx.clear_cookies(domain="127.0.0.1")
            site = ctx.new_page()
            site.goto(sites.url("plain"))
            expect(site.locator("#ok")).to_have_text("Ingelogd op plain", timeout=30000)
            site.close()
            # De testcode zelf heeft weer een VaultX-sessie nodig (Authentik-sessie bestaat nog).
            page.goto(f"{api}/auth/login?next=/api/v1/me")
            page.wait_for_url(f"{api}/api/v1/me", timeout=30000)
            # Geopend (de auditlog is voor beheerders; hier staat "laatst geopend" bij het apparaat).
            dev = page.request.get(f"{api}/api/v1/vault/device-unlock").json()["devices"][0]
            assert dev["last_used_at"] and dev["last_used_ip"] == "127.0.0.1", dev
            p = popup(ctx, ext_id)
            expect(p.get_by_text("Ontgrendeld met Authentik")).to_be_visible()
            shot(p, "03-ontgrendeld-met-authentik")
            p.close()
            page.goto(f"{api}/api/v1/vault/device-unlock")

            step("Zonder MFA in Authentik: registreren geweigerd, de extensie zegt waarom")
            ctx2, ext2 = extension(pw, prof2)
            page2 = ctx2.new_page()
            login_vaultx(page2, api, users["nomfa"], None)
            enroll_and_seed(page2, api, f"{users['nomfa']}@du-e2e.test", sites)
            p2 = setup_extension(ctx2, ext2, api, f"{users['nomfa']}@du-e2e.test")
            for _ in range(30):
                p2.reload()
                if p2.get_by_text("tweede stap (MFA)").count():
                    break
                p2.wait_for_timeout(500)
            expect(p2.get_by_text("tweede stap (MFA)")).to_be_visible()
            st2 = page2.request.get(f"{api}/api/v1/vault/device-unlock").json()
            assert st2["reason"] == "mfa_required" and st2["devices"] == [], st2
            shot(p2, "04-zonder-mfa")
            ctx2.close()

            step("Apparaat ingetrokken: geen invullen meer zonder master password")
            dev = page.request.get(f"{api}/api/v1/vault/device-unlock").json()["devices"][0]
            assert page.request.delete(f"{api}/api/v1/vault/device-unlock/{dev['id']}", headers=CSRF).status == 204
            ctx.service_workers[0].evaluate("lock('Na 12 uur vergrendeld.')")
            before = len(sites.posted("plain"))
            site = ctx.new_page()
            site.goto(sites.url("plain"))
            site.wait_for_timeout(3000)
            assert site.locator("input[name=password]").input_value() == ""
            assert len(sites.posted("plain")) == before
            site.close()
            p = popup(ctx, ext_id)
            expect(p.get_by_label("Master password")).to_be_visible()
            shot(p, "05-ingetrokken")
            p.get_by_label("Master password").fill(MASTER)
            p.get_by_role("button", name="Ontgrendelen", exact=True).click()
            expect(p.get_by_text("Ontgrendeld")).to_be_visible(timeout=60000)
            p.close()
            for _ in range(30):
                if page.request.get(f"{api}/api/v1/vault/device-unlock").json()["devices"]:
                    break
                page.wait_for_timeout(500)

            step("Authentik plat en geen VaultX-sessie meer: het master password werkt gewoon")
            subprocess.run(["docker", "stop", args.ak_container], check=True, capture_output=True)
            stopped = True
            ctx.clear_cookies()
            p = popup(ctx, ext_id)
            p.get_by_role("button", name="Vergrendelen").click()
            p.reload()
            expect(p.get_by_label("Master password")).to_be_visible(timeout=30000)
            p.get_by_label("Master password").fill(MASTER)
            p.get_by_role("button", name="Ontgrendelen", exact=True).click()
            expect(p.get_by_text("Ontgrendeld")).to_be_visible(timeout=60000)
            shot(p, "06-authentik-plat-master-password")
            p.close()
            before = len(sites.posted("plain"))
            site = ctx.new_page()
            site.goto(sites.url("plain"))
            expect(site.locator("#ok")).to_have_text("Ingelogd op plain", timeout=30000)
            assert len(sites.posted("plain")) == before + 1
            ctx.close()
        step("OK: aangemeld bij Authentik met MFA = kluis open; zonder MFA, ingetrokken of plat: master password")
    finally:
        if stopped:
            subprocess.run(["docker", "start", args.ak_container], check=False, capture_output=True)
        backend.terminate()
        try:
            backend.wait(timeout=10)
        except subprocess.TimeoutExpired:
            backend.kill()


if __name__ == "__main__":
    main()
