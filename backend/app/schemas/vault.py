"""VaultX-eigen API voor de kluis: activeren vanuit de webinterface, status en apparaten.

Het Bitwarden-protocol zelf staat in app/schemas/bitwarden.py.
"""

import base64
import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_der_public_key
from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import ORMModel

# EncString type 2: AES-256-CBC + HMAC-SHA256, "2.<iv>|<ciphertext>|<mac>" in base64.
ENC_STRING_TYPE2 = re.compile(r"^2\.[A-Za-z0-9+/]+=*\|[A-Za-z0-9+/]+=*\|[A-Za-z0-9+/]+=*$")

PBKDF2_MIN_ITERATIONS = 600_000
PBKDF2_MAX_ITERATIONS = 2_000_000


class VaultEnroll(BaseModel):
    """Wat de browser na client-side sleutelafleiding naar VaultX stuurt.

    Het master password zelf verlaat de browser nooit.
    """

    salt: str = Field(max_length=320, description="Gebruikte KDF-salt; moet het kluis-e-mailadres zijn")
    kdf: Literal[0, 1] = Field(0, description="0 = PBKDF2-SHA256, 1 = Argon2id")
    kdf_iterations: int
    kdf_memory: int | None = Field(None, description="Argon2id, in MiB")
    kdf_parallelism: int | None = None
    master_password_hash: str = Field(max_length=128, description="base64(PBKDF2(master key, wachtwoord, 1))")
    user_key: str = Field(max_length=1024, description="User key versleuteld met de master key (EncString)")
    public_key: str = Field(max_length=4096, description="RSA-publieke sleutel, SPKI DER in base64")
    private_key: str = Field(
        max_length=8192, description="Private key versleuteld met de user key (EncString)"
    )

    @field_validator("master_password_hash")
    @classmethod
    def _hash_is_32_bytes(cls, v: str) -> str:
        try:
            raw = base64.b64decode(v, validate=True)
        except ValueError as exc:
            raise ValueError("master_password_hash is geen geldige base64") from exc
        if len(raw) != 32:
            raise ValueError("master_password_hash moet 32 bytes zijn")
        return v

    @field_validator("user_key", "private_key")
    @classmethod
    def _enc_string(cls, v: str) -> str:
        if not ENC_STRING_TYPE2.match(v):
            raise ValueError("verwacht een EncString van type 2 (AES-256-CBC-HMAC)")
        return v

    @field_validator("public_key")
    @classmethod
    def _rsa_public_key(cls, v: str) -> str:
        try:
            key = load_der_public_key(base64.b64decode(v, validate=True))
        except ValueError as exc:
            raise ValueError("public_key is geen geldige SPKI-sleutel") from exc
        if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
            raise ValueError("public_key moet een RSA-sleutel van minstens 2048 bit zijn")
        return v

    @model_validator(mode="after")
    def _kdf_minimums(self) -> "VaultEnroll":
        # Zelfde ondergrenzen als de Bitwarden-clients bij het instellen van een KDF.
        if self.kdf == 0:
            if not PBKDF2_MIN_ITERATIONS <= self.kdf_iterations <= PBKDF2_MAX_ITERATIONS:
                raise ValueError(
                    f"PBKDF2 vraagt {PBKDF2_MIN_ITERATIONS:,} tot {PBKDF2_MAX_ITERATIONS:,} iteraties"
                )
            self.kdf_memory = None
            self.kdf_parallelism = None
        else:
            if not 2 <= self.kdf_iterations <= 10:
                raise ValueError("Argon2id vraagt 2 tot 10 iteraties")
            if self.kdf_memory is None or not 16 <= self.kdf_memory <= 1024:
                raise ValueError("Argon2id vraagt 16 tot 1024 MiB geheugen")
            if self.kdf_parallelism is None or not 1 <= self.kdf_parallelism <= 16:
                raise ValueError("Argon2id vraagt een parallelisme van 1 tot 16")
        return self


class VaultDeviceOut(ORMModel):
    id: UUID
    name: str
    type: int
    type_name: str = ""
    created_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None


class VaultStatusOut(BaseModel):
    enrolled: bool
    # E-mailadres waarmee je inlogt in de Bitwarden-client (en de KDF-salt).
    email: str | None
    # Waarom activeren (nog) niet kan, bv. geen e-mailadres in Authentik.
    blocked_reason: str | None = None
    server_url: str
    kdf: int | None = None
    kdf_iterations: int | None = None
    kdf_memory: int | None = None
    kdf_parallelism: int | None = None
    enrolled_at: datetime | None = None
    revision_date: datetime | None = None
    item_count: int = 0
    trash_count: int = 0
    folder_count: int = 0
    devices: list[VaultDeviceOut] = []


class VaultUserSummary(BaseModel):
    """Kluisstatus van een gebruiker, voor beheerders (zonder inhoud)."""

    enrolled: bool
    email: str | None = None
    enrolled_at: datetime | None = None
    item_count: int = 0
    active_devices: int = 0
