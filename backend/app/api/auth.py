"""Login, callback en logout via Authentik (OIDC authorization code + PKCE), plus de gewone
login met een VaultX-wachtwoord (fase 8)."""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import re
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CSRF_HEADER, DbSession, MetaDep, OIDCDep, SettingsDep
from app.core.config import Settings
from app.core.context import RequestMeta
from app.core.errors import NotFoundError, PermissionDeniedError
from app.core.security import new_token, pkce_challenge
from app.models import User
from app.repositories import UserRepository
from app.services.audit import AuditService
from app.services.identity import IdentityService
from app.services.oidc import OIDCError
from app.services.password_login import MAX_PASSWORD_LENGTH, PasswordLoginService, SecondStep
from app.services.principal import Actor
from app.services.sessions import SessionService

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

STATE_COOKIE = "vaultx_oidc"
STATE_MAX_AGE = 600


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key.get_secret_value(), salt="vaultx-oidc-state")


# Relatief pad binnen VaultX: begint met één "/", geen backslash, spaties of
# stuurtekens (browsers strippen tabs, waardoor "/\t/evil" anders "//evil" wordt).
_SAFE_NEXT_RE = re.compile(r"^/(?![/\\])[A-Za-z0-9\-._~!$&'()*+,;=:@%/?#]*$")


def safe_next(value: str | None) -> str:
    """Enkel relatieve paden binnen VaultX (geen open redirect)."""
    if not value or len(value) > 2048 or not _SAFE_NEXT_RE.match(value):
        return "/"
    return value


def _error_redirect(code: str, next: str | None = None) -> RedirectResponse:
    # Relatief: de browser blijft op het adres waarop hij VaultX opende.
    query = {"error": code}
    if next and next != "/":
        query["next"] = next
    resp = RedirectResponse(f"/login?{urlencode(query)}", status_code=303)
    resp.delete_cookie(STATE_COOKIE, path="/auth")
    return resp


def _request_origin(request: Request) -> str:
    """Origin zoals de browser hem ziet (Host via nginx, schema via X-Forwarded-Proto)."""
    return f"{request.url.scheme}://{request.url.netloc}".lower()


def _allowed_origin(request: Request, settings: Settings) -> str | None:
    """Het origin van deze request als het in de configuratie staat; nooit blind de Host volgen."""
    origin = _request_origin(request)
    return origin if origin in settings.allowed_origins else None


def _cookie_secure(request: Request, settings: Settings) -> bool:
    """Secure-vlag voor cookies op dit adres.

    Altijd Secure, behalve bij een http-verzoek op een adres dat bewust zonder TLS mag: een
    http-origin uit VAULTX_EXTRA_ORIGINS of een privé/loopback-IP (bv. http://192.168.0.50 op
    het LAN). Een browser bewaart een Secure-cookie over http niet, dan lukt inloggen daar nooit.
    Een domeinnaam over http (bv. de publieke naam zonder NPM ervoor) krijgt nooit zo'n cookie.
    """
    if not settings.cookie_secure:
        return False
    if request.url.scheme == "https":
        return True
    if _request_origin(request) in settings.extra_origins:
        return False
    try:
        ip = ipaddress.ip_address((request.url.hostname or "").strip("[]"))
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback)


def _authentik_origin(request: Request, settings: Settings) -> str | None:
    """Origin waarvoor een Authentik-login kan: geconfigureerd, of dezelfde host als de publieke URL."""
    origin = _allowed_origin(request, settings)
    if origin is None and request.url.hostname == urlsplit(settings.public_url).hostname:
        origin = settings.public_origin  # zelfde host: de cookie hangt niet af van schema of poort
    return origin


@router.get("/login", summary="Start login bij Authentik")
async def login(request: Request, settings: SettingsDep, oidc: OIDCDep, next: str | None = None) -> Response:
    next = safe_next(next)
    origin = _authentik_origin(request, settings)
    if origin is None:
        # Authentik stuurt enkel terug naar een geregistreerde redirect-URI. Niet stilletjes naar
        # het publieke adres springen (dat is misschien onbereikbaar): op dit adres kan het niet.
        log.info("Authentik-login op %s, dat niet in VAULTX_EXTRA_ORIGINS staat", _request_origin(request))
        return _error_redirect("authentik_not_here", next)
    redirect_uri = f"{origin}/auth/callback"
    state, nonce, verifier = new_token(), new_token(), new_token(48)
    try:
        url = await oidc.authorization_url(
            state=state, nonce=nonce, code_challenge=pkce_challenge(verifier), redirect_uri=redirect_uri
        )
    except OIDCError:
        return _error_redirect("idp_unreachable", next)
    resp = RedirectResponse(url, status_code=303)
    resp.set_cookie(
        STATE_COOKIE,
        _serializer(settings).dumps({"s": state, "n": nonce, "v": verifier, "next": next, "r": redirect_uri}),
        max_age=STATE_MAX_AGE,
        path="/auth",
        httponly=True,
        secure=_cookie_secure(request, settings),
        samesite="lax",
    )
    return resp


@router.get("/callback", summary="OIDC redirect-URI")
async def callback(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    oidc: OIDCDep,
    meta: MetaDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    audit = AuditService(db)

    async def fail(reason: str, detail: str | None = None) -> RedirectResponse:
        await audit.record(
            "auth.login",
            Actor.anonymous(meta),
            outcome="failure",
            details={"reason": reason, "detail": detail},
        )
        await db.commit()
        return _error_redirect(reason, saved["next"] if saved else None)

    raw = request.cookies.get(STATE_COOKIE)
    try:
        saved = _serializer(settings).loads(raw, max_age=STATE_MAX_AGE) if raw else None
    except BadSignature:
        saved = None
    if not saved:
        # Geen (geldige) state-cookie: deze browser startte geen login. Niet in de auditlog, anders
        # kan iedereen zonder login die (gehashte, append-only) log volschrijven.
        log.info("OIDC-callback zonder geldige state-cookie (error=%r)", (error or "")[:40])
        return _error_redirect("idp_error" if error else "invalid_state")
    if error:
        # Enkel een OAuth-foutcode (bv. access_denied) bewaren, geen vrije tekst van de aanroeper.
        return await fail("idp_error", error if re.fullmatch(r"[a-z_]{1,40}", error) else "other")
    if not state or not code or not hmac.compare_digest(saved["s"], state):
        return await fail("invalid_state")

    try:
        tokens = await oidc.exchange_code(
            code=code, code_verifier=saved["v"], redirect_uri=saved.get("r") or settings.redirect_uri
        )
        claims = await oidc.validate_id_token(tokens["id_token"], nonce=saved["n"])
        if "groups" not in claims and tokens.get("access_token"):
            # Sommige providers zetten groepen enkel in userinfo.
            info = await oidc.userinfo(tokens["access_token"])
            if info.get("sub") == claims["sub"] and "groups" in info:
                claims["groups"] = info["groups"]
    except OIDCError as exc:
        log.warning("Login mislukt: %s", exc.message)
        return await fail("token_invalid", exc.message)

    # Twee gelijktijdige eerste logins van dezelfde gebruiker (of nieuwe organisaties uit de
    # groepen) botsen op een unieke sleutel: dan één keer opnieuw, nu bestaat de rij al.
    for attempt in range(2):
        try:
            result = await IdentityService(db, settings).login_from_claims(claims, meta)
            token, _ = await SessionService(db, settings).create(
                result.user, meta=meta, oidc_sid=claims.get("sid"), id_token=tokens["id_token"]
            )
            await db.commit()
            break
        except PermissionDeniedError:
            return _error_redirect("account_disabled", saved["next"])
        except IntegrityError:
            await db.rollback()
            if attempt:
                raise

    # Terug naar de pagina waar de gebruiker stond, op hetzelfde adres (relatief).
    resp = RedirectResponse(safe_next(saved["next"]), status_code=303)
    resp.delete_cookie(STATE_COOKIE, path="/auth")
    _session_cookie(resp, request, settings, token)
    return resp


def _session_cookie(resp: Response, request: Request, settings: Settings, token: str) -> None:
    resp.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
        httponly=True,
        secure=_cookie_secure(request, settings),
        samesite="lax",
    )


@router.get("/methods", summary="Welke loginmethodes de loginpagina toont")
async def methods(request: Request, settings: SettingsDep) -> dict:
    # Geen verbinding met Authentik nodig: de loginpagina laadt ook als Authentik of NPM plat ligt.
    return {
        "authentik": _authentik_origin(request, settings) is not None,
        "password": settings.local_login_enabled,
    }


class PasswordLogin(BaseModel):
    login: str = Field(min_length=1, max_length=320, description="Gebruikersnaam of e-mailadres")
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    next: str | None = None


@router.post(
    "/password",
    summary="Inloggen met gebruikersnaam en VaultX-wachtwoord",
    responses={401: {"description": "Onjuiste gegevens"}, 429: {"description": "Tijdelijk vergrendeld"}},
)
async def password_login(
    data: PasswordLogin, request: Request, db: DbSession, settings: SettingsDep, meta: MetaDep
) -> Response:
    if not settings.local_login_enabled:
        raise NotFoundError("Inloggen met een wachtwoord staat uit")
    if request.headers.get(CSRF_HEADER) != "1":
        # Ook hier: geen login vanuit een formulier op een andere site (login-CSRF).
        raise PermissionDeniedError("CSRF-header ontbreekt")
    result = await PasswordLoginService(db, settings).login(data.login, data.password, meta)
    if isinstance(result, SecondStep):
        # Wachtwoord klopt; nu de beveiligingssleutel (of de herstelcode), nog geen sessie.
        return JSONResponse(
            {"second_step": "security_key", "ticket": result.ticket, "security_key_options": result.options}
        )
    return await _password_session(request, db, settings, meta, result, data.next)


async def _password_session(
    request: Request, db: AsyncSession, settings: Settings, meta: RequestMeta, user: User, next: str | None
) -> Response:
    token, _ = await SessionService(db, settings).create(user, meta=meta, auth_method="password")
    await db.commit()
    # Relatief: wie VaultX op een ander adres opende, blijft daar (de cookie staat ook daar).
    resp = JSONResponse({"redirect_url": safe_next(next)})
    _session_cookie(resp, request, settings, token)
    return resp


class PasswordSecondStep(BaseModel):
    ticket: str = Field(min_length=1, max_length=512)
    security_key: dict[str, Any] | None = None
    recovery_code: str | None = Field(default=None, max_length=64)
    next: str | None = None


@router.post(
    "/password/second-step",
    summary="Tweede stap na het wachtwoord: beveiligingssleutel of herstelcode",
    responses={401: {"description": "Onjuist of verlopen"}, 429: {"description": "Tijdelijk vergrendeld"}},
)
async def password_second_step(
    data: PasswordSecondStep, request: Request, db: DbSession, settings: SettingsDep, meta: MetaDep
) -> Response:
    if not settings.local_login_enabled:
        raise NotFoundError("Inloggen met een wachtwoord staat uit")
    if request.headers.get(CSRF_HEADER) != "1":
        raise PermissionDeniedError("CSRF-header ontbreekt")
    user = await PasswordLoginService(db, settings).second_step(
        data.ticket,
        meta,
        security_key=json.dumps(data.security_key) if data.security_key else None,
        recovery_code=data.recovery_code,
    )
    return await _password_session(request, db, settings, meta, user, data.next)


@router.post("/logout", summary="Uitloggen (VaultX en Authentik)")
async def logout(
    request: Request, db: DbSession, settings: SettingsDep, oidc: OIDCDep, meta: MetaDep
) -> Response:
    if request.headers.get(CSRF_HEADER) != "1":
        raise PermissionDeniedError("CSRF-header ontbreekt")
    sessions = SessionService(db, settings)
    token = request.cookies.get(settings.session_cookie_name)
    id_token = None
    password_session = False
    resolved = await sessions.resolve(token) if token else None
    if resolved:
        id_token = resolved.session.id_token
        password_session = resolved.session.auth_method == "password"
        await sessions.revoke(resolved.session, "logout")
        await AuditService(db).record(
            "auth.logout", Actor.for_user(resolved.user, meta), target_type="user", target_id=resolved.user.id
        )
        await db.commit()
    redirect = None
    if not password_session:
        # Wie met een wachtwoord inlogde, heeft geen Authentik-sessie om af te sluiten.
        try:
            origin = _allowed_origin(request, settings) or settings.public_origin
            redirect = await oidc.end_session_url(
                id_token_hint=id_token, post_logout_redirect_uri=f"{origin}/login?logged_out=1"
            )
        except OIDCError:
            redirect = None
    resp = JSONResponse({"redirect_url": redirect or "/login?logged_out=1"})
    resp.delete_cookie(settings.session_cookie_name, path="/")
    return resp


@router.post(
    "/backchannel-logout",
    summary="OIDC back-channel logout (aangeroepen door Authentik)",
    responses={400: {"description": "Ongeldig logout-token"}},
)
async def backchannel_logout(
    db: DbSession, settings: SettingsDep, oidc: OIDCDep, meta: MetaDep, logout_token: str = Form(...)
) -> Response:
    headers = {"Cache-Control": "no-store"}
    try:
        claims = await oidc.validate_logout_token(logout_token)
    except OIDCError as exc:
        return JSONResponse(
            {"error": "invalid_request", "detail": exc.message}, status_code=400, headers=headers
        )
    sessions = SessionService(db, settings)
    revoked = []
    if claims.get("sid"):
        revoked = await sessions.revoke_by_sid(claims["sid"], "backchannel_logout")
    elif claims.get("sub"):
        user = await UserRepository(db).by_identity(claims["iss"], str(claims["sub"]))
        if user:
            n = await sessions.revoke_for_user(user.id, "backchannel_logout")
            revoked = [user.id] * n
    await AuditService(db).record(
        "auth.backchannel_logout",
        Actor(type="idp", label=claims["iss"], meta=meta),
        target_type="user",
        target_id=revoked[0] if revoked else None,
        details={"sid": claims.get("sid"), "sub": claims.get("sub"), "sessions_revoked": len(revoked)},
    )
    await db.commit()
    return Response(status_code=200, headers=headers)
