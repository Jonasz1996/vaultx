"""Auditlog met hashketen per tenant (zie KB-26 in het ontwerpdossier).

Elke regel bevat de hash van de vorige regel in dezelfde keten. Wijzigen of
weglaten van een regel in het midden breekt de keten en wordt door verify()
gedetecteerd. Afkappen van het einde van een keten detecteer je pas met
externe, ondertekende checkpoints; die staan gepland na phase-0.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.models import AuditLog
from app.repositories import AuditRepository
from app.services.principal import Actor

GENESIS_HASH = "0" * 64


def _chain_lock_key(organization_id: UUID | None) -> int:
    digest = hashlib.sha256(f"vaultx-audit:{organization_id or 'instance'}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _json_safe(details: dict[str, Any] | None) -> dict[str, Any]:
    return json.loads(json.dumps(details or {}, default=str))


def _canonical_ts(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def compute_hash(prev_hash: str, entry: AuditLog) -> str:
    payload = {
        "occurred_at": _canonical_ts(entry.occurred_at),
        "organization_id": str(entry.organization_id) if entry.organization_id else None,
        "actor_user_id": str(entry.actor_user_id) if entry.actor_user_id else None,
        "actor_type": entry.actor_type,
        "actor_label": entry.actor_label,
        "action": entry.action,
        "outcome": entry.outcome,
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "ip_address": entry.ip_address,
        "user_agent": entry.user_agent,
        "request_id": entry.request_id,
        "details": entry.details,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{prev_hash}\n{canonical}".encode()).hexdigest()


@dataclass(slots=True)
class VerifyResult:
    organization_id: UUID | None
    entries_checked: int
    valid: bool
    broken_at_id: int | None = None
    reason: str | None = None
    head_hash: str | None = None


class AuditService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = AuditRepository(db)

    async def record(
        self,
        action: str,
        actor: Actor,
        *,
        outcome: str = "success",
        organization_id: UUID | None = None,
        target_type: str | None = None,
        target_id: UUID | str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Voegt een regel toe binnen de lopende transactie (commit doet de caller)."""
        await self.repo.lock_chain(_chain_lock_key(organization_id))
        prev_hash = await self.repo.last_hash(organization_id) or GENESIS_HASH
        ua = actor.meta.user_agent
        entry = AuditLog(
            occurred_at=datetime.now(UTC),
            organization_id=organization_id,
            actor_user_id=actor.user_id,
            actor_type=actor.type,
            actor_label=actor.label,
            action=action,
            outcome=outcome,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            ip_address=actor.meta.ip_address,
            user_agent=ua[:512] if ua else None,
            request_id=actor.meta.request_id,
            details=_json_safe(details),
            prev_hash=prev_hash,
        )
        entry.hash = compute_hash(prev_hash, entry)
        self.repo.add(entry)
        await self.db.flush()
        return entry

    async def deny(
        self,
        action: str,
        actor: Actor,
        *,
        organization_id: UUID | None = None,
        target_type: str | None = None,
        target_id: UUID | str | None = None,
        message: str = "Onvoldoende rechten",
    ) -> PermissionDeniedError:
        """Legt een geweigerde actie vast en commit meteen; geeft de fout terug om te raisen.

        Wordt aangeroepen vóór er iets gewijzigd is, dus de commit bevat enkel de auditregel.
        """
        await self.record(
            action,
            actor,
            outcome="denied",
            organization_id=organization_id,
            target_type=target_type,
            target_id=target_id,
        )
        await self.db.commit()
        return PermissionDeniedError(message)

    async def verify(self, organization_id: UUID | None, batch: int = 1000) -> VerifyResult:
        prev = GENESIS_HASH
        checked = 0
        last_id = 0
        while True:
            rows = await self.repo.chain(organization_id, after_id=last_id, batch=batch)
            if not rows:
                break
            for entry in rows:
                checked += 1
                if entry.prev_hash != prev:
                    return VerifyResult(organization_id, checked, False, entry.id, "prev_hash past niet")
                if compute_hash(prev, entry) != entry.hash:
                    return VerifyResult(organization_id, checked, False, entry.id, "inhoud gewijzigd")
                prev = entry.hash
                last_id = entry.id
        return VerifyResult(organization_id, checked, True, head_hash=None if checked == 0 else prev)
