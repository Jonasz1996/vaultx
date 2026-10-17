"""Schema's voor automatische login (fase 5)."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.catalog import ACCESS_PATTERN, CheckOut
from app.schemas.common import ORMModel


class AppLoginIn(BaseModel):
    redirect_uris: list[Annotated[str, Field(max_length=2048)]] = Field(
        default_factory=list,
        max_length=10,
        description="Redirect URI's van de app (staan in de documentatie van de app)",
    )
    access: str = Field(
        "organization",
        pattern=ACCESS_PATTERN,
        description="Wie mag aanmelden: all, organization of team:<slug>",
    )
    app_url: str | None = Field(
        None, max_length=2048, description="URL waarop gebruikers de app openen; leeg = URL uit de catalogus"
    )


class AppLoginOut(ORMModel):
    id: UUID
    application_id: UUID
    app_url: str
    redirect_uris: list[str]
    access: str
    groups: list[str]
    provider_pk: int
    provider_name: str
    provider_created: bool
    application_slug: str | None
    application_name: str | None
    application_created: bool
    client_id: str
    cleanup_error: str | None
    created_at: datetime


class AppLoginStateOut(BaseModel):
    configured: bool = Field(description="Staat de Authentik-API ingesteld op de VaultX-server?")
    authentik_url: str = Field(description="Publieke URL van Authentik in de app-config")
    suggested_app_url: str | None
    login: AppLoginOut | None


class AppLoginPlanOut(BaseModel):
    app_url: str | None
    redirect_uris: list[str]
    access: str
    can_apply: bool
    checks: list[CheckOut]
    steps: list[str]
    groups: list[str]


class AppLoginConfigOut(BaseModel):
    client_id: str
    client_secret: str
    issuer: str
    discovery_url: str
    authorization_url: str
    token_url: str
    userinfo_url: str
    end_session_url: str
    scopes: list[str]
    redirect_uris: list[str]
    text: str = Field(description="Alles hierboven als tekst om te kopiëren")
