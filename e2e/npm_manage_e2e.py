"""End-to-end test: NPM volledig beheren vanuit VaultX, tegen een echte Nginx Proxy Manager.

Start een kleine app (achter NPM), een nep-OIDC-provider en de VaultX-backend,
en doet dan alles via de VaultX-API (zoals het scherm "NPM beheren"):

1. Proxy host aanmaken, bewerken (Advanced met CSS-thema en headers), uit- en
   aanzetten. Een bezoeker krijgt telkens wat verwacht wordt.
2. Beschermde Advanced-regels: sub_filter weghalen wordt geweigerd zonder
   bevestiging, en lukt met bevestiging.
3. Een nginx-fout en een upstream die niet antwoordt: VaultX zet terug.
4. Eigen certificaat opladen en gebruiken; verwijderen terwijl het in gebruik
   is, wordt geweigerd. Let's Encrypt voor een .test-domein faalt netjes.
5. Access list met gebruiker: de host vraagt een login; verwijderen terwijl in
   gebruik wordt geweigerd (NPM zou de host stil openzetten).
6. Redirection host (301), 404-host en stream (dubbele poort geweigerd).
7. Alles weer verwijderen via VaultX; het journaal toont elke stap.

Met --ui ook het scherm in een browser (Playwright, vite preview van de gebouwde
frontend), met --screenshots DIR de schermafdrukken.

Raakt enkel objecten onder .vaultx-manage-e2e.test aan (en streams op --stream-port).

    cd backend && python ../e2e/npm_manage_e2e.py http://localhost:81 admin@example.com wachtwoord \\
        --proxy 127.0.0.1 --http-port 8080 --https-port 8443
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "e2e"))

from bitwarden_e2e import free_port, login, step  # noqa: E402
from npm_write_e2e import self_signed  # noqa: E402

from tests.fake_oidc import FakeOIDCProvider  # noqa: E402

DB = os.environ.get("VAULTX_DATABASE_URL", "postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_test")
SUFFIX = ".vaultx-manage-e2e.test"
PREFIX = "VaultX manage-e2e"
CSRF = {"X-VaultX-CSRF": "1"}
THEME = (
    "location / {\n"
    "    proxy_pass $forward_scheme://$server:$port;\n"
    '    proxy_set_header Accept-Encoding "";\n'
    "    sub_filter '</head>' '<link rel=\"stylesheet\" href=\"https://css.jbogaert.be/app.css\"></head>';\n"
    "    sub_filter_once on;\n"
    '    add_header X-Frame-Options "SAMEORIGIN" always;\n'
    '    add_header X-Content-Type-Options "nosniff" always;\n'
    "}\n"
)


class App(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # stil
        pass

    def do_GET(self) -> None:  # noqa: N802
        body = b"<html><head><title>app</title></head><body>app</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def visit(
    proxy: str,
    port: int,
    domain: str,
    *,
    https: bool = False,
    want: int | None = None,
    contains: str | None = None,
) -> httpx.Response:
    """GET zoals een browser; opnieuw tot status en inhoud kloppen (nginx herlaadt asynchroon, en de
    standaardpagina van NPM geeft ook 200)."""
    scheme = "https" if https else "http"
    r = None
    for _ in range(20):
        with httpx.Client(verify=False, trust_env=False, follow_redirects=False, timeout=10) as c:  # noqa: S501
            r = c.get(
                f"{scheme}://{proxy}:{port}/",
                headers={"Host": domain},
                extensions={"sni_hostname": domain} if https else {},
            )
        if (want is None or r.status_code == want) and (contains is None or contains in r.text):
            return r
        time.sleep(0.5)
    assert r is not None
    return r


def cleanup(npm: httpx.Client, stream_port: int) -> None:
    for kind in ("proxy-hosts", "redirection-hosts", "dead-hosts"):
        for h in npm.get(f"/nginx/{kind}").raise_for_status().json():
            if any(d.endswith(SUFFIX) for d in h["domain_names"]):
                npm.delete(f"/nginx/{kind}/{h['id']}").raise_for_status()
    for s in npm.get("/nginx/streams").raise_for_status().json():
        if s["incoming_port"] == stream_port:
            npm.delete(f"/nginx/streams/{s['id']}").raise_for_status()
    for a in npm.get("/nginx/access-lists").raise_for_status().json():
        if a["name"].startswith(PREFIX):
            npm.delete(f"/nginx/access-lists/{a['id']}").raise_for_status()
    for c in npm.get("/nginx/certificates").raise_for_status().json():
        if (c.get("nice_name") or "").startswith(PREFIX):
            npm.delete(f"/nginx/certificates/{c['id']}").raise_for_status()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("npm_url")
    ap.add_argument("identity")
    ap.add_argument("secret")
    ap.add_argument("--proxy", default="127.0.0.1", help="adres waarop NPM poort 80/443 aanbiedt")
    ap.add_argument("--http-port", type=int, default=80)
    ap.add_argument("--https-port", type=int, default=443)
    ap.add_argument("--docker-host", default="172.17.0.1", help="hoe de NPM-container deze machine bereikt")
    ap.add_argument("--stream-port", type=int, default=25022)
    ap.add_argument("--ui", action="store_true", help="ook het scherm testen in een browser")
    ap.add_argument("--screenshots", type=Path)
    args = ap.parse_args()

    app_port = free_port()
    server = ThreadingHTTPServer(("0.0.0.0", app_port), App)  # noqa: S104
    threading.Thread(target=server.serve_forever, daemon=True).start()
    dead_port = free_port()  # hier luistert niets: nginx geeft 502

    npm = httpx.Client(base_url=f"{args.npm_url.rstrip('/')}/api", timeout=60, trust_env=False)
    token = npm.post("/tokens", json={"identity": args.identity, "secret": args.secret}).raise_for_status()
    npm.headers["Authorization"] = f"Bearer {token.json()['token']}"
    step("NPM opruimen (vorige e2e-objecten)")
    cleanup(npm, args.stream_port)

    fake = FakeOIDCProvider()
    fake.start()
    api_port = free_port()
    api = f"http://127.0.0.1:{api_port}"
    ui_port = free_port()
    ui = f"http://127.0.0.1:{ui_port}"
    env = {
        **os.environ,
        "VAULTX_DATABASE_URL": DB,
        "VAULTX_SECRET_KEY": "e2e-secret-key-e2e-secret-key-0123456789",
        "VAULTX_PUBLIC_URL": ui if args.ui else api,
        "VAULTX_OIDC_ISSUER": fake.issuer,
        "VAULTX_OIDC_CLIENT_ID": fake.client_id,
        "VAULTX_OIDC_CLIENT_SECRET": fake.client_secret,
        "VAULTX_OIDC_ADMIN_GROUPS": "vaultx-admins",
        "VAULTX_COOKIE_SECURE": "false",
        "VAULTX_NPM_SYNC_INTERVAL_MINUTES": "0",
        "VAULTX_LOG_LEVEL": "WARNING",
    }
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True)
    backend = subprocess.Popen(  # noqa: S603 - vaste argumenten
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(api_port), "--log-level", "warning"],
        cwd=BACKEND,
        env=env,
    )
    vite = None
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{api}/health", trust_env=False).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.2)
        else:
            raise SystemExit("backend start niet")

        step("VaultX: inloggen, organisatie en NPM-koppeling met schrijfrechten")
        admin = httpx.Client(base_url=api, trust_env=False, timeout=360, headers=CSRF)
        login(admin, fake, "admin-manage-e2e", "admin-manage-e2e@example.com", ["vaultx-admins"])
        slug = f"manage-e2e-{int(time.time())}"
        org = admin.post("/api/v1/organizations", json={"slug": slug, "name": "NPM beheren e2e"})
        org_id = org.raise_for_status().json()["id"]
        conn = admin.post(
            f"/api/v1/organizations/{org_id}/npm-connections",
            json={
                "name": "Thuis-NPM",
                "base_url": args.npm_url,
                "identity": args.identity,
                "secret": args.secret,
                "write_enabled": True,
                "probe_host": args.proxy,
                "probe_http_port": args.http_port,
                "probe_https_port": args.https_port,
            },
        ).raise_for_status()
        conn_id = conn.json()["id"]
        cbase = f"/api/v1/organizations/{org_id}/npm-connections/{conn_id}"
        mbase = f"{cbase}/manage"

        def preview(
            kind: str, action: str, npm_id: int | None = None, data: dict | None = None, **extra
        ) -> dict:
            body = {"action": action, "npm_id": npm_id, "data": data or {}, **extra}
            r = admin.post(f"{mbase}/{kind}/preview", json=body)
            assert r.status_code == 200, r.text
            return r.json()

        def do(kind: str, action: str, npm_id: int | None = None, data: dict | None = None, **extra) -> dict:
            plan = preview(kind, action, npm_id, data, **extra)
            body = {"action": action, "npm_id": npm_id, "data": data or {}, **extra}
            r = admin.post(
                f"{mbase}/{kind}/apply", json={**body, "expected_modified_on": plan["modified_on"]}
            )
            assert r.status_code == 200, r.text
            return r.json()

        def blocks(plan: dict) -> set[str]:
            return {c["code"] for c in plan["checks"] if c["level"] == "block"}

        app_domain = f"app{SUFFIX}"
        step("Proxy host aanmaken via VaultX")
        ch = do(
            "proxy_host",
            "create",
            data={"domain_names": [app_domain], "forward_host": args.docker_host, "forward_port": app_port},
        )
        assert ch["status"] == "applied", ch
        assert ch["probe_after"]["status"] == 200, ch
        app_id = ch["npm_id"]
        assert (
            visit(args.proxy, args.http_port, app_domain, want=200, contains="<title>app").status_code == 200
        )
        hosts = admin.get(f"{cbase}/hosts").raise_for_status().json()
        assert any(h["npm_id"] == app_id for h in hosts), "nieuwe host niet in de hostlijst"

        step("Advanced met CSS-thema en headers zetten; de bezoeker krijgt het thema")
        ch = do("proxy_host", "update", app_id, {"advanced_config": THEME})
        assert ch["status"] == "applied", ch
        r = visit(args.proxy, args.http_port, app_domain, want=200, contains="css.jbogaert.be")
        assert "css.jbogaert.be/app.css" in r.text and r.headers.get("x-frame-options") == "SAMEORIGIN", (
            r.text
        )

        step("sub_filter weghalen: geweigerd zonder bevestiging, gelukt met bevestiging")
        no_theme = THEME.replace(
            "    sub_filter '</head>' '<link rel=\"stylesheet\" href=\"https://css.jbogaert.be/app.css\"></head>';\n",
            "",
        )
        plan = preview("proxy_host", "update", app_id, {"advanced_config": no_theme})
        assert "removes_sub_filter" in blocks(plan), plan
        r = admin.post(
            f"{mbase}/proxy_host/apply",
            json={"action": "update", "npm_id": app_id, "data": {"advanced_config": no_theme}},
        )
        assert r.json()["status"] == "refused", r.text
        assert (
            "css.jbogaert.be"
            in visit(args.proxy, args.http_port, app_domain, want=200, contains="<title>app").text
        )
        ch = do("proxy_host", "update", app_id, {"advanced_config": no_theme}, confirm_removals=True)
        assert ch["status"] == "applied", ch
        do("proxy_host", "update", app_id, {"advanced_config": THEME})

        step("nginx-fout in Advanced: NPM zet de host offline, VaultX zet terug")
        ch = do("proxy_host", "update", app_id, {"advanced_config": THEME + "bad_directive_vaultx on;\n"})
        assert ch["status"] == "rolled_back" and ch["nginx_error"], ch
        r = visit(args.proxy, args.http_port, app_domain, want=200, contains="css.jbogaert.be")
        assert r.status_code == 200 and "css.jbogaert.be" in r.text, (r.status_code, r.text[:300], ch)

        step("Upstream die niet antwoordt (502): VaultX zet terug")
        ch = do("proxy_host", "update", app_id, {"forward_port": dead_port})
        assert ch["status"] == "rolled_back" and "502" in ch["message"], ch
        assert (
            visit(args.proxy, args.http_port, app_domain, want=200, contains="<title>app").status_code == 200
        )

        step("Uit- en weer aanzetten")
        assert do("proxy_host", "disable", app_id)["status"] == "applied"
        assert "<title>app" not in visit(args.proxy, args.http_port, app_domain, contains="Default Site").text
        ch = do("proxy_host", "enable", app_id)
        assert ch["status"] == "applied" and ch["probe_after"]["status"] == 200, ch

        step("Eigen certificaat opladen en gebruiken op een HTTPS-host")
        secure = f"secure{SUFFIX}"
        pem, key = self_signed(secure)
        cert_data = {
            "provider": "other",
            "nice_name": f"{PREFIX} secure",
            "certificate": pem.decode(),
            "certificate_key": key.decode(),
        }
        plan = preview("certificate", "create", data=cert_data)
        assert plan["can_apply"] and plan["certificate_check"]["cn"] == secure, plan
        ch = do("certificate", "create", data=cert_data)
        assert ch["status"] == "applied", ch
        cert_id = ch["npm_id"]
        assert "PRIVATE KEY" not in str(ch)
        ch = do(
            "proxy_host",
            "create",
            data={
                "domain_names": [secure],
                "forward_host": args.docker_host,
                "forward_port": app_port,
                "certificate_id": cert_id,
                "ssl_forced": True,
                "http2_support": True,
            },
        )
        assert ch["status"] == "applied" and ch["probe_after"]["status"] == 200, ch
        secure_id = ch["npm_id"]
        assert (
            visit(
                args.proxy, args.https_port, secure, https=True, want=200, contains="<title>app"
            ).status_code
            == 200
        )
        plan = preview("certificate", "delete", cert_id)
        assert "certificate_in_use" in blocks(plan), plan

        step("Let's Encrypt voor een .test-domein faalt netjes (niets achtergelaten)")
        le_before = len(npm.get("/nginx/certificates").json())
        ch = do("certificate", "create", data={"provider": "letsencrypt", "domain_names": [f"le{SUFFIX}"]})
        assert ch["status"] == "refused", ch
        assert len(npm.get("/nginx/certificates").json()) == le_before

        step("Access list met gebruiker: de host vraagt een login; verwijderen in gebruik geweigerd")
        ch = do(
            "access_list",
            "create",
            data={"name": f"{PREFIX} login", "items": [{"username": "jonas", "password": "geheim-123"}]},
        )
        assert ch["status"] == "applied", ch
        acl_id = ch["npm_id"]
        # Een eigen "location /" in Advanced vervangt die van NPM, en daarmee de access list: VaultX waarschuwt.
        plan = preview("proxy_host", "update", app_id, {"access_list_id": acl_id})
        assert "access_list_ignored" in {c["code"] for c in plan["checks"]}, plan
        ch = do("proxy_host", "update", app_id, {"advanced_config": ""}, confirm_removals=True)
        assert ch["status"] == "applied", ch
        assert do("proxy_host", "update", app_id, {"access_list_id": acl_id})["status"] == "applied"
        assert visit(args.proxy, args.http_port, app_domain, want=401).status_code == 401
        plan = preview("access_list", "delete", acl_id)
        assert "access_list_in_use" in blocks(plan), plan
        ch = do(
            "access_list",
            "update",
            acl_id,
            {"items": [{"username": "jonas"}, {"username": "eve", "password": "x-geheim-2"}]},
        )
        assert ch["status"] == "applied" and "x-geheim-2" not in str(ch), ch
        with httpx.Client(trust_env=False, timeout=10) as c:
            r = c.get(
                f"http://{args.proxy}:{args.http_port}/",
                headers={"Host": app_domain},
                auth=("jonas", "geheim-123"),
            )
            assert r.status_code == 200, "bestaand wachtwoord bleef niet behouden"
        assert do("proxy_host", "update", app_id, {"access_list_id": 0})["status"] == "applied"
        assert do("proxy_host", "update", app_id, {"advanced_config": THEME})["status"] == "applied"

        step("Redirection host (301), 404-host en stream")
        old = f"oud{SUFFIX}"
        ch = do(
            "redirection_host",
            "create",
            data={"domain_names": [old], "forward_domain_name": app_domain, "forward_http_code": 301},
        )
        assert ch["status"] == "applied" and ch["probe_after"]["status"] == 301, ch
        redirect_id = ch["npm_id"]
        ch = do("dead_host", "create", data={"domain_names": [f"weg{SUFFIX}"]})
        assert ch["status"] == "applied" and ch["probe_after"]["status"] == 404, ch
        dead_id = ch["npm_id"]
        sdata = {
            "incoming_port": args.stream_port,
            "forwarding_host": args.docker_host,
            "forwarding_port": app_port,
        }
        ch = do("stream", "create", data=sdata)
        assert ch["status"] == "applied", ch
        stream_id = ch["npm_id"]
        assert "port_in_use" in blocks(preview("stream", "create", data=sdata))
        plan = preview(
            "proxy_host", "create", data={"domain_names": [old], "forward_host": "x", "forward_port": 1}
        )
        assert "domain_in_use" in blocks(plan)

        overview = admin.get(mbase).raise_for_status().json()
        assert any(s["id"] == stream_id and s["nginx_online"] for s in overview["stream"]), overview["stream"]

        if args.ui:
            vite = subprocess.Popen(  # noqa: S603
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
            ui_check(ui, fake, f"/npm/{org_id}/{conn_id}/beheer", app_id, args.screenshots)

        step("Alles weer verwijderen via VaultX")
        for kind, i in (
            ("proxy_host", app_id),
            ("proxy_host", secure_id),
            ("redirection_host", redirect_id),
            ("dead_host", dead_id),
            ("stream", stream_id),
            ("access_list", acl_id),
            ("certificate", cert_id),
        ):
            ch = do(kind, "delete", i)
            assert ch["status"] == "applied", ch
        journal = admin.get(f"{mbase}/changes").raise_for_status().json()
        statuses = {c["status"] for c in journal}
        assert {"applied", "rolled_back", "refused"} <= statuses, statuses
        assert "<title>app" not in visit(args.proxy, args.http_port, app_domain, contains="Default Site").text
        print(f"\nOK: NPM beheren werkt tegen echte NPM ({len(journal)} journaalregels)")
    finally:
        if vite is not None:
            vite.terminate()
        backend.terminate()
        backend.wait(10)
        fake.stop()
        server.shutdown()


def ui_check(ui: str, fake: FakeOIDCProvider, path: str, app_id: int, shots: Path | None) -> None:
    from playwright.sync_api import expect, sync_playwright

    for _ in range(100):
        try:
            if httpx.get(ui, trust_env=False).status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.2)
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    def shot(page, name: str) -> None:
        if shots:
            page.screenshot(path=str(shots / f"{name}.png"), full_page=False)

    step("Scherm 'NPM beheren' in de browser")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 950}, locale="nl-BE", device_scale_factor=2 if shots else 1
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        fake.next_claims = {
            "sub": "admin-manage-e2e",
            "email": "admin-manage-e2e@example.com",
            "email_verified": True,
            "preferred_username": "admin-manage-e2e",
            "name": "Jonas",
            "groups": ["vaultx-admins"],
        }
        page.goto(f"{ui}/auth/login?next={path}")
        expect(page.get_by_role("heading", name="NPM beheren: Thuis-NPM")).to_be_visible(timeout=20000)
        expect(page.get_by_text(f"app{SUFFIX}").first).to_be_visible()
        shot(page, "01-proxy-hosts")

        row = page.locator("tr", has_text=f"app{SUFFIX}")
        row.get_by_role("button", name="Bewerken").click()
        dialog = page.locator("dialog[open]")
        expect(dialog.get_by_text("Advanced (eigen nginx-config)")).to_be_visible()
        shot(page, "02-bewerken")
        area = dialog.locator("textarea").last
        area.fill(
            area.input_value()
            .replace("    sub_filter_once on;\n", "")
            .replace(
                "    sub_filter '</head>' '<link rel=\"stylesheet\" href=\"https://css.jbogaert.be/app.css\"></head>';\n",
                "",
            )
        )
        dialog.get_by_role("button", name="Voorbeeld bekijken").click()
        expect(dialog.get_by_text("het CSS-thema (sub_filter)")).to_be_visible(timeout=20000)
        expect(dialog.get_by_role("button", name="Opslaan")).to_be_disabled()
        shot(page, "03-thema-beschermd")
        dialog.get_by_role("button", name="Annuleren").click()

        row.get_by_role("button", name="Bewerken").click()
        dialog = page.locator("dialog[open]")
        port = dialog.get_by_label("Poort", exact=True)
        port.fill("9999")
        dialog.get_by_role("button", name="Voorbeeld bekijken").click()
        expect(dialog.get_by_text("Wat VaultX in NPM doet")).to_be_visible(timeout=20000)
        shot(page, "04-voorbeeld")
        dialog.get_by_role("button", name="Opslaan").click()
        expect(dialog.get_by_text("Teruggezet")).to_be_visible(timeout=60000)
        shot(page, "05-teruggezet")
        dialog.get_by_role("button", name="Sluiten", exact=True).locator("visible=true").last.click()

        for tab, name in (
            ("Certificaten", "06-certificaten"),
            ("Access lists", "07-access-lists"),
            ("Redirects", "08-redirects"),
            ("Streams", "09-streams"),
        ):
            page.locator(".manage-tabs").get_by_text(tab).click()
            page.wait_for_timeout(300)
            shot(page, name)
        page.locator(".manage-tabs").get_by_text("Certificaten").click()
        page.get_by_role("button", name="Nieuw certificaat").click()
        dialog = page.locator("dialog[open]")
        expect(dialog.get_by_text("Let's Encrypt aanvragen")).to_be_attached()
        shot(page, "10-certificaat-aanvragen")
        dialog.get_by_role("button", name="Annuleren").click()
        page.locator(".manage-tabs").get_by_text("Journaal").click()
        expect(page.get_by_text("Teruggezet").first).to_be_visible()
        shot(page, "11-journaal")
        assert not errors, errors
        browser.close()
    _ = app_id


if __name__ == "__main__":
    main()
