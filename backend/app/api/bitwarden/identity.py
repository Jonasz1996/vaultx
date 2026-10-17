"""/identity: prelogin, token-endpoint en (geweigerde) registratie."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.bitwarden.deps import BitwardenRoute, vault_enabled
from app.api.deps import DbSession, MetaDep, SettingsDep
from app.core.errors import InvalidOperationError
from app.schemas.bitwarden import PreloginIn, prelogin_json, token_json
from app.services.vault_auth import IdentityError, VaultAuthService

router = APIRouter(
    prefix="/identity",
    tags=["bitwarden-identity"],
    route_class=BitwardenRoute,
    dependencies=[Depends(vault_enabled)],
)

# client_id's van de officiële clients.
KNOWN_CLIENTS = {"web", "browser", "desktop", "mobile", "cli", "connector"}


@router.post("/accounts/prelogin/password", summary="KDF-instellingen en salt voor een e-mailadres")
@router.post("/accounts/prelogin", summary="KDF-instellingen (oud pad)", include_in_schema=False)
async def prelogin(body: PreloginIn, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    return prelogin_json(await VaultAuthService(db, settings).prelogin(body.email))


def _form_value(form: Any, *names: str) -> str:
    for name in names:
        value = form.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


@router.post("/connect/token", summary="Inloggen of token verversen (OAuth2, form-encoded)")
async def token(request: Request, db: DbSession, settings: SettingsDep, meta: MetaDep) -> dict[str, Any]:
    form = await request.form()
    grant_type = _form_value(form, "grant_type")
    client_id = _form_value(form, "client_id") or "web"
    svc = VaultAuthService(db, settings)

    if grant_type == "refresh_token":
        refresh = _form_value(form, "refresh_token")
        if not refresh:
            raise IdentityError("invalid_request", "refresh_token ontbreekt")
        return token_json(await svc.refresh_grant(refresh, client_id))

    if grant_type != "password":
        raise IdentityError(
            "unsupported_grant_type",
            "Deze loginmethode wordt nog niet ondersteund. Log in met e-mail en master password.",
        )
    if client_id not in KNOWN_CLIENTS:
        raise IdentityError("invalid_client", "Onbekende client")
    if _form_value(form, "twoFactorToken", "twoFactorProvider"):
        raise IdentityError("invalid_request", "Tweestapsverificatie wordt nog niet ondersteund")
    username = _form_value(form, "username")
    password = _form_value(form, "password")
    identifier = _form_value(form, "deviceIdentifier", "device_identifier")
    if not username or not password or not identifier:
        raise IdentityError("invalid_request", "username, password en deviceIdentifier zijn verplicht")
    try:
        device_type = int(_form_value(form, "deviceType", "device_type") or "14")
    except ValueError:
        device_type = 14
    device_name = _form_value(form, "deviceName", "device_name") or "onbekend"
    grant = await svc.password_grant(
        username=username,
        password_hash=password,
        client_id=client_id,
        device_identifier=identifier[:255],
        device_name=device_name[:255],
        device_type=device_type,
        meta=meta,
    )
    return token_json(grant)


@router.post("/accounts/register/send-verification-email", include_in_schema=False)
@router.post("/accounts/register/finish", include_in_schema=False)
@router.post("/accounts/register", include_in_schema=False)
async def register(settings: SettingsDep) -> None:
    raise InvalidOperationError(
        f"Een kluis maak je aan in VaultX zelf, na inloggen via Authentik: {settings.public_url}/vault"
    )
