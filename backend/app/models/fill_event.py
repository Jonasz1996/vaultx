import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid, utcnow


class VaultFillEvent(Base):
    """VaultX Invuller vulde een item in (of de login mislukte daarna).

    Enkel het id van het item en de host van de pagina: nooit een gebruikersnaam, wachtwoord of
    volledige URL. Ouder dan 30 dagen wordt opgeruimd.
    """

    __tablename__ = "vault_fill_events"
    __table_args__ = (Index("ix_vault_fill_events_user_at", "user_id", "occurred_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vault_accounts.user_id", ondelete="CASCADE")
    )
    cipher_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    host: Mapped[str] = mapped_column(String(261))
    # "fill" (ingevuld) of "failed" (hetzelfde formulier kwam terug: wachtwoord klopt niet meer?).
    kind: Mapped[str] = mapped_column(String(16))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
