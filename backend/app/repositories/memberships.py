from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models import Membership, User
from app.repositories.base import Repository


class MembershipRepository(Repository[Membership]):
    model = Membership

    async def for_user(self, user_id: UUID) -> list[Membership]:
        rows = await self.db.scalars(
            select(Membership)
            .where(Membership.user_id == user_id)
            .options(selectinload(Membership.organization), selectinload(Membership.team))
        )
        return list(rows)

    async def org_membership(self, org_id: UUID, user_id: UUID) -> Membership | None:
        return await self.db.scalar(
            select(Membership).where(
                Membership.organization_id == org_id,
                Membership.user_id == user_id,
                Membership.team_id.is_(None),
            )
        )

    async def team_membership(self, team_id: UUID, user_id: UUID) -> Membership | None:
        return await self.db.scalar(
            select(Membership).where(Membership.team_id == team_id, Membership.user_id == user_id)
        )

    async def in_scope(self, membership_id: UUID, org_id: UUID, team_id: UUID | None) -> Membership | None:
        stmt = select(Membership).where(Membership.id == membership_id, Membership.organization_id == org_id)
        stmt = stmt.where(Membership.team_id.is_(None) if team_id is None else Membership.team_id == team_id)
        return await self.db.scalar(stmt.options(selectinload(Membership.user)))

    async def list_scope(self, org_id: UUID, team_id: UUID | None) -> list[Membership]:
        stmt = (
            select(Membership)
            .join(User, User.id == Membership.user_id)
            .where(Membership.organization_id == org_id)
            .options(selectinload(Membership.user))
            .order_by(func.lower(func.coalesce(User.display_name, User.email)))
        )
        stmt = stmt.where(Membership.team_id.is_(None) if team_id is None else Membership.team_id == team_id)
        return list(await self.db.scalars(stmt))

    async def count_owners(self, org_id: UUID) -> int:
        return int(
            await self.db.scalar(
                select(func.count()).where(
                    Membership.organization_id == org_id,
                    Membership.team_id.is_(None),
                    Membership.role == "owner",
                )
            )
            or 0
        )

    async def delete_user_team_memberships(self, org_id: UUID, user_id: UUID) -> None:
        rows = await self.db.scalars(
            select(Membership).where(
                Membership.organization_id == org_id,
                Membership.user_id == user_id,
                Membership.team_id.is_not(None),
            )
        )
        for m in rows:
            await self.db.delete(m)
