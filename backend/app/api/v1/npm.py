from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.deps import CurrentPrincipal, DbSession, SettingsDep
from app.api.v1.catalog import host_out
from app.schemas.catalog import (
    ACCESS_PATTERN,
    DiscoveredHostOut,
    DiscoveredHostUpdate,
    NpmChangeOut,
    NpmConnectionCreate,
    NpmConnectionOut,
    NpmConnectionUpdate,
    ProtectionAction,
    ProtectionApply,
    ProtectionPlanOut,
    SyncResultOut,
)
from app.services.npm import NpmService
from app.services.npm_write import NpmWriteService

router = APIRouter(prefix="/organizations/{org_id}/npm-connections", tags=["npm"])


def npm_service(request: Request, db: DbSession, settings: SettingsDep) -> NpmService:
    # Tests zetten app.state.npm_client_factory om tegen een nep-NPM te praten.
    return NpmService(db, settings, getattr(request.app.state, "npm_client_factory", None))


NpmDep = Annotated[NpmService, Depends(npm_service)]


def npm_write_service(request: Request, db: DbSession, settings: SettingsDep) -> NpmWriteService:
    state = request.app.state
    return NpmWriteService(
        db,
        settings,
        getattr(state, "npm_client_factory", None),
        getattr(state, "npm_prober", None),
        getattr(state, "authentik_client_factory", None),
    )


NpmWriteDep = Annotated[NpmWriteService, Depends(npm_write_service)]


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


# ---------------------------------------------------------------- Authentik-bescherming (fase 3)


@router.get(
    "/{conn_id}/hosts/{host_id}/protection",
    response_model=ProtectionPlanOut,
    summary="Voorbeeld: wat VaultX in NPM zou wijzigen, en of dat veilig kan",
    responses={502: {"description": "NPM onbereikbaar of weigert de aanmelding"}},
)
async def preview_protection(
    org_id: UUID,
    conn_id: UUID,
    host_id: UUID,
    p: CurrentPrincipal,
    svc: NpmWriteDep,
    action: Annotated[ProtectionAction, Query()] = "protect",
    access: Annotated[str, Query(pattern=ACCESS_PATTERN)] = "organization",
):
    view = await svc.preview(p, org_id, conn_id, host_id, action, access)
    plan = view.plan
    return ProtectionPlanOut(
        action=plan.action,
        npm_id=plan.npm_id,
        domain=view.domain,
        modified_on=plan.modified_on,
        can_apply=view.can_apply,
        checks=[{"code": c.code, "level": c.level, "message": c.message} for c in plan.checks],
        steps=plan.steps,
        before=plan.before,
        after=plan.after,
        probe_url=view.probe.url if view.probe else None,
        authentik=view.authentik,
    )


@router.post(
    "/{conn_id}/hosts/{host_id}/protection",
    response_model=NpmChangeOut,
    summary="Authentik-bescherming zetten of weghalen, met controle en automatisch terugzetten",
    description=(
        "Geeft altijd het journaal van de wijziging terug. `status` zegt wat er gebeurde: `applied`, "
        "`rolled_back` (controle faalde, oude config terug), `rollback_failed` (handwerk nodig) of "
        "`refused` (niets gewijzigd)."
    ),
)
async def apply_protection(
    org_id: UUID, conn_id: UUID, host_id: UUID, data: ProtectionApply, p: CurrentPrincipal, svc: NpmWriteDep
):
    return await svc.apply(
        p,
        org_id,
        conn_id,
        host_id,
        data.action,
        expected_modified_on=data.expected_modified_on,
        verify=data.verify,
        access=data.access,
    )


@router.get(
    "/{conn_id}/changes", response_model=list[NpmChangeOut], summary="Journaal van wijzigingen in NPM"
)
async def list_changes(
    org_id: UUID,
    conn_id: UUID,
    p: CurrentPrincipal,
    svc: NpmWriteDep,
    host_id: UUID | None = None,
):
    return await svc.list_changes(p, org_id, conn_id, host_id)
