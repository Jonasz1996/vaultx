from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import CurrentPrincipal, DbSession, SettingsDep
from app.schemas.app_login import (
    AppLoginConfigOut,
    AppLoginIn,
    AppLoginOut,
    AppLoginPlanOut,
    AppLoginRemoveIn,
    AppLoginStateOut,
    GrafanaAdminIn,
)
from app.services.app_login import AppLoginService, GrafanaAdmin, LoginPlan, LoginRequest

router = APIRouter(prefix="/organizations/{org_id}/applications/{app_id}/login", tags=["automatische login"])


def app_login_service(request: Request, db: DbSession, settings: SettingsDep) -> AppLoginService:
    # Tests zetten deze factories om tegen nep-Authentik, nep-Grafana en een nep-prober te praten.
    state = request.app.state
    return AppLoginService(
        db,
        settings,
        getattr(state, "authentik_client_factory", None),
        getattr(state, "grafana_client_factory", None),
        getattr(state, "npm_prober", None),
    )


LoginDep = Annotated[AppLoginService, Depends(app_login_service)]


def _admin(g: GrafanaAdminIn | None) -> GrafanaAdmin | None:
    return GrafanaAdmin(g.url, g.username, g.password) if g else None


def _request(data: AppLoginIn) -> LoginRequest:
    return LoginRequest(
        template=data.template,
        access=data.access,
        app_url=data.app_url,
        redirect_uris=data.redirect_uris,
        default_role=data.default_role,
        grafana=_admin(data.grafana),
    )


def _plan_out(plan: LoginPlan) -> AppLoginPlanOut:
    return AppLoginPlanOut(
        template=plan.template,  # type: ignore[arg-type]
        app_url=plan.app_url,
        redirect_uris=plan.redirect_uris,
        access=plan.access,
        can_apply=not plan.blocked,
        checks=[{"code": c.code, "level": c.level, "message": c.message} for c in plan.checks],  # type: ignore[misc]
        steps=plan.steps,
        groups=[g["name"] for g in plan.groups],
        configure_app=plan.configure_app,
    )


@router.get(
    "", response_model=AppLoginStateOut, summary="Automatische login van een app: sjablonen en toestand"
)
async def get_login(org_id: UUID, app_id: UUID, p: CurrentPrincipal, svc: LoginDep):
    return await svc.get(p, org_id, app_id)


@router.post(
    "/preview",
    response_model=AppLoginPlanOut,
    summary="Wat VaultX in Authentik (en Grafana) zou doen; wijzigt niets",
)
async def preview_login(org_id: UUID, app_id: UUID, data: AppLoginIn, p: CurrentPrincipal, svc: LoginDep):
    return _plan_out(await svc.preview(p, org_id, app_id, _request(data)))


@router.post(
    "",
    response_model=AppLoginOut,
    status_code=status.HTTP_201_CREATED,
    summary="Automatische login inrichten: OIDC-provider en applicatie in Authentik, optioneel Grafana",
)
async def create_login(org_id: UUID, app_id: UUID, data: AppLoginIn, p: CurrentPrincipal, svc: LoginDep):
    return await svc.apply(p, org_id, app_id, _request(data))


@router.post(
    "/check", response_model=AppLoginOut, summary="Opnieuw controleren of de app naar Authentik stuurt"
)
async def check_login(org_id: UUID, app_id: UUID, p: CurrentPrincipal, svc: LoginDep):
    return await svc.check(p, org_id, app_id)


@router.get(
    "/config",
    response_model=AppLoginConfigOut,
    summary="Config voor de app, met client secret (enkel beheerders, staat in de auditlog)",
)
async def login_config(org_id: UUID, app_id: UUID, p: CurrentPrincipal, svc: LoginDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await svc.config(p, org_id, app_id)


@router.post(
    "/remove",
    response_model=AppLoginOut | None,
    summary="Automatische login weghalen: Grafana terugzetten en opruimen in Authentik",
)
async def remove_login(
    org_id: UUID, app_id: UUID, data: AppLoginRemoveIn, p: CurrentPrincipal, svc: LoginDep
):
    return await svc.remove(p, org_id, app_id, _admin(data.grafana), data.force)
