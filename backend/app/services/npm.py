"""NPM-koppelingen beheren en synchroniseren naar de applicatiecatalogus."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, UpstreamError
from app.core.security import open_secret, seal_secret
from app.models import Application, AppSource, DiscoveredHost, NpmConnection
from app.repositories import (
    ApplicationRepository,
    DiscoveredHostRepository,
    NpmConnectionRepository,
    OrganizationRepository,
)
from app.schemas.catalog import NpmConnectionCreate, NpmConnectionUpdate
from app.services.audit import AuditService
from app.services.npm_client import NPMClient, NPMError, normalize_base_url
from app.services.npm_detect import Detection, analyze
from app.services.principal import Actor, Principal

log = logging.getLogger(__name__)

# Testen vervangen dit door een factory die een nep-NPM gebruikt.
ClientFactory = Callable[[NpmConnection], NPMClient]
MAX_AUDIT_LIST = 50


def default_client_factory(settings: Settings) -> ClientFactory:
    def make(conn: NpmConnection) -> NPMClient:
        return NPMClient(conn.base_url, verify_tls=conn.verify_tls, timeout=settings.npm_http_timeout_seconds)

    return make


@dataclass(slots=True)
class SyncResult:
    connection_id: UUID
    npm_version: str | None
    hosts_total: int = 0
    hosts_new: int = 0
    hosts_updated: int = 0
    hosts_removed: int = 0
    applications_created: int = 0
    applications_updated: int = 0


class NpmService:
    def __init__(self, db: AsyncSession, settings: Settings, client_factory: ClientFactory | None = None):
        self.db = db
        self.settings = settings
        self.client_factory = client_factory or default_client_factory(settings)
        self.connections = NpmConnectionRepository(db)
        self.hosts = DiscoveredHostRepository(db)
        self.apps = ApplicationRepository(db)
        self.orgs = OrganizationRepository(db)
        self.audit = AuditService(db)

    # ------------------------------------------------------------ helpers

    def _seal(self, conn_id: UUID, secret: str) -> bytes:
        return seal_secret(self.settings.secret_key.get_secret_value(), secret, f"npm:{conn_id}")

    def _open(self, conn: NpmConnection) -> str:
        return open_secret(
            self.settings.secret_key.get_secret_value(), conn.secret_ciphertext, f"npm:{conn.id}"
        )

    async def _org_visible(self, p: Principal, org_id: UUID) -> None:
        if not p.can_view_org(org_id) or await self.orgs.get(org_id) is None:
            raise NotFoundError("Organisatie niet gevonden")

    async def _conn_visible(self, p: Principal, org_id: UUID, conn_id: UUID) -> NpmConnection:
        await self._org_visible(p, org_id)
        conn = await self.connections.in_org(org_id, conn_id)
        if conn is None:
            raise NotFoundError("NPM-koppeling niet gevonden")
        return conn

    async def _require_manage(self, p: Principal, action: str, org_id: UUID, target_id: Any) -> None:
        if not p.can_manage_org(org_id):
            raise await self.audit.deny(
                action, p.actor, organization_id=org_id, target_type="npm_connection", target_id=target_id
            )

    async def _commit(self, conflict_message: str) -> None:
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ConflictError(conflict_message) from exc

    # ------------------------------------------------------------ koppelingen

    async def list_connections(self, p: Principal, org_id: UUID) -> tuple[list[NpmConnection], dict]:
        await self._org_visible(p, org_id)
        conns = await self.connections.list_for_org(org_id)
        return conns, await self.connections.host_counts([c.id for c in conns])

    async def get_connection(self, p: Principal, org_id: UUID, conn_id: UUID) -> NpmConnection:
        return await self._conn_visible(p, org_id, conn_id)

    async def host_count(self, conn_id: UUID) -> int:
        return (await self.connections.host_counts([conn_id])).get(conn_id, 0)

    async def create_connection(self, p: Principal, org_id: UUID, data: NpmConnectionCreate) -> NpmConnection:
        await self._org_visible(p, org_id)
        await self._require_manage(p, "npm_connection.create", org_id, data.name)
        if await self.connections.by_name(org_id, data.name):
            raise ConflictError(f"Er is al een NPM-koppeling '{data.name}'")
        conn_id = uuid.uuid4()
        conn = self.connections.add(
            NpmConnection(
                id=conn_id,
                organization_id=org_id,
                name=data.name,
                base_url=normalize_base_url(data.base_url),
                identity=data.identity,
                secret_ciphertext=self._seal(conn_id, data.secret),
                verify_tls=data.verify_tls,
                enabled=data.enabled,
            )
        )
        await self.db.flush()
        await self.audit.record(
            "npm_connection.created",
            p.actor,
            organization_id=org_id,
            target_type="npm_connection",
            target_id=conn.id,
            details=data.model_dump(exclude={"secret"}),
        )
        await self._commit(f"Er is al een NPM-koppeling '{data.name}'")
        return conn

    async def update_connection(
        self, p: Principal, org_id: UUID, conn_id: UUID, data: NpmConnectionUpdate
    ) -> NpmConnection:
        conn = await self._conn_visible(p, org_id, conn_id)
        await self._require_manage(p, "npm_connection.update", org_id, conn_id)
        values = data.model_dump(exclude_unset=True)
        secret = values.pop("secret", None)
        if values.get("base_url"):
            values["base_url"] = normalize_base_url(values["base_url"])
        changes = {
            k: {"from": getattr(conn, k), "to": v}
            for k, v in values.items()
            if v is not None and getattr(conn, k) != v
        }
        for field, change in changes.items():
            setattr(conn, field, change["to"])
        if secret is not None:
            conn.secret_ciphertext = self._seal(conn.id, secret)
            changes["secret"] = {"from": "***", "to": "***"}
        if changes:
            await self.audit.record(
                "npm_connection.updated",
                p.actor,
                organization_id=org_id,
                target_type="npm_connection",
                target_id=conn.id,
                details={"changes": changes},
            )
        await self._commit("Er is al een NPM-koppeling met die naam")
        return conn

    async def delete_connection(self, p: Principal, org_id: UUID, conn_id: UUID) -> None:
        conn = await self._conn_visible(p, org_id, conn_id)
        await self._require_manage(p, "npm_connection.delete", org_id, conn_id)
        await self.audit.record(
            "npm_connection.deleted",
            p.actor,
            organization_id=org_id,
            target_type="npm_connection",
            target_id=conn.id,
            details={"name": conn.name, "base_url": conn.base_url},
        )
        # Hosts verdwijnen mee (cascade); de catalogusitems blijven, zonder host.
        await self.connections.delete(conn)
        await self._commit("NPM-koppeling kon niet verwijderd worden")

    async def list_hosts(self, p: Principal, org_id: UUID, conn_id: UUID) -> list[DiscoveredHost]:
        await self._conn_visible(p, org_id, conn_id)
        return await self.hosts.for_connection(conn_id)

    async def set_host_ignored(
        self, p: Principal, org_id: UUID, conn_id: UUID, host_id: UUID, ignored: bool
    ) -> DiscoveredHost:
        await self._conn_visible(p, org_id, conn_id)
        host = await self.hosts.in_connection(conn_id, host_id)
        if host is None:
            raise NotFoundError("Host niet gevonden")
        if not p.can_manage_org(org_id):
            raise await self.audit.deny(
                "npm_host.update", p.actor, organization_id=org_id, target_type="npm_host", target_id=host_id
            )
        if host.ignored == ignored:
            return host
        host.ignored = ignored
        details: dict[str, Any] = {"ignored": ignored, "domains": host.domain_names}
        if ignored and host.application is not None:
            app = host.application
            host.application = None
            details["unlinked_application"] = app.name
            await self.db.flush()
            # Een automatisch aangemaakt item zonder andere hosts heeft geen bestaansreden meer.
            await self.db.refresh(app, ["hosts"])
            if app.source == AppSource.npm.value and app.auto_update and not app.hosts:
                await self.apps.delete(app)
                details["deleted_application"] = app.name
        await self.audit.record(
            "npm_host.updated",
            p.actor,
            organization_id=org_id,
            target_type="npm_host",
            target_id=host.id,
            details=details,
        )
        await self._commit("Host kon niet bijgewerkt worden")
        await self.db.refresh(host, ["application"])
        return host

    # ------------------------------------------------------------ sync

    async def sync_for_user(self, p: Principal, org_id: UUID, conn_id: UUID) -> SyncResult:
        await self._conn_visible(p, org_id, conn_id)
        await self._require_manage(p, "npm.sync", org_id, conn_id)
        return await self.sync(conn_id, p.actor)

    async def sync(self, conn_id: UUID, actor: Actor) -> SyncResult:
        """Leest alle proxy hosts uit NPM en werkt hosts en catalogus bij.

        Het netwerkdeel gebeurt buiten een databasetransactie; daarna gaat alles
        (hosts, catalogus, status van de koppeling, auditregel) in één commit.
        """
        conn = await self.connections.get(conn_id)
        if conn is None:
            raise NotFoundError("NPM-koppeling niet gevonden")
        org_id = conn.organization_id
        try:
            secret = self._open(conn)
        except Exception as exc:  # cryptography.exceptions.InvalidTag, ValueError
            message = (
                "Het opgeslagen NPM-wachtwoord kan niet ontsleuteld worden "
                "(is VAULTX_SECRET_KEY gewijzigd?). Vul het wachtwoord opnieuw in."
            )
            await self._record_failure(conn_id, actor, message)
            raise UpstreamError(message) from exc
        client = self.client_factory(conn)
        await self.db.commit()  # geen open transactie tijdens het netwerkverkeer

        try:
            async with client:
                await client.login(conn.identity, secret)
                version = await client.version()
                raw_hosts = await client.proxy_hosts()
        except NPMError as exc:
            await self._record_failure(conn_id, actor, exc.message)
            raise UpstreamError(exc.message) from exc

        await self.connections.lock_for_sync(conn_id)
        conn = await self.connections.get(conn_id)
        if conn is None:  # tussendoor verwijderd
            raise NotFoundError("NPM-koppeling niet gevonden")
        await self.db.refresh(conn)
        result = SyncResult(connection_id=conn_id, npm_version=version, hosts_total=len(raw_hosts))
        created_apps, removed_domains = await self._apply(conn, raw_hosts, result)

        now = datetime.now(UTC)
        conn.npm_version = version
        conn.last_sync_at = now
        conn.last_sync_status = "ok"
        conn.last_sync_error = None
        await self.audit.record(
            "npm.sync",
            actor,
            organization_id=org_id,
            target_type="npm_connection",
            target_id=conn_id,
            details={
                "npm_version": version,
                "hosts_total": result.hosts_total,
                "hosts_new": result.hosts_new,
                "hosts_updated": result.hosts_updated,
                "hosts_removed": result.hosts_removed,
                "applications_created": created_apps[:MAX_AUDIT_LIST],
                "applications_updated": result.applications_updated,
                "removed_domains": removed_domains[:MAX_AUDIT_LIST],
            },
        )
        await self.db.commit()
        return result

    async def _record_failure(self, conn_id: UUID, actor: Actor, message: str) -> None:
        await self.db.rollback()
        conn = await self.connections.get(conn_id)
        if conn is None:
            return
        conn.last_sync_at = datetime.now(UTC)
        conn.last_sync_status = "error"
        conn.last_sync_error = message
        await self.audit.record(
            "npm.sync",
            actor,
            outcome="failure",
            organization_id=conn.organization_id,
            target_type="npm_connection",
            target_id=conn_id,
            details={"error": message},
        )
        await self.db.commit()

    async def _apply(
        self, conn: NpmConnection, raw_hosts: list[dict[str, Any]], result: SyncResult
    ) -> tuple[list[str], list[str]]:
        now = datetime.now(UTC)
        existing = {h.npm_id: h for h in await self.hosts.for_connection(conn.id)}
        seen: set[int] = set()
        created_apps: list[str] = []

        for raw in raw_hosts:
            npm_id = raw["id"]
            if npm_id in seen:
                continue
            seen.add(npm_id)
            det = analyze(raw)
            host = existing.get(npm_id)
            values = self._host_values(raw, det)
            if host is None:
                host = self.hosts.add(
                    DiscoveredHost(
                        connection_id=conn.id,
                        organization_id=conn.organization_id,
                        npm_id=npm_id,
                        first_seen_at=now,
                        last_seen_at=now,
                        ignored=False,
                        **values,
                    )
                )
                host.application = None
                result.hosts_new += 1
            else:
                changed = host.removed_at is not None or any(getattr(host, k) != v for k, v in values.items())
                for k, v in values.items():
                    setattr(host, k, v)
                host.last_seen_at = now
                host.removed_at = None
                if changed:
                    result.hosts_updated += 1

            if host.ignored or det.ignore:
                continue
            app = host.application
            if app is None:
                app = self.apps.add(
                    Application(
                        organization_id=conn.organization_id,
                        source=AppSource.npm.value,
                        auto_update=True,
                        **self._app_values(det),
                    )
                )
                host.application = app
                result.applications_created += 1
                created_apps.append(app.name)
            elif app.auto_update:
                values = self._app_values(det)
                if any(getattr(app, k) != v for k, v in values.items()):
                    for k, v in values.items():
                        setattr(app, k, v)
                    result.applications_updated += 1

        removed_domains: list[str] = []
        for npm_id, host in existing.items():
            if npm_id not in seen and host.removed_at is None:
                host.removed_at = now
                result.hosts_removed += 1
                removed_domains.append(host.primary_domain)
        await self.db.flush()
        return created_apps, removed_domains

    @staticmethod
    def _host_values(raw: dict[str, Any], det: Detection) -> dict[str, Any]:
        access_list = raw.get("access_list") if isinstance(raw.get("access_list"), dict) else None
        acl_name = None
        if access_list:
            acl_name = str(access_list.get("name") or f"#{access_list.get('id')}")[:255]
        elif raw.get("access_list_id"):
            acl_name = f"#{raw['access_list_id']}"
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        return {
            "domain_names": [str(d) for d in raw.get("domain_names") or []],
            "forward_scheme": str(raw.get("forward_scheme") or "http")[:8],
            "forward_host": str(raw.get("forward_host") or "")[:255],
            "forward_port": int(raw.get("forward_port") or 0),
            "enabled": bool(raw.get("enabled", True)),
            "nginx_online": meta.get("nginx_online") is not False,
            "ssl": bool(raw.get("certificate_id")),
            "ssl_forced": bool(raw.get("ssl_forced")),
            "access_list": acl_name,
            "forward_auth": det.forward_auth,
            "detected_app_type": det.app_type,
            "detected_auth": det.auth_method,
            "labels": det.labels,
            "warnings": det.warnings,
        }

    @staticmethod
    def _app_values(det: Detection) -> dict[str, Any]:
        return {
            "name": det.name,
            "app_type": det.app_type,
            "url": det.url,
            "auth_method": det.auth_method,
            "tags": det.tags,
            "description": det.description,
        }
