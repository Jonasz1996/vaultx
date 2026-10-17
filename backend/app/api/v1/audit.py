from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.audit import AuditLogOut, AuditPage, AuditVerifyOut, DashboardOut
from app.services.audit_query import AuditQueryService

router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=AuditPage, summary="Auditlog doorzoeken (nieuwste eerst)")
async def search_audit(
    p: CurrentPrincipal,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before_id: Annotated[int | None, Query(description="Cursor: geef regels met id kleiner dan dit")] = None,
    organization_id: UUID | None = None,
    instance_only: Annotated[bool, Query(description="Enkel de instantieketen (geen organisatie)")] = False,
    actor_user_id: UUID | None = None,
    action: Annotated[
        str | None, Query(max_length=64, description="Prefix, bv. 'auth.' of 'membership.'")
    ] = None,
    outcome: Literal["success", "failure", "denied"] | None = None,
    target_id: Annotated[str | None, Query(max_length=64, description="Bv. de id van een applicatie")] = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> AuditPage:
    rows = await AuditQueryService(db).search(
        p,
        limit=limit,
        before_id=before_id,
        organization_id=organization_id,
        instance_only=instance_only,
        actor_user_id=actor_user_id,
        action=action,
        outcome=outcome,
        target_id=target_id,
        since=since,
        until=until,
    )
    return AuditPage(
        items=[AuditLogOut.model_validate(r) for r in rows],
        next_before_id=rows[-1].id if len(rows) == limit else None,
    )


@router.get("/audit/verify", response_model=AuditVerifyOut, summary="Hashketen controleren")
async def verify_audit(
    p: CurrentPrincipal, db: DbSession, organization_id: UUID | None = None
) -> AuditVerifyOut:
    result = await AuditQueryService(db).verify(p, organization_id)
    return AuditVerifyOut(
        organization_id=result.organization_id,
        entries_checked=result.entries_checked,
        valid=result.valid,
        broken_at_id=result.broken_at_id,
        reason=result.reason,
        head_hash=result.head_hash,
    )


@router.get("/dashboard", response_model=DashboardOut, summary="Kerncijfers voor het dashboard")
async def dashboard(p: CurrentPrincipal, db: DbSession) -> DashboardOut:
    return await AuditQueryService(db).dashboard(p)
