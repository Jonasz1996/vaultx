"""Notities in de persoonlijke kluis (fase 7).

De browser versleutelt elke notitie met de user key van de kluis; deze service
bewaart enkel blobs, bewaakt revisies tegen elkaar overschrijven en houdt een
beperkte versiegeschiedenis bij. Bewerken wordt niet in de auditlog gezet (de
editor bewaart automatisch, dat zou de log overspoelen); aanmaken, prullenbak,
terugzetten, verwijderen en importeren wel.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import VaultAccount, VaultNote, VaultNoteVersion
from app.repositories import VaultAccountRepository, VaultNoteRepository, VaultNoteVersionRepository
from app.schemas.notes import NoteContent
from app.services.audit import AuditService
from app.services.principal import Principal

# Een nieuwe versie in de geschiedenis hoogstens om de zoveel tijd (de editor bewaart
# tijdens het typen elke seconde), en niet meer dan zoveel versies per notitie.
VERSION_INTERVAL = timedelta(minutes=10)
MAX_VERSIONS = 50


class StaleNoteError(ConflictError):
    """Iemand anders (een ander tabblad of toestel) heeft de notitie intussen gewijzigd."""

    def __init__(self, current: VaultNote) -> None:
        super().__init__("Deze notitie is intussen elders gewijzigd")
        self.current = current


class NotesService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.accounts = VaultAccountRepository(db)
        self.notes = VaultNoteRepository(db)
        self.versions = VaultNoteVersionRepository(db)
        self.audit = AuditService(db)

    async def _account(self, p: Principal) -> VaultAccount:
        account = await self.accounts.get(p.user.id)
        if account is None:
            raise NotFoundError("Activeer eerst je kluis op de pagina Mijn kluis")
        return account

    async def _note(self, p: Principal, note_id: UUID, *, lock: bool = False) -> VaultNote:
        note = await self.notes.owned(p.user.id, note_id, lock=lock)
        if note is None:
            raise NotFoundError("Notitie niet gevonden")
        return note

    async def _record(self, p: Principal, action: str, note_id: UUID | None, **details) -> None:
        await self.audit.record(action, p.actor, target_type="vault_note", target_id=note_id, details=details)

    @staticmethod
    def _new(account: VaultAccount, content: str) -> VaultNote:
        now = datetime.now(UTC)
        return VaultNote(user_id=account.user_id, content=content, revision=1, created_at=now, updated_at=now)

    async def list(self, p: Principal) -> list[VaultNote]:
        account = await self._account(p)
        return await self.notes.for_user(account.user_id)

    async def create(self, p: Principal, data: NoteContent) -> VaultNote:
        account = await self._account(p)
        note = self.notes.add(self._new(account, data.content))
        await self.db.flush()
        await self._record(p, "vault.note.create", note.id)
        await self.db.commit()
        return note

    async def import_many(self, p: Principal, items: list[NoteContent]) -> list[VaultNote]:
        account = await self._account(p)
        notes = [self.notes.add(self._new(account, i.content)) for i in items]
        await self.db.flush()
        await self._record(p, "vault.note.import", None, count=len(notes))
        await self.db.commit()
        return notes

    async def update(self, p: Principal, note_id: UUID, data: NoteContent, base_revision: int) -> VaultNote:
        note = await self._note(p, note_id, lock=True)
        if note.revision != base_revision:
            raise StaleNoteError(note)
        if note.content == data.content:
            return note
        now = datetime.now(UTC)
        latest = await self.versions.latest(note.id)
        if latest is None or now - latest.saved_at >= VERSION_INTERVAL:
            self.versions.add(
                VaultNoteVersion(
                    note_id=note.id, revision=note.revision, content=note.content, saved_at=note.updated_at
                )
            )
            await self.db.flush()
            await self.versions.prune(note.id, MAX_VERSIONS)
        note.content = data.content
        note.revision += 1
        note.updated_at = now
        await self.db.commit()
        return note

    async def trash(self, p: Principal, note_id: UUID) -> VaultNote:
        note = await self._note(p, note_id, lock=True)
        if note.deleted_at is None:
            note.deleted_at = datetime.now(UTC)
            note.revision += 1
            await self._record(p, "vault.note.trash", note.id)
            await self.db.commit()
        return note

    async def restore(self, p: Principal, note_id: UUID) -> VaultNote:
        note = await self._note(p, note_id, lock=True)
        if note.deleted_at is not None:
            note.deleted_at = None
            note.revision += 1
            await self._record(p, "vault.note.restore", note.id)
            await self.db.commit()
        return note

    async def delete(self, p: Principal, note_id: UUID) -> None:
        note = await self._note(p, note_id, lock=True)
        await self._record(p, "vault.note.delete", note.id)
        await self.notes.delete(note)
        await self.db.commit()

    async def history(self, p: Principal, note_id: UUID) -> list[VaultNoteVersion]:
        note = await self._note(p, note_id)
        return await self.versions.for_note(note.id)
