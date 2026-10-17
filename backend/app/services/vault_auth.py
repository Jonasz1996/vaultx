"""Inloggen van Bitwarden-clients op een VaultX-kluis.

Flow (zoals de officiële clients het doen):
1. prelogin: de client vraagt KDF-instellingen en salt op voor een e-mailadres;
2. de client leidt lokaal de master key af en stuurt een afgeleide hash naar
   /identity/connect/token (grant_type=password);
3. VaultX controleert die hash, en daarnaast of de VaultX-gebruiker nog actief
   is. Authentik blijft zo de baas over wie binnen mag: een gebruiker die in
   VaultX gedeactiveerd is, kan ook zijn kluis niet meer openen of verversen;
4. VaultX geeft een kortlevend access token (JWT, HS256) en een refresh token
   per apparaat. Het access token bevat de security stamp; wijzigt die (bv. bij
   het resetten van de kluis), dan zijn alle tokens meteen ongeldig.
"""

from __future__ import annotations

import hmac
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.core.context import RequestMeta
from app.core.security import (
    derive_key,
    dummy_master_password_hash,
    hash_token,
    new_token,
    open_secret,
    verify_master_password,
)
from app.models import User, VaultAccount, VaultDevice
from app.repositories import UserRepository, VaultAccountRepository, VaultDeviceRepository
from app.services import totp
from app.services.audit import AuditService
from app.services.principal import Actor

DEFAULT_KDF_ITERATIONS = 600_000
# "Onthoud mij" bij tweestapsverificatie: zo lang vraagt een apparaat geen code meer.
REMEMBER_DAYS = 30


def totp_context(user_id: UUID) -> str:
    return f"vault-totp:{user_id}"


def clear_two_factor(account: VaultAccount) -> None:
    """Zet tweestapsverificatie uit. Onthouden apparaten vervallen vanzelf: zonder
    geheim wordt er niet meer om een tweede stap gevraagd, en bij opnieuw aanzetten
    worden alle onthoud-tokens gewist (zie VaultService.enable_two_factor)."""
    account.totp_secret = None
    account.totp_pending = None
    account.totp_last_step = None
    account.recovery_code_hash = None
    account.two_factor_enabled_at = None


# Bitwarden DeviceType, voor leesbare apparaatnamen in de UI en de auditlog.
DEVICE_TYPES = {
    0: "Android",
    1: "iOS",
    2: "Chrome-extensie",
    3: "Firefox-extensie",
    4: "Opera-extensie",
    5: "Edge-extensie",
    6: "Windows-desktop",
    7: "macOS-desktop",
    8: "Linux-desktop",
    9: "Chrome",
    10: "Firefox",
    11: "Opera",
    12: "Edge",
    13: "Internet Explorer",
    14: "Onbekende browser",
    15: "Android (Amazon)",
    16: "Windows (UWP)",
    17: "Safari",
    18: "Vivaldi",
    19: "Vivaldi-extensie",
    20: "Safari-extensie",
    21: "SDK",
    22: "Server",
    23: "Windows-CLI",
    24: "macOS-CLI",
    25: "Linux-CLI",
    26: "DuckDuckGo",
}


def device_type_name(device_type: int) -> str:
    return DEVICE_TYPES.get(device_type, f"Type {device_type}")


class IdentityError(Exception):
    """Fout in /identity: wordt als OAuth-fout (400) met Bitwarden-ErrorModel teruggegeven."""

    def __init__(
        self, error: str, message: str, description: str | None = None, extra: dict | None = None
    ) -> None:
        super().__init__(message)
        self.error = error
        self.message = message
        self.description = description or error
        # Extra velden in het antwoord, bv. TwoFactorProviders2 als een tweede stap nodig is.
        self.extra = extra or {}


class TwoFactorProvider:
    """Bitwarden TwoFactorProviderType (enkel wat VaultX ondersteunt)."""

    AUTHENTICATOR = 0
    REMEMBER = 5
    RECOVERY_CODE = 8


@dataclass(slots=True)
class TwoFactorInput:
    provider: int | None
    token: str
    remember: bool


def two_factor_required(account: VaultAccount) -> IdentityError:
    return IdentityError(
        "invalid_grant",
        "Tweestapsverificatie is vereist.",
        "Two factor required.",
        extra={
            "TwoFactorProviders": [TwoFactorProvider.AUTHENTICATOR],
            "TwoFactorProviders2": {str(TwoFactorProvider.AUTHENTICATOR): None},
            "MasterPasswordPolicy": {"Object": "masterPasswordPolicy"},
            "Email": account.email,
        },
    )


class VaultAuthError(Exception):
    """Ongeldig of verlopen access token: de client moet verversen of opnieuw inloggen."""


@dataclass(slots=True)
class PreloginData:
    kdf: int
    iterations: int
    memory: int | None
    parallelism: int | None
    salt: str


@dataclass(slots=True)
class TokenGrant:
    access_token: str
    expires_in: int
    refresh_token: str | None
    account: VaultAccount
    # "Onthoud dit apparaat" na tweestapsverificatie; de client stuurt het later terug.
    two_factor_token: str | None = None


@dataclass(slots=True)
class VaultCaller:
    """Een geauthenticeerde Bitwarden-client."""

    user: User
    account: VaultAccount
    device: VaultDevice
    meta: RequestMeta

    @property
    def actor(self) -> Actor:
        return Actor.for_user(self.user, self.meta)

    @property
    def client_label(self) -> str:
        return f"{device_type_name(self.device.type)}: {self.device.name}"


def normalize_email(email: str) -> str:
    return email.strip().lower()


class VaultTokens:
    """Ondertekenen en controleren van de access tokens voor Bitwarden-clients."""

    ALGORITHM = "HS256"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.key = OctKey.import_key(
            derive_key(settings.secret_key.get_secret_value(), "vaultx/vault-access-token/v1")
        )
        self.issuer = f"{settings.public_url}|login"

    def issue(
        self, user: User, account: VaultAccount, device: VaultDevice, client_id: str
    ) -> tuple[str, int]:
        lifetime = self.settings.vault_access_token_minutes * 60
        now = int(time.time())
        claims = {
            "nbf": now,
            "iat": now,
            "exp": now + lifetime,
            "iss": self.issuer,
            # De clients lezen deze claims uit het token (zonder handtekening te controleren).
            "sub": str(account.user_id),
            "email": account.email,
            "email_verified": True,
            "name": user.display_name or user.username or account.email,
            "premium": True,
            "sstamp": account.security_stamp,
            "device": str(device.id),
            "devicetype": str(device.type),
            "client_id": client_id,
            "scope": ["api", "offline_access"],
            "amr": ["Application"],
        }
        return jwt.encode({"alg": self.ALGORITHM}, claims, self.key), lifetime

    def verify(self, token: str) -> dict:
        try:
            decoded = jwt.decode(token, self.key, algorithms=[self.ALGORITHM])
            jwt.JWTClaimsRegistry(
                iss={"essential": True, "value": self.issuer},
                exp={"essential": True},
                sub={"essential": True},
                sstamp={"essential": True},
                device={"essential": True},
                leeway=30,
            ).validate(decoded.claims)
        except (JoseError, ValueError) as exc:
            raise VaultAuthError("Ongeldig access token") from exc
        return decoded.claims


class VaultAuthService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.accounts = VaultAccountRepository(db)
        self.devices = VaultDeviceRepository(db)
        self.users = UserRepository(db)
        self.audit = AuditService(db)
        self.tokens = VaultTokens(settings)

    async def prelogin(self, email: str) -> PreloginData:
        email = normalize_email(email)
        account = await self.accounts.by_email(email)
        if account is None:
            # Geen verschil tussen bestaande en onbekende adressen: standaardwaarden teruggeven.
            return PreloginData(0, DEFAULT_KDF_ITERATIONS, None, None, email)
        return PreloginData(
            account.kdf_type,
            account.kdf_iterations,
            account.kdf_memory,
            account.kdf_parallelism,
            account.kdf_salt,
        )

    async def password_grant(
        self,
        *,
        username: str,
        password_hash: str,
        client_id: str,
        device_identifier: str,
        device_name: str,
        device_type: int,
        meta: RequestMeta,
        two_factor: TwoFactorInput | None = None,
    ) -> TokenGrant:
        email = normalize_email(username)
        account = await self.accounts.by_email(email)
        stored = account.master_password_hash if account else dummy_master_password_hash()
        valid = await run_in_threadpool(verify_master_password, password_hash, stored)
        now = datetime.now(UTC)

        if account is None:
            await self.audit.record(
                "vault.login",
                Actor.anonymous(meta),
                outcome="failure",
                target_type="vault",
                details={"reason": "onbekend e-mailadres", "client": device_type_name(device_type)},
            )
            await self.db.commit()
            raise IdentityError(
                "invalid_grant", "E-mailadres of master password is onjuist.", "invalid_username_or_password"
            )

        account = await self.accounts.lock(account.user_id)
        assert account is not None
        user = await self.users.get(account.user_id)
        assert user is not None
        actor = Actor.for_user(user, meta)

        if account.locked_until is not None and account.locked_until > now:
            await self.audit.record(
                "vault.login",
                actor,
                outcome="denied",
                target_type="vault",
                target_id=account.user_id,
                details={"reason": "tijdelijk vergrendeld", "client": device_type_name(device_type)},
            )
            await self.db.commit()
            raise IdentityError(
                "invalid_grant",
                "Te veel mislukte pogingen. Probeer het over enkele minuten opnieuw.",
                "account_locked",
            )

        if not valid:
            account.failed_logins += 1
            details = {"reason": "fout master password", "client": device_type_name(device_type)}
            if account.failed_logins >= self.settings.vault_max_failed_logins:
                account.locked_until = now + timedelta(minutes=self.settings.vault_lockout_minutes)
                account.failed_logins = 0
                details["locked_until"] = account.locked_until.isoformat()
            await self.audit.record(
                "vault.login",
                actor,
                outcome="failure",
                target_type="vault",
                target_id=account.user_id,
                details=details,
            )
            await self.db.commit()
            raise IdentityError(
                "invalid_grant", "E-mailadres of master password is onjuist.", "invalid_username_or_password"
            )

        if not user.is_active:
            await self.audit.record(
                "vault.login",
                actor,
                outcome="denied",
                target_type="vault",
                target_id=account.user_id,
                details={"reason": "gebruiker gedeactiveerd", "client": device_type_name(device_type)},
            )
            await self.db.commit()
            raise IdentityError("invalid_grant", "Je VaultX-account is gedeactiveerd.", "user_disabled")

        device = await self.devices.by_identifier(account.user_id, device_identifier)
        remember_token: str | None = None
        two_factor_method: str | None = None
        if account.two_factor_enabled:
            two_factor_method, remember_token = await self._second_step(
                account, actor, device, two_factor, device_type, now
            )

        account.failed_logins = 0
        account.locked_until = None
        refresh_token = new_token()
        if device is None:
            device = self.devices.add(
                VaultDevice(
                    user_id=account.user_id,
                    identifier=device_identifier,
                    name=device_name,
                    type=device_type,
                    refresh_token_hash=hash_token(refresh_token),
                    created_at=now,
                    last_seen_at=now,
                )
            )
        else:
            # Opnieuw inloggen op hetzelfde apparaat: nieuw refresh token, oude vervalt.
            device.name = device_name
            device.type = device_type
            device.refresh_token_hash = hash_token(refresh_token)
            device.last_seen_at = now
            device.revoked_at = None
        if remember_token is not None:
            device.remember_token_hash = hash_token(remember_token)
            device.remember_until = now + timedelta(days=REMEMBER_DAYS)
        await self.db.flush()
        await self.audit.record(
            "vault.login",
            actor,
            target_type="vault",
            target_id=account.user_id,
            details={
                "client": device_type_name(device_type),
                "device": device_name,
                "device_id": str(device.id),
                **({"two_factor": two_factor_method} if two_factor_method else {}),
            },
        )
        await self.db.commit()
        access_token, expires_in = self.tokens.issue(user, account, device, client_id)
        return TokenGrant(access_token, expires_in, refresh_token, account, remember_token)

    async def _second_step(
        self,
        account: VaultAccount,
        actor: Actor,
        device: VaultDevice | None,
        two_factor: TwoFactorInput | None,
        device_type: int,
        now: datetime,
    ) -> tuple[str, str | None]:
        """Tweede stap na een juist master password. Geeft (methode, nieuw onthoud-token) terug,
        of gooit IdentityError: zonder code de vraag om een code, met een foute code een fout
        die meetelt voor de vergrendeling (net als een fout master password)."""
        if two_factor is None or not two_factor.token:
            raise two_factor_required(account)
        if two_factor.provider == TwoFactorProvider.REMEMBER:
            if (
                device is not None
                and device.remember_token_hash is not None
                and device.remember_until is not None
                and device.remember_until > now
                and hmac.compare_digest(device.remember_token_hash, hash_token(two_factor.token))
            ):
                return "onthouden apparaat", None
            # Verlopen of onbekend: gewoon opnieuw om een code vragen (zoals Bitwarden).
            raise two_factor_required(account)

        method: str | None = None
        if two_factor.provider == TwoFactorProvider.AUTHENTICATOR and account.totp_secret is not None:
            secret = open_secret(
                self.settings.secret_key.get_secret_value(),
                account.totp_secret,
                totp_context(account.user_id),
            )
            step = totp.verify(secret, two_factor.token, account.totp_last_step)
            if step is not None:
                account.totp_last_step = step
                method = "authenticator"
        elif two_factor.provider == TwoFactorProvider.RECOVERY_CODE and account.recovery_code_hash:
            given = hash_token(totp.normalize_recovery_code(two_factor.token))
            if hmac.compare_digest(given, account.recovery_code_hash):
                # Zoals bij Bitwarden: de herstelcode zet tweestapsverificatie uit.
                clear_two_factor(account)
                await self.audit.record(
                    "vault.2fa.disable",
                    actor,
                    target_type="vault",
                    target_id=account.user_id,
                    details={"reason": "herstelcode gebruikt", "client": device_type_name(device_type)},
                )
                method = "herstelcode"
        if method is None:
            account.failed_logins += 1
            details = {"reason": "foute code (tweestapsverificatie)", "client": device_type_name(device_type)}
            if account.failed_logins >= self.settings.vault_max_failed_logins:
                account.locked_until = now + timedelta(minutes=self.settings.vault_lockout_minutes)
                account.failed_logins = 0
                details["locked_until"] = account.locked_until.isoformat()
            await self.audit.record(
                "vault.login",
                actor,
                outcome="failure",
                target_type="vault",
                target_id=account.user_id,
                details=details,
            )
            await self.db.commit()
            raise IdentityError(
                "invalid_grant", "De code is ongeldig. Probeer opnieuw.", "invalid_two_factor"
            )
        new_remember = new_token() if two_factor.remember and account.two_factor_enabled else None
        return method, new_remember

    async def refresh_grant(self, refresh_token: str, client_id: str) -> TokenGrant:
        invalid = IdentityError("invalid_grant", "Je sessie is verlopen. Log opnieuw in.", "invalid_grant")
        device = await self.devices.by_refresh_hash(hash_token(refresh_token))
        if device is None or device.revoked_at is not None:
            raise invalid
        now = datetime.now(UTC)
        if device.last_seen_at < now - timedelta(days=self.settings.vault_device_idle_days):
            raise invalid
        account = await self.accounts.get(device.user_id)
        user = await self.users.get(device.user_id)
        if account is None or user is None or not user.is_active:
            raise invalid
        device.last_seen_at = now
        await self.db.commit()
        access_token, expires_in = self.tokens.issue(user, account, device, client_id)
        return TokenGrant(access_token, expires_in, refresh_token, account)

    async def authenticate(self, access_token: str, meta: RequestMeta) -> VaultCaller:
        claims = self.tokens.verify(access_token)
        try:
            user_id = UUID(str(claims["sub"]))
            device_id = UUID(str(claims["device"]))
        except ValueError as exc:
            raise VaultAuthError("Ongeldig access token") from exc
        account = await self.accounts.get(user_id)
        if account is None or account.security_stamp != claims["sstamp"]:
            raise VaultAuthError("Kluis gereset of verwijderd")
        device = await self.devices.get(device_id)
        if device is None or device.user_id != user_id or device.revoked_at is not None:
            raise VaultAuthError("Apparaat afgemeld")
        user = await self.users.get(user_id)
        if user is None or not user.is_active:
            raise VaultAuthError("Gebruiker gedeactiveerd")
        return VaultCaller(user=user, account=account, device=device, meta=meta)
