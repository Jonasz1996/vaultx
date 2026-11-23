"""Beheer vanaf de commandolijn, voor als niemand meer kan inloggen (bv. Authentik ligt plat).

    python -m app.cli create-user --username jonas --email jonas@example.com --admin
    python -m app.cli set-password jonas
    python -m app.cli reset-vault-2fa jonas@example.com    # telefoon, sleutels en herstelcode kwijt
    python -m app.cli reset-login-2fa jonas                # sleutel voor het wachtwoordlogin kwijt
    python -m app.cli reset-account '<herstelcode>'          # kan niet inloggen: code van de loginpagina
    python -m app.cli item-history jonas@example.com      # vorige versies van kluisitems
    python -m app.cli item-restore jonas@example.com 42   # versie 42 terugzetten als extra item
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
from dataclasses import dataclass
from datetime import datetime

from app.core.config import get_settings
from app.core.context import RequestMeta
from app.core.db import dispose_engine, get_sessionmaker
from app.core.errors import VaultXError
from app.models import User
from app.repositories import UserRepository
from app.services.audit import AuditService
from app.services.password_login import (
    TEMPORARY_PASSWORD_MINUTES,
    check_password_policy,
    new_temporary_password,
    set_password,
    set_temporary_password,
)
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


@dataclass(slots=True)
class AccountReset:
    user: User
    password: str
    valid_until: datetime
    login_keys: int
    vault_two_factor: bool
    sessions_revoked: int
    reactivated: bool
    requested_at: datetime | None = None
    requested_from: str | None = None


async def reset_account(
    login: str | None = None,
    *,
    code: str | None = None,
    password: str | None = None,
    minutes: int = TEMPORARY_PASSWORD_MINUTES,
    keep_vault_2fa: bool = False,
) -> AccountReset:
    """Accountherstel: MFA van het VaultX-login (en standaard van de kluis) weg, alle sessies
    in, en een tijdelijk wachtwoord dat bij de eerste login vervangen moet worden.

    De kluisversleuteling (master password) blijft altijd onaangeroerd: die kan een beheerder
    niet resetten zonder dat de kluisinhoud verloren gaat.

    Met ``code``: de herstelcode die de gebruiker op de loginpagina kreeg (eenmalig, 24 uur
    geldig); het account volgt uit het openstaande verzoek. Zonder code: ``login`` rechtstreeks.
    """
    from app.models import VaultAccount
    from app.services import account_reset
    from app.services.vault_auth import clear_vault_two_factor
    from app.services.webauthn import SCOPE_LOGIN, WebAuthnService

    if minutes < 5 or minutes > 7 * 24 * 60:
        raise VaultXError("De geldigheid moet tussen 5 minuten en 7 dagen liggen")
    settings = get_settings()
    temp = password if password is not None else new_temporary_password()
    check_password_policy(settings, temp)
    if (code is None) == (login is None):
        raise VaultXError("Geef een herstelcode of (met --login) een gebruikersnaam of e-mailadres")
    async with get_sessionmaker()() as db:
        request = await account_reset.redeem(db, code) if code is not None else None
        try:
            found = await _find_user(db, request.login if request else login)
        except VaultXError:
            if request is None:
                raise
            # De code was echt, maar bij die login hoort geen (eenduidig) account. Code is op.
            await db.commit()
            raise VaultXError(
                f"De code is geldig, maar er is geen eenduidig VaultX-account voor {request.login!r}. "
                "Er is niets gewijzigd."
            ) from None
        user = await db.get(User, found.id, with_for_update=True)
        assert user is not None
        webauthn = WebAuthnService(db, settings)
        login_keys = len(await webauthn.credentials(user.id, SCOPE_LOGIN))
        await webauthn.delete_all(user.id, SCOPE_LOGIN)
        user.login_recovery_code_hash = None
        vault_2fa = False
        account = await db.get(VaultAccount, user.id, with_for_update=True)
        if account is not None and account.two_factor_enabled and not keep_vault_2fa:
            await clear_vault_two_factor(db, settings, account)
            vault_2fa = True
        had_password = user.has_password
        valid_until = set_temporary_password(user, temp, minutes)
        reactivated = not user.is_active
        user.is_active = True
        # Alle sessies, ook die via Authentik: wie het account misbruikte, ligt eruit.
        revoked = await SessionService(db, settings).revoke_for_user(user.id, "account_reset")
        actor = Actor.system("cli", RequestMeta())
        if vault_2fa:
            await AuditService(db).record(
                "vault.2fa.disable",
                actor,
                target_type="vault",
                target_id=user.id,
                details={"via": "accountherstel (cli)"},
            )
        await AuditService(db).record(
            "user.account_reset",
            actor,
            target_type="user",
            target_id=user.id,
            details={
                "had_password": had_password,
                "temporary_until": valid_until.isoformat(),
                "login_keys_removed": login_keys,
                "vault_2fa_reset": vault_2fa,
                "sessions_revoked": revoked,
                "reactivated": reactivated,
                "password_chosen_by_admin": password is not None,
                "via": "herstelcode" if request else "login",
                **(
                    {
                        "request_id": str(request.id),
                        "requested_at": request.created_at.isoformat(),
                        "requested_from": request.ip_address,
                    }
                    if request
                    else {}
                ),
            },
        )
        await db.commit()
        return AccountReset(
            user,
            temp,
            valid_until,
            login_keys,
            vault_2fa,
            revoked,
            reactivated,
            request.created_at if request else None,
            request.ip_address if request else None,
        )


async def _vault_account(db, email: str):
    from app.repositories import VaultAccountRepository
    from app.services.vault_auth import normalize_email

    account = await VaultAccountRepository(db).by_email(normalize_email(email))
    if account is None:
        raise VaultXError(f"Geen kluis voor {email!r}")
    return account


def _present(data: dict) -> str:
    """Welke velden ingevuld zijn (namen, geen inhoud: alles is versleuteld)."""
    login = data.get("login") if isinstance(data.get("login"), dict) else {}
    marks = [
        name
        for name, value in (
            ("gebruiker", login.get("username")),
            ("wachtwoord", login.get("password")),
            ("totp", login.get("totp")),
            ("uris", login.get("uris")),
            ("passkey", login.get("fido2Credentials")),
            ("notities", data.get("notes")),
            ("velden", data.get("fields")),
            ("geschiedenis", data.get("passwordHistory")),
        )
        if value
    ]
    return ",".join(marks) or "-"


async def item_history(email: str, item: str | None, limit: int) -> int:
    from uuid import UUID

    from app.repositories import VaultCipherVersionRepository

    async with get_sessionmaker()() as db:
        account = await _vault_account(db, email)
        rows = await VaultCipherVersionRepository(db).for_user(
            account.user_id, UUID(item) if item else None, limit
        )
    if not rows:
        print("Geen bewaarde versies.")
        return 0
    print("versie\titem\tvervangen op (lokaal)\treden\tdoor\tversie was van\tingevuld")
    for v in rows:
        print(
            f"{v.id}\t{v.cipher_id}\t{v.replaced_at.astimezone():%Y-%m-%d %H:%M:%S}\t{v.reason}\t"
            f"{v.replaced_by or '-'}\t{v.revision_date.astimezone():%Y-%m-%d %H:%M:%S}\t{_present(v.data)}"
        )
    return 0


async def item_restore(email: str, version_id: int) -> str:
    from app.services.vault import VaultService

    async with get_sessionmaker()() as db:
        account = await _vault_account(db, email)
        copy = await VaultService(db).restore_version_as_copy(
            account.user_id, version_id, Actor.system("cli", RequestMeta())
        )
        return str(copy.id)


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


def _print_account_reset(res: AccountReset) -> None:
    u = res.user
    until = res.valid_until.astimezone().strftime("%d-%m-%Y %H:%M")
    print(f"Account {u.username or u.email} ({u.email or '-'}) is gereset.")
    if res.requested_at is not None:
        when = res.requested_at.astimezone().strftime("%d-%m-%Y %H:%M")
        print(f"Herstelcode aangevraagd op {when} vanaf {res.requested_from or 'onbekend adres'}.")
    print()
    print(f"  Tijdelijk wachtwoord:  {res.password}")
    print(f"  Geldig tot:            {until} (daarna opnieuw resetten)")
    print()
    print("Geef dit wachtwoord persoonlijk door. Bij de eerste login kiest de gebruiker meteen een")
    print("eigen wachtwoord; het tijdelijke geeft zelf geen toegang tot VaultX.")
    print()
    print(f"  - beveiligingssleutels voor het wachtwoordlogin weggehaald: {res.login_keys}")
    print(
        "  - tweestapsverificatie van de kluis (authenticator-app, sleutels, herstelcode): "
        + ("uitgezet" if res.vault_two_factor else "stond niet aan of bewust behouden")
    )
    print(f"  - VaultX-sessies ingetrokken: {res.sessions_revoked}")
    if res.reactivated:
        print("  - het account was gedeactiveerd en is weer actief")
    print()
    print("Niet gereset: het master password van de kluis. Daarmee is de kluis versleuteld; een")
    print("beheerder kan het niet resetten zonder dat de kluisinhoud verloren gaat. Kwijt? Dan blijft")
    print("enkel de kluis opnieuw aanmaken over (Mijn kluis), met verlies van de items.")
    print("De gebruiker stelt na het inloggen zelf opnieuw een beveiligingssleutel en de")
    print("tweestapsverificatie van de kluis in. Alles staat in de auditlog (user.account_reset).")


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
    ra = sub.add_parser(
        "reset-account",
        help="Accountherstel: MFA weg en een tijdelijk wachtwoord, bij de eerste login te wijzigen",
    )
    ra.add_argument(
        "code",
        nargs="?",
        help="Herstelcode die de gebruiker op de loginpagina kreeg (login.XXXX-XXXX-XXXX-XXXX)",
    )
    ra.add_argument("--login", help="Zonder code: gebruikersnaam of e-mailadres rechtstreeks")
    ra.add_argument(
        "--minutes",
        type=int,
        default=TEMPORARY_PASSWORD_MINUTES,
        help=f"Zo lang werkt het tijdelijke wachtwoord (standaard {TEMPORARY_PASSWORD_MINUTES})",
    )
    ra.add_argument(
        "--keep-vault-2fa",
        action="store_true",
        help="Tweestapsverificatie van de kluis (Bitwarden-apps) laten staan",
    )
    ra.add_argument(
        "--password-stdin",
        action="store_true",
        help="Zelf het tijdelijke wachtwoord geven (stdin) in plaats van er een te laten maken",
    )
    ih = sub.add_parser("item-history", help="Vorige versies van kluisitems tonen")
    ih.add_argument("email", help="E-mailadres van de kluis")
    ih.add_argument("--item", help="Enkel dit item (id)")
    ih.add_argument("--limit", type=int, default=100)
    ir = sub.add_parser(
        "item-restore", help="Een vorige versie terugzetten als extra item (overschrijft niets)"
    )
    ir.add_argument("email", help="E-mailadres van de kluis")
    ir.add_argument("version", type=int, help="Versienummer uit item-history")
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

    if args.command in ("item-history", "item-restore"):

        async def run_items() -> int | str:
            try:
                if args.command == "item-history":
                    return await item_history(args.email, args.item, args.limit)
                return await item_restore(args.email, args.version)
            finally:
                await dispose_engine()

        try:
            result = asyncio.run(run_items())
        except (VaultXError, ValueError) as exc:
            print(f"Fout: {getattr(exc, 'message', exc)}", file=sys.stderr)
            return 1
        if args.command == "item-restore":
            print(f"OK: versie {args.version} staat terug als nieuw item {result}. Synchroniseer je apps.")
        return 0

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

    if args.command == "reset-account":

        async def run_account_reset() -> AccountReset:
            try:
                given = _read_password(True) if args.password_stdin else None
                return await reset_account(
                    args.login,
                    code=args.code,
                    password=given,
                    minutes=args.minutes,
                    keep_vault_2fa=args.keep_vault_2fa,
                )
            finally:
                await dispose_engine()

        try:
            res = asyncio.run(run_account_reset())
        except VaultXError as exc:
            print(f"Fout: {exc.message}", file=sys.stderr)
            return 1
        _print_account_reset(res)
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
