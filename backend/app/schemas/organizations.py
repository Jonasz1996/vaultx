from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import SLUG_PATTERN, ORMModel
from app.schemas.users import UserSummary


class OrganizationCreate(BaseModel):
    slug: str = Field(pattern=SLUG_PATTERN, max_length=63)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(None, max_length=4000)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=4000)


class OrganizationOut(ORMModel):
    id: UUID
    slug: str
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    member_count: int = 0
    team_count: int = 0
    my_role: str | None = None


class TeamCreate(BaseModel):
    slug: str = Field(pattern=SLUG_PATTERN, max_length=63)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(None, max_length=4000)


class TeamUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=4000)


class TeamOut(ORMModel):
    id: UUID
    organization_id: UUID
    slug: str
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    member_count: int = 0


OrgRoleLiteral = Literal["owner", "admin", "member"]
TeamRoleLiteral = Literal["maintainer", "member"]


class OrgMemberCreate(BaseModel):
    user_id: UUID
    role: OrgRoleLiteral = "member"


class OrgMemberUpdate(BaseModel):
    role: OrgRoleLiteral


class TeamMemberCreate(BaseModel):
    user_id: UUID
    role: TeamRoleLiteral = "member"


class TeamMemberUpdate(BaseModel):
    role: TeamRoleLiteral


class MemberOut(ORMModel):
    id: UUID
    organization_id: UUID
    team_id: UUID | None
    role: str
    source: str
    created_at: datetime
    user: UserSummary
