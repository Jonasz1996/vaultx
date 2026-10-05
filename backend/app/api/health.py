from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.api.deps import DbSession

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness")
async def health() -> dict:
    return {"status": "ok", "service": "vaultx", "version": __version__}


@router.get("/health/ready", summary="Readiness (database bereikbaar)")
async def ready(db: DbSession) -> JSONResponse:
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "up"})
