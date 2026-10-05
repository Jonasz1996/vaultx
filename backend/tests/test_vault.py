"""Fase 2: kluis voor Bitwarden-clients (activeren, inloggen, sync, items, mappen)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from tests.bw_crypto import Account, new_enrollment, unwrap_user_key

PASSWORD = "correct horse battery staple"


async def enroll(client, sub: str, email: str | None = None) -> Account:
    await client.login(sub, email=email)
    account, payload = new_enrollment(email or f"{sub}@example.com", PASSWORD)
    r = await client.post_json("/api/v1/me/vault", payload)
    assert r.status_code == 201, r.text
    return account


async def token(client, email: str, password_hash: str, identifier: str = "dev-1", **extra) -> httpx.Response:
    form = {
        "grant_type": "password",
        "username": email,
        "password": password_hash,
        "scope": "api offline_access",
        "client_id": "cli",
        "deviceType": "25",
        "deviceIdentifier": identifier,
        "deviceName": "linux",
        **extra,
    }
    return await client.post("/identity/connect/token", data=form)


async def bearer(client, account: Account, identifier: str = "dev-1") -> dict[str, str]:
    r = await token(client, account.email, account.password_hash, identifier)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def jwt_claims(access_token: str) -> dict:
    body = access_token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def login_item(account: Account, name: str, username: str, password: str, **extra) -> dict:
    return {
        "type": 1,
        "name": account.enc(name),
        "notes": None,
        "favorite": False,
        "folderId": None,
        "organizationId": None,
        "reprompt": 0,
        "login": {
            "username": account.enc(username),
            "password": account.enc(password),
            "uris": [{"uri": account.enc("https://grafana.example.com"), "match": None}],
            "totp": None,
        },
        "fields": [],
        **extra,
    }


async def test_status_and_enroll(make_client):
    c = await make_client()
    await c.login("jonas", email="Jonas@Example.com")
    st = (await c.get("/api/v1/me/vault")).json()
    assert st["enrolled"] is False
    assert st["email"] == "jonas@example.com"
    assert st["blocked_reason"] is None

    account, payload = new_enrollment("jonas@example.com", PASSWORD)
    r = await c.post_json("/api/v1/me/vault", {**payload, "salt": "other@example.com"})
    assert r.status_code == 422
    r = await c.post_json("/api/v1/me/vault", {**payload, "kdf_iterations": 100_000})
    assert r.status_code == 422
    r = await c.post_json("/api/v1/me/vault", {**payload, "user_key": "not-an-encstring"})
    assert r.status_code == 422
    # Zonder CSRF-header geweigerd (cookie-authenticatie)
    r = await c.post("/api/v1/me/vault", json=payload)
    assert r.status_code == 403

    r = await c.post_json("/api/v1/me/vault", payload)
    assert r.status_code == 201, r.text
    st = r.json()
    assert st["enrolled"] is True and st["kdf_iterations"] == 600_000
    r = await c.post_json("/api/v1/me/vault", payload)
    assert r.status_code == 409


async def test_email_belongs_to_one_vault(make_client):
    a = await make_client()
    await enroll(a, "alice", email="shared@example.com")
    b = await make_client()
    await b.login("bob", email="shared@example.com")
    st = (await b.get("/api/v1/me/vault")).json()
    assert st["blocked_reason"]
    _, payload = new_enrollment("shared@example.com", PASSWORD)
    r = await b.post_json("/api/v1/me/vault", payload)
    assert r.status_code == 409


async def test_prelogin_does_not_reveal_accounts(make_client):
    c = await make_client()
    await enroll(c, "jonas")
    for path in ("/identity/accounts/prelogin/password", "/identity/accounts/prelogin"):
        known = (await c.post(path, json={"email": "Jonas@example.com"})).json()
        unknown = (await c.post(path, json={"email": "nobody@example.com"})).json()
        assert known["kdfSettings"] == {
            "kdfType": 0,
            "iterations": 600000,
            "memory": None,
            "parallelism": None,
        }
        assert known["salt"] == "jonas@example.com"
        assert unknown["kdfSettings"] == known["kdfSettings"]
        assert unknown["salt"] == "nobody@example.com"
        assert known["kdfIterations"] == 600000


async def test_login_returns_keys_and_jwt(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    anon = await make_client()

    r = await token(anon, account.email, "d3Jvbmc=")
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"
    assert r.json()["ErrorModel"]["Message"]
    r = await token(anon, "nobody@example.com", account.password_hash)
    assert r.status_code == 400

    r = await token(anon, "JONAS@example.com", account.password_hash)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "Bearer" and body["refresh_token"]
    assert body["Kdf"] == 0 and body["KdfIterations"] == 600000
    unlock = body["UserDecryptionOptions"]["MasterPasswordUnlock"]
    assert unlock["salt"] == "jonas@example.com"
    # De client kan de user key uitpakken met zijn master key
    assert unwrap_user_key(account.mkey, body["Key"]) == account.user_key
    assert body["AccountKeys"]["publicKeyEncryptionKeyPair"]["wrappedPrivateKey"] == body["PrivateKey"]
    claims = jwt_claims(body["access_token"])
    me = (await c.get("/api/v1/me")).json()
    assert claims["sub"] == me["id"]
    assert claims["email"] == "jonas@example.com"
    assert claims["premium"] is True

    # Refresh levert een nieuw access token voor hetzelfde apparaat
    r = await anon.post(
        "/identity/connect/token",
        data={"grant_type": "refresh_token", "refresh_token": body["refresh_token"], "client_id": "cli"},
    )
    assert r.status_code == 200, r.text
    assert jwt_claims(r.json()["access_token"])["device"] == claims["device"]

    st = (await c.get("/api/v1/me/vault")).json()
    assert [d["type_name"] for d in st["devices"]] == ["Linux-CLI"]


async def test_sync_and_item_lifecycle(admin, make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account)

    sync = (await c.get("/api/sync", headers=h)).json()
    assert sync["ciphers"] == [] and sync["folders"] == []
    assert sync["profile"]["email"] == "jonas@example.com"
    assert sync["profile"]["key"]
    rev0 = (await c.get("/api/accounts/revision-date", headers=h)).json()

    folder = (await c.post("/api/folders", json={"name": account.enc("Infra")}, headers=h)).json()
    item = login_item(account, "Grafana", "admin", "s3cret!", folderId=folder["id"])
    r = await c.post("/api/ciphers", json=item, headers=h)
    assert r.status_code == 200, r.text
    cipher = r.json()
    assert cipher["folderId"] == folder["id"]
    assert account.dec(cipher["name"]) == "Grafana"
    assert account.dec(cipher["login"]["password"]) == "s3cret!"
    assert (await c.get("/api/accounts/revision-date", headers=h)).json() > rev0

    # Bijwerken met een verouderde revisiedatum wordt geweigerd
    stale = {
        **login_item(account, "Grafana", "admin", "nieuw"),
        "lastKnownRevisionDate": "2020-01-01T00:00:00Z",
    }
    r = await c.put(f"/api/ciphers/{cipher['id']}", json=stale, headers=h)
    assert r.status_code == 400
    assert "message" in r.json()
    fresh = {
        **login_item(account, "Grafana", "admin", "nieuw"),
        "lastKnownRevisionDate": cipher["revisionDate"],
    }
    r = await c.put(f"/api/ciphers/{cipher['id']}", json=fresh, headers=h)
    assert r.status_code == 200, r.text
    assert account.dec(r.json()["login"]["password"]) == "nieuw"
    assert r.json()["folderId"] is None

    # Prullenbak, terugzetten, definitief verwijderen
    assert (await c.put(f"/api/ciphers/{cipher['id']}/delete", headers=h)).status_code == 200
    synced = (await c.get("/api/sync", headers=h)).json()["ciphers"]
    assert synced[0]["deletedDate"] is not None
    assert (await c.put(f"/api/ciphers/{cipher['id']}/restore", headers=h)).json()["deletedDate"] is None
    assert (await c.delete(f"/api/ciphers/{cipher['id']}", headers=h)).status_code == 200
    assert (await c.get(f"/api/ciphers/{cipher['id']}", headers=h)).status_code == 404

    # Map verwijderen
    assert (await c.delete(f"/api/folders/{folder['id']}", headers=h)).status_code == 200
    assert (await c.get("/api/sync", headers=h)).json()["folders"] == []

    st = (await c.get("/api/v1/me/vault")).json()
    assert st["item_count"] == 0

    actions = [
        e["action"] for e in (await admin.get("/api/v1/audit", params={"action": "vault."})).json()["items"]
    ]
    assert {
        "vault.enroll",
        "vault.login",
        "vault.item.create",
        "vault.item.update",
        "vault.item.delete",
    } <= set(actions)


async def test_unknown_fields_survive_round_trip(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account)
    item = login_item(account, "Note", "", "", type=2, secureNote={"type": 0}, futureField={"x": 1})
    item.pop("login")
    r = await c.post("/api/ciphers", json=item, headers=h)
    assert r.status_code == 200, r.text
    got = (await c.get(f"/api/ciphers/{r.json()['id']}", headers=h)).json()
    assert got["type"] == 2 and got["secureNote"] == {"type": 0} and got["futureField"] == {"x": 1}

    r = await c.post(
        "/api/ciphers", json={**item, "organizationId": "00000000-0000-0000-0000-000000000001"}, headers=h
    )
    assert r.status_code == 400


async def test_users_cannot_see_each_others_items(make_client):
    a = await make_client()
    alice = await enroll(a, "alice")
    ha = await bearer(a, alice)
    cipher = (await a.post("/api/ciphers", json=login_item(alice, "x", "y", "z"), headers=ha)).json()

    b = await make_client()
    bob = await enroll(b, "bob")
    hb = await bearer(b, bob)
    assert (await b.get(f"/api/ciphers/{cipher['id']}", headers=hb)).status_code == 404
    assert (await b.put(f"/api/ciphers/{cipher['id']}/delete", headers=hb)).status_code == 404
    assert (await b.get("/api/sync", headers=hb)).json()["ciphers"] == []


async def test_bad_tokens_rejected(make_client):
    c = await make_client()
    assert (await c.get("/api/sync")).status_code == 401
    assert (await c.get("/api/sync", headers={"Authorization": "Bearer nope"})).status_code == 401
    account = await enroll(c, "jonas")
    h = await bearer(c, account)
    forged = h["Authorization"][:-4] + "AAAA"
    assert (await c.get("/api/sync", headers={"Authorization": forged})).status_code == 401


async def test_revoked_device_and_deactivated_user_lose_access(admin, make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    r = await token(c, account.email, account.password_hash, "dev-1")
    refresh = r.json()["refresh_token"]
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    h2 = await bearer(c, account, "dev-2")

    devices = (await c.get("/api/v1/me/vault")).json()["devices"]
    assert len(devices) == 2
    r = await c.post_json(f"/api/v1/me/vault/devices/{devices[0]['id']}/revoke", {})
    assert r.status_code == 200 and r.json()["revoked_at"]
    statuses = {
        (await c.get("/api/sync", headers=h)).status_code,
        (await c.get("/api/sync", headers=h2)).status_code,
    }
    assert statuses == {200, 401}

    # Gedeactiveerd in VaultX: tokens, refresh en nieuwe logins werken niet meer
    me = (await c.get("/api/v1/me")).json()
    r = await admin.patch_json(f"/api/v1/users/{me['id']}", {"is_active": False})
    assert r.status_code == 200
    assert (await c.get("/api/sync", headers=h2)).status_code == 401
    r = await c.post(
        "/identity/connect/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh, "client_id": "cli"},
    )
    assert r.status_code == 400
    r = await token(c, account.email, account.password_hash, "dev-3")
    assert r.status_code == 400
    assert "gedeactiveerd" in r.json()["ErrorModel"]["Message"]


async def test_reset_invalidates_tokens(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account)
    r = await c.delete_("/api/v1/me/vault")
    assert r.status_code == 204
    assert (await c.get("/api/sync", headers=h)).status_code == 401
    assert (await c.get("/api/v1/me/vault")).json()["enrolled"] is False
    # Daarna opnieuw activeren kan
    _, payload = new_enrollment("jonas@example.com", "ander wachtwoord")
    assert (await c.post_json("/api/v1/me/vault", payload)).status_code == 201


async def test_lockout_after_failed_logins(make_client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "vault_max_failed_logins", 3)
    c = await make_client()
    account = await enroll(c, "jonas")
    for _ in range(3):
        assert (await token(c, account.email, "d3Jvbmc=")).status_code == 400
    r = await token(c, account.email, account.password_hash)
    assert r.status_code == 400
    assert r.json()["error_description"] == "account_locked"


async def test_config_and_registration(make_client):
    c = await make_client()
    cfg = (await c.get("/api/config")).json()
    assert cfg["server"]["name"] == "VaultX"
    assert cfg["environment"]["identity"].endswith("/identity")
    assert cfg["settings"]["disableUserRegistration"] is True
    r = await c.post("/identity/accounts/register/send-verification-email", json={"email": "x@example.com"})
    assert r.status_code == 400
    assert "/vault" in r.json()["message"]
    r = await c.post("/identity/connect/token", data={"grant_type": "client_credentials", "client_id": "x"})
    assert r.json()["error"] == "unsupported_grant_type"


@pytest.mark.parametrize("path", ["/api/v1/me/vault"])
async def test_vault_api_requires_login(make_client, path):
    c = await make_client()
    assert (await c.get(path)).status_code == 401


async def test_user_key_id_backfill(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account)
    assert (await c.get("/api/sync", headers=h)).json()["userDecryption"]["userKeyId"] is None
    r = await c.post("/api/accounts/key-management/user-key-id", json={"userKeyId": "a1b2-c3"}, headers=h)
    assert r.status_code == 200, r.text
    assert (await c.get("/api/sync", headers=h)).json()["userDecryption"]["userKeyId"] == "a1b2-c3"
    r = await c.post("/api/accounts/key-management/user-key-id", json={"userKeyId": "<script>"}, headers=h)
    assert r.status_code == 400
