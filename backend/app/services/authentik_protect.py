"""De Authentik-kant van "Beschermen met Authentik" (fase 4).

Voor VaultX de config in NPM zet (npm_write.py), zorgt het dat Authentik het
domein kent: een proxy provider in forward-auth-modus (single application) met
de externe URL van de host, een applicatie, optioneel groepsbindingen voor de
toegang, en de provider op de gekozen outpost. Bestaat er al een provider voor
dat domein (ook een domain-level provider op die outpost), dan gebruikt VaultX
die en maakt het niets nieuws aan.

Elke stap wordt meteen in een `AkState` bijgehouden, zodat een mislukte stap,
of een mislukte controle achteraf in NPM, precies kan terugdraaien wat VaultX
aanmaakte. Opruimen bij "Bescherming weghalen" gebruikt dezelfde gegevens,
bewaard in authentik_protections.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

from app.services.authentik_client import AuthentikClient, AuthentikError
from app.services.npm_protect import Check

ACCESS_RE = re.compile(r"^(all|organization|team:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)$")
PROVIDER_PREFIX = "VaultX: "
SLUG_PREFIX = "vaultx-"
# Authentik: slug max. 50 tekens, providernaam uniek.
MAX_SLUG = 50
NAME_ATTEMPTS = 5


def access_label(access: str, org_slug: str) -> str:
    if access == "all":
        return "alle Authentik-gebruikers"
    if access == "organization":
        return f"leden van organisatie {org_slug}"
    return f"leden van team {access.split(':', 1)[1]}"


def app_slug(domain: str, attempt: int = 0) -> str:
    base = SLUG_PREFIX + re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")
    suffix = f"-{attempt + 1}" if attempt else ""
    return base[: MAX_SLUG - len(suffix)].rstrip("-") + suffix


def provider_name(domain: str, attempt: int = 0) -> str:
    return PROVIDER_PREFIX + domain + (f" ({attempt + 1})" if attempt else "")


def _host_of(url: str | None) -> str:
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def access_groups(
    groups: list[dict[str, Any]], prefix: str, org_slug: str, access: str
) -> list[dict[str, Any]]:
    """Authentik-groepen die volgens de groepconventie (group_mapping.py) bij de toegang horen."""
    if access == "organization":
        scope = f"{prefix}{org_slug}"
        ok = lambda n: n == scope or n.startswith(f"{scope}:") or n.startswith(f"{scope}/")  # noqa: E731
    else:
        scope = f"{prefix}{org_slug}/{access.split(':', 1)[1]}"
        ok = lambda n: n == scope or n.startswith(f"{scope}:")  # noqa: E731
    return sorted(
        (g for g in groups if isinstance(g.get("name"), str) and ok(g["name"])), key=lambda g: g["name"]
    )


@dataclass(slots=True)
class AkPlan:
    """Wat VaultX in Authentik zou doen voor één host."""

    domain: str | None
    external_host: str | None
    access: str
    checks: list[Check] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    outpost: dict[str, Any] | None = None
    # Bestaande provider die VaultX hergebruikt (pk, name, mode, assigned_application_slug).
    provider: dict[str, Any] | None = None
    domain_level: bool = False
    create_provider: bool = False
    create_application: bool = False
    assign_outpost: bool = False
    app_name: str = ""
    groups: list[dict[str, Any]] = field(default_factory=list)
    flows: dict[str, str] = field(default_factory=dict)
    # Al eerder door VaultX aangemaakt (uit authentik_protections), bij hergebruik.
    provider_owned: bool = False
    application_owned: bool = False
    outpost_owned: bool = False

    @property
    def blocked(self) -> bool:
        return any(c.level == "block" for c in self.checks)

    def block(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "block", message))

    def warn(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "warn", message))

    def info(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "info", message))

    def summary(self) -> dict[str, Any]:
        return {
            "external_host": self.external_host,
            "outpost": (self.outpost or {}).get("name"),
            "provider": (self.provider or {}).get("name"),
            "create_provider": self.create_provider,
            "create_application": self.create_application,
            "assign_outpost": self.assign_outpost,
            "access": self.access,
            "groups": [g["name"] for g in self.groups],
            "steps": self.steps,
        }


@dataclass(slots=True)
class AkState:
    """Wat er in Authentik staat door deze wijziging; groeit stap voor stap mee."""

    domain: str
    external_host: str
    access: str
    outpost_pk: str
    outpost_name: str | None = None
    outpost_assigned: bool = False
    provider_pk: int | None = None
    provider_name: str | None = None
    provider_created: bool = False
    application_slug: str | None = None
    application_name: str | None = None
    application_created: bool = False
    groups: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any) -> AkState:
        return cls(**{f: getattr(row, f) for f in cls.__slots__})  # type: ignore[attr-defined]

    def removal_steps(self) -> list[str]:
        steps = []
        if self.outpost_assigned and not self.provider_created:
            steps.append(
                f"Authentik: provider '{self.provider_name}' van outpost '{self.outpost_name}' halen."
            )
        if self.application_created:
            steps.append(
                f"Authentik: applicatie '{self.application_name}' ({self.application_slug}) verwijderen."
            )
        if self.provider_created:
            steps.append(f"Authentik: proxy provider '{self.provider_name}' verwijderen.")
        return steps

    def leftovers(self) -> str:
        parts = []
        if self.application_created:
            parts.append(f"applicatie {self.application_slug}")
        if self.provider_created:
            parts.append(f"proxy provider '{self.provider_name}'")
        elif self.outpost_assigned:
            parts.append(f"provider '{self.provider_name}' op outpost '{self.outpost_name}'")
        return ", ".join(parts)


class AuthentikProtector:
    def __init__(self, client: AuthentikClient, *, authorization_flow: str, invalidation_flow: str) -> None:
        self.client = client
        self.authorization_flow = authorization_flow
        self.invalidation_flow = invalidation_flow

    # ------------------------------------------------------------ plan

    async def plan(
        self,
        *,
        domain: str | None,
        https: bool,
        other_domains: list[str],
        app_name: str,
        outpost_pk: str,
        access: str,
        org_slug: str,
        team_slugs: set[str],
        group_prefix: str,
        previous: AkState | None = None,
    ) -> AkPlan:
        """Leest Authentik en beslist wat er moet gebeuren. Wijzigt niets."""
        external = f"{'https' if https else 'http'}://{domain}" if domain else None
        plan = AkPlan(domain=domain, external_host=external, access=access, app_name=app_name)
        if not ACCESS_RE.fullmatch(access):
            plan.block("ak_access_invalid", "Ongeldige keuze voor de toegang.")
            return plan
        if not domain or external is None:
            plan.block(
                "ak_no_domain",
                "De host heeft enkel wildcard-domeinen; VaultX kan daar geen Authentik-provider voor maken.",
            )
            return plan
        try:
            outpost = await self.client.outpost(outpost_pk)
        except AuthentikError as exc:
            if exc.status == 404:
                plan.block(
                    "ak_outpost_missing",
                    "De outpost die op de koppeling staat, bestaat niet meer in Authentik. Kies een andere.",
                )
                return plan
            raise
        plan.outpost = {
            "pk": str(outpost["pk"]),
            "name": outpost.get("name"),
            "providers": outpost.get("providers") or [],
        }
        if outpost.get("type") != "proxy":
            plan.block("ak_outpost_type", f"Outpost '{outpost.get('name')}' is geen proxy-outpost.")
            return plan
        if not (outpost.get("config") or {}).get("authentik_host"):
            plan.warn(
                "ak_outpost_host",
                f"Bij outpost '{outpost.get('name')}' is 'authentik_host' niet ingesteld. Dan weet de "
                "outpost niet naar welke Authentik-URL hij een browser voor de aanmelding moet "
                "sturen; vul in "
                "Authentik bij de outpost de publieke URL van Authentik in.",
            )
        on_outpost = set(plan.outpost["providers"])

        providers = await self.client.proxy_providers()
        exact = [p for p in providers if _host_of(p.get("external_host")) == domain.lower()]
        domain_level = [
            p
            for p in providers
            if p.get("mode") == "forward_domain"
            and p.get("pk") in on_outpost
            and (cd := str(p.get("cookie_domain") or "").lstrip(".").lower())
            and (domain.lower() == cd or domain.lower().endswith(f".{cd}"))
        ]
        if exact:
            found = sorted(exact, key=lambda p: (p.get("pk") not in on_outpost, p.get("pk")))[0]
            if found.get("mode") not in {"forward_single", "forward_domain"}:
                plan.block(
                    "ak_provider_proxy_mode",
                    f"Provider '{found.get('name')}' voor {domain} staat in proxy-modus, niet in forward "
                    "auth. VaultX past hem niet aan; zet hem in Authentik op "
                    "'Forward auth (single application)'.",
                )
                return plan
            plan.provider = found
            if previous and previous.provider_pk == found.get("pk"):
                plan.provider_owned = previous.provider_created
            if _host_of(found.get("external_host")) and not str(found.get("external_host")).startswith(
                external.split("://")[0] + "://"
            ):
                plan.warn(
                    "ak_scheme_mismatch",
                    f"Provider '{found.get('name')}' heeft externe URL {found.get('external_host')}, de host "
                    f"is {external}. Na de aanmelding stuurt Authentik dan naar het andere schema.",
                )
            plan.info(
                "ak_provider_reused", f"Bestaande Authentik-provider '{found.get('name')}' wordt gebruikt."
            )
        elif domain_level:
            found = domain_level[0]
            plan.provider = found
            plan.domain_level = True
            plan.info(
                "ak_domain_level",
                f"Domain-level provider '{found.get('name')}' op outpost '{outpost.get('name')}' dekt "
                f"{domain} al. VaultX maakt in Authentik niets aan.",
            )
            return plan
        else:
            plan.create_provider = True

        if plan.provider is not None:
            slug = plan.provider.get("assigned_application_slug")
            if slug:
                plan.info(
                    "ak_access_existing",
                    f"Authentik-applicatie "
                    f"'{plan.provider.get('assigned_application_name') or slug}' bestaat "
                    "al; VaultX laat de toegang daar zoals ze is.",
                )
                if previous and previous.application_slug == slug:
                    plan.application_owned = previous.application_created
            else:
                plan.create_application = True
            if plan.provider.get("pk") not in on_outpost:
                plan.assign_outpost = True
            elif previous and previous.provider_pk == plan.provider.get("pk"):
                plan.outpost_owned = previous.outpost_assigned
        else:
            plan.create_application = True
            plan.assign_outpost = True
            for key, slug in (
                ("authorization", self.authorization_flow),
                ("invalidation", self.invalidation_flow),
            ):
                pk = await self.client.flow_pk(slug)
                if pk is None:
                    plan.block(
                        "ak_flow_missing",
                        f"Flow '{slug}' bestaat niet in Authentik. Stel VAULTX_AUTHENTIK_"
                        f"{key.upper()}_FLOW in op een bestaande flow.",
                    )
                plan.flows[key] = pk or ""

        if plan.create_application:
            if access == "all":
                plan.warn(
                    "ak_access_all",
                    "Iedereen met een Authentik-account komt na de aanmelding binnen. Kies een organisatie "
                    "of team om de toegang te beperken.",
                )
            else:
                if access.startswith("team:") and access.split(":", 1)[1] not in team_slugs:
                    plan.block("ak_team_unknown", "Dat team bestaat niet in deze organisatie.")
                    return plan
                found_groups = await self.client.groups(f"{group_prefix}{org_slug}")
                plan.groups = access_groups(found_groups, group_prefix, org_slug, access)
                if not plan.groups:
                    expected = (
                        f"{group_prefix}{org_slug}"
                        if access == "organization"
                        else f"{group_prefix}{org_slug}/{access.split(':', 1)[1]}"
                    )
                    plan.block(
                        "ak_no_group",
                        f"Geen Authentik-groep gevonden voor {access_label(access, org_slug)} (verwacht: "
                        f"'{expected}'). Maak die groep aan in Authentik, of kies "
                        "'alle Authentik-gebruikers'.",
                    )

        if other_domains:
            plan.warn(
                "ak_extra_domains",
                f"De host heeft ook {', '.join(other_domains)}. De provider die VaultX maakt, dekt enkel "
                f"{domain}; op de andere domeinen geeft de outpost een fout.",
            )
        if plan.blocked:
            return plan

        if plan.create_provider:
            names = {p.get("name") for p in providers}
            attempt = 0
            while provider_name(domain, attempt) in names and attempt < NAME_ATTEMPTS:
                attempt += 1
            plan.provider = {"name": provider_name(domain, attempt)}
            plan.steps.append(
                f"Authentik: proxy provider '{plan.provider['name']}' aanmaken (forward auth, single "
                f"application, externe URL {external})."
            )
        if plan.create_application:
            plan.steps.append(f"Authentik: applicatie '{app_name}' ({app_slug(domain)}) aanmaken.")
            if plan.groups:
                plan.steps.append(
                    "Authentik: toegang enkel voor " + ", ".join(f"'{g['name']}'" for g in plan.groups) + "."
                )
            else:
                plan.steps.append("Authentik: toegang voor alle Authentik-gebruikers (geen groepsbinding).")
        if plan.assign_outpost:
            plan.steps.append(f"Authentik: provider toewijzen aan outpost '{outpost.get('name')}'.")
        return plan

    # ------------------------------------------------------------ uitvoeren

    async def apply(self, plan: AkPlan, state: AkState) -> None:
        """Voert het plan uit en vult state na elke gelukte stap. Faalt een stap: AuthentikError,
        en state zegt wat er al stond (zie rollback)."""
        assert plan.outpost is not None and plan.provider is not None and plan.domain and plan.external_host
        state.outpost_name = plan.outpost.get("name")
        if plan.domain_level:
            state.provider_pk = plan.provider.get("pk")
            state.provider_name = plan.provider.get("name")
            return
        if plan.create_provider:
            provider = await self._create_provider(plan)
            state.provider_created = True
        else:
            provider = plan.provider
            state.provider_created = plan.provider_owned
        state.provider_pk = int(provider["pk"])
        state.provider_name = provider.get("name")

        if plan.create_application:
            app = await self._create_application(plan, state.provider_pk)
            state.application_created = True
            state.application_slug = app["slug"]
            state.application_name = app.get("name")
            for order, group in enumerate(plan.groups):
                await self.client.create_binding(str(app["pk"]), str(group["pk"]), order)
                state.groups.append(group["name"])
        else:
            state.application_slug = provider.get("assigned_application_slug")
            state.application_name = provider.get("assigned_application_name")
            state.application_created = plan.application_owned

        if plan.assign_outpost:
            # Opnieuw lezen vlak voor het schrijven: de lijst kan intussen gewijzigd zijn.
            current = await self.client.outpost(state.outpost_pk)
            providers = list(current.get("providers") or [])
            if state.provider_pk not in providers:
                await self.client.set_outpost_providers(state.outpost_pk, [*providers, state.provider_pk])
            state.outpost_assigned = True
        else:
            state.outpost_assigned = plan.outpost_owned

    async def _create_provider(self, plan: AkPlan) -> dict[str, Any]:
        assert plan.domain and plan.provider is not None
        name = plan.provider["name"]
        for attempt in range(NAME_ATTEMPTS):
            body = {
                "name": name,
                "mode": "forward_single",
                "external_host": plan.external_host,
                "authorization_flow": plan.flows["authorization"],
                "invalidation_flow": plan.flows["invalidation"],
            }
            try:
                return await self.client.create_proxy_provider(body)
            except AuthentikError as exc:
                if exc.status == 400 and exc.field_has("name", "exists") and attempt < NAME_ATTEMPTS - 1:
                    name = provider_name(plan.domain, attempt + 1)
                    continue
                raise
        raise AuthentikError("Geen vrije providernaam gevonden")  # pragma: no cover

    async def _create_application(self, plan: AkPlan, provider_pk: int) -> dict[str, Any]:
        assert plan.domain
        for attempt in range(NAME_ATTEMPTS):
            body = {
                "name": plan.app_name,
                "slug": app_slug(plan.domain, attempt),
                "provider": provider_pk,
                "meta_launch_url": plan.external_host,
                "meta_description": "Aangemaakt door VaultX",
                "policy_engine_mode": "any",
            }
            try:
                return await self.client.create_application(body)
            except AuthentikError as exc:
                if exc.status == 400 and exc.field_has("slug", "exists") and attempt < NAME_ATTEMPTS - 1:
                    continue
                raise
        raise AuthentikError("Geen vrije applicatie-slug gevonden")  # pragma: no cover

    # ------------------------------------------------------------ terugdraaien / opruimen

    async def undo(self, state: AkState) -> list[str]:
        """Haalt weg wat VaultX aanmaakte of toewees. Geeft de fouten terug (leeg = gelukt).

        Volgorde: eerst van de outpost, dan de applicatie (bindingen gaan mee), dan de provider
        (een verwijderde provider verdwijnt ook van de outpost). Wat al weg is, telt als gelukt.
        """
        errors: list[str] = []
        if state.outpost_assigned and not state.provider_created and state.provider_pk is not None:
            try:
                current = await self.client.outpost(state.outpost_pk)
                providers = list(current.get("providers") or [])
                if state.provider_pk in providers:
                    providers.remove(state.provider_pk)
                    await self.client.set_outpost_providers(state.outpost_pk, providers)
                state.outpost_assigned = False
            except AuthentikError as exc:
                if exc.status != 404:
                    errors.append(exc.message)
        if state.application_created and state.application_slug:
            try:
                await self.client.delete_application(state.application_slug)
                state.application_created = False
            except AuthentikError as exc:
                if exc.status != 404:
                    errors.append(exc.message)
                else:
                    state.application_created = False
        if state.provider_created and state.provider_pk is not None:
            try:
                await self.client.delete_proxy_provider(state.provider_pk)
                state.provider_created = False
                state.outpost_assigned = False
            except AuthentikError as exc:
                if exc.status != 404:
                    errors.append(exc.message)
                else:
                    state.provider_created = False
        return errors
