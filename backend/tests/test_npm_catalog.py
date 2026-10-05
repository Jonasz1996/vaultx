from sqlalchemy import text

from app.core.db import get_engine
from tests.fake_npm import proxy_host

FA_LOCATION = {
    "path": "/",
    "forward_scheme": "http",
    "forward_host": "grafana",
    "forward_port": 3000,
    "advanced_config": "auth_request /outpost.goauthentik.io/auth/nginx;",
}


async def _org(admin, slug="acme"):
    r = await admin.post_json("/api/v1/organizations", {"slug": slug, "name": slug.title()})
    assert r.status_code == 201, r.text
    return r.json()


async def _conn(admin, org, **extra):
    body = {
        "name": "Homelab NPM",
        "base_url": "http://npm.lan:81/api/",
        "identity": "admin@example.com",
        "secret": "npm-secret",
        **extra,
    }
    r = await admin.post_json(f"/api/v1/organizations/{org['id']}/npm-connections", body)
    assert r.status_code == 201, r.text
    return r.json()


def _seed(fake_npm):
    fake_npm.hosts = [
        proxy_host(
            1,
            ["grafana.domain.be"],
            forward_host="10.0.0.5",
            forward_port=3000,
            advanced_config="# vaultx.auth = oidc\n# vaultx.tags = monitoring",
            certificate_id=1,
            ssl_forced=True,
        ),
        proxy_host(2, ["dash.domain.be"], forward_host="grafana", locations=[FA_LOCATION], certificate_id=1),
        proxy_host(3, ["nas.domain.be"], forward_host="10.0.0.9", forward_port=5000),
        proxy_host(4, ["old.domain.be"], advanced_config="# vaultx.ignore = true"),
    ]


async def test_connection_secret_is_encrypted_and_never_returned(admin):
    org = await _org(admin)
    conn = await _conn(admin, org)
    assert conn["base_url"] == "http://npm.lan:81"
    assert "secret" not in conn and "secret_ciphertext" not in conn
    async with get_engine().connect() as c:
        raw = await c.scalar(text("SELECT secret_ciphertext FROM npm_connections"))
    assert b"npm-secret" not in raw
    audit = (await admin.get("/api/v1/audit", params={"action": "npm_connection."})).json()["items"]
    assert audit[0]["action"] == "npm_connection.created"
    assert "secret" not in audit[0]["details"]


async def test_sync_fills_catalog(admin, fake_npm):
    _seed(fake_npm)
    org = await _org(admin)
    conn = await _conn(admin, org)
    base = f"/api/v1/organizations/{org['id']}/npm-connections/{conn['id']}"
    r = await admin.post_json(f"{base}/sync", {})
    assert r.status_code == 200, r.text
    res = r.json()
    assert (res["hosts_total"], res["hosts_new"], res["applications_created"]) == (4, 4, 3)
    assert res["npm_version"] == "2.16.0"

    apps = {a["name"]: a for a in (await admin.get("/api/v1/catalog")).json()}
    assert set(apps) == {"Grafana", "Dash", "Nas"}
    g = apps["Grafana"]
    # Het voorbeeld uit de opdracht: host, app, auth, status
    assert (g["hosts"][0]["domain_names"][0], g["app_type"], g["auth_method"], g["status"]) == (
        "grafana.domain.be",
        "grafana",
        "oidc",
        "protected",
    )
    assert g["tags"] == ["monitoring"] and g["source"] == "npm" and g["auto_update"]
    assert apps["Dash"]["auth_method"] == "forward_auth" and apps["Dash"]["status"] == "protected"
    assert apps["Dash"]["app_type"] == "grafana"
    assert apps["Nas"]["status"] == "unknown"
    assert apps["Nas"]["warning_count"] == 1  # geen TLS

    hosts = (await admin.get(f"{base}/hosts")).json()
    old = next(h for h in hosts if h["npm_id"] == 4)
    assert old["application"] is None and old["labels"] == {"ignore": "true"}

    conn = (await admin.get(base)).json()
    assert conn["last_sync_status"] == "ok" and conn["host_count"] == 4
    assert len((await admin.get("/api/v1/catalog", params={"status": "protected"})).json()) == 2
    assert [a["name"] for a in (await admin.get("/api/v1/catalog", params={"q": "nas"})).json()] == ["Nas"]

    audit = (await admin.get("/api/v1/audit", params={"action": "npm.sync"})).json()["items"]
    assert sorted(audit[0]["details"]["applications_created"]) == ["Dash", "Grafana", "Nas"]
    v = (await admin.get("/api/v1/audit/verify", params={"organization_id": org["id"]})).json()
    assert v["valid"]


async def test_resync_updates_respects_manual_edits_and_marks_removed(admin, fake_npm):
    _seed(fake_npm)
    org = await _org(admin)
    conn = await _conn(admin, org)
    base = f"/api/v1/organizations/{org['id']}/npm-connections/{conn['id']}"
    await admin.post_json(f"{base}/sync", {})
    apps = {a["name"]: a for a in (await admin.get("/api/v1/catalog")).json()}

    # Gebruiker hernoemt "Nas" en zet de aanmelding: auto_update gaat uit.
    r = await admin.patch_json(
        f"/api/v1/organizations/{org['id']}/applications/{apps['Nas']['id']}",
        {"name": "Synology", "auth_method": "app"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["auto_update"] is False and r.json()["status"] == "restricted"

    # In NPM: Nas krijgt een label, Dash verdwijnt, Grafana gaat offline, nieuwe host erbij.
    fake_npm.hosts[2]["advanced_config"] = "# vaultx.app = NAS"
    fake_npm.hosts[0]["meta"] = {"nginx_online": False}
    del fake_npm.hosts[1]
    fake_npm.hosts.append(proxy_host(9, ["jellyfin.domain.be"], forward_port=8096))
    res = (await admin.post_json(f"{base}/sync", {})).json()
    assert (res["hosts_new"], res["hosts_removed"], res["applications_created"]) == (1, 1, 1)

    apps = {a["name"]: a for a in (await admin.get("/api/v1/catalog")).json()}
    assert "Synology" in apps and "NAS" not in apps
    assert apps["Dash"]["status"] == "removed"
    assert apps["Grafana"]["status"] == "offline"
    assert apps["Jellyfin"]["app_type"] == "jellyfin"

    # Host komt terug -> niet meer removed
    fake_npm.hosts.append(proxy_host(2, ["dash.domain.be"], forward_host="grafana", locations=[FA_LOCATION]))
    await admin.post_json(f"{base}/sync", {})
    apps = {a["name"]: a for a in (await admin.get("/api/v1/catalog")).json()}
    assert apps["Dash"]["status"] == "protected"


async def test_delete_app_ignores_host_and_unignore_brings_it_back(admin, fake_npm):
    _seed(fake_npm)
    org = await _org(admin)
    conn = await _conn(admin, org)
    base = f"/api/v1/organizations/{org['id']}/npm-connections/{conn['id']}"
    await admin.post_json(f"{base}/sync", {})
    nas = next(a for a in (await admin.get("/api/v1/catalog")).json() if a["name"] == "Nas")
    r = await admin.delete_(f"/api/v1/organizations/{org['id']}/applications/{nas['id']}")
    assert r.status_code == 204
    await admin.post_json(f"{base}/sync", {})
    names = {a["name"] for a in (await admin.get("/api/v1/catalog")).json()}
    assert "Nas" not in names

    host = next(h for h in (await admin.get(f"{base}/hosts")).json() if h["npm_id"] == 3)
    assert host["ignored"]
    r = await admin.patch_json(f"{base}/hosts/{host['id']}", {"ignored": False})
    assert r.status_code == 200
    await admin.post_json(f"{base}/sync", {})
    assert "Nas" in {a["name"] for a in (await admin.get("/api/v1/catalog")).json()}

    # Negeren via de host verwijdert het automatisch aangemaakte item meteen.
    host = next(h for h in (await admin.get(f"{base}/hosts")).json() if h["npm_id"] == 3)
    r = await admin.patch_json(f"{base}/hosts/{host['id']}", {"ignored": True})
    assert r.json()["application"] is None
    assert "Nas" not in {a["name"] for a in (await admin.get("/api/v1/catalog")).json()}


async def test_sync_failures_are_recorded(admin, fake_npm):
    org = await _org(admin)
    conn = await _conn(admin, org, secret="fout")
    base = f"/api/v1/organizations/{org['id']}/npm-connections/{conn['id']}"
    r = await admin.post_json(f"{base}/sync", {})
    assert r.status_code == 502
    assert "Aanmelden bij NPM mislukt" in r.json()["detail"]
    c = (await admin.get(base)).json()
    assert c["last_sync_status"] == "error" and "Invalid email or password" in c["last_sync_error"]

    fake_npm.requires_2fa = True
    await admin.patch_json(base, {"secret": "npm-secret"})
    r = await admin.post_json(f"{base}/sync", {})
    assert r.status_code == 502 and "2FA" in r.json()["detail"]

    fake_npm.requires_2fa = False
    fake_npm.down = True
    r = await admin.post_json(f"{base}/sync", {})
    assert r.status_code == 502 and "niet bereikbaar" in r.json()["detail"]

    fake_npm.down = False
    r = await admin.post_json(f"{base}/sync", {})
    assert r.status_code == 200
    assert (await admin.get(base)).json()["last_sync_error"] is None
    audit = (await admin.get("/api/v1/audit", params={"action": "npm.sync"})).json()["items"]
    assert [a["outcome"] for a in audit] == ["success", "failure", "failure", "failure"]


async def test_permissions_members_read_only_outsiders_see_nothing(admin, make_client, fake_npm):
    _seed(fake_npm)
    org = await _org(admin)
    other = await _org(admin, "beta")
    conn = await _conn(admin, org)
    await admin.post_json(f"/api/v1/organizations/{org['id']}/npm-connections/{conn['id']}/sync", {})

    member = await make_client()
    await member.login("member")
    mid = (await member.get("/api/v1/me")).json()["id"]
    await admin.post_json(f"/api/v1/organizations/{org['id']}/members", {"user_id": mid, "role": "member"})
    outsider = await make_client()
    await outsider.login("outsider")

    assert len((await member.get("/api/v1/catalog")).json()) == 3
    assert (await outsider.get("/api/v1/catalog")).json() == []
    assert (await outsider.get(f"/api/v1/organizations/{org['id']}/npm-connections")).status_code == 404
    assert (await member.get(f"/api/v1/organizations/{org['id']}/npm-connections")).status_code == 200
    r = await member.post_json(f"/api/v1/organizations/{org['id']}/npm-connections/{conn['id']}/sync", {})
    assert r.status_code == 403
    r = await member.post_json(f"/api/v1/organizations/{org['id']}/applications", {"name": "X"})
    assert r.status_code == 403
    # Koppeling van acme bestaat niet onder beta
    assert (
        await admin.get(f"/api/v1/organizations/{other['id']}/npm-connections/{conn['id']}")
    ).status_code == 404
    denied = (await admin.get("/api/v1/audit", params={"outcome": "denied"})).json()["items"]
    assert {d["action"] for d in denied} == {"npm.sync", "application.create"}


async def test_manual_application_crud(admin):
    org = await _org(admin)
    base = f"/api/v1/organizations/{org['id']}/applications"
    r = await admin.post_json(base, {"name": "Intranet", "url": "https://intra.lan", "auth_method": "saml"})
    assert r.status_code == 201, r.text
    app = r.json()
    assert (app["source"], app["status"], app["hosts"]) == ("manual", "protected", [])
    assert (await admin.post_json(base, {"name": "x", "url": "ftp://x"})).status_code == 422
    r = await admin.patch_json(f"{base}/{app['id']}", {"auth_method": "none"})
    assert r.json()["status"] == "unprotected"
    assert (await admin.delete_(f"{base}/{app['id']}")).status_code == 204
    assert (await admin.get(f"{base}/{app['id']}")).status_code == 404


async def test_scheduler_syncs_due_connections(admin, fake_npm, app):
    from app.core.config import get_settings
    from app.services import npm_scheduler
    from app.services.npm import NpmService

    _seed(fake_npm)
    org = await _org(admin)
    await _conn(admin, org)
    await _conn(admin, org, name="Uit", enabled=False)
    settings = get_settings().model_copy(update={"npm_sync_interval_minutes": 15})

    original = NpmService.__init__

    def patched(self, db, s, client_factory=None):
        original(self, db, s, app.state.npm_client_factory)

    NpmService.__init__ = patched
    try:
        assert await npm_scheduler.sync_due_connections(settings) == 1
        assert await npm_scheduler.sync_due_connections(settings) == 0  # net gedaan
    finally:
        NpmService.__init__ = original
    audit = (await admin.get("/api/v1/audit", params={"action": "npm.sync"})).json()["items"]
    assert audit[0]["actor_type"] == "system" and audit[0]["actor_label"] == "npm-sync"
