from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class UserOut(ORMModel):
    id: UUID
    email: str | None
    email_verified: bool
    username: str | None
    display_name: str | None
    is_admin: bool
    # Beheerdersrechten in VaultX toegekend (los van een Authentik-beheerdersgroep).
    admin_granted: bool
    is_active: bool
    idp_groups: list[str]
    last_login_at: datetime | None
    created_at: datetime
    has_password: bool
    has_authentik: bool


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
    has_password: bool | None = None
    has_authentik: bool | None = None


USERNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._@+-]{1,149}$"
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class UserUpdate(BaseModel):
    is_active: bool | None = None
    # Beheerdersrechten toekennen of intrekken in VaultX (een Authentik-groep blijft gelden).
    is_admin: bool | None = None
    # Enkel voor gewone accounts; bij Authentik-accounts komen deze uit Authentik.
    username: str | None = Field(None, pattern=USERNAME_PATTERN)
    email: str | None = Field(None, max_length=320, pattern=EMAIL_PATTERN)
    display_name: str | None = Field(None, max_length=255)


class UserCreate(BaseModel):
    """Gewoon account met een VaultX-wachtwoord, zonder Authentik."""

    username: str = Field(pattern=USERNAME_PATTERN)
    email: str = Field(max_length=320, pattern=EMAIL_PATTERN)
    display_name: str | None = Field(None, max_length=255)
    password: str = Field(max_length=1024)
    is_admin: bool = False


class PasswordSet(BaseModel):
    password: str = Field(max_length=1024)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


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
    oidc_issuer: str | None
    oidc_subject: str | None
    password_changed_at: datetime | None = None
    locked_until: datetime | None = None
    memberships: list[MembershipBrief] = []


class SessionOut(ORMModel):
    id: UUID
    ip_address: str | None
    user_agent: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    auth_method: str
    current: bool = False


class MeOut(UserOut):
    memberships: list[MembershipBrief]
    auth_method: str
