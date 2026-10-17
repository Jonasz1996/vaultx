"""Gedeelde FastAPI-dependencies: config, database, OIDC-provider en de ingelogde gebruiker."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.context import RequestMeta
from app.core.db import get_db
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.repositories import MembershipRepository, UserRepository
from app.services.oidc import OIDCError, OIDCProvider
from app.services.principal import Principal
from app.services.sessions import SessionService

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-vaultx-csrf"

DbSession = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def request_meta(request: Request) -> RequestMeta:
    return RequestMeta(
        request_id=getattr(request.state, "request_id", None),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


def get_oidc(request: Request) -> OIDCProvider:
    return request.app.state.oidc


MetaDep = Annotated[RequestMeta, Depends(request_meta)]
OIDCDep = Annotated[OIDCProvider, Depends(get_oidc)]


async def current_principal(
    request: Request, db: DbSession, settings: SettingsDep, oidc: OIDCDep, meta: MetaDep
) -> Principal:
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        # API-clients: Authentik access token (JWT), gevalideerd tegen de JWKS.
        try:
            claims = await oidc.validate_access_token(authz[7:].strip())
        except OIDCError as exc:
            raise AuthenticationError(exc.message) from exc
        user = await UserRepository(db).by_identity(claims["iss"], str(claims["sub"]))
        if user is None:
            raise AuthenticationError("Onbekende gebruiker: log eerst één keer in via de webinterface")
        if not user.is_active:
            raise AuthenticationError("Account gedeactiveerd")
        memberships = await MembershipRepository(db).for_user(user.id)
        return Principal.build(user, memberships, auth_method="bearer", meta=meta)

    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise AuthenticationError("Niet ingelogd")
    resolved = await SessionService(db, settings).resolve(token)
    if resolved is None:
        raise AuthenticationError("Sessie verlopen of ingetrokken")
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        # Cookie-authenticatie + wijzigende request: eis een custom header. Die kan een
        # andere site niet meesturen zonder CORS-preflight, en CORS staat uit.
        raise PermissionDeniedError("CSRF-header ontbreekt")
    memberships = await MembershipRepository(db).for_user(resolved.user.id)
    return Principal.build(
        resolved.user,
        memberships,
        auth_method="session",
        meta=meta,
        session_id=resolved.session.id,
    )


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
