"""Notities (fase 7). De inhoud is een EncString die de server niet kan lezen."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel
from app.schemas.vault import ENC_STRING_TYPE2

# Ruim genoeg voor een lange notitie (een EncString is ~1,4x de tekst), klein genoeg om
# geen opslagplaats voor grote bestanden te worden.
MAX_NOTE_CONTENT = 2_000_000


class NoteContent(BaseModel):
    content: str = Field(max_length=MAX_NOTE_CONTENT, description="EncString (type 2) met de notitie")

    @field_validator("content")
    @classmethod
    def _enc_string(cls, v: str) -> str:
        if not ENC_STRING_TYPE2.match(v):
            raise ValueError("verwacht een EncString van type 2 (AES-256-CBC-HMAC)")
        return v


class NoteCreate(NoteContent):
    pass


class NoteUpdate(NoteContent):
    base_revision: int = Field(ge=1, description="Revisie waarop de wijziging gebaseerd is")


class NoteOut(ORMModel):
    id: UUID
    content: str
    revision: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class NoteVersionOut(ORMModel):
    revision: int
    content: str
    saved_at: datetime


class NoteImport(BaseModel):
    """Meerdere notities in één keer, bv. een geïmporteerde Obsidian-kluis."""

    notes: list[NoteContent] = Field(min_length=1, max_length=200)
