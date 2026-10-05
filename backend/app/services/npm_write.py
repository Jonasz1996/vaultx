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

Fase 4: staat er een Authentik-outpost op de koppeling, dan maakt VaultX tussen
stap 3 en 4 ook de Authentik-kant aan (authentik_protect.py): provider,
applicatie, groepsbindingen en de toewijzing aan de outpost. Faalt daarna iets,
dan draait VaultX zowel NPM als Authentik terug. Bij weghalen ruimt VaultX na
een gelukte wijziging in NPM op wat het in Authentik aanmaakte.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
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
from app.models import AuthentikProtection, DiscoveredHost, NpmChange, NpmChangeStatus, NpmConnection
from app.repositories import (
    ApplicationRepository,
    AuthentikProtectionRepository,
    DiscoveredHostRepository,
    NpmChangeRepository,
    OrganizationRepository,
    TeamRepository,
)
from app.services.authentik_client import AuthentikClient, AuthentikError
from app.services.authentik_protect import AkPlan, AkState, AuthentikProtector, access_label
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
# Een outpost laadt een nieuwe provider pas na een paar seconden; daarna langer proberen.
PROBE_ATTEMPTS_AFTER_AUTHENTIK = 20
PROBE_DELAY_AFTER_AUTHENTIK = 1.5
MAX_NGINX_ERROR = 2000


AuthentikFactory = Callable[[], AuthentikClient]


@dataclass(slots=True)
class PlanView:
    plan: Plan
    domain: str
    probe: ProbeTarget | None
    # Fase 4: wat VaultX in Authentik doet (None = niets, Authentik staat niet op de koppeling).
    authentik: dict[str, Any] | None = None
    authentik_steps: list[str] = field(default_factory=list)

    @property
    def can_apply(self) -> bool:
        return self.plan.has_changes


def _domains(raw: dict[str, Any]) -> list[str]:
    return [d for d in raw.get("domain_names") or [] if isinstance(d, str) and "*" not in d]


def probe_target(conn: NpmConnection, raw: dict[str, Any]) -> ProbeTarget | None:
    domain = next(iter(_domains(raw)), None)
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
        authentik_factory: AuthentikFactory | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.npm = NpmService(db, settings, client_factory)
        self.hosts = DiscoveredHostRepository(db)
        self.changes = NpmChangeRepository(db)
        self.prober = prober or make_prober(settings.npm_http_timeout_seconds)
        self.audit = self.npm.audit
        self.protections = AuthentikProtectionRepository(db)
        self.authentik_factory = authentik_factory or self._default_authentik_client

    def _default_authentik_client(self) -> AuthentikClient:
        token = self.settings.authentik_api_token
        return AuthentikClient(
            self.settings.authentik_base_url,
            token.get_secret_value() if token else "",
            verify_tls=self.settings.authentik_verify_tls,
            timeout=self.settings.authentik_http_timeout_seconds,
        )

    def _protector(self, client: AuthentikClient) -> AuthentikProtector:
        return AuthentikProtector(
            client,
            authorization_flow=self.settings.authentik_authorization_flow,
            invalidation_flow=self.settings.authentik_invalidation_flow,
        )

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

    async def _ak_context(self, org_id: UUID, host: DiscoveredHost) -> dict[str, Any]:
        """Gegevens uit de database die het Authentik-plan nodig heeft (voor het netwerkverkeer)."""
        org = await OrganizationRepository(self.db).get(org_id)
        teams = await TeamRepository(self.db).list_for_org(org_id)
        app = await ApplicationRepository(self.db).get(host.application_id) if host.application_id else None
        previous = await self.protections.for_host(host.connection_id, host.npm_id)
        return {
            "org_slug": org.slug if org else "",
            "team_slugs": {t.slug for t in teams},
            "app_name": (app.name if app else None) or host.primary_domain,
            "previous": AkState.from_row(previous) if previous else None,
        }

    async def _ak_plan(
        self,
        client: AuthentikClient,
        conn: NpmConnection,
        raw: dict[str, Any],
        access: str,
        ctx: dict[str, Any],
    ) -> AkPlan:
        domains = _domains(raw)
        assert conn.authentik_outpost_pk
        return await self._protector(client).plan(
            domain=domains[0] if domains else None,
            https=bool(raw.get("certificate_id")),
            other_domains=domains[1:],
            app_name=ctx["app_name"],
            outpost_pk=conn.authentik_outpost_pk,
            access=access,
            org_slug=ctx["org_slug"],
            team_slugs=ctx["team_slugs"],
            group_prefix=self.settings.oidc_group_prefix,
            previous=ctx["previous"],
        )

    async def preview(
        self,
        p: Principal,
        org_id: UUID,
        conn_id: UUID,
        host_id: UUID,
        action: Action,
        access: str = "organization",
    ) -> PlanView:
        conn, host = await self._load(p, org_id, conn_id, host_id, action)
        ctx = await self._ak_context(org_id, host)
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
        view = PlanView(plan=plan, domain=host.primary_domain, probe=target)
        if action == "protect":
            await self._preview_authentik(view, conn, raw, access, ctx)
        else:
            previous: AkState | None = ctx["previous"]
            if previous is not None:
                view.authentik_steps = previous.removal_steps()
                view.authentik = {"remove": previous.as_dict()}
                if view.authentik_steps and not self.settings.authentik_api_enabled:
                    plan.warn(
                        "authentik_api_missing",
                        "De Authentik-API is niet ingesteld (VAULTX_AUTHENTIK_API_TOKEN); VaultX kan de "
                        f"Authentik-objecten niet opruimen: {previous.leftovers()}.",
                    )
        plan.steps = view.authentik_steps + plan.steps
        return view

    async def _preview_authentik(
        self, view: PlanView, conn: NpmConnection, raw: dict[str, Any], access: str, ctx: dict[str, Any]
    ) -> None:
        plan = view.plan
        if not conn.authentik_outpost_pk:
            if plan.blocked:
                return
            plan.info(
                "authentik_manual",
                "VaultX maakt in Authentik niets aan: zorg zelf voor een proxy provider in forward-auth-"
                "modus op je outpost, of kies op de koppeling een outpost om VaultX dat te laten doen.",
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
        access: str = "organization",
    ) -> NpmChange:
        conn, host = await self._load(p, org_id, conn_id, host_id, action)
        actor = p.actor
        ctx = await self._ak_context(org_id, host)
        use_authentik = action == "protect" and bool(conn.authentik_outpost_pk)
        if use_authentik and not self.settings.authentik_api_enabled:
            raise InvalidOperationError("De Authentik-API is niet ingesteld op de VaultX-server")
        ctx["use_authentik"] = use_authentik
        ctx["access"] = access
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
                    client, conn, host.npm_id, action, expected_modified_on, verify, result, ctx
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
        ctx: dict[str, Any],
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

        ak_client: AuthentikClient | None = None
        ak_state: AkState | None = None
        if ctx.get("use_authentik"):
            ak_client = self.authentik_factory()
        try:
            if ak_client is not None:
                ak_state = await self._apply_authentik(ak_client, conn, raw, ctx, result)
                result["steps"] = result["authentik"]["plan"]["steps"] + plan.steps

            async def rollback(reason: str) -> dict[str, Any]:
                restored = await self._rollback(client, npm_id, plan, reason, result, before_probe, target)
                if ak_client is not None and ak_state is not None:
                    await self._undo_authentik(ak_client, ak_state, result)
                return restored

            ak_plan = (result.get("authentik") or {}).get("plan") or {}
            changed_authentik = any(
                ak_plan.get(k) for k in ("create_provider", "create_application", "assign_outpost")
            )
            current = await self._write(
                client,
                npm_id,
                action,
                plan,
                result,
                before_probe,
                target,
                verify,
                rollback,
                slow_probe=changed_authentik,
            )
            if result.get("status") == NpmChangeStatus.applied.value and action == "unprotect":
                await self._cleanup_authentik(ctx, result)
            return current
        finally:
            if ak_client is not None:
                await ak_client.aclose()

    async def _apply_authentik(
        self,
        ak_client: AuthentikClient,
        conn: NpmConnection,
        raw: dict[str, Any],
        ctx: dict[str, Any],
        result: dict[str, Any],
    ) -> AkState:
        """Authentik-kant aanmaken; bij een fout terugdraaien en de wijziging stoppen (_Abort)."""
        try:
            ak_plan = await self._ak_plan(ak_client, conn, raw, ctx["access"], ctx)
        except AuthentikError as exc:
            raise _Abort(f"Authentik: {exc.message}") from exc
        result["authentik"] = {"plan": ak_plan.summary()}
        if ak_plan.blocked:
            raise _Abort(" ".join(c.message for c in ak_plan.checks if c.level == "block"))
        assert ak_plan.domain and ak_plan.external_host and conn.authentik_outpost_pk
        state = AkState(
            domain=ak_plan.domain,
            external_host=ak_plan.external_host,
            access=ctx["access"],
            outpost_pk=conn.authentik_outpost_pk,
        )
        try:
            await self._protector(ak_client).apply(ak_plan, state)
        except AuthentikError as exc:
            result["authentik"]["state"] = state.as_dict()
            await self._undo_authentik(ak_client, state, result)
            undo_error = result["authentik"].get("undo_error")
            if undo_error:
                result["status"] = NpmChangeStatus.rollback_failed.value
                raise _Abort(
                    f"Authentik: {exc.message} Terugdraaien in Authentik lukte niet ({undo_error}); "
                    f"verwijder met de hand: {state.leftovers()}. NPM is niet gewijzigd."
                ) from exc
            raise _Abort(
                f"Authentik: {exc.message} Wat VaultX al aanmaakte, is weer weg; NPM is niet gewijzigd."
            ) from exc
        result["authentik"]["state"] = state.as_dict()
        return state

    async def _undo_authentik(
        self, ak_client: AuthentikClient, state: AkState, result: dict[str, Any]
    ) -> None:
        leftovers = state.leftovers()
        errors = await self._protector(ak_client).undo(state)
        ak = result.setdefault("authentik", {})
        ak["state"] = state.as_dict()
        if errors:
            ak["undo_error"] = "; ".join(errors)[:MAX_NGINX_ERROR]
            if result.get("status") == NpmChangeStatus.rolled_back.value:
                result["status"] = NpmChangeStatus.rollback_failed.value
                result["message"] += (
                    f" In Authentik lukte het terugdraaien niet ({ak['undo_error']}); verwijder met de hand: "
                    f"{leftovers}."
                )
        elif leftovers:
            ak["undone"] = True
            if result.get("status") == NpmChangeStatus.rolled_back.value:
                result["message"] += f" Ook in Authentik is weer weg wat VaultX aanmaakte ({leftovers})."

    async def _cleanup_authentik(self, ctx: dict[str, Any], result: dict[str, Any]) -> None:
        """Na weghalen in NPM: opruimen wat VaultX in Authentik aanmaakte."""
        previous: AkState | None = ctx["previous"]
        if previous is None:
            return
        ak = result.setdefault("authentik", {})
        ak["remove"] = previous.as_dict()
        leftovers = previous.leftovers()
        if not leftovers:
            ak["removed"] = True
            return
        if not self.settings.authentik_api_enabled:
            ak["cleanup_error"] = "Authentik-API niet ingesteld"
        else:
            try:
                async with self.authentik_factory() as ak_client:
                    errors = await self._protector(ak_client).undo(previous)
            except AuthentikError as exc:
                errors = [exc.message]
            if errors:
                ak["cleanup_error"] = "; ".join(errors)[:MAX_NGINX_ERROR]
        if ak.get("cleanup_error"):
            result["message"] += (
                f" Opruimen in Authentik lukte niet ({ak['cleanup_error']}); verwijder met de hand: "
                f"{previous.leftovers()}."
            )
        else:
            ak["removed"] = True
            result["message"] += f" In Authentik verwijderd: {leftovers}."

    async def _write(
        self,
        client: NPMClient,
        npm_id: int,
        action: Action,
        plan: Plan,
        result: dict[str, Any],
        before_probe: ProbeResult | None,
        target: ProbeTarget | None,
        verify: bool,
        rollback: Callable[[str], Any],
        *,
        slow_probe: bool,
    ) -> dict[str, Any]:
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
            after_probe = await self._probe_until_ok(action, before_probe, target, slow=slow_probe)
            result["probe_after"] = after_probe.as_dict()
            reason = judge(action, before_probe, after_probe, provider_by_vaultx=slow_probe)
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
        self, action: Action, before: ProbeResult | None, target: ProbeTarget, *, slow: bool = False
    ) -> ProbeResult:
        attempts = PROBE_ATTEMPTS_AFTER_AUTHENTIK if slow else PROBE_ATTEMPTS
        delay = PROBE_DELAY_AFTER_AUTHENTIK if slow else PROBE_DELAY_SECONDS
        result = await self.prober(target)
        for _ in range(attempts - 1):
            if judge(action, before, result, provider_by_vaultx=slow) is None:
                break
            await asyncio.sleep(delay)
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
        change.authentik = result.get("authentik")
        change.finished_at = datetime.now(UTC)
        await self._store_protection(change, result)
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
                **({"authentik": _audit_authentik(result["authentik"])} if result.get("authentik") else {}),
            },
        )
        await self.db.commit()

    async def _store_protection(self, change: NpmChange, result: dict[str, Any]) -> None:
        """authentik_protections bijwerken na een gelukte (of half gelukte) wijziging."""
        ak = result.get("authentik") or {}
        row = await self.protections.for_host(change.connection_id, change.npm_id)
        if change.action == "protect" and change.status == NpmChangeStatus.applied.value and ak.get("state"):
            values = {k: v for k, v in ak["state"].items() if k in AkState.__slots__}  # type: ignore[attr-defined]
            if row is None:
                row = self.protections.add(
                    AuthentikProtection(
                        organization_id=change.organization_id,
                        connection_id=change.connection_id,
                        npm_id=change.npm_id,
                        **values,
                    )
                )
            else:
                for k, v in values.items():
                    setattr(row, k, v)
                row.cleanup_error = None
        elif change.action == "unprotect" and change.status == NpmChangeStatus.applied.value and row:
            if ak.get("cleanup_error"):
                row.cleanup_error = ak["cleanup_error"]
                for k, v in (ak.get("remove") or {}).items():
                    if k in ("outpost_assigned", "provider_created", "application_created"):
                        setattr(row, k, v)
            else:
                await self.protections.delete(row)
        elif (
            change.action == "protect"
            and row
            and ak.get("state")
            and change.status != NpmChangeStatus.applied.value
        ):
            # Teruggedraaid: wat er nog van VaultX in Authentik staat, volgt de toestand na het terugdraaien.
            for k in ("outpost_assigned", "provider_created", "application_created"):
                setattr(row, k, bool(getattr(row, k)) and bool(ak["state"].get(k)))

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


def _audit_authentik(ak: dict[str, Any]) -> dict[str, Any]:
    state = ak.get("state") or ak.get("remove") or {}
    return {
        "provider": state.get("provider_name"),
        "application": state.get("application_slug"),
        "outpost": state.get("outpost_name"),
        "groups": state.get("groups"),
        "provider_created": state.get("provider_created"),
        "application_created": state.get("application_created"),
        "undone": ak.get("undone", False),
        "removed": ak.get("removed", False),
        "error": ak.get("undo_error") or ak.get("cleanup_error"),
    }
