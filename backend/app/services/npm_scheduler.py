"""Periodieke sync van alle actieve NPM-koppelingen, als achtergrondtaak in de API-proces.

Draaien er meerdere VaultX-nodes, dan doet elke node dit. Dat is veilig: een
advisory lock per koppeling serialiseert de syncs, en een koppeling die net
gesynchroniseerd is, wordt overgeslagen.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.core.db import get_sessionmaker
from app.core.errors import VaultXError
from app.repositories import NpmConnectionRepository
from app.services.npm import NpmService
from app.services.principal import Actor

log = logging.getLogger(__name__)


async def sync_due_connections(settings: Settings) -> int:
    """Synchroniseert elke actieve koppeling waarvan de laatste sync ouder is dan het interval."""
    interval = timedelta(minutes=settings.npm_sync_interval_minutes)
    cutoff = datetime.now(UTC) - interval * 0.9
    async with get_sessionmaker()() as db:
        due = [
            c.id
            for c in await NpmConnectionRepository(db).list_enabled()
            if c.last_sync_at is None or c.last_sync_at < cutoff
        ]
    done = 0
    for conn_id in due:
        async with get_sessionmaker()() as db:
            try:
                await NpmService(db, settings).sync(conn_id, Actor.system("npm-sync"))
                done += 1
            except VaultXError as exc:
                log.warning("NPM-sync %s mislukt: %s", conn_id, exc.message)
            except Exception:
                log.exception("NPM-sync %s mislukt", conn_id)
    return done


async def run_scheduler(settings: Settings) -> None:
    period = settings.npm_sync_interval_minutes * 60
    await asyncio.sleep(min(30, period))  # laat de app eerst opstarten
    while True:
        try:
            await sync_due_connections(settings)
        except Exception:
            log.exception("NPM-scheduler: ronde mislukt")
        await asyncio.sleep(period)


def start_scheduler(settings: Settings) -> asyncio.Task | None:
    if settings.npm_sync_interval_minutes <= 0:
        return None
    return asyncio.create_task(run_scheduler(settings), name="npm-sync-scheduler")


async def stop_scheduler(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
