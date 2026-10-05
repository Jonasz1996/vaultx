"""Login, callback en logout via Authentik (OIDC authorization code + PKCE)."""

from __future__ import annotations

import hmac
import logging
import re
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.api.deps import CSRF_HEADER, DbSession, MetaDep, OIDCDep, SettingsDep
from app.core.config import Settings
from app.core.errors import PermissionDeniedError
from app.core.security import new_token, pkce_challenge
from app.repositories import UserRepository
from app.services.audit import AuditService
from app.services.identity import IdentityService
from app.services.oidc import OIDCError
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
    if error:
        return await fail("idp_error", error[:100])
    if not saved or not state or not code or not hmac.compare_digest(saved["s"], state):
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

    try:
        result = await IdentityService(db, settings).login_from_claims(claims, meta)
    except PermissionDeniedError:
        return _error_redirect(settings, "account_disabled")

    token, _ = await SessionService(db, settings).create(
        result.user, meta=meta, oidc_sid=claims.get("sid"), id_token=tokens["id_token"]
    )
    await db.commit()

    resp = RedirectResponse(f"{settings.public_url}{saved['next']}", status_code=303)
    resp.delete_cookie(STATE_COOKIE, path="/auth")
    resp.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
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
    resolved = await sessions.resolve(token) if token else None
    if resolved:
        id_token = resolved.session.id_token
        await sessions.revoke(resolved.session, "logout")
        await AuditService(db).record(
            "auth.logout", Actor.for_user(resolved.user, meta), target_type="user", target_id=resolved.user.id
        )
        await db.commit()
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
