import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid, utcnow


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
    # Fase 3: mag VaultX naar deze NPM schrijven (Authentik-bescherming zetten)? Standaard niet.
    write_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Hoe nginx in NPM de Authentik-outpost bereikt, bv. http://authentik-server:9000.
    authentik_outpost_url: Mapped[str | None] = mapped_column(String(2048))
    # Waar VaultX de proxy hosts zelf aanspreekt om na een wijziging te controleren of ze werken.
    # Leeg = de host uit base_url.
    probe_host: Mapped[str | None] = mapped_column(String(255))
    probe_http_port: Mapped[int] = mapped_column(Integer, default=80, server_default="80")
    probe_https_port: Mapped[int] = mapped_column(Integer, default=443, server_default="443")

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
    # Authentik-config staat er door VaultX op (fase 3, zie services/npm_protect.py).
    vaultx_managed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
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


class NpmChangeStatus(enum.StrEnum):
    running = "running"  # bezig (of de node viel weg tijdens de wijziging, zie interrupted)
    applied = "applied"  # gewijzigd en gecontroleerd
    rolled_back = "rolled_back"  # controle faalde, oude config teruggezet
    rollback_failed = "rollback_failed"  # terugzetten lukte niet: handwerk nodig, zie before
    refused = "refused"  # niet uitgevoerd (controle vooraf faalde of host intussen gewijzigd)
    interrupted = "interrupted"  # bleef op running staan; status in NPM onbekend


class NpmChange(Base):
    """Journaal van elke schrijfactie naar NPM, met de config van voor en na.

    `before` is de momentopname waarmee VaultX terugzet; bij rollback_failed is
    dat ook wat je met de hand terugzet. Eén lopende wijziging per host tegelijk
    (unieke index), ook over meerdere VaultX-nodes heen.
    """

    __tablename__ = "npm_changes"
    __table_args__ = (
        CheckConstraint("action IN ('protect','unprotect')", name="action_valid"),
        CheckConstraint(
            "status IN ('running','applied','rolled_back','rollback_failed','refused','interrupted')",
            name="status_valid",
        ),
        Index(
            "uq_npm_changes_running_host",
            "connection_id",
            "npm_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("npm_connections.id", ondelete="CASCADE"), index=True
    )
    host_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("npm_hosts.id", ondelete="SET NULL"), index=True
    )
    npm_id: Mapped[int] = mapped_column(Integer)
    domain: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    actor_label: Mapped[str | None] = mapped_column(String(320))
    # {"advanced_config": ..., "locations": [...]}, zoals NPM ze teruggaf / zoals VaultX ze schreef.
    before: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    probe_before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    probe_after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    message: Mapped[str | None] = mapped_column(Text)
    nginx_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
