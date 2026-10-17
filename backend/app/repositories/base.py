from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.core.db import Base

ModelT = TypeVar("ModelT", bound=Base)


class Repository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, id_: UUID) -> ModelT | None:
        return await self.db.get(self.model, id_)

    def add(self, obj: ModelT) -> ModelT:
        self.db.add(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.db.delete(obj)

    async def page(self, stmt: Select, *, limit: int, offset: int) -> tuple[list, int]:
        total = await self.db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
        rows = (await self.db.scalars(stmt.limit(limit).offset(offset))).all()
        return list(rows), int(total or 0)
