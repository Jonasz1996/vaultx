from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel

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

    _url = field_validator("base_url")(_check_url)


class NpmConnectionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    base_url: str | None = Field(None, max_length=2048)
    identity: str | None = Field(None, min_length=1, max_length=320)
    secret: str | None = Field(None, min_length=1, max_length=1024)
    verify_tls: bool | None = None
    enabled: bool | None = None

    _url = field_validator("base_url")(_check_url)


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
    ignored: bool
    first_seen_at: datetime
    last_seen_at: datetime
    removed_at: datetime | None
    application: ApplicationRef | None


class DiscoveredHostUpdate(BaseModel):
    ignored: bool


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
    created_at: datetime
    updated_at: datetime
