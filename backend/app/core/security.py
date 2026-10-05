"""Kleine cryptografische hulpfuncties van de server.

De kluiscrypto zelf (master key, user key, items) gebeurt in de clients; de
server ziet alleen versleutelde blobs. Zie services/vault_auth.py voor het
server-side hashen van de master password hash.
"""

import base64
import functools
import hashlib
import hmac
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def new_token(nbytes: int = 32) -> str:
    """URL-veilig willekeurig token (256 bit standaard)."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """SHA-256 van een sessietoken. Tokens hebben 256 bit entropie, dus een
    snelle hash volstaat; een databaselek geeft geen bruikbare cookies."""
    return hashlib.sha256(token.encode()).hexdigest()


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# Versleuteling van infrastructuurgeheimen (bv. het wachtwoord van een NPM-account).
# AES-256-GCM met een sleutel die via HKDF uit VAULTX_SECRET_KEY wordt afgeleid.
# Formaat: versiebyte || nonce (12 bytes) || ciphertext+tag. De context (bv. de
# id van de koppeling) gaat mee als associated data, zodat een ciphertext niet
# naar een andere rij gekopieerd kan worden.
_SECRET_BOX_VERSION = b"\x01"


def derive_key(master: str, purpose: str) -> bytes:
    """Aparte 256-bit sleutel per doel, afgeleid van VAULTX_SECRET_KEY via HKDF."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose.encode()).derive(
        master.encode()
    )


def _secret_box_key(master: str) -> bytes:
    return derive_key(master, "vaultx/infra-secrets/v1")


def seal_secret(master: str, plaintext: str, context: str) -> bytes:
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_secret_box_key(master)).encrypt(nonce, plaintext.encode(), context.encode())
    return _SECRET_BOX_VERSION + nonce + ct


def open_secret(master: str, sealed: bytes, context: str) -> str:
    if sealed[:1] != _SECRET_BOX_VERSION:
        raise ValueError("Onbekend formaat van versleuteld geheim")
    return AESGCM(_secret_box_key(master)).decrypt(sealed[1:13], sealed[13:], context.encode()).decode()


# Master password hash van Bitwarden-clients. De client stuurt base64(PBKDF2(master key,
# master password, 1)); de server hasht dat nog eens met een eigen salt, zodat een
# databaselek niet rechtstreeks een bruikbare login oplevert.
# Formaat: pbkdf2_sha256$<iteraties>$<salt b64>$<hash b64>
_MPH_ITERATIONS = 600_000


def hash_master_password(client_hash: str, iterations: int = _MPH_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", client_hash.encode(), salt, iterations)
    return "$".join(
        ["pbkdf2_sha256", str(iterations), base64.b64encode(salt).decode(), base64.b64encode(digest).decode()]
    )


def verify_master_password(client_hash: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest_b64)
        digest = hashlib.pbkdf2_hmac(
            "sha256", client_hash.encode(), base64.b64decode(salt_b64), int(iterations)
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest, expected)


@functools.cache
def dummy_master_password_hash() -> str:
    """Vaste dummy-hash: een onbekend e-mailadres kost evenveel tijd als een bekend."""
    return hash_master_password("vaultx-dummy")
