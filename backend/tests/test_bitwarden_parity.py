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


async def _enable_2fa(c, account) -> tuple[str, str]:
    from app.services import totp

    r = await c.post_json("/api/v1/me/vault/2fa/setup", {"master_password_hash": account.password_hash})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/VaultX%3A")
    assert (await c.post_json("/api/v1/me/vault/2fa/enable", {"code": "000000"})).status_code == 422
    r = await c.post_json("/api/v1/me/vault/2fa/enable", {"code": totp.code_at(secret)})
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_code"]


async def test_two_step_login_with_authenticator(make_client):
    import time

    from app.services import totp
    from tests.test_vault import token

    c = await make_client()
    account = await enroll(c, "jonas")
    secret, recovery = await _enable_2fa(c, account)
    assert (await c.get("/api/v1/me/vault")).json()["two_factor_enabled"] is True

    # Zonder code: de client krijgt de vraag om een tweede stap, in het formaat van Bitwarden
    r = await token(c, account.email, account.password_hash)
    assert r.status_code == 400
    body = r.json()
    assert body["TwoFactorProviders2"] == {"0": None} and body["TwoFactorProviders"] == [0]
    assert "access_token" not in body

    # Foute code
    r = await token(c, account.email, account.password_hash, twoFactorToken="123456", twoFactorProvider="0")
    assert r.status_code == 400 and "TwoFactorProviders2" not in r.json()

    # De code van de vorige stap (de enable-code is al verbruikt), dus de volgende stap nemen
    code = totp.code_at(secret, time.time() + 30)
    r = await token(
        c,
        account.email,
        account.password_hash,
        twoFactorToken=code,
        twoFactorProvider="0",
        twoFactorRemember="1",
    )
    assert r.status_code == 200, r.text
    remember = r.json()["TwoFactorToken"]
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await c.get("/api/sync", headers=h)).json()["profile"]["twoFactorEnabled"] is True

    # Dezelfde code werkt geen tweede keer
    r = await token(
        c, account.email, account.password_hash, "dev-2", twoFactorToken=code, twoFactorProvider="0"
    )
    assert r.status_code == 400

    # Onthouden apparaat: geen code nodig, maar enkel op dat apparaat
    r = await token(c, account.email, account.password_hash, twoFactorToken=remember, twoFactorProvider="5")
    assert r.status_code == 200, r.text
    r = await token(
        c, account.email, account.password_hash, "dev-3", twoFactorToken=remember, twoFactorProvider="5"
    )
    assert r.status_code == 400 and "TwoFactorProviders2" in r.json()

    # Uitzetten vraagt master password en een code (hier de herstelcode)
    bad = {"master_password_hash": account.password_hash, "code": "AAAA-BBBB"}
    assert (await c.post_json("/api/v1/me/vault/2fa/disable", bad)).status_code == 422
    ok = {"master_password_hash": account.password_hash, "code": recovery.lower()}
    assert (await c.post_json("/api/v1/me/vault/2fa/disable", ok)).status_code == 204
    assert (await token(c, account.email, account.password_hash, "dev-4")).status_code == 200


async def test_recovery_code_at_login_and_admin_reset(make_client, admin):
    from tests.test_vault import token

    c = await make_client()
    account = await enroll(c, "jonas")
    _, recovery = await _enable_2fa(c, account)
    r = await token(c, account.email, account.password_hash, twoFactorToken=recovery, twoFactorProvider="8")
    assert r.status_code == 200, r.text
    assert (await c.get("/api/v1/me/vault")).json()["two_factor_enabled"] is False

    await _enable_2fa(c, account)
    me = (await c.get("/api/v1/me")).json()
    other = await make_client()
    await other.login("eve")
    assert (
        await other.delete(f"/api/v1/users/{me['id']}/vault-2fa", headers=other.mutating_headers())
    ).status_code == 403
    r = await admin.delete(f"/api/v1/users/{me['id']}/vault-2fa", headers=admin.mutating_headers())
    assert r.status_code == 204, r.text
    assert (await token(c, account.email, account.password_hash, "dev-9")).status_code == 200
    audit = (await admin.get("/api/v1/audit", params={"action": "vault.2fa."})).json()["items"]
    assert [a["action"] for a in audit].count("vault.2fa.enable") == 2


async def test_cli_resets_vault_two_factor(make_client):
    import pytest

    from app import cli
    from app.core.errors import VaultXError
    from tests.test_vault import token

    c = await make_client()
    account = await enroll(c, "jonas")
    await _enable_2fa(c, account)
    assert await cli.reset_vault_two_factor("JONAS@example.com") == "jonas@example.com"
    assert (await token(c, account.email, account.password_hash)).status_code == 200
    with pytest.raises(VaultXError):
        await cli.reset_vault_two_factor("jonas@example.com")


def _enc_blob(account, content: bytes) -> bytes:
    """EncArrayBuffer zoals de clients: 2 | iv | mac | ciphertext."""
    import base64

    enc = account.enc_bytes(content)  # "2.iv|ct|mac"
    iv, ct, mac = (base64.b64decode(x) for x in enc[2:].split("|"))
    return bytes([2]) + iv + mac + ct


async def test_attachment_lifecycle(make_client):
    from urllib.parse import urlsplit

    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account)
    item = (await c.post("/api/ciphers", json=login_item(account, "NAS", "admin", "pw"), headers=h)).json()
    blob = _enc_blob(account, b"inhoud van config.yml")
    body = {"fileName": account.enc("config.yml"), "key": account.enc("k" * 64), "fileSize": len(blob)}

    r = await c.post(f"/api/ciphers/{item['id']}/attachment/v2", json=body, headers=h)
    assert r.status_code == 200, r.text
    up = r.json()
    aid = up["attachmentId"]
    assert up["fileUploadType"] == 0 and up["url"].endswith(f"/attachment/{aid}")
    assert [x["id"] for x in up["cipherResponse"]["attachments"]] == [aid]
    sync = (await c.get("/api/sync", headers=h)).json()
    assert sync["ciphers"][0]["attachments"] is None  # nog niet geüpload

    # Verkeerde grootte of onversleutelde inhoud wordt geweigerd
    files = {"data": ("x", b"plain text", "application/octet-stream")}
    r = await c.post(f"/api/ciphers/{item['id']}/attachment/{aid}", files=files, headers=h)
    assert r.status_code == 400
    files = {"data": (body["fileName"], blob, "application/octet-stream")}
    r = await c.post(f"/api/ciphers/{item['id']}/attachment/{aid}", files=files, headers=h)
    assert r.status_code == 200, r.text
    assert (
        await c.post(f"/api/ciphers/{item['id']}/attachment/{aid}", files=files, headers=h)
    ).status_code == 400

    sync = (await c.get("/api/sync", headers=h)).json()
    att = sync["ciphers"][0]["attachments"][0]
    assert att["id"] == aid and att["size"] == str(len(blob)) and att["fileName"] == body["fileName"]

    info = (await c.get(f"/api/ciphers/{item['id']}/attachment/{aid}", headers=h)).json()
    url = urlsplit(info["url"])
    r = await c.get(f"{url.path}?{url.query}")  # zonder login, met token
    assert r.status_code == 200 and r.content == blob
    assert (await c.get(url.path + "?token=1.abc")).status_code == 404
    other = info["url"].replace(aid, item["id"])  # token hoort bij één bijlage
    assert (await c.get(urlsplit(other).path + "?" + url.query)).status_code == 404

    # Een andere gebruiker ziet of wist ze niet
    e = await make_client()
    eve = await enroll(e, "eve")
    eh = await bearer(e, eve)
    assert (await e.get(f"/api/ciphers/{item['id']}/attachment/{aid}", headers=eh)).status_code == 404
    assert (await e.delete(f"/api/ciphers/{item['id']}/attachment/{aid}", headers=eh)).status_code == 404

    r = await c.delete(f"/api/ciphers/{item['id']}/attachment/{aid}", headers=h)
    assert r.status_code == 200 and r.json()["cipher"]["attachments"] is None
    assert (await c.get(f"{url.path}?{url.query}")).status_code == 404


async def test_attachment_limits(make_client, monkeypatch):
    from app.core.config import get_settings

    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account)
    item = (await c.post("/api/ciphers", json=login_item(account, "NAS", "admin", "pw"), headers=h)).json()
    name = account.enc("groot.iso")
    too_big = {"fileName": name, "fileSize": 101 * 1024 * 1024}
    r = await c.post(f"/api/ciphers/{item['id']}/attachment/v2", json=too_big, headers=h)
    assert r.status_code == 400 and "100 MB" in r.json()["message"]
    monkeypatch.setattr(get_settings(), "vault_attachment_storage_mb", 1)
    ok = {"fileName": name, "fileSize": 600 * 1024}
    assert (await c.post(f"/api/ciphers/{item['id']}/attachment/v2", json=ok, headers=h)).status_code == 200
    r = await c.post(f"/api/ciphers/{item['id']}/attachment/v2", json=ok, headers=h)
    assert r.status_code == 400 and "vol" in r.json()["message"]
