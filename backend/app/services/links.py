"""Links, collecties en tags (fase 10): een bladwijzerbeheerder zoals Linkwarden.

Alles is per gebruiker; delen met anderen komt in een latere batch. Gewone bewerkingen
van een link komen niet in de auditlog (net als bij notities), aanmaken, verwijderen,
collecties, tags en importeren wel.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidOperationError, NotFoundError
from app.models import Link, LinkArchive, LinkCollection, LinkTag
from app.repositories import LinkCollectionRepository, LinkRepository, LinkTagRepository
from app.schemas.links import CollectionIn, CollectionUpdate, LinkIn, LinkUpdate
from app.services.audit import AuditService
from app.services.principal import Actor, Principal

ARCHIVE_KINDS = ("screenshot", "pdf", "readable", "page", "preview")


class LinksService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.collections_repo = LinkCollectionRepository(db)
        self.tags_repo = LinkTagRepository(db)
        self.links = LinkRepository(db)

    async def _audit(self, actor: Actor, action: str, target_type: str, target_id: Any, **details) -> None:
        await AuditService(self.db).record(
            action, actor, target_type=target_type, target_id=target_id, details=details or None
        )

    # Collecties

    async def _collection(self, p: Principal, collection_id: UUID) -> LinkCollection:
        c = await self.collections_repo.owned(p.user.id, collection_id)
        if c is None:
            raise NotFoundError("Collectie niet gevonden")
        return c

    async def collections(self, p: Principal) -> tuple[list[LinkCollection], dict[UUID | None, int]]:
        return (
            await self.collections_repo.for_user(p.user.id),
            await self.collections_repo.link_counts(p.user.id),
        )

    async def create_collection(self, p: Principal, data: CollectionIn) -> LinkCollection:
        if data.parent_id is not None:
            await self._collection(p, data.parent_id)
        c = self.collections_repo.add(
            LinkCollection(
                user_id=p.user.id,
                parent_id=data.parent_id,
                name=data.name.strip(),
                description=data.description,
                color=data.color,
            )
        )
        await self.db.flush()
        await self._audit(p.actor, "links.collection.create", "link_collection", c.id, name=c.name)
        await self.db.commit()
        return c

    async def update_collection(
        self, p: Principal, collection_id: UUID, data: CollectionUpdate
    ) -> LinkCollection:
        c = await self._collection(p, collection_id)
        if "parent_id" in data.model_fields_set and data.parent_id != c.parent_id:
            if data.parent_id is not None:
                # Niet in zichzelf of in een eigen subcollectie hangen.
                all_cols = {x.id: x for x in await self.collections_repo.for_user(p.user.id)}
                node = all_cols.get(data.parent_id)
                if node is None:
                    raise NotFoundError("Bovenliggende collectie niet gevonden")
                while node is not None:
                    if node.id == c.id:
                        raise InvalidOperationError("Een collectie kan niet in zichzelf of een subcollectie")
                    node = all_cols.get(node.parent_id) if node.parent_id else None
            c.parent_id = data.parent_id
        if data.name is not None:
            c.name = data.name.strip()
        if data.description is not None:
            c.description = data.description
        if data.color is not None:
            c.color = data.color
        c.updated_at = datetime.now(UTC)
        await self.db.commit()
        return c

    async def delete_collection(self, p: Principal, collection_id: UUID) -> None:
        c = await self._collection(p, collection_id)
        await self._audit(p.actor, "links.collection.delete", "link_collection", c.id, name=c.name)
        await self.collections_repo.delete(c)
        await self.db.commit()

    # Tags

    async def tags(self, p: Principal) -> list[tuple[LinkTag, int]]:
        return await self.tags_repo.with_counts(p.user.id)

    async def resolve_tags(self, user_id: UUID, names: list[str], cache: dict[str, LinkTag] | None = None):
        """Bestaande tags opzoeken en ontbrekende aanmaken. Geeft (tags, aantal nieuw)."""
        cache = cache if cache is not None else {}
        missing = [n for n in names if n not in cache]
        cache.update(await self.tags_repo.by_names(user_id, missing))
        created = 0
        for n in names:
            if n not in cache:
                cache[n] = self.tags_repo.add(LinkTag(user_id=user_id, name=n, created_at=datetime.now(UTC)))
                created += 1
        return [cache[n] for n in names], created

    async def _tag(self, p: Principal, tag_id: UUID) -> LinkTag:
        t = await self.tags_repo.owned(p.user.id, tag_id)
        if t is None:
            raise NotFoundError("Tag niet gevonden")
        return t

    async def rename_tag(self, p: Principal, tag_id: UUID, name: str) -> LinkTag:
        """Hernoemen; bestaat de nieuwe naam al, dan worden beide tags samengevoegd."""
        tag = await self._tag(p, tag_id)
        name = " ".join(name.split()).lstrip("#")
        if not name:
            raise InvalidOperationError("Lege tagnaam")
        if name == tag.name:
            return tag
        other = (await self.tags_repo.by_names(p.user.id, [name])).get(name)
        old = tag.name
        if other is None:
            tag.name = name
            result = tag
        else:
            for link in await self.tags_repo.links_with(tag.id):
                if other not in link.tags:
                    link.tags.append(other)
                link.tags.remove(tag)
            await self.db.flush()
            await self.tags_repo.delete(tag)
            result = other
        await self._audit(p.actor, "links.tag.rename", "link_tag", result.id, old=old, new=name)
        await self.db.commit()
        return result

    async def delete_tag(self, p: Principal, tag_id: UUID) -> None:
        tag = await self._tag(p, tag_id)
        await self._audit(p.actor, "links.tag.delete", "link_tag", tag.id, name=tag.name)
        await self.tags_repo.delete(tag)
        await self.db.commit()

    # Links

    async def _link(self, p: Principal, link_id: UUID) -> Link:
        link = await self.links.owned(p.user.id, link_id)
        if link is None:
            raise NotFoundError("Link niet gevonden")
        return link

    async def search(
        self, p: Principal, *, limit: int, offset: int, **filters: Any
    ) -> tuple[list[Link], int]:
        return await self.links.page(self.links.search(p.user.id, **filters), limit=limit, offset=offset)

    async def get(self, p: Principal, link_id: UUID) -> Link:
        return await self._link(p, link_id)

    async def create(self, p: Principal, data: LinkIn) -> Link:
        if data.collection_id is not None:
            await self._collection(p, data.collection_id)
        tags, _ = await self.resolve_tags(p.user.id, data.tags)
        now = datetime.now(UTC)
        link = self.links.add(
            Link(
                user_id=p.user.id,
                collection_id=data.collection_id,
                url=data.url,
                name=data.name.strip(),
                description=data.description,
                pinned=data.pinned,
                source="manual",
                created_at=now,
                updated_at=now,
                tags=tags,
                archives=[],
            )
        )
        await self.db.flush()
        await self._audit(p.actor, "links.link.create", "link", link.id, url=link.url)
        await self.db.commit()
        return link

    async def update(self, p: Principal, link_id: UUID, data: LinkUpdate) -> Link:
        link = await self._link(p, link_id)
        if "collection_id" in data.model_fields_set and data.collection_id != link.collection_id:
            if data.collection_id is not None:
                await self._collection(p, data.collection_id)
            link.collection_id = data.collection_id
        if data.url is not None:
            link.url = data.url
        if data.name is not None:
            link.name = data.name.strip()
        if data.description is not None:
            link.description = data.description
        if data.pinned is not None:
            link.pinned = data.pinned
        if data.tags is not None:
            link.tags, _ = await self.resolve_tags(p.user.id, data.tags)
        link.updated_at = datetime.now(UTC)
        await self.db.commit()
        return link

    async def delete(self, p: Principal, link_id: UUID) -> None:
        link = await self._link(p, link_id)
        await self._audit(p.actor, "links.link.delete", "link", link.id, url=link.url)
        await self.links.delete(link)
        await self.db.commit()

    async def archive(self, p: Principal, link_id: UUID, kind: str) -> LinkArchive:
        link = await self._link(p, link_id)
        a = await self.links.archive(link.id, kind)
        if a is None:
            raise NotFoundError("Dit archief bestaat niet voor deze link")
        return a

    async def export(self, p: Principal) -> dict[str, Any]:
        cols = await self.collections_repo.for_user(p.user.id)
        links = await self.links.all_for_user(p.user.id)
        return {
            "format": "vaultx-links",
            "version": 1,
            "exported_at": datetime.now(UTC).isoformat(),
            "collections": [
                {
                    "id": str(c.id),
                    "parent_id": str(c.parent_id) if c.parent_id else None,
                    "name": c.name,
                    "description": c.description,
                    "color": c.color,
                    "created_at": c.created_at.isoformat(),
                }
                for c in cols
            ],
            "links": [
                {
                    "url": link.url,
                    "name": link.name,
                    "description": link.description,
                    "collection_id": str(link.collection_id) if link.collection_id else None,
                    "tags": [t.name for t in link.tags],
                    "pinned": link.pinned,
                    "created_at": link.created_at.isoformat(),
                }
                for link in links
            ],
        }
