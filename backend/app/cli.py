"""Beheer vanaf de commandolijn, voor als niemand meer kan inloggen (bv. Authentik ligt plat).

    python -m app.cli create-user --username jonas --email jonas@example.com --admin
    python -m app.cli set-password jonas
    python -m app.cli reset-vault-2fa jonas@example.com    # telefoon en herstelcode kwijt

Het wachtwoord wordt gevraagd (twee keer, zonder echo) of met --password-stdin van stdin gelezen.
Op de Debian-CT, als root (zie docs/accounts.md):
    set -a; . /etc/vaultx/vaultx.env; set +a; cd /opt/vaultx/backend
    /opt/vaultx/venv/bin/python -m app.cli set-password jonas
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.core.config import get_settings
from app.core.context import RequestMeta
from app.core.db import dispose_engine, get_sessionmaker
from app.core.errors import VaultXError
from app.models import User
from app.repositories import UserRepository
from app.services.audit import AuditService
from app.services.password_login import check_password_policy, set_password
from app.services.principal import Actor
from app.services.sessions import SessionService


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Nieuw wachtwoord: ")
    if getpass.getpass("Nogmaals: ") != first:
        raise VaultXError("De wachtwoorden zijn niet gelijk")
    return first


async def create_user(username: str, email: str, name: str | None, admin: bool, password: str) -> User:
    settings = get_settings()
    async with get_sessionmaker()() as db:
        users = UserRepository(db)
        if await users.login_name_taken({username, email.lower()}):
            raise VaultXError("Deze gebruikersnaam of dit e-mailadres is al in gebruik")
        user = User(
            username=username,
            email=email.lower(),
            email_verified=False,
            display_name=name,
            idp_groups=[],
            admin_granted=admin,
            is_admin=admin,
            is_active=True,
        )
        check_password_policy(settings, password, user)
        set_password(user, password)
        users.add(user)
        await db.flush()
        await AuditService(db).record(
            "user.created",
            Actor.system("cli", RequestMeta()),
            target_type="user",
            target_id=user.id,
            details={"username": username, "email": user.email, "is_admin": admin, "method": "password"},
        )
        await db.commit()
        return user


async def reset_password(login: str, password: str) -> User:
    """Zet het wachtwoord van een account (gebruikersnaam of e-mail, ook een Authentik-account)."""
    settings = get_settings()
    name = login.strip().lower()
    async with get_sessionmaker()() as db:
        users = UserRepository(db)
        matches = [
            u
            for u in (await users.search(query=name, limit=50, offset=0))[0]
            if name in {(u.username or "").lower(), (u.email or "").lower()}
        ]
        if len(matches) != 1:
            raise VaultXError(f"Geen eenduidig account gevonden voor {login!r}")
        user = matches[0]
        check_password_policy(settings, password, user)
        had_password = user.has_password
        set_password(user, password)
        user.is_active = True
        revoked = await SessionService(db, settings).revoke_password_sessions(user.id, "password_reset")
        await AuditService(db).record(
            "user.password_reset" if had_password else "user.password_set",
            Actor.system("cli", RequestMeta()),
            target_type="user",
            target_id=user.id,
            details={"sessions_revoked": revoked},
        )
        await db.commit()
        return user


async def reset_vault_two_factor(email: str) -> str:
    """Tweestapsverificatie van een kluis uitzetten; het master password blijft nodig."""
    from app.repositories import VaultAccountRepository
    from app.services.vault_auth import clear_two_factor, normalize_email

    async with get_sessionmaker()() as db:
        found = await VaultAccountRepository(db).by_email(normalize_email(email))
        account = await VaultAccountRepository(db).lock(found.user_id) if found else None
        if account is None or not account.two_factor_enabled:
            raise VaultXError(f"Geen kluis met tweestapsverificatie voor {email!r}")
        clear_two_factor(account)
        await AuditService(db).record(
            "vault.2fa.disable",
            Actor.system("cli", RequestMeta()),
            target_type="vault",
            target_id=account.user_id,
            details={"via": "cli"},
        )
        await db.commit()
        return account.email


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="VaultX-accounts beheren")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create-user", help="Gewoon account aanmaken")
    c.add_argument("--username", required=True)
    c.add_argument("--email", required=True)
    c.add_argument("--name")
    c.add_argument("--admin", action="store_true", help="Instantiebeheerder maken")
    c.add_argument("--password-stdin", action="store_true")
    s = sub.add_parser("set-password", help="Wachtwoord van een account instellen en het account activeren")
    s.add_argument("login", help="Gebruikersnaam of e-mailadres")
    s.add_argument("--password-stdin", action="store_true")
    t = sub.add_parser("reset-vault-2fa", help="Tweestapsverificatie van een kluis uitzetten")
    t.add_argument("email", help="E-mailadres van de kluis (login in de Bitwarden-apps)")
    args = parser.parse_args(argv)

    if args.command == "reset-vault-2fa":

        async def run_reset() -> str:
            try:
                return await reset_vault_two_factor(args.email)
            finally:
                await dispose_engine()

        try:
            email = asyncio.run(run_reset())
        except VaultXError as exc:
            print(f"Fout: {exc.message}", file=sys.stderr)
            return 1
        print(f"OK: {email} logt in de Bitwarden-apps weer in met enkel het master password.")
        return 0

    async def run() -> User:
        try:
            password = _read_password(args.password_stdin)
            if args.command == "create-user":
                return await create_user(args.username, args.email, args.name, args.admin, password)
            return await reset_password(args.login, password)
        finally:
            await dispose_engine()

    try:
        user = asyncio.run(run())
    except VaultXError as exc:
        print(f"Fout: {exc.message}", file=sys.stderr)
        return 1
    print(f"OK: {user.username or user.email} ({user.id}) kan nu inloggen met een wachtwoord.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
