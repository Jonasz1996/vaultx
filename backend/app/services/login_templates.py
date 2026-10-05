"""Sjablonen voor automatische login (fase 5): wat een app van Authentik nodig heeft.

Een sjabloon zegt welke redirect URI de app gebruikt en welke instellingen de
app zelf moet krijgen. VaultX maakt in Authentik een OAuth2/OpenID-provider
met die redirect URI en toont (of zet, bij Grafana) de instellingen voor de
app. Methode 4 uit 02b: de app wordt zelf een client van Authentik, dus geen
wachtwoord meer en geen tweede login voor wie al een Authentik-sessie heeft.

- ``grafana``: generic OAuth in Grafana, met ``auto_login`` (meteen door naar
  Authentik), PKCE en refresh tokens expliciet aan (staan standaard uit in
  Grafana, onderzoek 03 E4), en rollen uit de Authentik-groepen van de
  organisatie via ``role_attribute_path``.
- ``oidc``: eender welke app met OpenID Connect; de beheerder geeft de
  redirect URI op en krijgt issuer, client ID en secret.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

GRAFANA_ROLES = ("Viewer", "Editor", "Admin")
# Scopes die de provider meekrijgt (beheerde scope mappings van Authentik).
SCOPES = ("openid", "email", "profile", "offline_access")
SCOPE_MANAGED = {s: f"goauthentik.io/providers/oauth2/scope-{s}" for s in SCOPES}


@dataclass(frozen=True, slots=True)
class LoginTemplate:
    key: str
    label: str
    # app_type's uit de catalogus waarvoor dit sjabloon past; None = elke app.
    app_types: frozenset[str] | None
    description: str
    # Pad achter de app-URL waar de app de code van Authentik ontvangt; None = beheerder geeft op.
    redirect_path: str | None = None
    # VaultX kan de instellingen zelf in de app zetten (met een beheerdersaccount van de app).
    can_configure_app: bool = False


TEMPLATES: dict[str, LoginTemplate] = {
    "grafana": LoginTemplate(
        key="grafana",
        label="Grafana",
        app_types=frozenset({"grafana"}),
        description=(
            "Grafana meldt aan via Authentik (generic OAuth, automatisch doorsturen). Rollen komen uit de "
            "Authentik-groepen van de organisatie."
        ),
        redirect_path="/login/generic_oauth",
        can_configure_app=True,
    ),
    "oidc": LoginTemplate(
        key="oidc",
        label="Andere app (OpenID Connect)",
        app_types=None,
        description=(
            "Voor elke app die OpenID Connect kent (Portainer, Gitea, Proxmox, ...). Je geeft de redirect "
            "URI van de app op en krijgt issuer, client ID en secret om in de app in te vullen."
        ),
    ),
}


def templates_for(app_type: str | None) -> list[LoginTemplate]:
    """Passende sjablonen voor een app, het meest specifieke eerst."""
    specific = [t for t in TEMPLATES.values() if t.app_types and app_type in t.app_types]
    generic = [t for t in TEMPLATES.values() if t.app_types is None]
    return specific + generic


def normalize_app_url(url: str | None) -> str | None:
    """https://grafana.example.be/ -> https://grafana.example.be; None bij een ongeldige URL."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment:
        return None
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"


def redirect_uris(template: LoginTemplate, app_url: str, custom: list[str]) -> list[str]:
    if template.redirect_path:
        return [app_url + template.redirect_path]
    return [u.strip() for u in custom if u.strip()]


# ---------------------------------------------------------------- Authentik-URL's


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


# ---------------------------------------------------------------- Grafana

_JMES_SAFE = re.compile(r"^[^\n\r]{1,255}$")


def _jmes_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def grafana_admin_groups(group_prefix: str, org_slug: str, instance_admin_groups: list[str]) -> list[str]:
    """Authentik-groepen die in Grafana 'Admin' geven: eigenaars en beheerders van de organisatie,
    plus de VaultX-instantiebeheerders."""
    groups = [f"{group_prefix}{org_slug}:owner", f"{group_prefix}{org_slug}:admin", *instance_admin_groups]
    return [g for g in dict.fromkeys(groups) if _JMES_SAFE.fullmatch(g)]


def grafana_role_path(admin_groups: list[str], default_role: str) -> str:
    """JMESPath voor role_attribute_path. && bindt sterker dan ||, vandaar de haakjes."""
    if default_role not in GRAFANA_ROLES:
        raise ValueError(f"Onbekende Grafana-rol {default_role}")
    if not admin_groups or default_role == "Admin":
        return _jmes_literal(default_role)
    tests = " || ".join(f"contains(groups[*], {_jmes_literal(g)})" for g in admin_groups)
    return f"({tests}) && 'Admin' || {_jmes_literal(default_role)}"


# (sleutel in grafana.ini, sleutel in de SSO settings API)
_GRAFANA_KEYS = (
    ("enabled", "enabled"),
    ("name", "name"),
    ("client_id", "clientId"),
    ("client_secret", "clientSecret"),
    ("scopes", "scopes"),
    ("auth_url", "authUrl"),
    ("token_url", "tokenUrl"),
    ("api_url", "apiUrl"),
    ("use_pkce", "usePkce"),
    ("use_refresh_token", "useRefreshToken"),
    ("auto_login", "autoLogin"),
    ("allow_sign_up", "allowSignUp"),
    ("login_attribute_path", "loginAttributePath"),
    ("name_attribute_path", "nameAttributePath"),
    ("email_attribute_path", "emailAttributePath"),
    ("role_attribute_path", "roleAttributePath"),
    ("role_attribute_strict", "roleAttributeStrict"),
    ("signout_redirect_url", "signoutRedirectUrl"),
)


def grafana_values(urls: AuthentikUrls, client_id: str, client_secret: str, role_path: str) -> dict[str, Any]:
    """De generic OAuth-instellingen, met de sleutels uit grafana.ini."""
    return {
        "enabled": True,
        "name": "Authentik",
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": " ".join(SCOPES),
        "auth_url": urls.authorize,
        "token_url": urls.token,
        "api_url": urls.userinfo,
        "use_pkce": True,
        "use_refresh_token": True,
        "auto_login": True,
        "allow_sign_up": True,
        "login_attribute_path": "preferred_username",
        "name_attribute_path": "name",
        "email_attribute_path": "email",
        "role_attribute_path": role_path,
        "role_attribute_strict": False,
        "signout_redirect_url": urls.end_session,
    }


def grafana_api_settings(values: dict[str, Any]) -> dict[str, Any]:
    return {api: values[ini] for ini, api in _GRAFANA_KEYS}


def _ini_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def grafana_ini(app_url: str, values: dict[str, Any]) -> str:
    lines = [
        "[server]",
        "# Moet exact de URL zijn waarop gebruikers Grafana openen: Grafana bouwt er de redirect URI mee.",
        f"root_url = {app_url}/",
        "",
        "[auth.generic_oauth]",
    ]
    lines += [f"{ini} = {_ini_value(values[ini])}" for ini, _ in _GRAFANA_KEYS]
    return "\n".join(lines) + "\n"


def grafana_env(app_url: str, values: dict[str, Any]) -> str:
    lines = [
        "# Omgevingsvariabelen voor de Grafana-container (bv. in docker-compose of een .env-bestand).",
        f"GF_SERVER_ROOT_URL={app_url}/",
    ]
    for ini, _ in _GRAFANA_KEYS:
        value = _ini_value(values[ini])
        # role_attribute_path bevat spaties en quotes: tussen dubbele quotes zetten.
        if any(c in value for c in " '\"&|()"):
            value = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        lines.append(f"GF_AUTH_GENERIC_OAUTH_{ini.upper()}={value}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- generieke OIDC-app


def oidc_summary(urls: AuthentikUrls, client_id: str, client_secret: str, redirects: list[str]) -> str:
    lines = [
        "# In te vullen in de app (OpenID Connect / OAuth2).",
        f"Issuer:              {urls.issuer}",
        f"Discovery-URL:       {urls.discovery}",
        f"Client ID:           {client_id}",
        f"Client secret:       {client_secret}",
        f"Scopes:              {' '.join(SCOPES)}",
        f"Authorization URL:   {urls.authorize}",
        f"Token URL:           {urls.token}",
        f"Userinfo URL:        {urls.userinfo}",
        f"Uitloggen (end session): {urls.end_session}",
        "Redirect URI('s) die Authentik aanvaardt:",
        *[f"  {u}" for u in redirects],
        "Gebruikersnaam-claim: preferred_username; groepen-claim: groups.",
    ]
    return "\n".join(lines) + "\n"
