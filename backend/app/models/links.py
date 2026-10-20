"""Links (fase 10): een bladwijzerbeheerder zoals Linkwarden.

Anders dan de kluis en de notities zijn links niet end-to-end versleuteld: archiveren,
zoeken in de tekst, RSS en publieke deellinks werken enkel als de server de URL en de
tekst kan lezen. Ze horen bij een VaultX-gebruiker, niet bij zijn kluis.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship

from app.models.base import Base, TimestampMixin, new_uuid, utcnow

link_tag_map = Table(
    "link_tag_map",
    Base.metadata,
    Column("link_id", UUID(as_uuid=True), ForeignKey("links.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "tag_id",
        UUID(as_uuid=True),
        ForeignKey("link_tags.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)


class LinkCollection(TimestampMixin, Base):
    """Collectie (map) met links; collecties kunnen genest zijn."""

    __tablename__ = "link_collections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Een collectie verwijderen haalt ook haar subcollecties en links weg (zoals in Linkwarden).
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("link_collections.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(16), default="#0ea5e9")


class LinkTag(Base):
    __tablename__ = "link_tags"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_link_tags_user_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Link(TimestampMixin, Base):
    __tablename__ = "links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Leeg = "Zonder collectie".
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("link_collections.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    # Tekst van de pagina (uit een Linkwarden-archief of later zelf gearchiveerd), voor zoeken.
    text_content: Mapped[str | None] = deferred(mapped_column(Text))
    # Waar de link vandaan komt: manual | linkwarden | bookmarks.
    source: Mapped[str] = mapped_column(String(32), default="manual")
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tags: Mapped[list[LinkTag]] = relationship(secondary=link_tag_map, lazy="selectin", order_by=LinkTag.name)
    archives: Mapped[list["LinkArchive"]] = relationship(
        lazy="selectin", order_by="LinkArchive.kind", cascade="all, delete-orphan", passive_deletes=True
    )


class LinkArchive(Base):
    """Gearchiveerde versie van een link: screenshot, pdf, leesbare versie of volledige pagina."""

    __tablename__ = "link_archives"
    __table_args__ = (UniqueConstraint("link_id", "kind", name="uq_link_archives_link_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("links.id", ondelete="CASCADE"), index=True
    )
    # screenshot | pdf | readable | page | preview
    kind: Mapped[str] = mapped_column(String(16))
    media_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


__all__ = ["Link", "LinkArchive", "LinkCollection", "LinkTag", "link_tag_map"]
