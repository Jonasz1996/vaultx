"""Ontgrendelen met Authentik op een apparaat (VaultX Invuller). Zie services/device_unlock.py.

Alles hier vraagt een VaultX-sessie in de browser (cookie). Een Bitwarden-token, een Authentik
access token of een API-sleutel volstaat nooit; openen vraagt bovendien een sessie die via
Authentik begon, met MFA en niet te oud.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentPrincipal, DbSession, SettingsDep
from app.services.device_unlock import DeviceUnlockService, device_json

router = APIRouter(prefix="/vault/device-unlock", tags=["vault"])


class DeviceUnlockOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    last_used_at: datetime | None
    last_used_ip: str | None


class DeviceUnlockStatus(BaseModel):
    server_enabled: bool
    user_enabled: bool
    require_mfa: bool
    max_auth_age_hours: int
    # Kan deze sessie nu een apparaat openen? Zo niet: waarom (code en uitleg).
    eligible: bool
    reason: str | None
    message: str | None
    devices: list[DeviceUnlockOut]


class DeviceUnlockRegister(BaseModel):
    device_identifier: str = Field(min_length=8, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    blob: str = Field(
        min_length=20, max_length=1000, description="User key versleuteld met de apparaatsleutel"
    )


class DeviceUnlockRegistered(BaseModel):
    id: str


class DeviceUnlockOne(BaseModel):
    id: str
    active: bool
    eligible: bool
    reason: str | None
    message: str | None


class DeviceUnlockBlob(BaseModel):
    blob: str


class DeviceUnlockSettings(BaseModel):
    enabled: bool


@router.get("", response_model=DeviceUnlockStatus, summary="Apparaten die met Authentik ontgrendelen")
async def device_unlock_status(
    p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> DeviceUnlockStatus:
    svc = DeviceUnlockService(db, settings)
    account = await svc._account(p.user.id)
    e = await svc.eligibility(p, account)
    return DeviceUnlockStatus(
        server_enabled=settings.device_unlock and settings.vault_enabled,
        user_enabled=bool(account and account.device_unlock_enabled),
        require_mfa=settings.device_unlock_require_mfa,
        max_auth_age_hours=settings.device_unlock_max_auth_age_hours,
        eligible=e.ok,
        reason=e.reason,
        message=e.message,
        devices=[DeviceUnlockOut(**device_json(d)) for d in await svc.devices(p.user.id)],
    )


@router.post(
    "",
    response_model=DeviceUnlockRegistered,
    status_code=status.HTTP_201_CREATED,
    summary="Apparaat registreren (na ontgrendelen met het master password)",
)
async def device_unlock_register(
    data: DeviceUnlockRegister, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> DeviceUnlockRegistered:
    row = await DeviceUnlockService(db, settings).register(p, data.device_identifier, data.name, data.blob)
    return DeviceUnlockRegistered(id=str(row.id))


@router.put("/settings", response_model=DeviceUnlockSettings, summary="Ontgrendelen met Authentik aan of uit")
async def device_unlock_settings(
    data: DeviceUnlockSettings, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> DeviceUnlockSettings:
    account = await DeviceUnlockService(db, settings).set_enabled(p, data.enabled)
    return DeviceUnlockSettings(enabled=account.device_unlock_enabled)


@router.get("/{unlock_id}", response_model=DeviceUnlockOne, summary="Is dit apparaat nog geldig?")
async def device_unlock_one(
    unlock_id: UUID, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> DeviceUnlockOne:
    svc = DeviceUnlockService(db, settings)
    row = await svc.get(p, unlock_id)
    e = await svc.eligibility(p)
    return DeviceUnlockOne(
        id=str(row.id), active=row.revoked_at is None, eligible=e.ok, reason=e.reason, message=e.message
    )


@router.post("/{unlock_id}/open", response_model=DeviceUnlockBlob, summary="Versleutelde user key ophalen")
async def device_unlock_open(
    unlock_id: UUID, p: CurrentPrincipal, db: DbSession, settings: SettingsDep, response: Response
) -> DeviceUnlockBlob:
    response.headers["Cache-Control"] = "no-store"
    return DeviceUnlockBlob(blob=await DeviceUnlockService(db, settings).open(p, unlock_id))


@router.delete("/{unlock_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Apparaat intrekken")
async def device_unlock_revoke(
    unlock_id: UUID, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> Response:
    await DeviceUnlockService(db, settings).revoke(p, unlock_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
