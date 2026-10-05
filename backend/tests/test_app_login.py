"""Fase 5: automatische login voor een app uit de catalogus (OIDC-provider in Authentik)."""

from sqlalchemy import text

from app.core.db import get_engine
from app.services.app_login import login_slug, normalize_app_url, valid_redirect_uri
from tests.fake_authentik import FakeAuthentik
from tests.fake_npm import proxy_host

REDIRECT = "https://wiki.domain.be/oauth/callback"


async def _org_app(admin, fake_authentik: FakeAuthentik, *, url="https://wiki.domain.be"):
    org = (await admin.post_json("/api/v1/organizations", {"slug": "acme", "name": "Acme"})).json()
    fake_authentik.add_group("vaultx:acme")
    fake_authentik.add_group("vaultx:acme:admin")
    body = {"name": "Wiki", "app_type": "wiki", "url": url}
    a = (await admin.post_json(f"/api/v1/organizations/{org['id']}/applications", body)).json()
    return org, a, f"/api/v1/organizations/{org['id']}/applications/{a['id']}"


def _codes(plan):
    return {c["code"]: c["level"] for c in plan["checks"]}


async def test_state(admin, fake_authentik):
    _, _, base = await _org_app(admin, fake_authentik)
    r = await admin.get(f"{base}/login")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["configured"] and data["login"] is None
    assert data["suggested_app_url"] == "https://wiki.domain.be"
    # De publieke Authentik-URL komt standaard van de OIDC-issuer van VaultX zelf.
    assert data["authentik_url"].startswith("http://127.0.0.1")


async def test_configure_view_config_and_remove(admin, fake_authentik):
    _, a, base = await _org_app(admin, fake_authentik)
    body = {"redirect_uris": [REDIRECT]}
    r = await admin.post_json(f"{base}/login/preview", body)
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["can_apply"], plan
    assert plan["app_url"] == "https://wiki.domain.be"
    assert plan["redirect_uris"] == [REDIRECT]
    assert plan["groups"] == ["vaultx:acme", "vaultx:acme:admin"]
    assert any("niets in de app" in s for s in plan["steps"])
    assert not fake_authentik.oauth2(), "voorbeeld wijzigt niets"

    r = await admin.post_json(f"{base}/login", body)
    assert r.status_code == 201, r.text
    login = r.json()
    [provider] = fake_authentik.oauth2()
    assert provider["name"] == "VaultX login: wiki.domain.be"
    assert provider["redirect_uris"] == [{"matching_mode": "strict", "url": REDIRECT}]
    assert provider["grant_types"] == ["authorization_code", "refresh_token"]
    assert provider["client_type"] == "confidential"
    assert provider["signing_key"] == fake_authentik.keypairs[0]["pk"]
    assert len(provider["property_mappings"]) == 4
    assert login["client_id"] == provider["client_id"]
    assert login["application_slug"] == "vaultx-login-wiki-domain-be"
    assert (
        fake_authentik.applications["vaultx-login-wiki-domain-be"]["meta_launch_url"]
        == "https://wiki.domain.be"
    )
    assert fake_authentik.bindings_for("vaultx-login-wiki-domain-be") == ["vaultx:acme", "vaultx:acme:admin"]

    # Het secret staat versleuteld in de database.
    async with get_engine().connect() as conn:
        raw = (await conn.execute(text("SELECT client_secret_ciphertext FROM app_logins"))).scalar_one()
    assert provider["client_secret"].encode() not in raw

    r = await admin.get(f"{base}/login/config")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    cfg = r.json()
    assert cfg["client_secret"] == provider["client_secret"]
    assert cfg["issuer"].endswith("/application/o/vaultx-login-wiki-domain-be/")
    assert cfg["discovery_url"] == cfg["issuer"] + ".well-known/openid-configuration"
    assert cfg["authorization_url"].endswith("/application/o/authorize/")
    assert cfg["scopes"] == ["openid", "email", "profile", "offline_access"]
    assert cfg["redirect_uris"] == [REDIRECT]
    for value in (cfg["issuer"], cfg["client_id"], cfg["client_secret"], REDIRECT):
        assert value in cfg["text"]
    actions = [
        e["action"] for e in (await admin.get("/api/v1/audit", params={"target_id": a["id"]})).json()["items"]
    ]
    assert "app_login.configure" in actions and "app_login.config_viewed" in actions

    # In de catalogus, en de app kan niet weg zolang de login er staat.
    assert (await admin.get(base)).json()["auto_login"] is True
    assert (await admin.delete_(base)).status_code == 422

    # Een tweede keer inrichten kan niet.
    plan = (await admin.post_json(f"{base}/login/preview", body)).json()
    assert _codes(plan)["login_exists"] == "block"

    r = await admin.post_json(f"{base}/login/remove", {})
    assert r.status_code == 200, r.text
    assert r.json() is None
    assert not fake_authentik.oauth2() and not fake_authentik.applications and not fake_authentik.bindings
    assert (await admin.get(f"{base}/login")).json()["login"] is None
    assert (await admin.get(base)).json()["auto_login"] is False


async def test_redirect_uri_validation(admin, fake_authentik):
    _, _, base = await _org_app(admin, fake_authentik)
    r = await admin.post_json(f"{base}/login/preview", {"redirect_uris": []})
    assert r.status_code == 422, "minstens één redirect URI"
    plan = (await admin.post_json(f"{base}/login/preview", {"redirect_uris": ["  "]})).json()
    assert _codes(plan)["login_redirect_missing"] == "block"
    bad = {"redirect_uris": ["javascript:alert(1)", "/relatief"]}
    plan = (await admin.post_json(f"{base}/login/preview", bad)).json()
    assert _codes(plan)["login_redirect_invalid"] == "block" and not plan["can_apply"]
    plan = (await admin.post_json(f"{base}/login/preview", {"redirect_uris": ["http://wiki.lan/cb"]})).json()
    assert plan["can_apply"] and _codes(plan)["login_http"] == "warn"
    # Dubbele en lege regels vallen weg.
    double = {"redirect_uris": [REDIRECT, "", REDIRECT]}
    plan = (await admin.post_json(f"{base}/login/preview", double)).json()
    assert plan["redirect_uris"] == [REDIRECT]
    r = await admin.post_json(f"{base}/login", {"redirect_uris": ["ftp://x/cb"]})
    assert r.status_code == 422 and not fake_authentik.oauth2()


async def test_missing_app_url(admin, fake_authentik):
    _, _, base = await _org_app(admin, fake_authentik, url=None)
    assert (await admin.get(f"{base}/login")).json()["suggested_app_url"] is None
    plan = (await admin.post_json(f"{base}/login/preview", {"redirect_uris": [REDIRECT]})).json()
    assert _codes(plan)["login_app_url"] == "block"
    body = {"redirect_uris": [REDIRECT], "app_url": "https://wiki.domain.be/"}
    plan = (await admin.post_json(f"{base}/login/preview", body)).json()
    assert plan["can_apply"] and plan["app_url"] == "https://wiki.domain.be"


async def test_authentik_failure_midway_is_undone(admin, fake_authentik):
    _, _, base = await _org_app(admin, fake_authentik)
    fake_authentik.fail["POST /policies/bindings/"] = (403, {"detail": "no permission"})
    r = await admin.post_json(f"{base}/login", {"redirect_uris": [REDIRECT]})
    assert r.status_code == 502, r.text
    assert "mag dit niet" in r.json()["detail"] and "draaide terug" in r.json()["detail"]
    assert not fake_authentik.oauth2() and not fake_authentik.applications
    assert (await admin.get(f"{base}/login")).json()["login"] is None
    audit = (await admin.get("/api/v1/audit", params={"action": "app_login.configure"})).json()["items"]
    assert audit[0]["outcome"] == "failure"


async def test_cleanup_failure_is_kept_for_manual_work(admin, fake_authentik):
    _, _, base = await _org_app(admin, fake_authentik)
    assert (await admin.post_json(f"{base}/login", {"redirect_uris": [REDIRECT]})).status_code == 201
    fake_authentik.fail["DELETE /providers/oauth2/"] = (500, {"detail": "boom"})
    r = await admin.post_json(f"{base}/login/remove", {})
    assert r.status_code == 200, r.text
    left = r.json()
    assert left["cleanup_error"] and left["provider_created"] and not left["application_created"]
    fake_authentik.fail.clear()
    r = await admin.post_json(f"{base}/login/remove", {})
    assert r.json() is None and not fake_authentik.oauth2()


async def test_access_and_authentik_blockers(admin, fake_authentik):
    _, _, base = await _org_app(admin, fake_authentik)
    body = {"redirect_uris": [REDIRECT]}
    plan = (await admin.post_json(f"{base}/login/preview", {**body, "access": "team:ops"})).json()
    assert _codes(plan)["ak_team_unknown"] == "block"
    plan = (await admin.post_json(f"{base}/login/preview", {**body, "access": "all"})).json()
    assert plan["can_apply"] and _codes(plan)["ak_access_all"] == "warn" and plan["groups"] == []

    fake_authentik.groups.clear()
    plan = (await admin.post_json(f"{base}/login/preview", body)).json()
    assert _codes(plan)["ak_no_group"] == "block"

    fake_authentik.keypairs.clear()
    mappings = fake_authentik.scope_mappings
    fake_authentik.scope_mappings = [m for m in mappings if "profile" not in m["managed"]]
    plan = (await admin.post_json(f"{base}/login/preview", {**body, "access": "all"})).json()
    assert _codes(plan)["ak_signing_key"] == "warn" and _codes(plan)["ak_scope_missing"] == "block"


async def test_provider_name_collision_gets_suffix(admin, fake_authentik):
    _, _, base = await _org_app(admin, fake_authentik)
    fake_authentik.add_oauth2_provider({"name": "VaultX login: wiki.domain.be"})
    r = await admin.post_json(f"{base}/login", {"redirect_uris": [REDIRECT]})
    assert r.status_code == 201, r.text
    assert r.json()["provider_name"] == "VaultX login: wiki.domain.be (2)"


async def test_app_url_from_npm_host(admin, fake_npm, fake_authentik):
    """Een app uit de NPM-sync krijgt de URL van zijn proxy host als voorstel."""
    host = proxy_host(1, ["wiki.domain.be"], forward_host="wiki", forward_port=8080, certificate_id=1)
    fake_npm.hosts = [host]
    org = (await admin.post_json("/api/v1/organizations", {"slug": "acme", "name": "Acme"})).json()
    fake_authentik.add_group("vaultx:acme")
    body = {
        "name": "NPM",
        "base_url": "http://npm.lan:81",
        "identity": "admin@example.com",
        "secret": "npm-secret",
    }
    conn = (await admin.post_json(f"/api/v1/organizations/{org['id']}/npm-connections", body)).json()
    nbase = f"/api/v1/organizations/{org['id']}/npm-connections/{conn['id']}"
    assert (await admin.post_json(f"{nbase}/sync", {})).status_code == 200
    [a] = (await admin.get("/api/v1/catalog")).json()
    base = f"/api/v1/organizations/{org['id']}/applications/{a['id']}"
    assert (await admin.get(f"{base}/login")).json()["suggested_app_url"] == "https://wiki.domain.be"
    r = await admin.post_json(f"{base}/login", {"redirect_uris": [REDIRECT]})
    assert r.status_code == 201, r.text
    assert r.json()["app_url"] == "https://wiki.domain.be"


async def test_members_cannot_configure_or_see_config(admin, make_client, fake_authentik):
    _, _, base = await _org_app(admin, fake_authentik)
    body = {"redirect_uris": [REDIRECT]}
    assert (await admin.post_json(f"{base}/login", body)).status_code == 201
    member = await make_client()
    await member.login("bob", groups=["vaultx:acme"])
    state = await member.get(f"{base}/login")
    assert state.status_code == 200 and state.json()["login"]["client_id"]
    assert "client_secret" not in state.json()["login"]
    assert (await member.post_json(f"{base}/login/preview", body)).status_code == 403
    assert (await member.get(f"{base}/login/config")).status_code == 403
    assert (await member.post_json(f"{base}/login/remove", {})).status_code == 403
    denied = (await admin.get("/api/v1/audit", params={"outcome": "denied"})).json()["items"]
    assert {e["action"] for e in denied} >= {
        "app_login.configure",
        "app_login.config_viewed",
        "app_login.remove",
    }


def test_url_helpers():
    assert normalize_app_url("https://wiki.domain.be/") == "https://wiki.domain.be"
    assert normalize_app_url("https://wiki.domain.be:8443/sub/") == "https://wiki.domain.be:8443/sub"
    for bad in (None, "", "ftp://x", "https://x/?a=1", "https://x:99999", "wiki.domain.be"):
        assert normalize_app_url(bad) is None, bad
    assert valid_redirect_uri("https://wiki.domain.be/cb?x=1")
    assert valid_redirect_uri("http://10.0.0.5:8006")
    for bad in ("javascript:alert(1)", "/cb", "https://x/cb#frag", "https://x/c b", "https://:80/cb"):
        assert not valid_redirect_uri(bad), bad
    assert login_slug("wiki.domain.be") == "vaultx-login-wiki-domain-be"
    assert login_slug("wiki.domain.be", 1) == "vaultx-login-wiki-domain-be-2"
    assert len(login_slug("a" * 80 + ".be", 3)) <= 50
