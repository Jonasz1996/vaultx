"""Bitwarden-compatibele API (fase 2): /identity en /api zoals de officiële clients ze aanroepen.

Geen code van Bitwarden of Vaultwarden: dit is een eigen implementatie van het
protocol, afgeleid uit het gedrag en de publieke broncode van de clients.
"""

from fastapi import APIRouter

from app.api.bitwarden import core, identity

router = APIRouter()
router.include_router(identity.router)
router.include_router(core.router)
