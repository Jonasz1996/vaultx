"""Gebruikersbeheer. Authentik-accounts ontstaan bij hun eerste login; gewone accounts (met een
VaultX-wachtwoord) maakt een beheerder aan."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.errors import ConflictError, InvalidOperationError, NotFoundError, TooManyAttemptsError
from app.core.security import verify_password
from app.models import User, UserSession
from app.repositories import MembershipRepository, UserRepository
from app.schemas.users import MembershipBrief, PasswordChange, UserCreate, UserUpdate
from app.services.audit import AuditService
from app.services.identity import effective_admin
from app.services.password_login import check_password_policy, set_password
from app.services.principal import Principal
from app.services.sessions import SessionService
from app.services.vault_push import PushType, publish


def membership_briefs(memberships) -> list[MembershipBrief]:
    return [
        MembershipBrief(
            id=m.id,
            organization_id=m.organization_id,
            organization_slug=m.organization.slug,
            organization_name=m.organization.name,
            team_id=m.team_id,
            team_slug=m.team.slug if m.team else None,
            team_name=m.team.name if m.team else None,
            role=m.role,
            source=m.source,
        )
        for m in sorted(memberships, key=lambda m: (m.organization.name.lower(), m.team_id is not None))
    ]


class UserService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.users = UserRepository(db)
        self.memberships = MembershipRepository(db)
        self.sessions = SessionService(db, settings)
        self.audit = AuditService(db)

    async def search(self, p: Principal, *, query: str | None, limit: int, offset: int):
        if not p.is_admin and not p.managed_org_ids:
            raise await self.audit.deny("user.list", p.actor)
        # Organisatiebeheerders mogen alle gebruikers opzoeken om ze toe te voegen,
        # maar zien enkel samenvattingen (zie router).
        return await self.users.search(query=query, limit=limit, offset=offset)

    async def get(self, p: Principal, user_id: UUID) -> User:
        if not p.is_admin and p.user.id != user_id:
            raise NotFoundError("Gebruiker niet gevonden")
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("Gebruiker niet gevonden")
        return user

    async def memberships_of(self, user_id: UUID) -> list[MembershipBrief]:
        return membership_briefs(await self.memberships.for_user(user_id))

    async def _require_admin(self, p: Principal, action: str, user_id: UUID | None = None) -> None:
        if not p.is_admin:
            raise await self.audit.deny(action, p.actor, target_type="user", target_id=user_id)

    async def _check_login_names(self, names: set[str], exclude_id: UUID | None) -> None:
        if await self.users.login_name_taken(names, exclude_id=exclude_id):
            raise ConflictError(
                "Deze gebruikersnaam of dit e-mailadres is al in gebruik door een ander account"
            )

    async def create_local(self, p: Principal, data: UserCreate) -> User:
        await self._require_admin(p, "user.create")
        if not self.settings.local_login_enabled:
            raise InvalidOperationError("Inloggen met een wachtwoord staat uit (VAULTX_LOCAL_LOGIN_ENABLED)")
        username, email = data.username.strip(), data.email.strip().lower()
        await self._check_login_names({username, email}, None)
        user = User(
            username=username,
            email=email,
            # Een beheerder vult het adres zelf in; er is geen bevestigingsmail.
            email_verified=False,
            display_name=(data.display_name or "").strip() or None,
            idp_groups=[],
            admin_granted=data.is_admin,
            is_admin=data.is_admin,
            is_active=True,
        )
        check_password_policy(self.settings, data.password, user)
        set_password(user, data.password)
        self.users.add(user)
        await self.db.flush()
        await self.audit.record(
            "user.created",
            p.actor,
            target_type="user",
            target_id=user.id,
            details={"username": username, "email": email, "is_admin": user.is_admin, "method": "password"},
        )
        await self.db.commit()
        return user

    async def update(self, p: Principal, user_id: UUID, data: UserUpdate) -> User:
        await self._require_admin(p, "user.update", user_id)
        user = await self.get(p, user_id)
        if data.is_active is not None and data.is_active != user.is_active:
            if user.id == p.user.id and not data.is_active:
                raise InvalidOperationError("Je kan je eigen account niet deactiveren")
            user.is_active = data.is_active
            revoked = 0
            if not data.is_active:
                revoked = await self.sessions.revoke_for_user(user.id, "user_deactivated")
                # Bitwarden-apps met een open verbinding melden zich meteen af.
                await publish(self.db, user.id, PushType.LOG_OUT)
            await self.audit.record(
                "user.activated" if data.is_active else "user.deactivated",
                p.actor,
                target_type="user",
                target_id=user.id,
                details={"sessions_revoked": revoked},
            )
        if data.is_admin is not None and data.is_admin != user.admin_granted:
            was_admin = user.is_admin
            user.admin_granted = data.is_admin
            user.is_admin = effective_admin(user, self.settings)
            if user.id == p.user.id and was_admin and not user.is_admin:
                raise InvalidOperationError("Je kan je eigen beheerdersrechten niet intrekken")
            await self.audit.record(
                "user.admin_changed",
                p.actor,
                target_type="user",
                target_id=user.id,
                details={"admin_granted": user.admin_granted, "is_admin": user.is_admin, "source": "vaultx"},
            )
        profile = {
            k: v
            for k, v in {
                "username": data.username.strip() if data.username else None,
                "email": data.email.strip().lower() if data.email else None,
                "display_name": data.display_name.strip() if data.display_name is not None else None,
            }.items()
            if v is not None and v != getattr(user, k)
        }
        if profile:
            if user.has_authentik:
                raise InvalidOperationError(
                    "Naam en e-mailadres van een Authentik-account komen uit Authentik; wijzig ze daar"
                )
            await self._check_login_names({profile.get("username", ""), profile.get("email", "")}, user.id)
            for k, v in profile.items():
                setattr(user, k, v or None)
            await self.audit.record(
                "user.updated", p.actor, target_type="user", target_id=user.id, details={"changed": profile}
            )
        await self.db.commit()
        return user

    async def set_password(self, p: Principal, user_id: UUID, password: str) -> User:
        """Beheerder geeft een account (ook een Authentik-account) een nieuw VaultX-wachtwoord."""
        await self._require_admin(p, "user.password_set", user_id)
        if not self.settings.local_login_enabled:
            raise InvalidOperationError("Inloggen met een wachtwoord staat uit (VAULTX_LOCAL_LOGIN_ENABLED)")
        user = await self.get(p, user_id)
        if not user.username and not user.email:
            raise InvalidOperationError(
                "Dit account heeft geen gebruikersnaam of e-mailadres om mee in te loggen"
            )
        await self._check_login_names({user.username or "", user.email or ""}, user.id)
        check_password_policy(self.settings, password, user)
        had_password = user.has_password
        set_password(user, password)
        # Sessies die met het oude wachtwoord begonnen, vervallen (je eigen huidige sessie blijft).
        # Authentik-sessies hangen niet van dat wachtwoord af en blijven.
        keep = p.session_id if user.id == p.user.id else None
        revoked = await self.sessions.revoke_password_sessions(user.id, "password_reset", keep=keep)
        await self.audit.record(
            "user.password_reset" if had_password else "user.password_set",
            p.actor,
            target_type="user",
            target_id=user.id,
            details={"sessions_revoked": revoked},
        )
        await self.db.commit()
        return user

    async def remove_password(self, p: Principal, user_id: UUID) -> User:
        await self._require_admin(p, "user.password_removed", user_id)
        user = await self.get(p, user_id)
        if not user.has_password:
            return user
        if not user.has_authentik:
            raise InvalidOperationError(
                "Dit account kan enkel met een wachtwoord inloggen. Deactiveer het account in plaats daarvan."
            )
        user.password_hash = None
        user.password_changed_at = datetime.now(UTC)
        user.failed_logins = 0
        user.locked_until = None
        revoked = await self.sessions.revoke_password_sessions(user.id, "password_removed")
        await self.audit.record(
            "user.password_removed",
            p.actor,
            target_type="user",
            target_id=user.id,
            details={"sessions_revoked": revoked},
        )
        await self.db.commit()
        return user

    async def change_own_password(self, p: Principal, data: PasswordChange) -> User:
        user = p.user
        if not user.has_password:
            raise InvalidOperationError(
                "Je account heeft geen VaultX-wachtwoord; vraag er een aan een beheerder"
            )
        now = datetime.now(UTC)
        if user.locked_until is not None and user.locked_until > now:
            raise TooManyAttemptsError("Te veel foute pogingen. Probeer het over enkele minuten opnieuw.")
        if not await run_in_threadpool(verify_password, data.current_password, user.password_hash or ""):
            # Zelfde teller als bij inloggen: met een gestolen sessie raad je het wachtwoord niet.
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= self.settings.local_login_max_failed:
                user.locked_until = now + timedelta(minutes=self.settings.local_login_lockout_minutes)
                user.failed_logins = 0
            await self.audit.record(
                "user.password_changed",
                p.actor,
                outcome="failure",
                target_type="user",
                target_id=user.id,
                details={"reason": "huidig wachtwoord onjuist"},
            )
            await self.db.commit()
            raise InvalidOperationError("Je huidige wachtwoord is onjuist")
        if data.new_password == data.current_password:
            raise InvalidOperationError("Kies een ander wachtwoord dan je huidige")
        check_password_policy(self.settings, data.new_password, user)
        set_password(user, data.new_password)
        revoked = await self.sessions.revoke_password_sessions(user.id, "password_changed", keep=p.session_id)
        await self.audit.record(
            "user.password_changed",
            p.actor,
            target_type="user",
            target_id=user.id,
            details={"other_sessions_revoked": revoked},
        )
        await self.db.commit()
        return user

    async def list_sessions(self, p: Principal, user_id: UUID) -> list[UserSession]:
        await self.get(p, user_id)
        return await self.sessions.active_for_user(user_id)

    async def revoke_sessions(self, p: Principal, user_id: UUID) -> int:
        if not p.is_admin and p.user.id != user_id:
            raise await self.audit.deny("session.revoke_all", p.actor, target_type="user", target_id=user_id)
        await self.get(p, user_id)
        n = await self.sessions.revoke_for_user(
            user_id, "revoked_by_user" if p.user.id == user_id else "revoked_by_admin"
        )
        await self.audit.record(
            "session.revoked_all", p.actor, target_type="user", target_id=user_id, details={"count": n}
        )
        await self.db.commit()
        return n
