from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentPrincipal, DbSession, SettingsDep
from app.core.errors import NotFoundError
from app.schemas.vault import VaultDeviceOut, VaultEnroll, VaultStatusOut
from app.services.vault import VaultService
from app.services.vault_auth import device_type_name

router = APIRouter(prefix="/me/vault", tags=["vault"])


def _device_out(d) -> VaultDeviceOut:
    return VaultDeviceOut.model_validate(d).model_copy(update={"type_name": device_type_name(d.type)})


async def _status(p: CurrentPrincipal, db: DbSession, settings: SettingsDep) -> VaultStatusOut:
    if not settings.vault_enabled:
        raise NotFoundError("De kluis is uitgeschakeld op deze server")
    st = await VaultService(db).status(p)
    a = st.account
    return VaultStatusOut(
        enrolled=a is not None,
        email=st.email,
        blocked_reason=st.blocked_reason,
        server_url=settings.public_url,
        kdf=a.kdf_type if a else None,
        kdf_iterations=a.kdf_iterations if a else None,
        kdf_memory=a.kdf_memory if a else None,
        kdf_parallelism=a.kdf_parallelism if a else None,
        enrolled_at=a.created_at if a else None,
        revision_date=a.revision_date if a else None,
        item_count=st.item_count,
        trash_count=st.trash_count,
        folder_count=st.folder_count,
        devices=[_device_out(d) for d in st.devices],
    )


@router.get("", response_model=VaultStatusOut, summary="Status van mijn kluis")
async def vault_status(p: CurrentPrincipal, db: DbSession, settings: SettingsDep) -> VaultStatusOut:
    return await _status(p, db, settings)


@router.post(
    "",
    response_model=VaultStatusOut,
    status_code=status.HTTP_201_CREATED,
    summary="Kluis activeren met client-side afgeleide sleutels",
)
async def enroll(
    body: VaultEnroll, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> VaultStatusOut:
    if not settings.vault_enabled:
        raise NotFoundError("De kluis is uitgeschakeld op deze server")
    await VaultService(db).enroll(p, body)
    return await _status(p, db, settings)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Kluis met alle items verwijderen")
async def reset(p: CurrentPrincipal, db: DbSession) -> Response:
    await VaultService(db).reset(p)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/devices/{device_id}/revoke", response_model=VaultDeviceOut, summary="Apparaat afmelden van mijn kluis"
)
async def revoke_device(device_id: UUID, p: CurrentPrincipal, db: DbSession) -> VaultDeviceOut:
    return _device_out(await VaultService(db).revoke_device(p, device_id))
