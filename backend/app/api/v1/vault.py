import json
from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentPrincipal, DbSession, SettingsDep
from app.core.errors import NotFoundError
from app.schemas.vault import (
    SecurityKeyAdd,
    SecurityKeyAdded,
    SecurityKeyOptions,
    SecurityKeyOut,
    SecurityKeyStart,
    TwoFactorDisable,
    TwoFactorEnable,
    TwoFactorEnableOut,
    TwoFactorSetup,
    TwoFactorSetupOut,
    VaultDeviceOut,
    VaultEnroll,
    VaultPasswordChange,
    VaultReset,
    VaultStatusOut,
    VaultUnlock,
    VaultUnlockOut,
)
from app.services.vault import VaultService
from app.services.vault_auth import device_type_name
from app.services.webauthn import rp_id

router = APIRouter(prefix="/me/vault", tags=["vault"])


def _device_out(d) -> VaultDeviceOut:
    return VaultDeviceOut.model_validate(d).model_copy(update={"type_name": device_type_name(d.type)})


async def _status(p: CurrentPrincipal, db: DbSession, settings: SettingsDep) -> VaultStatusOut:
    if not settings.vault_enabled:
        raise NotFoundError("De kluis is uitgeschakeld op deze server")
    svc = VaultService(db)
    st = await svc.status(p)
    a = st.account
    keys = await svc.security_keys(p.user.id, settings) if a else []
    return VaultStatusOut(
        enrolled=a is not None,
        email=st.email,
        blocked_reason=st.blocked_reason,
        server_url=settings.public_url,
        kdf=a.kdf_type if a else None,
        kdf_iterations=a.kdf_iterations if a else None,
        kdf_memory=a.kdf_memory if a else None,
        kdf_parallelism=a.kdf_parallelism if a else None,
        kdf_salt=a.kdf_salt if a else None,
        enrolled_at=a.created_at if a else None,
        revision_date=a.revision_date if a else None,
        item_count=st.item_count,
        trash_count=st.trash_count,
        folder_count=st.folder_count,
        note_count=st.note_count,
        two_factor_enabled=bool(a and a.two_factor_enabled),
        two_factor_enabled_at=a.two_factor_enabled_at if a else None,
        authenticator_enabled=bool(a and a.totp_secret is not None),
        security_keys=[SecurityKeyOut.model_validate(k) for k in keys],
        security_keys_available=rp_id(settings) is not None,
        security_keys_rp_id=rp_id(settings),
        devices=[_device_out(d) for d in st.devices],
    )


@router.get("", response_model=VaultStatusOut, summary="Status van mijn kluis")
async def vault_status(p: CurrentPrincipal, db: DbSession, settings: SettingsDep) -> VaultStatusOut:
    return await _status(p, db, settings)


@router.post(
    "",
    response_model=VaultStatusOut,
    status_code=status.HTTP_201_CREATED,
    summary="Kluis activeren met client-side afgeleide sleutels",
)
async def enroll(
    body: VaultEnroll, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> VaultStatusOut:
    if not settings.vault_enabled:
        raise NotFoundError("De kluis is uitgeschakeld op deze server")
    await VaultService(db).enroll(p, body)
    return await _status(p, db, settings)


@router.post(
    "/unlock",
    response_model=VaultUnlockOut,
    summary="Kluis ontgrendelen in de browser (versleutelde user key na controle master password)",
)
async def unlock(
    body: VaultUnlock, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> VaultUnlockOut:
    if not settings.vault_enabled:
        raise NotFoundError("De kluis is uitgeschakeld op deze server")
    account = await VaultService(db).unlock(p, body.master_password_hash, settings)
    return VaultUnlockOut(user_key=account.user_key)


@router.post(
    "/password",
    response_model=VaultStatusOut,
    summary="Master password of sleutelafleiding wijzigen (alle apparaten worden afgemeld)",
)
async def change_password(
    body: VaultPasswordChange, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> VaultStatusOut:
    if not settings.vault_enabled:
        raise NotFoundError("De kluis is uitgeschakeld op deze server")
    await VaultService(db).change_password(p, body, settings)
    return await _status(p, db, settings)


@router.post(
    "/2fa/setup",
    response_model=TwoFactorSetupOut,
    summary="Tweestapsverificatie instellen: nieuw TOTP-geheim (na controle master password)",
)
async def two_factor_setup(
    body: TwoFactorSetup, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> TwoFactorSetupOut:
    secret, uri = await VaultService(db).setup_two_factor(p, body.master_password_hash, settings)
    return TwoFactorSetupOut(secret=secret, otpauth_uri=uri)


@router.post(
    "/2fa/enable",
    response_model=TwoFactorEnableOut,
    summary="Tweestapsverificatie aanzetten met een eerste code; geeft de herstelcode",
)
async def two_factor_enable(
    body: TwoFactorEnable, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> TwoFactorEnableOut:
    return TwoFactorEnableOut(recovery_code=await VaultService(db).enable_two_factor(p, body.code, settings))


@router.post(
    "/2fa/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Tweestapsverificatie uitzetten (master password + code of herstelcode)",
)
async def two_factor_disable(
    body: TwoFactorDisable, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> Response:
    await VaultService(db).disable_two_factor(p, body.master_password_hash, body.code, settings, _key(body))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _key(body: TwoFactorDisable) -> str | None:
    return json.dumps(body.security_key) if body.security_key else None


@router.post(
    "/2fa/authenticator/remove",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Authenticator-app weghalen (sleutels blijven); master password + tweede stap",
)
async def remove_authenticator(
    body: TwoFactorDisable, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> Response:
    await VaultService(db).remove_authenticator(p, body.master_password_hash, body.code, _key(body), settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/2fa/challenge",
    response_model=SecurityKeyOptions,
    summary="Opties om met een gekoppelde sleutel te bevestigen (leeg zonder sleutels)",
)
async def two_factor_challenge(
    p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> SecurityKeyOptions:
    return SecurityKeyOptions(options=await VaultService(db).two_factor_challenge(p, settings))


@router.post(
    "/2fa/security-keys/options",
    response_model=SecurityKeyOptions,
    summary="Beveiligingssleutel of passkey koppelen, stap 1 (na controle master password)",
)
async def security_key_options(
    body: SecurityKeyStart, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> SecurityKeyOptions:
    return SecurityKeyOptions(
        options=await VaultService(db).security_key_options(p, body.master_password_hash, settings)
    )


@router.post(
    "/2fa/security-keys",
    response_model=SecurityKeyAdded,
    status_code=status.HTTP_201_CREATED,
    summary="Beveiligingssleutel of passkey koppelen, stap 2; geeft de herstelcode bij de eerste",
)
async def add_security_key(
    body: SecurityKeyAdd, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> SecurityKeyAdded:
    key, recovery = await VaultService(db).add_security_key(
        p, body.name, json.dumps(body.credential), settings
    )
    return SecurityKeyAdded(key=SecurityKeyOut.model_validate(key), recovery_code=recovery)


@router.post(
    "/2fa/security-keys/{key_id}/remove",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Beveiligingssleutel weghalen; master password + tweede stap",
)
async def remove_security_key(
    key_id: UUID, body: TwoFactorDisable, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> Response:
    await VaultService(db).remove_security_key(
        p, key_id, body.master_password_hash, body.code, _key(body), settings
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Kluis met alle items verwijderen (master password, of net opnieuw ingelogd)",
)
async def reset(body: VaultReset, p: CurrentPrincipal, db: DbSession, settings: SettingsDep) -> Response:
    await VaultService(db).reset(p, body.master_password_hash, settings)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/devices/{device_id}/revoke", response_model=VaultDeviceOut, summary="Apparaat afmelden van mijn kluis"
)
async def revoke_device(device_id: UUID, p: CurrentPrincipal, db: DbSession) -> VaultDeviceOut:
    return _device_out(await VaultService(db).revoke_device(p, device_id))
