"""Kleine cryptografische hulpfuncties (geen kluiscrypto: die komt pas later)."""

import base64
import hashlib
import secrets


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
