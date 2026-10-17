from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text

from app.models import AuditLog
from app.repositories.base import Repository


class AuditRepository(Repository[AuditLog]):
    model = AuditLog

    async def lock_chain(self, lock_key: int) -> None:
        """Serialiseert schrijvers per keten tot het einde van de transactie."""
        await self.db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})

    async def last_hash(self, organization_id: UUID | None) -> str | None:
        return await self.db.scalar(
            select(AuditLog.hash)
            .where(AuditLog.organization_id.is_not_distinct_from(organization_id))
            .order_by(AuditLog.id.desc())
            .limit(1)
        )

    async def search(
        self,
        *,
        limit: int,
        before_id: int | None = None,
        organization_ids: list[UUID] | None = None,
        instance_chain: bool | None = None,
        organization_id: UUID | None = None,
        actor_user_id: UUID | None = None,
        target_id: str | None = None,
        action_prefix: str | None = None,
        outcome: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
        if before_id is not None:
            stmt = stmt.where(AuditLog.id < before_id)
        if organization_ids is not None:
            stmt = stmt.where(AuditLog.organization_id.in_(organization_ids))
        if instance_chain:
            stmt = stmt.where(AuditLog.organization_id.is_(None))
        if organization_id is not None:
            stmt = stmt.where(AuditLog.organization_id == organization_id)
        if actor_user_id is not None:
            stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
        if target_id is not None:
            stmt = stmt.where(AuditLog.target_id == target_id)
        if action_prefix:
            stmt = stmt.where(AuditLog.action.startswith(action_prefix, autoescape=True))
        if outcome:
            stmt = stmt.where(AuditLog.outcome == outcome)
        if since is not None:
            stmt = stmt.where(AuditLog.occurred_at >= since)
        if until is not None:
            stmt = stmt.where(AuditLog.occurred_at < until)
        return list(await self.db.scalars(stmt))

    async def chain(self, organization_id: UUID | None, *, after_id: int, batch: int):
        return list(
            await self.db.scalars(
                select(AuditLog)
                .where(
                    AuditLog.organization_id.is_not_distinct_from(organization_id),
                    AuditLog.id > after_id,
                )
                .order_by(AuditLog.id)
                .limit(batch)
            )
        )
