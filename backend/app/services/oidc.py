"""OpenID Connect-client voor Authentik (werkt met elke conforme OIDC-provider).

Bewust een kleine eigen implementatie bovenop httpx + joserfc in plaats van een
framework-integratie: zo is elke controle expliciet, testbaar en zonder
verborgen sessie-middleware. Ondersteunt:

* authorization code flow met PKCE (S256), state en nonce
* ID-tokenvalidatie (handtekening via JWKS, iss, aud, azp, exp, iat, nonce)
* validatie van bearer access tokens (JWT) voor API-clients
* validatie van back-channel logout tokens
* RP-initiated logout (end_session_endpoint)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError, JoseError
from joserfc.jwk import KeySet

from app.core.config import Settings
from app.core.errors import AuthenticationError

log = logging.getLogger(__name__)

# Alleen asymmetrische algoritmen. Configureer in Authentik een signing key
# op de provider; HS256 (ondertekend met het client secret) wordt geweigerd.
ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]
BACKCHANNEL_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"
CLOCK_LEEWAY_SECONDS = 60
METADATA_TTL_SECONDS = 3600
JWKS_MIN_REFRESH_SECONDS = 30


class OIDCError(AuthenticationError):
    code = "oidc_error"


class OIDCProvider:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._http = http or httpx.AsyncClient(
            timeout=settings.oidc_http_timeout_seconds,
            verify=settings.oidc_verify_tls,
            follow_redirects=False,
        )
        self._metadata: dict[str, Any] | None = None
        self._metadata_fetched_at = 0.0
        self._jwks: KeySet | None = None
        self._jwks_fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ discovery

    async def metadata(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._metadata is None or now - self._metadata_fetched_at > METADATA_TTL_SECONDS:
            async with self._lock:
                if self._metadata is None or now - self._metadata_fetched_at > METADATA_TTL_SECONDS:
                    resp = await self._get(self.settings.discovery_url)
                    data = resp.json()
                    for field in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
                        if field not in data:
                            raise OIDCError(f"Discovery-document mist '{field}'")
                    if data["issuer"] != self.settings.oidc_issuer:
                        # Mix-up-bescherming: de geconfigureerde issuer moet exact overeenkomen.
                        raise OIDCError(
                            f"Issuer in discovery ({data['issuer']}) verschilt van "
                            f"VAULTX_OIDC_ISSUER ({self.settings.oidc_issuer})"
                        )
                    self._metadata = data
                    self._metadata_fetched_at = time.monotonic()
        assert self._metadata is not None
        return self._metadata

    async def _jwks_keyset(self, force: bool = False) -> KeySet:
        now = time.monotonic()
        stale = self._jwks is None or now - self._jwks_fetched_at > METADATA_TTL_SECONDS
        may_refresh = now - self._jwks_fetched_at > JWKS_MIN_REFRESH_SECONDS
        if stale or (force and may_refresh):
            meta = await self.metadata()
            resp = await self._get(meta["jwks_uri"])
            self._jwks = KeySet.import_key_set(resp.json())
            self._jwks_fetched_at = time.monotonic()
        assert self._jwks is not None
        return self._jwks

    async def _get(self, url: str) -> httpx.Response:
        try:
            resp = await self._http.get(url, headers={"Accept": "application/json"})
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as exc:
            log.warning("OIDC-provider onbereikbaar: %s (%s)", url, exc)
            raise OIDCError("Identity provider is niet bereikbaar") from exc

    # ------------------------------------------------------------------ login flow

    async def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        meta = await self.metadata()
        params = {
            "response_type": "code",
            "client_id": self.settings.oidc_client_id,
            "redirect_uri": self.settings.redirect_uri,
            "scope": self.settings.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{meta['authorization_endpoint']}?{urlencode(params)}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> dict[str, Any]:
        meta = await self.metadata()
        try:
            resp = await self._http.post(
                meta["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.settings.redirect_uri,
                    "code_verifier": code_verifier,
                },
                auth=(self.settings.oidc_client_id, self.settings.oidc_client_secret.get_secret_value()),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise OIDCError("Identity provider is niet bereikbaar") from exc
        if resp.status_code != 200:
            log.warning("Token-uitwisseling geweigerd: %s %s", resp.status_code, resp.text[:300])
            raise OIDCError("Inlogcode werd geweigerd door de identity provider")
        tokens = resp.json()
        if "id_token" not in tokens:
            raise OIDCError("Geen id_token ontvangen (scope 'openid' ontbreekt?)")
        return tokens

    async def userinfo(self, access_token: str) -> dict[str, Any]:
        meta = await self.metadata()
        endpoint = meta.get("userinfo_endpoint")
        if not endpoint:
            return {}
        try:
            resp = await self._http.get(endpoint, headers={"Authorization": f"Bearer {access_token}"})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise OIDCError("Userinfo kon niet opgehaald worden") from exc
        return resp.json()

    async def end_session_url(
        self, *, id_token_hint: str | None, post_logout_redirect_uri: str
    ) -> str | None:
        meta = await self.metadata()
        endpoint = meta.get("end_session_endpoint")
        if not endpoint:
            return None
        params = {
            "post_logout_redirect_uri": post_logout_redirect_uri,
            "client_id": self.settings.oidc_client_id,
        }
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        return f"{endpoint}?{urlencode(params)}"

    # ------------------------------------------------------------------ tokenvalidatie

    async def _decode(self, token: str) -> jwt.Token:
        keyset = await self._jwks_keyset()
        try:
            return jwt.decode(token, keyset, algorithms=ALLOWED_ALGORITHMS)
        except InvalidKeyIdError:
            # Onbekende kid: sleutel is mogelijk geroteerd. JWKS verversen en één keer opnieuw.
            keyset = await self._jwks_keyset(force=True)
            try:
                return jwt.decode(token, keyset, algorithms=ALLOWED_ALGORITHMS)
            except (JoseError, ValueError) as exc:
                raise OIDCError("Token ondertekend met onbekende sleutel") from exc
        except (JoseError, ValueError) as exc:
            raise OIDCError("Token ongeldig of handtekening klopt niet") from exc

    def _validate_claims(self, claims: dict[str, Any], audience: str, **extra: Any) -> None:
        registry = jwt.JWTClaimsRegistry(
            leeway=CLOCK_LEEWAY_SECONDS,
            iss={"essential": True, "value": self.settings.oidc_issuer},
            aud={"essential": True, "value": audience},
            sub={"essential": True},
            **extra,
        )
        try:
            registry.validate(claims)
        except JoseError as exc:
            raise OIDCError(f"Tokenclaims ongeldig: {exc}") from exc

    async def validate_id_token(self, id_token: str, *, nonce: str) -> dict[str, Any]:
        token = await self._decode(id_token)
        claims = dict(token.claims)
        self._validate_claims(
            claims,
            self.settings.oidc_client_id,
            exp={"essential": True},
            iat={"essential": True},
            nonce={"essential": True, "value": nonce},
        )
        aud = claims.get("aud")
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != self.settings.oidc_client_id:
            raise OIDCError("ID-token heeft meerdere audiences zonder geldige azp")
        return claims

    async def validate_access_token(self, access_token: str) -> dict[str, Any]:
        token = await self._decode(access_token)
        claims = dict(token.claims)
        self._validate_claims(claims, self.settings.api_audience, exp={"essential": True})
        return claims

    async def validate_logout_token(self, logout_token: str) -> dict[str, Any]:
        token = await self._decode(logout_token)
        claims = dict(token.claims)
        registry = jwt.JWTClaimsRegistry(
            leeway=CLOCK_LEEWAY_SECONDS,
            iss={"essential": True, "value": self.settings.oidc_issuer},
            aud={"essential": True, "value": self.settings.oidc_client_id},
            iat={"essential": True},
        )
        try:
            registry.validate(claims)
        except JoseError as exc:
            raise OIDCError(f"Logout-token ongeldig: {exc}") from exc
        events = claims.get("events")
        if not isinstance(events, dict) or BACKCHANNEL_LOGOUT_EVENT not in events:
            raise OIDCError("Logout-token mist het back-channel logout event")
        if "nonce" in claims:
            raise OIDCError("Logout-token mag geen nonce bevatten")
        if not claims.get("sid") and not claims.get("sub"):
            raise OIDCError("Logout-token bevat geen sid of sub")
        return claims
