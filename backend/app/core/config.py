"""Applicatieconfiguratie, volledig via omgevingsvariabelen met prefix VAULTX_."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VAULTX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Algemeen
    environment: str = "production"
    log_level: str = "INFO"
    # Publieke basis-URL zoals de browser VaultX ziet (achter NPM/nginx), zonder slash op het einde.
    public_url: str = "http://localhost:8080"
    # Sleutel voor het ondertekenen van de korte OIDC-state cookie. Minstens 32 tekens.
    secret_key: SecretStr = Field(min_length=32)

    # Database
    database_url: str = "postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx"
    database_pool_size: int = 10
    database_echo: bool = False

    # OIDC / Authentik
    # Issuer exact zoals Authentik hem publiceert (inclusief slash op het einde),
    # bv. https://auth.example.com/application/o/vaultx/
    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: SecretStr
    oidc_scopes: str = "openid email profile"
    # Verwachte audience van bearer tokens voor API-clients. Leeg = client_id.
    oidc_api_audience: str | None = None
    # Authentik-groepen die VaultX-instantiebeheerder maken.
    oidc_admin_groups: Annotated[list[str], NoDecode] = ["vaultx-admins"]
    # Groepen met dit prefix worden vertaald naar lidmaatschappen (zie docs/authentik.md).
    oidc_group_sync: bool = True
    oidc_group_prefix: str = "vaultx:"
    oidc_http_timeout_seconds: float = 10.0
    oidc_verify_tls: bool = True

    # Sessies
    session_cookie_name: str = "vaultx_session"
    session_ttl_hours: int = 12
    session_idle_minutes: int = 120
    cookie_secure: bool = True

    # NPM-connector
    # Elke zoveel minuten alle actieve NPM-koppelingen synchroniseren. 0 = enkel handmatig.
    npm_sync_interval_minutes: int = Field(15, ge=0)
    npm_http_timeout_seconds: float = 15.0

    @field_validator("oidc_admin_groups", mode="before")
    @classmethod
    def _split_groups(cls, value: object) -> object:
        if isinstance(value, str):
            return [g.strip() for g in value.split(",") if g.strip()]
        return value

    @field_validator("public_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def discovery_url(self) -> str:
        return f"{self.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_url}/auth/callback"

    @property
    def api_audience(self) -> str:
        return self.oidc_api_audience or self.oidc_client_id


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
