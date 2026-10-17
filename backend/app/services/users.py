"""Gebruikersbeheer (identiteit zelf komt uit Authentik)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import InvalidOperationError, NotFoundError
from app.models import User, UserSession
from app.repositories import MembershipRepository, UserRepository
from app.schemas.users import MembershipBrief, UserUpdate
from app.services.audit import AuditService
from app.services.principal import Principal
from app.services.sessions import SessionService


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

    async def update(self, p: Principal, user_id: UUID, data: UserUpdate) -> User:
        if not p.is_admin:
            raise await self.audit.deny("user.update", p.actor, target_type="user", target_id=user_id)
        user = await self.get(p, user_id)
        if data.is_active is not None and data.is_active != user.is_active:
            if user.id == p.user.id and not data.is_active:
                raise InvalidOperationError("Je kan je eigen account niet deactiveren")
            user.is_active = data.is_active
            revoked = 0
            if not data.is_active:
                revoked = await self.sessions.revoke_for_user(user.id, "user_deactivated")
            await self.audit.record(
                "user.activated" if data.is_active else "user.deactivated",
                p.actor,
                target_type="user",
                target_id=user.id,
                details={"sessions_revoked": revoked},
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
