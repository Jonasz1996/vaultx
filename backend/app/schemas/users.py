from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import ORMModel


class UserOut(ORMModel):
    id: UUID
    email: str | None
    email_verified: bool
    username: str | None
    display_name: str | None
    is_admin: bool
    is_active: bool
    idp_groups: list[str]
    last_login_at: datetime | None
    created_at: datetime


class UserSummary(ORMModel):
    id: UUID
    email: str | None
    username: str | None
    display_name: str | None
    is_active: bool


class UserListItem(ORMModel):
    """Beheerders zien alle velden; organisatiebeheerders enkel de samenvatting."""

    id: UUID
    email: str | None
    username: str | None
    display_name: str | None
    is_active: bool
    email_verified: bool | None = None
    is_admin: bool | None = None
    idp_groups: list[str] | None = None
    last_login_at: datetime | None = None
    created_at: datetime | None = None


class UserUpdate(BaseModel):
    is_active: bool | None = None


class MembershipBrief(BaseModel):
    id: UUID
    organization_id: UUID
    organization_slug: str
    organization_name: str
    team_id: UUID | None
    team_slug: str | None
    team_name: str | None
    role: str
    source: str


class UserDetail(UserOut):
    oidc_issuer: str
    oidc_subject: str
    memberships: list[MembershipBrief] = []


class SessionOut(ORMModel):
    id: UUID
    ip_address: str | None
    user_agent: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    current: bool = False


class MeOut(UserOut):
    memberships: list[MembershipBrief]
    auth_method: str
