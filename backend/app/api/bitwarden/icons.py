"""/icons/{domein}/icon.png: websitepictogrammen naast de items in de Bitwarden-apps."""

from __future__ import annotations

import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.api.deps import DbSession, SettingsDep
from app.models import Application, DiscoveredHost
from app.services.icons import IconCache, normalize_domain

router = APIRouter(prefix="/icons", tags=["bitwarden-icons"])

ICON_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}
KNOWN_TTL = 60.0


async def known_domains(request: Request, db: DbSession) -> set[str]:
    """Domeinen van je eigen apps (NPM-hosts en catalogus): die mogen een privé-adres hebben."""
    state = request.app.state
    cached = getattr(state, "icon_known_domains", None)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]
    domains: set[str] = set()
    for names in await db.scalars(
        select(DiscoveredHost.domain_names).where(DiscoveredHost.removed_at.is_(None))
    ):
        domains.update(str(n).lower() for n in names or [])
    for url in await db.scalars(select(Application.url).where(Application.url.is_not(None))):
        host = urlsplit(url or "").hostname
        if host:
            domains.add(host.lower())
    state.icon_known_domains = (time.monotonic() + KNOWN_TTL, domains)
    return domains


def _cache(request: Request) -> IconCache:
    state = request.app.state
    if not hasattr(state, "icon_cache"):
        fetcher = getattr(state, "icon_fetcher", None)  # tests: nep-fetcher
        state.icon_cache = IconCache(fetcher) if fetcher else IconCache()
    return state.icon_cache


@router.get("/{domain}/icon.png", summary="Pictogram van een website (voor de Bitwarden-apps)")
async def icon(domain: str, request: Request, db: DbSession, settings: SettingsDep) -> Response:
    name = normalize_domain(domain)
    if not settings.vault_enabled or not settings.vault_icons or name is None:
        return Response(status_code=404, headers={"Cache-Control": "public, max-age=86400"})
    known = await known_domains(request, db)
    found = await _cache(request).get(name, lambda host: host.lower() in known)
    if found is None:
        return Response(status_code=404, headers={"Cache-Control": "public, max-age=86400", **ICON_HEADERS})
    return Response(
        found.data,
        media_type=found.content_type,
        headers={"Cache-Control": "public, max-age=604800", **ICON_HEADERS},
    )
