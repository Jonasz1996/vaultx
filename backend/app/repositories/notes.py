from uuid import UUID

from sqlalchemy import delete, func, select

from app.models import VaultNote, VaultNoteVersion
from app.repositories.base import Repository


class VaultNoteRepository(Repository[VaultNote]):
    model = VaultNote

    async def for_user(self, user_id: UUID) -> list[VaultNote]:
        rows = await self.db.scalars(
            select(VaultNote).where(VaultNote.user_id == user_id).order_by(VaultNote.created_at)
        )
        return list(rows)

    async def owned(self, user_id: UUID, note_id: UUID, *, lock: bool = False) -> VaultNote | None:
        stmt = select(VaultNote).where(VaultNote.id == note_id, VaultNote.user_id == user_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return await self.db.scalar(stmt)

    async def counts(self, user_id: UUID) -> tuple[int, int]:
        """(actieve notities, notities in de prullenbak)."""
        rows = await self.db.execute(
            select(VaultNote.deleted_at.is_(None), func.count())
            .where(VaultNote.user_id == user_id)
            .group_by(VaultNote.deleted_at.is_(None))
        )
        counts = {bool(active): int(n) for active, n in rows}
        return counts.get(True, 0), counts.get(False, 0)


class VaultNoteVersionRepository(Repository[VaultNoteVersion]):
    model = VaultNoteVersion

    async def for_note(self, note_id: UUID) -> list[VaultNoteVersion]:
        rows = await self.db.scalars(
            select(VaultNoteVersion)
            .where(VaultNoteVersion.note_id == note_id)
            .order_by(VaultNoteVersion.revision.desc())
        )
        return list(rows)

    async def latest(self, note_id: UUID) -> VaultNoteVersion | None:
        return await self.db.scalar(
            select(VaultNoteVersion)
            .where(VaultNoteVersion.note_id == note_id)
            .order_by(VaultNoteVersion.revision.desc())
            .limit(1)
        )

    async def prune(self, note_id: UUID, keep: int) -> None:
        """Houdt enkel de `keep` recentste versies over."""
        keep_ids = (
            select(VaultNoteVersion.id)
            .where(VaultNoteVersion.note_id == note_id)
            .order_by(VaultNoteVersion.revision.desc())
            .limit(keep)
        )
        await self.db.execute(
            delete(VaultNoteVersion).where(
                VaultNoteVersion.note_id == note_id, VaultNoteVersion.id.not_in(keep_ids)
            )
        )
