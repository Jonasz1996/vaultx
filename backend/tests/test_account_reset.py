"""Accountherstel: `app.cli reset-account` en het tijdelijke wachtwoord bij de eerste login."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app import cli
from app.core.db import get_sessionmaker
from app.core.errors import VaultXError
from app.models import AuditLog, User
from tests.soft_webauthn import SoftKey
from tests.test_bitwarden_parity import _enable_2fa
from tests.test_password_login import create, pw_login
from tests.test_vault import enroll, token
from tests.test_webauthn import add_login_key


async def change(c, ticket, new):
    return await c.post_json("/auth/password/change-temporary", {"ticket": ticket, "new_password": new})


async def test_reset_account_full_flow(admin, make_client):
    u = await create(admin)
    c = await make_client()
    assert (await pw_login(c, "carol")).status_code == 200
    await add_login_key(c, SoftKey())

    res = await cli.reset_account("carol@example.com")
    assert re.fullmatch(r"([A-Za-z2-9]{5}-){3}[A-Za-z2-9]{5}", res.password)
    assert res.login_keys == 1 and res.sessions_revoked >= 1
    assert timedelta(minutes=55) < res.valid_until - datetime.now(UTC) <= timedelta(minutes=60)
    # Bestaande sessie ligt eruit; het oude wachtwoord werkt niet meer
    assert (await c.get("/api/v1/me")).status_code == 401
    assert (await pw_login(c, "carol")).status_code == 401

    # Tijdelijk wachtwoord: geen sessie, wel een ticket om een eigen wachtwoord te kiezen
    c2 = await make_client()
    r = await pw_login(c2, "carol", res.password)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["password_change"] == "required" and "redirect_url" not in body
    assert "second_step" not in body  # de sleutel is weg
    assert (await c2.get("/api/v1/me")).status_code == 401

    # Vervalst ticket, leeg of hetzelfde wachtwoord: geweigerd
    assert (await change(c2, body["ticket"] + "x", "nieuw-eigen-wachtwoord")).status_code == 401
    assert (await change(c2, body["ticket"], res.password)).status_code == 422
    r = await c2.post("/auth/password/change-temporary", json={"ticket": body["ticket"], "new_password": "x"})
    assert r.status_code == 403  # CSRF-header

    r = await change(c2, body["ticket"], "nieuw-eigen-wachtwoord")
    assert r.status_code == 200, r.text
    assert r.json()["redirect_url"] == f"/users/{u['id']}?hersteld=1"
    me = (await c2.get("/api/v1/me")).json()
    assert me["username"] == "carol"
    assert (await c2.get("/api/v1/me/security-keys")).json()["keys"] == []
    # Ticket is maar één keer bruikbaar (wachtwoord is gewijzigd)
    assert (await change(c2, body["ticket"], "nog-een-ander")).status_code == 401
    # Tijdelijk wachtwoord werkt niet meer, het nieuwe wel en meteen
    c3 = await make_client()
    assert (await pw_login(c3, "carol", res.password)).status_code == 401
    assert "redirect_url" in (await pw_login(c3, "carol", "nieuw-eigen-wachtwoord")).json()

    async with get_sessionmaker()() as db:
        actions = (await db.scalars(select(AuditLog.action).where(AuditLog.target_id == u["id"]))).all()
    assert "user.account_reset" in actions and "user.password_change" in actions


async def test_temporary_password_expires(admin, make_client):
    await create(admin)
    res = await cli.reset_account("carol")
    c = await make_client()
    ticket = (await pw_login(c, "carol", res.password)).json()["ticket"]
    async with get_sessionmaker()() as db:
        await db.execute(
            update(User)
            .where(User.username == "carol")
            .values(password_temporary_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
    r = await pw_login(c, "carol", res.password)
    assert r.status_code == 401 and "verlopen" in r.text
    assert (await change(c, ticket, "nieuw-eigen-wachtwoord")).status_code == 401
    # Een nieuwe reset geeft een nieuw, werkend tijdelijk wachtwoord
    res2 = await cli.reset_account("carol", minutes=10)
    assert (await pw_login(c, "carol", res2.password)).json()["password_change"] == "required"


async def test_reset_account_vault_two_factor_but_not_vault_key(make_client):
    c = await make_client()
    account = await enroll(c, "dora")
    await _enable_2fa(c, account)
    assert (await token(c, account.email, account.password_hash)).status_code == 400

    # Met --keep-vault-2fa blijft de tweestapsverificatie van de kluis staan
    res = await cli.reset_account("dora", keep_vault_2fa=True)
    assert res.vault_two_factor is False
    assert (await token(c, account.email, account.password_hash)).status_code == 400

    # Standaard gaat ze uit; het master password blijft hetzelfde (kluis niet geraakt)
    res = await cli.reset_account("dora@example.com", password="mijn-tijdelijke")
    assert res.vault_two_factor is True and res.password == "mijn-tijdelijke"
    r = await token(c, account.email, account.password_hash)
    assert r.status_code == 200, r.text
    # Authentik-account kreeg een (tijdelijk) VaultX-wachtwoord; Authentik-sessie ingetrokken
    assert (await c.get("/api/v1/me")).status_code == 401
    assert (await pw_login(c, "dora", "mijn-tijdelijke")).json()["password_change"] == "required"


async def test_reset_account_errors(admin):
    await create(admin)
    with pytest.raises(VaultXError):
        await cli.reset_account("niemand@example.com")
    with pytest.raises(VaultXError):
        await cli.reset_account("carol", minutes=1)


async def test_admin_password_reset_clears_temporary(admin, make_client):
    u = await create(admin)
    await cli.reset_account("carol")
    r = await admin.put(
        f"/api/v1/users/{u['id']}/password",
        json={"password": "door-beheerder-gezet"},
        headers=admin.mutating_headers(),
    )
    assert r.status_code in (200, 204), r.text
    c = await make_client()
    assert "redirect_url" in (await pw_login(c, "carol", "door-beheerder-gezet")).json()


def test_cli_main_prints_password(monkeypatch, capsys):
    from app.cli import AccountReset

    async def fake(login=None, **kw):
        return AccountReset(
            User(username=login, email=None),
            "abcde-fghjk-mnpqr-stuvw",
            datetime.now(UTC) + timedelta(hours=1),
            2,
            True,
            3,
            False,
        )

    monkeypatch.setattr(cli, "reset_account", fake)
    assert cli.main(["reset-account", "carol.ABCD-EFGH-JKLM-NPQR"]) == 0
    out = capsys.readouterr().out
    assert "abcde-fghjk-mnpqr-stuvw" in out and "master password" in out


# --- Herstelcode vanaf de loginpagina -----------------------------------------------------


async def request_code(c, login):
    return await c.post_json("/auth/password/reset-request", {"login": login})


async def test_reset_request_same_answer_for_any_login(admin, make_client):
    await create(admin)
    c = await make_client()
    a = await request_code(c, "Carol@Example.com ")
    b = await request_code(c, "bestaat.niet@example.com")
    assert a.status_code == b.status_code == 200
    assert set(a.json()) == set(b.json()) == {"code", "expires_at"}
    assert re.fullmatch(r"carol@example\.com\.([A-Z2-9]{4}-){3}[A-Z2-9]{4}", a.json()["code"])
    assert b.json()["code"].startswith("bestaat.niet@example.com.")
    # Zonder CSRF-header geen code
    r = await c.post("/auth/password/reset-request", json={"login": "carol"})
    assert r.status_code == 403


async def test_reset_with_code(admin, make_client):
    u = await create(admin)
    c = await make_client()
    code = (await request_code(c, "carol@example.com")).json()["code"]

    # Zelfverzonnen of aangepaste codes werken niet
    login_part, secret = code.rsplit(".", 1)
    for bad in (f"admin.{secret}", f"{login_part}.AAAA-BBBB-CCCC-DDDD", "carol", f"{login_part}.{secret}X"):
        with pytest.raises(VaultXError):
            await cli.reset_account(code=bad)

    # Spaties rond de code (plakken) en hoofdletters in de login maken niet uit
    res = await cli.reset_account(code=f"  {code.replace('carol', 'Carol')} ")
    assert res.user.id is not None and str(res.user.id) == u["id"]
    assert (await pw_login(c, "carol", res.password)).json()["password_change"] == "required"
    # Eenmalig
    with pytest.raises(VaultXError, match="Onbekende|al gebruikt"):
        await cli.reset_account(code=code)

    async with get_sessionmaker()() as db:
        details = (
            await db.scalars(
                select(AuditLog.details).where(
                    AuditLog.action == "user.account_reset", AuditLog.target_id == u["id"]
                )
            )
        ).one()
    assert details["via"] == "herstelcode" and details["request_id"]


async def test_reset_code_expires_and_is_replaced(admin, make_client):
    from app.models import AccountResetRequest

    await create(admin)
    c = await make_client()
    old = (await request_code(c, "carol")).json()["code"]
    new = (await request_code(c, "carol")).json()["code"]
    with pytest.raises(VaultXError):
        await cli.reset_account(code=old)  # vervangen door de nieuwe
    async with get_sessionmaker()() as db:
        await db.execute(
            update(AccountResetRequest).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
    with pytest.raises(VaultXError, match="verlopen"):
        await cli.reset_account(code=new)


async def test_valid_code_for_unknown_account_changes_nothing(make_client):
    c = await make_client()
    code = (await request_code(c, "niemand@example.com")).json()["code"]
    with pytest.raises(VaultXError, match="geen eenduidig VaultX-account"):
        await cli.reset_account(code=code)
    with pytest.raises(VaultXError, match="al gebruikt|Onbekende"):
        await cli.reset_account(code=code)


async def test_reset_requests_are_capped(monkeypatch, make_client):
    from sqlalchemy import func

    from app.models import AccountResetRequest
    from app.services import account_reset

    monkeypatch.setattr(account_reset, "MAX_OPEN_REQUESTS", 3)
    c = await make_client()
    for i in range(6):
        assert (await request_code(c, f"spam{i}")).status_code == 200
    async with get_sessionmaker()() as db:
        assert await db.scalar(select(func.count()).select_from(AccountResetRequest)) == 3


def test_cli_requires_code_or_login(capsys):
    assert cli.main(["reset-account"]) == 1
    assert "herstelcode" in capsys.readouterr().err
