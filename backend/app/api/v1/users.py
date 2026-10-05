from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import CurrentPrincipal, DbSession, SettingsDep
from app.schemas.common import Page
from app.schemas.users import SessionOut, UserDetail, UserListItem, UserOut, UserSummary, UserUpdate
from app.services.users import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "",
    response_model=Page[UserListItem],
    response_model_exclude_none=True,
    summary="Gebruikers zoeken (beheerders: volledig, organisatiebeheerders: samenvatting)",
)
async def list_users(
    p: CurrentPrincipal,
    db: DbSession,
    settings: SettingsDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    users, total = await UserService(db, settings).search(p, query=q, limit=limit, offset=offset)
    if p.is_admin:
        items = [UserListItem.model_validate(u) for u in users]
    else:
        items = [UserListItem(**UserSummary.model_validate(u).model_dump()) for u in users]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


async def _detail(svc: UserService, user) -> UserDetail:
    return UserDetail(
        **UserOut.model_validate(user).model_dump(),
        oidc_issuer=user.oidc_issuer,
        oidc_subject=user.oidc_subject,
        memberships=await svc.memberships_of(user.id),
    )


@router.get("/{user_id}", response_model=UserDetail)
async def get_user(user_id: UUID, p: CurrentPrincipal, db: DbSession, settings: SettingsDep) -> UserDetail:
    svc = UserService(db, settings)
    return await _detail(svc, await svc.get(p, user_id))


@router.patch("/{user_id}", response_model=UserDetail, summary="Account (de)activeren")
async def update_user(
    user_id: UUID, data: UserUpdate, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> UserDetail:
    svc = UserService(db, settings)
    return await _detail(svc, await svc.update(p, user_id, data))


@router.get("/{user_id}/sessions", response_model=list[SessionOut])
async def user_sessions(user_id: UUID, p: CurrentPrincipal, db: DbSession, settings: SettingsDep):
    sessions = await UserService(db, settings).list_sessions(p, user_id)
    return [
        SessionOut.model_validate(s).model_copy(update={"current": s.id == p.session_id}) for s in sessions
    ]


@router.post("/{user_id}/sessions/revoke", summary="Alle sessies van een gebruiker intrekken")
async def revoke_user_sessions(
    user_id: UUID, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> dict:
    return {"revoked": await UserService(db, settings).revoke_sessions(p, user_id)}
