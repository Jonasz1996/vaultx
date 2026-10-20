import json
from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentPrincipal, DbSession, SettingsDep
from app.schemas.users import (
    LoginKeyRemove,
    LoginKeyStart,
    LoginKeyStatus,
    MeOut,
    PasswordChange,
    SessionOut,
    UserOut,
)
from app.schemas.vault import SecurityKeyAdd, SecurityKeyAdded, SecurityKeyOptions, SecurityKeyOut
from app.services.password_login import LoginSecurityKeyService
from app.services.users import UserService
from app.services.webauthn import rp_id

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


@router.post("/password", summary="Mijn VaultX-wachtwoord wijzigen")
async def change_my_password(
    data: PasswordChange, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> dict:
    await UserService(db, settings).change_own_password(p, data)
    return {"ok": True}


# --- Beveiligingssleutels als tweede stap na het VaultX-wachtwoord ---------------------


@router.get("/security-keys", response_model=LoginKeyStatus, summary="Mijn sleutels voor het wachtwoordlogin")
async def my_security_keys(p: CurrentPrincipal, db: DbSession, settings: SettingsDep) -> LoginKeyStatus:
    keys = await LoginSecurityKeyService(db, settings).keys(p.user.id)
    return LoginKeyStatus(
        available=rp_id(settings) is not None,
        rp_id=rp_id(settings),
        has_password=p.user.has_password,
        keys=[SecurityKeyOut.model_validate(k) for k in keys],
    )


@router.post(
    "/security-keys/options",
    response_model=SecurityKeyOptions,
    summary="Sleutel koppelen, stap 1 (na controle van je wachtwoord)",
)
async def security_key_options(
    body: LoginKeyStart, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> SecurityKeyOptions:
    return SecurityKeyOptions(options=await LoginSecurityKeyService(db, settings).options(p, body.password))


@router.post(
    "/security-keys",
    response_model=SecurityKeyAdded,
    status_code=status.HTTP_201_CREATED,
    summary="Sleutel koppelen, stap 2; geeft de herstelcode bij de eerste sleutel",
)
async def add_security_key(
    body: SecurityKeyAdd, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> SecurityKeyAdded:
    key, recovery = await LoginSecurityKeyService(db, settings).add(p, body.name, json.dumps(body.credential))
    return SecurityKeyAdded(key=SecurityKeyOut.model_validate(key), recovery_code=recovery)


@router.post(
    "/security-keys/challenge",
    response_model=SecurityKeyOptions,
    summary="Opties om met een gekoppelde sleutel te bevestigen",
)
async def security_key_challenge(
    p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> SecurityKeyOptions:
    return SecurityKeyOptions(options=await LoginSecurityKeyService(db, settings).challenge(p))


@router.post(
    "/security-keys/{key_id}/remove",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sleutel weghalen: wachtwoord plus een sleutel of de herstelcode",
)
async def remove_security_key(
    key_id: UUID, body: LoginKeyRemove, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> Response:
    await LoginSecurityKeyService(db, settings).remove(
        p,
        key_id,
        body.password,
        security_key=json.dumps(body.security_key) if body.security_key else None,
        recovery_code=body.recovery_code,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
