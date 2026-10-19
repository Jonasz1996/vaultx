"""Gewone login met gebruikersnaam (of e-mail) en een VaultX-wachtwoord, naast Authentik."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.context import RequestMeta
from app.core.errors import AuthenticationError, InvalidOperationError, TooManyAttemptsError
from app.core.security import (
    dummy_password_hash,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.models import User
from app.repositories import UserRepository
from app.services.audit import AuditService
from app.services.principal import Actor

log = logging.getLogger(__name__)

WRONG_CREDENTIALS = "Gebruikersnaam of wachtwoord is onjuist"
MAX_PASSWORD_LENGTH = 1024


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

    async def authenticate(self, login: str, password: str, meta: RequestMeta) -> User:
        """Controleert gebruikersnaam en wachtwoord. Commit zowel bij succes als bij falen."""
        user = await self.users.by_login(login) if len(login) <= 320 else None
        if user is None or user.password_hash is None:
            # Even traag als een echt account; niet in de auditlog, anders kan iedereen zonder
            # account die (append-only) log volschrijven.
            verify_password(password, dummy_password_hash())
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
        if not verify_password(password, user.password_hash):
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

        user.failed_logins = 0
        user.locked_until = None
        user.last_login_at = now
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        await self.audit.record(
            "auth.login",
            actor,
            target_type="user",
            target_id=user.id,
            details={"method": "password", "is_admin": user.is_admin},
        )
        return user

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
