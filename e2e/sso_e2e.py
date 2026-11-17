"""Aangemeld bij Authentik = meteen in VaultX, tegen een echte Authentik in Chromium.

VaultX logt hier zelf in via een echte Authentik (geen nep-provider): het script maakt met het
beheertoken een OAuth2/OpenID-provider en applicatie voor VaultX aan, met een gebruiker
``jonas-sso-e2e``. Dan, met de gebouwde webinterface:

1. Gewone login aan (standaard): een browser zonder Authentik-sessie opent VaultX. VaultX probeert
   stil (prompt=none), Authentik antwoordt ``login_required`` en de loginpagina verschijnt, zonder
   lus en zonder mislukte login in de auditlog.
2. Dezelfde gebruiker meldt zich bij Authentik aan en opent dan VaultX: meteen binnen, zonder klik
   en zonder aanmeldstap van Authentik.
3. ``/login?lokaal=1`` toont de loginpagina, ook met een Authentik-sessie (noodtoegang).
4. Gewone login uit (VAULTX_LOCAL_LOGIN_ENABLED=false): zonder sessie stuurt VaultX meteen door
   naar het aanmelden bij Authentik, en daarna terug naar de gevraagde pagina.
5. Authentik onbereikbaar: de loginpagina met de gewone login blijft werken.

    cd frontend && npm ci && npm run build && cd ..
    VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_e2e \\
      python e2e/sso_e2e.py --authentik http://localhost:9000 --authentik-token <beheertoken> [screenshots]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "e2e"))
sys.path.insert(0, str(ROOT / "backend"))

from authentik_npm_e2e import Authentik  # noqa: E402
from autologin_e2e import authentik_login, watch_stages  # noqa: E402
from notes_e2e import BACKEND, DB, FRONTEND, free_port, step, wait_http  # noqa: E402

SLUG = "vaultx-sso-e2e"
USER = "jonas-sso-e2e"
EMAIL = "jonas@sso-e2e.test"
SCOPES = ("openid", "email", "profile")


def vaultx_provider(ak: Authentik, redirect_uri: str) -> tuple[str, str, str]:
    """OAuth2/OpenID-provider + applicatie voor VaultX zelf, zoals in docs/authentik.md."""
    if ak.one("/core/applications/", slug=SLUG, superuser_full_list="true"):
        ak.c.delete(f"/core/applications/{SLUG}/").raise_for_status()
    for p in ak.get("/providers/oauth2/", search=SLUG):
        ak.c.delete(f"/providers/oauth2/{p['pk']}/")
    flows = {
        k: ak.one("/flows/instances/", slug=slug)["pk"]
        for k, slug in (
            ("authorization", "default-provider-authorization-implicit-consent"),
            ("invalidation", "default-provider-invalidation-flow"),
        )
    }
    managed = {m.get("managed"): m["pk"] for m in ak.get("/propertymappings/provider/scope/")}
    mappings = [managed[f"goauthentik.io/providers/oauth2/scope-{s}"] for s in SCOPES]
    key = next(
        k for k in ak.get("/crypto/certificatekeypairs/") if k["name"] == "authentik Self-signed Certificate"
    )
    client_id, secret = f"{SLUG}-client", "sso-e2e-client-secret-0123456789abcdef"
    provider = ak.c.post(
        "/providers/oauth2/",
        json={
            "name": SLUG,
            "client_id": client_id,
            "client_secret": secret,
            "client_type": "confidential",
            "authorization_flow": flows["authorization"],
            "invalidation_flow": flows["invalidation"],
            "grant_types": ["authorization_code", "refresh_token"],
            "redirect_uris": [{"matching_mode": "strict", "url": redirect_uri}],
            "property_mappings": mappings,
            "signing_key": key["pk"],
            "include_claims_in_id_token": True,
            "issuer_mode": "per_provider",
        },
    ).raise_for_status()
    ak.c.post(
        "/core/applications/",
        json={"name": "VaultX (sso-e2e)", "slug": SLUG, "provider": provider.json()["pk"]},
    ).raise_for_status()
    return (
        client_id,
        secret,
        f"{ak.c.base_url.scheme}://{ak.c.base_url.netloc.decode()}/application/o/{SLUG}/",
    )


class VaultX:
    """Backend + gebouwde webinterface (vite preview) op een vaste poort, opnieuw te starten."""

    def __init__(self, env: dict[str, str]) -> None:
        self.api_port, self.ui_port = free_port(), free_port()
        self.ui = f"http://127.0.0.1:{self.ui_port}"
        self.env = {**os.environ, **env, "VAULTX_PUBLIC_URL": self.ui}
        self.procs: list[subprocess.Popen] = []

    @contextmanager
    def running(self, **extra: str) -> Iterator[None]:
        env = {**self.env, **extra}
        backend = subprocess.Popen(  # noqa: S603 - vaste argumenten
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--port",
                str(self.api_port),
                "--log-level",
                "warning",
            ],
            cwd=BACKEND,
            env=env,
        )
        vite = subprocess.Popen(  # noqa: S603
            [
                str(FRONTEND / "node_modules" / ".bin" / "vite"),
                "preview",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.ui_port),
                "--strictPort",
            ],
            cwd=FRONTEND,
            env={**os.environ, "VAULTX_BACKEND_URL": f"http://127.0.0.1:{self.api_port}"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            wait_http(f"http://127.0.0.1:{self.api_port}/health", "backend")
            wait_http(self.ui, "webinterface")
            yield
        finally:
            for p in (backend, vite):
                p.terminate()
            for p in (backend, vite):
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--authentik", default="http://localhost:9000")
    ap.add_argument("--authentik-token", required=True, help="beheertoken (enkel voor de voorbereiding)")
    ap.add_argument("screenshots", nargs="?", type=Path)
    args = ap.parse_args()
    authentik = args.authentik.rstrip("/")
    shots: Path | None = args.screenshots
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    def shot(page: Page, name: str) -> None:
        if shots:
            page.screenshot(path=str(shots / f"{name}.png"))

    if not (FRONTEND / "dist" / "index.html").exists():
        raise SystemExit("Bouw eerst de webinterface: cd frontend && npm run build")

    step("Authentik: provider en applicatie voor VaultX, gebruiker jonas-sso-e2e")
    ak = Authentik(authentik, args.authentik_token)
    ak.wait_ready("default-provider-authorization-implicit-consent")
    ak.ensure_user(USER, [])
    pk = ak.one("/core/users/", username=USER)["pk"]
    ak.c.patch(f"/core/users/{pk}/", json={"email": EMAIL}).raise_for_status()

    vx = VaultX(
        {
            "VAULTX_DATABASE_URL": DB,
            "VAULTX_SECRET_KEY": "e2e-secret-key-e2e-secret-key-0123456789",
            "VAULTX_COOKIE_SECURE": "false",
            "VAULTX_NPM_SYNC_INTERVAL_MINUTES": "0",
            "VAULTX_LOG_LEVEL": "WARNING",
        }
    )
    client_id, secret, issuer = vaultx_provider(ak, f"{vx.ui}/auth/callback")
    vx.env.update(
        {
            "VAULTX_OIDC_ISSUER": issuer,
            "VAULTX_OIDC_CLIENT_ID": client_id,
            "VAULTX_OIDC_CLIENT_SECRET": secret,
        }
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"], cwd=BACKEND, env=vx.env, check=True
    )
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=vx.env, check=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        def new_page() -> Page:
            ctx = browser.new_context(viewport={"width": 1280, "height": 860}, locale="en-US")
            page = ctx.new_page()
            page.on("pageerror", lambda e: print("    pagina-fout:", e))
            return page

        with vx.running():
            step("Zonder Authentik-sessie: stille poging, dan de loginpagina (geen lus)")
            page = new_page()
            stages = watch_stages(page)
            page.goto(f"{vx.ui}/vault")
            expect(page).to_have_url(f"{vx.ui}/login?stil=1&next=%2Fvault", timeout=30000)
            expect(page.get_by_role("link", name="Inloggen met Authentik")).to_be_visible()
            expect(page.get_by_label("Gebruikersnaam of e-mail")).to_be_visible()
            assert not stages, f"de stille poging mag geen Authentik-scherm tonen: {stages}"
            page.wait_for_timeout(1500)
            expect(page).to_have_url(f"{vx.ui}/login?stil=1&next=%2Fvault")
            shot(page, "01-zonder-authentik-sessie")

            step("Aanmelden bij Authentik, dan VaultX openen: meteen binnen, zonder klik")
            page.goto(f"{authentik}/if/user/")
            authentik_login(page, USER)
            page.wait_for_url(f"{authentik}/if/user/**", timeout=60000)
            stages = watch_stages(page)
            started = time.monotonic()
            page.goto(f"{vx.ui}/vault")
            expect(page).to_have_url(f"{vx.ui}/vault", timeout=30000)
            expect(page.get_by_role("heading", name="Mijn kluis")).to_be_visible(timeout=30000)
            took = time.monotonic() - started
            assert not [s for s in stages if s in ("ak-stage-identification", "ak-stage-password")], stages
            me = page.request.get(f"{vx.ui}/api/v1/me").json()
            assert me["email"] == EMAIL, me
            shot(page, "02-meteen-binnen")
            print(f"    binnen in {took:.1f}s, Authentik-stappen: {', '.join(stages) or 'geen'}")

            # Geen mislukte login in de auditlog door de stille poging van daarnet.
            audit = page.request.get(f"{vx.ui}/api/v1/audit", params={"action": "auth.login"}).json()["items"]
            assert all(a["outcome"] == "success" for a in audit), audit

            step("Noodtoegang: /login?lokaal=1 stuurt niet door, ook met een Authentik-sessie")
            page2 = page.context.new_page()
            page2.goto(f"{vx.ui}/login?lokaal=1")
            expect(page2.get_by_text("Noodtoegang")).to_be_visible()
            page2.wait_for_timeout(1500)
            expect(page2).to_have_url(f"{vx.ui}/login?lokaal=1")
            shot(page2, "03-noodtoegang")
            page.context.close()

        with vx.running(VAULTX_LOCAL_LOGIN_ENABLED="false"):
            step("Gewone login uit: zonder sessie meteen naar het aanmelden bij Authentik")
            page = new_page()
            page.goto(f"{vx.ui}/notes")
            page.wait_for_url(f"{authentik}/**", timeout=30000)
            shot(page, "04-meteen-naar-authentik")
            authentik_login(page, USER)
            expect(page).to_have_url(f"{vx.ui}/notes", timeout=60000)
            print("    na het aanmelden terug op /notes")
            page.context.close()

        with vx.running(VAULTX_OIDC_ISSUER="http://127.0.0.1:9/application/o/weg/"):
            step("Authentik onbereikbaar: de loginpagina blijft werken")
            page = new_page()
            page.goto(f"{vx.ui}/vault")
            expect(page.get_by_text("Authentik is momenteel niet bereikbaar.")).to_be_visible(timeout=30000)
            expect(page.get_by_label("Gebruikersnaam of e-mail")).to_be_visible()
            shot(page, "05-authentik-plat")
            page.context.close()
        browser.close()

    with httpx.Client(trust_env=False) as c:
        assert c.get(f"{issuer}.well-known/openid-configuration").status_code == 200
    step("OK: aangemeld bij Authentik = meteen in VaultX, en zelf inloggen blijft altijd mogelijk")


if __name__ == "__main__":
    main()
