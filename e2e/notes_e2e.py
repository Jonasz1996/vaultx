"""Fase 7: notities end-to-end in een echte browser (Chromium via Playwright).

Start een nep-Authentik (OIDC), de VaultX-backend en de gebouwde webinterface
(`vite preview`, met de backend erachter zoals nginx dat doet) en doorloopt:
kluis activeren, ontgrendelen (fout en juist wachtwoord), voorbeeldnotities,
wikilinks, een catalogus-link, taken afvinken, [[-suggesties, hernoemen met
links bijwerken, zoeken op tag, snelkiezer, grafiek, import, versiegeschiedenis,
prullenbak en vergrendelen. Daarna controleert het in PostgreSQL dat er nergens
klare tekst van de notities staat.

    cd frontend && npm ci && npm run build && cd ..
    pip install playwright && playwright install chromium
    VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_e2e \\
      python e2e/notes_e2e.py [map-voor-screenshots]
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import psycopg
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
sys.path.insert(0, str(BACKEND))

from tests.fake_oidc import FakeOIDCProvider  # noqa: E402

DB = os.environ.get("VAULTX_DATABASE_URL", "postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_e2e")
PASSWORD = "correct horse battery staple notes"
EMAIL = "jonas@example.com"
CSRF = {"X-VaultX-CSRF": "1"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def step(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def wait_http(url: str, what: str) -> None:
    for _ in range(150):
        try:
            if httpx.get(url, timeout=2).status_code < 500:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise SystemExit(f"{what} start niet ({url})")


def saved(page: Page) -> None:
    expect(page.locator(".save-state")).to_have_text("Bewaard", timeout=10_000)


def body_of(page: Page, title: str) -> str:
    """Leest een notitie terug zoals de browser haar ontsleutelt (via de editor)."""
    open_note(page, title)
    page.get_by_role("button", name="Naast elkaar").click()
    return page.locator("textarea.editor").input_value()


def open_note(page: Page, title: str) -> None:
    page.keyboard.press("Control+o")
    page.get_by_placeholder("Typ een titel").fill(title)
    page.keyboard.press("Enter")
    expect(page.locator(".note-title")).to_have_value(title)


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
    importdir = Path(tempfile.mkdtemp(prefix="notes-import-"))
    (importdir / "Wifi thuis.md").write_text(
        "# Wifi thuis\n\nSSID: thuis\nWachtwoord: ||geheim-wifi||\n#thuis\n"
    )
    (importdir / "Boodschappen.md").write_text("- [ ] melk\n- [x] brood\n")
    (importdir / "foto.png").write_bytes(b"\x89PNG")
    try:
        wait_http(f"http://127.0.0.1:{api_port}/health", "backend")
        wait_http(ui, "webinterface")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="nl-BE")
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("dialog", lambda d: d.accept())

            step("Inloggen via (nep-)Authentik als beheerder")
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

            org = page.request.post(
                f"{ui}/api/v1/organizations", data={"slug": "thuis", "name": "Thuis"}, headers=CSRF
            )
            assert org.ok, org.text()
            app = page.request.post(
                f"{ui}/api/v1/organizations/{org.json()['id']}/applications",
                data={"name": "Grafana", "url": "https://grafana.thuis.lan", "app_type": "grafana"},
                headers=CSRF,
            )
            assert app.ok, app.text()

            step("Kluis activeren in de browser")
            pw_inputs = page.locator("input[type=password]")
            pw_inputs.nth(0).fill(PASSWORD)
            pw_inputs.nth(1).fill(PASSWORD)
            page.get_by_label("Ik begrijp dat niemand").check()
            page.get_by_role("button", name="Kluis activeren").click()
            expect(page.get_by_text("Verbinden met een Bitwarden-app")).to_be_visible(timeout=30_000)

            step("Notities: fout wachtwoord geweigerd, juist wachtwoord ontgrendelt")
            page.get_by_role("link", name="Notities").click()
            expect(page.get_by_role("heading", name="Kluis ontgrendelen")).to_be_visible()
            page.locator("input[type=password]").fill("verkeerd wachtwoord")
            page.get_by_role("button", name="Ontgrendelen").click()
            expect(page.get_by_text("Master password is onjuist")).to_be_visible(timeout=30_000)
            shot(page, "01-ontgrendelen")
            page.locator("input[type=password]").fill(PASSWORD)
            page.get_by_role("button", name="Ontgrendelen").click()
            expect(page.get_by_text("Nog geen notities")).to_be_visible(timeout=30_000)

            step("Voorbeeldnotities: links, catalogus-app, callout, taken, geheim")
            page.get_by_role("button", name="Voorbeeldnotities maken").click()
            md = page.locator(".preview .md")
            expect(md.get_by_role("heading", name="Welkom in je notities")).to_be_visible()
            expect(md.locator("a.wikilink[data-note]").first).to_have_text("Runbook Grafana")
            expect(md.locator("a.app-link")).to_have_text("Grafana")
            expect(md.locator(".callout-info .callout-title")).to_have_text("Callouts")
            expect(md.locator("a.tag").first).to_have_text("#vaultx")
            expect(page.locator(".props")).to_contain_text("handleiding")
            secret = md.locator(".secret")
            expect(secret).not_to_have_class("secret revealed")
            secret.click()
            expect(secret).to_have_class("secret revealed")
            shot(page, "02-lezen")
            md.locator("input[data-task]").nth(1).check()
            saved(page)

            step("Wikilink volgen en backlinks zien")
            md.locator("a.wikilink[data-note]").first.click()
            expect(page.locator(".note-title")).to_have_value("Runbook Grafana")
            expect(page.locator(".backlinks").first).to_contain_text("Welkom")
            page.get_by_role("button", name="Naast elkaar").click()
            shot(page, "03-naast-elkaar-backlinks")

            step("Nieuwe notitie met [[-suggesties en een geneste tag")
            page.get_by_role("button", name="+ Nieuw").click()
            title = page.locator(".note-title")
            expect(title).to_be_focused()
            title.fill("Proxmox herstel")
            title.press("Enter")
            editor = page.locator("textarea.editor")
            editor.click()
            editor.press_sequentially("Zie [[runb")
            expect(page.locator(".suggest")).to_contain_text("Runbook Grafana")
            shot(page, "04-suggesties")
            page.keyboard.press("Enter")
            editor.press_sequentially(" en [[app:Graf")
            expect(page.locator(".suggest")).to_contain_text("app uit de catalogus")
            page.keyboard.press("Enter")
            editor.press_sequentially(" #infra/proxmox\n- [ ] back-up testen")
            page.keyboard.press("Enter")
            editor.press_sequentially("restore proberen")
            saved(page)
            value = editor.input_value()
            assert "Zie [[Runbook Grafana]] en [[app:Grafana]] #infra/proxmox" in value, value
            assert "- [ ] back-up testen\n- [ ] restore proberen" in value, value

            step("Hernoemen: links in andere notities gaan mee")
            open_note(page, "Runbook Grafana")
            page.locator(".note-title").fill("Grafana runbook")
            page.locator(".note-title").press("Enter")
            expect(page.locator(".toast")).to_contain_text("Links in 2 notities bijgewerkt")
            saved(page)
            welkom = body_of(page, "Welkom")
            assert (
                "[[Grafana runbook#Herstarten]]" in welkom and "[[Grafana runbook|het runbook]]" in welkom
            ), welkom
            assert "- [x] Je Obsidian-kluis importeren" in welkom, welkom
            assert "[[Grafana runbook]]" in body_of(page, "Proxmox herstel")

            step("Zoeken op tag en grafiek")
            page.locator(".notes-search").fill("#infra")
            results = page.locator(".results .result-title")
            expect(results).to_have_count(3)  # ook Welkom noemt #infra/proxmox
            page.locator(".notes-search").fill("")
            page.get_by_role("button", name="Grafiek", exact=True).click()
            expect(page.locator(".graph-page .graph-node")).to_have_count(3)
            page.get_by_label("Tags").check()
            shot(page, "05-grafiek")
            page.get_by_role("button", name="Grafiek", exact=True).click()

            step("Importeren: 2 Markdown-bestanden, png overgeslagen")
            page.locator("button[aria-label=Meer]").click()
            page.locator("input[accept='.md,.markdown,.txt']").set_input_files(
                [
                    str(importdir / "Wifi thuis.md"),
                    str(importdir / "Boodschappen.md"),
                    str(importdir / "foto.png"),
                ]
            )
            expect(page.locator(".modal-card")).to_contain_text("2 notities worden")
            page.locator(".modal-card").get_by_role("button", name="Importeren").click()
            expect(page.locator(".toast")).to_contain_text("2 notities geïmporteerd")
            expect(page.locator(".tree .tree-item")).to_have_count(6)  # map Runbooks + 5 notities

            step("Versiegeschiedenis")
            open_note(page, "Proxmox herstel")
            page.get_by_role("button", name="Geschiedenis").click()
            try:
                expect(page.locator(".history-list li")).to_have_count(1)
            except AssertionError:
                shot(page, "fout-geschiedenis")
                print("dialoog:", page.locator("dialog[open]").all_inner_texts())
                print(
                    "versies:",
                    page.request.get(f"{ui}/api/v1/me/notes").json()
                    and [
                        (
                            n["id"],
                            n["revision"],
                            len(page.request.get(f"{ui}/api/v1/me/notes/{n['id']}/versions").json()),
                        )
                        for n in page.request.get(f"{ui}/api/v1/me/notes").json()
                    ],
                )
                raise
            shot(page, "06-geschiedenis")
            page.keyboard.press("Escape")

            step("Prullenbak: verwijderen en terugzetten")
            open_note(page, "Boodschappen")
            page.get_by_role("button", name="Verwijderen").click()
            expect(page.locator(".toast")).to_contain_text("prullenbak")
            page.locator("button[aria-label=Meer]").click()
            page.get_by_role("button", name="Prullenbak (1)").click()
            expect(page.locator(".trash tbody tr")).to_have_count(1)
            page.locator(".trash").get_by_role("button", name="Terugzetten").click()
            expect(page.get_by_text("De prullenbak is leeg.")).to_be_visible()

            step("Server ziet enkel versleutelde tekst")
            notes = page.request.get(f"{ui}/api/v1/me/notes").json()
            assert len(notes) == 5 and all(n["content"].startswith("2.") for n in notes), notes
            dsn = DB.replace("postgresql+psycopg://", "postgresql://")
            with psycopg.connect(dsn) as conn:
                rows = conn.execute(
                    "SELECT content FROM vault_notes UNION ALL SELECT content FROM vault_note_versions"
                ).fetchall()
            assert rows
            for (content,) in rows:
                for plain in ("Proxmox", "Grafana", "geheim-wifi", "Welkom", "infra"):
                    assert plain not in content, f"klare tekst '{plain}' in de database"
            print(f"    {len(rows)} versleutelde blobs in de database, geen klare tekst")

            step("Vergrendelen en opnieuw ontgrendelen")
            page.get_by_role("button", name="Vergrendelen").click()
            expect(page.get_by_role("heading", name="Kluis ontgrendelen")).to_be_visible()
            page.locator("input[type=password]").fill(PASSWORD)
            page.get_by_role("button", name="Ontgrendelen").click()
            expect(page.locator(".tree .tree-item")).to_have_count(6, timeout=30_000)
            open_note(page, "Wifi thuis")
            page.get_by_role("button", name="Lezen").click()
            expect(page.locator(".preview .secret")).to_have_text("geheim-wifi")

            audit = page.request.get(f"{ui}/api/v1/audit", params={"action": "vault.", "limit": 100}).json()[
                "items"
            ]
            pairs = {(a["action"], a["outcome"]) for a in audit}
            assert ("vault.unlock", "failure") in pairs and ("vault.unlock", "success") in pairs, pairs
            assert ("vault.note.import", "success") in pairs and ("vault.note.trash", "success") in pairs, (
                pairs
            )
            assert not errors, errors
            browser.close()
        step("OK: notities werken end-to-end en de server ziet enkel versleutelde tekst")
    finally:
        for proc in (vite, backend):
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(importdir, ignore_errors=True)


if __name__ == "__main__":
    main()
