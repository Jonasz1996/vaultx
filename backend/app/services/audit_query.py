"""Lezen en verifiëren van de auditlog, met autorisatie per tenant."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import AuditLog, UserSession
from app.repositories import AuditRepository, OrganizationRepository, TeamRepository, UserRepository
from app.schemas.audit import DashboardOut
from app.services.audit import AuditService, VerifyResult
from app.services.principal import Principal


class AuditQueryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = AuditRepository(db)
        self.audit = AuditService(db)

    async def search(
        self,
        p: Principal,
        *,
        limit: int,
        before_id: int | None,
        organization_id: UUID | None,
        instance_only: bool,
        actor_user_id: UUID | None,
        action: str | None,
        outcome: str | None,
        since: datetime | None,
        until: datetime | None,
    ) -> list[AuditLog]:
        scope_ids: list[UUID] | None = None
        if not p.is_admin:
            if instance_only:
                raise await self.audit.deny(
                    "audit.read", p.actor, target_type="audit_chain", target_id="instance"
                )
            scope_ids = p.managed_org_ids
            if organization_id is not None and organization_id not in scope_ids:
                raise NotFoundError("Organisatie niet gevonden")
            if not scope_ids:
                return []
        return await self.repo.search(
            limit=limit,
            before_id=before_id,
            organization_ids=scope_ids,
            instance_chain=instance_only or None,
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            action_prefix=action,
            outcome=outcome,
            since=since,
            until=until,
        )

    async def verify(self, p: Principal, organization_id: UUID | None) -> VerifyResult:
        if organization_id is None:
            if not p.is_admin:
                raise await self.audit.deny(
                    "audit.verify", p.actor, target_type="audit_chain", target_id="instance"
                )
        elif not p.can_manage_org(organization_id):
            raise NotFoundError("Organisatie niet gevonden")
        return await self.audit.verify(organization_id)

    async def dashboard(self, p: Principal) -> DashboardOut:
        now = datetime.now(UTC)
        day_ago = now - timedelta(hours=24)
        users, orgs, teams = UserRepository(self.db), OrganizationRepository(self.db), TeamRepository(self.db)

        async def count_audit(*conds) -> int:
            return int(await self.db.scalar(select(func.count()).select_from(AuditLog).where(*conds)) or 0)

        if p.is_admin:
            active_sessions = int(
                await self.db.scalar(
                    select(func.count())
                    .select_from(UserSession)
                    .where(UserSession.revoked_at.is_(None), UserSession.expires_at > now)
                )
                or 0
            )
            return DashboardOut(
                users_total=await users.count(),
                users_active=await users.count(active_only=True),
                admins=await users.count(admins_only=True),
                organizations=await orgs.count(),
                teams=await teams.count(),
                active_sessions=active_sessions,
                logins_24h=await count_audit(
                    AuditLog.action == "auth.login",
                    AuditLog.outcome == "success",
                    AuditLog.occurred_at >= day_ago,
                ),
                denied_24h=await count_audit(AuditLog.outcome == "denied", AuditLog.occurred_at >= day_ago),
                recent_events=await self.repo.search(limit=10),
            )
        managed = p.managed_org_ids
        return DashboardOut(
            users_total=0,
            users_active=0,
            admins=0,
            organizations=len(p.org_roles),
            teams=len(p.team_roles),
            active_sessions=0,
            logins_24h=0,
            denied_24h=0,
            recent_events=await self.repo.search(limit=10, organization_ids=managed) if managed else [],
        )
