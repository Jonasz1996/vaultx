"""Automatische login voor een app uit de catalogus (fase 5).

Het hoofdscenario uit de opdracht: een gebruiker die al bij Authentik is
aangemeld, opent https://grafana.example.be en zit meteen in Grafana, zonder
tweede login. VaultX richt daarvoor methode 4 uit 02b in (de app wordt zelf een
OpenID Connect-client van Authentik):

1. Voorbeeld: VaultX leest Authentik (flows, scope mappings, certificaat,
   groepen) en, als de beheerder een Grafana-beheerder opgeeft, Grafana (welke
   URL Grafana van zichzelf denkt te hebben, en of er al een OAuth-login staat).
   Er verandert niets.
2. Toepassen: in Authentik een OAuth2/OpenID-provider met de redirect URI van
   de app, een applicatie en groepsbindingen voor de toegang. Bij Grafana met
   beheerdersaccount zet VaultX de generic OAuth-login ook meteen in Grafana
   (SSO settings API, werkt zonder herstart). Faalt een stap, dan draait VaultX
   terug wat het al aanmaakte.
3. Controle: VaultX opent /login van de app zoals een bezoeker zonder sessie.
   Grafana hoort dan door te sturen naar Authentik met de juiste client ID en
   redirect URI.

Het client secret staat versleuteld in app_logins; beheerders kunnen de
app-config (met secret) opnieuw opvragen, en dat staat in de auditlog. De
gegevens van de Grafana-beheerder bewaart VaultX niet.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, InvalidOperationError, NotFoundError, UpstreamError
from app.core.security import open_secret, seal_secret
from app.models import Application, AppLogin, DiscoveredHost
from app.repositories import (
    ApplicationRepository,
    AppLoginRepository,
    OrganizationRepository,
    TeamRepository,
)
from app.services.audit import AuditService
from app.services.authentik_client import AuthentikClient, AuthentikError
from app.services.authentik_protect import ACCESS_RE, NAME_ATTEMPTS, access_groups, access_label
from app.services.grafana_client import GrafanaClient, GrafanaError
from app.services.login_templates import (
    GRAFANA_ROLES,
    SCOPE_MANAGED,
    TEMPLATES,
    AuthentikUrls,
    LoginTemplate,
    grafana_admin_groups,
    grafana_api_settings,
    grafana_env,
    grafana_ini,
    grafana_role_path,
    grafana_values,
    normalize_app_url,
    oidc_summary,
    redirect_uris,
    templates_for,
)
from app.services.npm_probe import REDIRECTS, Prober, ProbeTarget, make_prober
from app.services.npm_protect import Check
from app.services.principal import Principal

log = logging.getLogger(__name__)

PROVIDER_PREFIX = "VaultX login: "
SLUG_PREFIX = "vaultx-login-"
MAX_SLUG = 50
# Grafana past nieuwe SSO-instellingen meteen toe; geef het toch even de tijd.
CHECK_ATTEMPTS = 3
CHECK_DELAY_SECONDS = 1.0

AuthentikFactory = Callable[[], AuthentikClient]
GrafanaFactory = Callable[[str, str, str], GrafanaClient]


def login_slug(domain: str, attempt: int = 0) -> str:
    base = SLUG_PREFIX + re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")
    suffix = f"-{attempt + 1}" if attempt else ""
    return base[: MAX_SLUG - len(suffix)].rstrip("-") + suffix


def login_provider_name(domain: str, attempt: int = 0) -> str:
    return PROVIDER_PREFIX + domain + (f" ({attempt + 1})" if attempt else "")


@dataclass(slots=True)
class GrafanaAdmin:
    """Beheerder van Grafana voor één actie; wordt niet bewaard."""

    url: str
    username: str
    password: str


@dataclass(slots=True)
class LoginRequest:
    template: str
    access: str = "organization"
    app_url: str | None = None
    redirect_uris: list[str] = field(default_factory=list)
    default_role: str = "Viewer"
    grafana: GrafanaAdmin | None = None


@dataclass(slots=True)
class LoginPlan:
    template: str
    app_url: str | None
    redirect_uris: list[str]
    access: str
    checks: list[Check] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    provider_name: str = ""
    app_name: str = ""
    groups: list[dict[str, Any]] = field(default_factory=list)
    flows: dict[str, str] = field(default_factory=dict)
    mappings: list[str] = field(default_factory=list)
    signing_key: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    role_path: str | None = None
    configure_app: bool = False
    grafana_version: str | None = None

    @property
    def blocked(self) -> bool:
        return any(c.level == "block" for c in self.checks)

    def block(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "block", message))

    def warn(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "warn", message))

    def info(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "info", message))


@dataclass(slots=True)
class CheckResult:
    status: str  # ok | failed | unknown
    message: str
    url: str | None = None


def _query(location: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items() if v}


class AppLoginService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        authentik_factory: AuthentikFactory | None = None,
        grafana_factory: GrafanaFactory | None = None,
        prober: Prober | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.apps = ApplicationRepository(db)
        self.logins = AppLoginRepository(db)
        self.orgs = OrganizationRepository(db)
        self.audit = AuditService(db)
        self.authentik_factory = authentik_factory or self._default_authentik
        self.grafana_factory = grafana_factory or self._default_grafana
        self.prober = prober or make_prober(settings.npm_http_timeout_seconds)

    def _default_authentik(self) -> AuthentikClient:
        token = self.settings.authentik_api_token
        return AuthentikClient(
            self.settings.authentik_base_url,
            token.get_secret_value() if token else "",
            verify_tls=self.settings.authentik_verify_tls,
            timeout=self.settings.authentik_http_timeout_seconds,
        )

    def _default_grafana(self, url: str, username: str, password: str) -> GrafanaClient:
        # Grafana staat meestal in het eigen netwerk met een eigen certificaat; VaultX stuurt enkel
        # het beheerderswachtwoord mee naar de URL die de beheerder zelf opgaf.
        return GrafanaClient(url, username, password, verify_tls=False)

    def _secret_context(self, login_id: UUID) -> str:
        return f"app-login:{login_id}"

    # ------------------------------------------------------------ laden en rechten

    async def _app(self, p: Principal, org_id: UUID, app_id: UUID) -> Application:
        if not p.can_view_org(org_id) or await self.orgs.get(org_id) is None:
            raise NotFoundError("Organisatie niet gevonden")
        app = await self.apps.in_org(org_id, app_id)
        if app is None:
            raise NotFoundError("Applicatie niet gevonden")
        return app

    async def _manage(self, p: Principal, org_id: UUID, app_id: UUID, action: str) -> Application:
        app = await self._app(p, org_id, app_id)
        if not p.can_manage_org(org_id):
            raise await self.audit.deny(
                f"app_login.{action}",
                p.actor,
                organization_id=org_id,
                target_type="application",
                target_id=app_id,
            )
        return app

    async def get(self, p: Principal, org_id: UUID, app_id: UUID) -> dict[str, Any]:
        app = await self._app(p, org_id, app_id)
        login = await self.logins.for_application(app.id)
        host = _live_host(app)
        return {
            "configured": self.settings.authentik_api_enabled,
            "authentik_url": self.settings.authentik_public_base,
            "templates": [_template_out(t) for t in templates_for(app.app_type)],
            "suggested_app_url": normalize_app_url(app.url),
            "suggested_grafana_url": (
                f"{host.forward_scheme}://{host.forward_host}:{host.forward_port}" if host else None
            ),
            "login": login,
        }

    # ------------------------------------------------------------ voorbeeld

    async def _context(self, app: Application) -> dict[str, Any]:
        org = await self.orgs.get(app.organization_id)
        teams = await TeamRepository(self.db).list_for_org(app.organization_id)
        return {"org_slug": org.slug if org else "", "team_slugs": {t.slug for t in teams}}

    async def _plan(
        self, client: AuthentikClient, app: Application, req: LoginRequest, ctx: dict[str, Any]
    ) -> LoginPlan:
        template: LoginTemplate | None = TEMPLATES.get(req.template)
        app_url = normalize_app_url(req.app_url or app.url)
        plan = LoginPlan(template=req.template, app_url=app_url, redirect_uris=[], access=req.access)
        plan.app_name = app.name
        if template is None:
            plan.block("login_template", "Onbekend sjabloon.")
            return plan
        if not ACCESS_RE.fullmatch(req.access):
            plan.block("ak_access_invalid", "Ongeldige keuze voor de toegang.")
            return plan
        if app_url is None:
            plan.block(
                "login_app_url",
                "De app heeft geen geldige URL (http(s)://host[/pad]). Vul de URL in waarop gebruikers de "
                "app openen.",
            )
            return plan
        if template.app_types and app.app_type not in template.app_types:
            plan.warn(
                "login_app_type",
                f"Deze app staat in de catalogus niet als {template.label} "
                f"({app.app_type or 'type onbekend'}).",
            )
        plan.redirect_uris = redirect_uris(template, app_url, req.redirect_uris)
        for uri in plan.redirect_uris:
            if normalize_app_url(uri) is None:
                plan.block("login_redirect_invalid", f"Ongeldige redirect URI: {uri}")
        if not plan.redirect_uris:
            plan.block("login_redirect_missing", "Geef minstens één redirect URI van de app op.")
        if app_url.startswith("http://"):
            plan.warn(
                "login_http",
                "De app draait over http. De aanmelding werkt, maar codes en tokens gaan dan "
                "onversleuteld over het netwerk; zet HTTPS aan in NPM.",
            )
        if req.template == "grafana":
            if req.default_role not in GRAFANA_ROLES:
                plan.block("login_role", "Kies Viewer, Editor of Admin als standaardrol.")
                return plan
            admins = grafana_admin_groups(
                self.settings.oidc_group_prefix, ctx["org_slug"], self.settings.oidc_admin_groups
            )
            plan.options = {"default_role": req.default_role, "admin_groups": admins}
            plan.role_path = grafana_role_path(admins, req.default_role)
            plan.info(
                "login_roles",
                f"Rollen in Grafana: Admin voor {', '.join(admins)}; {req.default_role} voor de anderen.",
            )

        # Authentik: flows, scope mappings, certificaat, groepen.
        for key, slug in (
            ("authorization", self.settings.authentik_authorization_flow),
            ("invalidation", self.settings.authentik_invalidation_flow),
        ):
            pk = await client.flow_pk(slug)
            if pk is None:
                plan.block(
                    "ak_flow_missing",
                    f"Flow '{slug}' bestaat niet in Authentik. Stel VAULTX_AUTHENTIK_{key.upper()}_FLOW "
                    "in op een bestaande flow.",
                )
            plan.flows[key] = pk or ""
        managed = {m.get("managed"): str(m["pk"]) for m in await client.scope_mappings() if m.get("managed")}
        for scope, key in SCOPE_MANAGED.items():
            if key in managed:
                plan.mappings.append(managed[key])
            elif scope != "offline_access":
                plan.block(
                    "ak_scope_missing",
                    f"De standaard scope mapping voor '{scope}' ontbreekt in Authentik ({key}).",
                )
        keys = await client.certificate_keypairs()
        found = next((k for k in keys if k.get("name") == self.settings.authentik_signing_key), None)
        if found:
            plan.signing_key = str(found["pk"])
        else:
            plan.warn(
                "ak_signing_key",
                f"Certificaat '{self.settings.authentik_signing_key}' niet gevonden in Authentik. ID-tokens "
                "worden dan met het client secret getekend (HS256); stel VAULTX_AUTHENTIK_SIGNING_KEY in.",
            )

        if req.access == "all":
            plan.warn(
                "ak_access_all",
                "Iedereen met een Authentik-account kan zich bij deze app aanmelden. Kies een organisatie of "
                "team om dat te beperken.",
            )
        else:
            if req.access.startswith("team:") and req.access.split(":", 1)[1] not in ctx["team_slugs"]:
                plan.block("ak_team_unknown", "Dat team bestaat niet in deze organisatie.")
                return plan
            prefix = self.settings.oidc_group_prefix
            groups = await client.groups(f"{prefix}{ctx['org_slug']}")
            plan.groups = access_groups(groups, prefix, ctx["org_slug"], req.access)
            if not plan.groups:
                expected = (
                    f"{prefix}{ctx['org_slug']}"
                    if req.access == "organization"
                    else f"{prefix}{ctx['org_slug']}/{req.access.split(':', 1)[1]}"
                )
                plan.block(
                    "ak_no_group",
                    f"Geen Authentik-groep gevonden voor {access_label(req.access, ctx['org_slug'])} "
                    f"(verwacht: '{expected}'). Maak die groep aan in Authentik, of kies 'alle "
                    "Authentik-gebruikers'.",
                )

        domain = urlsplit(app_url).hostname or app.name
        names = {p.get("name") for p in await client.oauth2_providers(PROVIDER_PREFIX + domain)}
        attempt = 0
        while login_provider_name(domain, attempt) in names and attempt < NAME_ATTEMPTS:
            attempt += 1
        plan.provider_name = login_provider_name(domain, attempt)
        plan.steps.append(
            f"Authentik: OAuth2/OpenID-provider '{plan.provider_name}' aanmaken (redirect URI "
            f"{', '.join(plan.redirect_uris)}, scopes openid, email, profile, offline_access)."
        )
        plan.steps.append(f"Authentik: applicatie '{app.name}' ({login_slug(domain)}) aanmaken.")
        if plan.groups:
            plan.steps.append(
                "Authentik: aanmelden enkel voor " + ", ".join(f"'{g['name']}'" for g in plan.groups) + "."
            )
        else:
            plan.steps.append("Authentik: aanmelden voor alle Authentik-gebruikers (geen groepsbinding).")
        return plan

    async def _plan_grafana(self, plan: LoginPlan, admin: GrafanaAdmin) -> None:
        """Leest Grafana met het opgegeven beheerdersaccount; wijzigt niets."""
        url = normalize_app_url(admin.url)
        if url is None:
            plan.block("grafana_url", "Ongeldig adres voor de Grafana-API.")
            return
        try:
            async with self.grafana_factory(url, admin.username, admin.password) as g:
                front = await g.frontend_settings()
                current = await g.sso_settings()
        except GrafanaError as exc:
            plan.block("grafana_api", f"Grafana: {exc.message}")
            return
        plan.grafana_version = str((front.get("buildInfo") or {}).get("version") or "") or None
        root = normalize_app_url(str(front.get("appUrl") or ""))
        if root != plan.app_url:
            plan.block(
                "grafana_root_url",
                f"Grafana denkt dat het op {front.get('appUrl') or '(leeg)'} staat, niet op {plan.app_url}/. "
                "Dan stuurt Grafana Authentik een verkeerde redirect URI. Zet in Grafana "
                f"GF_SERVER_ROOT_URL={plan.app_url}/ (of root_url in grafana.ini), herstart Grafana en "
                "probeer opnieuw.",
            )
        settings = current.get("settings") or {}
        if settings.get("enabled"):
            plan.block(
                "grafana_oauth_exists",
                f"Grafana heeft al een generic OAuth-login (client '{settings.get('clientId')}', "
                f"{settings.get('authUrl') or 'geen URL'}). VaultX overschrijft die niet; zet ze eerst "
                "uit in Grafana (Administration > Authentication).",
            )
        if plan.blocked:
            return
        plan.configure_app = True
        label = f"Grafana {plan.grafana_version}" if plan.grafana_version else "Grafana"
        plan.steps.append(
            f"{label}: generic OAuth-login met Authentik aanzetten, met automatisch doorsturen naar "
            "Authentik (via de API, zonder herstart)."
        )
        plan.info(
            "grafana_break_glass",
            "Lokaal aanmelden in Grafana blijft mogelijk via /login?disableAutoLogin, bv. met het "
            "admin-account als Authentik onbeschikbaar is.",
        )

    async def preview(self, p: Principal, org_id: UUID, app_id: UUID, req: LoginRequest) -> LoginPlan:
        app = await self._manage(p, org_id, app_id, "configure")
        if not self.settings.authentik_api_enabled:
            raise InvalidOperationError("De Authentik-API is niet ingesteld op de VaultX-server")
        existing = await self.logins.for_application(app.id)
        ctx = await self._context(app)
        try:
            async with self.authentik_factory() as client:
                plan = await self._plan(client, app, req, ctx)
        except AuthentikError as exc:
            raise UpstreamError(f"Authentik: {exc.message}") from exc
        if existing is not None:
            plan.block(
                "login_exists", "Automatische login staat al ingericht. Haal ze eerst weg om ze te wijzigen."
            )
        if req.grafana is not None and req.template == "grafana" and not plan.blocked:
            await self._plan_grafana(plan, req.grafana)
        elif req.template == "grafana":
            plan.steps.append(
                "Grafana: de instellingen zelf invullen (grafana.ini of omgevingsvariabelen) en Grafana "
                "herstarten. Of geef een Grafana-beheerder op, dan zet VaultX ze meteen."
            )
        return plan

    # ------------------------------------------------------------ toepassen

    async def apply(self, p: Principal, org_id: UUID, app_id: UUID, req: LoginRequest) -> AppLogin:
        plan = await self.preview(p, org_id, app_id, req)
        if plan.blocked:
            raise InvalidOperationError(
                " ".join(c.message for c in plan.checks if c.level == "block") or "Niet mogelijk"
            )
        app = await self._app(p, org_id, app_id)
        assert plan.app_url is not None
        domain = urlsplit(plan.app_url).hostname or app.name
        state: dict[str, Any] = {
            "provider_pk": None,
            "provider_created": False,
            "slug": None,
            "app_created": False,
        }
        try:
            async with self.authentik_factory() as client:
                try:
                    provider = await self._create_provider(client, plan, domain)
                    state.update(provider_pk=int(provider["pk"]), provider_created=True)
                    state["provider_name"] = provider.get("name")
                    created = await self._create_application(client, plan, domain, state["provider_pk"])
                    state.update(slug=created["slug"], app_created=True, app_name=created.get("name"))
                    for order, group in enumerate(plan.groups):
                        await client.create_binding(str(created["pk"]), str(group["pk"]), order)
                    if plan.configure_app:
                        assert req.grafana is not None
                        await self._configure_grafana(req.grafana, plan, provider, state["slug"])
                except (AuthentikError, GrafanaError) as exc:
                    errors = await self._undo(client, state)
                    where = "Grafana" if isinstance(exc, GrafanaError) else "Authentik"
                    message = f"{where}: {exc.message}"
                    if errors:
                        message += " Terugdraaien in Authentik lukte niet volledig: " + "; ".join(errors)
                    else:
                        message += " VaultX draaide terug wat het in Authentik aanmaakte."
                    await self.audit.record(
                        "app_login.configure",
                        p.actor,
                        outcome="failure",
                        organization_id=org_id,
                        target_type="application",
                        target_id=app.id,
                        details={"template": req.template, "error": message[:500]},
                    )
                    await self.db.commit()
                    raise UpstreamError(message) from exc
        except AuthentikError as exc:  # factory of contextmanager
            raise UpstreamError(f"Authentik: {exc.message}") from exc

        now = datetime.now(UTC)
        login = AppLogin(
            organization_id=org_id,
            application_id=app.id,
            template=req.template,
            app_url=plan.app_url,
            redirect_uris=plan.redirect_uris,
            access=req.access,
            groups=[g["name"] for g in plan.groups],
            options=plan.options,
            provider_pk=state["provider_pk"],
            provider_name=state.get("provider_name") or plan.provider_name,
            provider_created=True,
            application_slug=state["slug"],
            application_name=state.get("app_name"),
            application_created=True,
            client_id=str(provider["client_id"]),
            client_secret_ciphertext=b"",
            app_configured=plan.configure_app,
            app_configured_at=now if plan.configure_app else None,
            created_by_user_id=p.user.id,
        )
        self.logins.add(login)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            # Iemand anders richtte tegelijk de login voor deze app in: wat VaultX net aanmaakte, weg.
            await self.db.rollback()
            async with self.authentik_factory() as client:
                await self._undo(client, state)
            raise ConflictError("Er werd net al een automatische login ingericht voor deze app") from exc
        login.client_secret_ciphertext = seal_secret(
            self.settings.secret_key.get_secret_value(),
            str(provider.get("client_secret") or ""),
            self._secret_context(login.id),
        )
        await self.audit.record(
            "app_login.configure",
            p.actor,
            organization_id=org_id,
            target_type="application",
            target_id=app.id,
            details={
                "template": req.template,
                "app_url": plan.app_url,
                "provider": login.provider_name,
                "application": login.application_slug,
                "client_id": login.client_id,
                "access": req.access,
                "groups": login.groups,
                "app_configured": plan.configure_app,
            },
        )
        await self.db.commit()
        if login.template == "grafana":
            await self._run_check(login, app)
            await self.db.commit()
        await self.db.refresh(login)
        return login

    async def _create_provider(self, client: AuthentikClient, plan: LoginPlan, domain: str) -> dict[str, Any]:
        """Maakt de provider aan, met een client secret dat VaultX zelf kiest.

        Authentik geeft het secret enkel terug aan wie de provider mag wijzigen; het serviceaccount
        heeft dat recht niet nodig als VaultX het secret zelf meegeeft. Het antwoord krijgt het
        secret er weer bij.
        """
        name = plan.provider_name
        secret = secrets.token_urlsafe(48)
        for attempt in range(NAME_ATTEMPTS):
            body: dict[str, Any] = {
                "name": name,
                "client_secret": secret,
                "authorization_flow": plan.flows["authorization"],
                "invalidation_flow": plan.flows["invalidation"],
                "client_type": "confidential",
                # Zonder expliciete grant types weigert Authentik 2026.x de authorize-stap.
                "grant_types": ["authorization_code", "refresh_token"],
                "redirect_uris": [{"matching_mode": "strict", "url": u} for u in plan.redirect_uris],
                "property_mappings": plan.mappings,
                "sub_mode": "hashed_user_id",
                "include_claims_in_id_token": True,
                "issuer_mode": "per_provider",
            }
            if plan.signing_key:
                body["signing_key"] = plan.signing_key
            try:
                return {**await client.create_oauth2_provider(body), "client_secret": secret}
            except AuthentikError as exc:
                if exc.status == 400 and exc.field_has("name", "exists") and attempt < NAME_ATTEMPTS - 1:
                    name = login_provider_name(domain, attempt + 1)
                    continue
                raise
        raise AuthentikError("Geen vrije providernaam gevonden")  # pragma: no cover

    async def _create_application(
        self, client: AuthentikClient, plan: LoginPlan, domain: str, provider_pk: int
    ) -> dict[str, Any]:
        for attempt in range(NAME_ATTEMPTS):
            body = {
                "name": plan.app_name,
                "slug": login_slug(domain, attempt),
                "provider": provider_pk,
                "meta_launch_url": plan.app_url,
                "meta_description": "Automatische login, aangemaakt door VaultX",
                "policy_engine_mode": "any",
            }
            try:
                return await client.create_application(body)
            except AuthentikError as exc:
                if exc.status == 400 and exc.field_has("slug", "exists") and attempt < NAME_ATTEMPTS - 1:
                    continue
                raise
        raise AuthentikError("Geen vrije applicatie-slug gevonden")  # pragma: no cover

    def _urls(self, slug: str) -> AuthentikUrls:
        return AuthentikUrls(self.settings.authentik_public_base, slug)

    def _grafana_values(self, login_or_plan: Any, client_id: str, secret: str, slug: str) -> dict[str, Any]:
        options = login_or_plan.options or {}
        role_path = grafana_role_path(
            options.get("admin_groups") or [], options.get("default_role") or "Viewer"
        )
        return grafana_values(self._urls(slug), client_id, secret, role_path)

    async def _configure_grafana(
        self, admin: GrafanaAdmin, plan: LoginPlan, provider: dict[str, Any], slug: str
    ) -> None:
        values = self._grafana_values(
            plan, str(provider["client_id"]), str(provider.get("client_secret")), slug
        )
        url = normalize_app_url(admin.url)
        assert url is not None
        async with self.grafana_factory(url, admin.username, admin.password) as g:
            await g.put_sso_settings(grafana_api_settings(values))
            back = (await g.sso_settings()).get("settings") or {}
        if not back.get("enabled") or back.get("clientId") != provider["client_id"]:
            raise GrafanaError(
                "Grafana nam de instellingen niet over (na het opslaan staat de login niet aan)."
            )

    async def _undo(self, client: AuthentikClient, state: dict[str, Any]) -> list[str]:
        """Haalt de applicatie (met bindingen) en de provider weg die VaultX aanmaakte."""
        errors: list[str] = []
        if state.get("app_created") and state.get("slug"):
            try:
                await client.delete_application(state["slug"])
                state["app_created"] = False
            except AuthentikError as exc:
                if exc.status == 404:
                    state["app_created"] = False
                else:
                    errors.append(exc.message)
        if state.get("provider_created") and state.get("provider_pk") is not None:
            try:
                await client.delete_oauth2_provider(state["provider_pk"])
                state["provider_created"] = False
            except AuthentikError as exc:
                if exc.status == 404:
                    state["provider_created"] = False
                else:
                    errors.append(exc.message)
        return errors

    # ------------------------------------------------------------ controleren

    def _targets(self, app: Application, url: str) -> ProbeTarget | None:
        parts = urlsplit(url)
        scheme, domain = parts.scheme, (parts.hostname or "")
        if not domain:
            return None
        host = _live_host(app, domain)
        if host is not None and host.connection is not None:
            conn = host.connection
            address = conn.probe_host or urlsplit(conn.base_url).hostname or domain
            port = conn.probe_https_port if scheme == "https" else conn.probe_http_port
        else:
            address = domain
            port = parts.port or (443 if scheme == "https" else 80)
        return ProbeTarget(scheme=scheme, address=address, port=port, domain=domain, path=parts.path or "/")

    async def _probe(self, base: ProbeTarget, path: str) -> Any:
        return await self.prober(
            ProbeTarget(
                scheme=base.scheme, address=base.address, port=base.port, domain=base.domain, path=path
            )
        )

    async def _grafana_check(self, login: AppLogin, app: Application) -> CheckResult:
        base = self._targets(app, login.app_url)
        if base is None:
            return CheckResult("failed", "De app-URL heeft geen host.")
        prefix = base.path.rstrip("/")
        first = await self._probe(base, f"{prefix}/login")
        url = first.url
        if first.error:
            return CheckResult("unknown", f"VaultX bereikt {login.app_url} niet ({first.error}).", url)
        location = first.location or ""
        if first.status in REDIRECTS and "/outpost.goauthentik.io/" in location:
            return CheckResult(
                "unknown",
                "De host staat achter Authentik forward auth, dus VaultX komt zonder sessie niet tot bij "
                "Grafana. Open de app in je browser om te testen.",
                url,
            )
        if first.status == 200:
            return CheckResult(
                "failed",
                "Grafana toont nog zijn eigen aanmeldformulier: de instellingen zijn nog niet actief. Zet "
                "ze in grafana.ini of als omgevingsvariabelen en herstart Grafana.",
                url,
            )
        if first.status not in REDIRECTS or "/login/generic_oauth" not in location:
            return CheckResult(
                "failed", f"Verwacht: doorsturen naar /login/generic_oauth. Gekregen: {first.summary()}.", url
            )
        nxt = urlsplit(urljoin(f"{base.scheme}://{base.domain}{prefix}/login", location))
        second = await self._probe(base, nxt.path + (f"?{nxt.query}" if nxt.query else ""))
        if second.error:
            return CheckResult(
                "unknown", f"VaultX bereikt {login.app_url} niet ({second.error}).", second.url
            )
        target = second.location or ""
        authorize = self._urls(login.application_slug or "").authorize
        if second.status not in REDIRECTS or not target.startswith(authorize):
            return CheckResult(
                "failed",
                f"Verwacht: doorsturen naar Authentik ({authorize}). Gekregen: {second.summary()}.",
                second.url,
            )
        q = _query(target)
        if q.get("client_id") != login.client_id:
            return CheckResult(
                "failed",
                f"Grafana stuurt naar Authentik met client '{q.get('client_id')}', niet met die van VaultX "
                f"('{login.client_id}').",
                second.url,
            )
        if q.get("redirect_uri") not in login.redirect_uris:
            return CheckResult(
                "failed",
                f"Grafana vraagt redirect URI {q.get('redirect_uri')}, Authentik aanvaardt enkel "
                f"{', '.join(login.redirect_uris)}. Zet GF_SERVER_ROOT_URL={login.app_url}/ in Grafana.",
                second.url,
            )
        return CheckResult(
            "ok",
            "Grafana stuurt bezoekers zonder sessie door naar Authentik, met de juiste client en redirect "
            "URI. Wie al bij Authentik is aangemeld, komt meteen binnen.",
            second.url,
        )

    async def _run_check(self, login: AppLogin, app: Application) -> CheckResult:
        result = CheckResult(
            "unknown", "Geen controle voor dit sjabloon: meld je aan in de app om te testen."
        )
        if login.template == "grafana":
            for attempt in range(CHECK_ATTEMPTS):
                result = await self._grafana_check(login, app)
                if result.status != "failed" or not login.app_configured or attempt == CHECK_ATTEMPTS - 1:
                    break
                await asyncio.sleep(CHECK_DELAY_SECONDS)
        login.last_check_at = datetime.now(UTC)
        login.last_check_status = result.status
        login.last_check_message = result.message
        return result

    async def check(self, p: Principal, org_id: UUID, app_id: UUID) -> AppLogin:
        app = await self._manage(p, org_id, app_id, "check")
        login = await self.logins.for_application(app.id)
        if login is None:
            raise NotFoundError("Geen automatische login ingericht voor deze app")
        result = await self._run_check(login, app)
        await self.audit.record(
            "app_login.check",
            p.actor,
            outcome="success" if result.status != "failed" else "failure",
            organization_id=org_id,
            target_type="application",
            target_id=app.id,
            details={"status": result.status, "message": result.message, "url": result.url},
        )
        await self.db.commit()
        await self.db.refresh(login)
        return login

    # ------------------------------------------------------------ config opvragen

    async def config(self, p: Principal, org_id: UUID, app_id: UUID) -> dict[str, Any]:
        app = await self._manage(p, org_id, app_id, "config_viewed")
        login = await self.logins.for_application(app.id)
        if login is None:
            raise NotFoundError("Geen automatische login ingericht voor deze app")
        try:
            secret = open_secret(
                self.settings.secret_key.get_secret_value(),
                login.client_secret_ciphertext,
                self._secret_context(login.id),
            )
        except Exception as exc:  # InvalidTag, ValueError
            raise UpstreamError(
                "Het client secret kan niet ontsleuteld worden (werd VAULTX_SECRET_KEY gewijzigd?). Haal de "
                "login weg en richt ze opnieuw in."
            ) from exc
        urls = self._urls(login.application_slug or "")
        out: dict[str, Any] = {
            "template": login.template,
            "client_id": login.client_id,
            "client_secret": secret,
            "issuer": urls.issuer,
            "discovery_url": urls.discovery,
            "redirect_uris": login.redirect_uris,
            "files": [],
        }
        if login.template == "grafana":
            values = self._grafana_values(login, login.client_id, secret, login.application_slug or "")
            out["files"] = [
                {"name": "grafana.ini", "content": grafana_ini(login.app_url, values)},
                {"name": "grafana.env", "content": grafana_env(login.app_url, values)},
            ]
        else:
            out["files"] = [
                {
                    "name": "oidc.txt",
                    "content": oidc_summary(urls, login.client_id, secret, login.redirect_uris),
                }
            ]
        await self.audit.record(
            "app_login.config_viewed",
            p.actor,
            organization_id=org_id,
            target_type="application",
            target_id=app.id,
            details={"client_id": login.client_id},
        )
        await self.db.commit()
        return out

    # ------------------------------------------------------------ weghalen

    async def remove(
        self, p: Principal, org_id: UUID, app_id: UUID, grafana: GrafanaAdmin | None, force: bool = False
    ) -> AppLogin | None:
        """Zet Grafana terug (als VaultX de login daar zette) en ruimt Authentik op.

        Geeft None als alles weg is, anders de rij met cleanup_error.
        """
        app = await self._manage(p, org_id, app_id, "remove")
        login = await self.logins.for_application(app.id)
        if login is None:
            raise NotFoundError("Geen automatische login ingericht voor deze app")
        details: dict[str, Any] = {"template": login.template, "client_id": login.client_id}
        if login.app_configured:
            if grafana is not None:
                url = normalize_app_url(grafana.url)
                if url is None:
                    raise InvalidOperationError("Ongeldig adres voor de Grafana-API")
                try:
                    async with self.grafana_factory(url, grafana.username, grafana.password) as g:
                        current = (await g.sso_settings()).get("settings") or {}
                        if current.get("clientId") == login.client_id:
                            await g.reset_sso_settings()
                            details["grafana_reset"] = True
                        else:
                            details["grafana_reset"] = False
                except GrafanaError as exc:
                    raise UpstreamError(f"Grafana: {exc.message} Er werd niets weggehaald.") from exc
            elif not force:
                raise InvalidOperationError(
                    "VaultX zette de login ook in Grafana. Geef een Grafana-beheerder op, zodat VaultX die "
                    "weer uitzet; anders stuurt Grafana iedereen naar een Authentik-provider die niet meer "
                    "bestaat en kan niemand nog aanmelden (behalve via /login?disableAutoLogin)."
                )
            else:
                details["grafana_reset"] = False
        state = {
            "provider_pk": login.provider_pk,
            "provider_created": login.provider_created,
            "slug": login.application_slug,
            "app_created": login.application_created,
        }
        try:
            async with self.authentik_factory() as client:
                errors = await self._undo(client, state)
        except AuthentikError as exc:
            errors = [exc.message]
        if errors:
            login.provider_created = state["provider_created"]
            login.application_created = state["app_created"]
            login.app_configured = login.app_configured and not details.get("grafana_reset")
            login.cleanup_error = "; ".join(errors)[:2000]
            await self.audit.record(
                "app_login.remove",
                p.actor,
                outcome="failure",
                organization_id=org_id,
                target_type="application",
                target_id=app.id,
                details={**details, "error": login.cleanup_error},
            )
            await self.db.commit()
            await self.db.refresh(login)
            return login
        await self.logins.delete(login)
        await self.audit.record(
            "app_login.remove",
            p.actor,
            organization_id=org_id,
            target_type="application",
            target_id=app.id,
            details={**details, "provider": login.provider_name, "application": login.application_slug},
        )
        await self.db.commit()
        return None


def _live_host(app: Application, domain: str | None = None) -> DiscoveredHost | None:
    hosts = [h for h in app.hosts if h.removed_at is None]
    if domain:
        hosts = [h for h in hosts if domain.lower() in (d.lower() for d in h.domain_names)]
    return hosts[0] if hosts else None


def _template_out(t: LoginTemplate) -> dict[str, Any]:
    return {
        "key": t.key,
        "label": t.label,
        "description": t.description,
        "redirect_path": t.redirect_path,
        "can_configure_app": t.can_configure_app,
    }
