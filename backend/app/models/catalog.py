import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid


class AuthMethod(enum.StrEnum):
    """Hoe een applicatie haar gebruikers aanmeldt."""

    forward_auth = "forward_auth"  # Authentik-outpost via auth_request in NPM
    oidc = "oidc"
    saml = "saml"
    header = "header"  # app vertrouwt identiteitsheaders van de proxy
    access_list = "access_list"  # NPM access list (IP en/of basic auth)
    app = "app"  # eigen login van de applicatie, los van Authentik
    none = "none"  # bewust open
    unknown = "unknown"


class AppSource(enum.StrEnum):
    npm = "npm"  # aangemaakt door een NPM-sync
    manual = "manual"


class NpmConnection(TimestampMixin, Base):
    """Koppeling van een organisatie met één Nginx Proxy Manager-instantie.

    Het wachtwoord van het NPM-account staat versleuteld in secret_ciphertext
    (AES-GCM, sleutel afgeleid van VAULTX_SECRET_KEY) en verlaat de backend nooit.
    """

    __tablename__ = "npm_connections"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_npm_connections_organization_name"),
        CheckConstraint(
            "last_sync_status IS NULL OR last_sync_status IN ('ok','error')", name="sync_status_valid"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    base_url: Mapped[str] = mapped_column(String(2048))
    identity: Mapped[str] = mapped_column(String(320))
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    npm_version: Mapped[str | None] = mapped_column(String(32))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_status: Mapped[str | None] = mapped_column(String(16))
    last_sync_error: Mapped[str | None] = mapped_column(Text)

    hosts = relationship(
        "DiscoveredHost", back_populates="connection", cascade="all, delete-orphan", passive_deletes=True
    )


class Application(TimestampMixin, Base):
    """Item in de applicatiecatalogus van een organisatie.

    Een NPM-sync maakt applicaties aan voor nieuwe proxy hosts en houdt ze bij
    zolang auto_update aan staat. Wie een applicatie met de hand aanpast, zet
    auto_update uit: de sync overschrijft die keuzes dan niet meer.
    """

    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint(
            "auth_method IN ('forward_auth','oidc','saml','header','access_list','app','none','unknown')",
            name="auth_method_valid",
        ),
        CheckConstraint("source IN ('npm','manual')", name="source_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    app_type: Mapped[str | None] = mapped_column(String(64))  # sleutel uit de fingerprintlijst
    url: Mapped[str | None] = mapped_column(String(2048))
    description: Mapped[str | None] = mapped_column(Text)
    auth_method: Mapped[str] = mapped_column(String(32), default=AuthMethod.unknown.value)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    source: Mapped[str] = mapped_column(String(16), default=AppSource.manual.value)
    auto_update: Mapped[bool] = mapped_column(Boolean, default=False)

    hosts = relationship("DiscoveredHost", back_populates="application")


class DiscoveredHost(Base):
    """Proxy host zoals NPM hem bij de laatste sync teruggaf.

    Feiten uit NPM, bij elke sync overschreven. removed_at is gezet zodra de
    host niet meer in NPM staat; de rij blijft bestaan voor de historiek.
    """

    __tablename__ = "npm_hosts"
    __table_args__ = (UniqueConstraint("connection_id", "npm_id", name="uq_npm_hosts_connection_npm_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("npm_connections.id", ondelete="CASCADE"), index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id", ondelete="SET NULL"), index=True
    )
    npm_id: Mapped[int] = mapped_column(Integer)
    domain_names: Mapped[list[str]] = mapped_column(JSONB, default=list)
    forward_scheme: Mapped[str] = mapped_column(String(8))
    forward_host: Mapped[str] = mapped_column(String(255))
    forward_port: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean)
    nginx_online: Mapped[bool] = mapped_column(Boolean, default=True)
    ssl: Mapped[bool] = mapped_column(Boolean, default=False)
    ssl_forced: Mapped[bool] = mapped_column(Boolean, default=False)
    access_list: Mapped[str | None] = mapped_column(String(255))
    forward_auth: Mapped[bool] = mapped_column(Boolean, default=False)
    detected_app_type: Mapped[str | None] = mapped_column(String(64))
    detected_auth: Mapped[str] = mapped_column(String(32))
    labels: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    # Lijst van {"code": ..., "message": ...}, zie services/npm_detect.py.
    warnings: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    # Door de gebruiker genegeerd: krijgt geen catalogusitem (label vaultx.ignore werkt ook).
    ignored: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    connection = relationship("NpmConnection", back_populates="hosts")
    application = relationship("Application", back_populates="hosts")

    @property
    def primary_domain(self) -> str:
        return self.domain_names[0] if self.domain_names else ""
