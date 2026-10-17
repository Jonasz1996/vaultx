"""Notities (fase 7): een Markdown-kennisbank in de persoonlijke kluis.

Net als de kluisitems zijn notities end-to-end versleuteld. De browser zet titel,
map, tekst en vlaggen samen in één JSON-document en versleutelt dat met de user
key (EncString type 2). De server ziet alleen een ondoorzichtige blob, een
revisienummer en tijdstempels; links, tags en zoeken rekent de browser uit.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.models.base import Base, new_uuid, utcnow


class VaultNote(Base):
    __tablename__ = "vault_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vault_accounts.user_id", ondelete="CASCADE"), index=True
    )
    # EncString van {"v": 1, "title", "folder", "body", "pinned"}.
    content: Mapped[str] = mapped_column(Text)
    # Stijgt bij elke wijziging; een client die met een oudere revisie schrijft, krijgt 409.
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VaultNoteVersion(Base):
    """Eerdere versie van een notitie (versiegeschiedenis), even versleuteld als de notitie."""

    __tablename__ = "vault_note_versions"
    __table_args__ = (UniqueConstraint("note_id", "revision", name="uq_vault_note_versions_note_revision"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vault_notes.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    # Moment waarop deze versie de actuele was (de updated_at van toen).
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VaultNoteFile(Base):
    """Bijlage bij notities (afbeelding, pdf, ...), versleuteld in de browser.

    `data` is een EncArrayBuffer zoals Bitwarden die voor bijlagen gebruikt: één byte
    type (2), IV (16), MAC (32) en de AES-256-CBC-ciphertext. De bestandsnaam is een
    EncString; de server kent enkel de grootte van de versleutelde blob.
    """

    __tablename__ = "vault_note_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vault_accounts.user_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


__all__ = ["VaultNote", "VaultNoteFile", "VaultNoteVersion"]
