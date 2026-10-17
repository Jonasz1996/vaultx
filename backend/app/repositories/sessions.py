from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update

from app.models import UserSession
from app.repositories.base import Repository


class SessionRepository(Repository[UserSession]):
    model = UserSession

    async def by_token_hash(self, token_hash: str) -> UserSession | None:
        return await self.db.scalar(select(UserSession).where(UserSession.token_hash == token_hash))

    async def active_for_user(self, user_id: UUID, now: datetime) -> list[UserSession]:
        rows = await self.db.scalars(
            select(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .order_by(UserSession.created_at.desc())
        )
        return list(rows)

    async def revoke_where(self, *conditions, now: datetime, reason: str) -> list[UUID]:
        result = await self.db.execute(
            update(UserSession)
            .where(UserSession.revoked_at.is_(None), *conditions)
            .values(revoked_at=now, revoked_reason=reason)
            .returning(UserSession.user_id)
        )
        return [row[0] for row in result.all()]
