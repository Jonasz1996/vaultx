import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid


class User(TimestampMixin, Base):
    """Een persoon in VaultX. Inloggen kan via Authentik (identiteit = issuer + subject), met een
    eigen VaultX-wachtwoord (password_hash), of allebei. Een gewoon account heeft geen issuer/subject.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("oidc_issuer", "oidc_subject", name="uq_users_oidc_identity"),
        # Wachtwoordlogin zoekt hoofdletterongevoelig op gebruikersnaam of e-mail.
        Index("ix_users_lower_username", text("lower(username)")),
        Index("ix_users_lower_email", text("lower(email)")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    oidc_issuer: Mapped[str | None] = mapped_column(String(512))
    oidc_subject: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    username: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    # Effectieve instantiebeheerder: via een Authentik-beheerdersgroep of door een beheerder in
    # VaultX toegekend (admin_granted). Zie IdentityService.effective_admin.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    admin_granted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Laatst ontvangen groepen uit de IdP, ter info en voor de admin UI.
    idp_groups: Mapped[list[str]] = mapped_column(JSONB, default=list)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Gewone login (fase 8): Argon2id-hash van het VaultX-wachtwoord, NULL = geen wachtwoordlogin.
    password_hash: Mapped[str | None] = mapped_column(Text)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Te veel foute wachtwoorden na elkaar: tijdelijk geen wachtwoordlogin.
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships = relationship("Membership", back_populates="user", cascade="all, delete-orphan")

    @property
    def has_password(self) -> bool:
        return self.password_hash is not None

    @property
    def has_authentik(self) -> bool:
        return self.oidc_issuer is not None
