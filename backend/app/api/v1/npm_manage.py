"""NPM volledig beheren: alle objecten van een NPM-koppeling lezen en wijzigen."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request

from app.api.deps import CurrentPrincipal, DbSession, SettingsDep
from app.core.errors import InvalidOperationError
from app.schemas.npm_manage import (
    CheckOut,
    DnsProviderOut,
    KindName,
    ManageChangeOut,
    ManagePlanOut,
    ManageRequest,
    parse_data,
)
from app.services.npm_manage import NpmManageService
from app.services.npm_manage_plan import redact

router = APIRouter(prefix="/organizations/{org_id}/npm-connections/{conn_id}/manage", tags=["npm-beheer"])


def manage_service(request: Request, db: DbSession, settings: SettingsDep) -> NpmManageService:
    state = request.app.state
    return NpmManageService(
        db,
        settings,
        getattr(state, "npm_client_factory", None),
        getattr(state, "npm_prober", None),
        settle_seconds=getattr(state, "npm_probe_settle", None),
    )


ManageDep = Annotated[NpmManageService, Depends(manage_service)]
KindPath = Annotated[KindName, Path(description="Soort NPM-object")]


def _data(kind: str, req: ManageRequest) -> dict[str, Any]:
    try:
        return parse_data(kind, req.data)
    except ValueError as exc:
        raise InvalidOperationError(f"Ongeldige invoer: {exc}") from exc


@router.get("", summary="Alles uit NPM: hosts, redirects, 404-hosts, streams, access lists, certificaten")
async def overview(org_id: UUID, conn_id: UUID, p: CurrentPrincipal, svc: ManageDep) -> dict[str, Any]:
    return await svc.overview(p, org_id, conn_id)


@router.get("/dns-providers", response_model=list[DnsProviderOut], summary="DNS-providers voor Let's Encrypt")
async def dns_providers(org_id: UUID, conn_id: UUID, p: CurrentPrincipal, svc: ManageDep):
    return await svc.dns_providers(p, org_id, conn_id)


@router.get("/changes", response_model=list[ManageChangeOut], summary="Journaal van NPM-beheer")
async def changes(
    org_id: UUID,
    conn_id: UUID,
    p: CurrentPrincipal,
    svc: ManageDep,
    kind: Annotated[KindName | None, Query()] = None,
):
    return await svc.list_changes(p, org_id, conn_id, kind)


@router.post(
    "/{kind}/preview", response_model=ManagePlanOut, summary="Voorbeeld van een wijziging (schrijft niets)"
)
async def preview(
    org_id: UUID, conn_id: UUID, kind: KindPath, req: ManageRequest, p: CurrentPrincipal, svc: ManageDep
):
    data = _data(kind, req)
    view = await svc.preview(
        p,
        org_id,
        conn_id,
        kind,
        req.action,
        npm_id=req.npm_id,
        data=data,
        confirm_removals=req.confirm_removals,
    )
    plan = view.plan
    return ManagePlanOut(
        kind=plan.kind,  # type: ignore[arg-type]
        action=plan.action,  # type: ignore[arg-type]
        npm_id=plan.npm_id,
        label=plan.label,
        modified_on=plan.modified_on,
        can_apply=plan.can_apply,
        checks=[CheckOut(code=c.code, level=c.level, message=c.message) for c in plan.checks],
        steps=plan.steps,
        changes=plan.changes,
        before=redact(plan.kind, plan.before),
        after=redact(plan.kind, plan.after),
        expect=plan.expect,
        probe_url=view.probe.url if view.probe else None,
        certificate_check=view.certificate_check,
    )


@router.post(
    "/{kind}/apply", response_model=ManageChangeOut, summary="Wijziging uitvoeren met controle en rollback"
)
async def apply(
    org_id: UUID, conn_id: UUID, kind: KindPath, req: ManageRequest, p: CurrentPrincipal, svc: ManageDep
):
    data = _data(kind, req)
    return await svc.apply(
        p,
        org_id,
        conn_id,
        kind,
        req.action,
        npm_id=req.npm_id,
        data=data,
        expected_modified_on=req.expected_modified_on,
        confirm_removals=req.confirm_removals,
        verify=req.verify,
    )
