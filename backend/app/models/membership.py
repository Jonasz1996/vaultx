import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, new_uuid, utcnow


class OrgRole(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    member = "member"


class TeamRole(enum.StrEnum):
    maintainer = "maintainer"
    member = "member"


class MembershipSource(enum.StrEnum):
    manual = "manual"  # aangemaakt via UI/API
    idp = "idp"  # afgeleid van Authentik-groepen, wordt bij elke login gesynchroniseerd


class Membership(Base):
    """Lidmaatschap van een gebruiker.

    team_id NULL  -> lidmaatschap van de organisatie (rol: owner/admin/member)
    team_id gezet -> lidmaatschap van een team binnen die organisatie (rol: maintainer/member)
    """

    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint(
            "(team_id IS NULL AND role IN ('owner','admin','member'))"
            " OR (team_id IS NOT NULL AND role IN ('maintainer','member'))",
            name="role_matches_scope",
        ),
        CheckConstraint("source IN ('manual','idp')", name="source_valid"),
        # Een teamlidmaatschap kan enkel naar een team van dezelfde organisatie wijzen.
        ForeignKeyConstraint(
            ["team_id", "organization_id"],
            ["teams.id", "teams.organization_id"],
            ondelete="CASCADE",
            name="fk_memberships_team_org",
        ),
        Index(
            "uq_memberships_org_user",
            "organization_id",
            "user_id",
            unique=True,
            postgresql_where=text("team_id IS NULL"),
        ),
        Index(
            "uq_memberships_team_user",
            "team_id",
            "user_id",
            unique=True,
            postgresql_where=text("team_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    role: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16), default=MembershipSource.manual.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow
    )

    user = relationship("User", back_populates="memberships")
    organization = relationship("Organization")
    team = relationship(
        "Team",
        primaryjoin="Membership.team_id == Team.id",
        foreign_keys="Membership.team_id",
        viewonly=True,
    )
