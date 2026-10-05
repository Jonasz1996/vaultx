"""Applicatieconfiguratie, volledig via omgevingsvariabelen met prefix VAULTX_."""

from functools import lru_cache
from typing import Annotated
from urllib.parse import urlsplit

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

    # Authentik-API (fase 4): VaultX maakt bij "Beschermen met Authentik" zelf de proxy provider,
    # de applicatie en de outpost-toewijzing aan. Leeg token = uit. Gebruik een serviceaccount met
    # enkel de rechten uit docs/npm.md, geen superuser-token.
    # Basis-URL van Authentik; leeg = scheme en host van VAULTX_OIDC_ISSUER.
    authentik_api_url: str | None = None
    authentik_api_token: SecretStr | None = None
    authentik_verify_tls: bool = True
    authentik_http_timeout_seconds: float = 15.0
    # Flows voor de proxy- en OAuth2-providers die VaultX aanmaakt (slugs, standaard die van Authentik zelf).
    authentik_authorization_flow: str = "default-provider-authorization-implicit-consent"
    authentik_invalidation_flow: str = "default-provider-invalidation-flow"
    # Automatische login (fase 5): publieke URL van Authentik zoals browsers en apps hem gebruiken
    # (issuer, authorize- en token-URL in de app-config). Leeg = scheme en host van VAULTX_OIDC_ISSUER.
    authentik_public_url: str | None = None
    # Certificaat waarmee Authentik de ID-tokens van door VaultX aangemaakte OIDC-providers tekent.
    # Bestaat het niet, dan tekent Authentik met het client secret (HS256).
    authentik_signing_key: str = "authentik Self-signed Certificate"

    # Kluis voor Bitwarden-clients (fase 2)
    vault_enabled: bool = True
    # Levensduur van een access token voor Bitwarden-clients.
    vault_access_token_minutes: int = Field(60, ge=5, le=24 * 60)
    # Een apparaat dat zo lang niets van zich liet horen, moet opnieuw inloggen.
    vault_device_idle_days: int = Field(30, ge=1)
    # Na zoveel foute master passwords na elkaar wordt het account tijdelijk vergrendeld.
    vault_max_failed_logins: int = Field(10, ge=1)
    vault_lockout_minutes: int = Field(15, ge=1)

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

    @field_validator("authentik_api_url", "authentik_public_url")
    @classmethod
    def _strip_ak_url(cls, value: str | None) -> str | None:
        return value.strip().rstrip("/") or None if value else None

    @property
    def authentik_base_url(self) -> str:
        if self.authentik_api_url:
            return self.authentik_api_url
        parts = urlsplit(self.oidc_issuer)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def authentik_public_base(self) -> str:
        if self.authentik_public_url:
            return self.authentik_public_url
        parts = urlsplit(self.oidc_issuer)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def authentik_api_enabled(self) -> bool:
        return bool(self.authentik_api_token and self.authentik_api_token.get_secret_value())

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
