"""Kleine cryptografische hulpfuncties (geen kluiscrypto: die komt pas later)."""

import base64
import hashlib
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


def _secret_box_key(master: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"vaultx/infra-secrets/v1").derive(
        master.encode()
    )


def seal_secret(master: str, plaintext: str, context: str) -> bytes:
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_secret_box_key(master)).encrypt(nonce, plaintext.encode(), context.encode())
    return _SECRET_BOX_VERSION + nonce + ct


def open_secret(master: str, sealed: bytes, context: str) -> str:
    if sealed[:1] != _SECRET_BOX_VERSION:
        raise ValueError("Onbekend formaat van versleuteld geheim")
    return AESGCM(_secret_box_key(master)).decrypt(sealed[1:13], sealed[13:], context.encode()).decode()
