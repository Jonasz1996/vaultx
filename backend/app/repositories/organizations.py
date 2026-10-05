from uuid import UUID

from sqlalchemy import func, select

from app.models import Membership, Organization, Team
from app.repositories.base import Repository


class OrganizationRepository(Repository[Organization]):
    model = Organization

    async def by_slug(self, slug: str) -> Organization | None:
        return await self.db.scalar(select(Organization).where(Organization.slug == slug))

    async def by_slugs(self, slugs: set[str]) -> dict[str, Organization]:
        if not slugs:
            return {}
        rows = await self.db.scalars(select(Organization).where(Organization.slug.in_(slugs)))
        return {o.slug: o for o in rows}

    async def list_page(
        self, *, limit: int, offset: int, only_ids: list[UUID] | None = None
    ) -> tuple[list[Organization], int]:
        stmt = select(Organization).order_by(func.lower(Organization.name), Organization.id)
        if only_ids is not None:
            stmt = stmt.where(Organization.id.in_(only_ids))
        return await self.page(stmt, limit=limit, offset=offset)

    async def member_counts(self, org_ids: list[UUID]) -> dict[UUID, int]:
        if not org_ids:
            return {}
        rows = await self.db.execute(
            select(Membership.organization_id, func.count())
            .where(Membership.organization_id.in_(org_ids), Membership.team_id.is_(None))
            .group_by(Membership.organization_id)
        )
        return {org_id: n for org_id, n in rows.all()}

    async def team_counts(self, org_ids: list[UUID]) -> dict[UUID, int]:
        if not org_ids:
            return {}
        rows = await self.db.execute(
            select(Team.organization_id, func.count())
            .where(Team.organization_id.in_(org_ids))
            .group_by(Team.organization_id)
        )
        return {org_id: n for org_id, n in rows.all()}

    async def count(self) -> int:
        return int(await self.db.scalar(select(func.count()).select_from(Organization)) or 0)


class TeamRepository(Repository[Team]):
    model = Team

    async def in_org(self, org_id: UUID, team_id: UUID) -> Team | None:
        return await self.db.scalar(select(Team).where(Team.id == team_id, Team.organization_id == org_id))

    async def by_slug(self, org_id: UUID, slug: str) -> Team | None:
        return await self.db.scalar(select(Team).where(Team.organization_id == org_id, Team.slug == slug))

    async def by_org_slug_pairs(self, pairs: set[tuple[UUID, str]]) -> dict[tuple[UUID, str], Team]:
        if not pairs:
            return {}
        org_ids = {p[0] for p in pairs}
        rows = await self.db.scalars(select(Team).where(Team.organization_id.in_(org_ids)))
        return {(t.organization_id, t.slug): t for t in rows if (t.organization_id, t.slug) in pairs}

    async def list_for_org(self, org_id: UUID) -> list[Team]:
        rows = await self.db.scalars(
            select(Team).where(Team.organization_id == org_id).order_by(func.lower(Team.name))
        )
        return list(rows)

    async def member_counts(self, team_ids: list[UUID]) -> dict[UUID, int]:
        if not team_ids:
            return {}
        rows = await self.db.execute(
            select(Membership.team_id, func.count())
            .where(Membership.team_id.in_(team_ids))
            .group_by(Membership.team_id)
        )
        return {team_id: n for team_id, n in rows.all()}

    async def count(self) -> int:
        return int(await self.db.scalar(select(func.count()).select_from(Team)) or 0)
