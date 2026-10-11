from uuid import UUID

from fastapi import APIRouter, Header, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import CurrentPrincipal, DbSession, SettingsDep
from app.core.config import Settings
from app.core.errors import InvalidOperationError, NotFoundError, VaultXError
from app.schemas.notes import (
    NoteCreate,
    NoteFileOut,
    NoteFilesOut,
    NoteImport,
    NoteOut,
    NoteUpdate,
    NoteVersionOut,
)
from app.services.notes import ENC_FILE_HEADER, NotesService, StaleNoteError

router = APIRouter(prefix="/me/notes", tags=["notes"])


def _enabled(settings: Settings) -> None:
    if not settings.vault_enabled:
        raise NotFoundError("De kluis is uitgeschakeld op deze server")


@router.get("", response_model=list[NoteOut], summary="Al mijn notities (versleuteld), ook in de prullenbak")
async def list_notes(p: CurrentPrincipal, db: DbSession, settings: SettingsDep) -> list[NoteOut]:
    _enabled(settings)
    return [NoteOut.model_validate(n) for n in await NotesService(db).list(p)]


@router.post("", response_model=NoteOut, status_code=status.HTTP_201_CREATED, summary="Notitie aanmaken")
async def create_note(body: NoteCreate, p: CurrentPrincipal, db: DbSession, settings: SettingsDep) -> NoteOut:
    _enabled(settings)
    return NoteOut.model_validate(await NotesService(db).create(p, body))


@router.post(
    "/import",
    response_model=list[NoteOut],
    status_code=status.HTTP_201_CREATED,
    summary="Meerdere notities tegelijk aanmaken (import)",
)
async def import_notes(
    body: NoteImport, p: CurrentPrincipal, db: DbSession, settings: SettingsDep
) -> list[NoteOut]:
    _enabled(settings)
    return [NoteOut.model_validate(n) for n in await NotesService(db).import_many(p, body.notes)]


MiB = 1024 * 1024


class FileTooLargeError(VaultXError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "file_too_large"


def _max_blob(settings: Settings) -> int:
    # Versleuteling voegt een kop en hoogstens één blok padding toe.
    return settings.vault_note_file_max_mb * MiB + ENC_FILE_HEADER + 16


@router.get("/files", response_model=NoteFilesOut, summary="Mijn bijlagen (namen versleuteld, zonder inhoud)")
async def list_files(p: CurrentPrincipal, db: DbSession, settings: SettingsDep) -> NoteFilesOut:
    _enabled(settings)
    files, used = await NotesService(db).list_files(p)
    return NoteFilesOut(
        files=[NoteFileOut.model_validate(f) for f in files],
        used_bytes=used,
        quota_bytes=settings.vault_note_storage_mb * MiB,
        max_file_bytes=settings.vault_note_file_max_mb * MiB,
    )


@router.post(
    "/files",
    response_model=NoteFileOut,
    status_code=status.HTTP_201_CREATED,
    summary="Versleutelde bijlage opladen",
    description="Body: de EncArrayBuffer als application/octet-stream. "
    "Header X-VaultX-File-Name: de versleutelde bestandsnaam (EncString).",
)
async def upload_file(
    request: Request,
    p: CurrentPrincipal,
    db: DbSession,
    settings: SettingsDep,
    x_vaultx_file_name: str = Header(min_length=1),
) -> NoteFileOut:
    _enabled(settings)
    limit = _max_blob(settings)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise FileTooLargeError(f"Een bijlage mag hoogstens {settings.vault_note_file_max_mb} MB zijn")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise FileTooLargeError(f"Een bijlage mag hoogstens {settings.vault_note_file_max_mb} MB zijn")
        chunks.append(chunk)
    if not total:
        raise InvalidOperationError("Leeg bestand")
    f = await NotesService(db).upload(
        p, x_vaultx_file_name, b"".join(chunks), settings.vault_note_storage_mb * MiB
    )
    return NoteFileOut.model_validate(f)


@router.get(
    "/files/{file_id}",
    summary="Versleutelde bijlage ophalen",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_file(file_id: UUID, p: CurrentPrincipal, db: DbSession, settings: SettingsDep) -> Response:
    _enabled(settings)
    f = await NotesService(db).file(p, file_id)
    # Een bijlage verandert nooit (nieuwe inhoud = nieuw id), dus de browser mag ze bijhouden.
    return Response(
        content=f.data,
        media_type="application/octet-stream",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Bijlage verwijderen")
async def delete_file(file_id: UUID, p: CurrentPrincipal, db: DbSession) -> Response:
    await NotesService(db).delete_file(p, file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{note_id}",
    response_model=NoteOut,
    summary="Notitie bewaren",
    responses={409: {"description": "Intussen elders gewijzigd; `current` bevat de actuele versie"}},
)
async def update_note(note_id: UUID, body: NoteUpdate, p: CurrentPrincipal, db: DbSession):
    try:
        note = await NotesService(db).update(p, note_id, body, body.base_revision)
    except StaleNoteError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": exc.code,
                "detail": exc.message,
                "current": NoteOut.model_validate(exc.current).model_dump(mode="json"),
            },
        )
    return NoteOut.model_validate(note)


@router.post("/{note_id}/trash", response_model=NoteOut, summary="Naar de prullenbak")
async def trash_note(note_id: UUID, p: CurrentPrincipal, db: DbSession) -> NoteOut:
    return NoteOut.model_validate(await NotesService(db).trash(p, note_id))


@router.post("/{note_id}/restore", response_model=NoteOut, summary="Terugzetten uit de prullenbak")
async def restore_note(note_id: UUID, p: CurrentPrincipal, db: DbSession) -> NoteOut:
    return NoteOut.model_validate(await NotesService(db).restore(p, note_id))


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Definitief verwijderen")
async def delete_note(note_id: UUID, p: CurrentPrincipal, db: DbSession) -> Response:
    await NotesService(db).delete(p, note_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{note_id}/versions", response_model=list[NoteVersionOut], summary="Eerdere versies")
async def note_versions(note_id: UUID, p: CurrentPrincipal, db: DbSession) -> list[NoteVersionOut]:
    return [NoteVersionOut.model_validate(v) for v in await NotesService(db).history(p, note_id)]
