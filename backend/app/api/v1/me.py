from fastapi import APIRouter

from app.api.deps import CurrentPrincipal, DbSession, SettingsDep
from app.schemas.users import MeOut, SessionOut, UserOut
from app.services.users import UserService

router = APIRouter(prefix="/me", tags=["me"])


@router.get("", response_model=MeOut, summary="Ingelogde gebruiker met lidmaatschappen")
async def me(p: CurrentPrincipal, db: DbSession, settings: SettingsDep) -> MeOut:
    svc = UserService(db, settings)
    return MeOut(
        **UserOut.model_validate(p.user).model_dump(),
        memberships=await svc.memberships_of(p.user.id),
        auth_method=p.auth_method,
    )


@router.get("/sessions", response_model=list[SessionOut], summary="Mijn actieve sessies")
async def my_sessions(p: CurrentPrincipal, db: DbSession, settings: SettingsDep) -> list[SessionOut]:
    sessions = await UserService(db, settings).list_sessions(p, p.user.id)
    return [
        SessionOut.model_validate(s).model_copy(update={"current": s.id == p.session_id}) for s in sessions
    ]
