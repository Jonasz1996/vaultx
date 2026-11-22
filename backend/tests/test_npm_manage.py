"""NPM volledig beheren via de API: voorbeeld, beschermde Advanced-config, controle, rollback, journaal."""

from tests.fake_npm import proxy_host

PEM_CERT = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
PEM_KEY = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\n"
JONAS_ADVANCED = """location / {
    proxy_pass $forward_scheme://$server:$port;
    sub_filter '</head>' '<link rel="stylesheet" href="https://css.jbogaert.be/proxmox.css"></head>';
    sub_filter_once on;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    auth_request /outpost.goauthentik.io/auth/nginx;
}
"""
VAULTX_BLOCK = (
    "# >>> vaultx:authentik (beheerd door VaultX, niet met de hand wijzigen)\n"
    "location /outpost.goauthentik.io {\n    proxy_pass http://ak:9000/outpost.goauthentik.io;\n}\n"
    "# <<< vaultx:authentik\n"
)


async def _setup(admin, fake_npm, *, write=True):
    fake_npm.hosts = [
        proxy_host(1, ["grafana.domain.be"], forward_host="grafana", forward_port=3000, certificate_id=5),
        proxy_host(2, ["proxmox.domain.be"], forward_host="pve", advanced_config=JONAS_ADVANCED),
        proxy_host(3, ["beschermd.domain.be"], forward_host="app", advanced_config=VAULTX_BLOCK),
    ]
    fake_npm.certificates = [
        {
            "id": 5,
            "provider": "letsencrypt",
            "nice_name": "domain.be",
            "domain_names": ["*.domain.be", "domain.be"],
            "expires_on": "2027-01-01 00:00:00",
            "modified_on": "2026-10-05 10:00:00",
            "meta": {"certificate_key": "-----BEGIN PRIVATE KEY-----geheim"},
        }
    ]
    fake_npm.objects["access-lists"] = [
        {
            "id": 7,
            "name": "LAN",
            "satisfy_any": False,
            "pass_auth": False,
            "modified_on": "2026-10-05 10:00:00",
            "items": [{"username": "bob", "password": "", "hint": "g****"}],
            "clients": [{"address": "192.168.10.0/24", "directive": "allow"}],
            "passwords": {"bob": "geheim"},
        }
    ]
    r = await admin.post_json("/api/v1/organizations", {"slug": "acme", "name": "Acme"})
    org = r.json()
    body = {
        "name": "NPM",
        "base_url": "http://npm.lan:81",
        "identity": "admin@example.com",
        "secret": "npm-secret",
        "write_enabled": write,
    }
    r = await admin.post_json(f"/api/v1/organizations/{org['id']}/npm-connections", body)
    assert r.status_code == 201, r.text
    conn = r.json()
    base = f"/api/v1/organizations/{org['id']}/npm-connections/{conn['id']}"
    assert (await admin.post_json(f"{base}/sync", {})).status_code == 200
    return org, f"{base}/manage"


async def _do(client, base, kind, action, npm_id=None, data=None, **extra):
    body = {"action": action, "npm_id": npm_id, "data": data or {}, **extra}
    plan = await client.post_json(f"{base}/{kind}/preview", body)
    assert plan.status_code == 200, plan.text
    plan = plan.json()
    r = await client.post_json(f"{base}/{kind}/apply", {**body, "expected_modified_on": plan["modified_on"]})
    assert r.status_code == 200, r.text
    return plan, r.json()


def _codes(plan, level="block"):
    return {c["code"] for c in plan["checks"] if c["level"] == level}


async def test_overview_hides_secrets(admin, fake_npm):
    _, base = await _setup(admin, fake_npm)
    r = await admin.get(base)
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data) == {
        "proxy_host",
        "redirection_host",
        "dead_host",
        "stream",
        "access_list",
        "certificate",
    }
    assert [h["label"] for h in data["proxy_host"]] == [
        "beschermd.domain.be",
        "grafana.domain.be",
        "proxmox.domain.be",
    ]
    assert data["proxy_host"][0]["managed_blocks"] == ["authentik"]
    cert = data["certificate"][0]
    assert "meta" not in cert and cert["used_by"] == ["proxy host grafana.domain.be"]
    assert "geheim" not in r.text and "hint" not in r.text
    assert data["access_list"][0]["items"] == [{"username": "bob"}]


async def test_create_update_delete_proxy_host(admin, fake_npm):
    _, base = await _setup(admin, fake_npm)
    data = {
        "domain_names": ["nieuw.domain.be"],
        "forward_host": "nieuw",
        "forward_port": 8080,
        "certificate_id": 5,
        "ssl_forced": True,
    }
    plan, change = await _do(admin, base, "proxy_host", "create", data=data)
    assert plan["can_apply"] and plan["probe_url"] == "https://nieuw.domain.be/ via npm.lan:443"
    assert change["status"] == "applied", change
    assert change["object_type"] == "proxy_host" and change["probe_after"]["status"] == 200
    new_id = change["npm_id"]
    assert fake_npm.host(new_id)["forward_port"] == 8080
    # De hostlijst en catalogus zien de nieuwe host meteen.
    hosts = (await admin.get(base.replace("/manage", "/hosts"))).json()
    assert any(h["npm_id"] == new_id for h in hosts)

    plan, change = await _do(admin, base, "proxy_host", "update", new_id, {"forward_port": 9090})
    assert [c["field"] for c in plan["changes"]] == ["forward_port"]
    assert change["status"] == "applied" and fake_npm.puts[-1] == (new_id, {"forward_port": 9090})

    plan, change = await _do(admin, base, "proxy_host", "delete", new_id)
    assert change["status"] == "applied" and fake_npm.host(new_id) is None
    assert change["before"]["forward_port"] == 9090  # om later opnieuw aan te maken
    journal = (await admin.get(f"{base}/changes", params={"kind": "proxy_host"})).json()
    assert [c["action"] for c in journal] == ["delete", "update", "create"]


async def test_domain_conflicts_and_certificate_checks(admin, fake_npm):
    _, base = await _setup(admin, fake_npm)
    body = {
        "action": "create",
        "data": {"domain_names": ["grafana.domain.be"], "forward_host": "x", "forward_port": 1},
    }
    plan = (await admin.post_json(f"{base}/proxy_host/preview", body)).json()
    assert "domain_in_use" in _codes(plan) and not plan["can_apply"]
    body["data"] = {
        "domain_names": ["andere.be"],
        "forward_host": "x",
        "forward_port": 1,
        "certificate_id": 5,
    }
    plan = (await admin.post_json(f"{base}/proxy_host/preview", body)).json()
    assert plan["can_apply"] and "certificate_mismatch" in _codes(plan, "warn")
    body["data"]["certificate_id"] = 99
    plan = (await admin.post_json(f"{base}/proxy_host/preview", body)).json()
    assert "certificate_missing" in _codes(plan)


async def test_jonas_advanced_config_is_protected(admin, fake_npm):
    _, base = await _setup(admin, fake_npm)
    stripped = JONAS_ADVANCED.replace("    sub_filter_once on;\n", "").replace(
        "    sub_filter '</head>' '<link rel=\"stylesheet\" href=\"https://css.jbogaert.be/proxmox.css\"></head>';\n",
        "",
    )
    body = {"action": "update", "npm_id": 2, "data": {"advanced_config": stripped}}
    plan = (await admin.post_json(f"{base}/proxy_host/preview", body)).json()
    assert "removes_sub_filter" in _codes(plan) and not plan["can_apply"]
    # Uitvoeren zonder bevestiging: geweigerd, NPM ongemoeid.
    r = await admin.post_json(f"{base}/proxy_host/apply", body)
    assert r.json()["status"] == "refused" and not fake_npm.puts
    # Met bevestiging mag het wel.
    plan, change = await _do(
        admin, base, "proxy_host", "update", 2, {"advanced_config": stripped}, confirm_removals=True
    )
    assert "removes_sub_filter" in _codes(plan, "warn") and change["status"] == "applied"

    # Een gewone wijziging aan de rest van de host laat de Advanced-config byte voor byte staan.
    fake_npm.puts.clear()
    _, change = await _do(admin, base, "proxy_host", "update", 1, {"block_exploits": False})
    assert fake_npm.puts == [(1, {"block_exploits": False})]

    # Het VaultX-blok mag niemand via het beheerscherm aanpassen, ook niet met bevestiging.
    changed = VAULTX_BLOCK.replace("http://ak:9000", "http://ander:9000")
    body = {"action": "update", "npm_id": 3, "data": {"advanced_config": changed}, "confirm_removals": True}
    plan = (await admin.post_json(f"{base}/proxy_host/preview", body)).json()
    assert "managed_block_changed" in _codes(plan)
    body["data"]["advanced_config"] = VAULTX_BLOCK + "client_max_body_size 50m;\n"
    plan = (await admin.post_json(f"{base}/proxy_host/preview", body)).json()
    assert plan["can_apply"], plan["checks"]


async def test_nginx_error_and_bad_upstream_roll_back(admin, fake_npm):
    _, base = await _setup(admin, fake_npm)
    _, change = await _do(admin, base, "proxy_host", "update", 1, {"advanced_config": "bad_directive on;\n"})
    assert change["status"] == "rolled_back", change
    assert "unknown directive" in change["nginx_error"]
    assert fake_npm.host(1)["advanced_config"] == "" and fake_npm.host(1)["meta"]["nginx_online"]

    fake_npm.unreachable_upstreams.add("verkeerd")
    _, change = await _do(admin, base, "proxy_host", "update", 1, {"forward_host": "verkeerd"})
    assert change["status"] == "rolled_back" and "502" in change["message"]
    assert fake_npm.host(1)["forward_host"] == "grafana"

    # Een nieuwe host met een upstream die (nog) niet antwoordt, blijft staan met een waarschuwing.
    data = {"domain_names": ["later.domain.be"], "forward_host": "verkeerd", "forward_port": 80}
    _, change = await _do(admin, base, "proxy_host", "create", data=data)
    assert change["status"] == "applied" and "Controleer doelhost" in change["message"]


async def test_vaultx_hosts_cannot_be_deleted_here(admin, fake_npm):
    _, base = await _setup(admin, fake_npm)
    # Host 3 draagt een VaultX-blok maar staat niet in authentik_protections: verwijderen mag dus,
    # zolang VaultX hem niet publiceerde. Een gepubliceerde host weigert VaultX hier.
    from sqlalchemy import update

    from app.core.db import get_engine
    from app.models import DiscoveredHost

    async with get_engine().begin() as c:
        await c.execute(
            update(DiscoveredHost).where(DiscoveredHost.npm_id == 3).values(vaultx_published=True)
        )
    plan = (await admin.post_json(f"{base}/proxy_host/preview", {"action": "delete", "npm_id": 3})).json()
    assert "published_by_vaultx" in _codes(plan)


async def test_modified_since_preview_is_refused(admin, fake_npm):
    _, base = await _setup(admin, fake_npm)
    body = {"action": "update", "npm_id": 1, "data": {"forward_port": 1234}}
    plan = (await admin.post_json(f"{base}/proxy_host/preview", body)).json()
    fake_npm.host(1)["modified_on"] = "2026-11-22 10:00:00"
    r = await admin.post_json(
        f"{base}/proxy_host/apply", {**body, "expected_modified_on": plan["modified_on"]}
    )
    assert r.json()["status"] == "refused" and not fake_npm.puts


async def test_enable_disable(admin, fake_npm):
    _, base = await _setup(admin, fake_npm)
    _, change = await _do(admin, base, "proxy_host", "disable", 1)
    assert change["status"] == "applied" and fake_npm.host(1)["enabled"] is False
    plan = (await admin.post_json(f"{base}/proxy_host/preview", {"action": "disable", "npm_id": 1})).json()
    assert "no_changes" in _codes(plan)
    _, change = await _do(admin, base, "proxy_host", "enable", 1)
    assert change["status"] == "applied" and fake_npm.host(1)["enabled"] is True


async def test_certificates(admin, fake_npm):
    _, base = await _setup(admin, fake_npm)
    plan = (await admin.post_json(f"{base}/certificate/preview", {"action": "delete", "npm_id": 5})).json()
    assert "certificate_in_use" in _codes(plan)

    data = {"provider": "letsencrypt", "domain_names": ["*.nieuw.be"]}
    plan = (await admin.post_json(f"{base}/certificate/preview", {"action": "create", "data": data})).json()
    assert "wildcard_needs_dns" in _codes(plan)
    data = {"provider": "letsencrypt", "domain_names": ["nieuw.be"], "nice_name": "nieuw"}
    _, change = await _do(admin, base, "certificate", "create", data=data)
    assert change["status"] == "applied" and "Geldig tot" in change["message"]
    le_id = change["npm_id"]
    _, change = await _do(admin, base, "certificate", "renew", le_id)
    assert change["status"] == "applied" and fake_npm.renewed == [le_id]

    fake_npm.letsencrypt_error = "Some challenges have failed."
    data = {"provider": "letsencrypt", "domain_names": ["faalt.be"]}
    _, change = await _do(admin, base, "certificate", "create", data=data)
    assert change["status"] == "refused" and "challenges" in change["message"]

    data = {"provider": "other", "nice_name": "eigen", "certificate": PEM_CERT, "certificate_key": PEM_KEY}
    plan, change = await _do(admin, base, "certificate", "create", data=data)
    assert plan["certificate_check"]["cn"] == "eigen.domain.be"
    assert change["status"] == "applied" and fake_npm.uploads == [change["npm_id"]]
    assert "BEGIN PRIVATE KEY" not in str(change)

    fake_npm.fail_upload = True
    data["nice_name"] = "eigen2"
    _, change = await _do(admin, base, "certificate", "create", data=data)
    assert change["status"] == "rolled_back" and change["npm_id"] in fake_npm.deleted

    plan = await admin.post_json(
        f"{base}/certificate/preview",
        {"action": "create", "data": {**data, "certificate_key": "-----BEGIN PRIVATE KEY-----WRONG KEY"}},
    )
    assert "key_mismatch" in _codes(plan.json())


async def test_access_lists(admin, fake_npm):
    _, base = await _setup(admin, fake_npm)
    fake_npm.host(1)["access_list_id"] = 7
    plan = (await admin.post_json(f"{base}/access_list/preview", {"action": "delete", "npm_id": 7})).json()
    assert "access_list_in_use" in _codes(plan)

    # Een gebruiker toevoegen; bob houdt zijn wachtwoord (leeg = ongewijzigd, zoals in NPM).
    items = [{"username": "bob"}, {"username": "eve", "password": "nieuw-geheim"}]
    plan, change = await _do(admin, base, "access_list", "update", 7, {"items": items})
    assert change["status"] == "applied", change
    assert fake_npm.objects["access-lists"][0]["passwords"] == {"bob": "geheim", "eve": "nieuw-geheim"}
    assert "nieuw-geheim" not in str(change) and "nieuw-geheim" not in str(plan)

    plan = (
        await admin.post_json(
            f"{base}/access_list/preview",
            {"action": "update", "npm_id": 7, "data": {"items": [{"username": "zonder"}]}},
        )
    ).json()
    assert "missing_password" in _codes(plan)

    # Satisfy Any op een lijst van een host met forward auth zou Authentik omzeilen.
    fake_npm.host(2)["access_list_id"] = 7
    plan = (
        await admin.post_json(
            f"{base}/access_list/preview", {"action": "update", "npm_id": 7, "data": {"satisfy_any": True}}
        )
    ).json()
    assert "satisfy_any_forward_auth" in _codes(plan)

    data = {"name": "Thuis", "clients": [{"address": "10.0.0.0/8", "directive": "allow"}]}
    _, change = await _do(admin, base, "access_list", "create", data=data)
    assert change["status"] == "applied"
    _, change = await _do(admin, base, "access_list", "delete", change["npm_id"])
    assert change["status"] == "applied"


async def test_streams_redirects_and_404_hosts(admin, fake_npm):
    _, base = await _setup(admin, fake_npm)
    data = {"incoming_port": 2222, "forwarding_host": "git", "forwarding_port": 22}
    _, change = await _do(admin, base, "stream", "create", data=data)
    assert change["status"] == "applied" and change["domain"] == ":2222 → git:22"
    plan = (await admin.post_json(f"{base}/stream/preview", {"action": "create", "data": data})).json()
    assert "port_in_use" in _codes(plan)
    plan = (
        await admin.post_json(
            f"{base}/stream/preview", {"action": "create", "data": {**data, "incoming_port": 443}}
        )
    ).json()
    assert "reserved_port" in _codes(plan)

    data = {
        "domain_names": ["oud.domain.be"],
        "forward_domain_name": "nieuw.domain.be",
        "forward_http_code": 301,
    }
    plan, change = await _do(admin, base, "redirection_host", "create", data=data)
    assert plan["expect"] == "301 naar nieuw.domain.be"
    assert change["status"] == "applied" and change["probe_after"]["status"] == 301

    plan = (
        await admin.post_json(
            f"{base}/redirection_host/preview",
            {
                "action": "create",
                "data": {**data, "domain_names": ["lus.be"], "forward_domain_name": "lus.be"},
            },
        )
    ).json()
    assert "redirect_loop" in _codes(plan)

    _, change = await _do(admin, base, "dead_host", "create", data={"domain_names": ["weg.domain.be"]})
    assert change["status"] == "applied" and change["probe_after"]["status"] == 404


async def test_permissions_and_write_switch(admin, make_client, fake_npm):
    org, base = await _setup(admin, fake_npm, write=False)
    plan = (await admin.post_json(f"{base}/proxy_host/preview", {"action": "disable", "npm_id": 1})).json()
    assert "write_disabled" in _codes(plan)
    r = await admin.post_json(f"{base}/proxy_host/apply", {"action": "disable", "npm_id": 1})
    assert r.status_code == 422

    member = await make_client()
    await member.login("member")
    mid = (await member.get("/api/v1/me")).json()["id"]
    await admin.post_json(f"/api/v1/organizations/{org['id']}/members", {"user_id": mid, "role": "member"})
    assert (await member.get(base)).status_code == 403
    assert (
        await member.post_json(f"{base}/proxy_host/preview", {"action": "disable", "npm_id": 1})
    ).status_code == 403
    assert (await member.get(f"{base}/changes")).status_code == 403

    bad = await admin.post_json(
        f"{base}/proxy_host/preview", {"action": "create", "data": {"forward_port": 0}}
    )
    assert bad.status_code == 422
