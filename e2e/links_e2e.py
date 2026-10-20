"""Links (fase 10) in een echte browser (Chromium via Playwright).

Importeert een Linkwarden-export via de webinterface en een nagebootste Linkwarden-database
met gearchiveerde bestanden via `python -m app.cli import-linkwarden`, en controleert daarna
in de browser: collectieboom, zoeken (ook in de gearchiveerde tekst), tags, vastpinnen, een
link toevoegen en bewerken, de leesbare versie (gesaneerd) en de screenshot, zonder
CSP-fouten.

    cd frontend && npm ci && npm run build && cd ..
    VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_e2e \\
      python e2e/links_e2e.py [map-voor-screenshots]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import psycopg
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "e2e"))

from notes_e2e import BACKEND, DB, FRONTEND, free_port, png, step, wait_http  # noqa: E402

from tests.test_links import LINKWARDEN_EXPORT, LW_SCHEMA  # noqa: E402

READABLE = {
    "title": "Portainer",
    "byline": "Portainer-team",
    "content": (
        "<h2>Containers beheren</h2><p>Portainer is een webinterface voor Docker.</p>"
        '<p onclick="alert(1)">Klik</p><script>window.hacked = true</script>'
        '<img src="https://tracker.example/pixel.gif">'
    ),
    "textContent": "Containers beheren. Portainer is een webinterface voor Docker.",
}


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

    # Nagebootste Linkwarden-database in een eigen schema, met de bestanden in een map.
    plain = DB.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(plain, autocommit=True) as conn:
        conn.execute(LW_SCHEMA)
    files = Path(tempfile.mkdtemp(prefix="linkwarden-"))
    (files / "archives/2").mkdir(parents=True)
    (files / "archives/2/1.jpeg").write_bytes(png(640, 360, (36, 99, 235)))  # inhoud telt, niet de extensie
    (files / "archives/2/1.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (files / "archives/2/1_readability.json").write_text(json.dumps(READABLE))
    page_html = "<html><body><script>alert(1)</script><h1>Portainer</h1></body></html>"
    (files / "archives/2/1.html").write_text(page_html)

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
            answers: list[str] = []  # antwoorden voor prompt(), anders gewoon OK
            page.on("dialog", lambda d: d.accept(answers.pop(0)) if answers else d.accept())

            step("Inloggen en de lege pagina Links openen")
            fake.next_claims = {
                "sub": "jonas",
                "email": "jonas@example.com",
                "email_verified": True,
                "preferred_username": "jonas",
                "name": "Jonas",
                "groups": ["vaultx-admins"],
            }
            page.goto(f"{ui}/auth/login?next=/links")
            expect(page.get_by_role("heading", name="Alle links")).to_be_visible()
            expect(page.get_by_text("Nog geen links")).to_be_visible()

            step("Linkwarden-database importeren met de CLI (met archieven)")
            r = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.cli",
                    "import-linkwarden",
                    "--dsn",
                    plain + "?options=-csearch_path%3Dlw_test",
                    "--from",
                    "jonas",
                    "--to",
                    "jonas",
                    "--files",
                    str(files),
                ],
                cwd=BACKEND,
                env=env,
                capture_output=True,
                text=True,
            )
            print(r.stdout, r.stderr)
            assert r.returncode == 0 and "2 links aangemaakt" in r.stdout and "4 archieven" in r.stdout

            step("Linkwarden-export importeren via de webinterface")
            page.get_by_role("button", name="Importeren").click()
            dialog = page.locator("dialog[open]")
            export = files / "linkwarden-export.json"
            export.write_text(json.dumps(LINKWARDEN_EXPORT))
            dialog.locator("input[type=file]").set_input_files(str(export))
            dialog.get_by_role("button", name="Importeren").click()
            expect(dialog.locator(".good-box")).to_contain_text("1 collecties en 2 links aangemaakt")
            shot(page, "01-import", dialog)
            dialog.get_by_role("button", name="Sluiten").click()

            step("Collectieboom en filters")
            side = page.locator(".notes-side")
            # "Homelab" bestond al door de database-import en wordt hergebruikt.
            expect(side.locator(".link-col")).to_have_count(3)
            expect(side.locator(".link-col.depth-1", has_text="Docker")).to_be_visible()
            expect(side.locator(".link-col.depth-1", has_text="Backups")).to_be_visible()
            expect(page.locator(".link-row")).to_have_count(4)
            shot(page, "02-alle-links")

            side.locator(".link-col", has_text="Docker").click()
            expect(page.get_by_role("heading", name="Docker")).to_be_visible()
            expect(page.locator(".link-row")).to_have_count(1)
            row = page.locator(".link-row", has_text="Portainer")
            expect(row.locator(".chip-archive")).to_have_count(4)

            step("Zoeken in de gearchiveerde tekst en filteren op tag")
            side.get_by_text("Alle links").click()
            side.get_by_placeholder("Zoeken in links").fill("container browser")
            expect(page.locator(".link-row")).to_have_count(1)
            expect(page.locator(".link-row")).to_contain_text("Portainer")
            side.get_by_placeholder("Zoeken in links").fill("")
            side.get_by_role("button", name="#virtualisatie").click()
            expect(page.locator(".link-row")).to_have_count(2)
            page.get_by_role("button", name="wissen").click()
            side.get_by_text("Vastgepind").click()
            expect(page.locator(".link-row")).to_have_count(2)  # Portainer (database) en Restic (export)

            step("Leesbare versie: gesaneerd, zonder scripts of externe afbeeldingen")
            side.get_by_text("Alle links").click()
            row = page.locator(".link-row", has_text="Portainer")
            row.get_by_role("button", name="Leesbaar").click()
            reader = page.locator("dialog[open] .link-readable")
            expect(reader.get_by_role("heading", name="Containers beheren")).to_be_visible()
            assert reader.locator("script, [onclick]").count() == 0
            assert page.evaluate("window.hacked") is None
            shot(page, "03-leesbaar", page.locator("dialog[open]"))
            page.locator("dialog[open]").get_by_role("button", name="Sluiten").click()

            step("Screenshot uit het Linkwarden-archief")
            row.get_by_role("button", name="Screenshot").click()
            img = page.locator("dialog[open] img.link-shot")
            expect(img).to_be_visible()
            expect(img).to_have_js_property("naturalWidth", 640)
            page.locator("dialog[open]").get_by_role("button", name="Sluiten").click()

            step("Gearchiveerde pagina wordt in een sandbox geserveerd")
            href = row.get_by_role("link", name="Pagina").get_attribute("href")
            cookies = {c["name"]: c["value"] for c in ctx.cookies()}
            r2 = httpx.get(f"{ui}{href}", cookies=cookies)
            assert r2.status_code == 200 and r2.headers["content-security-policy"].startswith("sandbox;")

            step("Link toevoegen in een nieuwe subcollectie, met tags")
            page.get_by_role("button", name="Collectie", exact=True).click()
            dialog = page.locator("dialog[open]")
            dialog.get_by_label("Naam").fill("Monitoring")
            dialog.get_by_label("In collectie").select_option(label="Homelab")
            dialog.get_by_role("button", name="Aanmaken").click()
            expect(side.locator(".link-col.depth-1", has_text="Monitoring")).to_be_visible()
            side.locator(".link-col", has_text="Monitoring").click()
            page.get_by_role("button", name="Link toevoegen").click()
            dialog = page.locator("dialog[open]")
            dialog.get_by_label("URL").fill("grafana.com/docs")
            dialog.get_by_label("Titel").fill("Grafana documentatie")
            dialog.get_by_label("Tags").fill("monitoring, docs")
            shot(page, "04-link-toevoegen", dialog)
            dialog.get_by_role("button", name="Toevoegen").click()
            row = page.locator(".link-row", has_text="Grafana documentatie")
            expect(row).to_be_visible()
            expect(row.get_by_role("link", name="Grafana documentatie")).to_have_attribute(
                "href", "https://grafana.com/docs"
            )
            expect(row.get_by_role("button", name="#monitoring")).to_be_visible()

            step("Vastpinnen en bewerken")
            row.get_by_role("button", name="Vastpinnen").click()
            expect(row.get_by_role("button", name="Losmaken")).to_be_visible()
            row.get_by_role("button", name="Bewerken").click()
            dialog = page.locator("dialog[open]")
            dialog.get_by_label("Beschrijving").fill("Dashboards en alerts")
            dialog.get_by_role("button", name="Bewaren").click()
            expect(row).to_contain_text("Dashboards en alerts")
            side.get_by_text("Alle links").click()
            expect(page.locator(".link-row")).to_have_count(5)
            shot(page, "05-overzicht")

            step("Tags beheren: hernoemen voegt samen")
            side.get_by_role("button", name="beheren").click()
            dialog = page.locator("dialog[open]")
            answers.append("backup")
            dialog.locator("li", has_text="#docs").get_by_role("button", name="Hernoemen").click()
            expect(dialog.locator("li", has_text="#backup")).to_contain_text("2 links")
            dialog.get_by_role("button", name="Sluiten").click()

            assert not errors, errors
            browser.close()
        print("OK: links in de browser werken")
    finally:
        backend.terminate()
        vite.terminate()
        backend.wait(10)
        vite.wait(10)
        with psycopg.connect(plain, autocommit=True) as conn:
            conn.execute("DROP SCHEMA IF EXISTS lw_test CASCADE")


if __name__ == "__main__":
    main()
