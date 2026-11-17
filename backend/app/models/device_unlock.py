import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid, utcnow


class VaultDeviceUnlock(Base):
    """Ontgrendelen met Authentik op één apparaat (VaultX Invuller).

    De helft van de sleutel staat op het apparaat, de helft hier: ``blob`` is de user key van de
    kluis, versleuteld (EncString type 2) met een willekeurige apparaatsleutel D die enkel in de
    extensie staat. VaultX kan de blob dus nooit zelf openen; een dump van deze tabel is zonder D
    waardeloos, en D is zonder deze blob (en een geldige Authentik-sessie met MFA) waardeloos.
    """

    __tablename__ = "vault_device_unlock"
    __table_args__ = (
        # Per apparaat één actieve rij.
        Index(
            "uq_vault_device_unlock_active",
            "user_id",
            "device_identifier",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vault_accounts.user_id", ondelete="CASCADE"), index=True
    )
    # Vaste id die de extensie zelf koos (geen geheim).
    device_identifier: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    blob: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_ip: Mapped[str | None] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(64))
