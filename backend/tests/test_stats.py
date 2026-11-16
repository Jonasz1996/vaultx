"""Lees-API voor dashboards: API-sleutels en /api/v1/stats (enkel tellingen, nooit inhoud)."""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.main import create_app
from app.services.oidc import OIDCProvider
from tests.test_vault import bearer, enroll, login_item


async def _key(c, name: str = "Dashboard", **extra) -> dict:
    r = await c.post_json("/api/v1/me/api-keys", {"name": name, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def test_api_key_lifecycle_and_scope(make_client, admin):
    c = await make_client()
    account = await enroll(c, "jonas")
    created = await _key(c, expires_in_days=30)
    token = created["token"]
    assert token.startswith("vxk_") and created["key"]["prefix"] == token[:12]
    assert created["key"]["expires_at"] is not None

    # De sleutel staat nergens in de lijst, enkel het begin ervan.
    r = await c.get("/api/v1/me/api-keys")
    assert r.status_code == 200 and token not in r.text and len(r.json()) == 1

    # Wat inhoud in de kluis, notities zijn niet nodig voor de telling.
    bw = await bearer(c, account)
    for name in ("Grafana", "Proxmox"):
        r = await c.post("/api/ciphers", json=login_item(account, name, "jonas", "geheim"), headers=bw)
        assert r.status_code == 200, r.text
    card = {**login_item(account, "Visa", "x", "y"), "type": 3, "login": None, "card": {}}
    r = await c.post("/api/ciphers", json=card, headers=bw)
    assert r.status_code == 200, r.text
    r = await c.post_json("/api/v1/links", {"url": "https://example.org", "tags": ["a"]})
    assert r.status_code == 201

    anon = await make_client()
    hdr = {"Authorization": f"Bearer {token}"}
    r = await anon.get("/api/v1/stats", headers=hdr)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    s = r.json()
    assert s["scope"] == "user" and "users" not in s
    assert s["vault"]["accounts"] == 1
    assert s["vault"]["items"]["total"] == 3
    assert s["vault"]["items"]["logins"] == 2 and s["vault"]["items"]["cards"] == 1
    assert s["vault"]["devices"]["total"] == 1
    assert s["vault"]["devices"]["by_type"] == {"Linux-CLI": 1}
    assert s["links"] == {**s["links"], "total": 1, "tags": 1}
    assert s["sessions"]["active"] >= 1
    assert s["activity_last_24h"]["vault_logins"] == 1
    # Geen inhoud: geen versleutelde blobs, geen URL's of namen.
    assert "Grafana" not in r.text and "example.org" not in r.text and account.email not in r.text

    # Laatst gebruikt wordt bijgehouden.
    r = await c.get("/api/v1/me/api-keys")
    assert r.json()[0]["last_used_at"] is not None

    # De sleutel werkt enkel op /api/v1/stats, niet op de rest van de API.
    for path in ("/api/v1/me", "/api/v1/links", "/api/v1/catalog", "/api/v1/me/api-keys", "/api/sync"):
        r = await anon.get(path, headers=hdr)
        assert r.status_code == 401, (path, r.status_code)
    r = await anon.post("/api/v1/me/api-keys", json={"name": "x"}, headers=hdr)
    assert r.status_code in (401, 403)

    # Een beheerder ziet heel VaultX, met de gebruikers erbij.
    akey = (await _key(admin, "Beheer"))["token"]
    r = await anon.get("/api/v1/stats", headers={"Authorization": f"Bearer {akey}"})
    s = r.json()
    assert s["scope"] == "instance"
    assert s["users"]["total"] == 2 and s["users"]["admins"] == 1
    assert s["vault"]["items"]["total"] == 3

    # Ook zonder sleutel bruikbaar vanuit de webinterface (sessie).
    r = await c.get("/api/v1/stats")
    assert r.status_code == 200 and r.json()["scope"] == "user"

    # Intrekken: daarna 401. Een andere gebruiker kan jouw sleutel niet intrekken.
    r = await admin.delete_(f"/api/v1/me/api-keys/{created['key']['id']}")
    assert r.status_code == 404
    r = await c.delete_(f"/api/v1/me/api-keys/{created['key']['id']}")
    assert r.status_code == 204
    r = await anon.get("/api/v1/stats", headers=hdr)
    assert r.status_code == 401
    r = await anon.get("/api/v1/stats", headers={"Authorization": "Bearer vxk_onzin"})
    assert r.status_code == 401
    r = await anon.get("/api/v1/stats")
    assert r.status_code == 401

    # Auditlog: aanmaken en intrekken staan erin, de sleutel zelf niet.
    r = await admin.get("/api/v1/audit", params={"limit": 100})
    assert r.status_code == 200, r.text
    actions = [e["action"] for e in r.json()["items"]]
    assert "api_key.create" in actions and "api_key.revoke" in actions
    assert token not in r.text


async def test_expired_key_and_deactivated_user(make_client, admin):
    c = await make_client()
    await c.login("carol")
    token = (await _key(c))["token"]
    from sqlalchemy import text

    from app.core.db import get_engine

    async with get_engine().begin() as conn:
        await conn.execute(text("UPDATE api_keys SET expires_at = now() - interval '1 minute'"))
    anon = await make_client()
    r = await anon.get("/api/v1/stats", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and "verlopen" in r.text

    token2 = (await _key(c, "nieuw"))["token"]
    me = (await c.get("/api/v1/me")).json()
    r = await admin.patch_json(f"/api/v1/users/{me['id']}", {"is_active": False})
    assert r.status_code == 200, r.text
    r = await anon.get("/api/v1/stats", headers={"Authorization": f"Bearer {token2}"})
    assert r.status_code == 401


async def test_cors_only_for_stats_and_configured_origin(monkeypatch):
    monkeypatch.setenv("VAULTX_DASHBOARD_ORIGINS", "https://dashboard.example.be/")
    get_settings.cache_clear()
    try:
        application = create_app()
        application.state.oidc = OIDCProvider(get_settings())
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            pre = {
                "Origin": "https://dashboard.example.be",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            }
            r = await c.options("/api/v1/stats", headers=pre)
            assert r.status_code == 200
            assert r.headers["access-control-allow-origin"] == "https://dashboard.example.be"
            assert "access-control-allow-credentials" not in r.headers

            r = await c.options("/api/v1/stats", headers={**pre, "Origin": "https://evil.example"})
            assert "access-control-allow-origin" not in r.headers

            # Andere paden krijgen nooit CORS-headers.
            r = await c.options("/api/v1/me", headers=pre)
            assert "access-control-allow-origin" not in r.headers
            r = await c.get("/api/v1/me", headers={"Origin": "https://dashboard.example.be"})
            assert "access-control-allow-origin" not in r.headers
        await application.state.oidc.aclose()
    finally:
        get_settings.cache_clear()
