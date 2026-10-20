from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.config import Settings
from tests.conftest import EXTRA_ORIGIN, PUBLIC_URL


async def test_unauthenticated_api_is_401(make_client):
    c = await make_client()
    r = await c.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json()["error"] == "unauthenticated"


async def test_login_redirect_uses_pkce_and_sets_state_cookie(make_client, fake_oidc):
    c = await make_client()
    r = await c.get("/auth/login")
    assert r.status_code == 303
    loc = urlsplit(r.headers["location"])
    q = parse_qs(loc.query)
    assert loc.path == "/application/o/authorize/"
    assert q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == [f"{PUBLIC_URL}/auth/callback"]
    assert {"state", "nonce", "code_challenge"} <= q.keys()
    assert "vaultx_oidc" in r.headers["set-cookie"]


async def test_full_login_creates_user_session_and_audit(make_client):
    c = await make_client()
    r = await c.login("alice", groups=["vaultx-admins"], next="/organizations")
    assert r.status_code == 303
    assert r.headers["location"] == "/organizations"
    me = (await c.get("/api/v1/me")).json()
    assert me["email"] == "alice@example.com"
    assert me["is_admin"] is True
    assert me["auth_method"] == "session"
    events = (await c.get("/api/v1/audit", params={"instance_only": True})).json()["items"]
    actions = [e["action"] for e in events]
    assert "user.provisioned" in actions and "auth.login" in actions


async def test_open_redirect_is_blocked(make_client):
    c = await make_client()
    r = await c.login("bob", next="//evil.example.com/x")
    assert r.headers["location"] == "/"


async def test_callback_with_wrong_state_is_rejected(make_client):
    c = await make_client()
    await c.get("/auth/login")
    r = await c.get("/auth/callback", params={"code": "x", "state": "forged"})
    assert r.status_code == 303
    assert "error=invalid_state" in r.headers["location"]
    assert (await c.get("/api/v1/me")).status_code == 401


async def test_callback_without_state_cookie_is_rejected(make_client, fake_oidc):
    attacker = await make_client()
    r = await attacker.get("/auth/login")
    async with httpx.AsyncClient() as idp:
        r2 = await idp.get(r.headers["location"])
    victim = await make_client()  # login-CSRF: slachtoffer heeft geen state-cookie
    cb = urlsplit(r2.headers["location"])
    r3 = await victim.get(f"{cb.path}?{cb.query}")
    assert "error=invalid_state" in r3.headers["location"]


async def test_idp_error_is_reported(make_client):
    c = await make_client()
    await c.get("/auth/login")
    r = await c.get("/auth/callback", params={"error": "access_denied", "state": "x"})
    assert "error=idp_error" in r.headers["location"]


async def test_admin_flag_follows_groups(make_client):
    c = await make_client()
    await c.login("carol", groups=["vaultx-admins"])
    assert (await c.get("/api/v1/me")).json()["is_admin"] is True
    c2 = await make_client()
    await c2.login("carol", groups=[])
    assert (await c2.get("/api/v1/me")).json()["is_admin"] is False


async def test_logout_revokes_session_and_returns_end_session_url(make_client, fake_oidc):
    c = await make_client()
    await c.login("dave")
    r = await c.post("/auth/logout", headers={"X-VaultX-CSRF": "1"})
    assert r.status_code == 200
    url = r.json()["redirect_url"]
    assert url.startswith(f"{fake_oidc.base}/application/o/vaultx/end-session/")
    assert "id_token_hint=" in url
    assert (await c.get("/api/v1/me")).status_code == 401


async def test_logout_requires_csrf_header(make_client):
    c = await make_client()
    await c.login("erin")
    r = await c.post("/auth/logout")
    assert r.status_code == 403
    assert (await c.get("/api/v1/me")).status_code == 200


async def test_mutation_with_cookie_requires_csrf_header(admin):
    r = await admin.post("/api/v1/organizations", json={"slug": "acme", "name": "Acme"})
    assert r.status_code == 403
    r = await admin.post_json("/api/v1/organizations", {"slug": "acme", "name": "Acme"})
    assert r.status_code == 201


async def test_bearer_token_auth(make_client, fake_oidc):
    c = await make_client()
    await c.login("frank")
    api = await make_client()
    token = fake_oidc.access_token("frank")
    r = await api.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["auth_method"] == "bearer"


async def test_bearer_token_wrong_audience_or_issuer_rejected(make_client, fake_oidc):
    c = await make_client()
    await c.login("gina")
    bad_aud = fake_oidc.access_token("gina", aud="other-client")
    bad_iss = fake_oidc.access_token("gina", iss="https://evil.example.com/")
    for token in (bad_aud, bad_iss, "not-a-jwt"):
        r = await c.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, token


async def test_bearer_token_signed_by_unknown_key_rejected(make_client, fake_oidc):
    from joserfc.jwk import RSAKey

    c = await make_client()
    await c.login("hank")
    rogue = RSAKey.generate_key(2048, parameters={"kid": "key-1"})
    import time

    now = int(time.time())
    token = fake_oidc.sign(
        {"iss": fake_oidc.issuer, "aud": "vaultx", "sub": "hank", "iat": now, "exp": now + 60}, key=rogue
    )
    r = await c.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_backchannel_logout_by_sid(make_client, fake_oidc):
    c = await make_client()
    await c.login("ivy")
    sid = f"sid-{fake_oidc.sid_counter}"
    other = await make_client()
    r = await other.post("/auth/backchannel-logout", data={"logout_token": fake_oidc.logout_token(sid=sid)})
    assert r.status_code == 200
    assert (await c.get("/api/v1/me")).status_code == 401


async def test_backchannel_logout_rejects_token_with_nonce(make_client, fake_oidc):
    c = await make_client()
    r = await c.post(
        "/auth/backchannel-logout", data={"logout_token": fake_oidc.logout_token(sid="x", nonce="n")}
    )
    assert r.status_code == 400


async def test_deactivated_user_cannot_log_in(admin, make_client):
    c = await make_client()
    await c.login("jack")
    me = (await c.get("/api/v1/me")).json()
    r = await admin.patch_json(f"/api/v1/users/{me['id']}", {"is_active": False})
    assert r.status_code == 200
    assert (await c.get("/api/v1/me")).status_code == 401  # sessies ingetrokken
    c2 = await make_client()
    r = await c2.login("jack")
    assert "error=account_disabled" in r.headers["location"]


def test_safe_next_rejects_tricky_redirects():
    from app.api.auth import safe_next

    for bad in [
        "//evil.com",
        "/\t/evil.com",
        "/\\evil.com",
        "https://evil.com",
        "/ /x",
        "",
        None,
        "/\n/evil",
    ]:
        assert safe_next(bad) == "/", bad
    for good in ["/", "/organizations", "/audit?organization_id=1&x=2", "/users/abc-123"]:
        assert safe_next(good) == good


async def test_callback_without_state_cookie_writes_no_audit_row(make_client, admin):
    # Zonder login de append-only auditlog volschrijven mag niet lukken.
    stranger = await make_client()
    for _ in range(3):
        r = await stranger.get("/auth/callback", params={"error": "x" * 100, "state": "y"})
        assert "error=idp_error" in r.headers["location"]
    items = (await admin.get("/api/v1/audit", params={"action": "auth.login"})).json()["items"]
    assert not any(i["outcome"] == "failure" for i in items)


async def test_simultaneous_first_logins_both_succeed(make_client):
    import asyncio

    a, b = await make_client(), await make_client()
    ra, rb = await asyncio.gather(
        a.login("dave", groups=["vaultx:nieuw"]), b.login("dave", groups=["vaultx:nieuw"])
    )
    assert ra.status_code == rb.status_code == 303, (ra.text, rb.text)
    for c in (a, b):
        assert (await c.get("/api/v1/me")).status_code == 200


async def test_login_on_extra_origin_stays_there(make_client, fake_oidc):
    c = await make_client(EXTRA_ORIGIN)
    r = await c.get("/auth/login", params={"next": "/vault"})
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["redirect_uri"] == [f"{EXTRA_ORIGIN}/auth/callback"]
    r = await c.login("frank", next="/notes?note=abc")
    assert r.status_code == 303
    assert r.headers["location"] == "/notes?note=abc"
    assert (await c.get("/api/v1/me")).json()["email"] == "frank@example.com"
    out = (await c.post("/auth/logout", headers={"X-VaultX-CSRF": "1"})).json()["redirect_url"]
    q = parse_qs(urlsplit(out).query)
    assert q["post_logout_redirect_uri"] == [f"{EXTRA_ORIGIN}/login?logged_out=1"]


async def test_authentik_login_on_unknown_origin_does_not_jump_to_public_url(make_client):
    c = await make_client("http://192.168.1.50")
    assert (await c.get("/auth/methods")).json() == {"authentik": False, "password": True}
    r = await c.get("/auth/login", params={"next": "/vault"})
    assert r.status_code == 303
    assert r.headers["location"] == "/login?error=authentik_not_here&next=%2Fvault"
    assert 'vaultx_oidc=""' in r.headers.get("set-cookie", "")  # enkel opgeruimd, geen state


async def test_host_header_is_not_trusted_for_redirect_uri(make_client):
    c = await make_client()
    r = await c.get("/auth/login", headers={"Host": "evil.example.com"})
    assert r.headers["location"] == "/login?error=authentik_not_here"


@pytest.mark.parametrize(
    ("url", "secure"),
    [
        ("https://vaultx.example.be/", True),
        ("http://vaultx.example.be/", True),  # publieke naam zonder TLS: nooit zonder Secure
        ("http://evil.example.com/", True),
        ("http://192.168.0.50/", False),  # LAN-IP zonder NPM ervoor
        ("https://192.168.0.50/", True),  # LAN-IP met het zelfondertekende certificaat (tls-cert.sh)
        ("http://10.1.2.3:8080/", False),
        ("http://127.0.0.1/", False),
        ("http://[fd00::5]/", False),
        ("http://8.8.8.8/", True),  # publiek IP
        ("http://vaultx.lan:8080/", False),  # http-origin uit VAULTX_EXTRA_ORIGINS
        ("http://vaultx.lan:9999/", True),
    ],
)
def test_cookie_secure_follows_address(url, secure):
    from starlette.requests import Request

    from app.api.auth import _cookie_secure

    parts = urlsplit(url)
    scope = {
        "type": "http",
        "scheme": parts.scheme,
        "server": (parts.hostname, parts.port or 80),
        "path": "/",
        "query_string": b"",
        "headers": [(b"host", parts.netloc.encode())],
    }
    settings = Settings(
        secret_key="x" * 32,
        oidc_issuer="https://a/",
        oidc_client_id="c",
        oidc_client_secret="s",
        public_url="https://vaultx.example.be",
        extra_origins=EXTRA_ORIGIN,
        cookie_secure=True,
    )
    assert _cookie_secure(Request(scope), settings) is secure


async def test_login_error_keeps_the_page(make_client):
    c = await make_client()
    await c.get("/auth/login", params={"next": "/vault"})
    r = await c.get("/auth/callback", params={"error": "access_denied", "state": "x"})
    assert r.headers["location"] == "/login?error=idp_error&next=%2Fvault"


def test_extra_origins_are_validated():
    base = {
        "secret_key": "x" * 32,
        "oidc_issuer": "https://a/",
        "oidc_client_id": "c",
        "oidc_client_secret": "s",
    }
    s = Settings(
        **base, public_url="https://Vault.example.be/", extra_origins="https://a.lan/,http://10.0.0.5:8080"
    )
    assert s.allowed_origins == {"https://vault.example.be", "https://a.lan", "http://10.0.0.5:8080"}
    with pytest.raises(ValueError):
        Settings(**base, extra_origins="vaultx.lan")
