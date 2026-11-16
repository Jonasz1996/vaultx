"""Statistieken over VaultX voor een dashboard: enkel tellingen en tijdstippen, nooit inhoud.

Een instantiebeheerder krijgt cijfers over heel VaultX ("instance"), een gewone gebruiker
enkel over zichzelf ("user"). Kluisitems en notities zijn end-to-end versleuteld: de server
kent hun type en aantal, niet hun inhoud, en geeft dus ook nooit meer dan dat.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.models import (
    AuditLog,
    Link,
    LinkCollection,
    LinkTag,
    NpmConnection,
    User,
    UserSession,
    VaultAccount,
    VaultAttachment,
    VaultCipher,
    VaultDevice,
    VaultFolder,
    VaultNote,
)
from app.models.base import utcnow
from app.repositories import ApplicationRepository
from app.services.catalog import app_status
from app.services.principal import Principal
from app.services.vault_auth import device_type_name

# Bitwarden CipherType.
CIPHER_TYPES = {1: "logins", 2: "notes", 3: "cards", 4: "identities", 5: "ssh_keys"}
ACTIVE_DEVICE_DAYS = 30


class StatsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _count(self, stmt: Select) -> int:
        return int(await self.db.scalar(stmt) or 0)

    async def collect(self, p: Principal) -> dict[str, Any]:
        instance = p.is_admin
        uid = p.user.id
        now = utcnow()
        day_ago = now - timedelta(hours=24)

        def mine(column):  # filter op de eigen gebruiker, tenzij instantiebreed
            return true() if instance else column == uid

        result: dict[str, Any] = {
            "scope": "instance" if instance else "user",
            "version": __version__,
            "generated_at": now,
        }

        # --- Gebruikers (enkel instantiebreed) ---------------------------------------------
        if instance:
            u = (
                await self.db.execute(
                    select(
                        func.count(),
                        func.count().filter(User.is_active),
                        func.count().filter(User.is_admin),
                        func.count().filter(User.oidc_issuer.is_not(None)),
                        func.count().filter(User.password_hash.is_not(None)),
                        func.count().filter(User.last_login_at >= day_ago),
                        func.count().filter(User.locked_until > now),
                    ).select_from(User)
                )
            ).one()
            result["users"] = {
                "total": u[0],
                "active": u[1],
                "admins": u[2],
                "with_authentik": u[3],
                "with_password": u[4],
                "logged_in_last_24h": u[5],
                "locked": u[6],
            }

        # --- Webinterface-sessies ------------------------------------------------------------
        live = and_(UserSession.revoked_at.is_(None), UserSession.expires_at > now, mine(UserSession.user_id))
        s = (
            await self.db.execute(
                select(func.count(), func.count(func.distinct(UserSession.user_id))).where(live)
            )
        ).one()
        result["sessions"] = {"active": s[0], "users": s[1]}

        # --- Kluis -----------------------------------------------------------------------------
        accounts = (
            await self.db.execute(
                select(
                    func.count(),
                    func.count().filter(VaultAccount.two_factor_enabled_at.is_not(None)),
                    func.max(VaultAccount.revision_date),
                )
                .select_from(VaultAccount)
                .where(mine(VaultAccount.user_id))
            )
        ).one()
        by_type: Counter[str] = Counter()
        rows = await self.db.execute(
            select(VaultCipher.type, func.count())
            .where(VaultCipher.deleted_at.is_(None), mine(VaultCipher.user_id))
            .group_by(VaultCipher.type)
        )
        for t, n in rows:
            by_type[CIPHER_TYPES.get(t, "other")] += n
        c = (
            await self.db.execute(
                select(
                    func.count().filter(VaultCipher.favorite & VaultCipher.deleted_at.is_(None)),
                    func.count().filter(VaultCipher.deleted_at.is_not(None)),
                    func.count().filter(
                        VaultCipher.archived_at.is_not(None) & VaultCipher.deleted_at.is_(None)
                    ),
                )
                .select_from(VaultCipher)
                .where(mine(VaultCipher.user_id))
            )
        ).one()
        att = (
            await self.db.execute(
                select(func.count(), func.coalesce(func.sum(VaultAttachment.size), 0)).where(
                    VaultAttachment.uploaded_at.is_not(None), mine(VaultAttachment.user_id)
                )
            )
        ).one()
        devices = list(
            await self.db.execute(
                select(VaultDevice.type, VaultDevice.last_seen_at).where(
                    VaultDevice.revoked_at.is_(None), mine(VaultDevice.user_id)
                )
            )
        )
        recent = now - timedelta(days=ACTIVE_DEVICE_DAYS)
        device_types = Counter(device_type_name(t) for t, _ in devices)
        result["vault"] = {
            "accounts": accounts[0],
            "with_two_factor": accounts[1],
            "last_change_at": accounts[2],
            "items": {
                "total": sum(by_type.values()),
                **{k: by_type.get(k, 0) for k in (*CIPHER_TYPES.values(), "other")},
                "favorites": c[0],
                "archived": c[2],
                "in_trash": c[1],
            },
            "folders": await self._count(
                select(func.count()).select_from(VaultFolder).where(mine(VaultFolder.user_id))
            ),
            "attachments": {"count": att[0], "bytes": int(att[1])},
            "devices": {
                "total": len(devices),
                f"active_last_{ACTIVE_DEVICE_DAYS}_days": sum(1 for _, seen in devices if seen >= recent),
                "by_type": dict(device_types.most_common()),
                "last_seen_at": max((seen for _, seen in devices), default=None),
            },
        }

        # --- Notities ---------------------------------------------------------------------
        n = (
            await self.db.execute(
                select(
                    func.count().filter(VaultNote.deleted_at.is_(None)),
                    func.count().filter(VaultNote.deleted_at.is_not(None)),
                    func.max(VaultNote.updated_at),
                ).where(mine(VaultNote.user_id))
            )
        ).one()
        result["notes"] = {"total": n[0], "in_trash": n[1], "last_change_at": n[2]}

        # --- Links ------------------------------------------------------------------------
        lk = (
            await self.db.execute(
                select(func.count(), func.count().filter(Link.pinned), func.max(Link.created_at)).where(
                    mine(Link.user_id)
                )
            )
        ).one()
        result["links"] = {
            "total": lk[0],
            "pinned": lk[1],
            "last_added_at": lk[2],
            "collections": await self._count(
                select(func.count()).select_from(LinkCollection).where(mine(LinkCollection.user_id))
            ),
            "tags": await self._count(select(func.count()).select_from(LinkTag).where(mine(LinkTag.user_id))),
        }

        # --- Applicatiecatalogus (organisaties die je mag zien) ---------------------------
        org_ids = None if instance else list(p.org_roles)
        apps = await ApplicationRepository(self.db).list_for_orgs(org_ids)
        statuses = Counter(app_status(a) for a in apps)
        live_hosts = [h for a in apps for h in a.hosts if h.removed_at is None]
        conns_stmt = select(NpmConnection).order_by(NpmConnection.name)
        if org_ids is not None:
            conns_stmt = conns_stmt.where(NpmConnection.organization_id.in_(org_ids))
        conns = list(await self.db.scalars(conns_stmt))
        result["catalog"] = {
            "applications": len(apps),
            "by_status": dict(statuses.most_common()),
            "auto_login": sum(1 for a in apps if a.login is not None),
            "hosts": len(live_hosts),
            "hosts_online": sum(1 for h in live_hosts if h.enabled and h.nginx_online),
            "hosts_with_ssl": sum(1 for h in live_hosts if h.ssl),
            "npm_connections": [
                {
                    "name": cn.name,
                    "enabled": cn.enabled,
                    "npm_version": cn.npm_version,
                    "last_sync_at": cn.last_sync_at,
                    "last_sync_status": cn.last_sync_status,
                }
                for cn in conns
            ],
        }

        # --- Activiteit van de laatste 24 uur (uit de auditlog) ---------------------------
        a = (
            await self.db.execute(
                select(
                    func.count(),
                    func.count().filter((AuditLog.action == "auth.login") & (AuditLog.outcome == "success")),
                    func.count().filter((AuditLog.action == "auth.login") & (AuditLog.outcome != "success")),
                    func.count().filter((AuditLog.action == "vault.login") & (AuditLog.outcome == "success")),
                    func.count().filter((AuditLog.action == "vault.login") & (AuditLog.outcome != "success")),
                ).where(
                    AuditLog.occurred_at >= day_ago,
                    true()
                    if instance
                    else or_(
                        AuditLog.actor_user_id == uid,
                        and_(AuditLog.target_type == "user", AuditLog.target_id == str(uid)),
                    ),
                )
            )
        ).one()
        result["activity_last_24h"] = {
            "events": a[0],
            "logins": a[1],
            "failed_logins": a[2],
            "vault_logins": a[3],
            "failed_vault_logins": a[4],
        }
        return result
