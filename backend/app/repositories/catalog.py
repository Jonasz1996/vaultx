from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import selectinload

from app.models import Application, DiscoveredHost, NpmConnection
from app.repositories.base import Repository


class NpmConnectionRepository(Repository[NpmConnection]):
    model = NpmConnection

    async def in_org(self, org_id: UUID, connection_id: UUID) -> NpmConnection | None:
        return await self.db.scalar(
            select(NpmConnection).where(
                NpmConnection.id == connection_id, NpmConnection.organization_id == org_id
            )
        )

    async def by_name(self, org_id: UUID, name: str) -> NpmConnection | None:
        return await self.db.scalar(
            select(NpmConnection).where(NpmConnection.organization_id == org_id, NpmConnection.name == name)
        )

    async def list_for_org(self, org_id: UUID) -> list[NpmConnection]:
        rows = await self.db.scalars(
            select(NpmConnection)
            .where(NpmConnection.organization_id == org_id)
            .order_by(func.lower(NpmConnection.name))
        )
        return list(rows)

    async def list_enabled(self) -> list[NpmConnection]:
        rows = await self.db.scalars(select(NpmConnection).where(NpmConnection.enabled.is_(True)))
        return list(rows)

    async def host_counts(self, connection_ids: list[UUID]) -> dict[UUID, int]:
        if not connection_ids:
            return {}
        rows = await self.db.execute(
            select(DiscoveredHost.connection_id, func.count())
            .where(DiscoveredHost.connection_id.in_(connection_ids), DiscoveredHost.removed_at.is_(None))
            .group_by(DiscoveredHost.connection_id)
        )
        return {cid: n for cid, n in rows.all()}

    async def lock_for_sync(self, connection_id: UUID) -> None:
        """Serialiseert syncs van dezelfde koppeling (ook over meerdere nodes), tot de commit."""
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"vaultx-npm-sync:{connection_id}"},
        )


class DiscoveredHostRepository(Repository[DiscoveredHost]):
    model = DiscoveredHost

    async def for_connection(self, connection_id: UUID) -> list[DiscoveredHost]:
        rows = await self.db.scalars(
            select(DiscoveredHost)
            .where(DiscoveredHost.connection_id == connection_id)
            .options(selectinload(DiscoveredHost.application))
            .order_by(DiscoveredHost.removed_at.is_not(None), DiscoveredHost.npm_id)
        )
        return list(rows)

    async def in_connection(self, connection_id: UUID, host_id: UUID) -> DiscoveredHost | None:
        return await self.db.scalar(
            select(DiscoveredHost)
            .where(DiscoveredHost.id == host_id, DiscoveredHost.connection_id == connection_id)
            .options(selectinload(DiscoveredHost.application))
        )


class ApplicationRepository(Repository[Application]):
    model = Application

    def _with_hosts(self):
        return select(Application).options(
            selectinload(Application.hosts).selectinload(DiscoveredHost.connection)
        )

    async def in_org(self, org_id: UUID, app_id: UUID) -> Application | None:
        return await self.db.scalar(
            self._with_hosts()
            .where(Application.id == app_id, Application.organization_id == org_id)
            .execution_options(populate_existing=True)
        )

    async def list_for_orgs(self, org_ids: list[UUID] | None, *, q: str | None = None) -> list[Application]:
        stmt = self._with_hosts().order_by(func.lower(Application.name), Application.id)
        if org_ids is not None:
            stmt = stmt.where(Application.organization_id.in_(org_ids))
        if q:
            needle = q.lower()
            stmt = stmt.where(
                func.lower(Application.name).contains(needle, autoescape=True)
                | func.lower(func.coalesce(Application.url, "")).contains(needle, autoescape=True)
                | func.lower(func.coalesce(Application.app_type, "")).contains(needle, autoescape=True)
            )
        return list(await self.db.scalars(stmt))
