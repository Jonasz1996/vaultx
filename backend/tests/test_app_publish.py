"""Fase 6: een app publiceren (NPM-host + Authentik + catalogus) en weer depubliceren."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services import npm_write
from app.services.npm_publish import PublishInput, covers, label_lines, plan_publish, theme_url
from tests.fake_authentik import EMBEDDED, FakeAuthentik
from tests.fake_npm import proxy_host

OUTPOST_URL = "http://authentik-server:9000"
SECRET_KEY = "-----BEGIN PRIVATE KEY-----\ngeheim\n-----END PRIVATE KEY-----\n"


@pytest.fixture(autouse=True)
def fast_probe(monkeypatch):
    monkeypatch.setattr(npm_write, "PROBE_ATTEMPTS_AFTER_AUTHENTIK", 2)
    monkeypatch.setattr(npm_write, "PROBE_DELAY_AFTER_AUTHENTIK", 0)
    monkeypatch.setattr(npm_write, "PROBE_DELAY_SECONDS", 0)


def _cert(cert_id: int, names: list[str], days: int = 60) -> dict:
    expires = (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "id": cert_id,
        "provider": "other",
        "nice_name": names[0],
        "domain_names": names,
        "expires_on": expires,
        "meta": {"certificate": "-----BEGIN CERTIFICATE-----", "certificate_key": SECRET_KEY},
    }


async def _setup(admin, fake_npm, fake_authentik: FakeAuthentik, *, outpost_pk=EMBEDDED, **extra):
    fake_npm.authentik = fake_authentik
    fake_npm.hosts = [proxy_host(1, ["wiki.domain.be"], forward_host="wiki")]
    fake_npm.certificates = [
        _cert(3, ["*.domain.be"]),
        _cert(4, ["other.be"]),
        _cert(5, ["old.domain.be"], -1),
    ]
    fake_authentik.add_group("vaultx:acme")
    org = (await admin.post_json("/api/v1/organizations", {"slug": "acme", "name": "Acme"})).json()
    body = {
        "name": "NPM",
        "base_url": "http://npm.lan:81",
        "identity": "admin@example.com",
        "secret": "npm-secret",
        "write_enabled": True,
        "authentik_outpost_url": OUTPOST_URL,
        "authentik_outpost_pk": outpost_pk,
        **extra,
    }
    r = await admin.post_json(f"/api/v1/organizations/{org['id']}/npm-connections", body)
    assert r.status_code == 201, r.text
    conn = r.json()
    base = f"/api/v1/organizations/{org['id']}/npm-connections/{conn['id']}"
    assert (await admin.post_json(f"{base}/sync", {})).status_code == 200
    return org, conn, base


def _request(**overrides) -> dict:
    return {
        "name": "Grafana",
        "domain": "grafana.domain.be",
        "forward_scheme": "http",
        "forward_host": "grafana",
        "forward_port": 3000,
        "certificate_id": 3,
        "theme_css_url": "https://css.domain.be/grafana.css",
        **overrides,
    }


def _codes(plan):
    return [c["code"] for c in plan["checks"]]


async def _preview(client, base, **overrides):
    r = await client.post_json(f"{base}/publish/preview", _request(**overrides))
    assert r.status_code == 200, r.text
    return r.json()


async def _publish(client, base, **overrides):
    r = await client.post_json(f"{base}/publish", _request(**overrides))
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- pure functies


def test_certificate_coverage_and_theme_template():
    assert covers(["*.domain.be"], "app.domain.be")
    assert not covers(["*.domain.be"], "a.b.domain.be")
    assert not covers(["*.domain.be"], "domain.be")
    assert covers(["domain.be", "www.domain.be"], "www.domain.be")
    assert not covers(["*.domain.be"], "app.otherdomain.be")
    assert (
        theme_url("https://css.example.be/{app}.css", "Proxmox VE") == "https://css.example.be/proxmox-ve.css"
    )
    assert theme_url(None, "x") is None


def test_labels_cannot_inject_nginx_config():
    data = PublishInput(
        name="App\n}\nlocation / { return 200; }",
        domain="a.b",
        forward_scheme="http",
        forward_host="a",
        forward_port=1,
        description="x; y",
    )
    labels = label_lines(data)
    assert all(line.startswith("#") for line in labels.splitlines())
    assert "{" not in labels and ";" not in labels


def test_plan_without_authentik_has_theme_and_headers_in_root_location():
    data = PublishInput(
        name="Wiki",
        domain="new.domain.be",
        forward_scheme="http",
        forward_host="wiki",
        forward_port=80,
        theme_css_url="https://css.domain.be/wiki.css",
        protect=False,
    )
    plan = plan_publish(data, existing_hosts=[], certificates=[], outpost_url=None)
    assert not plan.blocked
    assert {"unprotected", "no_tls"} <= {c.code for c in plan.checks}
    (root,) = plan.body["locations"]
    assert root["path"] == "/" and "auth_request" not in root["advanced_config"]
    assert "sub_filter '</head>'" in root["advanced_config"]
    assert 'add_header X-Frame-Options "SAMEORIGIN" always;' in root["advanced_config"]
    assert plan.body["certificate_id"] == 0 and not plan.body["ssl_forced"]


# ---------------------------------------------------------------- publiceren


async def test_publish_with_authentik_end_to_end(admin, fake_npm, fake_authentik):
    org, conn, base = await _setup(admin, fake_npm, fake_authentik)

    certs = (await admin.get(f"{base}/certificates")).json()
    assert [c["id"] for c in certs] == [3, 5, 4]
    assert "meta" not in certs[0] and SECRET_KEY not in str(certs), "geen private key naar de UI"

    plan = await _preview(admin, base)
    assert plan["can_apply"], plan["checks"]
    assert "upstream_ok" in _codes(plan)
    assert plan["probe_url"] == "https://grafana.domain.be/ via npm.lan:443"
    assert plan["steps"][0].startswith("Authentik: proxy provider 'VaultX: grafana.domain.be' aanmaken")
    assert plan["steps"][4].startswith("NPM: proxy host grafana.domain.be aanmaken naar http://grafana:3000")
    host = plan["host"]
    assert (host["certificate_id"], host["ssl_forced"], host["hsts_enabled"], host["http2_support"]) == (
        3,
        True,
        True,
        True,
    )
    assert "# vaultx.app = Grafana" in host["advanced_config"]
    assert "location /outpost.goauthentik.io {" in host["advanced_config"]
    root = host["locations"][0]["advanced_config"]
    assert root.index("auth_request") < root.index("sub_filter"), "eerst Authentik, dan het thema"
    assert plan["certificate"]["nice_name"] == "*.domain.be"
    assert len(fake_npm.hosts) == 1 and not fake_authentik.providers, "een voorbeeld wijzigt niets"

    change = await _publish(admin, base)
    assert change["status"] == "applied", change
    assert change["action"] == "publish" and change["npm_id"] > 1
    assert change["message"] == "De app staat online, beschermd met Authentik."
    assert change["probe_after"]["status"] == 302
    created = fake_npm.host(change["npm_id"])
    assert created["domain_names"] == ["grafana.domain.be"]
    assert created["advanced_config"] == host["advanced_config"]
    (provider,) = fake_authentik.providers.values()
    assert provider["external_host"] == "https://grafana.domain.be"
    assert fake_authentik.outposts[EMBEDDED]["providers"] == [provider["pk"]]
    assert fake_authentik.bindings_for("vaultx-grafana-domain-be") == ["vaultx:acme"]

    hosts = {h["npm_id"]: h for h in (await admin.get(f"{base}/hosts")).json()}
    published = hosts[change["npm_id"]]
    assert published["vaultx_published"] and published["vaultx_managed"] and published["forward_auth"]
    assert not hosts[1]["vaultx_published"]
    assert change["host_id"] == published["id"]
    apps = (await admin.get("/api/v1/catalog", params={"organization_id": org["id"]})).json()
    app = next(a for a in apps if a["name"] == "Grafana")
    assert app["status"] == "protected" and app["url"] == "https://grafana.domain.be"

    audit = (await admin.get("/api/v1/audit", params={"action": "npm_host.publish"})).json()["items"][0]
    assert audit["outcome"] == "success"
    assert audit["details"]["application"] == "Grafana"
    assert audit["details"]["authentik"]["provider"] == "VaultX: grafana.domain.be"

    # Een sync laat de markering staan; hetzelfde domein nog eens publiceren kan niet.
    assert (await admin.post_json(f"{base}/sync", {})).status_code == 200
    hosts = {h["npm_id"]: h for h in (await admin.get(f"{base}/hosts")).json()}
    assert hosts[change["npm_id"]]["vaultx_published"]
    plan = await _preview(admin, base)
    assert "domain_in_use" in _codes(plan) and not plan["can_apply"]
    change = await _publish(admin, base)
    assert change["status"] == "refused" and "staat al in NPM" in change["message"]


async def test_publish_checks(admin, fake_npm, fake_authentik):
    _, _, base = await _setup(admin, fake_npm, fake_authentik)
    fake_npm.unreachable_upstreams.add("grafana")
    assert "certificate_mismatch" in _codes(await _preview(admin, base, certificate_id=4))
    assert "certificate_expired" in _codes(
        await _preview(admin, base, domain="old.domain.be", certificate_id=5)
    )
    assert "certificate_missing" in _codes(await _preview(admin, base, certificate_id=99))
    assert "domain_invalid" in _codes(await _preview(admin, base, domain="nodot"))
    assert "upstream_host" in _codes(await _preview(admin, base, forward_host="http://x"))
    plan = await _preview(admin, base)
    assert "upstream_unreachable" in _codes(plan) and plan["can_apply"]
    plan = await _preview(admin, base, access="team:nope")
    assert "ak_team_unknown" in _codes(plan) and not plan["can_apply"]
    plan = await _preview(admin, base, certificate_id=0)
    assert {"no_tls", "block_exploits_http"} <= set(_codes(plan))
    r = await admin.post_json(f"{base}/publish/preview", _request(theme_css_url="https://x.be/a'b.css"))
    assert r.status_code == 422


async def test_publish_without_authentik(admin, fake_npm, fake_authentik):
    _, _, base = await _setup(admin, fake_npm, fake_authentik)
    change = await _publish(admin, base, protect=False, certificate_id=0, security_headers=False)
    assert change["status"] == "applied", change
    assert change["message"] == "De app staat online, zonder Authentik."
    created = fake_npm.host(change["npm_id"])
    assert "auth_request" not in str(created)
    assert "sub_filter" in created["locations"][0]["advanced_config"]
    assert "X-Frame-Options" not in str(created)
    assert not fake_authentik.providers


async def test_publish_rolls_back_npm_and_authentik(admin, fake_npm, fake_authentik):
    _, conn, base = await _setup(admin, fake_npm, fake_authentik)
    # nginx -t faalt (outpost onbekend voor nginx): host weg, Authentik teruggedraaid.
    r = await admin.patch_json(f"{base}", {"authentik_outpost_url": "http://does-not-exist:9000"})
    assert r.status_code == 200
    change = await _publish(admin, base)
    assert change["status"] == "rolled_back", change
    assert "nginx in NPM weigerde" in change["message"] and "weer verwijderd" in change["message"]
    assert change["nginx_error"] and change["npm_id"] in fake_npm.deleted
    assert [h["id"] for h in fake_npm.hosts] == [1]
    assert not fake_authentik.providers and not fake_authentik.applications
    assert change["authentik"]["undone"]

    # De outpost antwoordt niet (500): idem.
    await admin.patch_json(f"{base}", {"authentik_outpost_url": "http://dead-outpost:9000"})
    change = await _publish(admin, base)
    assert change["status"] == "rolled_back", change
    assert [h["id"] for h in fake_npm.hosts] == [1] and not fake_authentik.providers

    # Verwijderen lukt niet: handwerk, en het journaal zegt welke host.
    fake_npm.fail_deletes = 5
    change = await _publish(admin, base)
    assert change["status"] == "rollback_failed", change
    assert f"proxy host #{change['npm_id']}" in change["message"]

    # NPM weigert de nieuwe host: niets aangemaakt, Authentik teruggedraaid.
    fake_npm.hosts = [h for h in fake_npm.hosts if h["id"] == 1]
    fake_npm.fail_deletes = 0
    fake_npm.fail_create = True
    await admin.patch_json(f"{base}", {"authentik_outpost_url": OUTPOST_URL})
    change = await _publish(admin, base)
    assert change["status"] == "rolled_back" and "niets in NPM aangemaakt" in change["message"]
    assert not fake_authentik.providers

    audit = (await admin.get("/api/v1/audit", params={"action": "npm_host.publish"})).json()["items"]
    assert {a["outcome"] for a in audit} == {"failure"}


async def test_publish_refused_when_authentik_fails(admin, fake_npm, fake_authentik):
    _, _, base = await _setup(admin, fake_npm, fake_authentik)
    fake_authentik.fail["POST /core/applications/"] = (500, {"detail": "kapot"})
    change = await _publish(admin, base)
    assert change["status"] == "refused", change
    assert "NPM is niet gewijzigd" in change["message"]
    assert [h["id"] for h in fake_npm.hosts] == [1] and not fake_authentik.providers


async def test_only_admins_publish_and_write_must_be_enabled(admin, make_client, fake_npm, fake_authentik):
    org, conn, base = await _setup(admin, fake_npm, fake_authentik)
    member = await make_client()
    await member.login("bob", groups=["vaultx:acme"])
    assert (await member.post_json(f"{base}/publish/preview", _request())).status_code == 403
    assert (await member.post_json(f"{base}/publish", _request())).status_code == 403
    assert (await member.get(f"{base}/certificates")).status_code == 403

    await admin.patch_json(base, {"write_enabled": False})
    assert "write_disabled" in _codes(await _preview(admin, base))
    assert (await admin.post_json(f"{base}/publish", _request())).status_code in (400, 409, 422)


# ---------------------------------------------------------------- depubliceren


async def test_unpublish_removes_host_authentik_and_catalog(admin, fake_npm, fake_authentik):
    org, conn, base = await _setup(admin, fake_npm, fake_authentik)
    change = await _publish(admin, base)
    assert change["status"] == "applied"
    host_id = change["host_id"]
    hosts = {h["npm_id"]: h for h in (await admin.get(f"{base}/hosts")).json()}

    # Een host die VaultX niet publiceerde, verwijdert het niet.
    plan = (await admin.get(f"{base}/hosts/{hosts[1]['id']}/unpublish")).json()
    assert "not_published" in _codes(plan) and not plan["can_apply"]
    r = await admin.post_json(f"{base}/hosts/{hosts[1]['id']}/unpublish", {})
    assert r.status_code == 422 and fake_npm.host(1) is not None

    plan = (await admin.get(f"{base}/hosts/{host_id}/unpublish")).json()
    assert plan["can_apply"], plan
    assert plan["steps"] == [
        f"NPM: proxy host grafana.domain.be (#{change['npm_id']}) verwijderen.",
        "Authentik: applicatie 'Grafana' (vaultx-grafana-domain-be) verwijderen.",
        "Authentik: proxy provider 'VaultX: grafana.domain.be' verwijderen.",
        "VaultX: 'Grafana' uit de catalogus halen.",
    ]
    assert plan["before"]["domain_names"] == ["grafana.domain.be"]

    r = await admin.post_json(f"{base}/hosts/{host_id}/unpublish", {})
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "applied" and done["action"] == "unpublish", done
    assert "In Authentik verwijderd" in done["message"]
    assert fake_npm.host(change["npm_id"]) is None
    assert not fake_authentik.providers and not fake_authentik.applications
    assert done["before"]["advanced_config"].startswith("# Gepubliceerd door VaultX")
    apps = (await admin.get("/api/v1/catalog", params={"organization_id": org["id"]})).json()
    assert "Grafana" not in [a["name"] for a in apps]
    hosts = {h["id"]: h for h in (await admin.get(f"{base}/hosts")).json()}
    assert hosts[host_id]["removed_at"] and not hosts[host_id]["vaultx_published"]
    journal = (await admin.get(f"{base}/changes")).json()
    assert [c["action"] for c in journal][:2] == ["unpublish", "publish"]
    audit = (await admin.get("/api/v1/audit", params={"action": "npm_host.unpublish"})).json()["items"][0]
    assert audit["details"]["deleted_application"] == "Grafana"

    # Hetzelfde domein kan daarna opnieuw.
    again = await _publish(admin, base)
    assert again["status"] == "applied", again


async def test_theme_template_on_connection(admin, fake_npm, fake_authentik):
    _, conn, base = await _setup(
        admin, fake_npm, fake_authentik, theme_css_template="https://css.domain.be/{app}.css"
    )
    assert conn["theme_css_template"] == "https://css.domain.be/{app}.css"
    r = await admin.patch_json(base, {"theme_css_template": "javascript:alert(1)"})
    assert r.status_code == 422
    r = await admin.patch_json(base, {"theme_css_template": ""})
    assert r.json()["theme_css_template"] is None
