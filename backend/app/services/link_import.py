"""Links importeren (fase 10): Linkwarden-export, browserbladwijzers, VaultX-export en
rechtstreeks uit een Linkwarden-database (zie app.cli import-linkwarden).

Elke bron wordt eerst omgezet naar een neutrale structuur (ImportData) en daarna op één
manier in VaultX gezet. Opnieuw importeren is veilig: een collectie met dezelfde naam op
dezelfde plaats wordt hergebruikt en een link met dezelfde URL in dezelfde collectie wordt
aangevuld (tags, vastgepind, ontbrekende archieven) in plaats van verdubbeld.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidOperationError
from app.models import Link, LinkArchive, LinkCollection, LinkTag
from app.repositories import LinkCollectionRepository, LinkRepository
from app.schemas.links import ImportResult, _check_url, _clean_tags
from app.services.audit import AuditService
from app.services.links import LinksService
from app.services.principal import Actor

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
# Bij grote imports met archieven tussentijds committen, zodat niet alles in het geheugen blijft.
COMMIT_EVERY_BYTES = 200 * 1024 * 1024


@dataclass
class ImportArchive:
    kind: str
    media_type: str
    load: Callable[[], bytes | None]


@dataclass
class ImportCollection:
    key: str
    name: str
    parent: str | None = None
    description: str = ""
    color: str | None = None
    created_at: datetime | None = None


@dataclass
class ImportLink:
    url: str
    name: str = ""
    description: str = ""
    collection: str | None = None
    tags: list[str] = field(default_factory=list)
    pinned: bool = False
    created_at: datetime | None = None
    text_content: str | None = None
    archives: list[ImportArchive] = field(default_factory=list)


@dataclass
class ImportData:
    source: str
    collections: list[ImportCollection] = field(default_factory=list)
    links: list[ImportLink] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        if isinstance(value, int | float) or (isinstance(value, str) and value.isdigit()):
            n = float(value)
            # Bladwijzers: seconden, soms microseconden (Chrome) of milliseconden.
            while n > 1e11:
                n /= 1000
            return datetime.fromtimestamp(n, UTC)
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _color(value: Any) -> str | None:
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
            return value.lower()
        except ValueError:
            return None
    return None


# Linkwarden-export (Instellingen > Data > Export Data)


def parse_linkwarden_json(raw: str) -> ImportData:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidOperationError(f"Geen geldige JSON: {exc.msg} (regel {exc.lineno})") from exc
    if isinstance(data, dict) and data.get("format") == "vaultx-links":
        return parse_vaultx_json(data)
    cols = data.get("collections") if isinstance(data, dict) else data
    if not isinstance(cols, list):
        raise InvalidOperationError("Dit lijkt geen Linkwarden-export: er ontbreekt een lijst 'collections'")
    out = ImportData(source="linkwarden")
    ids = {str(c["id"]) for c in cols if isinstance(c, dict) and c.get("id") is not None}
    pinned_ids: set[str] = set()
    pinned_urls: set[str] = set()
    if isinstance(data, dict):
        for pl in data.get("pinnedLinks") or []:
            if isinstance(pl, dict):
                if pl.get("id") is not None:
                    pinned_ids.add(str(pl["id"]))
                elif pl.get("url"):
                    pinned_urls.add(pl["url"])
    for i, c in enumerate(cols):
        if not isinstance(c, dict):
            continue
        key = str(c["id"]) if c.get("id") is not None else f"#{i}"
        parent = str(c["parentId"]) if c.get("parentId") is not None else None
        out.collections.append(
            ImportCollection(
                key=key,
                name=(c.get("name") or "Naamloos").strip()[:255] or "Naamloos",
                parent=parent if parent in ids else None,
                description=c.get("description") or "",
                color=_color(c.get("color")),
                created_at=parse_datetime(c.get("createdAt")),
            )
        )
        for link in c.get("links") or []:
            if not isinstance(link, dict):
                continue
            url = link.get("url")
            if not url:
                out.warnings.append(
                    f"Overgeslagen: '{link.get('name') or '?'}' is een opgeladen bestand zonder URL"
                )
                continue
            out.links.append(
                ImportLink(
                    url=url,
                    name=link.get("name") or "",
                    description=link.get("description") or "",
                    collection=key,
                    tags=[t["name"] for t in link.get("tags") or [] if isinstance(t, dict) and t.get("name")],
                    pinned=str(link.get("id")) in pinned_ids or url in pinned_urls,
                    created_at=parse_datetime(link.get("importDate") or link.get("createdAt")),
                    text_content=link.get("textContent") or None,
                )
            )
    return out


def parse_vaultx_json(data: dict[str, Any]) -> ImportData:
    out = ImportData(source="vaultx")
    for c in data.get("collections") or []:
        out.collections.append(
            ImportCollection(
                key=str(c["id"]),
                name=(str(c.get("name") or "").strip()[:255]) or "Naamloos",
                parent=c.get("parent_id"),
                description=c.get("description") or "",
                color=_color(c.get("color")),
                created_at=parse_datetime(c.get("created_at")),
            )
        )
    for link in data.get("links") or []:
        out.links.append(
            ImportLink(
                url=link.get("url") or "",
                name=link.get("name") or "",
                description=link.get("description") or "",
                collection=link.get("collection_id"),
                tags=list(link.get("tags") or []),
                pinned=bool(link.get("pinned")),
                created_at=parse_datetime(link.get("created_at")),
            )
        )
    return out


# Browserbladwijzers (Netscape-formaat: Edge, Chrome, Firefox, ook Linkwarden en Pocket)

BOOKMARKS_ROOT = "Geïmporteerde bladwijzers"


class _BookmarkParser(HTMLParser):
    def __init__(self, out: ImportData) -> None:
        super().__init__(convert_charrefs=True)
        self.out = out
        self.stack: list[str] = ["root"]
        self.pending_folder: ImportCollection | None = None
        self.capture: str | None = None  # "folder" | "link" | "desc"
        self.text: list[str] = []
        self.current: ImportLink | None = None
        self.last_link: ImportLink | None = None
        self.counter = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): v or "" for k, v in attrs}
        self._end_desc()
        if tag == "h3":
            self.counter += 1
            self.pending_folder = ImportCollection(
                key=f"f{self.counter}",
                name="",
                parent=self.stack[-1],
                created_at=parse_datetime(a.get("add_date")),
            )
            self.capture, self.text = "folder", []
        elif tag == "dl":
            if self.pending_folder is not None:
                self.stack.append(self.pending_folder.key)
                self.pending_folder = None
            else:
                self.stack.append(self.stack[-1])
        elif tag == "a" and a.get("href"):
            tags = [t.strip() for t in (a.get("tags") or "").split(",")]
            self.current = ImportLink(
                url=a["href"],
                collection=self.stack[-1],
                tags=[t for t in tags if t],
                created_at=parse_datetime(a.get("add_date")),
            )
            self.capture, self.text = "link", []
        elif tag == "dd" and self.last_link is not None:
            self.capture, self.text = "desc", []

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self.capture == "folder" and self.pending_folder is not None:
            self.pending_folder.name = " ".join("".join(self.text).split())[:255] or "Naamloos"
            self.out.collections.append(self.pending_folder)
            self.capture = None
        elif tag == "a" and self.capture == "link" and self.current is not None:
            self.current.name = " ".join("".join(self.text).split())
            self.out.links.append(self.current)
            self.last_link, self.current, self.capture = self.current, None, None
        elif tag == "dl":
            self._end_desc()
            if len(self.stack) > 1:
                self.stack.pop()

    def handle_data(self, data: str) -> None:
        if self.capture:
            self.text.append(data)

    def _end_desc(self) -> None:
        if self.capture == "desc" and self.last_link is not None:
            self.last_link.description = "".join(self.text).strip()
            self.last_link = None
            self.capture = None

    def close(self) -> None:
        super().close()
        self._end_desc()


def parse_bookmarks_html(raw: str) -> ImportData:
    out = ImportData(source="bookmarks")
    parser = _BookmarkParser(out)
    parser.feed(raw)
    parser.close()
    if not out.links:
        raise InvalidOperationError("Geen bladwijzers gevonden; verwacht een HTML-export van je browser")
    # Alles komt onder één collectie, de mappen van de browser worden subcollecties.
    out.collections.insert(0, ImportCollection(key="root", name=BOOKMARKS_ROOT))
    return out


# In VaultX zetten


def _media_ok(a: ImportArchive) -> bool:
    return a.kind in {"screenshot", "pdf", "readable", "page", "preview"}


class LinkImporter:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.svc = LinksService(db)
        self.collections = LinkCollectionRepository(db)
        self.links = LinkRepository(db)

    async def apply(self, user_id: UUID, actor: Actor, data: ImportData) -> ImportResult:
        res = ImportResult(
            collections_created=0,
            links_created=0,
            links_updated=0,
            tags_created=0,
            archives_added=0,
            skipped=0,
            warnings=list(data.warnings),
        )
        now = datetime.now(UTC)

        existing = await self.collections.for_user(user_id)
        by_place: dict[tuple[UUID | None, str], LinkCollection] = {(c.parent_id, c.name): c for c in existing}
        known = {c.key for c in data.collections}
        resolved: dict[str, UUID] = {}
        pending = list(data.collections)
        # Ouders eerst; een cyclus of verdwenen ouder komt op het hoogste niveau.
        while pending:
            progressed = False
            rest = []
            for c in pending:
                if c.parent is not None and c.parent in known and c.parent not in resolved:
                    rest.append(c)
                    continue
                parent_id = resolved.get(c.parent) if c.parent else None
                hit = by_place.get((parent_id, c.name))
                if hit is None:
                    hit = self.collections.add(
                        LinkCollection(
                            user_id=user_id,
                            parent_id=parent_id,
                            name=c.name,
                            description=c.description,
                            color=c.color or "#0ea5e9",
                            created_at=c.created_at or now,
                            updated_at=now,
                        )
                    )
                    await self.db.flush()
                    by_place[(parent_id, c.name)] = hit
                    res.collections_created += 1
                resolved[c.key] = hit.id
                progressed = True
            if not progressed:
                for c in rest:
                    c.parent = None
            pending = rest

        current = {
            (link.collection_id, link.url): link
            for link in await self.links.all_for_user(user_id, with_text=True)
        }
        tag_cache: dict[str, LinkTag] = {}
        pending_bytes = 0
        for item in data.links:
            try:
                url = _check_url(item.url)
            except ValueError:
                res.skipped += 1
                res.warnings.append(f"Overgeslagen: ongeldig adres {item.url[:100]!r}")
                continue
            collection_id = resolved.get(item.collection) if item.collection else None
            tags, created = await self.svc.resolve_tags(user_id, _clean_tags(item.tags)[:50], tag_cache)
            res.tags_created += created
            link = current.get((collection_id, url))
            if link is None:
                link = Link(
                    user_id=user_id,
                    collection_id=collection_id,
                    url=url,
                    name=item.name.strip()[:2000],
                    description=item.description[:20_000],
                    pinned=item.pinned,
                    text_content=item.text_content,
                    source=data.source,
                    imported_at=now,
                    created_at=item.created_at or now,
                    updated_at=now,
                    tags=tags,
                    archives=[],
                )
                self.links.add(link)
                await self.db.flush()
                current[(collection_id, url)] = link
                res.links_created += 1
                have: set[str] = set()
            else:
                changed = False
                for t in tags:
                    if t not in link.tags:
                        link.tags.append(t)
                        changed = True
                if item.pinned and not link.pinned:
                    link.pinned = changed = True
                if not link.description and item.description:
                    link.description, changed = item.description[:20_000], True
                if not link.name and item.name:
                    link.name, changed = item.name.strip()[:2000], True
                if item.text_content and not link.text_content:
                    link.text_content, changed = item.text_content, True
                have = {a.kind for a in link.archives}
                added_before = res.archives_added
                pending_bytes += await self._archives(link, item, have, res)
                if changed or res.archives_added > added_before:
                    link.updated_at = now
                    res.links_updated += 1
                await self.db.flush()
                if pending_bytes > COMMIT_EVERY_BYTES:
                    await self.db.commit()
                    pending_bytes = 0
                continue
            pending_bytes += await self._archives(link, item, have, res)
            await self.db.flush()
            if pending_bytes > COMMIT_EVERY_BYTES:
                await self.db.commit()
                pending_bytes = 0

        await AuditService(self.db).record(
            "links.import",
            actor,
            target_type="user",
            target_id=user_id,
            details={
                "source": data.source,
                "collections_created": res.collections_created,
                "links_created": res.links_created,
                "links_updated": res.links_updated,
                "archives_added": res.archives_added,
                "skipped": res.skipped,
            },
        )
        await self.db.commit()
        if len(res.warnings) > 50:
            res.warnings = [*res.warnings[:50], f"... en nog {len(res.warnings) - 50} meldingen"]
        return res

    async def _archives(self, link: Link, item: ImportLink, have: set[str], res: ImportResult) -> int:
        added = 0
        for a in item.archives:
            if a.kind in have or not _media_ok(a):
                continue
            blob = a.load()
            if not blob:
                continue
            if len(blob) > MAX_ARCHIVE_BYTES:
                res.warnings.append(f"Archief te groot, overgeslagen: {a.kind} van {link.url[:100]}")
                continue
            if a.kind == "readable" and not link.text_content:
                try:
                    text = json.loads(blob).get("textContent")
                    if isinstance(text, str) and text.strip():
                        link.text_content = text
                except (ValueError, AttributeError):
                    pass
            arch = LinkArchive(
                link_id=link.id,
                kind=a.kind,
                media_type=a.media_type,
                size=len(blob),
                data=blob,
                created_at=datetime.now(UTC),
            )
            self.db.add(arch)
            await self.db.flush()
            # De bytes niet in de sessie houden; de link kent het archief via zijn soort.
            self.db.expunge(arch)
            have.add(a.kind)
            res.archives_added += 1
            added += len(blob)
        return added


def parse_upload(fmt: str, raw: str) -> ImportData:
    try:
        if fmt == "bookmarks":
            return parse_bookmarks_html(raw)
        return parse_linkwarden_json(raw)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        # Een bestand met een onverwachte vorm is een fout in de invoer, geen serverfout.
        raise InvalidOperationError(
            "Dit bestand heeft niet de verwachte vorm en kan niet geïmporteerd worden"
        ) from exc
