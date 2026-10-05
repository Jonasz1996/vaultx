"""Draadformaat van de Bitwarden-clients (requests en responses).

Alleen dit bestand en app/api/bitwarden/ kennen de veldnamen van het Bitwarden-
protocol; de services werken met de VaultX-modellen. Responses gebruiken
camelCase, zoals de huidige Bitwarden-server: de clients lezen elk veld zowel
in PascalCase als camelCase.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import User, VaultAccount, VaultCipher, VaultDevice, VaultFolder
from app.services.vault_auth import PreloginData, TokenGrant

# Max. grootte van het versleutelde deel van één item (JSON). Bijlagen horen hier niet in.
MAX_CIPHER_PAYLOAD_BYTES = 256 * 1024

# Velden die de server zelf beheert. Al de rest van een cipher-request is
# versleutelde inhoud en wordt ongewijzigd bewaard en teruggegeven.
SERVER_MANAGED_CIPHER_FIELDS = {
    "id",
    "type",
    "organizationId",
    "folderId",
    "favorite",
    "lastKnownRevisionDate",
    "archivedDate",
    "collectionIds",
    "attachments",
    "attachments2",
    "encryptedFor",
    "encryptedByKeyId",
    "edit",
    "viewPassword",
    "permissions",
    "organizationUseTotp",
    "revisionDate",
    "creationDate",
    "deletedDate",
    "object",
}


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def kdf_json(kdf: int, iterations: int, memory: int | None, parallelism: int | None) -> dict[str, Any]:
    return {"kdfType": kdf, "iterations": iterations, "memory": memory, "parallelism": parallelism}


# --- Requests -----------------------------------------------------------------


class _CamelIn(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class CipherIn(_CamelIn):
    type: int = Field(ge=1, le=99)
    name: str = Field(min_length=1, max_length=10_000)
    folder_id: UUID | None = Field(None, alias="folderId")
    organization_id: str | None = Field(None, alias="organizationId")
    favorite: bool = False
    last_known_revision_date: datetime | None = Field(None, alias="lastKnownRevisionDate")
    archived_date: datetime | None = Field(None, alias="archivedDate")
    encrypted_for: UUID | None = Field(None, alias="encryptedFor")

    @field_validator("folder_id", mode="before")
    @classmethod
    def _empty_folder(cls, v: Any) -> Any:
        return v or None

    def payload(self) -> dict[str, Any]:
        """Het versleutelde deel van het item, zoals het bewaard wordt."""
        raw = self.model_dump(by_alias=True)
        data = {k: v for k, v in raw.items() if k not in SERVER_MANAGED_CIPHER_FIELDS}
        if len(json.dumps(data, default=str)) > MAX_CIPHER_PAYLOAD_BYTES:
            raise ValueError("Item is te groot")
        return data


class CipherCreateIn(_CamelIn):
    """POST /api/ciphers/create: item met collecties (organisaties)."""

    cipher: CipherIn
    collection_ids: list[UUID] = Field(default_factory=list, alias="collectionIds")


class CipherPartialIn(_CamelIn):
    folder_id: UUID | None = Field(None, alias="folderId")
    favorite: bool = False

    @field_validator("folder_id", mode="before")
    @classmethod
    def _empty_folder(cls, v: Any) -> Any:
        return v or None


class IdsIn(_CamelIn):
    ids: list[UUID] = Field(max_length=5000)


class MoveIn(IdsIn):
    folder_id: UUID | None = Field(None, alias="folderId")


class FolderIn(_CamelIn):
    name: str = Field(min_length=1, max_length=10_000)


class UserKeyIdIn(_CamelIn):
    user_key_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$", alias="userKeyId")


class PreloginIn(_CamelIn):
    email: str = Field(max_length=320)


# --- Responses ----------------------------------------------------------------


def prelogin_json(data: PreloginData) -> dict[str, Any]:
    """Zowel het nieuwe (/prelogin/password) als het oude (/prelogin) formaat."""
    return {
        "kdfSettings": kdf_json(data.kdf, data.iterations, data.memory, data.parallelism),
        "salt": data.salt,
        "kdf": data.kdf,
        "kdfIterations": data.iterations,
        "kdfMemory": data.memory,
        "kdfParallelism": data.parallelism,
    }


def account_keys_json(account: VaultAccount) -> dict[str, Any]:
    keys: dict[str, Any] = {
        "publicKeyEncryptionKeyPair": {
            "wrappedPrivateKey": account.private_key,
            "publicKey": account.public_key,
            "object": "publicKeyEncryptionKeyPair",
        },
        "object": "privateKeys",
    }
    return keys


def master_password_unlock_json(account: VaultAccount) -> dict[str, Any]:
    return {
        "kdf": kdf_json(
            account.kdf_type, account.kdf_iterations, account.kdf_memory, account.kdf_parallelism
        ),
        "masterKeyEncryptedUserKey": account.user_key,
        "masterKeyWrappedUserKey": account.user_key,
        "salt": account.kdf_salt,
    }


def token_json(grant: TokenGrant) -> dict[str, Any]:
    a = grant.account
    out: dict[str, Any] = {
        "access_token": grant.access_token,
        "expires_in": grant.expires_in,
        "token_type": "Bearer",
        "scope": "api offline_access",
        "Key": a.user_key,
        "PrivateKey": a.private_key,
        "AccountKeys": account_keys_json(a),
        "Kdf": a.kdf_type,
        "KdfIterations": a.kdf_iterations,
        "KdfMemory": a.kdf_memory,
        "KdfParallelism": a.kdf_parallelism,
        "ResetMasterPassword": False,
        "ForcePasswordReset": False,
        "MasterPasswordPolicy": {"object": "masterPasswordPolicy"},
        "UserDecryptionOptions": {
            "HasMasterPassword": True,
            "MasterPasswordUnlock": master_password_unlock_json(a),
            "Object": "userDecryptionOptions",
        },
    }
    if grant.refresh_token:
        out["refresh_token"] = grant.refresh_token
    return out


def identity_error_json(error: str, description: str, message: str) -> dict[str, Any]:
    return {
        "error": error,
        "error_description": description,
        "ErrorModel": {"Message": message, "Object": "error"},
    }


def api_error_json(message: str, validation: dict[str, list[str]] | None = None) -> dict[str, Any]:
    return {"message": message, "validationErrors": validation, "object": "error"}


def profile_json(user: User, account: VaultAccount) -> dict[str, Any]:
    return {
        "id": str(account.user_id),
        "name": user.display_name or user.username,
        "email": account.email,
        "emailVerified": True,
        "premium": True,
        "premiumFromOrganization": False,
        "culture": "en-US",
        "twoFactorEnabled": False,
        "key": account.user_key,
        "privateKey": account.private_key,
        "accountKeys": account_keys_json(account),
        "securityStamp": account.security_stamp,
        "forcePasswordReset": False,
        "usesKeyConnector": False,
        "verifyDevices": False,
        "avatarColor": None,
        "creationDate": iso(account.created_at),
        "organizations": [],
        "providers": [],
        "providerOrganizations": [],
        "object": "profile",
    }


def folder_json(folder: VaultFolder) -> dict[str, Any]:
    return {
        "id": str(folder.id),
        "name": folder.name,
        "revisionDate": iso(folder.revision_date),
        "object": "folder",
    }


def cipher_json(cipher: VaultCipher) -> dict[str, Any]:
    return {
        **cipher.data,
        "id": str(cipher.id),
        "organizationId": None,
        "folderId": str(cipher.folder_id) if cipher.folder_id else None,
        "type": cipher.type,
        "favorite": cipher.favorite,
        "edit": True,
        "viewPassword": True,
        "permissions": {"delete": True, "restore": True},
        "organizationUseTotp": False,
        "collectionIds": [],
        "attachments": None,
        "revisionDate": iso(cipher.revision_date),
        "creationDate": iso(cipher.created_at),
        "deletedDate": iso(cipher.deleted_at),
        "archivedDate": iso(cipher.archived_at),
        "object": "cipherDetails",
    }


def sync_json(
    user: User, account: VaultAccount, folders: list[VaultFolder], ciphers: list[VaultCipher]
) -> dict[str, Any]:
    return {
        "profile": profile_json(user, account),
        "folders": [folder_json(f) for f in folders],
        "collections": [],
        "policies": [],
        "ciphers": [cipher_json(c) for c in ciphers],
        "domains": {"equivalentDomains": [], "globalEquivalentDomains": [], "object": "domains"},
        "sends": [],
        "userDecryption": {
            "masterPasswordUnlock": master_password_unlock_json(account),
            "userKeyId": account.user_key_id,
        },
        "object": "sync",
    }


def device_json(device: VaultDevice) -> dict[str, Any]:
    return {
        "id": str(device.id),
        "name": device.name,
        "type": device.type,
        "identifier": device.identifier,
        "creationDate": iso(device.created_at),
        "isTrusted": False,
        "object": "device",
    }


def list_json(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": items, "continuationToken": None, "object": "list"}
