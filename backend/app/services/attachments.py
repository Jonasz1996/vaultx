"""Bijlagen bij kluisitems voor de Bitwarden-apps (fase 9).

Zoals bij Bitwarden versleutelt de client alles: de inhoud (EncArrayBuffer), de
bestandsnaam en de bestandssleutel. VaultX bewaart de blob in PostgreSQL (net als
de bijlagen van notities), zodat back-ups en meerdere nodes niets extra nodig hebben.

Downloaden gebeurt zonder Authorization-header (de clients doen een kale fetch op
de URL), dus de URL bevat een kort geldig, ondertekend token.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict
from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.core.config import Settings
from app.core.errors import InvalidOperationError, NotFoundError
from app.core.security import derive_key
from app.models import VaultAttachment, VaultCipher
from app.services.audit import AuditService
from app.services.notes import check_enc_file, check_enc_name
from app.services.vault import VaultService
from app.services.vault_auth import VaultCaller
from app.services.vault_push import PushType

# Geldigheid van een download-URL: kort in het antwoord op "geef me de bijlage",
# langer in de sync (de clients vallen daarop terug).
DOWNLOAD_SECONDS = 5 * 60
SYNC_DOWNLOAD_SECONDS = 60 * 60
# Een aangekondigde bijlage die nooit geüpload werd, ruimen we na zo lang op.
PENDING_HOURS = 1


class AttachmentLinks:
    def __init__(self, settings: Settings) -> None:
        self.base = settings.public_url
        self.key = derive_key(settings.secret_key.get_secret_value(), "vaultx/attachment-download/v1")

    def _sig(self, attachment_id: UUID, expires: int) -> str:
        return hmac.new(self.key, f"{attachment_id}:{expires}".encode(), hashlib.sha256).hexdigest()

    def url(self, a: VaultAttachment, seconds: int = DOWNLOAD_SECONDS) -> str:
        expires = int(time.time()) + seconds
        token = f"{expires}.{self._sig(a.id, expires)}"
        return f"{self.base}/api/ciphers/{a.cipher_id}/attachment/{a.id}/download?token={token}"

    def check(self, attachment_id: UUID, token: str) -> bool:
        expires_raw, _, sig = token.partition(".")
        if not expires_raw.isdigit() or int(expires_raw) < time.time():
            return False
        return hmac.compare_digest(sig, self._sig(attachment_id, int(expires_raw)))

    def upload_url(self, a: VaultAttachment) -> str:
        return f"{self.base}/api/ciphers/{a.cipher_id}/attachment/{a.id}"


class AttachmentService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.vault = VaultService(db)
        self.audit = AuditService(db)

    async def by_cipher(self, user_id: UUID, cipher_ids: list[UUID] | None = None) -> dict[UUID, list]:
        """Geüploade bijlagen per item (zonder inhoud)."""
        stmt = select(VaultAttachment).where(
            VaultAttachment.user_id == user_id, VaultAttachment.uploaded_at.is_not(None)
        )
        if cipher_ids is not None:
            stmt = stmt.where(VaultAttachment.cipher_id.in_(cipher_ids))
        out: dict[UUID, list] = defaultdict(list)
        for a in await self.db.scalars(stmt.order_by(VaultAttachment.created_at)):
            out[a.cipher_id].append(a)
        return out

    async def _usage(self, user_id: UUID) -> int:
        attachments = await self.db.scalar(
            select(func.coalesce(func.sum(VaultAttachment.size), 0)).where(VaultAttachment.user_id == user_id)
        )
        return int(attachments or 0)

    async def _owned(self, c: VaultCaller, cipher_id: UUID, attachment_id: UUID) -> VaultAttachment:
        a = await self.db.scalar(
            select(VaultAttachment).where(
                VaultAttachment.id == attachment_id,
                VaultAttachment.cipher_id == cipher_id,
                VaultAttachment.user_id == c.account.user_id,
            )
        )
        if a is None:
            raise NotFoundError("Bijlage niet gevonden")
        return a

    async def create(
        self, c: VaultCaller, cipher_id: UUID, *, file_name: str, key: str | None, size: int
    ) -> tuple[VaultAttachment, VaultCipher]:
        check_enc_name(file_name)
        if key is not None:
            check_enc_name(key)
        max_bytes = self.settings.vault_attachment_max_mb * 1024 * 1024
        if size <= 0 or size > max_bytes:
            raise InvalidOperationError(
                f"Bijlagen mogen maximaal {self.settings.vault_attachment_max_mb} MB zijn"
            )
        cipher = await self.vault.get_cipher(c, cipher_id)
        now = await self.vault._touch(c)  # lockt het account: quotum en revisie in één keer
        await self.db.execute(
            delete(VaultAttachment).where(
                VaultAttachment.user_id == c.account.user_id,
                VaultAttachment.uploaded_at.is_(None),
                VaultAttachment.created_at < now - timedelta(hours=PENDING_HOURS),
            )
        )
        quota = self.settings.vault_attachment_storage_mb * 1024 * 1024
        if await self._usage(c.account.user_id) + size > quota:
            raise InvalidOperationError("Je opslag voor bijlagen is vol; verwijder eerst andere bijlagen")
        a = VaultAttachment(
            user_id=c.account.user_id,
            cipher_id=cipher.id,
            file_name=file_name,
            key=key,
            size=size,
            created_at=now,
        )
        self.db.add(a)
        cipher.revision_date = now
        await self.db.flush()
        await self.db.commit()
        return a, cipher

    async def renew(
        self, c: VaultCaller, cipher_id: UUID, attachment_id: UUID
    ) -> tuple[VaultAttachment, VaultCipher]:
        a = await self._owned(c, cipher_id, attachment_id)
        if a.uploaded_at is not None:
            raise InvalidOperationError("Deze bijlage is al geüpload")
        return a, await self.vault.get_cipher(c, cipher_id)

    async def upload(self, c: VaultCaller, cipher_id: UUID, attachment_id: UUID, data: bytes) -> None:
        a = await self._owned(c, cipher_id, attachment_id)
        if a.uploaded_at is not None:
            raise InvalidOperationError("Deze bijlage is al geüpload")
        check_enc_file(data)
        if len(data) != a.size:
            raise InvalidOperationError(
                "De grootte van het bestand komt niet overeen met wat aangekondigd was"
            )
        now = await self.vault._touch(c)
        a.data = data
        a.uploaded_at = now
        cipher = await self.vault.get_cipher(c, cipher_id)
        cipher.revision_date = now
        await self.vault._record(c, "vault.attachment.upload", "vault_item", cipher_id, size=len(data))
        await self.vault.push(c, PushType.SYNC_CIPHER_UPDATE, cipher_id, now)
        await self.db.commit()

    async def get(self, c: VaultCaller, cipher_id: UUID, attachment_id: UUID) -> VaultAttachment:
        a = await self._owned(c, cipher_id, attachment_id)
        if a.uploaded_at is None:
            raise NotFoundError("Bijlage niet gevonden")
        return a

    async def delete(self, c: VaultCaller, cipher_id: UUID, attachment_id: UUID) -> VaultCipher:
        a = await self._owned(c, cipher_id, attachment_id)
        now = await self.vault._touch(c)
        cipher = await self.vault.get_cipher(c, cipher_id)
        cipher.revision_date = now
        await self.db.delete(a)
        await self.vault._record(c, "vault.attachment.delete", "vault_item", cipher_id)
        await self.vault.push(c, PushType.SYNC_CIPHER_UPDATE, cipher_id, now)
        await self.db.commit()
        return cipher

    async def download(self, cipher_id: UUID, attachment_id: UUID, token: str) -> bytes:
        if not AttachmentLinks(self.settings).check(attachment_id, token):
            raise NotFoundError("Bijlage niet gevonden of link verlopen")
        a = await self.db.scalar(
            select(VaultAttachment)
            .options(undefer(VaultAttachment.data))
            .where(VaultAttachment.id == attachment_id, VaultAttachment.cipher_id == cipher_id)
        )
        if a is None or a.data is None:
            raise NotFoundError("Bijlage niet gevonden of link verlopen")
        return a.data


def attachment_json(a: VaultAttachment, links: AttachmentLinks, seconds: int = DOWNLOAD_SECONDS) -> dict:
    return {
        "id": str(a.id),
        "url": links.url(a, seconds),
        "fileName": a.file_name,
        "key": a.key,
        "size": str(a.size),
        "sizeName": size_name(a.size),
        "object": "attachment",
    }


def size_name(size: int) -> str:
    value = float(size)
    for unit in ("Bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "Bytes" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} Bytes"


__all__ = ["AttachmentLinks", "AttachmentService", "attachment_json"]
