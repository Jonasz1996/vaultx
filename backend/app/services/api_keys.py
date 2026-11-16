"""Persoonlijke API-sleutels voor de lees-API (/api/v1/stats), bv. voor een dashboard."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestMeta
from app.core.errors import AuthenticationError, InvalidOperationError, NotFoundError
from app.core.security import hash_token, new_token
from app.models import ApiKey, User
from app.models.base import utcnow
from app.services.audit import AuditService
from app.services.principal import Principal

TOKEN_PREFIX = "vxk_"
MAX_KEYS_PER_USER = 20
# "Laatst gebruikt" hoeft niet bij elke request naar de database: een dashboard vraagt vaak.
LAST_USED_RESOLUTION = timedelta(minutes=1)


def looks_like_api_key(token: str) -> bool:
    return token.startswith(TOKEN_PREFIX)


class ApiKeyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    async def list(self, p: Principal) -> list[ApiKey]:
        rows = await self.db.scalars(
            select(ApiKey)
            .where(ApiKey.user_id == p.user.id, ApiKey.revoked_at.is_(None))
            .order_by(ApiKey.created_at.desc())
        )
        return list(rows)

    async def create(self, p: Principal, name: str, expires_in_days: int | None) -> tuple[ApiKey, str]:
        if p.auth_method == "api_key":
            raise InvalidOperationError("Met een API-sleutel kan je geen nieuwe sleutels maken")
        count = await self.db.scalar(
            select(func.count())
            .select_from(ApiKey)
            .where(ApiKey.user_id == p.user.id, ApiKey.revoked_at.is_(None))
        )
        if (count or 0) >= MAX_KEYS_PER_USER:
            raise InvalidOperationError(
                f"Je hebt al {MAX_KEYS_PER_USER} sleutels; trek er eerst een in die je niet meer gebruikt"
            )
        token = TOKEN_PREFIX + new_token(32)
        key = ApiKey(
            user_id=p.user.id,
            name=name.strip(),
            token_hash=hash_token(token),
            prefix=token[:12],
            created_at=utcnow(),
            expires_at=utcnow() + timedelta(days=expires_in_days) if expires_in_days else None,
        )
        self.db.add(key)
        await self.db.flush()
        await self.audit.record(
            "api_key.create",
            p.actor,
            target_type="api_key",
            target_id=key.id,
            details={"name": key.name, "prefix": key.prefix, "expires_at": str(key.expires_at or "")},
        )
        await self.db.commit()
        return key, token

    async def revoke(self, p: Principal, key_id: UUID) -> None:
        key = await self.db.scalar(
            select(ApiKey).where(
                ApiKey.id == key_id, ApiKey.user_id == p.user.id, ApiKey.revoked_at.is_(None)
            )
        )
        if key is None:
            raise NotFoundError("Sleutel niet gevonden")
        key.revoked_at = utcnow()
        await self.audit.record(
            "api_key.revoke",
            p.actor,
            target_type="api_key",
            target_id=key.id,
            details={"name": key.name, "prefix": key.prefix},
        )
        await self.db.commit()

    async def authenticate(self, token: str, meta: RequestMeta) -> User:
        """Zoekt de gebruiker bij een sleutel en werkt "laatst gebruikt" bij."""
        key = await self.db.scalar(select(ApiKey).where(ApiKey.token_hash == hash_token(token)))
        now = utcnow()
        if key is None or key.revoked_at is not None:
            raise AuthenticationError("Ongeldige of ingetrokken API-sleutel")
        if key.expires_at is not None and key.expires_at <= now:
            raise AuthenticationError("API-sleutel verlopen")
        user = await self.db.get(User, key.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("Account gedeactiveerd")
        if key.last_used_at is None or now - key.last_used_at >= LAST_USED_RESOLUTION:
            key.last_used_at = now
            key.last_used_ip = meta.ip_address
            await self.db.commit()
        return user
