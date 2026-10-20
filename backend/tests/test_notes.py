"""Fase 7: notities (ontgrendelen in de browser, versleutelde opslag, revisies, versies)."""

from __future__ import annotations

import base64
import json
from datetime import timedelta

from app.core.config import get_settings
from app.services import notes as notes_service
from tests.bw_crypto import unwrap_user_key
from tests.test_vault import bearer, enroll


def note(account, title: str, body: str, folder: str = "") -> dict:
    blob = {"v": 1, "title": title, "folder": folder, "body": body, "pinned": False}
    return {"content": account.enc(json.dumps(blob))}


def read(account, n: dict) -> dict:
    return json.loads(account.dec(n["content"]))


async def test_unlock_returns_user_key_after_password_check(make_client, admin):
    c = await make_client()
    await c.login("jonas")
    r = await c.post_json("/api/v1/me/vault/unlock", {"master_password_hash": "x"})
    assert r.status_code == 404  # nog geen kluis

    account = await enroll(c, "jonas")
    st = (await c.get("/api/v1/me/vault")).json()
    assert st["kdf_salt"] == "jonas@example.com" and st["note_count"] == 0

    r = await c.post_json("/api/v1/me/vault/unlock", {"master_password_hash": account.password_hash})
    assert r.status_code == 200, r.text
    assert unwrap_user_key(account.mkey, r.json()["user_key"]) == account.user_key

    r = await c.post_json("/api/v1/me/vault/unlock", {"master_password_hash": "d3Jvbmc="})
    assert r.status_code == 422
    # Zonder CSRF-header geweigerd
    r = await c.post("/api/v1/me/vault/unlock", json={"master_password_hash": account.password_hash})
    assert r.status_code == 403

    entries = (await admin.get("/api/v1/audit", params={"action": "vault.unlock"})).json()["items"]
    assert sorted(e["outcome"] for e in entries) == ["failure", "success"]


async def test_unlock_shares_lockout_with_app_logins(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    for _ in range(10):
        r = await c.post_json("/api/v1/me/vault/unlock", {"master_password_hash": "d3Jvbmc="})
        assert r.status_code == 422
    # Vergrendeld: ook het juiste wachtwoord helpt even niet, ook niet vanuit een app.
    r = await c.post_json("/api/v1/me/vault/unlock", {"master_password_hash": account.password_hash})
    assert r.status_code == 403
    r = await c.post(
        "/identity/connect/token",
        data={
            "grant_type": "password",
            "username": account.email,
            "password": account.password_hash,
            "scope": "api offline_access",
            "client_id": "cli",
            "deviceType": "25",
            "deviceIdentifier": "dev-1",
            "deviceName": "linux",
        },
    )
    assert r.status_code == 400


async def test_notes_crud_trash_and_isolation(make_client, admin):
    c = await make_client()
    account = await enroll(c, "jonas")

    r = await c.post_json("/api/v1/me/notes", note(account, "Grafana runbook", "Zie [[Back-ups]] #ops"))
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["revision"] == 1 and created["deleted_at"] is None
    assert read(account, created)["title"] == "Grafana runbook"

    # Geen klare tekst toegelaten
    r = await c.post_json("/api/v1/me/notes", {"content": "gewoon tekst"})
    assert r.status_code == 422

    r = await c.put(
        f"/api/v1/me/notes/{created['id']}",
        json={**note(account, "Grafana runbook", "nieuw"), "base_revision": 1},
        headers=c.mutating_headers(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 2

    # Andere gebruiker ziet en raakt niets
    other = await make_client()
    await enroll(other, "eve")
    assert (await other.get("/api/v1/me/notes")).json() == []
    r = await other.put(
        f"/api/v1/me/notes/{created['id']}",
        json={"content": account.enc("x"), "base_revision": 2},
        headers=other.mutating_headers(),
    )
    assert r.status_code == 404
    assert (await other.post_json(f"/api/v1/me/notes/{created['id']}/trash", {})).status_code == 404

    r = await c.post_json(f"/api/v1/me/notes/{created['id']}/trash", {})
    assert r.status_code == 200 and r.json()["deleted_at"] is not None
    st = (await c.get("/api/v1/me/vault")).json()
    assert st["note_count"] == 0
    r = await c.post_json(f"/api/v1/me/notes/{created['id']}/restore", {})
    assert r.status_code == 200 and r.json()["deleted_at"] is None
    assert (await c.get("/api/v1/me/vault")).json()["note_count"] == 1

    assert (await c.delete_(f"/api/v1/me/notes/{created['id']}")).status_code == 204
    assert (await c.get("/api/v1/me/notes")).json() == []

    actions = {
        e["action"]
        for e in (await admin.get("/api/v1/audit", params={"action": "vault.note"})).json()["items"]
    }
    assert actions == {"vault.note.create", "vault.note.trash", "vault.note.restore", "vault.note.delete"}


async def test_stale_revision_is_refused_with_current_version(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    n = (await c.post_json("/api/v1/me/notes", note(account, "Wifi", "eerste"))).json()
    url = f"/api/v1/me/notes/{n['id']}"
    r = await c.put(
        url, json={**note(account, "Wifi", "tab A"), "base_revision": 1}, headers=c.mutating_headers()
    )
    assert r.status_code == 200
    r = await c.put(
        url, json={**note(account, "Wifi", "tab B"), "base_revision": 1}, headers=c.mutating_headers()
    )
    assert r.status_code == 409
    current = r.json()["current"]
    assert current["revision"] == 2 and read(account, current)["body"] == "tab A"


async def test_version_history_is_kept_and_pruned(make_client, monkeypatch):
    monkeypatch.setattr(notes_service, "VERSION_INTERVAL", timedelta(0))
    monkeypatch.setattr(notes_service, "MAX_VERSIONS", 3)
    c = await make_client()
    account = await enroll(c, "jonas")
    n = (await c.post_json("/api/v1/me/notes", note(account, "Log", "v1"))).json()
    url = f"/api/v1/me/notes/{n['id']}"
    for i in range(2, 7):
        r = await c.put(
            url, json={**note(account, "Log", f"v{i}"), "base_revision": i - 1}, headers=c.mutating_headers()
        )
        assert r.status_code == 200, r.text
    versions = (await c.get(f"{url}/versions")).json()
    assert [v["revision"] for v in versions] == [5, 4, 3]
    assert [read(account, v)["body"] for v in versions] == ["v5", "v4", "v3"]


async def test_versions_only_every_interval(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    n = (await c.post_json("/api/v1/me/notes", note(account, "Log", "v1"))).json()
    url = f"/api/v1/me/notes/{n['id']}"
    for i in range(2, 5):
        await c.put(
            url, json={**note(account, "Log", f"v{i}"), "base_revision": i - 1}, headers=c.mutating_headers()
        )
    # Autosave tijdens het typen: enkel de eerste wijziging bewaart de oude versie.
    assert [v["revision"] for v in (await c.get(f"{url}/versions")).json()] == [1]


async def test_import_and_reset_removes_notes(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    r = await c.post_json(
        "/api/v1/me/notes/import",
        {"notes": [note(account, f"Notitie {i}", "tekst", folder="Obsidian/Map") for i in range(5)]},
    )
    assert r.status_code == 201, r.text
    assert len(r.json()) == 5
    assert (await c.get("/api/v1/me/vault")).json()["note_count"] == 5

    # Bitwarden-clients merken niets van notities
    h = await bearer(c, account)
    assert (await c.get("/api/sync", headers=h)).json()["ciphers"] == []

    assert (await c.post_json("/api/v1/me/vault/reset", {})).status_code == 204
    account = await enroll(c, "jonas")
    assert (await c.get("/api/v1/me/notes")).json() == []


def enc_file(account, data: bytes) -> bytes:
    """EncArrayBuffer zoals de browser hem maakt: type 2 + IV + MAC + ciphertext."""
    s = account.enc_bytes(data)
    iv, ct, mac = (base64.b64decode(x) for x in s[2:].split("|"))
    return bytes([2]) + iv + mac + ct


async def upload(c, account, name: str, blob: bytes):
    headers = {**c.mutating_headers(), "Content-Type": "application/octet-stream"}
    headers["X-VaultX-File-Name"] = account.enc(name)
    return await c.post("/api/v1/me/notes/files", content=blob, headers=headers)


async def test_files_upload_download_quota_and_isolation(make_client, admin, monkeypatch):
    c = await make_client()
    account = await enroll(c, "jonas")
    picture = b"\x89PNG" + bytes(range(256)) * 40
    blob = enc_file(account, picture)

    r = await upload(c, account, "schema.png", blob)
    assert r.status_code == 201, r.text
    f = r.json()
    assert account.dec(f["name"]) == "schema.png" and f["size"] == len(blob)

    listing = (await c.get("/api/v1/me/notes/files")).json()
    assert [x["id"] for x in listing["files"]] == [f["id"]]
    assert listing["used_bytes"] == len(blob) and listing["max_file_bytes"] == 25 * 1024 * 1024

    r = await c.get(f"/api/v1/me/notes/files/{f['id']}")
    assert r.status_code == 200 and r.content == blob
    assert "immutable" in r.headers["cache-control"]

    # Onversleutelde inhoud of naam wordt geweigerd
    assert (await upload(c, account, "x.png", picture)).status_code == 422
    headers = {**c.mutating_headers(), "X-VaultX-File-Name": "klare-naam.png"}
    assert (await c.post("/api/v1/me/notes/files", content=blob, headers=headers)).status_code == 422
    # Zonder CSRF-header geweigerd
    r = await c.post("/api/v1/me/notes/files", content=blob, headers={"X-VaultX-File-Name": account.enc("x")})
    assert r.status_code == 403

    # Te groot en quotum vol
    settings = get_settings()
    monkeypatch.setattr(settings, "vault_note_file_max_mb", 1)
    big = enc_file(account, b"x" * (1024 * 1024 + 100))
    assert (await upload(c, account, "groot.bin", big)).status_code == 413
    monkeypatch.setattr(settings, "vault_note_storage_mb", 1)
    almost = enc_file(account, b"y" * (1024 * 1024 - 1000))
    r = await upload(c, account, "vol.bin", almost)
    assert r.status_code == 422 and "vol" in r.json()["detail"]

    # Andere gebruiker ziet en raakt niets
    other = await make_client()
    await enroll(other, "eve")
    assert (await other.get("/api/v1/me/notes/files")).json()["files"] == []
    assert (await other.get(f"/api/v1/me/notes/files/{f['id']}")).status_code == 404
    assert (await other.delete_(f"/api/v1/me/notes/files/{f['id']}")).status_code == 404

    assert (await c.delete_(f"/api/v1/me/notes/files/{f['id']}")).status_code == 204
    assert (await c.get(f"/api/v1/me/notes/files/{f['id']}")).status_code == 404

    actions = sorted(
        e["action"]
        for e in (await admin.get("/api/v1/audit", params={"action": "vault.note.file"})).json()["items"]
    )
    assert actions == ["vault.note.file.delete", "vault.note.file.upload"]


async def test_files_removed_with_vault(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    assert (await upload(c, account, "a.txt", enc_file(account, b"hallo"))).status_code == 201
    assert (await c.post_json("/api/v1/me/vault/reset", {})).status_code == 204
    await enroll(c, "jonas")
    assert (await c.get("/api/v1/me/notes/files")).json()["files"] == []


async def test_client_chosen_note_id(make_client):
    # De browser kiest het id en versleutelt het mee, zodat de server notities niet kan omwisselen.
    import uuid

    a = await make_client()
    acc = await enroll(a, "jonas")
    nid = str(uuid.uuid4())
    r = await a.post_json("/api/v1/me/notes", {**note(acc, "Eén", "x"), "id": nid})
    assert r.status_code == 201, r.text
    assert r.json()["id"] == nid
    r = await a.post_json("/api/v1/me/notes", {**note(acc, "Twee", "y"), "id": nid})
    assert r.status_code == 409
    # Ook een ander account kan dat id niet hergebruiken.
    b = await make_client()
    acc_b = await enroll(b, "bob")
    r = await b.post_json("/api/v1/me/notes", {**note(acc_b, "Bob", "z"), "id": nid})
    assert r.status_code == 409
    other = str(uuid.uuid4())
    r = await a.post_json("/api/v1/me/notes/import", {"notes": [{**note(acc, "Imp", "i"), "id": other}]})
    assert r.status_code in (200, 201), r.text
    assert [n["id"] for n in r.json()] == [other]
    assert len((await a.get("/api/v1/me/notes")).json()) == 2
