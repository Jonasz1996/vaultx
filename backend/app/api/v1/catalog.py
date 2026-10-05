from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentPrincipal, DbSession
from app.models import Application, DiscoveredHost
from app.schemas.catalog import (
    ApplicationCreate,
    ApplicationHostOut,
    ApplicationOut,
    ApplicationUpdate,
    AppStatusLiteral,
    DiscoveredHostOut,
)
from app.services.catalog import CatalogService, app_status

router = APIRouter(tags=["catalog"])


def host_out(h: DiscoveredHost) -> DiscoveredHostOut:
    return DiscoveredHostOut.model_validate(h)


def app_out(a: Application) -> ApplicationOut:
    hosts = sorted(a.hosts, key=lambda h: (h.removed_at is not None, h.primary_domain))
    return ApplicationOut(
        id=a.id,
        organization_id=a.organization_id,
        name=a.name,
        app_type=a.app_type,
        url=a.url,
        description=a.description,
        auth_method=a.auth_method,
        status=app_status(a),
        tags=a.tags or [],
        source=a.source,
        auto_update=a.auto_update,
        hosts=[
            ApplicationHostOut(
                id=h.id,
                connection_id=h.connection_id,
                connection_name=h.connection.name,
                domain_names=h.domain_names,
                forward=f"{h.forward_scheme}://{h.forward_host}:{h.forward_port}",
                enabled=h.enabled,
                nginx_online=h.nginx_online,
                forward_auth=h.forward_auth,
                warnings=h.warnings,
                removed_at=h.removed_at,
            )
            for h in hosts
        ],
        warning_count=sum(len(h.warnings) for h in hosts if h.removed_at is None),
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


@router.get(
    "/catalog",
    response_model=list[ApplicationOut],
    summary="Applicatiecatalogus over alle zichtbare organisaties",
)
async def catalog(
    p: CurrentPrincipal,
    db: DbSession,
    organization_id: UUID | None = None,
    q: Annotated[str | None, Query(max_length=100, description="Zoekt in naam, URL en type")] = None,
    status_: Annotated[AppStatusLiteral | None, Query(alias="status")] = None,
):
    apps = await CatalogService(db).list(p, organization_id=organization_id, q=q, status=status_)
    return [app_out(a) for a in apps]


base = "/organizations/{org_id}/applications"


@router.post(base, response_model=ApplicationOut, status_code=status.HTTP_201_CREATED)
async def create_application(org_id: UUID, data: ApplicationCreate, p: CurrentPrincipal, db: DbSession):
    return app_out(await CatalogService(db).create(p, org_id, data))


@router.get(base + "/{app_id}", response_model=ApplicationOut)
async def get_application(org_id: UUID, app_id: UUID, p: CurrentPrincipal, db: DbSession):
    return app_out(await CatalogService(db).get(p, org_id, app_id))


@router.patch(base + "/{app_id}", response_model=ApplicationOut)
async def update_application(
    org_id: UUID, app_id: UUID, data: ApplicationUpdate, p: CurrentPrincipal, db: DbSession
):
    return app_out(await CatalogService(db).update(p, org_id, app_id, data))


@router.delete(base + "/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_application(org_id: UUID, app_id: UUID, p: CurrentPrincipal, db: DbSession) -> Response:
    await CatalogService(db).delete(p, org_id, app_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
