"""Fase 8: gewone accounts met een VaultX-wachtwoord, naast Authentik."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app import cli
from app.core.db import get_sessionmaker
from app.core.errors import VaultXError
from app.models import AuditLog, User
from tests.bw_crypto import new_enrollment
from tests.test_vault import token

PW = "een-lang-genoeg-wachtwoord"


async def create(admin, username="carol", email="carol@example.com", password=PW, **extra):
    r = await admin.post_json(
        "/api/v1/users", {"username": username, "email": email, "password": password, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def pw_login(client, login, password=PW, next=None):
    return await client.post_json("/auth/password", {"login": login, "password": password, "next": next})


async def test_methods_endpoint(make_client):
    c = await make_client()
    assert (await c.get("/auth/methods")).json() == {"authentik": True, "password": True}


async def test_admin_creates_account_and_user_logs_in(admin, make_client):
    u = await create(admin, display_name="Carol")
    assert u["has_password"] is True and u["has_authentik"] is False
    assert u["oidc_issuer"] is None and u["is_admin"] is False

    c = await make_client()
    r = await pw_login(c, "CAROL", next="/vault")
    assert r.status_code == 200, r.text
    assert r.json()["redirect_url"] == "/vault"
    me = (await c.get("/api/v1/me")).json()
    assert me["username"] == "carol" and me["is_admin"] is False
    # Ook met het e-mailadres.
    c2 = await make_client()
    assert (await pw_login(c2, "carol@example.com")).status_code == 200
    sessions = (await c.get("/api/v1/me/sessions")).json()
    assert {s["auth_method"] for s in sessions} == {"password"}


async def test_open_redirect_blocked(admin, make_client):
    await create(admin)
    c = await make_client()
    r = await pw_login(c, "carol", next="//evil.example.com/")
    assert r.json()["redirect_url"] == "/"


async def test_login_requires_csrf_header(admin, make_client):
    await create(admin)
    c = await make_client()
    r = await c.post("/auth/password", json={"login": "carol", "password": PW})
    assert r.status_code == 403
    assert (await c.get("/api/v1/me")).status_code == 401


async def test_wrong_password_and_unknown_user_look_the_same(admin, make_client):
    await create(admin)
    c = await make_client()
    a = await pw_login(c, "carol", "fout-wachtwoord-123")
    b = await pw_login(c, "niemand", "fout-wachtwoord-123")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()
    # Een Authentik-account zonder wachtwoord kan niet met een wachtwoord inloggen.
    assert (await pw_login(c, "admin", "wat-dan-ook-12345")).status_code == 401


async def test_lockout_after_too_many_failures(admin, make_client):
    await create(admin)
    c = await make_client()
    for _ in range(5):
        assert (await pw_login(c, "carol", "fout-wachtwoord-123")).status_code == 401
    r = await pw_login(c, "carol")
    assert r.status_code == 429
    # Een beheerder die het wachtwoord reset, heft de vergrendeling op.
    users = (await admin.get("/api/v1/users", params={"q": "carol"})).json()["items"]
    detail = (await admin.get(f"/api/v1/users/{users[0]['id']}")).json()
    assert detail["locked_until"] is not None
    r = await admin.put(
        f"/api/v1/users/{detail['id']}/password",
        json={"password": "nog-een-ander-wachtwoord"},
        headers=admin.mutating_headers(),
    )
    assert r.status_code == 200
    assert (await pw_login(c, "carol", "nog-een-ander-wachtwoord")).status_code == 200
    async with get_sessionmaker()() as db:
        outcomes = (await db.scalars(select(AuditLog.outcome).where(AuditLog.action == "auth.login"))).all()
    assert outcomes.count("failure") == 5 and "denied" in outcomes


async def test_lock_expires(admin, make_client):
    u = await create(admin)
    async with get_sessionmaker()() as db:
        await db.execute(
            update(User)
            .where(User.username == "carol")
            .values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
    c = await make_client()
    assert (await pw_login(c, "carol")).status_code == 200
    assert u["id"]


async def test_deactivated_account_cannot_log_in(admin, make_client):
    u = await create(admin)
    c = await make_client()
    assert (await pw_login(c, "carol")).status_code == 200
    await admin.patch_json(f"/api/v1/users/{u['id']}", {"is_active": False})
    assert (await c.get("/api/v1/me")).status_code == 401
    r = await pw_login(c, "carol")
    assert r.status_code == 401 and "gedeactiveerd" in r.json()["detail"]


async def test_password_policy_and_duplicates(admin):
    # Geen eisen aan het wachtwoord, behalve niet leeg.
    r = await admin.post_json("/api/v1/users", {"username": "kort", "email": "k@example.com", "password": ""})
    assert r.status_code == 422
    await create(admin, username="kort", email="k@example.com", password="kort")
    await create(admin, username="dave", email="dave@example.com", password="Dave@example.com")
    await create(admin)
    # Gebruikersnaam of e-mail van een ander account (ook kruiselings) wordt geweigerd.
    for username, email in [
        ("carol", "x@example.com"),
        ("xx", "CAROL@example.com"),
        ("carol@example.com", "y@e.be"),
    ]:
        r = await admin.post_json("/api/v1/users", {"username": username, "email": email, "password": PW})
        assert r.status_code == 409, (username, email, r.text)
    # Ook de gebruikersnaam van een Authentik-account.
    r = await admin.post_json(
        "/api/v1/users", {"username": "admin", "email": "z@example.com", "password": PW}
    )
    assert r.status_code == 409


async def test_only_admins_manage_accounts(admin, make_client):
    u = await create(admin)
    c = await make_client()
    await c.login("bob")
    r = await c.post_json("/api/v1/users", {"username": "eve", "email": "eve@example.com", "password": PW})
    assert r.status_code == 403
    r = await c.put(f"/api/v1/users/{u['id']}/password", json={"password": PW}, headers=c.mutating_headers())
    assert r.status_code == 403
    r = await c.patch_json(f"/api/v1/users/{u['id']}", {"is_admin": True})
    assert r.status_code == 403


async def test_regular_account_can_be_admin(admin, make_client):
    u = await create(admin, is_admin=True)
    assert u["is_admin"] is True and u["admin_granted"] is True
    c = await make_client()
    await pw_login(c, "carol")
    assert (await c.get("/api/v1/me")).json()["is_admin"] is True
    # Intrekken door een andere beheerder werkt, eigen rechten intrekken niet.
    r = await c.patch_json(f"/api/v1/users/{u['id']}", {"is_admin": False})
    assert r.status_code == 422
    r = await admin.patch_json(f"/api/v1/users/{u['id']}", {"is_admin": False})
    assert r.json()["is_admin"] is False
    assert (await c.get("/api/v1/me")).json()["is_admin"] is False


async def test_admin_granted_survives_authentik_login(admin, make_client):
    c = await make_client()
    await c.login("frank")
    me = (await c.get("/api/v1/me")).json()
    r = await admin.patch_json(f"/api/v1/users/{me['id']}", {"is_admin": True})
    assert r.json()["is_admin"] is True
    await c.login("frank")  # zonder vaultx-admins-groep
    assert (await c.get("/api/v1/me")).json()["is_admin"] is True
    # Wie beheerder is via de Authentik-groep, blijft dat ook zonder toekenning in VaultX.
    admin_me = (await admin.get("/api/v1/me")).json()
    assert admin_me["admin_granted"] is False and admin_me["is_admin"] is True


async def test_authentik_user_gets_password_same_account(admin, make_client):
    c = await make_client()
    await c.login("gina", email="gina@example.com")
    gina = (await c.get("/api/v1/me")).json()
    r = await admin.put(
        f"/api/v1/users/{gina['id']}/password", json={"password": PW}, headers=admin.mutating_headers()
    )
    assert r.status_code == 200 and r.json()["has_password"] and r.json()["has_authentik"]

    p = await make_client()
    assert (await pw_login(p, "gina")).status_code == 200
    assert (await p.get("/api/v1/me")).json()["id"] == gina["id"]
    # Naam en e-mail van een Authentik-account komen uit Authentik.
    r = await admin.patch_json(f"/api/v1/users/{gina['id']}", {"display_name": "Andere naam"})
    assert r.status_code == 422

    # Wachtwoord weghalen: wachtwoordsessies weg, Authentik-sessie blijft.
    r = await admin.delete_(f"/api/v1/users/{gina['id']}/password")
    assert r.status_code == 200 and r.json()["has_password"] is False
    assert (await p.get("/api/v1/me")).status_code == 401
    assert (await c.get("/api/v1/me")).status_code == 200
    assert (await pw_login(p, "gina")).status_code == 401


async def test_password_of_regular_only_account_cannot_be_removed(admin):
    u = await create(admin)
    r = await admin.delete_(f"/api/v1/users/{u['id']}/password")
    assert r.status_code == 422


async def test_admin_reset_revokes_sessions(admin, make_client):
    u = await create(admin)
    c = await make_client()
    await pw_login(c, "carol")
    r = await admin.put(
        f"/api/v1/users/{u['id']}/password",
        json={"password": "helemaal-nieuw-wachtwoord"},
        headers=admin.mutating_headers(),
    )
    assert r.status_code == 200
    assert (await c.get("/api/v1/me")).status_code == 401
    assert (await pw_login(c, "carol")).status_code == 401
    assert (await pw_login(c, "carol", "helemaal-nieuw-wachtwoord")).status_code == 200


async def test_change_own_password(admin, make_client):
    await create(admin)
    c, other = await make_client(), await make_client()
    await pw_login(c, "carol")
    await pw_login(other, "carol")
    r = await c.post_json(
        "/api/v1/me/password", {"current_password": "fout", "new_password": "nieuw-wachtwoord-1"}
    )
    assert r.status_code == 422
    r = await c.post_json("/api/v1/me/password", {"current_password": PW, "new_password": ""})
    assert r.status_code == 422
    r = await c.post_json(
        "/api/v1/me/password", {"current_password": PW, "new_password": "nieuw-wachtwoord-1"}
    )
    assert r.status_code == 200
    # Huidige sessie blijft, andere sessies zijn ingetrokken.
    assert (await c.get("/api/v1/me")).status_code == 200
    assert (await other.get("/api/v1/me")).status_code == 401
    assert (await pw_login(other, "carol", "nieuw-wachtwoord-1")).status_code == 200


async def test_authentik_user_without_password_cannot_change_it(make_client):
    c = await make_client()
    await c.login("hank")
    r = await c.post_json(
        "/api/v1/me/password", {"current_password": "x", "new_password": "nieuw-wachtwoord-1"}
    )
    assert r.status_code == 422


async def test_logout_of_password_session_skips_authentik(admin, make_client):
    await create(admin)
    c = await make_client()
    await pw_login(c, "carol")
    r = await c.post("/auth/logout", headers={"X-VaultX-CSRF": "1"})
    assert r.json()["redirect_url"] == "/login?logged_out=1"
    assert (await c.get("/api/v1/me")).status_code == 401


async def test_regular_account_profile_edit(admin):
    u = await create(admin)
    await create(admin, username="dora", email="dora@example.com")
    r = await admin.patch_json(
        f"/api/v1/users/{u['id']}", {"display_name": "Carol C", "email": "C2@example.com"}
    )
    assert r.status_code == 200
    assert r.json()["display_name"] == "Carol C" and r.json()["email"] == "c2@example.com"
    r = await admin.patch_json(f"/api/v1/users/{u['id']}", {"username": "dora"})
    assert r.status_code == 409


async def test_regular_account_can_use_vault_and_bitwarden(admin, make_client):
    await create(admin)
    c = await make_client()
    await pw_login(c, "carol")
    account, payload = new_enrollment("carol@example.com", "master password los van login")
    r = await c.post_json("/api/v1/me/vault", payload)
    assert r.status_code == 201, r.text
    r = await token(c, account.email, account.password_hash)
    assert r.status_code == 200, r.text


async def test_cli_create_and_reset(make_client):
    user = await cli.create_user("root", "Root@example.com", None, True, "cli-wachtwoord-12345")
    assert user.email == "root@example.com"
    c = await make_client()
    assert (await pw_login(c, "root", "cli-wachtwoord-12345")).status_code == 200
    assert (await c.get("/api/v1/me")).json()["is_admin"] is True
    await cli.reset_password("root@example.com", "tweede-wachtwoord-123")
    assert (await c.get("/api/v1/me")).status_code == 401
    assert (await pw_login(c, "root", "tweede-wachtwoord-123")).status_code == 200
    with pytest.raises(VaultXError):
        await cli.reset_password("niemand", "tweede-wachtwoord-123")
    with pytest.raises(VaultXError):
        await cli.create_user("root", "ander@example.com", None, False, "cli-wachtwoord-12345")


@pytest.mark.parametrize("base_url", ["http://192.168.1.50", "https://192.168.1.50"])
async def test_password_login_works_on_any_address(admin, make_client, base_url):
    # Ook op een adres dat niet in de configuratie staat: geen omweg via VAULTX_PUBLIC_URL. Ook
    # over https met het zelfondertekende certificaat van de container (WebCrypto vraagt https).
    await create(admin)
    c = await make_client(base_url)
    r = await pw_login(c, "carol", next="/notes?note=1")
    assert r.json()["redirect_url"] == "/notes?note=1"
    assert (await c.get("/api/v1/me")).json()["username"] == "carol"
    r = await c.post("/auth/logout", headers={"X-VaultX-CSRF": "1"})
    assert r.json()["redirect_url"] == "/login?logged_out=1"
