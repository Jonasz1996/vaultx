import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from starlette.concurrency import run_in_threadpool

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.links import (
    CollectionIn,
    CollectionOut,
    CollectionUpdate,
    ImportResult,
    LinkImportIn,
    LinkIn,
    LinkOut,
    LinkPage,
    LinkSort,
    LinkUpdate,
    TagOut,
    TagRename,
)
from app.services.link_import import LinkImporter, parse_upload
from app.services.links import LinksService

router = APIRouter(prefix="/links", tags=["links"])

ArchiveKind = Literal["screenshot", "pdf", "readable", "page", "preview"]

# Een gearchiveerde pagina is vreemde HTML: zonder scripts, formulieren of eigen origin tonen.
# Enkel ingebedde (data:) bronnen, zodat openen ook niets naar buiten lekt.
# Welk bestandstype elke soort archief mag hebben. Een archief met een ander type (bv. HTML
# als "screenshot" uit een gemanipuleerde Linkwarden-database) wordt enkel als download gegeven,
# anders draait die HTML als VaultX zelf in de browser.
ARCHIVE_TYPES = {
    "screenshot": {"image/png", "image/jpeg", "image/webp"},
    "preview": {"image/png", "image/jpeg", "image/webp"},
    "pdf": {"application/pdf"},
    "readable": {"application/json"},
    "page": {"text/html"},
}

PAGE_CSP = (
    "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline' data:; "
    "font-src data:; media-src data:"
)


def _collection_out(c, counts) -> CollectionOut:
    return CollectionOut.model_validate(c).model_copy(update={"link_count": counts.get(c.id, 0)})


@router.get(
    "/collections", response_model=list[CollectionOut], summary="Mijn collecties (plat, met parent_id)"
)
async def list_collections(p: CurrentPrincipal, db: DbSession) -> list[CollectionOut]:
    cols, counts = await LinksService(db).collections(p)
    return [_collection_out(c, counts) for c in cols]


@router.post(
    "/collections",
    response_model=CollectionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Collectie aanmaken",
)
async def create_collection(body: CollectionIn, p: CurrentPrincipal, db: DbSession) -> CollectionOut:
    return CollectionOut.model_validate(await LinksService(db).create_collection(p, body))


@router.patch(
    "/collections/{collection_id}", response_model=CollectionOut, summary="Collectie wijzigen of verplaatsen"
)
async def update_collection(
    collection_id: UUID, body: CollectionUpdate, p: CurrentPrincipal, db: DbSession
) -> CollectionOut:
    svc = LinksService(db)
    c = await svc.update_collection(p, collection_id, body)
    _, counts = await svc.collections(p)
    return _collection_out(c, counts)


@router.delete(
    "/collections/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Collectie verwijderen, met haar subcollecties en links",
)
async def delete_collection(collection_id: UUID, p: CurrentPrincipal, db: DbSession) -> Response:
    await LinksService(db).delete_collection(p, collection_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/tags", response_model=list[TagOut], summary="Mijn tags met het aantal links")
async def list_tags(p: CurrentPrincipal, db: DbSession) -> list[TagOut]:
    return [TagOut(id=t.id, name=t.name, link_count=n) for t, n in await LinksService(db).tags(p)]


@router.patch(
    "/tags/{tag_id}", response_model=TagOut, summary="Tag hernoemen (samenvoegen als de naam al bestaat)"
)
async def rename_tag(tag_id: UUID, body: TagRename, p: CurrentPrincipal, db: DbSession) -> TagOut:
    t = await LinksService(db).rename_tag(p, tag_id, body.name)
    return TagOut(id=t.id, name=t.name)


@router.delete(
    "/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Tag verwijderen (links blijven)"
)
async def delete_tag(tag_id: UUID, p: CurrentPrincipal, db: DbSession) -> Response:
    await LinksService(db).delete_tag(p, tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/import", response_model=ImportResult, summary="Links importeren (Linkwarden-JSON of bladwijzers)"
)
async def import_links(body: LinkImportIn, p: CurrentPrincipal, db: DbSession) -> ImportResult:
    # Een bestand van tientallen MB parsen mag de andere verzoeken niet ophouden.
    data = await run_in_threadpool(parse_upload, body.format, body.data)
    return await LinkImporter(db).apply(p.user.id, p.actor, data)


@router.get("/export", summary="Alle links en collecties als JSON")
async def export_links(p: CurrentPrincipal, db: DbSession) -> Response:
    body = json.dumps(await LinksService(db).export(p), ensure_ascii=False, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="vaultx-links.json"'},
    )


@router.get("", response_model=LinkPage, summary="Links zoeken en filteren")
async def list_links(
    p: CurrentPrincipal,
    db: DbSession,
    q: str | None = Query(
        None, max_length=500, description="Zoekt in titel, URL, beschrijving, tekst en tags"
    ),
    collection_id: UUID | None = None,
    unsorted: bool = Query(False, description="Enkel links zonder collectie"),
    tag: str | None = Query(None, max_length=100),
    pinned: bool | None = None,
    sort: LinkSort = "newest",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> LinkPage:
    items, total = await LinksService(db).search(
        p,
        q=q,
        collection_id=collection_id,
        unsorted=unsorted,
        tag=tag,
        pinned=pinned,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return LinkPage(items=[LinkOut.model_validate(i) for i in items], total=total, limit=limit, offset=offset)


@router.post("", response_model=LinkOut, status_code=status.HTTP_201_CREATED, summary="Link opslaan")
async def create_link(body: LinkIn, p: CurrentPrincipal, db: DbSession) -> LinkOut:
    return LinkOut.model_validate(await LinksService(db).create(p, body))


@router.get("/{link_id}", response_model=LinkOut, summary="Eén link")
async def get_link(link_id: UUID, p: CurrentPrincipal, db: DbSession) -> LinkOut:
    return LinkOut.model_validate(await LinksService(db).get(p, link_id))


@router.patch("/{link_id}", response_model=LinkOut, summary="Link wijzigen")
async def update_link(link_id: UUID, body: LinkUpdate, p: CurrentPrincipal, db: DbSession) -> LinkOut:
    return LinkOut.model_validate(await LinksService(db).update(p, link_id, body))


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Link verwijderen")
async def delete_link(link_id: UUID, p: CurrentPrincipal, db: DbSession) -> Response:
    await LinksService(db).delete(p, link_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{link_id}/archives/{kind}",
    summary="Gearchiveerde versie (screenshot, pdf, leesbare versie, volledige pagina)",
    response_class=Response,
)
async def get_archive(
    link_id: UUID, kind: ArchiveKind, p: CurrentPrincipal, db: DbSession, download: bool = False
) -> Response:
    a = await LinksService(db).archive(p, link_id, kind)
    headers = {
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
    }
    ext = {"screenshot": "png", "preview": "jpeg", "pdf": "pdf", "readable": "json", "page": "html"}[kind]
    if a.media_type == "image/jpeg":
        ext = "jpeg"
    if kind == "page":
        headers["Content-Security-Policy"] = PAGE_CSP
    media_type = a.media_type.split(";")[0].strip().lower()
    if media_type not in ARCHIVE_TYPES[kind]:
        media_type = "application/octet-stream"
        headers["Content-Security-Policy"] = PAGE_CSP
        download = True
    if download:
        headers["Content-Disposition"] = f'attachment; filename="link-{link_id}-{kind}.{ext}"'
    return Response(content=a.data, media_type=media_type, headers=headers)
