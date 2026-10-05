"""Persoonlijke kluis, compatibel met de officiële Bitwarden-clients (fase 2).

De server ziet nooit een master password of een sleutel in klare tekst. Alles
wat de clients versleutelen (namen, gebruikersnamen, wachtwoorden, notities,
de user key zelf) bewaart VaultX als ondoorzichtige EncStrings.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid, utcnow


class KdfType(enum.IntEnum):
    pbkdf2_sha256 = 0
    argon2id = 1


class VaultAccount(TimestampMixin, Base):
    """Kluisaccount van één VaultX-gebruiker (1:1, zelfde id als users.id).

    Bitwarden-clients loggen in met e-mail + master password. Het e-mailadres is
    dat van de gebruiker in Authentik op het moment van activeren; het blijft
    vast omdat het ook de KDF-salt is (zie kdf_salt).
    """

    __tablename__ = "vault_accounts"
    __table_args__ = (
        CheckConstraint("kdf_type IN (0, 1)", name="kdf_type_valid"),
        UniqueConstraint("email", name="uq_vault_accounts_email"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Kleine letters, zonder spaties: de loginnaam voor Bitwarden-clients.
    email: Mapped[str] = mapped_column(String(320))
    # Salt voor de client-side KDF. Vandaag gelijk aan het e-mailadres, maar apart
    # bewaard omdat Bitwarden de salt loskoppelt van het e-mailadres.
    kdf_salt: Mapped[str] = mapped_column(String(320))
    kdf_type: Mapped[int] = mapped_column(Integer)
    kdf_iterations: Mapped[int] = mapped_column(Integer)
    kdf_memory: Mapped[int | None] = mapped_column(Integer)
    kdf_parallelism: Mapped[int | None] = mapped_column(Integer)
    # Server-side hash (PBKDF2-SHA256 met eigen salt) van de master password hash
    # die de client stuurt. Formaat: zie services/vault_auth.py.
    master_password_hash: Mapped[str] = mapped_column(String(255))
    # User key, versleuteld met de master key (EncString).
    user_key: Mapped[str] = mapped_column(Text)
    public_key: Mapped[str] = mapped_column(Text)
    # Private key, versleuteld met de user key (EncString).
    private_key: Mapped[str] = mapped_column(Text)
    # Bitwarden-v2-velden (signing key, signed public key, security state) als
    # ondoorzichtige blob, zodat een latere v2-upgrade geen schemawijziging vraagt.
    account_keys_v2: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Id van de user key zoals de client hem meldt (POST /api/accounts/key-management/user-key-id).
    user_key_id: Mapped[str | None] = mapped_column(String(64))
    # Verandert bij elk event dat bestaande tokens ongeldig moet maken.
    security_stamp: Mapped[str] = mapped_column(String(64))
    # Laatste wijziging aan de kluis; clients doen een volledige sync als die nieuwer is.
    revision_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Bescherming tegen wachtwoord raden via het token-endpoint.
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user = relationship("User")


class VaultDevice(Base):
    """Een Bitwarden-client (CLI, extensie, app) die is ingelogd op een kluis."""

    __tablename__ = "vault_devices"
    __table_args__ = (UniqueConstraint("user_id", "identifier", name="uq_vault_devices_user_identifier"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vault_accounts.user_id", ondelete="CASCADE"), index=True
    )
    # Door de client gekozen apparaat-id (deviceIdentifier).
    identifier: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    # Bitwarden DeviceType (bv. 8 = Chrome-extensie, 23 = Linux CLI).
    type: Mapped[int] = mapped_column(Integer)
    # SHA-256 van het refresh token (zoals bij browsersessies).
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VaultFolder(Base):
    __tablename__ = "vault_folders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vault_accounts.user_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)  # EncString
    revision_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VaultCipher(Base):
    """Een kluisitem (login, notitie, kaart, ...).

    `data` bevat het versleutelde, type-specifieke deel zoals de client het
    stuurt (name, notes, login, fields, passwordHistory, key, ...). VaultX
    interpreteert die velden niet; alleen de velden die de server zelf beheert
    (map, favoriet, prullenbak, revisiedatum) zijn kolommen.
    """

    __tablename__ = "vault_ciphers"
    __table_args__ = (CheckConstraint("type BETWEEN 1 AND 99", name="type_valid"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vault_accounts.user_id", ondelete="CASCADE"), index=True
    )
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vault_folders.id", ondelete="SET NULL")
    )
    type: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    favorite: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revision_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = ["KdfType", "VaultAccount", "VaultCipher", "VaultDevice", "VaultFolder"]
