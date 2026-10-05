from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import CurrentPrincipal, DbSession, SettingsDep
from app.schemas.app_login import (
    AppLoginConfigOut,
    AppLoginIn,
    AppLoginOut,
    AppLoginPlanOut,
    AppLoginStateOut,
)
from app.services.app_login import AppLoginService, LoginPlan, LoginRequest

router = APIRouter(prefix="/organizations/{org_id}/applications/{app_id}/login", tags=["automatische login"])


def app_login_service(request: Request, db: DbSession, settings: SettingsDep) -> AppLoginService:
    # Tests zetten deze factory om tegen een nep-Authentik te praten.
    return AppLoginService(db, settings, getattr(request.app.state, "authentik_client_factory", None))


LoginDep = Annotated[AppLoginService, Depends(app_login_service)]


def _request(data: AppLoginIn) -> LoginRequest:
    return LoginRequest(redirect_uris=data.redirect_uris, access=data.access, app_url=data.app_url)


def _plan_out(plan: LoginPlan) -> AppLoginPlanOut:
    return AppLoginPlanOut(
        app_url=plan.app_url,
        redirect_uris=plan.redirect_uris,
        access=plan.access,
        can_apply=not plan.blocked,
        checks=[{"code": c.code, "level": c.level, "message": c.message} for c in plan.checks],  # type: ignore[misc]
        steps=plan.steps,
        groups=[g["name"] for g in plan.groups],
    )


@router.get("", response_model=AppLoginStateOut, summary="Automatische login van een app: toestand")
async def get_login(org_id: UUID, app_id: UUID, p: CurrentPrincipal, svc: LoginDep):
    return await svc.get(p, org_id, app_id)


@router.post(
    "/preview", response_model=AppLoginPlanOut, summary="Wat VaultX in Authentik zou doen; wijzigt niets"
)
async def preview_login(org_id: UUID, app_id: UUID, data: AppLoginIn, p: CurrentPrincipal, svc: LoginDep):
    return _plan_out(await svc.preview(p, org_id, app_id, _request(data)))


@router.post(
    "",
    response_model=AppLoginOut,
    status_code=status.HTTP_201_CREATED,
    summary="Automatische login inrichten: OIDC-provider, applicatie en groepsbindingen in Authentik",
)
async def create_login(org_id: UUID, app_id: UUID, data: AppLoginIn, p: CurrentPrincipal, svc: LoginDep):
    return await svc.apply(p, org_id, app_id, _request(data))


@router.get(
    "/config",
    response_model=AppLoginConfigOut,
    summary="Instellingen voor de app, met client secret (enkel beheerders, staat in de auditlog)",
)
async def login_config(org_id: UUID, app_id: UUID, p: CurrentPrincipal, svc: LoginDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await svc.config(p, org_id, app_id)


@router.post(
    "/remove",
    response_model=AppLoginOut | None,
    summary="Automatische login weghalen: provider en applicatie in Authentik opruimen",
)
async def remove_login(org_id: UUID, app_id: UUID, p: CurrentPrincipal, svc: LoginDep):
    return await svc.remove(p, org_id, app_id)
