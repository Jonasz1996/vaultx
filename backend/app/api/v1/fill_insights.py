"""Inzicht in VaultX Invuller op Mijn kluis: wat de extensie de laatste 30 dagen invulde."""

from datetime import datetime

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.api.deps import CurrentPrincipal, DbSession
from app.services.fill_events import FillEventService

router = APIRouter(prefix="/vault/fill-insights", tags=["vault"])


class FillItem(BaseModel):
    item_id: str
    host: str
    fills: int
    failures: int
    last_fill_at: datetime | None
    last_failure_at: datetime | None


class FillInsights(BaseModel):
    days: int
    fills: int
    failures: int
    items: list[FillItem]


@router.get("", response_model=FillInsights, summary="Ingevulde items en mislukte logins (30 dagen)")
async def fill_insights(p: CurrentPrincipal, db: DbSession, response: Response) -> FillInsights:
    response.headers["Cache-Control"] = "no-store"
    return FillInsights(**await FillEventService(db).summary(p.user.id))
