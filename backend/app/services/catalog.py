"""Applicatiecatalogus: wat er draait, achter welke host, en hoe je er binnenkomt."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidOperationError, NotFoundError
from app.models import Application, AppSource, AuthMethod, DiscoveredHost
from app.repositories import ApplicationRepository, OrganizationRepository
from app.schemas.catalog import ApplicationCreate, ApplicationUpdate
from app.services.audit import AuditService
from app.services.principal import Principal

AUTHENTIK_METHODS = {
    AuthMethod.forward_auth.value,
    AuthMethod.oidc.value,
    AuthMethod.saml.value,
    AuthMethod.header.value,
}
RESTRICTED_METHODS = {AuthMethod.access_list.value, AuthMethod.app.value}


def app_status(app: Application) -> str:
    """Status zoals de catalogus hem toont (zie AppStatusLiteral in schemas/catalog.py)."""
    hosts: list[DiscoveredHost] = list(app.hosts)
    live = [h for h in hosts if h.removed_at is None]
    if hosts and not live:
        return "removed"
    if live and not any(h.enabled and h.nginx_online for h in live):
        return "offline"
    if app.auth_method in AUTHENTIK_METHODS:
        return "protected"
    login = app.login
    if login is not None and login.last_check_status == "ok":
        # Fase 5: de app meldt zelf aan via Authentik en VaultX zag dat werken.
        return "protected"
    if app.auth_method in RESTRICTED_METHODS:
        return "restricted"
    if app.auth_method == AuthMethod.none.value:
        return "unprotected"
    return "unknown"


def _diff(obj: object, changes: dict[str, Any]) -> dict[str, Any]:
    return {k: {"from": getattr(obj, k), "to": v} for k, v in changes.items() if getattr(obj, k) != v}


class CatalogService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.apps = ApplicationRepository(db)
        self.orgs = OrganizationRepository(db)
        self.audit = AuditService(db)

    async def _org_visible(self, p: Principal, org_id: UUID) -> None:
        if not p.can_view_org(org_id) or await self.orgs.get(org_id) is None:
            raise NotFoundError("Organisatie niet gevonden")

    async def _app_visible(self, p: Principal, org_id: UUID, app_id: UUID) -> Application:
        await self._org_visible(p, org_id)
        app = await self.apps.in_org(org_id, app_id)
        if app is None:
            raise NotFoundError("Applicatie niet gevonden")
        return app

    async def _require_manage(self, p: Principal, action: str, org_id: UUID, target_id: Any) -> None:
        if not p.can_manage_org(org_id):
            raise await self.audit.deny(
                action, p.actor, organization_id=org_id, target_type="application", target_id=target_id
            )

    async def _reload(self, app: Application) -> Application:
        fresh = await self.apps.in_org(app.organization_id, app.id)
        assert fresh is not None
        return fresh

    async def list(
        self,
        p: Principal,
        *,
        organization_id: UUID | None = None,
        q: str | None = None,
        status: str | None = None,
    ) -> list[Application]:
        if organization_id is not None:
            await self._org_visible(p, organization_id)
            org_ids: list[UUID] | None = [organization_id]
        else:
            org_ids = None if p.is_admin else list(p.org_roles)
        apps = await self.apps.list_for_orgs(org_ids, q=q)
        if status:
            apps = [a for a in apps if app_status(a) == status]
        return apps

    async def get(self, p: Principal, org_id: UUID, app_id: UUID) -> Application:
        return await self._app_visible(p, org_id, app_id)

    async def create(self, p: Principal, org_id: UUID, data: ApplicationCreate) -> Application:
        await self._org_visible(p, org_id)
        await self._require_manage(p, "application.create", org_id, data.name)
        app = self.apps.add(
            Application(
                organization_id=org_id, source=AppSource.manual.value, auto_update=False, **data.model_dump()
            )
        )
        await self.db.flush()
        await self.audit.record(
            "application.created",
            p.actor,
            organization_id=org_id,
            target_type="application",
            target_id=app.id,
            details=data.model_dump(),
        )
        await self.db.commit()
        return await self._reload(app)

    async def update(self, p: Principal, org_id: UUID, app_id: UUID, data: ApplicationUpdate) -> Application:
        app = await self._app_visible(p, org_id, app_id)
        await self._require_manage(p, "application.update", org_id, app_id)
        values = data.model_dump(exclude_unset=True)
        if values.get("name") is None:
            values.pop("name", None)
        if values.get("auth_method") is None:
            values.pop("auth_method", None)
        if values.get("tags") is None:
            values.pop("tags", None)
        # Een handmatige wijziging is een keuze die de volgende sync niet mag terugdraaien,
        # tenzij de gebruiker uitdrukkelijk auto_update meestuurt.
        explicit_auto = values.pop("auto_update", None)
        changes = _diff(app, values)
        if changes and app.auto_update and explicit_auto is None:
            explicit_auto = False
        if explicit_auto is not None and explicit_auto != app.auto_update:
            changes["auto_update"] = {"from": app.auto_update, "to": explicit_auto}
        for field, change in changes.items():
            setattr(app, field, change["to"])
        if changes:
            await self.audit.record(
                "application.updated",
                p.actor,
                organization_id=org_id,
                target_type="application",
                target_id=app.id,
                details={"changes": changes},
            )
        await self.db.commit()
        return await self._reload(app)

    async def delete(self, p: Principal, org_id: UUID, app_id: UUID) -> None:
        app = await self._app_visible(p, org_id, app_id)
        await self._require_manage(p, "application.delete", org_id, app_id)
        if app.login is not None:
            # Anders blijven de provider en applicatie in Authentik achter (en Grafana wijst ernaar).
            raise InvalidOperationError("Haal eerst de automatische login van deze app weg")
        # Hosts van dit item krijgen "negeren", anders maakt de volgende sync het opnieuw aan.
        ignored = []
        for host in app.hosts:
            host.ignored = True
            ignored.append(host.primary_domain)
        await self.audit.record(
            "application.deleted",
            p.actor,
            organization_id=org_id,
            target_type="application",
            target_id=app.id,
            details={"name": app.name, "source": app.source, "hosts_ignored": ignored},
        )
        await self.apps.delete(app)
        await self.db.commit()
