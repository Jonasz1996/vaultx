from sqlalchemy import text

from app.core.db import get_engine


async def test_chain_verifies_and_detects_tampering(admin):
    await admin.post_json("/api/v1/organizations", {"slug": "acme", "name": "Acme"})
    r = (await admin.get("/api/v1/audit/verify")).json()
    assert r["valid"] is True and r["entries_checked"] >= 2

    # Simuleer een aanvaller met databasetoegang die de trigger omzeilt
    async with get_engine().begin() as conn:
        await conn.execute(text("SET LOCAL session_replication_role = replica"))
        await conn.execute(
            text(
                "UPDATE audit_logs SET details = '{\"forged\": true}'::jsonb "
                "WHERE id = (SELECT min(id) FROM audit_logs WHERE organization_id IS NULL)"
            )
        )
    r = (await admin.get("/api/v1/audit/verify")).json()
    assert r["valid"] is False
    assert r["reason"] == "inhoud gewijzigd"


async def test_audit_rows_cannot_be_updated_or_deleted(admin):
    async with get_engine().connect() as conn:
        for stmt in ("UPDATE audit_logs SET action = 'x'", "DELETE FROM audit_logs", "TRUNCATE audit_logs"):
            try:
                await conn.execute(text(stmt))
            except Exception as exc:  # noqa: BLE001
                assert "append-only" in str(exc)
                await conn.rollback()
            else:
                raise AssertionError(f"{stmt} had geweigerd moeten worden")


async def test_per_org_chains_are_independent(admin):
    acme = (await admin.post_json("/api/v1/organizations", {"slug": "acme", "name": "Acme"})).json()
    beta = (await admin.post_json("/api/v1/organizations", {"slug": "beta", "name": "Beta"})).json()
    for org in (acme, beta):
        r = (await admin.get("/api/v1/audit/verify", params={"organization_id": org["id"]})).json()
        assert r["valid"] and r["entries_checked"] == 1
    events = (await admin.get("/api/v1/audit", params={"organization_id": acme["id"]})).json()["items"]
    assert events[0]["prev_hash"] == "0" * 64


async def test_denied_actions_are_audited(admin, make_client):
    u = await make_client()
    await u.login("nosy")
    await u.post_json("/api/v1/organizations", {"slug": "x", "name": "X"})
    events = (await admin.get("/api/v1/audit", params={"outcome": "denied"})).json()["items"]
    assert events[0]["action"] == "organization.create"
    assert events[0]["actor_label"] == "nosy@example.com"


async def test_non_admin_cannot_read_instance_chain(make_client):
    u = await make_client()
    await u.login("nosy2")
    assert (await u.get("/api/v1/audit", params={"instance_only": True})).status_code == 403
    assert (await u.get("/api/v1/audit")).json()["items"] == []


async def test_pagination_cursor(admin):
    for i in range(5):
        await admin.post_json("/api/v1/organizations", {"slug": f"o{i}", "name": f"O{i}"})
    page1 = (await admin.get("/api/v1/audit", params={"limit": 3})).json()
    assert len(page1["items"]) == 3 and page1["next_before_id"]
    page2 = (
        await admin.get("/api/v1/audit", params={"limit": 3, "before_id": page1["next_before_id"]})
    ).json()
    assert page2["items"][0]["id"] < page1["items"][-1]["id"]


async def test_dashboard(admin):
    d = (await admin.get("/api/v1/dashboard")).json()
    assert d["users_total"] == 1 and d["admins"] == 1 and d["logins_24h"] == 1
    assert d["active_sessions"] == 1
