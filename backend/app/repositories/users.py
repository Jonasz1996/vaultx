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

    async def by_login(self, login: str) -> User | None:
        """Account met wachtwoordlogin op gebruikersnaam of e-mail (hoofdletterongevoelig).
        Bij twijfel (de naam past bij meer dan één account) geen resultaat."""
        name = login.strip().lower()
        if not name:
            return None
        rows = (
            await self.db.scalars(
                select(User).where(
                    User.password_hash.is_not(None),
                    or_(func.lower(User.username) == name, func.lower(User.email) == name),
                )
            )
        ).all()
        by_username = [u for u in rows if (u.username or "").lower() == name]
        if len(by_username) == 1:
            return by_username[0]
        return rows[0] if len(rows) == 1 else None

    async def login_name_taken(self, names: set[str], *, exclude_id: UUID | None = None) -> bool:
        """Is een van deze namen al de gebruikersnaam of het e-mailadres van een ander account?"""
        lowered = {n.lower() for n in names if n}
        if not lowered:
            return False
        stmt = select(User.id).where(
            or_(func.lower(User.username).in_(lowered), func.lower(User.email).in_(lowered))
        )
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        return await self.db.scalar(stmt.limit(1)) is not None

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
