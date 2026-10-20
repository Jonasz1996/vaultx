"""Gewone login met gebruikersnaam (of e-mail) en een VaultX-wachtwoord, naast Authentik.

Met een gekoppelde beveiligingssleutel of passkey (WebAuthn) volgt na het wachtwoord een
tweede stap: de server geeft dan een kort geldig, ondertekend ticket en de opties voor de
sleutel, en maakt pas een sessie als de sleutel (of de herstelcode) klopt. Via Authentik
inloggen vraagt die stap niet: daar regelt Authentik zelf de MFA.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.context import RequestMeta
from app.core.errors import AuthenticationError, InvalidOperationError, NotFoundError, TooManyAttemptsError
from app.core.security import (
    dummy_password_hash,
    hash_password,
    hash_token,
    password_needs_rehash,
    verify_password,
)
from app.models import User, WebAuthnCredential
from app.repositories import UserRepository
from app.services import totp
from app.services.audit import AuditService
from app.services.principal import Actor, Principal
from app.services.webauthn import SCOPE_LOGIN, WebAuthnError, WebAuthnService

log = logging.getLogger(__name__)

WRONG_CREDENTIALS = "Gebruikersnaam of wachtwoord is onjuist"
MAX_PASSWORD_LENGTH = 1024
# Zo lang heb je na een juist wachtwoord om je sleutel aan te raken.
SECOND_STEP_SECONDS = 300


@dataclass(slots=True)
class SecondStep:
    """Wachtwoord klopt, nu nog de sleutel: ticket en opties voor navigator.credentials.get."""

    ticket: str
    options: dict[str, Any] | None


def check_password_policy(settings: Settings, password: str, user: User | None = None) -> None:
    """Geen eisen aan het wachtwoord, behalve dat het niet leeg mag zijn."""
    if not password:
        raise InvalidOperationError("Vul een wachtwoord in")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise InvalidOperationError(f"Het wachtwoord mag hoogstens {MAX_PASSWORD_LENGTH} tekens lang zijn")


def set_password(user: User, password: str) -> None:
    user.password_hash = hash_password(password)
    user.password_changed_at = datetime.now(UTC)
    user.failed_logins = 0
    user.locked_until = None


class PasswordLoginService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.users = UserRepository(db)
        self.audit = AuditService(db)

    def _tickets(self) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(self.settings.secret_key.get_secret_value(), salt="vaultx/login-2fa/v1")

    @staticmethod
    def _password_mark(user: User) -> str:
        # Ticket vervalt als het wachtwoord intussen wijzigt.
        return hash_token(user.password_hash or "")[:16]

    async def login(self, login: str, password: str, meta: RequestMeta) -> User | SecondStep:
        """Wachtwoord controleren. Zonder sleutels: de ingelogde gebruiker; met sleutels: een
        tweede stap (nog geen sessie, geen geslaagde login in de auditlog)."""
        user = await self.authenticate(login, password, meta, finish=False)
        creds = await WebAuthnService(self.db, self.settings).credentials(user.id, SCOPE_LOGIN)
        if not creds:
            await self._finish(user, Actor.for_user(user, meta), password)
            return user
        await self.db.commit()
        ticket = self._tickets().dumps({"u": str(user.id), "p": self._password_mark(user)})
        options = WebAuthnService(self.db, self.settings).assertion_options(user.id, SCOPE_LOGIN, creds)
        return SecondStep(ticket=ticket, options=options)

    async def second_step(
        self,
        ticket: str,
        meta: RequestMeta,
        *,
        security_key: str | None = None,
        recovery_code: str | None = None,
    ) -> User:
        """Tweede stap na het wachtwoord: een gekoppelde sleutel of de herstelcode. Een fout
        telt mee voor dezelfde vergrendeling als een fout wachtwoord."""
        expired = AuthenticationError("De login is verlopen. Vul je wachtwoord opnieuw in.")
        try:
            data = self._tickets().loads(ticket, max_age=SECOND_STEP_SECONDS)
            user_id = UUID(str(data["u"]))
        except (BadSignature, KeyError, TypeError, ValueError):
            raise expired from None
        user = await self.db.get(User, user_id, with_for_update=True)
        if user is None or user.password_hash is None or data.get("p") != self._password_mark(user):
            raise expired
        now = datetime.now(UTC)
        actor = Actor.for_user(user, meta)
        if user.locked_until is not None and user.locked_until > now:
            await self._deny(user, actor, "denied", "tijdelijk vergrendeld na te veel foute pogingen")
            raise TooManyAttemptsError(
                "Te veel foute pogingen. Wachtwoordlogin voor dit account is tijdelijk vergrendeld; "
                "probeer het later opnieuw."
            )
        if not user.is_active:
            await self._deny(user, actor, "denied", "account gedeactiveerd in VaultX")
            raise AuthenticationError("Dit account is gedeactiveerd in VaultX")
        webauthn = WebAuthnService(self.db, self.settings)
        via: str | None = None
        if security_key:
            try:
                cred = await webauthn.verify(user.id, SCOPE_LOGIN, security_key)
                via = f"beveiligingssleutel ({cred.name})"
            except WebAuthnError:
                via = None
        elif recovery_code and user.login_recovery_code_hash:
            given = hash_token(totp.normalize_recovery_code(recovery_code))
            if hmac.compare_digest(given, user.login_recovery_code_hash):
                # Zoals bij de kluis: de herstelcode zet de tweede stap uit.
                await webauthn.delete_all(user.id, SCOPE_LOGIN)
                user.login_recovery_code_hash = None
                await self.audit.record(
                    "auth.2fa.disable",
                    actor,
                    target_type="user",
                    target_id=user.id,
                    details={"via": "herstelcode"},
                )
                via = "herstelcode"
        if via is None:
            user.failed_logins = (user.failed_logins or 0) + 1
            locked = user.failed_logins >= self.settings.local_login_max_failed
            if locked:
                user.locked_until = now + timedelta(minutes=self.settings.local_login_lockout_minutes)
                user.failed_logins = 0
            await self._deny(user, actor, "failure", "tweede stap klopt niet", locked=locked)
            raise AuthenticationError(
                "De beveiligingssleutel werd niet aanvaard" if security_key else "De herstelcode klopt niet"
            )
        await self._finish(user, actor, None, second_step=via)
        return user

    async def authenticate(
        self, login: str, password: str, meta: RequestMeta, *, finish: bool = True
    ) -> User:
        """Controleert gebruikersnaam en wachtwoord. Commit bij falen; met finish=False telt een
        juist wachtwoord nog niet als geslaagde login (er volgt een tweede stap)."""
        user = await self.users.by_login(login) if len(login) <= 320 else None
        if user is None or user.password_hash is None:
            # Even traag als een echt account; niet in de auditlog, anders kan iedereen zonder
            # account die (append-only) log volschrijven.
            # Argon2 in een thread: anders staat de hele server stil zolang hij rekent.
            await run_in_threadpool(verify_password, password, dummy_password_hash())
            log.info("Wachtwoordlogin voor onbekende naam geweigerd")
            raise AuthenticationError(WRONG_CREDENTIALS)

        now = datetime.now(UTC)
        actor = Actor.for_user(user, meta)
        if user.locked_until is not None and user.locked_until > now:
            await self._deny(user, actor, "denied", "tijdelijk vergrendeld na te veel foute wachtwoorden")
            raise TooManyAttemptsError(
                "Te veel foute pogingen. Wachtwoordlogin voor dit account is tijdelijk vergrendeld; "
                "probeer het later opnieuw."
            )
        if not await run_in_threadpool(verify_password, password, user.password_hash):
            user.failed_logins = (user.failed_logins or 0) + 1
            locked = user.failed_logins >= self.settings.local_login_max_failed
            if locked:
                user.locked_until = now + timedelta(minutes=self.settings.local_login_lockout_minutes)
                user.failed_logins = 0
            await self._deny(user, actor, "failure", "fout wachtwoord", locked=locked)
            raise AuthenticationError(WRONG_CREDENTIALS)
        if not user.is_active:
            await self._deny(user, actor, "denied", "account gedeactiveerd in VaultX")
            raise AuthenticationError("Dit account is gedeactiveerd in VaultX")

        if finish:
            await self._finish(user, actor, password)
        return user

    async def _finish(
        self, user: User, actor: Actor, password: str | None, *, second_step: str | None = None
    ) -> None:
        user.failed_logins = 0
        user.locked_until = None
        user.last_login_at = datetime.now(UTC)
        if password is not None and user.password_hash and password_needs_rehash(user.password_hash):
            user.password_hash = await run_in_threadpool(hash_password, password)
        details: dict[str, Any] = {"method": "password", "is_admin": user.is_admin}
        if second_step:
            details["second_step"] = second_step
        await self.audit.record("auth.login", actor, target_type="user", target_id=user.id, details=details)

    async def _deny(
        self, user: User, actor: Actor, outcome: str, reason: str, *, locked: bool = False
    ) -> None:
        details: dict = {"method": "password", "reason": reason}
        if locked:
            details["locked_minutes"] = self.settings.local_login_lockout_minutes
        await self.audit.record(
            "auth.login", actor, outcome=outcome, target_type="user", target_id=user.id, details=details
        )
        await self.db.commit()


class LoginSecurityKeyService:
    """Beveiligingssleutels en passkeys als tweede stap na het VaultX-wachtwoord (Mijn account)."""

    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.audit = AuditService(db)
        self.webauthn = WebAuthnService(db, settings)

    async def keys(self, user_id: UUID) -> list[WebAuthnCredential]:
        return await self.webauthn.credentials(user_id, SCOPE_LOGIN)

    async def _verify_password(self, p: Principal, password: str, action: str) -> User:
        user = await self.db.get(User, p.user.id, with_for_update=True)
        assert user is not None
        if user.password_hash is None:
            raise InvalidOperationError(
                "Je account heeft geen VaultX-wachtwoord. Wie via Authentik inlogt, stelt zijn "
                "beveiligingssleutel in Authentik in."
            )
        now = datetime.now(UTC)
        if user.locked_until is not None and user.locked_until > now:
            raise TooManyAttemptsError("Te veel foute pogingen. Probeer het over enkele minuten opnieuw.")
        if not await run_in_threadpool(verify_password, password, user.password_hash):
            # Zelfde teller als bij inloggen: met een gestolen sessie raad je het wachtwoord niet.
            self._count_failure(user, now)
            await self.audit.record(
                action,
                p.actor,
                outcome="failure",
                target_type="user",
                target_id=user.id,
                details={"reason": "wachtwoord onjuist"},
            )
            await self.db.commit()
            raise InvalidOperationError("Je wachtwoord is onjuist")
        return user

    def _count_failure(self, user: User, now: datetime) -> None:
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= self.settings.local_login_max_failed:
            user.locked_until = now + timedelta(minutes=self.settings.local_login_lockout_minutes)
            user.failed_logins = 0

    async def options(self, p: Principal, password: str) -> dict[str, Any]:
        user = await self._verify_password(p, password, "auth.2fa.enable")
        await self.db.commit()
        try:
            return await self.webauthn.registration_options(
                user.id, SCOPE_LOGIN, user.username or user.email or str(user.id), user.display_name or ""
            )
        except WebAuthnError as exc:
            raise InvalidOperationError(str(exc)) from exc

    async def add(self, p: Principal, name: str, credential: str) -> tuple[WebAuthnCredential, str | None]:
        user = await self.db.get(User, p.user.id, with_for_update=True)
        assert user is not None
        if user.password_hash is None:
            raise InvalidOperationError("Je account heeft geen VaultX-wachtwoord")
        try:
            cred = await self.webauthn.register(user.id, SCOPE_LOGIN, name, credential)
        except WebAuthnError as exc:
            raise InvalidOperationError(str(exc)) from exc
        recovery = None
        if user.login_recovery_code_hash is None:
            recovery = totp.new_recovery_code()
            user.login_recovery_code_hash = hash_token(totp.normalize_recovery_code(recovery))
        await self.audit.record(
            "auth.2fa.enable",
            p.actor,
            target_type="user",
            target_id=user.id,
            details={"method": "beveiligingssleutel", "name": cred.name, "passkey": cred.backed_up},
        )
        await self.db.commit()
        return cred, recovery

    async def challenge(self, p: Principal) -> dict[str, Any] | None:
        return self.webauthn.assertion_options(p.user.id, SCOPE_LOGIN, await self.keys(p.user.id))

    async def remove(
        self,
        p: Principal,
        key_id: UUID,
        password: str,
        *,
        security_key: str | None = None,
        recovery_code: str | None = None,
    ) -> None:
        """Sleutel weghalen: wachtwoord plus een gekoppelde sleutel of de herstelcode, anders
        haalt wie je sessie en wachtwoord heeft de tweede stap zomaar weg."""
        action = "auth.2fa.disable"
        user = await self._verify_password(p, password, action)
        cred = await self.db.get(WebAuthnCredential, key_id)
        if cred is None or cred.user_id != user.id or cred.scope != SCOPE_LOGIN:
            raise NotFoundError("Sleutel niet gevonden")
        via = None
        if security_key:
            try:
                used = await self.webauthn.verify(user.id, SCOPE_LOGIN, security_key)
                via = f"beveiligingssleutel ({used.name})"
            except WebAuthnError:
                via = None
        elif recovery_code and user.login_recovery_code_hash:
            given = hash_token(totp.normalize_recovery_code(recovery_code))
            if hmac.compare_digest(given, user.login_recovery_code_hash):
                via = "herstelcode"
        if via is None:
            self._count_failure(user, datetime.now(UTC))
            await self.audit.record(
                action,
                p.actor,
                outcome="failure",
                target_type="user",
                target_id=user.id,
                details={"reason": "tweede stap ontbreekt of klopt niet"},
            )
            await self.db.commit()
            raise InvalidOperationError(
                "De beveiligingssleutel werd niet aanvaard" if security_key else "De herstelcode klopt niet"
            )
        user.failed_logins = 0
        name = cred.name
        await self.db.delete(cred)
        await self.db.flush()
        off = not await self.keys(user.id)
        if off:
            user.login_recovery_code_hash = None
        await self.audit.record(
            action,
            p.actor,
            target_type="user",
            target_id=user.id,
            details={"name": name, "via": via, "two_factor_off": off},
        )
        await self.db.commit()

    async def reset(self, user: User, actor: Actor) -> int:
        """Beheerder of CLI: alle sleutels voor het wachtwoordlogin weg (sleutel en code kwijt)."""
        count = len(await self.keys(user.id))
        await self.webauthn.delete_all(user.id, SCOPE_LOGIN)
        user.login_recovery_code_hash = None
        await self.audit.record(
            "auth.2fa.disable",
            actor,
            target_type="user",
            target_id=user.id,
            details={"via": "beheerder", "keys": count},
        )
        await self.db.commit()
        return count
