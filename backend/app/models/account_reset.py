import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid, utcnow


class AccountResetRequest(Base):
    """Openstaand verzoek "kan niet inloggen" van de loginpagina.

    De gebruiker krijgt een code (login + geheim deel) die hij aan de beheerder geeft; die voert
    `app.cli reset-account <code>` uit. In de database staat enkel de SHA-256 van het geheime
    deel. Het verzoek bewaart de login zoals ingetypt, zonder het account op te zoeken: zo
    gedraagt de loginpagina zich voor elk adres hetzelfde. Eenmalig en tijdelijk geldig.
    """

    __tablename__ = "account_reset_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    login: Mapped[str] = mapped_column(String(320), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(String(64))
