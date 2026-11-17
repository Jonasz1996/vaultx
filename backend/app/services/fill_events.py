"""Wat VaultX Invuller invulde: per item en host, de laatste 30 dagen (M4, inzicht).

De extensie meldt enkel het id van het item, de host van de pagina en of het invullen lukte of
de login daarna mislukte. Nooit inhoud: de kluis is end-to-end versleuteld en blijft dat.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import VaultCipher, VaultFillEvent
from app.models.base import utcnow
from app.services.vault_auth import VaultCaller

KEEP_DAYS = 30
KINDS = {"fill", "failed"}
# host of host:poort, zoals URL.host in de browser (kleine letters, IDN als punycode).
HOST_RE = re.compile(r"^(?:[a-z0-9-]{1,63}\.)*[a-z0-9-]{1,63}(?::\d{1,5})?$|^\[[0-9a-f:.]+\](?::\d{1,5})?$")


def valid_host(host: str) -> bool:
    return len(host) <= 261 and bool(HOST_RE.match(host))


class FillEventService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def record(self, c: VaultCaller, events: list[dict[str, Any]]) -> int:
        """Bewaart de gemelde invullingen. Onbekende items (niet van deze gebruiker) vallen weg."""
        uid = c.user.id
        ids = {e["item_id"] for e in events}
        own = (
            set(
                await self.db.scalars(
                    select(VaultCipher.id).where(VaultCipher.user_id == uid, VaultCipher.id.in_(ids))
                )
            )
            if ids
            else set()
        )
        now = utcnow()
        oldest = now - timedelta(days=KEEP_DAYS)
        rows = []
        for e in events:
            at = e.get("at") or now
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            if e["item_id"] not in own or at < oldest:
                continue
            rows.append(
                VaultFillEvent(
                    user_id=uid,
                    cipher_id=e["item_id"],
                    host=e["host"],
                    kind=e["kind"],
                    occurred_at=min(at, now),
                    device_id=c.device.id,
                )
            )
        self.db.add_all(rows)
        await self.db.execute(
            delete(VaultFillEvent).where(VaultFillEvent.user_id == uid, VaultFillEvent.occurred_at < oldest)
        )
        await self.db.commit()
        return len(rows)

    async def summary(self, user_id: uuid.UUID) -> dict[str, Any]:
        """Per item en host: aantal invullingen en mislukte logins, de laatste 30 dagen."""
        since = utcnow() - timedelta(days=KEEP_DAYS)
        rows = await self.db.execute(
            select(
                VaultFillEvent.cipher_id,
                VaultFillEvent.host,
                func.count().filter(VaultFillEvent.kind == "fill"),
                func.count().filter(VaultFillEvent.kind == "failed"),
                func.max(VaultFillEvent.occurred_at).filter(VaultFillEvent.kind == "fill"),
                func.max(VaultFillEvent.occurred_at).filter(VaultFillEvent.kind == "failed"),
            )
            .where(VaultFillEvent.user_id == user_id, VaultFillEvent.occurred_at >= since)
            .group_by(VaultFillEvent.cipher_id, VaultFillEvent.host)
        )
        items = [
            {
                "item_id": str(cid),
                "host": host,
                "fills": fills,
                "failures": failures,
                "last_fill_at": last_fill,
                "last_failure_at": last_failure,
            }
            for cid, host, fills, failures, last_fill, last_failure in rows
        ]
        items.sort(key=lambda r: (-(r["failures"] > 0), -r["fills"], r["host"]))
        return {
            "days": KEEP_DAYS,
            "fills": sum(r["fills"] for r in items),
            "failures": sum(r["failures"] for r in items),
            "items": items,
        }
