from urllib.parse import parse_qs, urlsplit

import httpx

from tests.conftest import PUBLIC_URL


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
    assert r.headers["location"] == f"{PUBLIC_URL}/organizations"
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
    assert r.headers["location"] == f"{PUBLIC_URL}/"


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
