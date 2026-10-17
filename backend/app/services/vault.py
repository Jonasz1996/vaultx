"""Persoonlijke kluis: activeren vanuit VaultX en items/mappen voor Bitwarden-clients.

Activeren gebeurt in de VaultX-webinterface, waar de gebruiker al via Authentik
is ingelogd. De browser leidt de sleutels af uit het master password en stuurt
alleen versleutelde sleutels en een afgeleide hash. Zo is een kluis altijd aan
een Authentik-identiteit gekoppeld, en kan niemand een kluis aanmaken voor het
e-mailadres van iemand anders.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.errors import ConflictError, InvalidOperationError, NotFoundError
from app.core.security import hash_master_password
from app.models import VaultAccount, VaultCipher, VaultDevice, VaultFolder
from app.repositories import (
    VaultAccountRepository,
    VaultCipherRepository,
    VaultDeviceRepository,
    VaultFolderRepository,
)
from app.schemas.vault import VaultEnroll
from app.services.audit import AuditService
from app.services.principal import Principal
from app.services.vault_auth import VaultCaller, device_type_name, normalize_email


class StaleCipherError(Exception):
    """De client werkt met een verouderde versie van het item."""


def new_security_stamp() -> str:
    return secrets.token_hex(16)


@dataclass(slots=True)
class VaultStatus:
    email: str | None
    blocked_reason: str | None
    account: VaultAccount | None
    devices: list[VaultDevice]
    item_count: int = 0
    trash_count: int = 0
    folder_count: int = 0


class VaultService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.accounts = VaultAccountRepository(db)
        self.devices = VaultDeviceRepository(db)
        self.folders = VaultFolderRepository(db)
        self.ciphers = VaultCipherRepository(db)
        self.audit = AuditService(db)

    # --- Beheer vanuit VaultX (ingelogd via Authentik) --------------------------------

    @staticmethod
    def _enroll_email(p: Principal) -> tuple[str | None, str | None]:
        if not p.user.email:
            return None, "Je account in Authentik heeft geen e-mailadres. Vul het daar in en log opnieuw in."
        return normalize_email(p.user.email), None

    async def status(self, p: Principal) -> VaultStatus:
        account = await self.accounts.get(p.user.id)
        if account is None:
            email, blocked = self._enroll_email(p)
            if email and await self.accounts.by_email(email) is not None:
                blocked = "Dit e-mailadres is al in gebruik voor een andere kluis."
            return VaultStatus(email=email, blocked_reason=blocked, account=None, devices=[])
        items, trash = await self.ciphers.counts(account.user_id)
        return VaultStatus(
            email=account.email,
            blocked_reason=None,
            account=account,
            devices=await self.devices.for_user(account.user_id),
            item_count=items,
            trash_count=trash,
            folder_count=len(await self.folders.for_user(account.user_id)),
        )

    async def enroll(self, p: Principal, data: VaultEnroll) -> VaultAccount:
        if await self.accounts.get(p.user.id) is not None:
            raise ConflictError("Je kluis is al geactiveerd")
        email, blocked = self._enroll_email(p)
        if email is None:
            raise InvalidOperationError(blocked)
        if data.salt != email:
            # De browser moet de sleutels afleiden met precies de salt die clients later krijgen.
            raise InvalidOperationError("Salt komt niet overeen met het e-mailadres van je kluis")
        if await self.accounts.by_email(email) is not None:
            raise ConflictError("Dit e-mailadres is al in gebruik voor een andere kluis")
        now = datetime.now(UTC)
        account = self.accounts.add(
            VaultAccount(
                user_id=p.user.id,
                email=email,
                kdf_salt=email,
                kdf_type=data.kdf,
                kdf_iterations=data.kdf_iterations,
                kdf_memory=data.kdf_memory,
                kdf_parallelism=data.kdf_parallelism,
                master_password_hash=await run_in_threadpool(hash_master_password, data.master_password_hash),
                user_key=data.user_key,
                public_key=data.public_key,
                private_key=data.private_key,
                security_stamp=new_security_stamp(),
                revision_date=now,
                failed_logins=0,
                created_at=now,
                updated_at=now,
            )
        )
        await self.db.flush()
        await self.audit.record(
            "vault.enroll",
            p.actor,
            target_type="vault",
            target_id=p.user.id,
            details={"email": email, "kdf": data.kdf, "kdf_iterations": data.kdf_iterations},
        )
        await self.db.commit()
        return account

    async def reset(self, p: Principal) -> None:
        """Verwijdert de kluis met alle items. Zonder master password is de inhoud
        toch onleesbaar; dit is de enige 'herstel'-optie bij een vergeten wachtwoord."""
        account = await self.accounts.lock(p.user.id)
        if account is None:
            raise NotFoundError("Je hebt nog geen kluis")
        items, trash = await self.ciphers.counts(account.user_id)
        await self.accounts.delete(account)
        await self.audit.record(
            "vault.reset",
            p.actor,
            target_type="vault",
            target_id=p.user.id,
            details={"items_deleted": items + trash},
        )
        await self.db.commit()

    async def revoke_device(self, p: Principal, device_id: UUID) -> VaultDevice:
        device = await self.devices.get(device_id)
        if device is None or device.user_id != p.user.id:
            raise NotFoundError("Apparaat niet gevonden")
        if device.revoked_at is None:
            device.revoked_at = datetime.now(UTC)
            await self.audit.record(
                "vault.device.revoke",
                p.actor,
                target_type="vault_device",
                target_id=device.id,
                details={"device": device.name, "client": device_type_name(device.type)},
            )
            await self.db.commit()
        return device

    # --- Bitwarden-clients -------------------------------------------------------------

    async def _touch(self, c: VaultCaller) -> datetime:
        """Lockt het account en zet een nieuwe revisiedatum (strikt stijgend)."""
        account = await self.accounts.lock(c.account.user_id)
        assert account is not None
        now = datetime.now(UTC)
        if account.revision_date >= now:
            now = account.revision_date + timedelta(microseconds=1)
        account.revision_date = now
        c.account = account
        return now

    async def set_user_key_id(self, c: VaultCaller, user_key_id: str) -> None:
        """De client meldt het id van zijn user key (nieuw in de Bitwarden-SDK)."""
        account = await self.accounts.lock(c.account.user_id)
        assert account is not None
        if account.user_key_id != user_key_id:
            account.user_key_id = user_key_id
            await self._record(c, "vault.user_key_id.set", "vault", account.user_id, user_key_id=user_key_id)
            await self.db.commit()

    async def _record(
        self, c: VaultCaller, action: str, target_type: str, target_id: Any, **details: Any
    ) -> None:
        await self.audit.record(
            action,
            c.actor,
            target_type=target_type,
            target_id=target_id,
            details={"client": c.client_label, **details},
        )

    async def _folder_id(self, c: VaultCaller, folder_id: UUID | None) -> UUID | None:
        if folder_id is not None and await self.folders.owned(c.account.user_id, folder_id) is None:
            raise InvalidOperationError("Map niet gevonden")
        return folder_id

    async def sync_data(self, c: VaultCaller) -> tuple[list[VaultFolder], list[VaultCipher]]:
        return (
            await self.folders.for_user(c.account.user_id),
            await self.ciphers.for_user(c.account.user_id),
        )

    async def get_cipher(self, c: VaultCaller, cipher_id: UUID) -> VaultCipher:
        cipher = await self.ciphers.owned(c.account.user_id, cipher_id)
        if cipher is None:
            raise NotFoundError("Item niet gevonden")
        return cipher

    async def list_ciphers(self, c: VaultCaller) -> list[VaultCipher]:
        return [x for x in await self.ciphers.for_user(c.account.user_id) if x.deleted_at is None]

    async def create_cipher(
        self,
        c: VaultCaller,
        *,
        type_: int,
        payload: dict[str, Any],
        folder_id: UUID | None,
        favorite: bool,
        archived_at: datetime | None,
    ) -> VaultCipher:
        folder_id = await self._folder_id(c, folder_id)
        now = await self._touch(c)
        cipher = self.ciphers.add(
            VaultCipher(
                user_id=c.account.user_id,
                folder_id=folder_id,
                type=type_,
                data=payload,
                favorite=favorite,
                created_at=now,
                revision_date=now,
                archived_at=archived_at,
            )
        )
        await self.db.flush()
        await self._record(c, "vault.item.create", "vault_item", cipher.id, type=type_)
        await self.db.commit()
        return cipher

    async def update_cipher(
        self,
        c: VaultCaller,
        cipher_id: UUID,
        *,
        type_: int,
        payload: dict[str, Any],
        folder_id: UUID | None,
        favorite: bool,
        archived_at: datetime | None,
        last_known_revision: datetime | None,
    ) -> VaultCipher:
        cipher = await self.get_cipher(c, cipher_id)
        # Zelfde regel als Bitwarden: een client met een oudere kopie mag niet overschrijven.
        # Eén seconde marge, want clients ronden af op milliseconden.
        if (
            last_known_revision is not None
            and abs((cipher.revision_date - last_known_revision.astimezone(UTC)).total_seconds()) > 1
        ):
            raise StaleCipherError()
        folder_id = await self._folder_id(c, folder_id)
        now = await self._touch(c)
        cipher.type = type_
        cipher.data = payload
        cipher.folder_id = folder_id
        cipher.favorite = favorite
        cipher.archived_at = archived_at
        cipher.revision_date = now
        await self._record(c, "vault.item.update", "vault_item", cipher.id, type=type_)
        await self.db.commit()
        return cipher

    async def update_cipher_partial(
        self, c: VaultCaller, cipher_id: UUID, *, folder_id: UUID | None, favorite: bool
    ) -> VaultCipher:
        cipher = await self.get_cipher(c, cipher_id)
        cipher.folder_id = await self._folder_id(c, folder_id)
        cipher.favorite = favorite
        cipher.revision_date = await self._touch(c)
        await self.db.commit()
        return cipher

    async def trash_ciphers(self, c: VaultCaller, cipher_ids: list[UUID]) -> None:
        ciphers = await self.ciphers.owned_many(c.account.user_id, cipher_ids)
        if not ciphers:
            raise NotFoundError("Item niet gevonden")
        now = await self._touch(c)
        for cipher in ciphers:
            if cipher.deleted_at is None:
                cipher.deleted_at = now
                cipher.revision_date = now
                await self._record(c, "vault.item.trash", "vault_item", cipher.id)
        await self.db.commit()

    async def restore_ciphers(self, c: VaultCaller, cipher_ids: list[UUID]) -> list[VaultCipher]:
        ciphers = await self.ciphers.owned_many(c.account.user_id, cipher_ids)
        if not ciphers:
            raise NotFoundError("Item niet gevonden")
        now = await self._touch(c)
        for cipher in ciphers:
            if cipher.deleted_at is not None:
                cipher.deleted_at = None
                cipher.revision_date = now
                await self._record(c, "vault.item.restore", "vault_item", cipher.id)
        await self.db.commit()
        return ciphers

    async def delete_ciphers(self, c: VaultCaller, cipher_ids: list[UUID]) -> None:
        ciphers = await self.ciphers.owned_many(c.account.user_id, cipher_ids)
        if not ciphers:
            raise NotFoundError("Item niet gevonden")
        await self._touch(c)
        for cipher in ciphers:
            await self._record(c, "vault.item.delete", "vault_item", cipher.id)
            await self.ciphers.delete(cipher)
        await self.db.commit()

    async def move_ciphers(self, c: VaultCaller, cipher_ids: list[UUID], folder_id: UUID | None) -> None:
        folder_id = await self._folder_id(c, folder_id)
        ciphers = await self.ciphers.owned_many(c.account.user_id, cipher_ids)
        now = await self._touch(c)
        for cipher in ciphers:
            cipher.folder_id = folder_id
            cipher.revision_date = now
        await self.db.commit()

    async def create_folder(self, c: VaultCaller, name: str) -> VaultFolder:
        now = await self._touch(c)
        folder = self.folders.add(VaultFolder(user_id=c.account.user_id, name=name, revision_date=now))
        await self.db.flush()
        await self._record(c, "vault.folder.create", "vault_folder", folder.id)
        await self.db.commit()
        return folder

    async def get_folder(self, c: VaultCaller, folder_id: UUID) -> VaultFolder:
        folder = await self.folders.owned(c.account.user_id, folder_id)
        if folder is None:
            raise NotFoundError("Map niet gevonden")
        return folder

    async def update_folder(self, c: VaultCaller, folder_id: UUID, name: str) -> VaultFolder:
        folder = await self.get_folder(c, folder_id)
        folder.name = name
        folder.revision_date = await self._touch(c)
        await self._record(c, "vault.folder.update", "vault_folder", folder.id)
        await self.db.commit()
        return folder

    async def delete_folder(self, c: VaultCaller, folder_id: UUID) -> None:
        folder = await self.get_folder(c, folder_id)
        await self._touch(c)
        await self._record(c, "vault.folder.delete", "vault_folder", folder.id)
        await self.folders.delete(folder)
        await self.db.commit()
