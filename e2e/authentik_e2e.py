"""End-to-end test: VaultX + echte Authentik (docker/authentik) in een echte browser.

Vereist een draaiende stack op http://localhost:8080 (zie e2e/README.md) en
een lege VaultX-database. Schermafbeeldingen komen in de map uit argv[1].

    pip install playwright && playwright install chromium
    python e2e/authentik_e2e.py ./e2e-shots
"""

import asyncio
import os
import sys

from playwright.async_api import async_playwright

BASE = os.environ.get("VAULTX_URL", "http://localhost:8080")
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(OUT, exist_ok=True)


async def ak_login(page, user, pw):
    await page.wait_for_url(
        lambda u: "/if/flow/" in u or u.startswith(BASE + "/") and "/login" not in u,
        timeout=30000,
    )
    if "/if/flow/" not in page.url:
        return  # Authentik-sessie nog actief: meteen terug
    await page.locator("input[name=uidField]").fill(user)
    await page.get_by_role("button", name="Log in").click()
    await page.get_by_text("Not you?").wait_for()
    await page.wait_for_timeout(1000)
    await page.locator("input[name=password]").fill(pw)
    await page.get_by_role("button", name="Continue").click()
    try:
        await page.wait_for_url(lambda u: u.startswith(BASE), timeout=20000)
    except Exception:
        await page.screenshot(path=f"{OUT}/fail.png")
        print("FAIL at", page.url)
        raise


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_PATH") or None
        )
        ctx = await b.new_context(viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()
        errors = []
        page.on(
            "console", lambda m: errors.append(m.text) if m.type == "error" else None
        )
        await page.goto(f"{BASE}/organizations")
        await page.wait_for_url("**/login**")
        await page.screenshot(path=f"{OUT}/01-login.png")
        await page.get_by_text("Inloggen met Authentik").click()
        await ak_login(page, "alice", "alice-dev-password")
        await page.wait_for_selector("h1:has-text('Organisaties')")
        print("alice landed on", page.url)
        # organisatie demo aanmaken
        await page.get_by_role("button", name="Nieuwe organisatie").click()
        await page.locator("dialog input").first.fill("Demo")
        await page.get_by_role("button", name="Aanmaken").click()
        await page.wait_for_url("**/organizations/*", wait_until="commit")
        await page.get_by_role("button", name="Nieuw team").click()
        await page.locator("dialog[open] input").first.fill("Ops")
        await page.get_by_role("button", name="Aanmaken").click()
        await page.wait_for_url("**/teams/*", wait_until="commit")
        print("team created", page.url)
        await page.goto(f"{BASE}/")
        await page.wait_for_selector("text=Recente gebeurtenissen")
        await page.screenshot(path=f"{OUT}/02-dashboard-alice.png")
        # alice uitloggen
        await page.get_by_role("button", name="Uitloggen").click()
        await page.wait_for_url("**/login?logged_out=1**", timeout=30000)
        print("alice logout ->", page.url)

        # bob in nieuwe context
        ctx2 = await b.new_context(viewport={"width": 1400, "height": 900})
        page2 = await ctx2.new_page()
        await page2.goto(f"{BASE}/")
        await page2.get_by_text("Inloggen met Authentik").click()
        await ak_login(page2, "bob", "bob-dev-password")
        await page2.wait_for_selector("text=Mijn lidmaatschappen")
        await page2.screenshot(path=f"{OUT}/03-dashboard-bob.png")
        me = await page2.evaluate("fetch('/api/v1/me').then(r=>r.json())")
        got = sorted(
            (m["organization_slug"], m["team_slug"] or "", m["role"], m["source"])
            for m in me["memberships"]
        )
        print("bob memberships:", got, "admin:", me["is_admin"])
        assert got == [
            ("demo", "", "member", "idp"),
            ("demo", "ops", "member", "idp"),
        ], got
        assert me["is_admin"] is False

        # alice opnieuw, bekijk pagina's
        await page.goto(f"{BASE}/")
        await page.get_by_text("Inloggen met Authentik").click()
        await ak_login(page, "alice", "alice-dev-password")
        for path, name in [
            ("/users", "04-users"),
            ("/organizations", "05-orgs"),
            ("/teams", "07-teams"),
            ("/audit", "08-audit"),
        ]:
            await page.goto(BASE + path)
            await page.wait_for_load_state("networkidle")
            await page.screenshot(path=f"{OUT}/{name}.png", full_page=True)
        await page.goto(f"{BASE}/organizations")
        await page.get_by_role("link", name="Demo").click()
        await page.wait_for_selector("text=Keten controleren")
        await page.get_by_role("button", name="Keten controleren").click()
        await page.wait_for_selector("text=keten intact")
        await page.screenshot(path=f"{OUT}/06-org-detail.png", full_page=True)
        await page.goto(f"{BASE}/audit")
        await page.get_by_role("button", name="Keten controleren").click()
        await page.wait_for_selector("text=regels gecontroleerd")
        await page.screenshot(path=f"{OUT}/08-audit.png", full_page=True)
        # bob uitloggen -> backchannel test daarna via audit
        await page2.get_by_role("button", name="Uitloggen").click()
        await page2.wait_for_url("**/login?logged_out=1**", timeout=30000)
        await asyncio.sleep(4)
        audit = await page.evaluate(
            "fetch('/api/v1/audit?limit=100').then(r=>r.json())"
        )
        print(
            "actions:",
            sorted({e["action"] + ":" + e["outcome"] for e in audit["items"]}),
        )
        unexpected = [e for e in errors if "401" not in e]
        print("console errors:", unexpected)
        assert not unexpected, unexpected
        assert any(e["action"] == "auth.backchannel_logout" for e in audit["items"]), (
            "geen back-channel logout"
        )
        await b.close()


asyncio.run(main())
