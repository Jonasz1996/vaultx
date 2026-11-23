"""Accountherstel vanaf de loginpagina: de gebruiker vraagt een code, de beheerder lost hem in.

1. Loginpagina: de gebruiker vult zijn login in en krijgt een code, bv.
   ``carol@example.com.K7QM-3XPA-9D2F-HJ4N``. Voor elke invoer hetzelfde antwoord: de server zoekt
   het account op dat moment niet op, dus niemand leert of een account bestaat.
2. De gebruiker geeft de code aan de beheerder; die voert ``python -m app.cli reset-account
   <code>`` uit. Pas dan zoekt VaultX het account, en enkel een openstaand, niet verlopen
   verzoek met precies die code telt. Een zelfverzonnen code voor een andere naam werkt niet.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestMeta
from app.core.errors import VaultXError
from app.core.security import hash_token
from app.models import AccountResetRequest

log = logging.getLogger(__name__)

# Zo lang kan de beheerder de code inlossen (de gebruiker moet hem eerst bereiken).
REQUEST_HOURS = 24
# Nooit meer openstaande verzoeken dan dit: de pagina is zonder login bereikbaar.
MAX_OPEN_REQUESTS = 500
MAX_LOGIN_LENGTH = 320
# Hoofdletters en cijfers zonder 0/O en 1/I: wordt soms voorgelezen of overgetypt.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass(slots=True)
class ResetCode:
    code: str
    expires_at: datetime


def _secret() -> str:
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(4))


def _normalize(login: str) -> str:
    return login.strip().lower()


def split_code(code: str) -> tuple[str, str]:
    """``<login>.<geheim>`` -> (login, geheim). Het geheim bevat geen punt."""
    login, sep, secret = code.strip().rpartition(".")
    secret = secret.upper().replace(" ", "")
    if not sep or not login or len(secret) != 19:
        raise VaultXError("Dit is geen geldige herstelcode (verwacht: login.XXXX-XXXX-XXXX-XXXX)")
    return _normalize(login), secret


async def create_request(db: AsyncSession, login: str, meta: RequestMeta) -> ResetCode:
    """Nieuwe code voor deze login; een vorige openstaande code voor dezelfde login vervalt."""
    name = _normalize(login)[:MAX_LOGIN_LENGTH]
    now = datetime.now(UTC)
    expires = now + timedelta(hours=REQUEST_HOURS)
    secret = _secret()
    code = f"{name}.{secret}"
    # Opruimen: verlopen of gebruikte verzoeken, en een oudere code voor dezelfde login.
    await db.execute(
        delete(AccountResetRequest).where(
            or_(
                AccountResetRequest.expires_at <= now,
                AccountResetRequest.used_at.is_not(None),
                AccountResetRequest.login == name,
            )
        )
    )
    open_count = await db.scalar(select(func.count()).select_from(AccountResetRequest))
    if (open_count or 0) >= MAX_OPEN_REQUESTS:
        # Iemand vult de tabel: de code wordt getoond maar niet bewaard (en werkt dus niet).
        log.warning("Te veel openstaande herstelverzoeken (%s); nieuw verzoek niet bewaard", open_count)
    else:
        db.add(
            AccountResetRequest(
                login=name,
                code_hash=hash_token(secret),
                created_at=now,
                expires_at=expires,
                ip_address=meta.ip_address,
            )
        )
    await db.commit()
    return ResetCode(code=code, expires_at=expires)


async def redeem(db: AsyncSession, code: str) -> AccountResetRequest:
    """Beheerder (CLI): het openstaande verzoek bij deze code, meteen als gebruikt gemarkeerd.
    Commit niet; de aanroeper doet de reset in dezelfde transactie."""
    login, secret = split_code(code)
    req = await db.scalar(
        select(AccountResetRequest)
        .where(AccountResetRequest.code_hash == hash_token(secret))
        .with_for_update()
    )
    if req is None or req.login != login:
        raise VaultXError("Onbekende herstelcode. Laat de gebruiker op de loginpagina een nieuwe vragen.")
    if req.used_at is not None:
        raise VaultXError("Deze herstelcode is al gebruikt. Laat de gebruiker een nieuwe vragen.")
    if req.expires_at <= datetime.now(UTC):
        raise VaultXError("Deze herstelcode is verlopen. Laat de gebruiker een nieuwe vragen.")
    req.used_at = datetime.now(UTC)
    return req
