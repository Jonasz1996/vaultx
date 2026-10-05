"""Fase 5: automatische login voor een app uit de catalogus (Authentik-OIDC, optioneel meteen in Grafana)."""

import pytest
from sqlalchemy import text

from app.core.db import get_engine
from app.services import app_login
from app.services.login_templates import grafana_env, grafana_role_path
from tests.fake_authentik import FakeAuthentik
from tests.fake_grafana import PASSWORD, USER, FakeGrafana
from tests.fake_npm import proxy_host

GRAFANA_API = "http://grafana:3000"
ADMIN = {"url": GRAFANA_API, "username": USER, "password": PASSWORD}


@pytest.fixture(autouse=True)
def fast_check(monkeypatch):
    monkeypatch.setattr(app_login, "CHECK_DELAY_SECONDS", 0)


@pytest.fixture(autouse=True)
def prober(app, fake_npm, fake_grafana: FakeGrafana):
    """Een bezoeker zonder sessie: eerst NPM (forward auth?), dan Grafana zelf."""

    async def probe(t):
        r = await fake_npm.probe(t)
        if r.status == 200 or (r.status == 404 and not fake_npm.hosts):
            return fake_grafana.probe(t)
        return r

    app.state.npm_prober = probe


async def _org_app(
    admin, fake_authentik: FakeAuthentik, *, url="https://grafana.domain.be", app_type="grafana"
):
    org = (await admin.post_json("/api/v1/organizations", {"slug": "acme", "name": "Acme"})).json()
    fake_authentik.add_group("vaultx:acme")
    fake_authentik.add_group("vaultx:acme:admin")
    body = {"name": "Grafana", "app_type": app_type, "url": url}
    a = (await admin.post_json(f"/api/v1/organizations/{org['id']}/applications", body)).json()
    return org, a, f"/api/v1/organizations/{org['id']}/applications/{a['id']}"


def _codes(plan):
    return {c["code"]: c["level"] for c in plan["checks"]}


async def test_state_lists_templates(admin, fake_authentik):
    _, _, base = await _org_app(admin, fake_authentik)
    r = await admin.get(f"{base}/login")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["configured"] and data["login"] is None
    assert [t["key"] for t in data["templates"]] == ["grafana", "oidc"]
    assert data["suggested_app_url"] == "https://grafana.domain.be"
    # De publieke Authentik-URL komt standaard van de OIDC-issuer van VaultX zelf.
    assert data["authentik_url"].startswith("http://127.0.0.1")


async def test_grafana_without_admin_creates_authentik_side_and_config(admin, fake_authentik, fake_grafana):
    _, a, base = await _org_app(admin, fake_authentik)
    r = await admin.post_json(f"{base}/login/preview", {"template": "grafana"})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["can_apply"] and not plan["configure_app"]
    assert plan["redirect_uris"] == ["https://grafana.domain.be/login/generic_oauth"]
    assert plan["groups"] == ["vaultx:acme", "vaultx:acme:admin"]
    assert "login_roles" in _codes(plan)
    assert any("zelf invullen" in s for s in plan["steps"])
    assert not fake_authentik.oauth2(), "voorbeeld wijzigt niets"

    r = await admin.post_json(f"{base}/login", {"template": "grafana", "default_role": "Editor"})
    assert r.status_code == 201, r.text
    login = r.json()
    [provider] = fake_authentik.oauth2()
    assert provider["name"] == "VaultX login: grafana.domain.be"
    assert provider["redirect_uris"] == [
        {"matching_mode": "strict", "url": "https://grafana.domain.be/login/generic_oauth"}
    ]
    assert provider["grant_types"] == ["authorization_code", "refresh_token"]
    assert provider["client_type"] == "confidential"
    assert provider["signing_key"] == fake_authentik.keypairs[0]["pk"]
    assert len(provider["property_mappings"]) == 4
    assert login["client_id"] == provider["client_id"]
    assert login["application_slug"] == "vaultx-login-grafana-domain-be"
    assert fake_authentik.bindings_for("vaultx-login-grafana-domain-be") == [
        "vaultx:acme",
        "vaultx:acme:admin",
    ]
    assert not login["app_configured"]
    # Grafana kreeg de instellingen nog niet: de controle ziet nog het aanmeldformulier.
    assert login["last_check_status"] == "failed"
    assert "aanmeldformulier" in login["last_check_message"]

    # Het secret staat versleuteld in de database.
    async with get_engine().connect() as conn:
        raw = (await conn.execute(text("SELECT client_secret_ciphertext FROM app_logins"))).scalar_one()
    assert provider["client_secret"].encode() not in raw

    cfg = (await admin.get(f"{base}/login/config")).json()
    assert cfg["client_secret"] == provider["client_secret"]
    assert cfg["issuer"].endswith("/application/o/vaultx-login-grafana-domain-be/")
    ini = next(f["content"] for f in cfg["files"] if f["name"] == "grafana.ini")
    assert "root_url = https://grafana.domain.be/" in ini
    assert f"client_id = {provider['client_id']}" in ini
    assert "auto_login = true" in ini and "use_pkce = true" in ini and "use_refresh_token = true" in ini
    assert "scopes = openid email profile offline_access" in ini
    assert (
        "role_attribute_path = (contains(groups[*], 'vaultx:acme:owner') || contains(groups[*], "
        "'vaultx:acme:admin') || contains(groups[*], 'vaultx-admins')) && 'Admin' || 'Editor'"
    ) in ini
    env = next(f["content"] for f in cfg["files"] if f["name"] == "grafana.env")
    assert "GF_SERVER_ROOT_URL=https://grafana.domain.be/" in env
    actions = [
        e["action"] for e in (await admin.get("/api/v1/audit", params={"target_id": a["id"]})).json()["items"]
    ]
    assert "app_login.configure" in actions and "app_login.config_viewed" in actions

    # In de catalogus, en de app kan niet weg zolang de login er staat.
    assert (await admin.get(base)).json()["auto_login"] == "grafana"
    assert (await admin.delete_(base)).status_code == 422

    # Met de hand in Grafana gezet: de controle slaagt nu.
    fake_grafana.settings.update(
        enabled=True,
        autoLogin=True,
        clientId=provider["client_id"],
        authUrl=cfg["issuer"].split("/application")[0] + "/application/o/authorize/",
    )
    checked = (await admin.post_json(f"{base}/login/check", {})).json()
    assert checked["last_check_status"] == "ok", checked["last_check_message"]

    # Weghalen: VaultX zette niets in Grafana, dus geen beheerder nodig.
    r = await admin.post_json(f"{base}/login/remove", {})
    assert r.status_code == 200, r.text
    assert r.json() is None
    assert not fake_authentik.oauth2() and not fake_authentik.applications
    assert (await admin.get(f"{base}/login")).json()["login"] is None


async def test_grafana_with_admin_configures_grafana_and_removes_again(admin, fake_authentik, fake_grafana):
    _, _, base = await _org_app(admin, fake_authentik)
    plan = (await admin.post_json(f"{base}/login/preview", {"template": "grafana", "grafana": ADMIN})).json()
    assert plan["can_apply"] and plan["configure_app"], plan
    assert "grafana_break_glass" in _codes(plan)
    assert any(s.startswith("Grafana 13.2.3:") for s in plan["steps"])
    assert not fake_grafana.settings["enabled"]

    r = await admin.post_json(f"{base}/login", {"template": "grafana", "grafana": ADMIN})
    assert r.status_code == 201, r.text
    login = r.json()
    s = fake_grafana.settings
    assert s["enabled"] and s["autoLogin"] and s["usePkce"] and s["useRefreshToken"]
    assert s["clientId"] == login["client_id"]
    assert s["clientSecret"] == fake_authentik.oauth2()[0]["client_secret"]
    assert s["roleAttributePath"].endswith("&& 'Admin' || 'Viewer'")
    assert s["signoutRedirectUrl"].endswith("/application/o/vaultx-login-grafana-domain-be/end-session/")
    assert login["app_configured"]
    assert login["last_check_status"] == "ok", login["last_check_message"]
    # Werkt de login, dan telt de app in de catalogus als beschermd door Authentik.
    assert (await admin.get(base)).json()["status"] == "protected"

    # Zonder Grafana-beheerder weghalen zou Grafana naar een verdwenen provider laten wijzen.
    r = await admin.post_json(f"{base}/login/remove", {})
    assert r.status_code == 422 and "Grafana-beheerder" in r.json()["detail"]
    assert fake_authentik.oauth2()

    r = await admin.post_json(f"{base}/login/remove", {"grafana": ADMIN})
    assert r.status_code == 200, r.text
    assert not fake_grafana.settings["enabled"] and fake_grafana.source == "system"
    assert not fake_authentik.oauth2() and not fake_authentik.applications


async def test_grafana_checks_block_before_anything_changes(admin, fake_authentik, fake_grafana):
    _, _, base = await _org_app(admin, fake_authentik)

    fake_grafana.app_url = "http://localhost:3000/"
    plan = (await admin.post_json(f"{base}/login/preview", {"template": "grafana", "grafana": ADMIN})).json()
    assert _codes(plan)["grafana_root_url"] == "block" and not plan["can_apply"]
    assert "GF_SERVER_ROOT_URL=https://grafana.domain.be/" in plan["checks"][-1]["message"]
    r = await admin.post_json(f"{base}/login", {"template": "grafana", "grafana": ADMIN})
    assert r.status_code == 422 and not fake_authentik.oauth2()

    fake_grafana.app_url = "https://grafana.domain.be/"
    fake_grafana.settings.update(enabled=True, clientId="someone-else")
    plan = (await admin.post_json(f"{base}/login/preview", {"template": "grafana", "grafana": ADMIN})).json()
    assert _codes(plan)["grafana_oauth_exists"] == "block"

    wrong = {**ADMIN, "password": "fout"}
    plan = (await admin.post_json(f"{base}/login/preview", {"template": "grafana", "grafana": wrong})).json()
    assert _codes(plan)["grafana_api"] == "block"
    assert "401" in plan["checks"][-1]["message"]


async def test_grafana_failure_rolls_back_authentik(admin, fake_authentik, fake_grafana):
    _, a, base = await _org_app(admin, fake_authentik)
    fake_grafana.fail["PUT /api/v1/sso-settings/generic_oauth"] = (400, {"message": "Invalid auth url"})
    r = await admin.post_json(f"{base}/login", {"template": "grafana", "grafana": ADMIN})
    assert r.status_code == 502, r.text
    assert "Invalid auth url" in r.json()["detail"] and "draaide terug" in r.json()["detail"]
    assert not fake_authentik.oauth2() and not fake_authentik.applications and not fake_authentik.bindings
    assert (await admin.get(f"{base}/login")).json()["login"] is None
    audit = (await admin.get("/api/v1/audit", params={"action": "app_login.configure"})).json()["items"]
    assert audit[0]["outcome"] == "failure"

    # Grafana bewaart de instellingen niet: ook terugdraaien.
    fake_grafana.fail.clear()
    fake_grafana.ignore_put = True
    r = await admin.post_json(f"{base}/login", {"template": "grafana", "grafana": ADMIN})
    assert r.status_code == 502 and "nam de instellingen niet over" in r.json()["detail"]
    assert not fake_authentik.oauth2()


async def test_authentik_failure_midway_is_undone(admin, fake_authentik):
    _, _, base = await _org_app(admin, fake_authentik)
    fake_authentik.fail["POST /policies/bindings/"] = (403, {"detail": "no permission"})
    r = await admin.post_json(f"{base}/login", {"template": "grafana"})
    assert r.status_code == 502, r.text
    assert "mag dit niet" in r.json()["detail"]
    assert not fake_authentik.oauth2() and not fake_authentik.applications


async def test_cleanup_failure_is_kept_for_manual_work(admin, fake_authentik):
    _, _, base = await _org_app(admin, fake_authentik)
    assert (await admin.post_json(f"{base}/login", {"template": "grafana"})).status_code == 201
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
    plan = (
        await admin.post_json(f"{base}/login/preview", {"template": "grafana", "access": "team:ops"})
    ).json()
    assert _codes(plan)["ak_team_unknown"] == "block"
    plan = (await admin.post_json(f"{base}/login/preview", {"template": "grafana", "access": "all"})).json()
    assert plan["can_apply"] and _codes(plan)["ak_access_all"] == "warn" and plan["groups"] == []

    fake_authentik.groups.clear()
    plan = (await admin.post_json(f"{base}/login/preview", {"template": "grafana"})).json()
    assert _codes(plan)["ak_no_group"] == "block"

    fake_authentik.keypairs.clear()
    fake_authentik.scope_mappings = [
        m for m in fake_authentik.scope_mappings if "profile" not in m["managed"]
    ]
    plan = (await admin.post_json(f"{base}/login/preview", {"template": "grafana", "access": "all"})).json()
    assert _codes(plan)["ak_signing_key"] == "warn" and _codes(plan)["ak_scope_missing"] == "block"


async def test_http_and_missing_url(admin, fake_authentik):
    _, _, base = await _org_app(admin, fake_authentik, url="http://grafana.lan:3000/")
    plan = (await admin.post_json(f"{base}/login/preview", {"template": "grafana"})).json()
    assert _codes(plan)["login_http"] == "warn"
    assert plan["redirect_uris"] == ["http://grafana.lan:3000/login/generic_oauth"]
    plan = (
        await admin.post_json(f"{base}/login/preview", {"template": "grafana", "app_url": "ftp://x"})
    ).json()
    assert _codes(plan)["login_app_url"] == "block"


async def test_generic_oidc_template(admin, fake_authentik):
    _, _, base = await _org_app(
        admin, fake_authentik, url="https://portainer.domain.be", app_type="portainer"
    )
    state = (await admin.get(f"{base}/login")).json()
    assert [t["key"] for t in state["templates"]] == ["oidc"]
    plan = (await admin.post_json(f"{base}/login/preview", {"template": "oidc"})).json()
    assert _codes(plan)["login_redirect_missing"] == "block"
    body = {"template": "oidc", "redirect_uris": ["https://portainer.domain.be/"]}
    r = await admin.post_json(f"{base}/login", body)
    assert r.status_code == 201, r.text
    assert r.json()["last_check_status"] is None
    cfg = (await admin.get(f"{base}/login/config")).json()
    [f] = cfg["files"]
    assert f["name"] == "oidc.txt"
    assert cfg["issuer"] in f["content"] and cfg["client_secret"] in f["content"]
    assert "https://portainer.domain.be/" in f["content"]
    # Een tweede keer inrichten kan niet.
    plan = (await admin.post_json(f"{base}/login/preview", body)).json()
    assert _codes(plan)["login_exists"] == "block"


async def test_check_behind_forward_auth_is_unknown(admin, fake_npm, fake_authentik, fake_grafana):
    fake_npm.hosts = [
        proxy_host(
            1,
            ["grafana.domain.be"],
            forward_host="grafana",
            forward_port=3000,
            certificate_id=1,
            advanced_config="location /outpost.goauthentik.io {\n  proxy_pass http://ak:9000/outpost.goauthentik.io;\n}\n"
            "auth_request /outpost.goauthentik.io/auth/nginx;\n",
        )
    ]
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
    state = (await admin.get(f"{base}/login")).json()
    assert state["suggested_grafana_url"] == "http://grafana:3000"
    r = await admin.post_json(f"{base}/login", {"template": "grafana", "grafana": ADMIN})
    assert r.status_code == 201, r.text
    login = r.json()
    assert login["last_check_status"] == "unknown" and "forward auth" in login["last_check_message"]


async def test_members_cannot_configure_or_see_config(admin, make_client, fake_authentik):
    org, a, base = await _org_app(admin, fake_authentik)
    assert (await admin.post_json(f"{base}/login", {"template": "grafana"})).status_code == 201
    member = await make_client()
    await member.login("bob", groups=["vaultx:acme"])
    state = await member.get(f"{base}/login")
    assert state.status_code == 200 and state.json()["login"]["client_id"]
    assert (await member.post_json(f"{base}/login/preview", {"template": "grafana"})).status_code == 403
    assert (await member.get(f"{base}/login/config")).status_code == 403
    assert (await member.post_json(f"{base}/login/remove", {})).status_code == 403
    denied = (await admin.get("/api/v1/audit", params={"outcome": "denied"})).json()["items"]
    assert {e["action"] for e in denied} >= {
        "app_login.configure",
        "app_login.config_viewed",
        "app_login.remove",
    }


def test_role_path_precedence_and_escaping():
    assert grafana_role_path([], "Viewer") == "'Viewer'"
    assert grafana_role_path(["a"], "Admin") == "'Admin'"
    path = grafana_role_path(["vaultx:o:owner", "it's"], "Editor")
    assert (
        path
        == "(contains(groups[*], 'vaultx:o:owner') || contains(groups[*], 'it\\'s')) && 'Admin' || 'Editor'"
    )
    with pytest.raises(ValueError):
        grafana_role_path([], "Root")


def test_grafana_env_quotes_values_with_spaces():
    env = grafana_env(
        "https://g.be",
        {"enabled": True, "role_attribute_path": "a && 'Admin'", "scopes": "a b"}
        | {
            k: "x"
            for k in (
                "name",
                "client_id",
                "client_secret",
                "auth_url",
                "token_url",
                "api_url",
                "use_pkce",
                "use_refresh_token",
                "auto_login",
                "allow_sign_up",
                "login_attribute_path",
                "name_attribute_path",
                "email_attribute_path",
                "role_attribute_strict",
                "signout_redirect_url",
            )
        },
    )
    assert "GF_AUTH_GENERIC_OAUTH_ENABLED=true" in env
    assert "GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_PATH=\"a && 'Admin'\"" in env
    assert 'GF_AUTH_GENERIC_OAUTH_SCOPES="a b"' in env
