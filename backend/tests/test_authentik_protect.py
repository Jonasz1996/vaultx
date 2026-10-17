"""Fase 4: bij "Beschermen met Authentik" ook de Authentik-kant aanmaken, terugdraaien en opruimen."""

import pytest

from app.services import npm_write
from tests.fake_authentik import EMBEDDED, FakeAuthentik
from tests.fake_npm import proxy_host

OUTPOST_URL = "http://authentik-server:9000"


@pytest.fixture(autouse=True)
def fast_probe(monkeypatch):
    monkeypatch.setattr(npm_write, "PROBE_ATTEMPTS_AFTER_AUTHENTIK", 2)
    monkeypatch.setattr(npm_write, "PROBE_DELAY_AFTER_AUTHENTIK", 0)


async def _setup(
    admin, fake_npm, fake_authentik: FakeAuthentik, *, outpost_pk=EMBEDDED, outpost_url=OUTPOST_URL
):
    fake_npm.authentik = fake_authentik
    fake_npm.hosts = [
        proxy_host(
            1,
            ["grafana.domain.be"],
            forward_host="grafana",
            forward_port=3000,
            certificate_id=1,
            ssl_forced=True,
        ),
        proxy_host(2, ["wiki.domain.be", "docs.domain.be"], forward_host="wiki"),
    ]
    org = (await admin.post_json("/api/v1/organizations", {"slug": "acme", "name": "Acme"})).json()
    body = {
        "name": "NPM",
        "base_url": "http://npm.lan:81",
        "identity": "admin@example.com",
        "secret": "npm-secret",
        "write_enabled": True,
        "authentik_outpost_url": outpost_url,
        "authentik_outpost_pk": outpost_pk,
    }
    r = await admin.post_json(f"/api/v1/organizations/{org['id']}/npm-connections", body)
    assert r.status_code == 201, r.text
    conn = r.json()
    base = f"/api/v1/organizations/{org['id']}/npm-connections/{conn['id']}"
    assert (await admin.post_json(f"{base}/sync", {})).status_code == 200
    hosts = {h["npm_id"]: h for h in (await admin.get(f"{base}/hosts")).json()}
    return org, conn, base, hosts


async def _preview(client, base, host_id, action="protect", access="organization"):
    r = await client.get(f"{base}/hosts/{host_id}/protection", params={"action": action, "access": access})
    assert r.status_code == 200, r.text
    return r.json()


async def _apply(client, base, host_id, action="protect", access="organization"):
    plan = await _preview(client, base, host_id, action, access)
    body = {"action": action, "expected_modified_on": plan["modified_on"], "access": access}
    r = await client.post_json(f"{base}/hosts/{host_id}/protection", body)
    assert r.status_code == 200, r.text
    return plan, r.json()


def _codes(plan):
    return [c["code"] for c in plan["checks"]]


async def test_outposts_endpoint(admin, make_client, fake_authentik):
    org = (await admin.post_json("/api/v1/organizations", {"slug": "acme", "name": "Acme"})).json()
    r = await admin.get(f"/api/v1/organizations/{org['id']}/authentik/outposts")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["configured"] and data["api_url"] == "http://authentik.test"
    assert data["outposts"] == [
        {
            "pk": EMBEDDED,
            "name": "authentik Embedded Outpost",
            "managed": "goauthentik.io/outposts/embedded",
            "authentik_host": "https://auth.domain.be",
            "provider_count": 0,
        }
    ]
    fake_authentik.down = True
    data = (await admin.get(f"/api/v1/organizations/{org['id']}/authentik/outposts")).json()
    assert data["outposts"] == [] and "niet bereikbaar" in data["error"]

    member = await make_client()
    await member.login("bob", groups=["vaultx:acme"])
    assert (await member.get(f"/api/v1/organizations/{org['id']}/authentik/outposts")).status_code == 403


async def test_protect_creates_authentik_side_and_unprotect_removes_it(admin, fake_npm, fake_authentik):
    fake_authentik.add_group("vaultx:acme")
    fake_authentik.add_group("vaultx:acme:admin")
    fake_authentik.add_group("vaultx:acme2")
    fake_authentik.add_group("vaultx:other")
    org, conn, base, hosts = await _setup(admin, fake_npm, fake_authentik)
    assert conn["authentik_outpost_pk"] == EMBEDDED

    plan = await _preview(admin, base, hosts[1]["id"])
    assert plan["can_apply"], plan["checks"]
    assert plan["steps"][:4] == [
        "Authentik: proxy provider 'VaultX: grafana.domain.be' aanmaken (forward auth, single application, "
        "externe URL https://grafana.domain.be).",
        "Authentik: applicatie 'Grafana' (vaultx-grafana-domain-be) aanmaken.",
        "Authentik: toegang enkel voor 'vaultx:acme', 'vaultx:acme:admin'.",
        "Authentik: provider toewijzen aan outpost 'authentik Embedded Outpost'.",
    ]
    assert plan["authentik"]["groups"] == ["vaultx:acme", "vaultx:acme:admin"]
    assert plan["authentik"]["access_label"] == "leden van organisatie acme"
    assert not fake_authentik.providers, "een voorbeeld wijzigt niets"

    _, change = await _apply(admin, base, hosts[1]["id"])
    assert change["status"] == "applied", change
    assert change["probe_after"]["status"] == 302
    (provider,) = fake_authentik.providers.values()
    assert provider["name"] == "VaultX: grafana.domain.be"
    assert (provider["mode"], provider["external_host"]) == ("forward_single", "https://grafana.domain.be")
    assert provider["assigned_application_slug"] == "vaultx-grafana-domain-be"
    assert fake_authentik.outposts[EMBEDDED]["providers"] == [provider["pk"]]
    assert fake_authentik.bindings_for("vaultx-grafana-domain-be") == ["vaultx:acme", "vaultx:acme:admin"]
    state = change["authentik"]["state"]
    assert state["provider_created"] and state["application_created"] and state["outpost_assigned"]

    audit = (await admin.get("/api/v1/audit", params={"action": "npm_host.protect"})).json()["items"][0]
    assert audit["details"]["authentik"]["provider"] == "VaultX: grafana.domain.be"
    assert audit["details"]["authentik"]["groups"] == ["vaultx:acme", "vaultx:acme:admin"]

    # Opnieuw beschermen kan niet; weghalen toont ook wat er in Authentik verdwijnt.
    plan = await _preview(admin, base, hosts[1]["id"])
    assert "already_managed" in _codes(plan)
    plan = await _preview(admin, base, hosts[1]["id"], "unprotect")
    assert plan["steps"][:2] == [
        "Authentik: applicatie 'Grafana' (vaultx-grafana-domain-be) verwijderen.",
        "Authentik: proxy provider 'VaultX: grafana.domain.be' verwijderen.",
    ]
    _, change = await _apply(admin, base, hosts[1]["id"], "unprotect")
    assert change["status"] == "applied", change
    assert "In Authentik verwijderd" in change["message"]
    assert not fake_authentik.providers and not fake_authentik.applications
    assert fake_authentik.outposts[EMBEDDED]["providers"] == [] and not fake_authentik.bindings

    # Nog eens beschermen werkt weer van nul.
    _, change = await _apply(admin, base, hosts[1]["id"])
    assert change["status"] == "applied", change
    assert len(fake_authentik.providers) == 1


async def test_access_choices(admin, fake_npm, fake_authentik):
    org, conn, base, hosts = await _setup(admin, fake_npm, fake_authentik)
    plan = await _preview(admin, base, hosts[1]["id"])
    assert not plan["can_apply"] and "ak_no_group" in _codes(plan)
    assert "'vaultx:acme'" in next(c["message"] for c in plan["checks"] if c["code"] == "ak_no_group")

    r = await admin.get(f"{base}/hosts/{hosts[1]['id']}/protection", params={"access": "iedereen"})
    assert r.status_code == 422

    # Team: moet in VaultX bestaan, en de groep vaultx:acme/ops in Authentik.
    plan = await _preview(admin, base, hosts[1]["id"], access="team:ops")
    assert "ak_team_unknown" in _codes(plan)
    r = await admin.post_json(f"/api/v1/organizations/{org['id']}/teams", {"slug": "ops", "name": "Ops"})
    assert r.status_code == 201, r.text
    fake_authentik.add_group("vaultx:acme/ops")
    fake_authentik.add_group("vaultx:acme/ops:maintainer")
    fake_authentik.add_group("vaultx:acme/opsx")
    plan = await _preview(admin, base, hosts[1]["id"], access="team:ops")
    assert plan["can_apply"] and plan["authentik"]["groups"] == [
        "vaultx:acme/ops",
        "vaultx:acme/ops:maintainer",
    ]

    # Iedereen: geen binding, wel een waarschuwing. Twee domeinen: waarschuwing voor het tweede.
    plan, change = await _apply(admin, base, hosts[2]["id"], access="all")
    assert {"ak_access_all", "ak_extra_domains"} <= set(_codes(plan))
    assert change["status"] == "applied", change
    (provider,) = fake_authentik.providers.values()
    assert provider["external_host"] == "http://wiki.domain.be"
    assert fake_authentik.bindings == []


async def test_failed_check_rolls_back_npm_and_authentik(admin, fake_npm, fake_authentik):
    fake_authentik.add_group("vaultx:acme")
    org, conn, base, hosts = await _setup(
        admin, fake_npm, fake_authentik, outpost_url="http://dead-outpost:9000"
    )
    _, change = await _apply(admin, base, hosts[1]["id"])
    assert change["status"] == "rolled_back", change
    assert "Ook in Authentik is weer weg" in change["message"]
    assert change["authentik"]["undone"]
    assert not fake_authentik.providers and not fake_authentik.applications and not fake_authentik.bindings
    assert fake_npm.host(1)["advanced_config"] == "" and fake_npm.host(1)["locations"] == []
    host = next(h for h in (await admin.get(f"{base}/hosts")).json() if h["npm_id"] == 1)
    assert not host["vaultx_managed"]


async def test_outpost_that_does_not_know_the_domain_is_rolled_back(admin, fake_npm, fake_authentik):
    """Zonder Authentik-automatisering (fase 3) faalt de controle als de outpost het domein niet kent."""
    org, conn, base, hosts = await _setup(admin, fake_npm, fake_authentik, outpost_pk=None)
    plan = await _preview(admin, base, hosts[1]["id"])
    assert "authentik_manual" in _codes(plan) and plan["authentik"] is None
    _, change = await _apply(admin, base, hosts[1]["id"])
    assert change["status"] == "rolled_back" and change["authentik"] is None
    assert "Proxy Provider" in change["message"]


async def test_authentik_error_midway_undoes_and_leaves_npm_alone(admin, fake_npm, fake_authentik):
    fake_authentik.add_group("vaultx:acme")
    org, conn, base, hosts = await _setup(admin, fake_npm, fake_authentik)
    fake_authentik.fail["POST /policies/bindings/"] = (403, {"detail": "You do not have permission."})
    _, change = await _apply(admin, base, hosts[1]["id"])
    assert change["status"] == "refused", change
    assert "mag dit niet" in change["message"] and "NPM is niet gewijzigd" in change["message"]
    assert not fake_authentik.providers and not fake_authentik.applications
    assert fake_npm.puts == []

    # Lukt ook het opruimen niet: handwerk nodig, en het journaal zegt wat.
    fake_authentik.fail["DELETE /providers/proxy/"] = (500, {"detail": "boom"})
    _, change = await _apply(admin, base, hosts[1]["id"])
    assert change["status"] == "rollback_failed", change
    assert "verwijder met de hand" in change["message"] and "VaultX: grafana.domain.be" in change["message"]
    assert len(fake_authentik.providers) == 1 and not fake_authentik.applications
    assert fake_npm.puts == []


async def test_reuses_existing_provider_with_application(admin, fake_npm, fake_authentik):
    existing = fake_authentik.add_provider("https://grafana.domain.be", name="Grafana", app="grafana")
    org, conn, base, hosts = await _setup(admin, fake_npm, fake_authentik)
    plan = await _preview(admin, base, hosts[1]["id"])
    assert plan["can_apply"], plan["checks"]
    assert {"ak_provider_reused", "ak_access_existing"} <= set(_codes(plan))
    assert not any(s.startswith("Authentik:") for s in plan["steps"])
    _, change = await _apply(admin, base, hosts[1]["id"])
    assert change["status"] == "applied", change
    assert list(fake_authentik.providers) == [existing["pk"]] and list(fake_authentik.applications) == [
        "grafana"
    ]

    _, change = await _apply(admin, base, hosts[1]["id"], "unprotect")
    assert change["status"] == "applied", change
    assert "In Authentik" not in change["message"]
    assert list(fake_authentik.providers) == [existing["pk"]]
    assert fake_authentik.outposts[EMBEDDED]["providers"] == [existing["pk"]]


async def test_reuses_provider_without_app_and_outpost(admin, fake_npm, fake_authentik):
    fake_authentik.add_group("vaultx:acme")
    existing = fake_authentik.add_provider("https://grafana.domain.be", name="Mijn Grafana", outpost=None)
    org, conn, base, hosts = await _setup(admin, fake_npm, fake_authentik)
    plan = await _preview(admin, base, hosts[1]["id"])
    assert [s for s in plan["steps"] if s.startswith("Authentik:")] == [
        "Authentik: applicatie 'Grafana' (vaultx-grafana-domain-be) aanmaken.",
        "Authentik: toegang enkel voor 'vaultx:acme'.",
        "Authentik: provider toewijzen aan outpost 'authentik Embedded Outpost'.",
    ]
    _, change = await _apply(admin, base, hosts[1]["id"])
    assert change["status"] == "applied", change
    assert fake_authentik.outposts[EMBEDDED]["providers"] == [existing["pk"]]

    plan = await _preview(admin, base, hosts[1]["id"], "unprotect")
    assert plan["steps"][:2] == [
        "Authentik: provider 'Mijn Grafana' van outpost 'authentik Embedded Outpost' halen.",
        "Authentik: applicatie 'Grafana' (vaultx-grafana-domain-be) verwijderen.",
    ]
    _, change = await _apply(admin, base, hosts[1]["id"], "unprotect")
    assert change["status"] == "applied", change
    assert list(fake_authentik.providers) == [existing["pk"]] and not fake_authentik.applications
    assert fake_authentik.outposts[EMBEDDED]["providers"] == []


async def test_domain_level_provider_and_blocking_cases(admin, fake_npm, fake_authentik):
    fake_authentik.add_provider(
        "https://auth.domain.be", mode="forward_domain", cookie_domain="domain.be", app="domein"
    )
    org, conn, base, hosts = await _setup(admin, fake_npm, fake_authentik)
    plan = await _preview(admin, base, hosts[1]["id"])
    assert plan["can_apply"] and "ak_domain_level" in _codes(plan)
    _, change = await _apply(admin, base, hosts[1]["id"])
    assert change["status"] == "applied", change
    assert len(fake_authentik.providers) == 1

    # Provider in proxy-modus voor het domein: VaultX blijft eraf.
    fake_authentik.add_provider("http://wiki.domain.be", mode="proxy", name="Wiki proxy")
    plan = await _preview(admin, base, hosts[2]["id"])
    assert not plan["can_apply"] and "ak_provider_proxy_mode" in _codes(plan)


async def test_authentik_problems_block_the_preview(admin, fake_npm, fake_authentik):
    org, conn, base, hosts = await _setup(admin, fake_npm, fake_authentik)
    fake_authentik.fail["GET /outposts/"] = (401, {"detail": "Token invalid/expired"})
    plan = await _preview(admin, base, hosts[1]["id"])
    assert not plan["can_apply"] and "authentik_error" in _codes(plan)
    assert "API-token" in next(c["message"] for c in plan["checks"] if c["code"] == "authentik_error")
    fake_authentik.fail.clear()

    del fake_authentik.outposts[EMBEDDED]
    plan = await _preview(admin, base, hosts[1]["id"])
    assert "ak_outpost_missing" in _codes(plan)

    r = await admin.patch_json(base, {"authentik_outpost_pk": "geen-uuid"})
    assert r.status_code == 422
    r = await admin.patch_json(base, {"authentik_outpost_pk": ""})
    assert r.status_code == 200 and r.json()["authentik_outpost_pk"] is None
