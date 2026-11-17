"""Ontgrendelen met Authentik (apparaatsleutel): registreren, openen, intrekken en de eisen."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.models import AuditLog, UserSession
from tests.bw_crypto import encrypt, new_enrollment
from tests.test_password_login import create, pw_login

PASSWORD = "correct horse battery staple"
MFA = {"amr": ["pwd", "mfa"]}
BASE = "/api/v1/vault/device-unlock"


def device_blob(user_key: bytes) -> tuple[bytes, str]:
    """Zoals de extensie: willekeurige apparaatsleutel D, user key versleuteld met D."""
    d = os.urandom(64)
    return d, encrypt(user_key, d[:32], d[32:])


async def setup(make_client, sub="jonas", claims=MFA):
    c = await make_client()
    await c.login(sub, extra={**claims, "auth_time": int(time.time())})
    account, payload = new_enrollment(f"{sub}@example.com", PASSWORD)
    assert (await c.post_json("/api/v1/me/vault", payload)).status_code == 201
    return c, account


async def register(c, account, identifier="ext-chrome-0001"):
    _, blob = device_blob(account.user_key)
    r = await c.post_json(
        BASE, {"device_identifier": identifier, "name": "VaultX Invuller (Chrome)", "blob": blob}
    )
    return r, blob


async def test_register_open_and_list(make_client):
    c, account = await setup(make_client)
    st = (await c.get(BASE)).json()
    assert st["eligible"] and st["server_enabled"] and st["user_enabled"] and st["require_mfa"]
    r, blob = await register(c, account)
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    r = await c.post_json(f"{BASE}/{uid}/open", {})
    assert r.status_code == 200 and r.json() == {"blob": blob}
    assert r.headers["cache-control"] == "no-store"
    devices = (await c.get(BASE)).json()["devices"]
    assert [d["name"] for d in devices] == ["VaultX Invuller (Chrome)"] and devices[0]["last_used_at"]
    assert (await c.get(f"{BASE}/{uid}")).json()["active"] is True
    async with get_sessionmaker()() as db:
        actions = list(
            await db.scalars(select(AuditLog.action).where(AuditLog.action.like("vault.device_unlock%")))
        )
    assert actions == ["vault.device_unlock.register", "vault.device_unlock.open"]


async def test_reregister_replaces_old_blob(make_client):
    c, account = await setup(make_client)
    first, _ = await register(c, account)
    second, blob = await register(c, account)
    assert (await c.post_json(f"{BASE}/{first.json()['id']}/open", {})).status_code == 403
    assert (await c.post_json(f"{BASE}/{second.json()['id']}/open", {})).json()["blob"] == blob
    assert len((await c.get(BASE)).json()["devices"]) == 1


async def test_without_mfa_refused_and_audited(make_client):
    c, account = await setup(make_client, claims={"amr": ["pwd"]})
    st = (await c.get(BASE)).json()
    assert st["eligible"] is False and st["reason"] == "mfa_required"
    r, _ = await register(c, account)
    assert r.status_code == 403 and "MFA" in r.json()["detail"]
    async with get_sessionmaker()() as db:
        rows = list(
            await db.scalars(select(AuditLog).where(AuditLog.action == "vault.device_unlock.register"))
        )
    assert rows and rows[0].outcome == "denied" and rows[0].details["reason"] == "mfa_required"


async def test_mfa_requirement_can_be_turned_off(make_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "device_unlock_require_mfa", False)
    c, account = await setup(make_client, claims={"amr": ["pwd"]})
    r, _ = await register(c, account)
    assert r.status_code == 201


async def test_old_authentik_login_refused(make_client):
    c, account = await setup(make_client)
    r, _ = await register(c, account)
    uid = r.json()["id"]
    async with get_sessionmaker()() as db:
        await db.execute(update(UserSession).values(idp_auth_time=datetime.now(UTC) - timedelta(hours=13)))
        await db.commit()
    r = await c.post_json(f"{BASE}/{uid}/open", {})
    assert r.status_code == 403 and "te oud" in r.json()["detail"]


async def test_revoked_device_and_other_users_device(make_client):
    c, account = await setup(make_client)
    r, _ = await register(c, account)
    uid = r.json()["id"]
    other, _ = await setup(make_client, sub="eve")
    assert (await other.post_json(f"{BASE}/{uid}/open", {})).status_code == 404
    assert (await other.delete_(f"{BASE}/{uid}")).status_code == 404
    assert (await c.delete_(f"{BASE}/{uid}")).status_code == 204
    r = await c.post_json(f"{BASE}/{uid}/open", {})
    assert r.status_code == 403 and "ingetrokken" in r.json()["detail"]
    assert (await c.get(BASE)).json()["devices"] == []


async def test_password_session_bearer_and_csrf(make_client, admin):
    # Gewone VaultX-login (wachtwoord): geen Authentik, dus niet.
    await create(admin)
    carol = await make_client()
    assert (await pw_login(carol, "carol")).status_code == 200
    _, payload = new_enrollment("carol@example.com", PASSWORD)
    assert (await carol.post_json("/api/v1/me/vault", payload)).status_code == 201
    assert (await carol.get(BASE)).json()["reason"] == "not_authentik"
    # Een Bitwarden-token is geen VaultX-sessie.
    c, account = await setup(make_client)
    r, _ = await register(c, account)
    uid = r.json()["id"]
    from tests.test_vault import bearer

    h = await bearer(c, account)
    fresh = await make_client()
    assert (await fresh.post(f"{BASE}/{uid}/open", headers=h)).status_code == 401
    # Zonder CSRF-header (een andere website) niet.
    assert (await c.post(f"{BASE}/{uid}/open")).status_code == 403


async def test_password_change_and_disable_revoke_all(make_client):
    from tests.bw_crypto import master_key, master_password_hash, wrap_user_key

    c, account = await setup(make_client)
    r, _ = await register(c, account)
    uid = r.json()["id"]
    new_pw = "een nieuw en lang master password"
    mkey = master_key(new_pw, account.email)
    body = {
        "current_master_password_hash": account.password_hash,
        "kdf": 0,
        "kdf_iterations": 600000,
        "kdf_memory": None,
        "kdf_parallelism": None,
        "master_password_hash": master_password_hash(mkey, new_pw),
        "user_key": wrap_user_key(mkey, account.user_key),
    }
    assert (await c.post_json("/api/v1/me/vault/password", body)).status_code == 200
    assert (await c.post_json(f"{BASE}/{uid}/open", {})).status_code == 403
    assert (await c.get(BASE)).json()["devices"] == []

    r, _ = await register(c, account, "ext-chrome-0002")
    assert (
        await c.put(f"{BASE}/settings", json={"enabled": False}, headers=c.mutating_headers())
    ).json() == {"enabled": False}
    st = (await c.get(BASE)).json()
    assert st["devices"] == [] and st["reason"] == "user_disabled"
    assert (await register(c, account, "ext-chrome-0003"))[0].status_code == 403


async def test_server_switch(make_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "device_unlock", False)
    c, account = await setup(make_client)
    assert (await c.get(BASE)).json()["reason"] == "server_disabled"
    assert (await register(c, account))[0].status_code == 403


async def test_bad_input(make_client):
    c, _ = await setup(make_client)
    r = await c.post_json(
        BASE, {"device_identifier": "ext-chrome-0001", "name": "x", "blob": "geen encstring" * 3}
    )
    assert r.status_code == 422
    r = await c.post_json(BASE, {"device_identifier": "../../etc", "name": "x", "blob": "2." + "A" * 40})
    assert r.status_code == 422


async def test_backchannel_logout_ends_unlock(make_client, fake_oidc):
    c, account = await setup(make_client)
    r, _ = await register(c, account)
    uid = r.json()["id"]
    async with get_sessionmaker()() as db:
        sid = (await db.scalars(select(UserSession.oidc_sid))).first()
    token = fake_oidc.logout_token(sid=sid)
    assert (await c.post("/auth/backchannel-logout", data={"logout_token": token})).status_code == 200
    assert (await c.post_json(f"{BASE}/{uid}/open", {})).status_code == 401
