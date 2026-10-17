from uuid import UUID

from sqlalchemy import func, select

from app.models import VaultAccount, VaultCipher, VaultDevice, VaultFolder
from app.repositories.base import Repository


class VaultAccountRepository(Repository[VaultAccount]):
    model = VaultAccount

    async def by_email(self, email: str) -> VaultAccount | None:
        return await self.db.scalar(select(VaultAccount).where(VaultAccount.email == email))

    async def lock(self, user_id: UUID) -> VaultAccount | None:
        """Leest het account met een rijlock, zodat revisiedatums niet door elkaar lopen."""
        return await self.db.scalar(
            select(VaultAccount)
            .where(VaultAccount.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )


class VaultDeviceRepository(Repository[VaultDevice]):
    model = VaultDevice

    async def by_identifier(self, user_id: UUID, identifier: str) -> VaultDevice | None:
        return await self.db.scalar(
            select(VaultDevice).where(VaultDevice.user_id == user_id, VaultDevice.identifier == identifier)
        )

    async def by_refresh_hash(self, token_hash: str) -> VaultDevice | None:
        return await self.db.scalar(select(VaultDevice).where(VaultDevice.refresh_token_hash == token_hash))

    async def for_user(self, user_id: UUID) -> list[VaultDevice]:
        rows = await self.db.scalars(
            select(VaultDevice)
            .where(VaultDevice.user_id == user_id)
            .order_by(VaultDevice.last_seen_at.desc())
        )
        return list(rows)


class VaultFolderRepository(Repository[VaultFolder]):
    model = VaultFolder

    async def owned(self, user_id: UUID, folder_id: UUID) -> VaultFolder | None:
        return await self.db.scalar(
            select(VaultFolder).where(VaultFolder.id == folder_id, VaultFolder.user_id == user_id)
        )

    async def for_user(self, user_id: UUID) -> list[VaultFolder]:
        rows = await self.db.scalars(
            select(VaultFolder).where(VaultFolder.user_id == user_id).order_by(VaultFolder.revision_date)
        )
        return list(rows)


class VaultCipherRepository(Repository[VaultCipher]):
    model = VaultCipher

    async def owned(self, user_id: UUID, cipher_id: UUID) -> VaultCipher | None:
        return await self.db.scalar(
            select(VaultCipher).where(VaultCipher.id == cipher_id, VaultCipher.user_id == user_id)
        )

    async def owned_many(self, user_id: UUID, cipher_ids: list[UUID]) -> list[VaultCipher]:
        if not cipher_ids:
            return []
        rows = await self.db.scalars(
            select(VaultCipher).where(VaultCipher.user_id == user_id, VaultCipher.id.in_(cipher_ids))
        )
        return list(rows)

    async def for_user(self, user_id: UUID) -> list[VaultCipher]:
        rows = await self.db.scalars(
            select(VaultCipher).where(VaultCipher.user_id == user_id).order_by(VaultCipher.created_at)
        )
        return list(rows)

    async def counts(self, user_id: UUID) -> tuple[int, int]:
        """(actieve items, items in de prullenbak)."""
        row = (
            await self.db.execute(
                select(
                    func.count().filter(VaultCipher.deleted_at.is_(None)),
                    func.count().filter(VaultCipher.deleted_at.is_not(None)),
                ).where(VaultCipher.user_id == user_id)
            )
        ).one()
        return int(row[0]), int(row[1])
