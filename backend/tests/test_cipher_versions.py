"""Kluisitems mogen nooit stil verloren gaan: versies, revisiecontrole onder lock, auditsporen."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.cli import item_history, item_restore
from app.core.db import get_sessionmaker
from tests.test_vault import bearer, enroll, login_item


async def _audit(action: str, target: str | None = None) -> list[tuple[str, dict]]:
    sql = "SELECT outcome, details FROM audit_logs WHERE action = :a"
    params: dict = {"a": action}
    if target:
        sql += " AND target_id = :t"
        params["t"] = target
    async with get_sessionmaker()() as db:
        rows = (await db.execute(text(sql + " ORDER BY id"), params)).all()
    return [(r[0], r[1]) for r in rows]


async def _new_item(c, account, h, **extra) -> dict:
    r = await c.post("/api/ciphers", json=login_item(account, "Media", "jonas", "oud", **extra), headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _with_totp(account) -> dict:
    item = login_item(account, "Media", "jonas", "oud")
    item["login"]["totp"] = account.enc("JBSWY3DPEHPK3PXP")
    return item


async def test_update_keeps_previous_version_and_restore_makes_a_copy(make_client, capsys):
    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account)
    r = await c.post("/api/ciphers", json=_with_totp(account), headers=h)
    original = r.json()
    old_totp = original["login"]["totp"]

    # Een client slaat het item op zonder TOTP (bv. een oude of foute client).
    body = {
        **login_item(account, "Media", "jonas", "nieuw"),
        "lastKnownRevisionDate": original["revisionDate"],
    }
    r = await c.put(f"/api/ciphers/{original['id']}", json=body, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["login"]["totp"] is None

    # Het verlies staat in het auditlog, met de naam van het veld (niet de inhoud).
    updates = await _audit("vault.item.update", original["id"])
    assert updates[-1][0] == "success"
    assert updates[-1][1]["verwijderde_velden"] == ["login.totp"]
    version_id = updates[-1][1]["versie"]

    assert await item_history(account.email, original["id"], 10) == 0
    out = capsys.readouterr().out
    assert f"{version_id}\t{original['id']}" in out and "totp" in out

    copy_id = await item_restore(account.email, version_id)
    synced = {x["id"]: x for x in (await c.get("/api/sync", headers=h)).json()["ciphers"]}
    assert len(synced) == 2
    assert synced[copy_id]["login"]["totp"] == old_totp
    assert account.dec(synced[copy_id]["login"]["password"]) == "oud"
    # Het huidige item blijft zoals de client het opsloeg: herstellen overschrijft niets.
    assert account.dec(synced[original["id"]]["login"]["password"]) == "nieuw"
    assert (await _audit("vault.item.restore_version", copy_id))[0][1]["versie"] == version_id


async def test_stale_update_is_refused_logged_and_changes_nothing(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account)
    item = await _new_item(c, account, h)
    stale = {**login_item(account, "Media", "x", "y"), "lastKnownRevisionDate": "2020-01-01T00:00:00Z"}
    r = await c.put(f"/api/ciphers/{item['id']}", json=stale, headers=h)
    assert r.status_code == 400
    denied = await _audit("vault.item.update", item["id"])
    assert [o for o, _ in denied] == ["denied"]
    assert denied[0][1]["reden"] == "verouderde kopie"
    after = (await c.get(f"/api/ciphers/{item['id']}", headers=h)).json()
    assert after["revisionDate"] == item["revisionDate"]
    async with get_sessionmaker()() as db:
        assert await db.scalar(text("SELECT count(*) FROM vault_cipher_versions")) == 0


async def test_two_saves_from_the_same_copy_cannot_both_win(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    h1 = await bearer(c, account, "toestel-1")
    h2 = await bearer(c, account, "toestel-2")
    item = await _new_item(c, account, h1)

    def body(pw: str) -> dict:
        return {**login_item(account, "Media", "jonas", pw), "lastKnownRevisionDate": item["revisionDate"]}

    results = await asyncio.gather(
        c.put(f"/api/ciphers/{item['id']}", json=body("van-1"), headers=h1),
        c.put(f"/api/ciphers/{item['id']}", json=body("van-2"), headers=h2),
    )
    assert sorted(r.status_code for r in results) == [200, 400]


async def test_update_without_revision_is_marked_in_audit(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account)
    item = await _new_item(c, account, h)
    r = await c.put(f"/api/ciphers/{item['id']}", json=login_item(account, "Media", "j", "p"), headers=h)
    assert r.status_code == 200
    details = (await _audit("vault.item.update", item["id"]))[-1][1]
    assert details["revisiecontrole"] == "ontbrak"
    assert "verwijderde_velden" not in details


async def test_hard_delete_keeps_a_version(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account)
    item = await _new_item(c, account, h)
    assert (await c.delete(f"/api/ciphers/{item['id']}", headers=h)).status_code == 200
    version_id = (await _audit("vault.item.delete", item["id"]))[0][1]["versie"]
    copy_id = await item_restore(account.email, version_id)
    restored = (await c.get(f"/api/ciphers/{copy_id}", headers=h)).json()
    assert account.dec(restored["login"]["password"]) == "oud"


async def test_versions_are_capped_per_item(make_client, monkeypatch):
    import app.services.vault as vault

    monkeypatch.setattr(vault, "CIPHER_VERSIONS_KEPT", 3)
    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account)
    item = await _new_item(c, account, h)
    for n in range(5):
        r = await c.put(
            f"/api/ciphers/{item['id']}", json=login_item(account, "Media", "j", f"pw{n}"), headers=h
        )
        assert r.status_code == 200
    async with get_sessionmaker()() as db:
        assert await db.scalar(text("SELECT count(*) FROM vault_cipher_versions")) == 3


async def test_versions_are_private(make_client):
    c = await make_client()
    alice = await enroll(c, "alice")
    h = await bearer(c, alice)
    item = await _new_item(c, alice, h)
    await c.put(f"/api/ciphers/{item['id']}", json=login_item(alice, "Media", "j", "p"), headers=h)
    version_id = (await _audit("vault.item.update", item["id"]))[-1][1]["versie"]
    c2 = await make_client()
    bob = await enroll(c2, "bob")
    try:
        await item_restore(bob.email, version_id)
    except Exception as exc:  # noqa: BLE001
        assert "niet gevonden" in str(getattr(exc, "message", exc))
    else:
        raise AssertionError("bob mag de versie van alice niet terugzetten")
