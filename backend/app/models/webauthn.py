"""Beveiligingssleutels en passkeys (WebAuthn/FIDO2) als tweede stap.

Eén tabel voor twee doelen (`scope`):
- "vault": tweede stap voor de kluis (Bitwarden-apps, extensies en de webkluis);
- "login": tweede stap na het VaultX-wachtwoord (loginpagina).

Een YubiKey kan voor beide geregistreerd zijn; dat zijn dan twee rijen. VaultX bewaart
enkel de publieke sleutel en de teller van de sleutel, nooit iets geheims.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid, utcnow


class WebAuthnCredential(Base):
    __tablename__ = "webauthn_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "scope", "credential_id", name="uq_webauthn_credentials_user_scope_cred"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(100))
    credential_id: Mapped[bytes] = mapped_column(LargeBinary)
    # COSE-sleutel zoals de sleutel hem bij registratie gaf.
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    # Teller van de sleutel; een lagere of gelijke waarde wijst op een gekloonde sleutel.
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0)
    transports: Mapped[list[str]] = mapped_column(JSONB, default=list)
    aaguid: Mapped[str | None] = mapped_column(String(36))
    # Passkey die tussen apparaten synchroniseert (iCloud, Google, Bitwarden ...).
    backed_up: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Tijdstip (ms) van de laatst gebruikte challenge: een oudere wordt geweigerd (replay).
    last_challenge_ms: Mapped[int | None] = mapped_column(BigInteger)
