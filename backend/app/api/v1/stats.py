"""Lees-API voor dashboards: statistieken over VaultX, met een persoonlijke API-sleutel."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentPrincipal, DbSession, MetaDep, OIDCDep, SettingsDep, current_principal
from app.models import ApiKey
from app.repositories import MembershipRepository
from app.schemas.common import ORMModel
from app.services.api_keys import ApiKeyService, looks_like_api_key
from app.services.principal import Principal
from app.services.stats import StatsService

router = APIRouter(tags=["stats"])


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    # Leeg = verloopt nooit.
    expires_in_days: int | None = Field(None, ge=1, le=3650)


class ApiKeyOut(ORMModel):
    id: UUID
    name: str
    prefix: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    last_used_ip: str | None


class ApiKeyCreated(BaseModel):
    key: ApiKeyOut
    # Enkel nu zichtbaar; VaultX bewaart alleen een hash.
    token: str


async def stats_principal(
    request: Request, db: DbSession, settings: SettingsDep, oidc: OIDCDep, meta: MetaDep
) -> Principal:
    """Een API-sleutel ("Authorization: Bearer vxk_..."), anders de gewone login (webinterface)."""
    authz = request.headers.get("authorization", "")
    token = authz[7:].strip() if authz.lower().startswith("bearer ") else ""
    if looks_like_api_key(token):
        user = await ApiKeyService(db).authenticate(token, meta)
        memberships = await MembershipRepository(db).for_user(user.id)
        return Principal.build(user, memberships, auth_method="api_key", meta=meta)
    return await current_principal(request, db, settings, oidc, meta)


StatsPrincipal = Annotated[Principal, Depends(stats_principal)]


@router.get(
    "/stats",
    summary="Statistieken voor een dashboard (alleen tellingen, nooit inhoud)",
    description=(
        "Werkt met een API-sleutel (`Authorization: Bearer vxk_...`) of de gewone login. "
        "Een instantiebeheerder krijgt cijfers over heel VaultX (`scope: instance`), "
        "een gewone gebruiker enkel over zichzelf (`scope: user`)."
    ),
)
async def stats(p: StatsPrincipal, db: DbSession, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await StatsService(db).collect(p)


@router.get("/me/api-keys", response_model=list[ApiKeyOut], summary="Mijn API-sleutels")
async def list_api_keys(p: CurrentPrincipal, db: DbSession) -> list[ApiKey]:
    return await ApiKeyService(db).list(p)


@router.post(
    "/me/api-keys",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="API-sleutel aanmaken; de sleutel staat enkel in dit antwoord",
)
async def create_api_key(body: ApiKeyIn, p: CurrentPrincipal, db: DbSession) -> ApiKeyCreated:
    key, token = await ApiKeyService(db).create(p, body.name, body.expires_in_days)
    return ApiKeyCreated(key=ApiKeyOut.model_validate(key), token=token)


@router.delete(
    "/me/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT, summary="API-sleutel intrekken"
)
async def revoke_api_key(key_id: UUID, p: CurrentPrincipal, db: DbSession) -> Response:
    await ApiKeyService(db).revoke(p, key_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
