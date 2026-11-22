"""NPM volledig beheren vanuit VaultX: lezen, voorbeeld, uitvoeren met controle en rollback.

Dezelfde aanpak als fase 3 (npm_write.py), maar voor elk soort NPM-object:
proxy hosts, redirection hosts, 404-hosts, streams, access lists en
certificaten. Elke wijziging gaat zo:

1. Alles live uit NPM lezen (inventaris) en controleren dat het object niet
   veranderde sinds het voorbeeld (modified_on).
2. Plan maken (npm_manage_plan.py); weigeren bij elk blokkerend punt, zoals een
   Advanced-config waar het CSS-thema of Authentik uit zou verdwijnen.
3. Bij hosts: de host vooraf aanspreken zoals een bezoeker zonder sessie.
4. Schrijven via de NPM-API en meteen `meta.nginx_online` nakijken.
5. Opnieuw aanspreken en beoordelen.
6. Faalt 4 of 5: terugzetten (vorige config, of het nieuwe object weer weg).

VaultX schrijft enkel via de NPM-API en staat nooit in het aanvraagpad. Alles
komt in het journaal (npm_changes, met object_type) en in de auditlog, zonder
wachtwoorden, sleutels of DNS-gegevens.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, InvalidOperationError, NotFoundError, UpstreamError
from app.models import DiscoveredHost, NpmChange, NpmChangeStatus, NpmConnection
from app.repositories import AuthentikProtectionRepository, DiscoveredHostRepository, NpmChangeRepository
from app.services.npm import ClientFactory, NpmService
from app.services.npm_client import NPMClient, NPMError
from app.services.npm_manage_plan import (
    HOST_KINDS,
    KINDS,
    Inventory,
    ManagePlan,
    label_of,
    make_plan,
    managed_blocks,
    redact,
)
from app.services.npm_probe import Prober, ProbeResult, ProbeTarget, make_prober
from app.services.npm_write import STALE_AFTER, probe_target
from app.services.principal import Actor, Principal

log = logging.getLogger(__name__)

ROLLBACK_ATTEMPTS = 3
PROBE_ATTEMPTS = 4
PROBE_DELAY_SECONDS = 1.0
# NPM herlaadt nginx asynchroon: vlak na een wijziging antwoordt soms nog de oude config. Daarom
# eerst even wachten, en een geslaagde controle van een proxy host nog eens bevestigen.
PROBE_SETTLE_SECONDS = 2.0
# Let's Encrypt via NPM kan even duren (zeker met een DNS-challenge en propagatietijd).
CERTIFICATE_WAIT_SECONDS = 300.0
MAX_NGINX_ERROR = 2000


@dataclass(slots=True)
class PreviewView:
    plan: ManagePlan
    probe: ProbeTarget | None
    certificate_check: dict[str, Any] | None = None


@dataclass(slots=True)
class _Outcome:
    status: str = NpmChangeStatus.interrupted.value
    message: str = ""
    npm_id: int | None = None
    label: str | None = None
    probe_before: dict[str, Any] | None = None
    probe_after: dict[str, Any] | None = None
    nginx_error: str | None = None
    warnings: list[str] = field(default_factory=list)


class _Abort(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _clean(kind: str, obj: dict[str, Any]) -> dict[str, Any]:
    """Wat VaultX van een NPM-object toont: zonder eigenaar, sleutels of wachtwoord-hints."""
    out = {k: v for k, v in obj.items() if k not in ("owner", "meta", "certificate", "access_list")}
    meta = obj.get("meta") or {}
    if kind not in ("certificate", "access_list") and isinstance(meta, dict):
        out["nginx_online"] = meta.get("nginx_online")
        out["nginx_err"] = (str(meta.get("nginx_err"))[:MAX_NGINX_ERROR]) if meta.get("nginx_err") else None
    if kind == "access_list":
        keep = ("id", "created_on", "modified_on", "name", "satisfy_any", "pass_auth", "proxy_host_count")
        out = {k: obj.get(k) for k in keep if k in obj}
        out["items"] = [
            {"username": i.get("username")} for i in obj.get("items") or [] if isinstance(i, dict)
        ]
        out["clients"] = [
            {"address": c.get("address"), "directive": c.get("directive")}
            for c in obj.get("clients") or []
            if isinstance(c, dict)
        ]
    return out


class NpmManageService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        client_factory: ClientFactory | None = None,
        prober: Prober | None = None,
        *,
        settle_seconds: float | None = None,
    ) -> None:
        self.db = db
        self.settle = PROBE_SETTLE_SECONDS if settle_seconds is None else settle_seconds
        self.settings = settings
        self.npm = NpmService(db, settings, client_factory)
        self.changes = NpmChangeRepository(db)
        self.hosts = DiscoveredHostRepository(db)
        self.protections = AuthentikProtectionRepository(db)
        self.prober = prober or make_prober(settings.npm_http_timeout_seconds)
        self.audit = self.npm.audit

    # ------------------------------------------------------------ helpers

    async def _conn(self, p: Principal, org_id: UUID, conn_id: UUID, action: str) -> NpmConnection:
        conn = await self.npm.get_connection(p, org_id, conn_id)
        # Advanced-configs kunnen geheimen bevatten: enkel beheerders van de organisatie.
        if not p.can_manage_org(org_id):
            raise await self.audit.deny(
                f"npm_manage.{action}",
                p.actor,
                organization_id=org_id,
                target_type="npm_connection",
                target_id=conn_id,
            )
        return conn

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

    async def _vaultx_sets(self, conn_id: UUID) -> tuple[set[int], set[int]]:
        published = set(
            await self.db.scalars(
                select(DiscoveredHost.npm_id).where(
                    DiscoveredHost.connection_id == conn_id,
                    DiscoveredHost.vaultx_published.is_(True),
                    DiscoveredHost.removed_at.is_(None),
                )
            )
        )
        protected = {row.npm_id for row in await self.protections.for_connection(conn_id)}
        return published, protected

    @staticmethod
    async def _inventory(client: NPMClient, published: set[int], protected: set[int]) -> Inventory:
        inv = Inventory(published=published, protected=protected)
        for kind, info in KINDS.items():
            if kind == "certificate":
                inv.objects[kind] = await client.certificates()
            else:
                inv.objects[kind] = await client.list_objects(info.path, expand=info.expand)
        return inv

    # ------------------------------------------------------------ lezen

    async def overview(self, p: Principal, org_id: UUID, conn_id: UUID) -> dict[str, Any]:
        conn = await self._conn(p, org_id, conn_id, "read")
        published, protected = await self._vaultx_sets(conn.id)
        await self.db.commit()  # geen open transactie tijdens het netwerkverkeer
        try:
            client = await self._client(conn)
            async with client:
                inv = await self._inventory(client, published, protected)
        except NPMError as exc:
            raise UpstreamError(exc.message) from exc
        out: dict[str, Any] = {}
        for kind in KINDS:
            rows = []
            for obj in inv.of(kind):
                row = _clean(kind, obj)
                row["label"] = label_of(kind, obj)
                if kind in HOST_KINDS:
                    blocks = set()
                    for cfg in [obj.get("advanced_config") or ""] + [
                        (loc or {}).get("advanced_config") or "" for loc in obj.get("locations") or []
                    ]:
                        blocks |= {
                            b.split("\n", 1)[0].split("vaultx:", 1)[1].split()[0] for b in managed_blocks(cfg)
                        }
                    row["managed_blocks"] = sorted(blocks)
                if kind == "proxy_host":
                    row["vaultx_published"] = obj["id"] in published
                    row["vaultx_protected"] = obj["id"] in protected
                if kind == "certificate":
                    row["used_by"] = inv.certificate_users(int(obj["id"]))
                if kind == "access_list":
                    row["used_by"] = [
                        label_of("proxy_host", h) for h in inv.access_list_users(int(obj["id"]))
                    ]
                rows.append(row)
            out[kind] = sorted(rows, key=lambda r: str(r.get("label") or "").lower())
        return out

    async def dns_providers(self, p: Principal, org_id: UUID, conn_id: UUID) -> list[dict[str, Any]]:
        conn = await self._conn(p, org_id, conn_id, "read")
        await self.db.commit()
        try:
            client = await self._client(conn)
            async with client:
                return await client.dns_providers()
        except NPMError as exc:
            raise UpstreamError(exc.message) from exc

    async def list_changes(
        self, p: Principal, org_id: UUID, conn_id: UUID, kind: str | None = None
    ) -> list[NpmChange]:
        conn = await self._conn(p, org_id, conn_id, "journal")
        return await self.changes.for_connection(conn.id, object_type=kind, limit=100)

    # ------------------------------------------------------------ voorbeeld

    async def preview(
        self,
        p: Principal,
        org_id: UUID,
        conn_id: UUID,
        kind: str,
        action: str,
        *,
        npm_id: int | None,
        data: dict[str, Any],
        confirm_removals: bool = False,
    ) -> PreviewView:
        conn = await self._conn(p, org_id, conn_id, "preview")
        published, protected = await self._vaultx_sets(conn.id)
        await self.db.commit()
        cert_check: dict[str, Any] | None = None
        try:
            client = await self._client(conn)
            async with client:
                inv = await self._inventory(client, published, protected)
                plan = make_plan(
                    kind, action, inv, npm_id=npm_id, data=data, confirm_removals=confirm_removals
                )
                if kind == "certificate" and not plan.blocked and _needs_pem(action, data):
                    cert_check = await self._validate_pem(client, plan, data)
        except NPMError as exc:
            raise UpstreamError(exc.message) from exc
        self._connection_checks(plan, conn)
        target = self._target(conn, plan)
        if plan.expect and target is None and plan.kind in HOST_KINDS and not plan.blocked:
            plan.warn(
                "no_probe",
                "De host heeft enkel wildcard-domeinen; VaultX kan het resultaat niet zelf controleren.",
            )
        return PreviewView(plan=plan, probe=target, certificate_check=cert_check)

    @staticmethod
    def _connection_checks(plan: ManagePlan, conn: NpmConnection) -> None:
        if not conn.write_enabled:
            plan.block(
                "write_disabled",
                "Schrijven naar deze NPM staat uit. Zet 'VaultX mag wijzigen' aan op de koppeling; het "
                "NPM-account heeft dan 'Manage' nodig op hosts, streams, access lists en certificaten.",
            )

    @staticmethod
    async def _validate_pem(
        client: NPMClient, plan: ManagePlan, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        try:
            result = await client.validate_certificate(data["certificate"], data["certificate_key"])
        except NPMError as exc:
            plan.block("certificate_invalid", f"NPM aanvaardt dit certificaat niet: {exc.message}")
            return None
        info = result.get("certificate") if isinstance(result.get("certificate"), dict) else {}
        dates = info.get("dates") or {}
        check = {
            "cn": info.get("cn"),
            "issuer": info.get("issuer"),
            "valid_from": _ts(dates.get("from")),
            "valid_to": _ts(dates.get("to")),
        }
        if result.get("certificate_key") is not True:
            plan.block("key_mismatch", "De private key hoort niet bij dit certificaat.")
        to = dates.get("to")
        if isinstance(to, int | float) and to < datetime.now(UTC).timestamp():
            plan.block("certificate_expired", "Dit certificaat is al vervallen.")
        plan.info("certificate_read", f"Certificaat voor {check['cn']}, geldig tot {check['valid_to']}.")
        return check

    def _target(self, conn: NpmConnection, plan: ManagePlan) -> ProbeTarget | None:
        if not plan.expect:
            return None
        return probe_target(conn, plan.after or plan.before)

    # ------------------------------------------------------------ uitvoeren

    async def apply(
        self,
        p: Principal,
        org_id: UUID,
        conn_id: UUID,
        kind: str,
        action: str,
        *,
        npm_id: int | None,
        data: dict[str, Any],
        expected_modified_on: str | None,
        confirm_removals: bool = False,
        verify: bool = True,
    ) -> NpmChange:
        conn = await self._conn(p, org_id, conn_id, action)
        if not conn.write_enabled:
            raise InvalidOperationError("Schrijven naar deze NPM staat uit op de koppeling")
        actor = p.actor
        published, protected = await self._vaultx_sets(conn.id)
        journal_id = npm_id if action != "create" else 0
        host_row = (
            await self.hosts.by_npm_id(conn.id, npm_id)
            if kind == "proxy_host" and npm_id is not None
            else None
        )
        now = datetime.now(UTC)
        for stale in await self.changes.stale_running(conn.id, journal_id or 0, now - STALE_AFTER, kind):
            stale.status = NpmChangeStatus.interrupted.value
            stale.finished_at = now
            stale.message = "Onderbroken (herstartte VaultX tijdens de wijziging?). Controleer NPM."
        change = self.changes.add(
            NpmChange(
                organization_id=org_id,
                connection_id=conn.id,
                host_id=host_row.id if host_row else None,
                object_type=kind,
                npm_id=journal_id or 0,
                domain=KINDS[kind].label,
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
            raise ConflictError(
                "Er loopt al een wijziging op dit object"
                if action != "create"
                else "Er wordt al iets aangemaakt"
            ) from exc
        change_id = change.id

        outcome = _Outcome()
        plan: ManagePlan | None = None
        try:
            client = await self._client(conn)
            async with client:
                inv = await self._inventory(client, published, protected)
                plan = make_plan(
                    kind, action, inv, npm_id=npm_id, data=data, confirm_removals=confirm_removals
                )
                outcome.label = plan.label
                if expected_modified_on and plan.modified_on != expected_modified_on:
                    raise _Abort("Het object werd in NPM gewijzigd sinds het voorbeeld. Bekijk het opnieuw.")
                if plan.blocked:
                    raise _Abort(" ".join(c.message for c in plan.checks if c.level == "block"))
                await self._run(client, conn, plan, data, verify, outcome)
        except _Abort as exc:
            outcome.status = NpmChangeStatus.refused.value
            outcome.message = exc.message
        except NPMError as exc:
            # Fout voor er iets geschreven werd (aanmelden, lezen) of een weigering van NPM.
            outcome.status = NpmChangeStatus.refused.value
            outcome.message = f"NPM: {exc.message}"
        except Exception:
            log.exception("NPM-beheer: wijziging %s liep onverwacht mis", change_id)
            outcome.status = NpmChangeStatus.interrupted.value
            outcome.message = "Onverwachte fout tijdens de wijziging. Controleer het object in NPM."
            raise
        finally:
            await self._finish(change_id, conn, plan, outcome, actor)
        if kind == "proxy_host" and outcome.status == NpmChangeStatus.applied.value:
            await self._resync(conn.id, actor)
        return await self._get_change(change_id)

    async def _run(
        self,
        client: NPMClient,
        conn: NpmConnection,
        plan: ManagePlan,
        data: dict[str, Any],
        verify: bool,
        out: _Outcome,
    ) -> None:
        kind, action = plan.kind, plan.action
        path = KINDS[kind].path
        out.npm_id = plan.npm_id
        if kind == "certificate":
            await self._run_certificate(client, plan, data, out)
            return
        target = self._target(conn, plan) if verify else None
        before_probe: ProbeResult | None = None
        if target is not None and action in ("update", "enable"):
            before_probe = await self.prober(target)
            out.probe_before = before_probe.as_dict()

        if action == "delete":
            assert plan.npm_id is not None
            await client.delete_object(path, plan.npm_id)
            out.status = NpmChangeStatus.applied.value
            out.message = f"{KINDS[kind].label.capitalize()} {plan.label} is verwijderd uit NPM."
            return

        if action == "disable":
            assert plan.npm_id is not None
            await client.set_enabled(path, plan.npm_id, False)
            out.status = NpmChangeStatus.applied.value
            out.message = f"{KINDS[kind].label.capitalize()} {plan.label} staat uit."
            return

        # Vanaf hier verandert NPM. Elke fout leidt tot terugzetten.
        if action == "create":
            created = await client.create_object(path, plan.body)
            out.npm_id = int(created["id"])

            async def undo() -> str:
                await self._retry(lambda: client.delete_object(path, int(out.npm_id or 0)))
                return "Het nieuwe object is weer verwijderd; NPM is zoals ervoor."

        elif action == "update":
            assert plan.npm_id is not None
            try:
                await client.update_object(path, plan.npm_id, plan.body)
            except NPMError as exc:
                raise _Abort(f"NPM weigerde de wijziging: {exc.message}") from exc

            async def undo() -> str:
                restored = await self._retry(
                    lambda: client.update_object(path, int(plan.npm_id or 0), plan.before)
                )
                if (restored.get("meta") or {}).get("nginx_online") is False:
                    raise NPMError("nginx weigert ook de vorige config")
                return "De vorige config is teruggezet."

        else:  # enable
            assert plan.npm_id is not None
            await client.set_enabled(path, plan.npm_id, True)

            async def undo() -> str:
                await self._retry(lambda: client.set_enabled(path, int(plan.npm_id or 0), False))
                return "Het object staat weer uit."

        assert out.npm_id is not None
        reason = await self._check_after(client, conn, plan, out, verify, before_probe)
        if reason is None:
            out.status = NpmChangeStatus.applied.value
            done = {"create": "aangemaakt", "update": "bijgewerkt", "enable": "aangezet"}[action]
            out.message = f"{KINDS[kind].label.capitalize()} {plan.label} is {done}."
            if not verify and plan.expect:
                out.message += " Niet gecontroleerd: VaultX sprak het resultaat achteraf niet aan."
            if out.warnings:
                out.message += " " + " ".join(out.warnings)
            return
        try:
            undone = await undo()
        except NPMError as exc:
            out.status = NpmChangeStatus.rollback_failed.value
            out.message = (
                f"{reason} Terugzetten is NIET gelukt ({exc.message}). Kijk het object na in NPM; 'Voor' in "
                "het journaal toont de vorige config."
            )
            return
        out.status = NpmChangeStatus.rolled_back.value
        out.message = f"{reason} {undone}"

    async def _check_after(
        self,
        client: NPMClient,
        conn: NpmConnection,
        plan: ManagePlan,
        out: _Outcome,
        verify: bool,
        before_probe: ProbeResult | None,
    ) -> str | None:
        """None als alles in orde is, anders de reden om terug te zetten."""
        kind = plan.kind
        path = KINDS[kind].path
        current = await client.get_object(path, int(out.npm_id or 0), expand=KINDS[kind].expand)
        meta = current.get("meta") or {}
        if kind != "access_list" and meta.get("nginx_online") is False:
            out.nginx_error = str(meta.get("nginx_err") or "")[:MAX_NGINX_ERROR]
            return "nginx in NPM weigerde de nieuwe config."
        if kind == "access_list":
            for host in await client.list_objects("proxy-hosts"):
                if (
                    host.get("access_list_id") == out.npm_id
                    and (host.get("meta") or {}).get("nginx_online") is False
                ):
                    out.nginx_error = str((host.get("meta") or {}).get("nginx_err") or "")[:MAX_NGINX_ERROR]
                    return f"Host {label_of('proxy_host', host)} ging offline door de nieuwe access list."
        if not verify or not plan.expect:
            return None
        target = probe_target(conn, current)
        if target is None:
            return None
        if self.settle:
            await asyncio.sleep(self.settle)
        after = await self.prober(target)
        for _ in range(PROBE_ATTEMPTS - 1):
            if _judge(plan, before_probe, after, current)[0] is None:
                break
            await asyncio.sleep(PROBE_DELAY_SECONDS)
            after = await self.prober(target)
        if kind == "proxy_host" and self.settle and _judge(plan, before_probe, after, current)[0] is None:
            # "Geen 5xx" klopt ook voor de oude config: nog eens kijken na het herladen.
            await asyncio.sleep(self.settle)
            after = await self.prober(target)
        out.probe_after = after.as_dict()
        reason, warning = _judge(plan, before_probe, after, current)
        if warning:
            out.warnings.append(warning)
        return reason

    async def _run_certificate(
        self, client: NPMClient, plan: ManagePlan, data: dict[str, Any], out: _Outcome
    ) -> None:
        action = plan.action
        if action == "create" and plan.body.get("provider") == "letsencrypt":
            created = await client.create_object("certificates", plan.body, wait=CERTIFICATE_WAIT_SECONDS)
            out.npm_id = int(created["id"])
            out.status = NpmChangeStatus.applied.value
            out.message = f"Let's Encrypt-certificaat voor {plan.label} is aangevraagd en staat in NPM."
            if created.get("expires_on"):
                out.message += f" Geldig tot {created['expires_on']}."
        elif action == "create":
            created = await client.create_object("certificates", plan.body)
            out.npm_id = int(created["id"])
            try:
                await client.upload_certificate(
                    out.npm_id,
                    data["certificate"],
                    data["certificate_key"],
                    data.get("intermediate_certificate"),
                )
            except NPMError as exc:
                try:
                    await self._retry(lambda: client.delete_object("certificates", int(out.npm_id or 0)))
                except NPMError as exc2:
                    out.status = NpmChangeStatus.rollback_failed.value
                    out.message = (
                        f"Opladen mislukte ({exc.message}) en het lege certificaat #{out.npm_id} kon niet "
                        f"verwijderd worden ({exc2.message}). Verwijder het in NPM."
                    )
                    return
                out.status = NpmChangeStatus.rolled_back.value
                out.message = f"Opladen mislukte ({exc.message}); het lege certificaat is weer verwijderd."
                return
            out.status = NpmChangeStatus.applied.value
            out.message = f"Eigen certificaat '{plan.label}' staat in NPM."
        elif action == "upload":
            assert plan.npm_id is not None
            await client.upload_certificate(
                plan.npm_id,
                data["certificate"],
                data["certificate_key"],
                data.get("intermediate_certificate"),
            )
            out.status = NpmChangeStatus.applied.value
            out.message = f"Nieuw certificaatbestand opgeladen voor '{plan.label}'."
        elif action == "renew":
            assert plan.npm_id is not None
            renewed = await client.renew_certificate(plan.npm_id, wait=CERTIFICATE_WAIT_SECONDS)
            out.status = NpmChangeStatus.applied.value
            out.message = f"Certificaat {plan.label} is vernieuwd."
            if renewed.get("expires_on"):
                out.message += f" Geldig tot {renewed['expires_on']}."
        elif action == "delete":
            assert plan.npm_id is not None
            await client.delete_object("certificates", plan.npm_id)
            out.status = NpmChangeStatus.applied.value
            out.message = f"Certificaat {plan.label} is verwijderd uit NPM."

    @staticmethod
    async def _retry(fn):  # type: ignore[no-untyped-def]
        last: NPMError | None = None
        for attempt in range(ROLLBACK_ATTEMPTS):
            try:
                return await fn()
            except NPMError as exc:
                last = exc
                await asyncio.sleep(0.5 * (attempt + 1))
        assert last is not None
        raise last

    async def _finish(
        self, change_id: UUID, conn: NpmConnection, plan: ManagePlan | None, out: _Outcome, actor: Actor
    ) -> None:
        await self.db.rollback()
        change = await self.changes.get(change_id)
        if change is None:
            return
        kind = change.object_type
        change.status = out.status
        change.message = out.message
        if out.npm_id:
            change.npm_id = out.npm_id
        if out.label or plan is not None:
            change.domain = (out.label or (plan.label if plan else change.domain))[:255]
        if plan is not None:
            change.before = redact(kind, plan.before)
            change.after = {"object": redact(kind, plan.after), "changes": plan.changes, "steps": plan.steps}
        change.probe_before = out.probe_before
        change.probe_after = out.probe_after
        change.nginx_error = out.nginx_error
        change.finished_at = datetime.now(UTC)
        if kind == "proxy_host" and change.host_id is None and change.npm_id:
            row = await self.hosts.by_npm_id(conn.id, change.npm_id)
            change.host_id = row.id if row else None
        outcome = "success" if change.status == NpmChangeStatus.applied.value else "failure"
        await self.audit.record(
            f"npm_manage.{kind}.{change.action}",
            actor,
            outcome=outcome,
            organization_id=change.organization_id,
            target_type=f"npm_{kind}",
            target_id=change.host_id,
            details={
                "object": change.domain,
                "npm_id": change.npm_id,
                "connection": conn.name,
                "status": change.status,
                "verified": change.verified,
                "message": change.message,
                "changed_fields": [c["field"] for c in (plan.changes if plan else [])],
                "change_id": str(change.id),
            },
        )
        await self.db.commit()

    async def _resync(self, conn_id: UUID, actor: Actor) -> None:
        """Catalogus en hostlijst meteen bijwerken; een fout hier maakt de wijziging niet ongedaan."""
        try:
            await self.npm.sync(conn_id, actor)
        except Exception:  # noqa: BLE001 - de wijziging zelf is gelukt
            log.warning("Sync na NPM-beheer mislukte", exc_info=True)
            await self.db.rollback()

    async def _get_change(self, change_id: UUID) -> NpmChange:
        change = await self.changes.get(change_id)
        if change is None:
            raise NotFoundError("Wijziging niet gevonden")
        await self.db.refresh(change)
        return change


def _needs_pem(action: str, data: dict[str, Any]) -> bool:
    return action == "upload" or (action == "create" and data.get("provider") == "other")


def _ts(value: Any) -> str | None:
    if not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d")


def _judge(
    plan: ManagePlan, before: ProbeResult | None, after: ProbeResult, current: dict[str, Any]
) -> tuple[str | None, str | None]:
    """(reden om terug te zetten, waarschuwing) voor wat een bezoeker na de wijziging ziet."""
    kind = plan.kind
    if after.error:
        return f"Na de wijziging is de host niet bereikbaar: {after.error}.", None
    if kind == "proxy_host":
        if after.server_error and not (before and before.server_error):
            if plan.action == "create":
                return None, (
                    f"Let op: de host geeft {after.status}; nginx bereikt de app achter de host (nog) niet. "
                    "Controleer doelhost en poort."
                )
            earlier = before.summary() if before else "?"
            return f"De host geeft {after.status} na de wijziging (ervoor: {earlier}).", None
        return None, None
    if kind == "redirection_host":
        code = int(current.get("forward_http_code") or 301)
        target = str(current.get("forward_domain_name") or "")
        if after.status == code and target in (after.location or ""):
            return None, None
        return f"Verwacht: {code} naar {target}. Gekregen: {after.summary()}.", None
    if kind == "dead_host":
        if after.status == 404:
            return None, None
        return f"Verwacht: 404. Gekregen: {after.summary()}.", None
    return None, None
