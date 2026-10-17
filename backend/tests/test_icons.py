"""Fase 9: websitepictogrammen, inclusief de bescherming tegen SSRF."""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.services import icons
from app.services.icons import Icon, IconCache, icon_candidates, normalize_domain, sniff_image

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


def test_normalize_and_sniff():
    assert normalize_domain("Grafana.Example.BE.") == "grafana.example.be"
    assert normalize_domain("bücher.de") == "xn--bcher-kva.de"
    for bad in ("127.0.0.1", "localhost", "a..b", "x.com:8080", "x.com/../y", "[::1]"):
        assert normalize_domain(bad) is None, bad
    assert sniff_image(PNG) == "image/png"
    assert sniff_image(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is None
    assert sniff_image(b"<html>") is None


def test_icon_candidates_prefers_sized_png_and_skips_svg():
    html = """<link rel="icon" href="/a.svg" type="image/svg+xml">
    <link rel="shortcut icon" href="/fav.ico"><link rel="icon" sizes="32x32" href="icon-32.png">
    <link rel="stylesheet" href="x.css">"""
    urls = icon_candidates(html, "https://grafana.example.be/login")
    assert urls == [
        "https://grafana.example.be/icon-32.png",
        "https://grafana.example.be/fav.ico",
        "https://grafana.example.be/favicon.ico",
    ]


class _Site(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/":
            self.send_response(302)
            self.send_header("Location", "/home")
            self.end_headers()
        elif self.path == "/home":
            body = b'<html><link rel="icon" href="/i.png"></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/i.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def local_site(monkeypatch):
    """Een 'website' op 127.0.0.1; elke domeinnaam wordt naar dat adres opgelost."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site_port = server.server_address[1]

    async def fake_resolve(host: str, p: int) -> list[str]:
        return ["127.0.0.1"]

    monkeypatch.setattr(icons, "resolve", fake_resolve)
    # De site luistert op een willekeurige poort: zet http://<domein>/ om naar die poort.
    orig = icons._PinnedBackend.connect_tcp

    async def connect(self, host, port, **kw):
        return await orig(self, host, site_port if port == 80 else 1, **kw)

    monkeypatch.setattr(icons._PinnedBackend, "connect_tcp", connect)
    yield
    server.shutdown()


async def test_private_address_only_for_known_domains(local_site):
    assert await icons.fetch_icon("evil.example.com", lambda host: False) is None
    got = await icons.fetch_icon("grafana.lab.be", lambda host: host == "grafana.lab.be")
    assert got == Icon(PNG, "image/png")


async def test_cache_deduplicates_and_remembers_misses():
    calls: list[str] = []

    async def fetcher(domain, allow):
        calls.append(domain)
        await asyncio.sleep(0.01)
        return Icon(PNG, "image/png") if domain == "ok.example" else None

    cache = IconCache(fetcher)
    results = await asyncio.gather(*(cache.get("ok.example", lambda h: False) for _ in range(5)))
    assert all(r and r.data == PNG for r in results)
    assert await cache.get("none.example", lambda h: False) is None
    assert await cache.get("none.example", lambda h: False) is None
    assert calls == ["ok.example", "none.example"]


async def test_icon_route(app, make_client, admin):
    seen: dict[str, bool] = {}

    async def fetcher(domain, allow):
        seen[domain] = allow(domain)
        return Icon(PNG, "image/png") if domain == "grafana.example.com" else None

    app.state.icon_fetcher = fetcher
    c = await make_client()
    r = await c.get("/icons/grafana.example.com/icon.png")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert "max-age" in r.headers["cache-control"]
    assert "sandbox" in r.headers["content-security-policy"]
    assert seen == {"grafana.example.com": False}
    assert (await c.get("/icons/nothing.example.com/icon.png")).status_code == 404
    assert (await c.get("/icons/127.0.0.1/icon.png")).status_code == 404
    assert "127.0.0.1" not in seen


async def test_catalog_domains_may_be_private(app, admin):
    seen: dict[str, bool] = {}

    async def fetcher(domain, allow):
        seen[domain] = allow(domain)
        return None

    app.state.icon_fetcher = fetcher
    org = (await admin.post_json("/api/v1/organizations", {"slug": "acme", "name": "Acme"})).json()
    r = await admin.post_json(
        f"/api/v1/organizations/{org['id']}/applications",
        {"name": "Proxmox", "url": "https://pve.lab.be:8006"},
    )
    assert r.status_code == 201, r.text
    await admin.get("/icons/pve.lab.be/icon.png")
    await admin.get("/icons/other.lab.be/icon.png")
    assert seen == {"pve.lab.be": True, "other.lab.be": False}
