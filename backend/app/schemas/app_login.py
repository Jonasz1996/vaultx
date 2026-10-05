"""Schema's voor automatische login (fase 5)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.catalog import ACCESS_PATTERN, CheckOut
from app.schemas.common import ORMModel

TemplateLiteral = Literal["grafana", "oidc"]
GrafanaRoleLiteral = Literal["Viewer", "Editor", "Admin"]


class GrafanaAdminIn(BaseModel):
    """Grafana-serverbeheerder voor één actie. VaultX bewaart dit niet."""

    url: str = Field(max_length=2048, description="Adres van Grafana voor VaultX, bv. http://10.0.0.5:3000")
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class AppLoginIn(BaseModel):
    template: TemplateLiteral
    access: str = Field(
        "organization",
        pattern=ACCESS_PATTERN,
        description="Wie mag aanmelden: all, organization of team:<slug>",
    )
    app_url: str | None = Field(
        None, max_length=2048, description="URL waarop gebruikers de app openen; leeg = URL uit de catalogus"
    )
    redirect_uris: list[str] = Field(
        default_factory=list, max_length=10, description="Enkel voor sjabloon oidc: redirect URI's van de app"
    )
    default_role: GrafanaRoleLiteral = Field(
        "Viewer", description="Grafana: rol voor wie geen eigenaar of beheerder van de organisatie is"
    )
    grafana: GrafanaAdminIn | None = Field(
        None, description="Grafana: met een beheerder zet VaultX de login meteen in Grafana"
    )


class AppLoginRemoveIn(BaseModel):
    grafana: GrafanaAdminIn | None = None
    force: bool = Field(False, description="Weghalen zonder Grafana terug te zetten")


class LoginTemplateOut(BaseModel):
    key: TemplateLiteral
    label: str
    description: str
    redirect_path: str | None
    can_configure_app: bool


class AppLoginOut(ORMModel):
    id: UUID
    application_id: UUID
    template: TemplateLiteral
    app_url: str
    redirect_uris: list[str]
    access: str
    groups: list[str]
    options: dict
    provider_pk: int
    provider_name: str
    provider_created: bool
    application_slug: str | None
    application_name: str | None
    application_created: bool
    client_id: str
    app_configured: bool
    app_configured_at: datetime | None
    last_check_at: datetime | None
    last_check_status: Literal["ok", "failed", "unknown"] | None
    last_check_message: str | None
    cleanup_error: str | None
    created_at: datetime


class AppLoginStateOut(BaseModel):
    configured: bool = Field(description="Staat de Authentik-API ingesteld op de VaultX-server?")
    authentik_url: str = Field(description="Publieke URL van Authentik in de app-config")
    templates: list[LoginTemplateOut]
    suggested_app_url: str | None
    suggested_grafana_url: str | None = Field(description="Doel van de NPM-host: zo bereikt VaultX Grafana")
    login: AppLoginOut | None


class AppLoginPlanOut(BaseModel):
    template: TemplateLiteral
    app_url: str | None
    redirect_uris: list[str]
    access: str
    can_apply: bool
    checks: list[CheckOut]
    steps: list[str]
    groups: list[str]
    configure_app: bool


class ConfigFileOut(BaseModel):
    name: str
    content: str


class AppLoginConfigOut(BaseModel):
    template: TemplateLiteral
    client_id: str
    client_secret: str
    issuer: str
    discovery_url: str
    redirect_uris: list[str]
    files: list[ConfigFileOut]
