"""/api: config, sync, profiel, items en mappen voor Bitwarden-clients."""

from __future__ import annotations

import base64
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.bitwarden.deps import BitwardenRoute, Caller, vault_enabled
from app.api.deps import DbSession, SettingsDep
from app.core.errors import InvalidOperationError
from app.repositories import VaultAccountRepository, VaultDeviceRepository
from app.schemas.bitwarden import (
    CipherCreateIn,
    CipherIn,
    CipherPartialIn,
    FolderIn,
    IdsIn,
    MoveIn,
    UserKeyIdIn,
    cipher_json,
    device_json,
    folder_json,
    list_json,
    profile_json,
    sync_json,
)
from app.services.vault import VaultService

# Bitwarden-serverversie die VaultX meldt. De clients zetten functies aan of uit op basis
# van deze versie; wijzig ze alleen samen met een test tegen de nieuwe clients.
COMPAT_SERVER_VERSION = "2026.6.0"

router = APIRouter(
    prefix="/api",
    tags=["bitwarden-api"],
    route_class=BitwardenRoute,
    dependencies=[Depends(vault_enabled)],
)


@router.get("/config", summary="Serverconfiguratie voor Bitwarden-clients")
async def config(settings: SettingsDep) -> dict[str, Any]:
    base = settings.public_url
    return {
        "version": COMPAT_SERVER_VERSION,
        "gitHash": "vaultx",
        "server": {"name": "VaultX", "url": "https://github.com/Jonasz1996/vaultx"},
        "environment": {
            "cloudRegion": None,
            "vault": base,
            "api": f"{base}/api",
            "identity": f"{base}/identity",
            "notifications": f"{base}/notifications",
            "sso": "",
        },
        "featureStates": {},
        "push": {"pushTechnology": 0, "vapidPublicKey": None},
        "communication": None,
        "settings": {"disableUserRegistration": True, "enableEmailVerification": False},
        "object": "config",
    }


@router.get("/devices/knowndevice", summary="Heeft dit e-mailadres al eens ingelogd op dit apparaat?")
async def known_device(request: Request, db: DbSession) -> bool:
    raw_email = request.headers.get("x-request-email", "")
    identifier = request.headers.get("x-device-identifier", "")
    try:
        email = base64.urlsafe_b64decode(raw_email + "=" * (-len(raw_email) % 4)).decode().strip().lower()
    except ValueError:
        return False
    account = await VaultAccountRepository(db).by_email(email)
    if account is None or not identifier:
        return False
    device = await VaultDeviceRepository(db).by_identifier(account.user_id, identifier)
    return device is not None and device.revoked_at is None


@router.get("/sync", summary="Volledige kluis van de ingelogde gebruiker")
async def sync(c: Caller, db: DbSession) -> dict[str, Any]:
    folders, ciphers = await VaultService(db).sync_data(c)
    return sync_json(c.user, c.account, folders, ciphers)


@router.get("/accounts/profile")
async def profile(c: Caller) -> dict[str, Any]:
    return profile_json(c.user, c.account)


@router.get("/accounts/revision-date", summary="Laatste wijziging, in milliseconden sinds 1970")
async def revision_date(c: Caller) -> int:
    return int(c.account.revision_date.timestamp() * 1000)


@router.post("/accounts/key-management/user-key-id", status_code=200)
async def set_user_key_id(body: UserKeyIdIn, c: Caller, db: DbSession) -> None:
    await VaultService(db).set_user_key_id(c, body.user_key_id)


@router.get("/devices")
async def devices(c: Caller, db: DbSession) -> dict[str, Any]:
    rows = await VaultDeviceRepository(db).for_user(c.account.user_id)
    return list_json([device_json(d) for d in rows if d.revoked_at is None])


@router.get("/settings/domains")
async def domains(c: Caller) -> dict[str, Any]:
    return {"equivalentDomains": [], "globalEquivalentDomains": [], "object": "domains"}


# --- Items --------------------------------------------------------------------------


def _check_cipher(c: Caller, body: CipherIn) -> dict[str, Any]:
    if body.organization_id:
        raise InvalidOperationError("Items in organisaties worden nog niet ondersteund")
    if body.encrypted_for is not None and body.encrypted_for != c.account.user_id:
        raise InvalidOperationError("Item is voor een andere gebruiker versleuteld")
    try:
        return body.payload()
    except ValueError as exc:
        raise InvalidOperationError(str(exc)) from exc


@router.get("/ciphers")
async def list_ciphers(c: Caller, db: DbSession) -> dict[str, Any]:
    return list_json([cipher_json(x) for x in await VaultService(db).list_ciphers(c)])


@router.post("/ciphers")
async def create_cipher(body: CipherIn, c: Caller, db: DbSession) -> dict[str, Any]:
    payload = _check_cipher(c, body)
    cipher = await VaultService(db).create_cipher(
        c,
        type_=body.type,
        payload=payload,
        folder_id=body.folder_id,
        favorite=body.favorite,
        archived_at=body.archived_date,
    )
    return cipher_json(cipher)


@router.post("/ciphers/create")
async def create_cipher_with_collections(body: CipherCreateIn, c: Caller, db: DbSession) -> dict[str, Any]:
    if body.collection_ids:
        raise InvalidOperationError("Collecties worden nog niet ondersteund")
    return await create_cipher(body.cipher, c, db)


@router.put("/ciphers/delete", status_code=200)
async def trash_many(body: IdsIn, c: Caller, db: DbSession) -> None:
    await VaultService(db).trash_ciphers(c, body.ids)


@router.put("/ciphers/restore")
async def restore_many(body: IdsIn, c: Caller, db: DbSession) -> dict[str, Any]:
    ciphers = await VaultService(db).restore_ciphers(c, body.ids)
    return list_json([cipher_json(x) for x in ciphers])


@router.delete("/ciphers", status_code=200)
@router.post("/ciphers/delete", status_code=200)
async def delete_many(body: IdsIn, c: Caller, db: DbSession) -> None:
    await VaultService(db).delete_ciphers(c, body.ids)


@router.put("/ciphers/move", status_code=200)
@router.post("/ciphers/move", status_code=200)
async def move_many(body: MoveIn, c: Caller, db: DbSession) -> None:
    await VaultService(db).move_ciphers(c, body.ids, body.folder_id)


@router.get("/ciphers/{cipher_id}")
@router.get("/ciphers/{cipher_id}/details")
async def get_cipher(cipher_id: UUID, c: Caller, db: DbSession) -> dict[str, Any]:
    return cipher_json(await VaultService(db).get_cipher(c, cipher_id))


@router.put("/ciphers/{cipher_id}")
@router.post("/ciphers/{cipher_id}")
async def update_cipher(cipher_id: UUID, body: CipherIn, c: Caller, db: DbSession) -> dict[str, Any]:
    payload = _check_cipher(c, body)
    cipher = await VaultService(db).update_cipher(
        c,
        cipher_id,
        type_=body.type,
        payload=payload,
        folder_id=body.folder_id,
        favorite=body.favorite,
        archived_at=body.archived_date,
        last_known_revision=body.last_known_revision_date,
    )
    return cipher_json(cipher)


@router.put("/ciphers/{cipher_id}/partial")
@router.post("/ciphers/{cipher_id}/partial")
async def update_cipher_partial(
    cipher_id: UUID, body: CipherPartialIn, c: Caller, db: DbSession
) -> dict[str, Any]:
    cipher = await VaultService(db).update_cipher_partial(
        c, cipher_id, folder_id=body.folder_id, favorite=body.favorite
    )
    return cipher_json(cipher)


@router.put("/ciphers/{cipher_id}/delete", status_code=200, summary="Naar de prullenbak")
async def trash_cipher(cipher_id: UUID, c: Caller, db: DbSession) -> None:
    await VaultService(db).trash_ciphers(c, [cipher_id])


@router.put("/ciphers/{cipher_id}/restore", summary="Terugzetten uit de prullenbak")
async def restore_cipher(cipher_id: UUID, c: Caller, db: DbSession) -> dict[str, Any]:
    ciphers = await VaultService(db).restore_ciphers(c, [cipher_id])
    return cipher_json(ciphers[0])


@router.delete("/ciphers/{cipher_id}", status_code=200, summary="Definitief verwijderen")
@router.post("/ciphers/{cipher_id}/delete", status_code=200)
async def delete_cipher(cipher_id: UUID, c: Caller, db: DbSession) -> None:
    await VaultService(db).delete_ciphers(c, [cipher_id])


@router.post("/ciphers/{cipher_id}/attachment/v2", include_in_schema=False)
@router.post("/ciphers/{cipher_id}/attachment", include_in_schema=False)
async def attachments_not_supported(cipher_id: UUID, c: Caller) -> None:
    raise InvalidOperationError("Bijlagen worden nog niet ondersteund door VaultX")


# --- Mappen -------------------------------------------------------------------------


@router.get("/folders")
async def list_folders(c: Caller, db: DbSession) -> dict[str, Any]:
    folders, _ = await VaultService(db).sync_data(c)
    return list_json([folder_json(f) for f in folders])


@router.post("/folders")
async def create_folder(body: FolderIn, c: Caller, db: DbSession) -> dict[str, Any]:
    return folder_json(await VaultService(db).create_folder(c, body.name))


@router.get("/folders/{folder_id}")
async def get_folder(folder_id: UUID, c: Caller, db: DbSession) -> dict[str, Any]:
    return folder_json(await VaultService(db).get_folder(c, folder_id))


@router.put("/folders/{folder_id}")
@router.post("/folders/{folder_id}")
async def update_folder(folder_id: UUID, body: FolderIn, c: Caller, db: DbSession) -> dict[str, Any]:
    return folder_json(await VaultService(db).update_folder(c, folder_id, body.name))


@router.delete("/folders/{folder_id}", status_code=200)
@router.post("/folders/{folder_id}/delete", status_code=200)
async def delete_folder(folder_id: UUID, c: Caller, db: DbSession) -> None:
    await VaultService(db).delete_folder(c, folder_id)
