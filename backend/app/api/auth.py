"""Login, callback en logout via Authentik (OIDC authorization code + PKCE), plus de gewone
login met een VaultX-wachtwoord (fase 8)."""

from __future__ import annotations

import hmac
import logging
import re
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from app.api.deps import CSRF_HEADER, DbSession, MetaDep, OIDCDep, SettingsDep
from app.core.config import Settings
from app.core.errors import NotFoundError, PermissionDeniedError
from app.core.security import new_token, pkce_challenge
from app.repositories import UserRepository
from app.services.audit import AuditService
from app.services.identity import IdentityService
from app.services.oidc import OIDCError
from app.services.password_login import MAX_PASSWORD_LENGTH, PasswordLoginService
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


def _error_redirect(settings: Settings, code: str) -> RedirectResponse:
    resp = RedirectResponse(f"{settings.public_url}/login?{urlencode({'error': code})}", status_code=303)
    resp.delete_cookie(STATE_COOKIE, path="/auth")
    return resp


@router.get("/login", summary="Start login bij Authentik")
async def login(settings: SettingsDep, oidc: OIDCDep, next: str | None = None) -> Response:
    state, nonce, verifier = new_token(), new_token(), new_token(48)
    try:
        url = await oidc.authorization_url(state=state, nonce=nonce, code_challenge=pkce_challenge(verifier))
    except OIDCError:
        return _error_redirect(settings, "idp_unreachable")
    resp = RedirectResponse(url, status_code=303)
    resp.set_cookie(
        STATE_COOKIE,
        _serializer(settings).dumps({"s": state, "n": nonce, "v": verifier, "next": safe_next(next)}),
        max_age=STATE_MAX_AGE,
        path="/auth",
        httponly=True,
        secure=settings.cookie_secure,
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
        return _error_redirect(settings, reason)

    raw = request.cookies.get(STATE_COOKIE)
    try:
        saved = _serializer(settings).loads(raw, max_age=STATE_MAX_AGE) if raw else None
    except BadSignature:
        saved = None
    if not saved:
        # Geen (geldige) state-cookie: deze browser startte geen login. Niet in de auditlog, anders
        # kan iedereen zonder login die (gehashte, append-only) log volschrijven.
        log.info("OIDC-callback zonder geldige state-cookie (error=%r)", (error or "")[:40])
        return _error_redirect(settings, "idp_error" if error else "invalid_state")
    if error:
        # Enkel een OAuth-foutcode (bv. access_denied) bewaren, geen vrije tekst van de aanroeper.
        return await fail("idp_error", error if re.fullmatch(r"[a-z_]{1,40}", error) else "other")
    if not state or not code or not hmac.compare_digest(saved["s"], state):
        return await fail("invalid_state")

    try:
        tokens = await oidc.exchange_code(code=code, code_verifier=saved["v"])
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
            return _error_redirect(settings, "account_disabled")
        except IntegrityError:
            await db.rollback()
            if attempt:
                raise

    resp = RedirectResponse(f"{settings.public_url}{saved['next']}", status_code=303)
    resp.delete_cookie(STATE_COOKIE, path="/auth")
    _session_cookie(resp, settings, token)
    return resp


def _session_cookie(resp: Response, settings: Settings, token: str) -> None:
    resp.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


@router.get("/methods", summary="Welke loginmethodes de loginpagina toont")
async def methods(settings: SettingsDep) -> dict:
    return {"authentik": True, "password": settings.local_login_enabled}


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
    user = await PasswordLoginService(db, settings).authenticate(data.login, data.password, meta)
    token, _ = await SessionService(db, settings).create(user, meta=meta, auth_method="password")
    await db.commit()
    resp = JSONResponse({"redirect_url": f"{settings.public_url}{safe_next(data.next)}"})
    _session_cookie(resp, settings, token)
    return resp


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
            redirect = await oidc.end_session_url(
                id_token_hint=id_token, post_logout_redirect_uri=f"{settings.public_url}/login?logged_out=1"
            )
        except OIDCError:
            redirect = None
    resp = JSONResponse({"redirect_url": redirect or f"{settings.public_url}/login?logged_out=1"})
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
