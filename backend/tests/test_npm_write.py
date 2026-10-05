"""Authentik-bescherming zetten via de API: controle, rollback, journaal en rechten."""

import copy
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.core.db import get_engine
from tests.fake_npm import proxy_host

OUTPOST = "http://authentik-server:9000"


async def _setup(admin, fake_npm, *, outpost=OUTPOST, write=True, hosts=None):
    fake_npm.hosts = hosts or [
        proxy_host(
            1,
            ["grafana.domain.be"],
            forward_host="grafana",
            forward_port=3000,
            advanced_config="# vaultx.tags = monitoring\n",
            certificate_id=1,
            ssl_forced=True,
        ),
        proxy_host(2, ["*.wild.be"], forward_host="wild"),
    ]
    r = await admin.post_json("/api/v1/organizations", {"slug": "acme", "name": "Acme"})
    org = r.json()
    body = {
        "name": "NPM",
        "base_url": "http://npm.lan:81",
        "identity": "admin@example.com",
        "secret": "npm-secret",
        "write_enabled": write,
        "authentik_outpost_url": outpost,
    }
    r = await admin.post_json(f"/api/v1/organizations/{org['id']}/npm-connections", body)
    assert r.status_code == 201, r.text
    conn = r.json()
    base = f"/api/v1/organizations/{org['id']}/npm-connections/{conn['id']}"
    assert (await admin.post_json(f"{base}/sync", {})).status_code == 200
    hosts = {h["npm_id"]: h for h in (await admin.get(f"{base}/hosts")).json()}
    return org, conn, base, hosts


async def _protect(client, base, host_id, action="protect", **extra):
    plan = (await client.get(f"{base}/hosts/{host_id}/protection", params={"action": action})).json()
    body = {"action": action, "expected_modified_on": plan.get("modified_on"), **extra}
    return plan, await client.post_json(f"{base}/hosts/{host_id}/protection", body)


async def test_protect_and_unprotect_roundtrip(admin, fake_npm):
    org, conn, base, hosts = await _setup(admin, fake_npm)
    original = copy.deepcopy(fake_npm.host(1))
    assert conn["write_enabled"] and conn["authentik_outpost_url"] == OUTPOST

    plan, r = await _protect(admin, base, hosts[1]["id"])
    assert plan["can_apply"] and plan["probe_url"] == "https://grafana.domain.be/ via npm.lan:443"
    assert any("Custom location '/' aanmaken" in s for s in plan["steps"])
    assert plan["before"]["locations"] == [] and plan["after"]["locations"][0]["path"] == "/"
    assert r.status_code == 200, r.text
    change = r.json()
    assert change["status"] == "applied", change
    assert change["probe_before"]["status"] == 200
    assert change["probe_after"]["status"] == 302 and change["probe_after"]["summary"].endswith("Authentik")
    assert len(fake_npm.puts) == 1

    # De catalogus ziet de host meteen als beschermd, zonder nieuwe sync.
    host = next(h for h in (await admin.get(f"{base}/hosts")).json() if h["npm_id"] == 1)
    assert host["vaultx_managed"] and host["forward_auth"] and host["detected_auth"] == "forward_auth"
    app = next(a for a in (await admin.get("/api/v1/catalog")).json() if a["name"] == "Grafana")
    assert (app["auth_method"], app["status"]) == ("forward_auth", "protected")

    # Een sync achteraf houdt dat zo.
    assert (await admin.post_json(f"{base}/sync", {})).status_code == 200
    host = next(h for h in (await admin.get(f"{base}/hosts")).json() if h["npm_id"] == 1)
    assert host["vaultx_managed"] and not host["warnings"]

    plan, r = await _protect(admin, base, hosts[1]["id"], "unprotect")
    assert plan["can_apply"] and any(c["code"] == "unprotected_after" for c in plan["checks"])
    change = r.json()
    assert change["status"] == "applied", change
    assert fake_npm.host(1)["locations"] == original["locations"]
    assert fake_npm.host(1)["advanced_config"].rstrip() == original["advanced_config"].rstrip()

    changes = (await admin.get(f"{base}/changes")).json()
    assert [(c["action"], c["status"]) for c in changes] == [("unprotect", "applied"), ("protect", "applied")]
    audit = (await admin.get("/api/v1/audit", params={"action": "npm_host."})).json()["items"]
    assert [(a["action"], a["outcome"]) for a in audit][:2] == [
        ("npm_host.unprotect", "success"),
        ("npm_host.protect", "success"),
    ]


async def test_nginx_error_is_rolled_back(admin, fake_npm):
    org, conn, base, hosts = await _setup(admin, fake_npm, outpost="http://does-not-exist:9000")
    original = copy.deepcopy(fake_npm.host(1))
    _, r = await _protect(admin, base, hosts[1]["id"])
    change = r.json()
    assert change["status"] == "rolled_back", change
    assert "host not found" in change["nginx_error"]
    assert "teruggezet" in change["message"]
    assert len(fake_npm.puts) == 2  # wijziging + terugzetten
    restored = fake_npm.host(1)
    assert restored["meta"]["nginx_online"] is True
    assert (restored["advanced_config"], restored["locations"]) == (
        original["advanced_config"],
        original["locations"],
    )
    host = next(h for h in (await admin.get(f"{base}/hosts")).json() if h["npm_id"] == 1)
    assert not host["vaultx_managed"] and host["nginx_online"]
    audit = (await admin.get("/api/v1/audit", params={"action": "npm_host.protect"})).json()["items"]
    assert audit[0]["outcome"] == "failure" and audit[0]["details"]["status"] == "rolled_back"


async def test_failed_probe_is_rolled_back(admin, fake_npm):
    org, conn, base, hosts = await _setup(admin, fake_npm, outpost="http://dead-outpost:9000")
    _, r = await _protect(admin, base, hosts[1]["id"])
    change = r.json()
    assert change["status"] == "rolled_back", change
    assert change["probe_after"]["status"] == 500
    assert "outpost" in change["message"]
    assert fake_npm.host(1)["locations"] == []
    assert "vaultx:authentik" not in fake_npm.host(1)["advanced_config"]


async def test_failed_rollback_is_reported(admin, fake_npm):
    org, conn, base, hosts = await _setup(admin, fake_npm, outpost="http://does-not-exist:9000")
    fake_npm.fail_puts_after = 1
    _, r = await _protect(admin, base, hosts[1]["id"])
    change = r.json()
    assert change["status"] == "rollback_failed", change
    assert "NIET gelukt" in change["message"]
    # De momentopname om met de hand terug te zetten staat in het journaal.
    assert change["before"]["advanced_config"] == "# vaultx.tags = monitoring\n"


async def test_refuses_without_changing_npm(admin, fake_npm):
    org, conn, base, hosts = await _setup(admin, fake_npm)
    hid = hosts[1]["id"]

    # Host gewijzigd in NPM na het voorbeeld.
    r = await admin.post_json(
        f"{base}/hosts/{hid}/protection", {"action": "protect", "expected_modified_on": "2020-01-01 00:00:00"}
    )
    assert r.json()["status"] == "refused" and "gewijzigd" in r.json()["message"]

    # VaultX kan de host niet aanspreken: zonder controle niet uitvoeren...
    fake_npm.probe_down = True
    _, r = await _protect(admin, base, hid)
    assert r.json()["status"] == "refused" and "controleadres" in r.json()["message"]
    assert fake_npm.puts == []

    # ...tenzij de gebruiker uitdrukkelijk kiest voor zonder controle.
    _, r = await _protect(admin, base, hid, verify=False)
    change = r.json()
    assert (change["status"], change["verified"]) == ("applied", False)
    assert "Niet gecontroleerd" in change["message"]

    # Wildcard-host: geen controle mogelijk.
    plan = (await admin.get(f"{base}/hosts/{hosts[2]['id']}/protection")).json()
    assert plan["probe_url"] is None and any(c["code"] == "no_probe" for c in plan["checks"])


async def test_blocked_plan_and_write_disabled(admin, fake_npm):
    org, conn, base, hosts = await _setup(
        admin,
        fake_npm,
        write=False,
        hosts=[proxy_host(1, ["ha.domain.be"], access_list={"id": 3, "name": "LAN", "satisfy_any": True})],
    )
    plan = (await admin.get(f"{base}/hosts/{hosts[1]['id']}/protection")).json()
    assert not plan["can_apply"]
    assert {c["code"] for c in plan["checks"] if c["level"] == "block"} == {"satisfy_any", "write_disabled"}
    r = await admin.post_json(f"{base}/hosts/{hosts[1]['id']}/protection", {"action": "protect"})
    assert r.status_code == 422 and "staat uit" in r.json()["detail"]

    r = await admin.patch_json(base, {"write_enabled": True})
    assert r.status_code == 200 and r.json()["write_enabled"]
    _, r = await _protect(admin, base, hosts[1]["id"])
    assert r.json()["status"] == "refused" and "Satisfy Any" in r.json()["message"]
    assert fake_npm.puts == []


async def test_connection_write_settings_validation(admin, fake_npm):
    org, conn, base, hosts = await _setup(admin, fake_npm)
    r = await admin.patch_json(base, {"authentik_outpost_url": "http://x:9000/pad"})
    assert r.status_code == 422
    r = await admin.patch_json(base, {"authentik_outpost_url": "http://x;y"})
    assert r.status_code == 422
    r = await admin.patch_json(base, {"probe_host": "https://npm.lan"})
    assert r.status_code == 422
    r = await admin.patch_json(base, {"probe_host": "192.168.1.10", "probe_https_port": 8443})
    assert r.status_code == 200 and r.json()["probe_host"] == "192.168.1.10"
    plan = (await admin.get(f"{base}/hosts/{hosts[1]['id']}/protection")).json()
    assert plan["probe_url"] == "https://grafana.domain.be/ via 192.168.1.10:8443"
    r = await admin.patch_json(base, {"authentik_outpost_url": "", "probe_host": ""})
    assert r.status_code == 200
    assert (r.json()["authentik_outpost_url"], r.json()["probe_host"]) == (None, None)
    plan = (await admin.get(f"{base}/hosts/{hosts[1]['id']}/protection")).json()
    assert [c["code"] for c in plan["checks"]] == ["no_outpost_url"]


async def test_members_cannot_write(admin, make_client, fake_npm):
    org, conn, base, hosts = await _setup(admin, fake_npm)
    member = await make_client()
    await member.login("member")
    mid = (await member.get("/api/v1/me")).json()["id"]
    await admin.post_json(f"/api/v1/organizations/{org['id']}/members", {"user_id": mid, "role": "member"})
    hid = hosts[1]["id"]
    assert (await member.get(f"{base}/hosts/{hid}/protection")).status_code == 403
    r = await member.post_json(f"{base}/hosts/{hid}/protection", {"action": "protect"})
    assert r.status_code == 403
    assert (await member.get(f"{base}/changes")).status_code == 403
    assert fake_npm.puts == []
    audit = (await admin.get("/api/v1/audit", params={"action": "npm_host.protect"})).json()["items"]
    assert audit and all(a["outcome"] == "denied" for a in audit)


async def test_one_change_per_host_at_a_time(admin, fake_npm):
    org, conn, base, hosts = await _setup(admin, fake_npm)
    hid = hosts[1]["id"]
    async with get_engine().begin() as c:
        await c.execute(
            text(
                "INSERT INTO npm_changes (id, organization_id, connection_id, host_id, npm_id, domain,"
                " action, status, verified, before, created_at) VALUES (gen_random_uuid(), :org, :conn,"
                " :host, 1, 'x', 'protect', 'running', true, '{}', :ts)"
            ),
            {"org": org["id"], "conn": conn["id"], "host": hid, "ts": datetime.now(UTC)},
        )
    _, r = await _protect(admin, base, hid)
    assert r.status_code == 409

    # Een wijziging die al lang op 'running' staat, is onderbroken en blokkeert niet meer.
    async with get_engine().begin() as c:
        await c.execute(
            text("UPDATE npm_changes SET created_at = :ts"), {"ts": datetime.now(UTC) - timedelta(hours=1)}
        )
    _, r = await _protect(admin, base, hid)
    assert r.json()["status"] == "applied"
    statuses = sorted(c["status"] for c in (await admin.get(f"{base}/changes")).json())
    assert statuses == ["applied", "interrupted"]
