"""Links, collecties en tags (fase 10)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel

COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"
MAX_URL = 8192
MAX_TAGS = 50


def _clean_tags(tags: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for t in tags:
        t = " ".join(t.split()).lstrip("#")[:100]
        if t:
            seen.setdefault(t, None)
    return list(seen)


def _check_url(v: str) -> str:
    v = v.strip()
    scheme = v.split(":", 1)[0].lower() if ":" in v else ""
    # Geen javascript:/data:-links: die zouden in de webinterface code kunnen uitvoeren.
    if scheme not in {"http", "https", "ftp", "file", "mailto"}:
        raise ValueError("verwacht een http(s)-adres")
    return v


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field("", max_length=10_000)
    color: str = Field("#0ea5e9", pattern=COLOR_PATTERN)
    parent_id: UUID | None = None


class CollectionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=10_000)
    color: str | None = Field(None, pattern=COLOR_PATTERN)
    # Expliciet null = naar het hoogste niveau; weglaten = niet verplaatsen.
    parent_id: UUID | None = None


class CollectionOut(ORMModel):
    id: UUID
    parent_id: UUID | None
    name: str
    description: str
    color: str
    created_at: datetime
    updated_at: datetime
    link_count: int = 0


class TagOut(BaseModel):
    id: UUID
    name: str
    link_count: int = 0


class TagRename(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class LinkIn(BaseModel):
    url: str = Field(min_length=1, max_length=MAX_URL)
    name: str = Field("", max_length=2000)
    description: str = Field("", max_length=20_000)
    collection_id: UUID | None = None
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    pinned: bool = False

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _check_url(v)

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        return _clean_tags(v)


class LinkUpdate(BaseModel):
    url: str | None = Field(None, min_length=1, max_length=MAX_URL)
    name: str | None = Field(None, max_length=2000)
    description: str | None = Field(None, max_length=20_000)
    collection_id: UUID | None = None
    tags: list[str] | None = Field(None, max_length=MAX_TAGS)
    pinned: bool | None = None

    @field_validator("url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return None if v is None else _check_url(v)

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _clean_tags(v)


class ArchiveOut(ORMModel):
    kind: str
    media_type: str
    size: int
    created_at: datetime


class LinkOut(ORMModel):
    id: UUID
    url: str
    name: str
    description: str
    collection_id: UUID | None
    pinned: bool
    source: str
    tags: list[str]
    archives: list[ArchiveOut]
    created_at: datetime
    updated_at: datetime
    imported_at: datetime | None

    @field_validator("tags", mode="before")
    @classmethod
    def _tag_names(cls, v: object) -> object:
        return [t if isinstance(t, str) else t.name for t in v]  # type: ignore[union-attr]


class LinkPage(BaseModel):
    items: list[LinkOut]
    total: int
    limit: int
    offset: int


LinkSort = Literal["newest", "oldest", "name", "name_desc"]


class LinkImportIn(BaseModel):
    format: Literal["linkwarden", "bookmarks"]
    # De inhoud van het bestand (JSON van Linkwarden of HTML-bladwijzers van een browser).
    data: str = Field(min_length=1)


class ImportResult(BaseModel):
    collections_created: int
    links_created: int
    links_updated: int
    tags_created: int
    archives_added: int
    skipped: int
    warnings: list[str] = []
