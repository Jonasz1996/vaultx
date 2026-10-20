from uuid import UUID

from sqlalchemy import delete, select

from app.models import WebAuthnCredential
from app.repositories.base import Repository


class WebAuthnCredentialRepository(Repository[WebAuthnCredential]):
    model = WebAuthnCredential

    async def for_user(self, user_id: UUID, scope: str) -> list[WebAuthnCredential]:
        stmt = (
            select(WebAuthnCredential)
            .where(WebAuthnCredential.user_id == user_id, WebAuthnCredential.scope == scope)
            .order_by(WebAuthnCredential.created_at)
        )
        return list((await self.db.scalars(stmt)).all())

    async def delete_all(self, user_id: UUID, scope: str) -> None:
        await self.db.execute(
            delete(WebAuthnCredential).where(
                WebAuthnCredential.user_id == user_id, WebAuthnCredential.scope == scope
            )
        )
