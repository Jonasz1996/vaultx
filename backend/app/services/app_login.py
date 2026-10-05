"""Automatische login voor een app uit de catalogus (fase 5).

Het hoofdscenario uit de opdracht: een gebruiker die al bij Authentik is
aangemeld, opent een app achter NPM en zit er meteen in, zonder tweede login.
Methode 4 uit 02b: de app wordt zelf een OpenID Connect-client van Authentik.

VaultX praat daarvoor enkel met Authentik (de app-URL komt uit de catalogus,
dus uit NPM). Het koppelt niet met de app zelf: de beheerder vult issuer,
client ID en secret in de app in.

1. Voorbeeld: VaultX leest Authentik (flows, scope mappings, certificaat,
   groepen, bestaande providers). Er verandert niets.
2. Toepassen: in Authentik een OAuth2/OpenID-provider met de redirect URI's
   van de app, een applicatie en groepsbindingen voor de toegang. Faalt een
   stap, dan draait VaultX terug wat het al aanmaakte.
3. Config: de instellingen voor de app, met het client secret (staat
   versleuteld in app_logins; opvragen staat in de auditlog).
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, InvalidOperationError, NotFoundError, UpstreamError
from app.core.security import open_secret, seal_secret
from app.models import Application, AppLogin
from app.repositories import (
    ApplicationRepository,
    AppLoginRepository,
    OrganizationRepository,
    TeamRepository,
)
from app.services.audit import AuditService
from app.services.authentik_client import AuthentikClient, AuthentikError
from app.services.authentik_protect import ACCESS_RE, NAME_ATTEMPTS, access_groups, access_label
from app.services.npm_protect import Check
from app.services.principal import Principal

PROVIDER_PREFIX = "VaultX login: "
SLUG_PREFIX = "vaultx-login-"
MAX_SLUG = 50
# Scopes die de provider meekrijgt (beheerde scope mappings van Authentik).
SCOPES = ("openid", "email", "profile", "offline_access")
SCOPE_MANAGED = {s: f"goauthentik.io/providers/oauth2/scope-{s}" for s in SCOPES}

AuthentikFactory = Callable[[], AuthentikClient]


def login_slug(domain: str, attempt: int = 0) -> str:
    base = SLUG_PREFIX + re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")
    suffix = f"-{attempt + 1}" if attempt else ""
    return base[: MAX_SLUG - len(suffix)].rstrip("-") + suffix


def login_provider_name(domain: str, attempt: int = 0) -> str:
    return PROVIDER_PREFIX + domain + (f" ({attempt + 1})" if attempt else "")


def normalize_app_url(url: str | None) -> str | None:
    """https://app.example.be/ -> https://app.example.be; None bij een ongeldige URL."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        parts.port  # noqa: B018 - gooit ValueError bij een ongeldige poort
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment:
        return None
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def valid_redirect_uri(uri: str) -> bool:
    """Een redirect URI moet een absolute http(s)-URL zijn, zonder fragment."""
    try:
        parts = urlsplit(uri)
        parts.port  # noqa: B018
    except ValueError:
        return False
    return (
        parts.scheme in ("http", "https")
        and bool(parts.hostname)
        and not parts.fragment
        and not any(c.isspace() for c in uri)
    )


@dataclass(frozen=True, slots=True)
class AuthentikUrls:
    """URL's van Authentik zoals browsers en de app ze gebruiken (publieke URL)."""

    base: str
    slug: str

    @property
    def issuer(self) -> str:
        return f"{self.base}/application/o/{self.slug}/"

    @property
    def discovery(self) -> str:
        return f"{self.issuer}.well-known/openid-configuration"

    @property
    def authorize(self) -> str:
        return f"{self.base}/application/o/authorize/"

    @property
    def token(self) -> str:
        return f"{self.base}/application/o/token/"

    @property
    def userinfo(self) -> str:
        return f"{self.base}/application/o/userinfo/"

    @property
    def end_session(self) -> str:
        return f"{self.issuer}end-session/"


def config_text(urls: AuthentikUrls, client_id: str, client_secret: str, redirects: list[str]) -> str:
    """De instellingen om in de app in te vullen, als tekst om te kopiëren."""
    lines = [
        "# In te vullen in de app (OpenID Connect / OAuth2).",
        f"Issuer:                  {urls.issuer}",
        f"Discovery-URL:           {urls.discovery}",
        f"Client ID:               {client_id}",
        f"Client secret:           {client_secret}",
        f"Scopes:                  {' '.join(SCOPES)}",
        f"Authorization URL:       {urls.authorize}",
        f"Token URL:               {urls.token}",
        f"Userinfo URL:            {urls.userinfo}",
        f"Uitloggen (end session): {urls.end_session}",
        "Redirect URI('s) die Authentik aanvaardt:",
        *[f"  {u}" for u in redirects],
        "Gebruikersnaam-claim: preferred_username; e-mail: email; groepen: groups.",
    ]
    return "\n".join(lines) + "\n"


@dataclass(slots=True)
class LoginRequest:
    redirect_uris: list[str]
    access: str = "organization"
    app_url: str | None = None


@dataclass(slots=True)
class LoginPlan:
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

    @property
    def blocked(self) -> bool:
        return any(c.level == "block" for c in self.checks)

    def block(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "block", message))

    def warn(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "warn", message))

    def info(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "info", message))


class AppLoginService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        authentik_factory: AuthentikFactory | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.apps = ApplicationRepository(db)
        self.logins = AppLoginRepository(db)
        self.orgs = OrganizationRepository(db)
        self.audit = AuditService(db)
        self.authentik_factory = authentik_factory or self._default_authentik

    def _default_authentik(self) -> AuthentikClient:
        token = self.settings.authentik_api_token
        return AuthentikClient(
            self.settings.authentik_base_url,
            token.get_secret_value() if token else "",
            verify_tls=self.settings.authentik_verify_tls,
            timeout=self.settings.authentik_http_timeout_seconds,
        )

    def _secret_context(self, login_id: UUID) -> str:
        return f"app-login:{login_id}"

    def _urls(self, slug: str) -> AuthentikUrls:
        return AuthentikUrls(self.settings.authentik_public_base, slug)

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
        return {
            "configured": self.settings.authentik_api_enabled,
            "authentik_url": self.settings.authentik_public_base,
            "suggested_app_url": normalize_app_url(app.url),
            "login": await self.logins.for_application(app.id),
        }

    # ------------------------------------------------------------ voorbeeld

    async def _context(self, app: Application) -> dict[str, Any]:
        org = await self.orgs.get(app.organization_id)
        teams = await TeamRepository(self.db).list_for_org(app.organization_id)
        return {"org_slug": org.slug if org else "", "team_slugs": {t.slug for t in teams}}

    async def _plan(
        self, client: AuthentikClient, app: Application, req: LoginRequest, ctx: dict[str, Any]
    ) -> LoginPlan:
        app_url = normalize_app_url(req.app_url or app.url)
        redirects = list(dict.fromkeys(u.strip() for u in req.redirect_uris if u.strip()))
        plan = LoginPlan(app_url=app_url, redirect_uris=redirects, access=req.access)
        plan.app_name = app.name
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
        if not redirects:
            plan.block("login_redirect_missing", "Geef minstens één redirect URI van de app op.")
        for uri in redirects:
            if not valid_redirect_uri(uri):
                plan.block("login_redirect_invalid", f"Ongeldige redirect URI: {uri}")
            elif uri.startswith("http://"):
                plan.warn(
                    "login_http",
                    f"Redirect URI {uri} gaat over http: codes en tokens gaan dan onversleuteld over het "
                    "netwerk. Zet HTTPS aan in NPM.",
                )
        if plan.blocked:
            return plan

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
            f"{', '.join(plan.redirect_uris)}, scopes {', '.join(SCOPES)})."
        )
        plan.steps.append(f"Authentik: applicatie '{app.name}' ({login_slug(domain)}) aanmaken.")
        if plan.groups:
            plan.steps.append(
                "Authentik: aanmelden enkel voor " + ", ".join(f"'{g['name']}'" for g in plan.groups) + "."
            )
        else:
            plan.steps.append("Authentik: aanmelden voor alle Authentik-gebruikers (geen groepsbinding).")
        plan.steps.append(
            "Daarna toont VaultX issuer, client ID en secret om in de app in te vullen. VaultX wijzigt niets "
            "in de app zelf."
        )
        return plan

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
                except AuthentikError as exc:
                    errors = await self._undo(client, state)
                    message = f"Authentik: {exc.message}"
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
                        details={"app_url": plan.app_url, "error": message[:500]},
                    )
                    await self.db.commit()
                    raise UpstreamError(message) from exc
        except AuthentikError as exc:  # factory of contextmanager
            raise UpstreamError(f"Authentik: {exc.message}") from exc

        login = AppLogin(
            organization_id=org_id,
            application_id=app.id,
            app_url=plan.app_url,
            redirect_uris=plan.redirect_uris,
            access=req.access,
            groups=[g["name"] for g in plan.groups],
            provider_pk=state["provider_pk"],
            provider_name=state.get("provider_name") or plan.provider_name,
            provider_created=True,
            application_slug=state["slug"],
            application_name=state.get("app_name"),
            application_created=True,
            client_id=str(provider["client_id"]),
            client_secret_ciphertext=b"",
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
                "app_url": plan.app_url,
                "redirect_uris": plan.redirect_uris,
                "provider": login.provider_name,
                "application": login.application_slug,
                "client_id": login.client_id,
                "access": req.access,
                "groups": login.groups,
            },
        )
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
        out = {
            "client_id": login.client_id,
            "client_secret": secret,
            "issuer": urls.issuer,
            "discovery_url": urls.discovery,
            "authorization_url": urls.authorize,
            "token_url": urls.token,
            "userinfo_url": urls.userinfo,
            "end_session_url": urls.end_session,
            "scopes": list(SCOPES),
            "redirect_uris": login.redirect_uris,
            "text": config_text(urls, login.client_id, secret, login.redirect_uris),
        }
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

    async def remove(self, p: Principal, org_id: UUID, app_id: UUID) -> AppLogin | None:
        """Ruimt de provider en applicatie in Authentik op.

        Geeft None als alles weg is, anders de rij met cleanup_error.
        """
        app = await self._manage(p, org_id, app_id, "remove")
        login = await self.logins.for_application(app.id)
        if login is None:
            raise NotFoundError("Geen automatische login ingericht voor deze app")
        details: dict[str, Any] = {"client_id": login.client_id}
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
