from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import CurrentPrincipal, DbSession, SettingsDep
from app.api.v1.catalog import host_out
from app.schemas.catalog import (
    DiscoveredHostOut,
    DiscoveredHostUpdate,
    NpmConnectionCreate,
    NpmConnectionOut,
    NpmConnectionUpdate,
    SyncResultOut,
)
from app.services.npm import NpmService

router = APIRouter(prefix="/organizations/{org_id}/npm-connections", tags=["npm"])


def npm_service(request: Request, db: DbSession, settings: SettingsDep) -> NpmService:
    # Tests zetten app.state.npm_client_factory om tegen een nep-NPM te praten.
    return NpmService(db, settings, getattr(request.app.state, "npm_client_factory", None))


NpmDep = Annotated[NpmService, Depends(npm_service)]


async def _out(svc: NpmService, conn) -> NpmConnectionOut:
    return NpmConnectionOut.model_validate(conn).model_copy(
        update={"host_count": await svc.host_count(conn.id)}
    )


@router.get("", response_model=list[NpmConnectionOut], summary="NPM-koppelingen van een organisatie")
async def list_connections(org_id: UUID, p: CurrentPrincipal, svc: NpmDep):
    conns, counts = await svc.list_connections(p, org_id)
    return [
        NpmConnectionOut.model_validate(c).model_copy(update={"host_count": counts.get(c.id, 0)})
        for c in conns
    ]


@router.post("", response_model=NpmConnectionOut, status_code=status.HTTP_201_CREATED)
async def create_connection(org_id: UUID, data: NpmConnectionCreate, p: CurrentPrincipal, svc: NpmDep):
    return await _out(svc, await svc.create_connection(p, org_id, data))


@router.get("/{conn_id}", response_model=NpmConnectionOut)
async def get_connection(org_id: UUID, conn_id: UUID, p: CurrentPrincipal, svc: NpmDep):
    return await _out(svc, await svc.get_connection(p, org_id, conn_id))


@router.patch("/{conn_id}", response_model=NpmConnectionOut)
async def update_connection(
    org_id: UUID, conn_id: UUID, data: NpmConnectionUpdate, p: CurrentPrincipal, svc: NpmDep
):
    return await _out(svc, await svc.update_connection(p, org_id, conn_id, data))


@router.delete("/{conn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(org_id: UUID, conn_id: UUID, p: CurrentPrincipal, svc: NpmDep) -> Response:
    await svc.delete_connection(p, org_id, conn_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{conn_id}/sync",
    response_model=SyncResultOut,
    summary="Proxy hosts uit NPM inlezen en de catalogus bijwerken",
    responses={502: {"description": "NPM onbereikbaar of weigert de aanmelding"}},
)
async def sync_connection(org_id: UUID, conn_id: UUID, p: CurrentPrincipal, svc: NpmDep):
    result = await svc.sync_for_user(p, org_id, conn_id)
    return SyncResultOut(**{f: getattr(result, f) for f in SyncResultOut.model_fields})


@router.get("/{conn_id}/hosts", response_model=list[DiscoveredHostOut], summary="Ontdekte proxy hosts")
async def list_hosts(org_id: UUID, conn_id: UUID, p: CurrentPrincipal, svc: NpmDep):
    return [host_out(h) for h in await svc.list_hosts(p, org_id, conn_id)]


@router.patch(
    "/{conn_id}/hosts/{host_id}", response_model=DiscoveredHostOut, summary="Host negeren of terugzetten"
)
async def update_host(
    org_id: UUID, conn_id: UUID, host_id: UUID, data: DiscoveredHostUpdate, p: CurrentPrincipal, svc: NpmDep
):
    return host_out(await svc.set_host_ignored(p, org_id, conn_id, host_id, data.ignored))
