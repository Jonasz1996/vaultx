"""Meteen ingelogd via Authentik: stil proberen (prompt=none) en automatisch doorsturen."""

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.config import Settings, get_settings


@pytest.fixture
def not_logged_in_at_idp(fake_oidc):
    fake_oidc.logged_in = False
    yield
    fake_oidc.logged_in = None


async def _follow_to_idp(c, url: str) -> httpx.Response:
    r = await c.get(url)
    assert r.status_code == 303, r.text
    async with httpx.AsyncClient() as idp:
        r2 = await idp.get(r.headers["location"])
    assert r2.status_code == 302
    cb = urlsplit(r2.headers["location"])
    return await c.get(f"{cb.path}?{cb.query}")


async def test_methods_tell_the_login_page_what_to_do(make_client, monkeypatch):
    c = await make_client()
    m = (await c.get("/auth/methods")).json()
    # Gewone login staat aan: stil proberen, maar niet doorsturen naar het aanmeldscherm.
    assert m == {"authentik": True, "password": True, "silent": True, "auto_redirect": False}
    monkeypatch.setattr(get_settings(), "local_login_enabled", False)
    m = (await c.get("/auth/methods")).json()
    assert m["silent"] and m["auto_redirect"] and not m["password"]
    monkeypatch.setattr(get_settings(), "oidc_auto_redirect", False)
    assert (await c.get("/auth/methods")).json()["auto_redirect"] is False
    monkeypatch.setattr(get_settings(), "oidc_silent_login", False)
    assert (await c.get("/auth/methods")).json()["silent"] is False


async def test_methods_on_unknown_address_never_redirect(make_client):
    c = await make_client("http://192.168.10.50")
    m = (await c.get("/auth/methods")).json()
    assert m["authentik"] is False and m["silent"] is False and m["auto_redirect"] is False


def test_auto_redirect_default_follows_local_login():
    base = {
        "secret_key": "x" * 32,
        "oidc_issuer": "https://a/",
        "oidc_client_id": "c",
        "oidc_client_secret": "s",
    }
    assert Settings(**base, local_login_enabled=False).oidc_auto_redirect_enabled is True
    assert Settings(**base, local_login_enabled=True).oidc_auto_redirect_enabled is False
    assert Settings(**base, local_login_enabled=True, oidc_auto_redirect=True).oidc_auto_redirect_enabled


@pytest.fixture
def logged_in_at_idp(fake_oidc):
    fake_oidc.logged_in = True
    yield
    fake_oidc.logged_in = None


async def test_silent_login_with_idp_session_logs_in_without_a_click(
    make_client, fake_oidc, logged_in_at_idp
):
    c = await make_client()
    fake_oidc.next_claims = {"sub": "jonas", "email": "jonas@example.com", "preferred_username": "jonas"}
    r = await c.get("/auth/login", params={"silent": "1", "next": "/vault"})
    assert parse_qs(urlsplit(r.headers["location"]).query)["prompt"] == ["none"]
    async with httpx.AsyncClient() as idp:
        r2 = await idp.get(r.headers["location"])
    cb = urlsplit(r2.headers["location"])
    r3 = await c.get(f"{cb.path}?{cb.query}")
    assert r3.status_code == 303 and r3.headers["location"] == "/vault"
    assert (await c.get("/api/v1/me")).json()["email"] == "jonas@example.com"


async def test_silent_login_without_idp_session_shows_login_page(make_client, not_logged_in_at_idp, admin):
    c = await make_client()
    r = await _follow_to_idp(c, "/auth/login?silent=1&next=/vault")
    # Gewone login aan: de loginpagina, met een markering zodat ze niet opnieuw stil probeert.
    assert r.headers["location"] == "/login?stil=1&next=%2Fvault"
    assert (await c.get("/api/v1/me")).status_code == 401
    # Geen mislukte login in de auditlog: dit is gewoon "nog niet aangemeld".
    events = (await admin.get("/api/v1/audit", params={"instance_only": True})).json()["items"]
    assert not [e for e in events if e["action"] == "auth.login" and e["outcome"] == "failure"]


async def test_silent_login_without_idp_session_goes_to_real_login_when_auto_redirect(
    make_client, not_logged_in_at_idp, monkeypatch, fake_oidc
):
    monkeypatch.setattr(get_settings(), "local_login_enabled", False)
    c = await make_client()
    r = await _follow_to_idp(c, "/auth/login?silent=1&next=/notes")
    assert r.headers["location"] == "/auth/login?next=%2Fnotes"
    # Het echte aanmelden: zonder prompt=none.
    r = await c.get(r.headers["location"])
    assert "prompt" not in parse_qs(urlsplit(r.headers["location"]).query)


async def test_silent_flag_ignored_when_silent_login_off(make_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "oidc_silent_login", False)
    c = await make_client()
    r = await c.get("/auth/login", params={"silent": "1"})
    assert "prompt" not in parse_qs(urlsplit(r.headers["location"]).query)


async def test_other_error_on_silent_attempt_is_not_a_loop(make_client):
    c = await make_client()
    await c.get("/auth/login", params={"silent": "1"})
    r = await c.get("/auth/callback", params={"error": "invalid_request", "state": "x"})
    assert r.headers["location"] == "/login?stil=1"


async def test_normal_login_error_is_still_an_error(make_client):
    c = await make_client()
    await c.get("/auth/login")
    r = await c.get("/auth/callback", params={"error": "login_required", "state": "x"})
    assert "error=idp_error" in r.headers["location"]


async def test_silent_only_never_shows_the_idp_login(make_client, not_logged_in_at_idp, monkeypatch):
    # VaultX Invuller opent een tabblad op de achtergrond: nooit het aanmeldscherm, ook niet met
    # automatisch doorsturen, en ook als de stille login voor de webpagina uit staat.
    monkeypatch.setattr(get_settings(), "local_login_enabled", False)
    monkeypatch.setattr(get_settings(), "oidc_silent_login", False)
    c = await make_client()
    r = await c.get("/auth/login", params={"silent": "only"})
    assert parse_qs(urlsplit(r.headers["location"]).query)["prompt"] == ["none"]
    r = await _follow_to_idp(c, "/auth/login?silent=only&next=/api/v1/me")
    assert r.headers["location"] == "/login?stil=1&next=%2Fapi%2Fv1%2Fme"
