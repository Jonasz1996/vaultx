"""App publiceren en depubliceren via NPM (fase 6).

Een beheerder zet een nieuwe app in één stap online: VaultX maakt de proxy host
in NPM aan (domein, upstream, certificaat), beschermt hem met Authentik en zet
hem in de catalogus. Het bouwt op fase 3 en 4:

1. Plan (npm_publish.py): het domein is nog vrij in NPM, het certificaat dekt het,
   en de Authentik-config is dezelfde als bij "Beschermen met Authentik".
2. Eerst de Authentik-kant (provider, applicatie, groepen, outpost), zodat de
   host vanaf de eerste seconde beschermd online komt. Een bezoeker ziet nooit
   een onbeschermde app.
3. De host aanmaken in NPM, opnieuw lezen en `meta.nginx_online` nakijken.
4. De host aanspreken zoals een bezoeker zonder sessie: met Authentik hoort dat
   een doorverwijzing naar Authentik te geven.
5. Faalt 3 of 4: de host weer verwijderen en de Authentik-kant terugdraaien.
6. De host inlezen in de catalogus en markeren als gepubliceerd door VaultX.

Depubliceren verwijdert enkel hosts die VaultX zelf publiceerde, ruimt in
Authentik op wat VaultX aanmaakte en haalt het catalogusitem weg. De volledige
host staat in het journaal, om hem desnoods met de hand terug te zetten.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError, InvalidOperationError, NotFoundError, UpstreamError
from app.models import (
    AppSource,
    AuthentikProtection,
    DiscoveredHost,
    NpmChange,
    NpmChangeStatus,
    NpmConnection,
)
from app.repositories import OrganizationRepository, TeamRepository
from app.services.authentik_client import AuthentikClient, AuthentikError
from app.services.authentik_protect import AkState, access_label
from app.services.npm_client import NPMClient, NPMError
from app.services.npm_probe import ProbeResult, judge
from app.services.npm_protect import Check
from app.services.npm_publish import PublishInput, PublishPlan, host_snapshot, plan_publish
from app.services.npm_write import (
    MAX_NGINX_ERROR,
    STALE_AFTER,
    NpmWriteService,
    _Abort,
    _audit_authentik,
    probe_target,
)
from app.services.principal import Principal

log = logging.getLogger(__name__)

# Zolang NPM nog geen id gaf (zie NpmChange): één publicatie per koppeling tegelijk.
PENDING_NPM_ID = 0
DELETE_ATTEMPTS = 3
UPSTREAM_TIMEOUT = 3.0

UpstreamChecker = Callable[[str, int], Awaitable[str | None]]


async def check_upstream(host: str, port: int) -> str | None:
    """TCP-verbinding vanaf de VaultX-server; None als ze lukt, anders de fout."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), UPSTREAM_TIMEOUT)
    except TimeoutError:
        return "time-out"
    except socket.gaierror:
        return "naam onbekend"
    except OSError as exc:
        return exc.strerror or exc.__class__.__name__
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return None


@dataclass(slots=True)
class PublishView:
    plan: PublishPlan
    probe_url: str | None = None
    authentik: dict[str, Any] | None = None
    authentik_steps: list[str] = field(default_factory=list)

    @property
    def steps(self) -> list[str]:
        return self.authentik_steps + self.plan.steps

    @property
    def can_apply(self) -> bool:
        return not self.plan.blocked and bool(self.plan.body)


@dataclass(slots=True)
class UnpublishView:
    host: DiscoveredHost
    checks: list[Check] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    before: dict[str, Any] = field(default_factory=dict)
    authentik: dict[str, Any] | None = None

    @property
    def can_apply(self) -> bool:
        return not any(c.level == "block" for c in self.checks)


class PublishService(NpmWriteService):
    def __init__(self, *args: Any, upstream_checker: UpstreamChecker | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.upstream_checker = upstream_checker or check_upstream

    # ------------------------------------------------------------ helpers

    async def _conn_for_write(self, p: Principal, org_id: UUID, conn_id: UUID, action: str) -> NpmConnection:
        conn = await self.npm.get_connection(p, org_id, conn_id)
        if not p.can_manage_org(org_id):
            raise await self.audit.deny(
                f"npm_host.{action}",
                p.actor,
                organization_id=org_id,
                target_type="npm_connection",
                target_id=conn_id,
            )
        return conn

    async def _publish_context(self, org_id: UUID, app_name: str) -> dict[str, Any]:
        org = await OrganizationRepository(self.db).get(org_id)
        teams = await TeamRepository(self.db).list_for_org(org_id)
        return {
            "org_slug": org.slug if org else "",
            "team_slugs": {t.slug for t in teams},
            "app_name": app_name,
            "previous": None,
        }

    async def certificates(self, p: Principal, org_id: UUID, conn_id: UUID) -> list[dict[str, Any]]:
        conn = await self._conn_for_write(p, org_id, conn_id, "publish")
        await self.db.commit()
        try:
            client = await self._client(conn)
            async with client:
                certs = await client.certificates()
        except NPMError as exc:
            raise UpstreamError(exc.message) from exc
        return sorted(certs, key=lambda c: str(c.get("nice_name") or c.get("domain_names") or ""))

    async def _plan(self, client: NPMClient, conn: NpmConnection, data: PublishInput) -> PublishPlan:
        hosts = await client.proxy_hosts_raw()
        certs = await client.certificates() if data.certificate_id else []
        plan = plan_publish(
            data, existing_hosts=hosts, certificates=certs, outpost_url=conn.authentik_outpost_url
        )
        if not conn.write_enabled:
            plan.block(
                "write_disabled",
                "Schrijven naar deze NPM staat uit. Zet 'VaultX mag wijzigen' aan op de koppeling; het "
                "NPM-account heeft dan 'Proxy Hosts: Manage' en 'Certificates: View' nodig.",
            )
        return plan

    @staticmethod
    def _raw(plan: PublishPlan) -> dict[str, Any]:
        """De host zoals hij in NPM komt te staan, voor probe en Authentik-plan."""
        return {"id": PENDING_NPM_ID, **plan.body}

    # ------------------------------------------------------------ publiceren: voorbeeld

    async def preview_publish(
        self, p: Principal, org_id: UUID, conn_id: UUID, data: PublishInput, access: str
    ) -> PublishView:
        conn = await self._conn_for_write(p, org_id, conn_id, "publish")
        ctx = await self._publish_context(org_id, data.name.strip())
        await self.db.commit()  # geen open transactie tijdens het netwerkverkeer
        try:
            client = await self._client(conn)
            async with client:
                plan = await self._plan(client, conn, data)
        except NPMError as exc:
            raise UpstreamError(exc.message) from exc
        view = PublishView(plan=plan)
        if plan.blocked:
            return view
        raw = self._raw(plan)
        target = probe_target(conn, raw)
        view.probe_url = target.url if target else None

        error = await self.upstream_checker(data.forward_host, data.forward_port)
        if error:
            plan.warn(
                "upstream_unreachable",
                f"VaultX bereikt {data.forward_host}:{data.forward_port} niet ({error}). Staat NPM in een "
                "ander netwerk, dan kan NPM de app misschien wel bereiken; anders geeft de app na de "
                "aanmelding een 502.",
            )
        else:
            plan.info("upstream_ok", f"VaultX bereikt {data.forward_host}:{data.forward_port}.")

        if data.protect:
            await self._preview_authentik_publish(view, conn, raw, access, ctx)
        return view

    async def _preview_authentik_publish(
        self, view: PublishView, conn: NpmConnection, raw: dict[str, Any], access: str, ctx: dict[str, Any]
    ) -> None:
        plan = view.plan
        if not conn.authentik_outpost_pk:
            plan.info(
                "authentik_manual",
                "VaultX maakt in Authentik niets aan: er moet al een proxy provider voor dit domein zijn "
                "(bv. een domain-level provider), of kies op de koppeling een outpost.",
            )
            return
        if not self.settings.authentik_api_enabled:
            plan.block(
                "authentik_api_missing",
                "Op de koppeling staat een Authentik-outpost, maar de Authentik-API is niet ingesteld op de "
                "VaultX-server (VAULTX_AUTHENTIK_API_TOKEN).",
            )
            return
        try:
            async with self.authentik_factory() as client:
                ak = await self._ak_plan(client, conn, raw, access, ctx)
        except AuthentikError as exc:
            plan.block("authentik_error", f"Authentik: {exc.message}")
            return
        plan.checks.extend(ak.checks)
        view.authentik_steps = ak.steps
        view.authentik = {**ak.summary(), "access_label": access_label(access, ctx["org_slug"])}

    # ------------------------------------------------------------ publiceren: uitvoeren

    async def publish(
        self,
        p: Principal,
        org_id: UUID,
        conn_id: UUID,
        data: PublishInput,
        *,
        access: str = "organization",
        verify: bool = True,
    ) -> NpmChange:
        conn = await self._conn_for_write(p, org_id, conn_id, "publish")
        if not conn.write_enabled:
            raise InvalidOperationError("Schrijven naar deze NPM staat uit op de koppeling")
        use_authentik = data.protect and bool(conn.authentik_outpost_pk)
        if use_authentik and not self.settings.authentik_api_enabled:
            raise InvalidOperationError("De Authentik-API is niet ingesteld op de VaultX-server")
        ctx = await self._publish_context(org_id, data.name.strip())
        ctx["use_authentik"] = use_authentik
        ctx["access"] = access
        change_id = await self._start_change(
            p, org_id, conn, None, PENDING_NPM_ID, data.domain, "publish", verify
        )

        result: dict[str, Any] = {}
        npm_raw: dict[str, Any] | None = None
        try:
            client = await self._client(conn)
            async with client:
                npm_raw = await self._run_publish(client, conn, data, verify, ctx, result)
        except _Abort as exc:
            result.setdefault("status", NpmChangeStatus.refused.value)
            result["message"] = exc.message
        except NPMError as exc:
            result.setdefault("status", NpmChangeStatus.refused.value)
            result["message"] = f"NPM: {exc.message}"
        except Exception:
            log.exception("Publicatie %s liep onverwacht mis", change_id)
            result["status"] = NpmChangeStatus.interrupted.value
            result["message"] = "Onverwachte fout tijdens het publiceren. Controleer NPM en Authentik."
            raise
        finally:
            await self._finish_publish(change_id, conn.id, data, result, npm_raw, p)
        return await self._get_change(change_id)

    async def _start_change(
        self,
        p: Principal,
        org_id: UUID,
        conn: NpmConnection,
        host_id: UUID | None,
        npm_id: int,
        domain: str,
        action: str,
        verify: bool,
    ) -> UUID:
        now = datetime.now(UTC)
        for stale in await self.changes.stale_running(conn.id, npm_id, now - STALE_AFTER):
            stale.status = NpmChangeStatus.interrupted.value
            stale.finished_at = now
            stale.message = "Onderbroken (herstartte VaultX tijdens de wijziging?). Controleer NPM."
        change = self.changes.add(
            NpmChange(
                organization_id=org_id,
                connection_id=conn.id,
                host_id=host_id,
                npm_id=npm_id,
                domain=domain.strip().lower()[:255],
                action=action,
                status=NpmChangeStatus.running.value,
                verified=verify,
                actor_user_id=p.user.id,
                actor_label=p.actor.label,
                before={},
                created_at=now,
            )
        )
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            what = "publicatie op deze NPM-koppeling" if action == "publish" else "wijziging op deze host"
            raise ConflictError(f"Er loopt al een {what}") from exc
        return change.id

    async def _run_publish(
        self,
        client: NPMClient,
        conn: NpmConnection,
        data: PublishInput,
        verify: bool,
        ctx: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        plan = await self._plan(client, conn, data)
        result["domain"] = plan.domain
        if plan.blocked:
            raise _Abort(" ".join(c.message for c in plan.checks if c.level == "block"))
        result["after"] = plan.body
        result["steps"] = plan.steps
        raw = self._raw(plan)
        target = probe_target(conn, raw)
        if verify and target is None:
            raise _Abort("VaultX kan deze host niet controleren. Publiceer zonder controle.")

        ak_client: AuthentikClient | None = self.authentik_factory() if ctx["use_authentik"] else None
        ak_state: AkState | None = None
        try:
            if ak_client is not None:
                # Draait bij een fout zelf terug in Authentik en stopt met _Abort; NPM is dan ongemoeid.
                ak_state = await self._apply_authentik(ak_client, conn, raw, ctx, result)
                result["steps"] = result["authentik"]["plan"]["steps"] + plan.steps

            async def undo(reason: str, npm_id: int | None) -> None:
                await self._undo_publish(client, npm_id, reason, result)
                if ak_client is not None and ak_state is not None:
                    await self._undo_authentik(ak_client, ak_state, result)

            try:
                created = await client.create_proxy_host(plan.body)
            except NPMError as exc:
                await undo(f"NPM weigerde de nieuwe host: {exc.message}", None)
                return None
            npm_id = int(created["id"])
            result["npm_id"] = npm_id
            try:
                current = await client.proxy_host(npm_id)
            except NPMError as exc:
                await undo(f"Nieuwe host niet terug te lezen: {exc.message}", npm_id)
                return None
            meta = current.get("meta") or {}
            if meta.get("nginx_online") is False:
                result["nginx_error"] = str(meta.get("nginx_err") or "")[:MAX_NGINX_ERROR]
                await undo("nginx in NPM weigerde de config van de nieuwe host.", npm_id)
                return None

            if verify and target is not None:
                ak_plan = (result.get("authentik") or {}).get("plan") or {}
                slow = any(
                    ak_plan.get(k) for k in ("create_provider", "create_application", "assign_outpost")
                )
                after = await self._probe_publish(data.protect, target, slow=slow)
                result["probe_after"] = after.as_dict()
                reason = self._judge_publish(data.protect, after, slow)
                if reason:
                    await undo(reason, npm_id)
                    return None

            result["status"] = NpmChangeStatus.applied.value
            result["message"] = (
                "De app staat online, beschermd met Authentik."
                if data.protect
                else "De app staat online, zonder Authentik."
            )
            if not verify:
                result["message"] += " Niet gecontroleerd: VaultX sprak de host achteraf niet aan."
            elif (
                not data.protect
                and (after := result.get("probe_after"))
                and (after.get("status") or 0) >= 500
            ):
                result["message"] += (
                    f" Let op: de host antwoordt {after['status']}; NPM bereikt de app waarschijnlijk niet."
                )
            return current
        finally:
            if ak_client is not None:
                await ak_client.aclose()

    async def _probe_publish(self, protect: bool, target: Any, *, slow: bool) -> ProbeResult:
        from app.services import npm_write

        attempts = npm_write.PROBE_ATTEMPTS_AFTER_AUTHENTIK if slow else npm_write.PROBE_ATTEMPTS
        delay = npm_write.PROBE_DELAY_AFTER_AUTHENTIK if slow else npm_write.PROBE_DELAY_SECONDS
        result = await self.prober(target)
        for _ in range(attempts - 1):
            if self._judge_publish(protect, result, slow) is None:
                break
            await asyncio.sleep(delay)
            result = await self.prober(target)
        return result

    @staticmethod
    def _judge_publish(protect: bool, after: ProbeResult, slow: bool) -> str | None:
        if protect:
            return judge("protect", None, after, provider_by_vaultx=slow)
        if after.error:
            return f"De nieuwe host is niet bereikbaar via NPM: {after.error}."
        return None

    async def _undo_publish(
        self, client: NPMClient, npm_id: int | None, reason: str, result: dict[str, Any]
    ) -> None:
        """Nieuwe host weer weghalen (als hij er al was) en de uitkomst in result zetten."""
        result["status"] = NpmChangeStatus.rolled_back.value
        if npm_id is None:
            result["message"] = f"{reason} Er is niets in NPM aangemaakt."
            return
        last_error = ""
        for attempt in range(DELETE_ATTEMPTS):
            try:
                await client.delete_proxy_host(npm_id)
            except NPMError as exc:
                last_error = exc.message
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            result["message"] = f"{reason} De nieuwe host is weer verwijderd uit NPM."
            result["removed"] = True
            return
        result["status"] = NpmChangeStatus.rollback_failed.value
        result["message"] = (
            f"{reason} Verwijderen lukte NIET ({last_error}). Verwijder proxy host #{npm_id} met de "
            "hand in NPM."
        )

    async def _finish_publish(
        self,
        change_id: UUID,
        conn_id: UUID,
        data: PublishInput,
        result: dict[str, Any],
        npm_raw: dict[str, Any] | None,
        p: Principal,
    ) -> None:
        change = await self.changes.get(change_id)
        conn = await self.npm.connections.get(conn_id)
        if change is None or conn is None:
            return
        change.status = result.get("status", NpmChangeStatus.interrupted.value)
        change.message = result.get("message")
        change.after = result.get("after")
        change.probe_after = result.get("probe_after")
        change.nginx_error = result.get("nginx_error")
        change.authentik = result.get("authentik")
        change.finished_at = datetime.now(UTC)
        if result.get("domain"):
            change.domain = result["domain"][:255]
        # Ook bij een teruggedraaide publicatie: welk id NPM kort gaf (voor wie de logs naleest).
        if result.get("npm_id"):
            change.npm_id = result["npm_id"]
        host: DiscoveredHost | None = None
        if change.status == NpmChangeStatus.applied.value and npm_raw:
            host = await self.npm.refresh_host(conn, npm_raw)
            if host is not None:
                host.vaultx_published = True
                change.host_id = host.id
            await self._store_publish_protection(change, result)
        await self.audit.record(
            "npm_host.publish",
            p.actor,
            outcome="success" if change.status == NpmChangeStatus.applied.value else "failure",
            organization_id=change.organization_id,
            target_type="npm_host",
            target_id=change.host_id,
            details={
                "domain": change.domain,
                "npm_id": change.npm_id or None,
                "connection": conn.name,
                "upstream": f"{data.forward_scheme}://{data.forward_host}:{data.forward_port}",
                "certificate_id": data.certificate_id or None,
                "protect": data.protect,
                "theme_css_url": data.theme_css_url,
                "status": change.status,
                "verified": change.verified,
                "message": change.message,
                "steps": result.get("steps", []),
                "change_id": str(change.id),
                "application": (
                    host.application.name if host is not None and host.application is not None else None
                ),
                **({"authentik": _audit_authentik(result["authentik"])} if result.get("authentik") else {}),
            },
        )
        await self.db.commit()

    async def _store_publish_protection(self, change: NpmChange, result: dict[str, Any]) -> None:
        state = (result.get("authentik") or {}).get("state")
        if not state:
            return
        values = {k: v for k, v in state.items() if k in AkState.__slots__}  # type: ignore[attr-defined]
        row = await self.protections.for_host(change.connection_id, change.npm_id)
        if row is not None:  # restje van een vroegere host met hetzelfde id
            await self.protections.delete(row)
            await self.db.flush()
        self.protections.add(
            AuthentikProtection(
                organization_id=change.organization_id,
                connection_id=change.connection_id,
                npm_id=change.npm_id,
                **values,
            )
        )

    # ------------------------------------------------------------ depubliceren

    async def _load_published(
        self, p: Principal, org_id: UUID, conn_id: UUID, host_id: UUID
    ) -> tuple[NpmConnection, DiscoveredHost]:
        conn = await self._conn_for_write(p, org_id, conn_id, "unpublish")
        host = await self.hosts.in_connection(conn_id, host_id)
        if host is None:
            raise NotFoundError("Host niet gevonden")
        return conn, host

    async def _unpublish_checks(self, conn: NpmConnection, host: DiscoveredHost) -> list[Check]:
        checks: list[Check] = []
        if not conn.write_enabled:
            checks.append(
                Check("write_disabled", "block", "Schrijven naar deze NPM staat uit op de koppeling.")
            )
        if not host.vaultx_published:
            checks.append(
                Check(
                    "not_published",
                    "block",
                    "VaultX publiceerde deze host niet en verwijdert hem dus ook niet. Doe dat in NPM.",
                )
            )
        if host.removed_at is not None:
            checks.append(Check("host_removed", "block", "Deze host staat niet meer in NPM."))
        await self.db.refresh(host, ["application"])
        app = host.application
        if app is not None:
            await self.db.refresh(app, ["login"])
            if app.login is not None:
                checks.append(
                    Check(
                        "app_login",
                        "block",
                        f"'{app.name}' heeft een automatische login. Haal die eerst weg op de pagina "
                        "van de app.",
                    )
                )
        return checks

    async def preview_unpublish(
        self, p: Principal, org_id: UUID, conn_id: UUID, host_id: UUID
    ) -> UnpublishView:
        conn, host = await self._load_published(p, org_id, conn_id, host_id)
        view = UnpublishView(host=host, checks=await self._unpublish_checks(conn, host))
        previous = await self.protections.for_host(conn.id, host.npm_id)
        state = AkState.from_row(previous) if previous else None
        await self.db.commit()
        if not view.can_apply:
            return view
        try:
            client = await self._client(conn)
            async with client:
                raw = await client.proxy_host(host.npm_id)
        except NPMError as exc:
            raise UpstreamError(exc.message) from exc
        view.before = host_snapshot(raw)
        view.steps.append(f"NPM: proxy host {host.primary_domain} (#{host.npm_id}) verwijderen.")
        if state is not None:
            view.steps += state.removal_steps()
            view.authentik = {"remove": state.as_dict()}
            if state.leftovers() and not self.settings.authentik_api_enabled:
                view.checks.append(
                    Check(
                        "authentik_api_missing",
                        "warn",
                        "De Authentik-API is niet ingesteld (VAULTX_AUTHENTIK_API_TOKEN); VaultX kan "
                        f"{state.leftovers()} niet opruimen.",
                    )
                )
        if host.application is not None:
            view.steps.append(f"VaultX: '{host.application.name}' uit de catalogus halen.")
        view.checks.append(
            Check(
                "unpublish_final",
                "warn",
                "De app is daarna niet meer via NPM bereikbaar. De volledige host staat in het journaal.",
            )
        )
        return view

    async def unpublish(self, p: Principal, org_id: UUID, conn_id: UUID, host_id: UUID) -> NpmChange:
        conn, host = await self._load_published(p, org_id, conn_id, host_id)
        blocks = [c.message for c in await self._unpublish_checks(conn, host) if c.level == "block"]
        if blocks:
            raise InvalidOperationError(" ".join(blocks))
        previous = await self.protections.for_host(conn.id, host.npm_id)
        ctx = {"previous": AkState.from_row(previous) if previous else None}
        change_id = await self._start_change(
            p, org_id, conn, host.id, host.npm_id, host.primary_domain, "unpublish", False
        )
        result: dict[str, Any] = {}
        try:
            client = await self._client(conn)
            async with client:
                raw = await client.proxy_host(host.npm_id)
                result["before"] = host_snapshot(raw)
                await client.delete_proxy_host(host.npm_id)
            result["status"] = NpmChangeStatus.applied.value
            result["message"] = f"Proxy host {host.primary_domain} is verwijderd uit NPM."
            await self._cleanup_authentik(ctx, result)
        except NPMError as exc:
            result["status"] = NpmChangeStatus.refused.value
            result["message"] = f"NPM: {exc.message} Er is niets verwijderd."
        except Exception:
            log.exception("Depubliceren %s liep onverwacht mis", change_id)
            result["status"] = NpmChangeStatus.interrupted.value
            result["message"] = "Onverwachte fout tijdens het depubliceren. Controleer NPM en Authentik."
            raise
        finally:
            await self._finish_unpublish(change_id, conn.id, host.id, result, p)
        return await self._get_change(change_id)

    async def _finish_unpublish(
        self, change_id: UUID, conn_id: UUID, host_id: UUID, result: dict[str, Any], p: Principal
    ) -> None:
        change = await self.changes.get(change_id)
        conn = await self.npm.connections.get(conn_id)
        host = await self.hosts.get(host_id)
        if change is None or conn is None:
            return
        change.status = result.get("status", NpmChangeStatus.interrupted.value)
        change.message = result.get("message")
        change.before = result.get("before") or {}
        change.authentik = result.get("authentik")
        change.finished_at = datetime.now(UTC)
        removed_app = None
        if change.status == NpmChangeStatus.applied.value:
            ak = result.get("authentik") or {}
            row = await self.protections.for_host(change.connection_id, change.npm_id)
            if row is not None and ak.get("cleanup_error"):
                row.cleanup_error = ak["cleanup_error"]
                for k, v in (ak.get("remove") or {}).items():
                    if k in ("outpost_assigned", "provider_created", "application_created"):
                        setattr(row, k, v)
            elif row is not None:
                await self.protections.delete(row)
            if host is not None:
                host.removed_at = datetime.now(UTC)
                host.vaultx_published = False
                await self.db.refresh(host, ["application"])
                app = host.application
                if app is not None:
                    host.application = None
                    await self.db.flush()
                    await self.db.refresh(app, ["hosts", "login"])
                    live = [h for h in app.hosts if h.removed_at is None]
                    if app.source == AppSource.npm.value and not live and app.login is None:
                        removed_app = app.name
                        await self.npm.apps.delete(app)
        await self.audit.record(
            "npm_host.unpublish",
            p.actor,
            outcome="success" if change.status == NpmChangeStatus.applied.value else "failure",
            organization_id=change.organization_id,
            target_type="npm_host",
            target_id=host_id,
            details={
                "domain": change.domain,
                "npm_id": change.npm_id,
                "connection": conn.name,
                "status": change.status,
                "message": change.message,
                "deleted_application": removed_app,
                "change_id": str(change.id),
                **({"authentik": _audit_authentik(result["authentik"])} if result.get("authentik") else {}),
            },
        )
        await self.db.commit()
