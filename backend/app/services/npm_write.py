"""Authentik-bescherming zetten of weghalen op NPM proxy hosts, met controle en rollback.

Een mislukte schrijfactie zet een host in NPM meteen offline: NPM test de
nieuwe config met `nginx -t`, verwijdert ze bij een fout en antwoordt toch 200
(onderzoek 03, A9). Daarom gaat elke wijziging zo:

1. Live de host uit NPM lezen en controleren dat hij niet veranderde sinds het
   voorbeeld dat de gebruiker zag (modified_on).
2. Plan maken (npm_protect.py); weigeren bij elk blokkerend punt.
3. Controle vooraf: de host aanspreken zoals een bezoeker zonder sessie
   (npm_probe.py), zodat VaultX weet dat die controle werkt.
4. Wijziging schrijven en meteen `meta.nginx_online` in het antwoord nakijken.
5. Host opnieuw lezen en opnieuw aanspreken: zonder sessie hoort een bezoeker
   nu naar Authentik doorverwezen te worden.
6. Faalt 4 of 5: de momentopname van stap 1 terugschrijven en nagaan dat de
   host weer online is.

Elke stap staat in het journaal (npm_changes) en de uitkomst in de auditlog.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
    PermissionDeniedError,
    UpstreamError,
)
from app.models import DiscoveredHost, NpmChange, NpmChangeStatus, NpmConnection
from app.repositories import DiscoveredHostRepository, NpmChangeRepository
from app.services.npm import ClientFactory, NpmService
from app.services.npm_client import NPMClient, NPMError
from app.services.npm_probe import Prober, ProbeResult, ProbeTarget, judge, make_prober
from app.services.npm_protect import Action, Plan, applied, make_plan
from app.services.principal import Actor, Principal

log = logging.getLogger(__name__)

# Een wijziging die zo lang op 'running' blijft staan, is onderbroken (node herstart).
STALE_AFTER = timedelta(minutes=10)
ROLLBACK_ATTEMPTS = 3
# nginx herlaadt asynchroon; geef de nieuwe workers even de tijd voor de controle.
PROBE_ATTEMPTS = 3
PROBE_DELAY_SECONDS = 1.0
MAX_NGINX_ERROR = 2000


@dataclass(slots=True)
class PlanView:
    plan: Plan
    domain: str
    probe: ProbeTarget | None

    @property
    def can_apply(self) -> bool:
        return self.plan.has_changes


def probe_target(conn: NpmConnection, raw: dict[str, Any]) -> ProbeTarget | None:
    domain = next((d for d in raw.get("domain_names") or [] if isinstance(d, str) and "*" not in d), None)
    if not domain:
        return None
    address = conn.probe_host or urlsplit(conn.base_url).hostname or ""
    https = bool(raw.get("certificate_id"))
    return ProbeTarget(
        scheme="https" if https else "http",
        address=address,
        port=conn.probe_https_port if https else conn.probe_http_port,
        domain=domain,
    )


class _Abort(Exception):
    """Stopt de wijziging voor er iets in NPM veranderde."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NpmWriteService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        client_factory: ClientFactory | None = None,
        prober: Prober | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.npm = NpmService(db, settings, client_factory)
        self.hosts = DiscoveredHostRepository(db)
        self.changes = NpmChangeRepository(db)
        self.prober = prober or make_prober(settings.npm_http_timeout_seconds)
        self.audit = self.npm.audit

    # ------------------------------------------------------------ helpers

    async def _load(
        self, p: Principal, org_id: UUID, conn_id: UUID, host_id: UUID, action: str
    ) -> tuple[NpmConnection, DiscoveredHost]:
        conn = await self.npm.get_connection(p, org_id, conn_id)
        host = await self.hosts.in_connection(conn_id, host_id)
        if host is None:
            raise NotFoundError("Host niet gevonden")
        if not p.can_manage_org(org_id):
            raise await self.audit.deny(
                f"npm_host.{action}",
                p.actor,
                organization_id=org_id,
                target_type="npm_host",
                target_id=host_id,
            )
        if host.removed_at is not None:
            raise InvalidOperationError("Deze host staat niet meer in NPM")
        return conn, host

    async def _client(self, conn: NpmConnection) -> NPMClient:
        try:
            secret = self.npm._open(conn)
        except Exception as exc:  # InvalidTag, ValueError
            raise UpstreamError(
                "Het opgeslagen NPM-wachtwoord kan niet ontsleuteld worden. Vul het opnieuw in."
            ) from exc
        client = self.npm.client_factory(conn)
        try:
            await client.login(conn.identity, secret)
        except NPMError:
            await client.aclose()
            raise
        return client

    @staticmethod
    def _connection_checks(plan: Plan, conn: NpmConnection) -> None:
        if not conn.write_enabled:
            plan.block(
                "write_disabled",
                "Schrijven naar deze NPM staat uit. Zet 'VaultX mag wijzigen' aan op de koppeling; het "
                "NPM-account heeft dan 'Proxy Hosts: Manage' nodig.",
            )

    # ------------------------------------------------------------ voorbeeld

    async def preview(
        self, p: Principal, org_id: UUID, conn_id: UUID, host_id: UUID, action: Action
    ) -> PlanView:
        conn, host = await self._load(p, org_id, conn_id, host_id, action)
        await self.db.commit()  # geen open transactie tijdens het netwerkverkeer
        try:
            client = await self._client(conn)
            async with client:
                raw = await client.proxy_host(host.npm_id)
        except NPMError as exc:
            raise UpstreamError(exc.message) from exc
        plan = make_plan(action, raw, conn.authentik_outpost_url)
        self._connection_checks(plan, conn)
        target = probe_target(conn, raw)
        if target is None and not plan.blocked:
            plan.warn(
                "no_probe",
                "De host heeft enkel wildcard-domeinen; VaultX kan het resultaat niet zelf controleren.",
            )
        return PlanView(plan=plan, domain=host.primary_domain, probe=target)

    # ------------------------------------------------------------ uitvoeren

    async def apply(
        self,
        p: Principal,
        org_id: UUID,
        conn_id: UUID,
        host_id: UUID,
        action: Action,
        *,
        expected_modified_on: str | None,
        verify: bool = True,
    ) -> NpmChange:
        conn, host = await self._load(p, org_id, conn_id, host_id, action)
        actor = p.actor
        if not conn.write_enabled:
            raise InvalidOperationError("Schrijven naar deze NPM staat uit op de koppeling")

        now = datetime.now(UTC)
        for stale in await self.changes.stale_running(conn.id, host.npm_id, now - STALE_AFTER):
            stale.status = NpmChangeStatus.interrupted.value
            stale.finished_at = now
            stale.message = (
                "Onderbroken (herstartte VaultX tijdens de wijziging?). Controleer de host in NPM."
            )
        change = self.changes.add(
            NpmChange(
                organization_id=org_id,
                connection_id=conn.id,
                host_id=host.id,
                npm_id=host.npm_id,
                domain=host.primary_domain[:255],
                action=action,
                status=NpmChangeStatus.running.value,
                verified=verify,
                actor_user_id=p.user.id,
                actor_label=actor.label,
                before={},
                created_at=now,
            )
        )
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ConflictError("Er loopt al een wijziging op deze host") from exc
        change_id = change.id

        final_raw: dict[str, Any] | None = None
        result: dict[str, Any] = {}
        try:
            client = await self._client(conn)
            async with client:
                final_raw = await self._run(
                    client, conn, host.npm_id, action, expected_modified_on, verify, result
                )
        except _Abort as exc:
            result.setdefault("status", NpmChangeStatus.refused.value)
            result["message"] = exc.message
        except NPMError as exc:
            # Fout vóór er iets geschreven werd (aanmelden, lezen): niets veranderd.
            result.setdefault("status", NpmChangeStatus.refused.value)
            result["message"] = f"NPM: {exc.message}"
        except Exception:
            log.exception("NPM-wijziging %s liep onverwacht mis", change_id)
            result["status"] = NpmChangeStatus.interrupted.value
            result["message"] = "Onverwachte fout tijdens de wijziging. Controleer de host in NPM."
            raise
        finally:
            await self._finish(change_id, conn.id, action, result, final_raw, actor)
        return await self._get_change(change_id)

    async def _run(
        self,
        client: NPMClient,
        conn: NpmConnection,
        npm_id: int,
        action: Action,
        expected_modified_on: str | None,
        verify: bool,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        raw = await client.proxy_host(npm_id)
        if expected_modified_on and raw.get("modified_on") != expected_modified_on:
            raise _Abort("De host werd in NPM gewijzigd sinds het voorbeeld. Bekijk het voorbeeld opnieuw.")
        plan = make_plan(action, raw, conn.authentik_outpost_url)
        result["before"] = plan.before
        if plan.blocked:
            raise _Abort(" ".join(c.message for c in plan.checks if c.level == "block"))
        if not plan.has_changes:
            raise _Abort("Er is niets te wijzigen.")
        result["after"] = plan.after
        result["steps"] = plan.steps

        target = probe_target(conn, raw)
        before_probe: ProbeResult | None = None
        if verify:
            if target is None:
                raise _Abort(
                    "VaultX kan deze host niet controleren (enkel wildcard-domeinen). "
                    "Voer uit zonder controle."
                )
            before_probe = await self.prober(target)
            result["probe_before"] = before_probe.as_dict()
            if before_probe.error:
                raise _Abort(
                    f"VaultX kan de host niet bereiken om het resultaat te controleren "
                    f"({before_probe.url}: {before_probe.error}). Stel het controleadres in op de "
                    "koppeling, of voer uit zonder controle."
                )

        async def rollback(reason: str) -> dict[str, Any]:
            return await self._rollback(client, npm_id, plan, reason, result, before_probe, target)

        # Vanaf hier verandert NPM. Elke fout leidt tot terugzetten.
        try:
            saved = await client.update_proxy_host(npm_id, plan.after)
        except NPMError as exc:
            return await rollback(f"NPM weigerde de wijziging: {exc.message}")
        meta = saved.get("meta") or {}
        if meta.get("nginx_online") is False:
            result["nginx_error"] = str(meta.get("nginx_err") or "")[:MAX_NGINX_ERROR]
            return await rollback("nginx in NPM weigerde de nieuwe config; de host stond even offline.")
        try:
            current = await client.proxy_host(npm_id)
        except NPMError as exc:
            return await rollback(f"Host niet terug te lezen: {exc.message}")
        drift = not applied(plan, current)

        if verify and target is not None:
            after_probe = await self._probe_until_ok(action, before_probe, target)
            result["probe_after"] = after_probe.as_dict()
            reason = judge(action, before_probe, after_probe)
            if reason:
                return await rollback(reason)

        result["status"] = NpmChangeStatus.applied.value
        if action == "protect":
            result["message"] = "Authentik-bescherming staat aan."
        else:
            result["message"] = "Authentik-bescherming is weggehaald."
        if not verify:
            result["message"] += " Niet gecontroleerd: VaultX sprak de host achteraf niet aan."
        if drift:
            result["message"] += (
                " Let op: NPM toont nu niet exact wat VaultX schreef; kijk de host na in NPM."
            )
        return current

    async def _probe_until_ok(
        self, action: Action, before: ProbeResult | None, target: ProbeTarget
    ) -> ProbeResult:
        result = await self.prober(target)
        for _ in range(PROBE_ATTEMPTS - 1):
            if judge(action, before, result) is None:
                break
            await asyncio.sleep(PROBE_DELAY_SECONDS)
            result = await self.prober(target)
        return result

    async def _rollback(
        self,
        client: NPMClient,
        npm_id: int,
        plan: Plan,
        reason: str,
        result: dict[str, Any],
        before_probe: ProbeResult | None,
        target: ProbeTarget | None,
    ) -> dict[str, Any]:
        last_error = ""
        for attempt in range(ROLLBACK_ATTEMPTS):
            try:
                restored = await client.update_proxy_host(npm_id, plan.before)
            except NPMError as exc:
                last_error = exc.message
            else:
                if (restored.get("meta") or {}).get("nginx_online") is not False:
                    result["status"] = NpmChangeStatus.rolled_back.value
                    result["message"] = f"{reason} De vorige config is teruggezet."
                    if before_probe is not None and target is not None:
                        restored_probe = await self.prober(target)
                        for _ in range(PROBE_ATTEMPTS - 1):
                            if restored_probe.status == before_probe.status:
                                break
                            await asyncio.sleep(PROBE_DELAY_SECONDS)
                            restored_probe = await self.prober(target)
                        result["probe_restored"] = restored_probe.as_dict()
                        if restored_probe.status != before_probe.status:
                            result["message"] += (
                                f" Na het terugzetten antwoordt de host {restored_probe.summary()} "
                                f"(ervoor: {before_probe.summary()})."
                            )
                    return restored
                last_error = str((restored.get("meta") or {}).get("nginx_err") or "nginx-fout")[:300]
            await asyncio.sleep(0.5 * (attempt + 1))
        result["status"] = NpmChangeStatus.rollback_failed.value
        result["message"] = (
            f"{reason} Terugzetten is NIET gelukt ({last_error}). De host staat mogelijk offline: zet in NPM "
            "de Advanced-config en custom locations terug zoals in 'Voor' van deze wijziging."
        )
        try:
            return await client.proxy_host(npm_id)
        except NPMError:
            return {}

    async def _finish(
        self,
        change_id: UUID,
        conn_id: UUID,
        action: str,
        result: dict[str, Any],
        final_raw: dict[str, Any] | None,
        actor: Actor,
    ) -> None:
        change = await self.changes.get(change_id)
        conn = await self.npm.connections.get(conn_id)
        if change is None or conn is None:
            return
        change.status = result.get("status", NpmChangeStatus.interrupted.value)
        change.message = result.get("message")
        change.before = result.get("before") or {}
        change.after = result.get("after")
        change.probe_before = result.get("probe_before")
        change.probe_after = result.get("probe_after")
        change.nginx_error = result.get("nginx_error")
        change.finished_at = datetime.now(UTC)
        if final_raw and final_raw.get("id") == change.npm_id:
            await self.npm.refresh_host(conn, final_raw)
        outcome = "success" if change.status == NpmChangeStatus.applied.value else "failure"
        await self.audit.record(
            f"npm_host.{action}",
            actor,
            outcome=outcome,
            organization_id=change.organization_id,
            target_type="npm_host",
            target_id=change.host_id,
            details={
                "domain": change.domain,
                "npm_id": change.npm_id,
                "connection": conn.name,
                "status": change.status,
                "verified": change.verified,
                "message": change.message,
                "steps": result.get("steps", []),
                "change_id": str(change.id),
            },
        )
        await self.db.commit()

    async def _get_change(self, change_id: UUID) -> NpmChange:
        change = await self.changes.get(change_id)
        assert change is not None
        await self.db.refresh(change)
        return change

    # ------------------------------------------------------------ journaal

    async def list_changes(
        self, p: Principal, org_id: UUID, conn_id: UUID, host_id: UUID | None = None
    ) -> list[NpmChange]:
        await self.npm.get_connection(p, org_id, conn_id)
        # Het journaal bevat de volledige Advanced-config, met mogelijk geheimen erin.
        if not p.can_manage_org(org_id):
            raise PermissionDeniedError("Enkel beheerders van de organisatie zien het wijzigingsjournaal")
        npm_id = None
        if host_id is not None:
            host = await self.hosts.in_connection(conn_id, host_id)
            if host is None:
                raise NotFoundError("Host niet gevonden")
            npm_id = host.npm_id
        return await self.changes.for_connection(conn_id, npm_id=npm_id)
