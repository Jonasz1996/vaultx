from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import ORMModel


class AuditLogOut(ORMModel):
    id: int
    occurred_at: datetime
    organization_id: UUID | None
    actor_user_id: UUID | None
    actor_type: str
    actor_label: str | None
    action: str
    outcome: str
    target_type: str | None
    target_id: str | None
    ip_address: str | None
    user_agent: str | None
    request_id: str | None
    details: dict[str, Any]
    prev_hash: str
    hash: str


class AuditPage(BaseModel):
    items: list[AuditLogOut]
    next_before_id: int | None


class AuditVerifyOut(BaseModel):
    organization_id: UUID | None
    entries_checked: int
    valid: bool
    broken_at_id: int | None
    reason: str | None
    head_hash: str | None


class DashboardOut(BaseModel):
    users_total: int
    users_active: int
    admins: int
    organizations: int
    teams: int
    active_sessions: int
    logins_24h: int
    denied_24h: int
    recent_events: list[AuditLogOut]
