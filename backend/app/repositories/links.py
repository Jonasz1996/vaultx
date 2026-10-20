from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import undefer

from app.models import Link, LinkArchive, LinkCollection, LinkTag, link_tag_map
from app.repositories.base import Repository


class LinkCollectionRepository(Repository[LinkCollection]):
    model = LinkCollection

    async def for_user(self, user_id: UUID) -> list[LinkCollection]:
        rows = await self.db.scalars(
            select(LinkCollection).where(LinkCollection.user_id == user_id).order_by(LinkCollection.name)
        )
        return list(rows)

    async def owned(self, user_id: UUID, collection_id: UUID) -> LinkCollection | None:
        return await self.db.scalar(
            select(LinkCollection).where(
                LinkCollection.id == collection_id, LinkCollection.user_id == user_id
            )
        )

    async def link_counts(self, user_id: UUID) -> dict[UUID | None, int]:
        rows = await self.db.execute(
            select(Link.collection_id, func.count())
            .where(Link.user_id == user_id)
            .group_by(Link.collection_id)
        )
        return {cid: int(n) for cid, n in rows}


class LinkTagRepository(Repository[LinkTag]):
    model = LinkTag

    async def with_counts(self, user_id: UUID) -> list[tuple[LinkTag, int]]:
        rows = await self.db.execute(
            select(LinkTag, func.count(link_tag_map.c.link_id))
            .outerjoin(link_tag_map, link_tag_map.c.tag_id == LinkTag.id)
            .where(LinkTag.user_id == user_id)
            .group_by(LinkTag.id)
            .order_by(func.lower(LinkTag.name))
        )
        return [(t, int(n)) for t, n in rows]

    async def owned(self, user_id: UUID, tag_id: UUID) -> LinkTag | None:
        return await self.db.scalar(select(LinkTag).where(LinkTag.id == tag_id, LinkTag.user_id == user_id))

    async def by_names(self, user_id: UUID, names: list[str]) -> dict[str, LinkTag]:
        if not names:
            return {}
        rows = await self.db.scalars(
            select(LinkTag).where(LinkTag.user_id == user_id, LinkTag.name.in_(names))
        )
        return {t.name: t for t in rows}

    async def links_with(self, tag_id: UUID) -> list[Link]:
        rows = await self.db.scalars(
            select(Link)
            .join(link_tag_map, link_tag_map.c.link_id == Link.id)
            .where(link_tag_map.c.tag_id == tag_id)
        )
        return list(rows)


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class LinkRepository(Repository[Link]):
    model = Link

    async def owned(self, user_id: UUID, link_id: UUID) -> Link | None:
        return await self.db.scalar(select(Link).where(Link.id == link_id, Link.user_id == user_id))

    def search(
        self,
        user_id: UUID,
        *,
        q: str | None = None,
        collection_id: UUID | None = None,
        unsorted: bool = False,
        tag: str | None = None,
        pinned: bool | None = None,
        sort: str = "newest",
    ) -> Select:
        stmt = select(Link).where(Link.user_id == user_id)
        if collection_id is not None:
            stmt = stmt.where(Link.collection_id == collection_id)
        elif unsorted:
            stmt = stmt.where(Link.collection_id.is_(None))
        if pinned is not None:
            stmt = stmt.where(Link.pinned.is_(pinned))
        if tag:
            stmt = stmt.where(
                Link.id.in_(
                    select(link_tag_map.c.link_id)
                    .join(LinkTag, LinkTag.id == link_tag_map.c.tag_id)
                    .where(LinkTag.user_id == user_id, LinkTag.name == tag)
                )
            )
        for word in (q or "").split():
            pattern = _like(word)
            stmt = stmt.where(
                or_(
                    Link.name.ilike(pattern),
                    Link.url.ilike(pattern),
                    Link.description.ilike(pattern),
                    Link.text_content.ilike(pattern),
                    Link.id.in_(
                        select(link_tag_map.c.link_id)
                        .join(LinkTag, LinkTag.id == link_tag_map.c.tag_id)
                        .where(LinkTag.user_id == user_id, LinkTag.name.ilike(pattern))
                    ),
                )
            )
        order = {
            "newest": (Link.created_at.desc(), Link.id),
            "oldest": (Link.created_at.asc(), Link.id),
            "name": (func.lower(Link.name).asc(), Link.id),
            "name_desc": (func.lower(Link.name).desc(), Link.id),
        }[sort]
        return stmt.order_by(*order)

    async def in_collection(self, user_id: UUID, collection_id: UUID | None) -> list[Link]:
        cond = Link.collection_id.is_(None) if collection_id is None else Link.collection_id == collection_id
        return list(await self.db.scalars(select(Link).where(Link.user_id == user_id, cond)))

    async def all_for_user(self, user_id: UUID, *, with_text: bool = False) -> list[Link]:
        stmt = select(Link).where(Link.user_id == user_id).order_by(Link.created_at, Link.id)
        if with_text:
            stmt = stmt.options(undefer(Link.text_content))
        rows = await self.db.scalars(stmt)
        return list(rows)

    async def archive(self, link_id: UUID, kind: str) -> LinkArchive | None:
        return await self.db.scalar(
            select(LinkArchive)
            .where(LinkArchive.link_id == link_id, LinkArchive.kind == kind)
            .options(undefer(LinkArchive.data))
        )
