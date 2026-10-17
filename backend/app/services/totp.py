"""TOTP (RFC 6238) voor tweestapsverificatie van de kluis: 30 seconden, 6 cijfers, SHA-1.

Dat is wat elke authenticator-app (Aegis, Google Authenticator, 2FAS, Bitwarden
Authenticator, ...) standaard doet. Een code wordt maar één keer aanvaard.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

PERIOD = 30
DIGITS = 6
# Eén stap ervoor en erna aanvaarden: klokken van telefoons lopen soms wat achter.
WINDOW = 1


def new_secret() -> str:
    """160 bits, base32 zonder opvulling (zoals authenticator-apps verwachten)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def code_at(secret: str, when: float | None = None) -> str:
    return _code(secret, int((time.time() if when is None else when) // PERIOD))


def verify(secret: str, code: str, last_step: int | None, when: float | None = None) -> int | None:
    """Geeft de tijdstap van een geldige code terug, of None. Een stap die al gebruikt is
    (last_step) of ouder, telt niet: zo kan een afgeluisterde code niet opnieuw dienen."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != DIGITS:
        return None
    now = int((time.time() if when is None else when) // PERIOD)
    for step in range(now - WINDOW, now + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, account: str, issuer: str = "VaultX") -> str:
    label = quote(f"{issuer}:{account}")
    query = urlencode(
        {"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS, "period": PERIOD}
    )
    return f"otpauth://totp/{label}?{query}"


def new_recovery_code() -> str:
    """32 tekens (160 bits) zoals bij Bitwarden, in groepjes van vier om over te typen."""
    raw = base64.b32encode(secrets.token_bytes(20)).decode()
    return "-".join(raw[i : i + 4] for i in range(0, 32, 4))


def normalize_recovery_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())
