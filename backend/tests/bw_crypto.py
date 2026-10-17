"""Clientcrypto zoals de Bitwarden-clients die doen, voor tests (PBKDF2, EncString type 2).

Bewust los van de server- en frontendcode geschreven, zodat de tests een
onafhankelijke controle zijn van het formaat.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ITERATIONS = 600_000


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def master_key(password: str, salt: str, iterations: int = ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations, 32)


def master_password_hash(mkey: bytes, password: str) -> str:
    return b64(hashlib.pbkdf2_hmac("sha256", mkey, password.encode(), 1, 32))


def stretch(mkey: bytes) -> tuple[bytes, bytes]:
    """HKDF-Expand (zonder extract) naar een enc- en mac-sleutel."""
    enc = hmac.new(mkey, b"enc\x01", hashlib.sha256).digest()
    mac = hmac.new(mkey, b"mac\x01", hashlib.sha256).digest()
    return enc, mac


def encrypt(plain: bytes, enc_key: bytes, mac_key: bytes) -> str:
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plain) + padder.finalize()
    enc = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).encryptor()
    ct = enc.update(data) + enc.finalize()
    mac = hmac.new(mac_key, iv + ct, hashlib.sha256).digest()
    return f"2.{b64(iv)}|{b64(ct)}|{b64(mac)}"


def decrypt(enc_string: str, enc_key: bytes, mac_key: bytes) -> bytes:
    assert enc_string.startswith("2.")
    iv, ct, mac = (base64.b64decode(x) for x in enc_string[2:].split("|"))
    assert hmac.compare_digest(hmac.new(mac_key, iv + ct, hashlib.sha256).digest(), mac)
    dec = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    padded = dec.update(ct) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


@dataclass
class Account:
    email: str
    password: str
    mkey: bytes
    user_key: bytes

    @property
    def password_hash(self) -> str:
        return master_password_hash(self.mkey, self.password)

    def enc(self, text: str) -> str:
        return encrypt(text.encode(), self.user_key[:32], self.user_key[32:])

    def dec(self, enc_string: str) -> str:
        return decrypt(enc_string, self.user_key[:32], self.user_key[32:]).decode()


def new_enrollment(email: str, password: str) -> tuple[Account, dict]:
    salt = email.strip().lower()
    mkey = master_key(password, salt)
    enc_key, mac_key = stretch(mkey)
    user_key = os.urandom(64)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pkcs8 = private.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    spki = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    account = Account(salt, password, mkey, user_key)
    payload = {
        "salt": salt,
        "kdf": 0,
        "kdf_iterations": ITERATIONS,
        "master_password_hash": account.password_hash,
        "user_key": encrypt(user_key, enc_key, mac_key),
        "public_key": b64(spki),
        "private_key": encrypt(pkcs8, user_key[:32], user_key[32:]),
    }
    return account, payload


def unwrap_user_key(mkey: bytes, enc_user_key: str) -> bytes:
    return decrypt(enc_user_key, *stretch(mkey))


__all__ = ["Account", "new_enrollment", "unwrap_user_key"]
