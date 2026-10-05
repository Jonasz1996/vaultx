import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel
from app.services.npm_protect import normalize_outpost_url

AuthMethodLiteral = Literal["forward_auth", "oidc", "saml", "header", "access_list", "app", "none", "unknown"]
# protected: aanmelding via Authentik (forward auth, OIDC, SAML, headers)
# restricted: wel een drempel, maar niet via Authentik (access list, eigen login)
# unprotected: bewust open · unknown: nog niet bepaald
# offline: host uitgeschakeld of nginx-fout · removed: host niet meer in NPM
AppStatusLiteral = Literal["protected", "restricted", "unprotected", "unknown", "offline", "removed"]


def _check_url(value: str | None) -> str | None:
    if value is None:
        return value
    value = value.strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        raise ValueError("URL moet met http:// of https:// beginnen")
    return value


PROBE_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.:-]{0,253}[A-Za-z0-9])?$")


def _check_outpost(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return normalize_outpost_url(value)


def _check_outpost_pk(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        return str(UUID(value.strip()))
    except ValueError as exc:
        raise ValueError("Ongeldige outpost (verwacht de UUID van de outpost in Authentik)") from exc


def _check_probe_host(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip().strip("[]")
    if not PROBE_HOST_RE.fullmatch(value):
        raise ValueError("Geef enkel een hostnaam of IP-adres op, zonder http:// of poort")
    return value


class Warning_(BaseModel):
    code: str
    message: str


# ---------------------------------------------------------------- NPM-koppelingen


class NpmConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    base_url: str = Field(
        max_length=2048, description="Beheer-URL van NPM, bv. http://npm.lan:81", examples=["http://npm:81"]
    )
    identity: str = Field(min_length=1, max_length=320, description="E-mailadres van het NPM-account")
    secret: str = Field(min_length=1, max_length=1024, description="Wachtwoord; wordt nooit teruggegeven")
    verify_tls: bool = True
    enabled: bool = True
    write_enabled: bool = Field(
        False, description="VaultX mag Authentik-bescherming zetten (account: Proxy Hosts = Manage)"
    )
    authentik_outpost_url: str | None = Field(
        None,
        max_length=2048,
        description="Hoe nginx in NPM de Authentik-outpost bereikt",
        examples=["http://authentik-server:9000"],
    )
    probe_host: str | None = Field(
        None,
        max_length=255,
        description="Adres waarop VaultX de proxy hosts controleert; leeg = host van base_url",
    )
    probe_http_port: int = Field(80, ge=1, le=65535)
    probe_https_port: int = Field(443, ge=1, le=65535)
    authentik_outpost_pk: str | None = Field(
        None,
        max_length=64,
        description="Outpost in Authentik waarop VaultX zelf providers zet; leeg = niets aanmaken",
    )

    _url = field_validator("base_url")(_check_url)
    _outpost = field_validator("authentik_outpost_url")(_check_outpost)
    _outpost_pk = field_validator("authentik_outpost_pk")(_check_outpost_pk)
    _probe = field_validator("probe_host")(_check_probe_host)


class NpmConnectionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    base_url: str | None = Field(None, max_length=2048)
    identity: str | None = Field(None, min_length=1, max_length=320)
    secret: str | None = Field(None, min_length=1, max_length=1024)
    verify_tls: bool | None = None
    enabled: bool | None = None
    write_enabled: bool | None = None
    # Lege string = wissen.
    authentik_outpost_url: str | None = Field(None, max_length=2048)
    probe_host: str | None = Field(None, max_length=255)
    probe_http_port: int | None = Field(None, ge=1, le=65535)
    probe_https_port: int | None = Field(None, ge=1, le=65535)
    authentik_outpost_pk: str | None = Field(None, max_length=64)

    _url = field_validator("base_url")(_check_url)

    @field_validator("authentik_outpost_pk")
    @classmethod
    def _outpost_pk(cls, v: str | None) -> str | None:
        return v if v == "" else _check_outpost_pk(v)

    @field_validator("authentik_outpost_url")
    @classmethod
    def _outpost(cls, v: str | None) -> str | None:
        return v if v == "" else _check_outpost(v)

    @field_validator("probe_host")
    @classmethod
    def _probe(cls, v: str | None) -> str | None:
        return v if v == "" else _check_probe_host(v)


class NpmConnectionOut(ORMModel):
    id: UUID
    organization_id: UUID
    name: str
    base_url: str
    identity: str
    verify_tls: bool
    enabled: bool
    npm_version: str | None
    last_sync_at: datetime | None
    last_sync_status: Literal["ok", "error"] | None
    last_sync_error: str | None
    write_enabled: bool
    authentik_outpost_url: str | None
    probe_host: str | None
    probe_http_port: int
    probe_https_port: int
    authentik_outpost_pk: str | None
    created_at: datetime
    updated_at: datetime
    host_count: int = 0


class SyncResultOut(BaseModel):
    connection_id: UUID
    npm_version: str | None
    hosts_total: int
    hosts_new: int
    hosts_updated: int
    hosts_removed: int
    applications_created: int
    applications_updated: int


class ApplicationRef(ORMModel):
    id: UUID
    name: str


class DiscoveredHostOut(ORMModel):
    id: UUID
    connection_id: UUID
    npm_id: int
    domain_names: list[str]
    forward_scheme: str
    forward_host: str
    forward_port: int
    enabled: bool
    nginx_online: bool
    ssl: bool
    ssl_forced: bool
    access_list: str | None
    forward_auth: bool
    detected_app_type: str | None
    detected_auth: AuthMethodLiteral
    labels: dict[str, str]
    warnings: list[Warning_]
    vaultx_managed: bool
    ignored: bool
    first_seen_at: datetime
    last_seen_at: datetime
    removed_at: datetime | None
    application: ApplicationRef | None


class DiscoveredHostUpdate(BaseModel):
    ignored: bool


# ---------------------------------------------------------------- schrijven naar NPM (fase 3)

ProtectionAction = Literal["protect", "unprotect"]
# Wie de applicatie na de Authentik-aanmelding mag openen (fase 4).
ACCESS_PATTERN = r"^(all|organization|team:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)$"


class CheckOut(BaseModel):
    code: str
    level: Literal["block", "warn", "info"]
    message: str


class HostConfigOut(BaseModel):
    """De velden die VaultX in NPM wijzigt, zoals NPM ze kent."""

    advanced_config: str = ""
    locations: list[dict[str, Any]] = Field(default_factory=list)


class ProtectionPlanOut(BaseModel):
    action: ProtectionAction
    npm_id: int
    domain: str
    modified_on: str | None = Field(
        description="Meesturen bij uitvoeren: zo weigert VaultX als de host intussen wijzigde"
    )
    can_apply: bool
    checks: list[CheckOut]
    steps: list[str]
    before: HostConfigOut
    after: HostConfigOut
    probe_url: str | None = Field(description="Waar VaultX de host achteraf aanspreekt")
    authentik: dict[str, Any] | None = Field(
        None, description="Fase 4: wat VaultX in Authentik aanmaakt of opruimt; null = niets"
    )


class ProtectionApply(BaseModel):
    action: ProtectionAction
    expected_modified_on: str | None = None
    verify: bool = Field(True, description="Host vooraf en achteraf aanspreken; bij een fout terugzetten")
    access: str = Field(
        "organization",
        pattern=ACCESS_PATTERN,
        description="Toegang tot de Authentik-applicatie: all, organization of team:<slug>",
    )


class NpmChangeOut(ORMModel):
    id: UUID
    connection_id: UUID
    host_id: UUID | None
    npm_id: int
    domain: str
    action: ProtectionAction
    status: Literal["running", "applied", "rolled_back", "rollback_failed", "refused", "interrupted"]
    verified: bool
    actor_label: str | None
    message: str | None
    nginx_error: str | None
    before: dict[str, Any]
    after: dict[str, Any] | None
    probe_before: dict[str, Any] | None
    probe_after: dict[str, Any] | None
    authentik: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None


# ---------------------------------------------------------------- catalogus


class ApplicationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    app_type: str | None = Field(None, max_length=64)
    url: str | None = Field(None, max_length=2048)
    description: str | None = Field(None, max_length=4000)
    auth_method: AuthMethodLiteral = "unknown"
    tags: list[str] = Field(default_factory=list, max_length=20)

    _url = field_validator("url")(_check_url)


class ApplicationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    app_type: str | None = Field(None, max_length=64)
    url: str | None = Field(None, max_length=2048)
    description: str | None = Field(None, max_length=4000)
    auth_method: AuthMethodLiteral | None = None
    tags: list[str] | None = Field(None, max_length=20)
    auto_update: bool | None = Field(
        None,
        description="Sync mag naam, type, URL en aanmelding bijwerken; gaat uit bij handmatig wijzigen.",
    )

    _url = field_validator("url")(_check_url)


class ApplicationHostOut(ORMModel):
    id: UUID
    connection_id: UUID
    connection_name: str
    domain_names: list[str]
    forward: str
    enabled: bool
    nginx_online: bool
    ssl: bool
    forward_auth: bool
    warnings: list[Warning_]
    removed_at: datetime | None


class ApplicationOut(BaseModel):
    id: UUID
    organization_id: UUID
    name: str
    app_type: str | None
    url: str | None
    description: str | None
    auth_method: AuthMethodLiteral
    status: AppStatusLiteral
    tags: list[str]
    source: Literal["npm", "manual"]
    auto_update: bool
    hosts: list[ApplicationHostOut]
    warning_count: int
    auto_login: bool = Field(False, description="Fase 5: automatische login via Authentik ingericht")
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------- Authentik (fase 4)


class AuthentikOutpostOut(BaseModel):
    pk: str
    name: str
    managed: str | None = None
    authentik_host: str | None = None
    provider_count: int = 0


class AuthentikOutpostsOut(BaseModel):
    configured: bool = Field(description="Staat de Authentik-API ingesteld op de VaultX-server?")
    api_url: str
    outposts: list[AuthentikOutpostOut] = Field(default_factory=list)
    error: str | None = None
