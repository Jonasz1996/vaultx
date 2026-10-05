from uuid import UUID

from sqlalchemy import func, or_, select

from app.models import Membership, User
from app.repositories.base import Repository


class UserRepository(Repository[User]):
    model = User

    async def by_identity(self, issuer: str, subject: str) -> User | None:
        return await self.db.scalar(
            select(User).where(User.oidc_issuer == issuer, User.oidc_subject == subject)
        )

    async def search(
        self,
        *,
        query: str | None,
        limit: int,
        offset: int,
        organization_ids: list[UUID] | None = None,
    ) -> tuple[list[User], int]:
        stmt = select(User).order_by(func.lower(func.coalesce(User.display_name, User.email)), User.id)
        if query:
            like = f"%{query.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(User.email).like(like),
                    func.lower(User.username).like(like),
                    func.lower(User.display_name).like(like),
                )
            )
        if organization_ids is not None:
            stmt = stmt.where(
                User.id.in_(
                    select(Membership.user_id).where(Membership.organization_id.in_(organization_ids))
                )
            )
        return await self.page(stmt, limit=limit, offset=offset)

    async def count(self, *, active_only: bool = False, admins_only: bool = False) -> int:
        stmt = select(func.count()).select_from(User)
        if active_only:
            stmt = stmt.where(User.is_active.is_(True))
        if admins_only:
            stmt = stmt.where(User.is_admin.is_(True))
        return int(await self.db.scalar(stmt) or 0)
