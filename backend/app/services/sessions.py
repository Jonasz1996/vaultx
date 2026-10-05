"""Server-side sessies voor de admin UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.context import RequestMeta
from app.core.security import hash_token, new_token
from app.models import User, UserSession
from app.repositories import SessionRepository, UserRepository

# last_seen_at niet bij elke request wegschrijven.
TOUCH_INTERVAL = timedelta(seconds=60)


@dataclass(slots=True)
class ResolvedSession:
    session: UserSession
    user: User


class SessionService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.repo = SessionRepository(db)
        self.users = UserRepository(db)

    async def create(
        self, user: User, *, meta: RequestMeta, oidc_sid: str | None, id_token: str | None
    ) -> tuple[str, UserSession]:
        """Maakt een sessie aan (zonder commit) en geeft het ruwe cookietoken terug."""
        now = datetime.now(UTC)
        token = new_token()
        ua = meta.user_agent
        session = self.repo.add(
            UserSession(
                token_hash=hash_token(token),
                user_id=user.id,
                oidc_sid=oidc_sid,
                id_token=id_token,
                ip_address=meta.ip_address,
                user_agent=ua[:512] if ua else None,
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(hours=self.settings.session_ttl_hours),
            )
        )
        await self.db.flush()
        return token, session

    async def resolve(self, token: str) -> ResolvedSession | None:
        session = await self.repo.by_token_hash(hash_token(token))
        if session is None or session.revoked_at is not None:
            return None
        now = datetime.now(UTC)
        if session.expires_at <= now:
            return None
        if now - session.last_seen_at > timedelta(minutes=self.settings.session_idle_minutes):
            session.revoked_at = now
            session.revoked_reason = "idle_timeout"
            await self.db.commit()
            return None
        user = await self.users.get(session.user_id)
        if user is None or not user.is_active:
            return None
        if now - session.last_seen_at > TOUCH_INTERVAL:
            session.last_seen_at = now
            await self.db.commit()
        return ResolvedSession(session=session, user=user)

    async def revoke(self, session: UserSession, reason: str) -> None:
        if session.revoked_at is None:
            session.revoked_at = datetime.now(UTC)
            session.revoked_reason = reason

    async def revoke_for_user(self, user_id: UUID, reason: str) -> int:
        return len(
            await self.repo.revoke_where(UserSession.user_id == user_id, now=datetime.now(UTC), reason=reason)
        )

    async def revoke_by_sid(self, sid: str, reason: str) -> list[UUID]:
        return await self.repo.revoke_where(UserSession.oidc_sid == sid, now=datetime.now(UTC), reason=reason)

    async def active_for_user(self, user_id: UUID) -> list[UserSession]:
        return await self.repo.active_for_user(user_id, datetime.now(UTC))
