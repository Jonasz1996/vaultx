"""Beveiligingssleutels en passkeys (WebAuthn/FIDO2) als tweede stap.

Gebruikt voor de kluis (Bitwarden-provider 7 "FIDO2 WebAuthn": extensies, apps en de
webkluis) en voor het VaultX-wachtwoordlogin. Een YubiKey, een andere FIDO2-sleutel of
een passkey (Windows Hello, telefoon, Bitwarden ...) werkt allemaal op dezelfde manier.

Challenges zijn zelfdragend: tijdstip + willekeur + HMAC over gebruiker en doel. VaultX
hoeft ze dus niet op te slaan, en twee apparaten die tegelijk inloggen zitten elkaar
niet in de weg. Een challenge geldt vijf minuten en wordt na gebruik geweigerd: per
gebruiker en doel moet elke geslaagde challenge nieuwer zijn dan de vorige.

WebAuthn hangt aan een domeinnaam (de RP ID, standaard de host van VAULTX_PUBLIC_URL).
Op een IP-adres kan de browser geen sleutel gebruiken; daar blijven de authenticator-app
en de herstelcode werken.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, options_to_json_dict
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core.config import Settings
from app.core.security import derive_key
from app.models import WebAuthnCredential
from app.repositories import WebAuthnCredentialRepository

SCOPE_VAULT = "vault"
SCOPE_LOGIN = "login"
CHALLENGE_SECONDS = 300
TIMEOUT_MS = 120_000
MAX_KEYS = 20
# Een assertion is een paar honderd bytes JSON; een attestation met certificaten wat meer.
MAX_CREDENTIAL_JSON = 16_384
_MAC_BYTES = 16


class WebAuthnError(Exception):
    """Ongeldige, verlopen of onbekende sleutel. De boodschap is voor de gebruiker."""


def rp_id(settings: Settings) -> str | None:
    """Domeinnaam waaraan de sleutels hangen, of None als VaultX enkel een IP-adres heeft."""
    host = (settings.webauthn_rp_id or urlsplit(settings.public_url).hostname or "").strip().lower()
    if not host:
        return None
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return host
    return None


def expected_origins(settings: Settings, rp: str) -> list[str]:
    """Adressen van VaultX waarop een sleutel mag gebruikt worden: die met de RP ID als host
    of als hoger domein (vaultx.example.com met RP ID example.com)."""
    out = []
    for origin in sorted(settings.allowed_origins):
        host = (urlsplit(origin).hostname or "").lower()
        if host == rp or host.endswith("." + rp):
            out.append(origin)
    return out


def available(settings: Settings) -> bool:
    return rp_id(settings) is not None


def _mac_key(settings: Settings) -> bytes:
    return derive_key(settings.secret_key.get_secret_value(), "vaultx/webauthn-challenge/v1")


def _mac(settings: Settings, user_id: UUID, purpose: str, body: bytes) -> bytes:
    msg = user_id.bytes + purpose.encode() + b"\x00" + body
    return hmac.new(_mac_key(settings), msg, hashlib.sha256).digest()[:_MAC_BYTES]


def new_challenge(settings: Settings, user_id: UUID, purpose: str) -> bytes:
    body = int(time.time() * 1000).to_bytes(8, "big") + secrets.token_bytes(16)
    return body + _mac(settings, user_id, purpose, body)


def check_challenge(settings: Settings, user_id: UUID, purpose: str, challenge: bytes) -> int:
    """Controleert een challenge die VaultX zelf uitgaf; geeft het tijdstip (ms) terug."""
    if len(challenge) != 24 + _MAC_BYTES:
        raise WebAuthnError("Ongeldige challenge. Probeer opnieuw.")
    body, mac = challenge[:24], challenge[24:]
    if not hmac.compare_digest(mac, _mac(settings, user_id, purpose, body)):
        raise WebAuthnError("Ongeldige challenge. Probeer opnieuw.")
    issued_ms = int.from_bytes(body[:8], "big")
    age = time.time() * 1000 - issued_ms
    if age > CHALLENGE_SECONDS * 1000 or age < -60_000:
        raise WebAuthnError("De aanvraag is verlopen. Probeer opnieuw.")
    return issued_ms


def parse_credential(raw: str | dict[str, Any]) -> dict[str, Any]:
    """JSON van de browser (PublicKeyCredential). De Bitwarden-connector schrijft
    clientDataJson met kleine letters; de bibliotheek verwacht clientDataJSON."""
    if isinstance(raw, str):
        if len(raw) > MAX_CREDENTIAL_JSON:
            raise WebAuthnError("Het antwoord van de sleutel is te groot.")
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise WebAuthnError("Het antwoord van de sleutel is onleesbaar.") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("response"), dict):
        raise WebAuthnError("Het antwoord van de sleutel is onleesbaar.")
    data = dict(raw)
    response = dict(data["response"])
    for alt in ("clientDataJson", "clientdatajson"):
        if alt in response and "clientDataJSON" not in response:
            response["clientDataJSON"] = response.pop(alt)
    data["response"] = response
    data.setdefault("type", "public-key")
    if "rawId" not in data and isinstance(data.get("id"), str):
        data["rawId"] = data["id"]
    if "id" not in data and isinstance(data.get("rawId"), str):
        data["id"] = data["rawId"]
    return data


def _client_challenge(data: dict[str, Any]) -> bytes:
    try:
        client_data = json.loads(base64url_to_bytes(data["response"]["clientDataJSON"]))
        return base64url_to_bytes(client_data["challenge"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WebAuthnError("Het antwoord van de sleutel is onleesbaar.") from exc


def _transports(values: list[str] | None) -> list[AuthenticatorTransport] | None:
    out = []
    for v in values or []:
        try:
            out.append(AuthenticatorTransport(v))
        except ValueError:
            continue
    return out or None


def _descriptor(c: WebAuthnCredential) -> PublicKeyCredentialDescriptor:
    return PublicKeyCredentialDescriptor(id=c.credential_id, transports=_transports(c.transports))


@dataclass(slots=True)
class NewCredential:
    credential_id: bytes
    public_key: bytes
    sign_count: int
    aaguid: str | None
    backed_up: bool
    transports: list[str]


class WebAuthnService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.repo = WebAuthnCredentialRepository(db)

    def _rp(self) -> str:
        rp = rp_id(self.settings)
        if rp is None:
            raise WebAuthnError(
                "Beveiligingssleutels werken enkel op een domeinnaam. Stel VAULTX_PUBLIC_URL in op "
                "het https-adres van VaultX (bv. https://vaultx.example.com)."
            )
        return rp

    async def credentials(self, user_id: UUID, scope: str) -> list[WebAuthnCredential]:
        return await self.repo.for_user(user_id, scope)

    # --- Registreren ------------------------------------------------------------------

    async def registration_options(
        self, user_id: UUID, scope: str, user_name: str, display_name: str
    ) -> dict[str, Any]:
        rp = self._rp()
        existing = await self.repo.for_user(user_id, scope)
        if len(existing) >= MAX_KEYS:
            raise WebAuthnError(f"Je kan hoogstens {MAX_KEYS} sleutels koppelen.")
        options = generate_registration_options(
            rp_id=rp,
            rp_name="VaultX",
            user_id=user_id.bytes,
            user_name=user_name,
            user_display_name=display_name,
            challenge=new_challenge(self.settings, user_id, f"register:{scope}"),
            timeout=TIMEOUT_MS,
            # Geen passkey die zelf de gebruiker kent: dit is enkel een tweede stap.
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.DISCOURAGED,
                user_verification=UserVerificationRequirement.DISCOURAGED,
            ),
            exclude_credentials=[_descriptor(c) for c in existing],
        )
        return options_to_json_dict(options)

    async def register(self, user_id: UUID, scope: str, name: str, raw: str | dict) -> WebAuthnCredential:
        rp = self._rp()
        data = parse_credential(raw)
        challenge = _client_challenge(data)
        check_challenge(self.settings, user_id, f"register:{scope}", challenge)
        try:
            verified = verify_registration_response(
                credential=data,
                expected_challenge=challenge,
                expected_rp_id=rp,
                expected_origin=expected_origins(self.settings, rp),
                require_user_verification=False,
            )
        except WebAuthnException as exc:
            raise WebAuthnError(f"De sleutel kon niet gekoppeld worden: {exc}") from exc
        existing = await self.repo.for_user(user_id, scope)
        if any(c.credential_id == verified.credential_id for c in existing):
            raise WebAuthnError("Deze sleutel is al gekoppeld.")
        if len(existing) >= MAX_KEYS:
            raise WebAuthnError(f"Je kan hoogstens {MAX_KEYS} sleutels koppelen.")
        transports = data["response"].get("transports")
        cred = self.repo.add(
            WebAuthnCredential(
                user_id=user_id,
                scope=scope,
                name=name.strip()[:100] or "Beveiligingssleutel",
                credential_id=verified.credential_id,
                public_key=verified.credential_public_key,
                sign_count=verified.sign_count,
                transports=[t for t in transports if isinstance(t, str)][:8]
                if isinstance(transports, list)
                else [],
                aaguid=verified.aaguid if verified.aaguid != "00000000-0000-0000-0000-000000000000" else None,
                backed_up=verified.credential_backed_up,
                created_at=datetime.now(UTC),
            )
        )
        await self.db.flush()
        return cred

    # --- Gebruiken (tweede stap) ------------------------------------------------------

    def assertion_options(
        self, user_id: UUID, scope: str, creds: list[WebAuthnCredential]
    ) -> dict[str, Any] | None:
        """Opties voor navigator.credentials.get, in het formaat van de Bitwarden-server
        (base64url, allowCredentials altijd aanwezig). None zonder sleutels of domeinnaam."""
        rp = rp_id(self.settings)
        if rp is None or not creds:
            return None
        options = generate_authentication_options(
            rp_id=rp,
            challenge=new_challenge(self.settings, user_id, f"assert:{scope}"),
            timeout=TIMEOUT_MS,
            allow_credentials=[_descriptor(c) for c in creds],
            user_verification=UserVerificationRequirement.DISCOURAGED,
        )
        out = options_to_json_dict(options)
        out.setdefault("allowCredentials", [])
        return out

    async def verify(
        self, user_id: UUID, scope: str, raw: str | dict, creds: list[WebAuthnCredential] | None = None
    ) -> WebAuthnCredential:
        """Controleert een antwoord van een gekoppelde sleutel en werkt de teller bij."""
        rp = self._rp()
        data = parse_credential(raw)
        challenge = _client_challenge(data)
        issued_ms = check_challenge(self.settings, user_id, f"assert:{scope}", challenge)
        creds = creds if creds is not None else await self.repo.for_user(user_id, scope)
        try:
            raw_id = base64url_to_bytes(str(data.get("rawId") or data.get("id")))
        except ValueError as exc:
            raise WebAuthnError("Het antwoord van de sleutel is onleesbaar.") from exc
        cred = next((c for c in creds if hmac.compare_digest(c.credential_id, raw_id)), None)
        if cred is None:
            raise WebAuthnError("Deze sleutel is niet gekoppeld.")
        last = max((c.last_challenge_ms or 0 for c in creds), default=0)
        if issued_ms <= last:
            raise WebAuthnError("Deze aanvraag werd al gebruikt. Probeer opnieuw.")
        try:
            verified = verify_authentication_response(
                credential=data,
                expected_challenge=challenge,
                expected_rp_id=rp,
                expected_origin=expected_origins(self.settings, rp),
                credential_public_key=cred.public_key,
                credential_current_sign_count=cred.sign_count,
                require_user_verification=False,
            )
        except WebAuthnException as exc:
            raise WebAuthnError(f"De sleutel werd niet aanvaard: {exc}") from exc
        cred.sign_count = verified.new_sign_count
        cred.last_used_at = datetime.now(UTC)
        cred.last_challenge_ms = issued_ms
        return cred

    async def delete_all(self, user_id: UUID, scope: str) -> None:
        await self.repo.delete_all(user_id, scope)
