from uuid import UUID

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import CurrentPrincipal, DbSession, SettingsDep
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.schemas.notes import NoteCreate, NoteImport, NoteOut, NoteUpdate, NoteVersionOut
from app.services.notes import NotesService, StaleNoteError

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
