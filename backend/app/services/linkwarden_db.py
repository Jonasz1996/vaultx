"""Rechtstreeks lezen uit een Linkwarden-database (PostgreSQL, Prisma-schema van Linkwarden 2.x).

Handig als Linkwarden zelf niet meer draait: neemt collecties met hun nesting, links, tags,
vastgepinde links en datums mee, en met de map met Linkwarden-bestanden (STORAGE_FOLDER,
standaard `data`) ook de screenshots, pdf's, leesbare versies en volledige pagina's.
Er wordt enkel gelezen; de Linkwarden-database blijft ongewijzigd.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from app.core.errors import VaultXError
from app.services.link_import import ImportArchive, ImportCollection, ImportData, ImportLink, parse_datetime

# Linkwarden-kolom -> soort archief in VaultX
ARCHIVE_FIELDS = {
    "image": "screenshot",
    "pdf": "pdf",
    "readable": "readable",
    "monolith": "page",
    "preview": "preview",
}
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".json": "application/json",
    ".html": "text/html",
}


@dataclass
class LinkwardenUser:
    id: int
    email: str | None
    username: str | None
    name: str | None
    collections: int


def _dsn(dsn: str) -> str:
    # Ook de SQLAlchemy-schrijfwijze van VAULTX_DATABASE_URL aanvaarden.
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


class LinkwardenDatabase:
    def __init__(self, dsn: str) -> None:
        try:
            self.conn = psycopg.connect(_dsn(dsn), connect_timeout=15)
        except psycopg.Error as exc:
            raise VaultXError(f"Kan niet verbinden met de Linkwarden-database: {exc}") from exc
        self.conn.read_only = True
        # Archiefbestanden die in de database staan maar niet in de opgegeven map.
        self.missing_files = 0

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> LinkwardenDatabase:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _rows(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        with self.conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(sql, params)  # type: ignore[arg-type]
            return list(cur.fetchall())

    def _columns(self, table: str) -> set[str]:
        rows = self._rows(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (table,),
        )
        return {r["column_name"] for r in rows}

    def _has_table(self, table: str) -> bool:
        return bool(self._columns(table))

    def users(self) -> list[LinkwardenUser]:
        if not self._has_table("Link") or not self._has_table("Collection"):
            raise VaultXError('Dit is geen Linkwarden-database: de tabellen "Link" en "Collection" ontbreken')
        rows = self._rows(
            "SELECT u.id, u.email, u.username, u.name, "
            '(SELECT count(*) FROM "Collection" c WHERE c."ownerId" = u.id) AS collections '
            'FROM "User" u ORDER BY u.id'
        )
        return [LinkwardenUser(**r) for r in rows]

    def pick_user(self, wanted: str | None) -> LinkwardenUser:
        users = self.users()
        if wanted:
            w = wanted.strip().lower()
            hits = [u for u in users if w in {str(u.id), (u.email or "").lower(), (u.username or "").lower()}]
        else:
            # Zonder --from: de enige gebruiker, of de enige met collecties.
            hits = users if len(users) == 1 else [u for u in users if u.collections]
        if len(hits) != 1:
            listing = ", ".join(
                f"{u.id}: {u.username or u.email} ({u.collections} collecties)" for u in users
            )
            raise VaultXError(f"Kies een Linkwarden-gebruiker met --from. Gevonden: {listing or 'geen'}")
        return hits[0]

    def read(self, user: LinkwardenUser, files: Path | None) -> ImportData:
        out = ImportData(source="linkwarden")
        col_cols = self._columns("Collection")
        fields = ["id", "name", "description", "color", "parentId", "createdAt"]
        cols = self._rows(
            "SELECT " + ", ".join(f'"{f}"' for f in fields if f in col_cols) + ' FROM "Collection" '  # noqa: S608
            'WHERE "ownerId" = %s ORDER BY id',
            (user.id,),
        )
        ids = {c["id"] for c in cols}
        for c in cols:
            parent = c.get("parentId")
            out.collections.append(
                ImportCollection(
                    key=str(c["id"]),
                    name=(c.get("name") or "Naamloos").strip()[:255] or "Naamloos",
                    parent=str(parent) if parent in ids else None,
                    description=c.get("description") or "",
                    color=c.get("color")
                    if isinstance(c.get("color"), str) and len(c["color"]) == 7
                    else None,
                    created_at=parse_datetime(c.get("createdAt")),
                )
            )
        if not ids:
            return out

        link_cols = self._columns("Link")
        wanted = [
            "id",
            "name",
            "type",
            "description",
            "url",
            "textContent",
            "collectionId",
            "createdAt",
            "importDate",
        ]
        wanted += [f for f in ARCHIVE_FIELDS if f in link_cols]
        links = self._rows(
            "SELECT " + ", ".join(f'"{f}"' for f in wanted if f in link_cols) + ' FROM "Link" '  # noqa: S608
            'WHERE "collectionId" = ANY(%s) ORDER BY id',
            (list(ids),),
        )
        link_ids = [link["id"] for link in links]

        tags: dict[int, list[str]] = {}
        if self._has_table("_LinkToTag"):
            for r in self._rows(
                'SELECT lt."A" AS link, t.name FROM "_LinkToTag" lt JOIN "Tag" t ON t.id = lt."B" '
                'WHERE lt."A" = ANY(%s) ORDER BY t.name',
                (link_ids,),
            ):
                tags.setdefault(r["link"], []).append(r["name"])

        pinned: set[int] = set()
        for table in ("_PinnedLinks", "_LinkToUser"):
            if self._has_table(table):
                pinned |= {
                    r["link"]
                    for r in self._rows(f'SELECT "A" AS link FROM "{table}" WHERE "B" = %s', (user.id,))  # noqa: S608
                }
                break

        root = files.resolve() if files else None
        with_archives = 0

        def loader(rel: str):
            def load() -> bytes | None:
                assert root is not None
                for base in (root, root / "data"):
                    path = (base / rel).resolve()
                    if path.is_relative_to(root) and path.is_file():
                        return path.read_bytes()
                self.missing_files += 1
                return None

            return load

        for link in links:
            url = link.get("url")
            if not url:
                out.warnings.append(
                    f"Overgeslagen: '{link.get('name') or link['id']}' is een opgeladen bestand zonder URL"
                )
                continue
            archives: list[ImportArchive] = []
            for field, kind in ARCHIVE_FIELDS.items():
                rel = link.get(field)
                if not isinstance(rel, str) or not rel.startswith("archives/"):
                    continue  # leeg, "unavailable" of "pending"
                media = MEDIA_TYPES.get(Path(rel).suffix.lower())
                if media is None:
                    continue
                if root is not None:
                    archives.append(ImportArchive(kind=kind, media_type=media, load=loader(rel)))
            if any(link.get(f) and str(link.get(f)).startswith("archives/") for f in ARCHIVE_FIELDS):
                with_archives += 1
            out.links.append(
                ImportLink(
                    url=url,
                    name=link.get("name") or "",
                    description=link.get("description") or "",
                    collection=str(link["collectionId"]),
                    tags=tags.get(link["id"], []),
                    pinned=link["id"] in pinned,
                    created_at=parse_datetime(link.get("importDate") or link.get("createdAt")),
                    text_content=link.get("textContent") or None,
                    archives=archives,
                )
            )
        if with_archives and root is None:
            out.warnings.append(
                f"{with_archives} links hebben archieven in Linkwarden (screenshot, pdf, ...). "
                "Geef --files mee met de map van Linkwarden (STORAGE_FOLDER, standaard data) "
                "om ze mee te nemen."
            )
        return out
