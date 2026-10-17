"""/api: config, sync, profiel, items en mappen voor Bitwarden-clients."""

from __future__ import annotations

import base64
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.bitwarden.deps import BitwardenRoute, Caller, vault_enabled
from app.api.deps import DbSession, SettingsDep
from app.core.errors import InvalidOperationError
from app.repositories import VaultAccountRepository, VaultDeviceRepository
from app.schemas.bitwarden import (
    AttachmentIn,
    CipherCreateIn,
    CipherIn,
    CipherPartialIn,
    FolderIn,
    IdsIn,
    ImportIn,
    MoveIn,
    UserKeyIdIn,
    cipher_json,
    device_json,
    folder_json,
    list_json,
    profile_json,
    sync_json,
)
from app.services.attachments import (
    SYNC_DOWNLOAD_SECONDS,
    AttachmentLinks,
    AttachmentService,
    attachment_json,
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
            "icons": f"{base}/icons",
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
async def sync(c: Caller, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    folders, ciphers = await VaultService(db).sync_data(c)
    return sync_json(c.user, c.account, folders, await _ciphers_out(c, db, settings, ciphers))


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


async def _ciphers_out(
    c: Caller, db: DbSession, settings: SettingsDep, ciphers: list
) -> list[dict[str, Any]]:
    """Items met hun bijlagen (download-URL's met een ondertekend token)."""
    links = AttachmentLinks(settings)
    by_cipher = await AttachmentService(db, settings).by_cipher(c.account.user_id, [x.id for x in ciphers])
    return [
        cipher_json(x, [attachment_json(a, links, SYNC_DOWNLOAD_SECONDS) for a in by_cipher.get(x.id, [])])
        for x in ciphers
    ]


async def _cipher_out(c: Caller, db: DbSession, settings: SettingsDep, cipher) -> dict[str, Any]:
    return (await _ciphers_out(c, db, settings, [cipher]))[0]


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
async def list_ciphers(c: Caller, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    return list_json(await _ciphers_out(c, db, settings, await VaultService(db).list_ciphers(c)))


@router.post("/ciphers")
async def create_cipher(body: CipherIn, c: Caller, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    payload = _check_cipher(c, body)
    cipher = await VaultService(db).create_cipher(
        c,
        type_=body.type,
        payload=payload,
        folder_id=body.folder_id,
        favorite=body.favorite,
        archived_at=body.archived_date,
    )
    return await _cipher_out(c, db, settings, cipher)


@router.post("/ciphers/create")
async def create_cipher_with_collections(
    body: CipherCreateIn, c: Caller, db: DbSession, settings: SettingsDep
) -> dict[str, Any]:
    if body.collection_ids:
        raise InvalidOperationError("Collecties worden nog niet ondersteund")
    return await create_cipher(body.cipher, c, db, settings)


@router.post("/ciphers/import", status_code=200, summary="Import uit een andere kluis (al versleuteld)")
async def import_ciphers(body: ImportIn, c: Caller, db: DbSession) -> None:
    ciphers = [(x.type, _check_cipher(c, x), x.favorite, x.archived_date) for x in body.ciphers]
    await VaultService(db).import_items(
        c,
        folders=[(f.id, f.name) for f in body.folders],
        ciphers=ciphers,
        relationships=[(r.key, r.value) for r in body.folder_relationships],
    )


@router.put("/ciphers/delete", status_code=200)
async def trash_many(body: IdsIn, c: Caller, db: DbSession) -> None:
    await VaultService(db).trash_ciphers(c, body.ids)


@router.put("/ciphers/restore")
async def restore_many(body: IdsIn, c: Caller, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    ciphers = await VaultService(db).restore_ciphers(c, body.ids)
    return list_json(await _ciphers_out(c, db, settings, ciphers))


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
async def get_cipher(cipher_id: UUID, c: Caller, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    return await _cipher_out(c, db, settings, await VaultService(db).get_cipher(c, cipher_id))


@router.put("/ciphers/{cipher_id}")
@router.post("/ciphers/{cipher_id}")
async def update_cipher(
    cipher_id: UUID, body: CipherIn, c: Caller, db: DbSession, settings: SettingsDep
) -> dict[str, Any]:
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
    return await _cipher_out(c, db, settings, cipher)


@router.put("/ciphers/{cipher_id}/partial")
@router.post("/ciphers/{cipher_id}/partial")
async def update_cipher_partial(
    cipher_id: UUID, body: CipherPartialIn, c: Caller, db: DbSession, settings: SettingsDep
) -> dict[str, Any]:
    cipher = await VaultService(db).update_cipher_partial(
        c, cipher_id, folder_id=body.folder_id, favorite=body.favorite
    )
    return await _cipher_out(c, db, settings, cipher)


@router.put("/ciphers/{cipher_id}/delete", status_code=200, summary="Naar de prullenbak")
async def trash_cipher(cipher_id: UUID, c: Caller, db: DbSession) -> None:
    await VaultService(db).trash_ciphers(c, [cipher_id])


@router.put("/ciphers/{cipher_id}/restore", summary="Terugzetten uit de prullenbak")
async def restore_cipher(cipher_id: UUID, c: Caller, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    ciphers = await VaultService(db).restore_ciphers(c, [cipher_id])
    return await _cipher_out(c, db, settings, ciphers[0])


@router.delete("/ciphers/{cipher_id}", status_code=200, summary="Definitief verwijderen")
@router.post("/ciphers/{cipher_id}/delete", status_code=200)
async def delete_cipher(cipher_id: UUID, c: Caller, db: DbSession) -> None:
    await VaultService(db).delete_ciphers(c, [cipher_id])


# --- Bijlagen (fase 9) ----------------------------------------------------------------


def _upload_data_json(a, cipher_out: dict[str, Any], links: AttachmentLinks) -> dict[str, Any]:
    # Zoals Bitwarden: het item in het antwoord toont de nieuwe bijlage al (de CLI geeft dit
    # item terug als resultaat van "bw create attachment").
    if not any(x["id"] == str(a.id) for x in cipher_out["attachments"] or []):
        cipher_out["attachments"] = [*(cipher_out["attachments"] or []), attachment_json(a, links)]
    return {
        "attachmentId": str(a.id),
        "url": links.upload_url(a),
        "fileUploadType": 0,  # Direct: de client POST de inhoud naar VaultX zelf
        "cipherResponse": cipher_out,
        "cipherMiniResponse": None,
        "object": "attachment-fileUpload",
    }


@router.post("/ciphers/{cipher_id}/attachment/v2", summary="Bijlage aankondigen (naam, sleutel, grootte)")
async def create_attachment(
    cipher_id: UUID, body: AttachmentIn, c: Caller, db: DbSession, settings: SettingsDep
) -> dict[str, Any]:
    if body.admin_request:
        raise InvalidOperationError("Organisaties worden nog niet ondersteund")
    a, cipher = await AttachmentService(db, settings).create(
        c, cipher_id, file_name=body.file_name, key=body.key, size=body.file_size
    )
    return _upload_data_json(a, await _cipher_out(c, db, settings, cipher), AttachmentLinks(settings))


@router.get("/ciphers/{cipher_id}/attachment/{attachment_id}/renew", summary="Upload-URL opnieuw opvragen")
async def renew_attachment(
    cipher_id: UUID, attachment_id: UUID, c: Caller, db: DbSession, settings: SettingsDep
) -> dict[str, Any]:
    a, cipher = await AttachmentService(db, settings).renew(c, cipher_id, attachment_id)
    return _upload_data_json(a, await _cipher_out(c, db, settings, cipher), AttachmentLinks(settings))


@router.post("/ciphers/{cipher_id}/attachment/{attachment_id}", status_code=200, summary="Inhoud uploaden")
async def upload_attachment(
    cipher_id: UUID, attachment_id: UUID, request: Request, c: Caller, db: DbSession, settings: SettingsDep
) -> None:
    limit = settings.vault_attachment_max_mb * 1024 * 1024
    form = await request.form(max_files=1, max_fields=5, max_part_size=limit + 1024)
    upload = form.get("data")
    if not isinstance(upload, StarletteUploadFile):
        raise InvalidOperationError("Veld 'data' met het versleutelde bestand ontbreekt")
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise InvalidOperationError(f"Bijlagen mogen maximaal {settings.vault_attachment_max_mb} MB zijn")
    await AttachmentService(db, settings).upload(c, cipher_id, attachment_id, data)


@router.get(
    "/ciphers/{cipher_id}/attachment/{attachment_id}", summary="Gegevens en download-URL van een bijlage"
)
async def get_attachment(
    cipher_id: UUID, attachment_id: UUID, c: Caller, db: DbSession, settings: SettingsDep
) -> dict[str, Any]:
    a = await AttachmentService(db, settings).get(c, cipher_id, attachment_id)
    return attachment_json(a, AttachmentLinks(settings))


@router.get(
    "/ciphers/{cipher_id}/attachment/{attachment_id}/download",
    summary="Versleutelde inhoud (zonder login, met het token uit de download-URL)",
)
async def download_attachment(
    cipher_id: UUID, attachment_id: UUID, token: str, db: DbSession, settings: SettingsDep
) -> Response:
    data = await AttachmentService(db, settings).download(cipher_id, attachment_id, token)
    return Response(
        data,
        media_type="application/octet-stream",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/ciphers/{cipher_id}/attachment/{attachment_id}", summary="Bijlage verwijderen")
@router.post("/ciphers/{cipher_id}/attachment/{attachment_id}/delete", include_in_schema=False)
async def delete_attachment(
    cipher_id: UUID, attachment_id: UUID, c: Caller, db: DbSession, settings: SettingsDep
) -> dict[str, Any]:
    cipher = await AttachmentService(db, settings).delete(c, cipher_id, attachment_id)
    return {"cipher": await _cipher_out(c, db, settings, cipher), "object": "deleteAttachment"}


@router.post("/ciphers/{cipher_id}/attachment", include_in_schema=False)
async def legacy_attachment(cipher_id: UUID, c: Caller) -> None:
    raise InvalidOperationError("Werk je Bitwarden-app bij: deze manier van bijlagen uploaden is verouderd")


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
