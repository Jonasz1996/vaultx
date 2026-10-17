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


async def test_change_master_password_to_argon2id(make_client):
    from tests.bw_crypto import argon2_master_key, master_password_hash, wrap_user_key
    from tests.test_vault import PASSWORD, token

    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account)
    item = (
        await c.post("/api/ciphers", json=login_item(account, "Grafana", "admin", "pw"), headers=h)
    ).json()

    new_pw = "een nieuw en lang master password"
    mkey = argon2_master_key(new_pw, account.email, 3, 64, 4)
    body = {
        "current_master_password_hash": account.password_hash,
        "kdf": 1,
        "kdf_iterations": 3,
        "kdf_memory": 64,
        "kdf_parallelism": 4,
        "master_password_hash": master_password_hash(mkey, new_pw),
        "user_key": wrap_user_key(mkey, account.user_key),
    }
    wrong = {**body, "current_master_password_hash": master_password_hash(account.mkey, "fout")}
    r = await c.post_json("/api/v1/me/vault/password", wrong)
    assert r.status_code == 422 and "onjuist" in r.text
    weak = {**body, "kdf_memory": 8}
    assert (await c.post_json("/api/v1/me/vault/password", weak)).status_code == 422
    r = await c.post_json("/api/v1/me/vault/password", body)
    assert r.status_code == 200, r.text
    st = r.json()
    assert (st["kdf"], st["kdf_iterations"], st["kdf_memory"], st["kdf_parallelism"]) == (1, 3, 64, 4)
    assert all(d["revoked_at"] for d in st["devices"])

    # Oude tokens en het oude wachtwoord werken niet meer
    assert (await c.get("/api/sync", headers=h)).status_code == 401
    assert (await token(c, account.email, account.password_hash)).status_code == 400
    pre = (await c.post("/identity/accounts/prelogin/password", json={"email": account.email})).json()
    assert pre["kdfSettings"] == {"kdfType": 1, "iterations": 3, "memory": 64, "parallelism": 4}
    r = await token(c, account.email, master_password_hash(mkey, new_pw), "dev-2")
    assert r.status_code == 200, r.text
    # Zelfde user key, dus bestaande items blijven leesbaar
    from tests.bw_crypto import unwrap_user_key

    assert unwrap_user_key(mkey, r.json()["Key"]) == account.user_key
    h2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    got = (await c.get(f"/api/ciphers/{item['id']}", headers=h2)).json()
    assert account.dec(got["login"]["password"]) == "pw"
    assert new_pw != PASSWORD
