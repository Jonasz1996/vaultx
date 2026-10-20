"""Beheer vanaf de commandolijn, voor als niemand meer kan inloggen (bv. Authentik ligt plat).

    python -m app.cli create-user --username jonas --email jonas@example.com --admin
    python -m app.cli set-password jonas
    python -m app.cli reset-vault-2fa jonas@example.com    # telefoon, sleutels en herstelcode kwijt
    python -m app.cli reset-login-2fa jonas                # sleutel voor het wachtwoordlogin kwijt
    python -m app.cli import-linkwarden --dsn postgresql://linkwarden@db/linkwarden --to jonas

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
    from app.services.vault_auth import clear_vault_two_factor, normalize_email

    async with get_sessionmaker()() as db:
        found = await VaultAccountRepository(db).by_email(normalize_email(email))
        account = await VaultAccountRepository(db).lock(found.user_id) if found else None
        if account is None or not account.two_factor_enabled:
            raise VaultXError(f"Geen kluis met tweestapsverificatie voor {email!r}")
        await clear_vault_two_factor(db, get_settings(), account)
        await AuditService(db).record(
            "vault.2fa.disable",
            Actor.system("cli", RequestMeta()),
            target_type="vault",
            target_id=account.user_id,
            details={"via": "cli"},
        )
        await db.commit()
        return account.email


async def reset_login_two_factor(login: str) -> str:
    """Beveiligingssleutels voor het wachtwoordlogin weghalen; het wachtwoord blijft nodig."""
    from app.services.password_login import LoginSecurityKeyService

    async with get_sessionmaker()() as db:
        user = await _find_user(db, login)
        svc = LoginSecurityKeyService(db, get_settings())
        if not await svc.keys(user.id):
            raise VaultXError(f"{login!r} heeft geen beveiligingssleutels voor het wachtwoordlogin")
        await svc.reset(user, Actor.system("cli", RequestMeta()))
        return user.username or user.email or str(user.id)


async def _find_user(db, login: str) -> User:
    name = login.strip().lower()
    matches = [
        u
        for u in (await UserRepository(db).search(query=name, limit=50, offset=0))[0]
        if name in {(u.username or "").lower(), (u.email or "").lower()}
    ]
    if len(matches) != 1:
        raise VaultXError(f"Geen eenduidig VaultX-account gevonden voor {login!r}")
    return matches[0]


async def import_linkwarden(args: argparse.Namespace) -> int:
    """Links importeren uit een Linkwarden-database, een Linkwarden-export of browserbladwijzers."""
    from pathlib import Path

    from app.services.link_import import LinkImporter, parse_bookmarks_html, parse_linkwarden_json
    from app.services.linkwarden_db import LinkwardenDatabase

    missing = 0
    if args.dsn:
        with LinkwardenDatabase(args.dsn) as lw:
            if args.list_users:
                for u in lw.users():
                    print(f"{u.id}\t{u.username or '-'}\t{u.email or '-'}\t{u.collections} collecties")
                return 0
            lw_user = lw.pick_user(args.source_user)
            data = lw.read(lw_user, Path(args.files) if args.files else None)
            print(f"Linkwarden-gebruiker {lw_user.username or lw_user.email}: ", end="")
            # De bestanden worden pas bij het importeren gelezen; tel ontbrekende achteraf.
            importer_db = lw
    else:
        raw = await asyncio.to_thread(Path(args.file).read_text, encoding="utf-8-sig")
        data = (
            parse_bookmarks_html(raw)
            if args.file.lower().endswith((".html", ".htm"))
            else parse_linkwarden_json(raw)
        )
        importer_db = None
    print(f"{len(data.collections)} collecties en {len(data.links)} links gelezen.")
    if args.dry_run:
        for w in data.warnings:
            print(f"  let op: {w}")
        return 0
    if not args.to:
        raise VaultXError(
            "Geef met --to het VaultX-account (gebruikersnaam of e-mail) waar de links naartoe gaan"
        )
    try:
        async with get_sessionmaker()() as db:
            user = await _find_user(db, args.to)
            res = await LinkImporter(db).apply(user.id, Actor.system("cli", RequestMeta()), data)
    finally:
        if importer_db is not None:
            missing = importer_db.missing_files
    print(
        f"OK voor {user.username or user.email}: {res.collections_created} collecties en "
        f"{res.links_created} links "
        f"aangemaakt, {res.links_updated} bestaande links aangevuld, {res.tags_created} nieuwe tags, "
        f"{res.archives_added} archieven, {res.skipped} overgeslagen."
    )
    if missing:
        print(f"  let op: {missing} archiefbestanden staan in de database maar niet in {args.files}")
    for w in res.warnings:
        print(f"  let op: {w}")
    return 0


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
    r = sub.add_parser("reset-login-2fa", help="Beveiligingssleutels voor het wachtwoordlogin weghalen")
    r.add_argument("login", help="Gebruikersnaam of e-mailadres")
    li = sub.add_parser(
        "import-linkwarden",
        help="Links importeren uit een Linkwarden-database, Linkwarden-export (JSON) of bladwijzers (HTML)",
    )
    src = li.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--dsn",
        help="Linkwarden-database, bv. postgresql://linkwarden@192.168.10.193:5433/linkwarden "
        "(wachtwoord via PGPASSWORD of ~/.pgpass)",
    )
    src.add_argument("--file", help="Linkwarden-export (.json) of browserbladwijzers (.html)")
    li.add_argument("--to", help="VaultX-account (gebruikersnaam of e-mail) dat de links krijgt")
    li.add_argument("--from", dest="source_user", help="Linkwarden-gebruiker (id, gebruikersnaam of e-mail)")
    li.add_argument("--files", help="Map met de Linkwarden-bestanden (STORAGE_FOLDER, standaard data)")
    li.add_argument("--list-users", action="store_true", help="Enkel de Linkwarden-gebruikers tonen")
    li.add_argument("--dry-run", action="store_true", help="Enkel lezen en tellen, niets importeren")
    args = parser.parse_args(argv)

    if args.command == "import-linkwarden":

        async def run_import() -> int:
            try:
                return await import_linkwarden(args)
            finally:
                await dispose_engine()

        try:
            return asyncio.run(run_import())
        except (VaultXError, OSError, ValueError) as exc:
            print(f"Fout: {getattr(exc, 'message', exc)}", file=sys.stderr)
            return 1

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

    if args.command == "reset-login-2fa":

        async def run_login_reset() -> str:
            try:
                return await reset_login_two_factor(args.login)
            finally:
                await dispose_engine()

        try:
            name = asyncio.run(run_login_reset())
        except VaultXError as exc:
            print(f"Fout: {exc.message}", file=sys.stderr)
            return 1
        print(f"OK: {name} logt weer in met enkel het wachtwoord.")
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
