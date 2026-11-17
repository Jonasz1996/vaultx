"""Ontgrendelen met Authentik op een apparaat (VaultX Invuller, ontwerp KB-02).

"Authentik authenticeert, het apparaat ontsleutelt." De extensie maakt een willekeurige
apparaatsleutel D die enkel in de browser staat en stuurt de user key van de kluis, versleuteld
met D, naar VaultX. Later haalt ze die blob op met een VaultX-sessie die via Authentik begon
(met MFA, jonger dan 12 uur) en pakt hem uit met D.

| aanvaller heeft                         | resultaat                                   |
|-----------------------------------------|---------------------------------------------|
| dump van de VaultX-database             | niets: de blob heeft D nodig                |
| je Authentik-account (ook met MFA)      | niets: D staat enkel in jouw browser        |
| je browserprofiel (D)                   | niets zonder geldige Authentik-sessie + MFA |

Elke opening (en elke weigering) komt in de auditlog. Master password wijzigen, de kluis
verwijderen of de functie uitzetten trekt alle apparaten in.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import InvalidOperationError, NotFoundError, PermissionDeniedError
from app.models import UserSession, VaultAccount, VaultDeviceUnlock
from app.services.audit import AuditService
from app.services.principal import Principal

ENC_STRING_RE = re.compile(r"^2\.[A-Za-z0-9+/=]{16,64}\|[A-Za-z0-9+/=]{16,512}\|[A-Za-z0-9+/=]{16,64}$")
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")

REASONS = {
    "server_disabled": "Ontgrendelen met Authentik staat uit op deze server (VAULTX_DEVICE_UNLOCK).",
    "no_vault": "Je hebt nog geen kluis.",
    "user_disabled": "Je zette ontgrendelen met Authentik uit voor je kluis.",
    "not_browser_session": "Enkel met een VaultX-sessie in de browser, niet met een token.",
    "not_authentik": "Je meldde je aan met een VaultX-wachtwoord, niet via Authentik.",
    "mfa_required": (
        "Je aanmelding bij Authentik gebruikte geen tweede stap (MFA). Zet MFA aan in Authentik en "
        "meld je opnieuw aan."
    ),
    "too_old": "Je aanmelding bij Authentik is te oud. Meld je opnieuw aan.",
}


@dataclass
class Eligibility:
    ok: bool
    reason: str | None = None

    @property
    def message(self) -> str | None:
        return REASONS.get(self.reason or "")


class DeviceUnlockService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.audit = AuditService(db)

    # ------------------------------------------------------------------ voorwaarden

    async def _account(self, user_id: uuid.UUID) -> VaultAccount | None:
        return await self.db.get(VaultAccount, user_id)

    async def eligibility(self, p: Principal, account: VaultAccount | None = None) -> Eligibility:
        if not self.settings.device_unlock or not self.settings.vault_enabled:
            return Eligibility(False, "server_disabled")
        account = account or await self._account(p.user.id)
        if account is None:
            return Eligibility(False, "no_vault")
        if not account.device_unlock_enabled:
            return Eligibility(False, "user_disabled")
        if p.auth_method != "session" or p.session_id is None:
            return Eligibility(False, "not_browser_session")
        session = await self.db.get(UserSession, p.session_id)
        if session is None or session.auth_method != "oidc":
            return Eligibility(False, "not_authentik")
        if self.settings.device_unlock_require_mfa and not session.mfa:
            return Eligibility(False, "mfa_required")
        started = session.idp_auth_time or session.created_at
        if datetime.now(UTC) - started > timedelta(hours=self.settings.device_unlock_max_auth_age_hours):
            return Eligibility(False, "too_old")
        return Eligibility(True)

    async def _require(self, p: Principal, action: str, target: VaultDeviceUnlock | None = None) -> None:
        e = await self.eligibility(p)
        if e.ok:
            return
        await self.audit.record(
            action,
            p.actor,
            outcome="denied",
            target_type="vault_device_unlock",
            target_id=target.id if target else None,
            details={"reason": e.reason, "device": target.name if target else None},
        )
        await self.db.commit()
        raise PermissionDeniedError(e.message)

    # ------------------------------------------------------------------ lezen

    async def devices(self, user_id: uuid.UUID) -> list[VaultDeviceUnlock]:
        rows = await self.db.scalars(
            select(VaultDeviceUnlock)
            .where(VaultDeviceUnlock.user_id == user_id, VaultDeviceUnlock.revoked_at.is_(None))
            .order_by(VaultDeviceUnlock.created_at.desc())
        )
        return list(rows)

    async def _own(self, p: Principal, unlock_id: uuid.UUID) -> VaultDeviceUnlock:
        row = await self.db.get(VaultDeviceUnlock, unlock_id)
        if row is None or row.user_id != p.user.id:
            raise NotFoundError("Apparaat niet gevonden")
        return row

    async def get(self, p: Principal, unlock_id: uuid.UUID) -> VaultDeviceUnlock:
        return await self._own(p, unlock_id)

    # ------------------------------------------------------------------ wijzigen

    async def register(self, p: Principal, device_identifier: str, name: str, blob: str) -> VaultDeviceUnlock:
        if not DEVICE_ID_RE.match(device_identifier):
            raise InvalidOperationError("Ongeldige apparaat-id")
        if not ENC_STRING_RE.match(blob):
            raise InvalidOperationError("De sleutel moet een versleutelde EncString (type 2) zijn")
        await self._require(p, "vault.device_unlock.register")
        now = datetime.now(UTC)
        # Opnieuw registreren op hetzelfde apparaat vervangt de oude blob.
        await self.db.execute(
            update(VaultDeviceUnlock)
            .where(
                VaultDeviceUnlock.user_id == p.user.id,
                VaultDeviceUnlock.device_identifier == device_identifier,
                VaultDeviceUnlock.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoked_reason="replaced")
        )
        row = VaultDeviceUnlock(
            user_id=p.user.id,
            device_identifier=device_identifier,
            name=name.strip()[:128] or "Apparaat",
            blob=blob,
            created_at=now,
        )
        self.db.add(row)
        await self.db.flush()
        await self.audit.record(
            "vault.device_unlock.register",
            p.actor,
            target_type="vault_device_unlock",
            target_id=row.id,
            details={"device": row.name},
        )
        await self.db.commit()
        return row

    async def open(self, p: Principal, unlock_id: uuid.UUID) -> str:
        row = await self._own(p, unlock_id)
        if row.revoked_at is not None:
            await self.audit.record(
                "vault.device_unlock.open",
                p.actor,
                outcome="denied",
                target_type="vault_device_unlock",
                target_id=row.id,
                details={"reason": "revoked", "device": row.name},
            )
            await self.db.commit()
            raise PermissionDeniedError("Dit apparaat is ingetrokken. Ontgrendel met je master password.")
        await self._require(p, "vault.device_unlock.open", row)
        row.last_used_at = datetime.now(UTC)
        row.last_used_ip = p.meta.ip_address
        await self.audit.record(
            "vault.device_unlock.open",
            p.actor,
            target_type="vault_device_unlock",
            target_id=row.id,
            details={"device": row.name},
        )
        await self.db.commit()
        return row.blob

    async def revoke(self, p: Principal, unlock_id: uuid.UUID) -> None:
        row = await self._own(p, unlock_id)
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            row.revoked_reason = "user"
            await self.audit.record(
                "vault.device_unlock.revoke",
                p.actor,
                target_type="vault_device_unlock",
                target_id=row.id,
                details={"device": row.name},
            )
            await self.db.commit()

    async def set_enabled(self, p: Principal, enabled: bool) -> VaultAccount:
        account = await self._account(p.user.id)
        if account is None:
            raise NotFoundError("Je hebt nog geen kluis")
        if account.device_unlock_enabled != enabled:
            account.device_unlock_enabled = enabled
            revoked = 0 if enabled else await revoke_all(self.db, p.user.id, "disabled")
            await self.audit.record(
                "vault.device_unlock.settings",
                p.actor,
                target_type="vault",
                target_id=p.user.id,
                details={"enabled": enabled, "devices_revoked": revoked},
            )
            await self.db.commit()
        return account


async def revoke_all(db: AsyncSession, user_id: uuid.UUID, reason: str) -> int:
    """Alle apparaten intrekken (zonder commit), bv. na een nieuw master password."""
    result = await db.execute(
        update(VaultDeviceUnlock)
        .where(VaultDeviceUnlock.user_id == user_id, VaultDeviceUnlock.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
        .returning(VaultDeviceUnlock.id)
    )
    return len(result.all())


def device_json(row: VaultDeviceUnlock) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "created_at": row.created_at,
        "last_used_at": row.last_used_at,
        "last_used_ip": row.last_used_ip,
    }
