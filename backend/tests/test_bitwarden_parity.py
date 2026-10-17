"""Fase 9: wat de Bitwarden-apps nog misten (import, ...)."""

from __future__ import annotations

from tests.test_vault import bearer, enroll, login_item


async def test_import_with_folders(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account)
    existing = (await c.post("/api/folders", json={"name": account.enc("Bestaand")}, headers=h)).json()

    body = {
        "ciphers": [
            login_item(account, "Grafana", "admin", "pw-1"),
            login_item(account, "Proxmox", "root", "pw-2", favorite=True),
            {"type": 2, "name": account.enc("Notitie"), "secureNote": {"type": 0}, "notes": account.enc("x")},
        ],
        "folders": [{"name": account.enc("Infra")}, {"id": existing["id"], "name": existing["name"]}],
        "folderRelationships": [{"key": 0, "value": 0}, {"key": 2, "value": 1}],
    }
    r = await c.post("/api/ciphers/import", json=body, headers=h)
    assert r.status_code == 200, r.text

    sync = (await c.get("/api/sync", headers=h)).json()
    assert len(sync["folders"]) == 2  # één nieuwe, de bestaande hergebruikt
    infra = next(f["id"] for f in sync["folders"] if f["id"] != existing["id"])
    by_name = {account.dec(x["name"]): x for x in sync["ciphers"]}
    assert by_name["Grafana"]["folderId"] == infra
    assert by_name["Notitie"]["folderId"] == existing["id"]
    assert by_name["Proxmox"]["folderId"] is None and by_name["Proxmox"]["favorite"] is True
    assert account.dec(by_name["Proxmox"]["login"]["password"]) == "pw-2"

    await c.login("admin", groups=["vaultx-admins"])
    audit = (await c.get("/api/v1/audit", params={"action": "vault.import"})).json()["items"]
    assert audit[0]["details"]["items"] == 3 and audit[0]["details"]["folders"] == 1


async def test_import_rejects_bad_input(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account)
    bad_rel = {
        "ciphers": [login_item(account, "A", "a", "b")],
        "folders": [],
        "folderRelationships": [{"key": 0, "value": 0}],
    }
    r = await c.post("/api/ciphers/import", json=bad_rel, headers=h)
    assert r.status_code == 400 and "koppeling" in r.json()["message"]
    org = {"ciphers": [login_item(account, "A", "a", "b", organizationId="x")], "folders": []}
    assert (await c.post("/api/ciphers/import", json=org, headers=h)).status_code == 400
    # Niets half geïmporteerd
    assert (await c.get("/api/sync", headers=h)).json()["ciphers"] == []
    # Andermans map-id wordt een nieuwe map, geen lek naar diens kluis
    other = await make_client()
    other_acc = await enroll(other, "eve")
    oh = await bearer(other, other_acc)
    eve_folder = (await other.post("/api/folders", json={"name": other_acc.enc("Eve")}, headers=oh)).json()
    body = {
        "ciphers": [login_item(account, "A", "a", "b")],
        "folders": [{"id": eve_folder["id"], "name": account.enc("Mijn")}],
        "folderRelationships": [{"key": 0, "value": 0}],
    }
    assert (await c.post("/api/ciphers/import", json=body, headers=h)).status_code == 200
    folders = (await c.get("/api/sync", headers=h)).json()["folders"]
    assert len(folders) == 1 and folders[0]["id"] != eve_folder["id"]
    assert (await other.get("/api/sync", headers=oh)).json()["ciphers"] == []
