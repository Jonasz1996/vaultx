"""Wat VaultX afleidt uit een NPM proxy host: labels, applicatietype, aanmelding, waarschuwingen.

Pure functies zonder database of netwerk, zodat ze los te testen zijn.

Labels
------
NPM kent geen labels. VaultX leest daarom commentaarregels in het veld
*Advanced* van de proxy host of van een custom location, bijvoorbeeld::

    # vaultx.app = Grafana
    # vaultx.auth = oidc
    # vaultx.type = grafana
    # vaultx.tags = monitoring, ops
    # vaultx.description = Dashboards van het team
    # vaultx.ignore = true

nginx negeert commentaar, dus de labels veranderen niets aan de proxyconfig.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.models import AuthMethod

LABEL_RE = re.compile(r"^[ \t]*#[ \t]*vaultx\.([a-z][a-z0-9_.-]*)[ \t]*[:=][ \t]*(.*?)[ \t]*$", re.M | re.I)
# Een actieve auth_request-directive (geen commentaar, niet "off").
AUTH_REQUEST_RE = re.compile(r"^[^#\n]*?\bauth_request[ \t]+(?!off\b)([^;\s]+)", re.M)
AUTHENTIK_OUTPOST = "outpost.goauthentik.io"
# Zelfde test als NPM (backend/internal/nginx.js): een eigen "location /" in Advanced
# vervangt de standaardlocatie, en daarmee ook access lists en websocket-headers.
DEFAULT_LOCATION_RE = re.compile(r"^(?:.*;)?\s*?location\s*?/\s*?\{", re.M | re.I)

KNOWN_LABELS = {"app", "auth", "type", "tags", "description", "ignore"}
AUTH_ALIASES = {
    "forward-auth": AuthMethod.forward_auth.value,
    "forwardauth": AuthMethod.forward_auth.value,
    "authentik": AuthMethod.forward_auth.value,
    "proxy": AuthMethod.forward_auth.value,
    "headers": AuthMethod.header.value,
    "basic": AuthMethod.access_list.value,
    "local": AuthMethod.app.value,
    "open": AuthMethod.none.value,
    "public": AuthMethod.none.value,
}
AUTH_VALUES = {m.value for m in AuthMethod}
TRUE_VALUES = {"1", "true", "yes", "ja", "on"}


@dataclass(frozen=True, slots=True)
class Fingerprint:
    key: str
    name: str
    keywords: tuple[str, ...]
    ports: tuple[int, ...] = ()


# Bekende self-hosted applicaties. Trefwoorden matchen op stukken van de domeinnaam
# en de forward host (containernamen); poorten enkel als ze vrij uniek zijn.
FINGERPRINTS: tuple[Fingerprint, ...] = (
    Fingerprint("authentik", "Authentik", ("authentik", "auth", "sso"), (9000, 9443)),
    Fingerprint("grafana", "Grafana", ("grafana",)),
    Fingerprint("prometheus", "Prometheus", ("prometheus",), (9090,)),
    Fingerprint("alertmanager", "Alertmanager", ("alertmanager",), (9093,)),
    Fingerprint("uptime-kuma", "Uptime Kuma", ("uptime", "kuma"), (3001,)),
    Fingerprint("gitea", "Gitea", ("gitea",)),
    Fingerprint("forgejo", "Forgejo", ("forgejo",)),
    Fingerprint("gitlab", "GitLab", ("gitlab",)),
    Fingerprint("jenkins", "Jenkins", ("jenkins",)),
    Fingerprint("nextcloud", "Nextcloud", ("nextcloud",)),
    Fingerprint("paperless", "Paperless-ngx", ("paperless",)),
    Fingerprint("immich", "Immich", ("immich",), (2283,)),
    Fingerprint("jellyfin", "Jellyfin", ("jellyfin",), (8096,)),
    Fingerprint("plex", "Plex", ("plex",), (32400,)),
    Fingerprint("home-assistant", "Home Assistant", ("homeassistant", "hass", "ha"), (8123,)),
    Fingerprint("node-red", "Node-RED", ("nodered", "node-red"), (1880,)),
    Fingerprint("n8n", "n8n", ("n8n",), (5678,)),
    Fingerprint("portainer", "Portainer", ("portainer",)),
    Fingerprint("proxmox", "Proxmox VE", ("proxmox", "pve"), (8006,)),
    Fingerprint("truenas", "TrueNAS", ("truenas",)),
    Fingerprint("opnsense", "OPNsense", ("opnsense",)),
    Fingerprint("pfsense", "pfSense", ("pfsense",)),
    Fingerprint("unifi", "UniFi Network", ("unifi",), (8443,)),
    Fingerprint("pihole", "Pi-hole", ("pihole", "pi-hole")),
    Fingerprint("adguard", "AdGuard Home", ("adguard",)),
    Fingerprint("nginx-proxy-manager", "Nginx Proxy Manager", ("npm", "nginxproxymanager"), (81,)),
    Fingerprint("vaultwarden", "Vaultwarden", ("vaultwarden", "bitwarden")),
    Fingerprint("vaultx", "VaultX", ("vaultx",)),
    Fingerprint("syncthing", "Syncthing", ("syncthing",), (8384,)),
    Fingerprint("sonarr", "Sonarr", ("sonarr",), (8989,)),
    Fingerprint("radarr", "Radarr", ("radarr",), (7878,)),
    Fingerprint("prowlarr", "Prowlarr", ("prowlarr",), (9696,)),
    Fingerprint("qbittorrent", "qBittorrent", ("qbittorrent",)),
    Fingerprint("bookstack", "BookStack", ("bookstack",)),
    Fingerprint("wikijs", "Wiki.js", ("wikijs",)),
    Fingerprint("mealie", "Mealie", ("mealie",)),
    Fingerprint("frigate", "Frigate", ("frigate",)),
    Fingerprint("minio", "MinIO", ("minio",)),
    Fingerprint("kibana", "Kibana", ("kibana",), (5601,)),
    Fingerprint("keycloak", "Keycloak", ("keycloak",)),
)
FINGERPRINTS_BY_KEY = {f.key: f for f in FINGERPRINTS}


@dataclass(slots=True)
class Detection:
    """Resultaat van de analyse van één proxy host."""

    name: str
    app_type: str | None
    auth_method: str
    forward_auth: bool
    labels: dict[str, str]
    tags: list[str]
    description: str | None
    ignore: bool
    url: str | None
    warnings: list[dict[str, str]] = field(default_factory=list)

    def warn(self, code: str, message: str) -> None:
        self.warnings.append({"code": code, "message": message})


def parse_labels(*configs: str | None) -> dict[str, str]:
    """vaultx.*-labels uit één of meer Advanced-velden. Latere velden winnen niet: de eerste telt."""
    labels: dict[str, str] = {}
    for cfg in configs:
        for key, value in LABEL_RE.findall(cfg or ""):
            labels.setdefault(key.lower(), value.strip())
    return labels


def _tokens(*values: str) -> set[str]:
    out: set[str] = set()
    for v in values:
        parts = [p for p in re.split(r"[^a-z0-9]+", v.lower()) if p]
        out.update(parts)
        # "node-red", "pi-hole": ook de aaneengeschreven vorm
        out.update(a + b for a, b in zip(parts, parts[1:], strict=False))
    return out


def fingerprint(domains: list[str], forward_host: str, forward_port: int) -> Fingerprint | None:
    """Herkent een bekende applicatie aan subdomein, forward host of poort."""
    # Eerst het eerste label van elk domein (grafana.example.be), dan de forward host.
    first_labels = [d.split(".")[0] for d in domains if d and not d.startswith("*")]
    for source in (_tokens(*first_labels), _tokens(forward_host)):
        for f in FINGERPRINTS:
            if any(k.replace("-", "") in source for k in f.keywords):
                return f
    for f in FINGERPRINTS:
        if forward_port in f.ports:
            return f
    return None


def _normalize_auth(value: str) -> str | None:
    v = value.strip().lower().replace(" ", "_")
    v = AUTH_ALIASES.get(v, v)
    return v if v in AUTH_VALUES else None


def _pretty_name(domain: str) -> str:
    first = domain.split(".")[0] if domain else ""
    return first.replace("-", " ").replace("_", " ").title() or domain


def analyze(host: dict[str, Any]) -> Detection:
    """Analyseert één proxy host zoals NPM hem teruggeeft (GET /api/nginx/proxy-hosts)."""
    domains = [d for d in host.get("domain_names") or [] if isinstance(d, str)]
    host_cfg = host.get("advanced_config") or ""
    locations = host.get("locations") or []
    loc_cfgs = [loc.get("advanced_config") or "" for loc in locations if isinstance(loc, dict)]
    labels = parse_labels(host_cfg, *loc_cfgs)

    auth_targets = [m for cfg in (host_cfg, *loc_cfgs) for m in AUTH_REQUEST_RE.findall(cfg)]
    forward_auth = bool(auth_targets)
    access_list = host.get("access_list") if isinstance(host.get("access_list"), dict) else None
    has_access_list = bool(host.get("access_list_id")) or access_list is not None

    fp = None
    if labels.get("type") and labels["type"].lower() in FINGERPRINTS_BY_KEY:
        fp = FINGERPRINTS_BY_KEY[labels["type"].lower()]
    if fp is None:
        fp = fingerprint(domains, str(host.get("forward_host") or ""), int(host.get("forward_port") or 0))

    primary = next((d for d in domains if not d.startswith("*")), domains[0] if domains else "")
    # De productnaam enkel als het subdomein ze ook noemt; "dash.example.be" naar een
    # Grafana-container heet in de catalogus "Dash" (type grafana).
    named_by_domain = fp is not None and fingerprint(domains, "", 0) == fp
    name = labels.get("app") or (fp.name if fp and named_by_domain else _pretty_name(primary))
    app_type = labels.get("type", "").lower() or (fp.key if fp else None)
    has_cert = bool(host.get("certificate_id"))
    url = None
    if primary and not primary.startswith("*"):
        url = f"{'https' if has_cert else 'http'}://{primary}"

    det = Detection(
        name=name[:255],
        app_type=(app_type or None) and app_type[:64],
        auth_method=AuthMethod.unknown.value,
        forward_auth=forward_auth,
        labels=labels,
        tags=[t.strip() for t in labels.get("tags", "").split(",") if t.strip()][:20],
        description=labels.get("description") or None,
        ignore=labels.get("ignore", "").lower() in TRUE_VALUES,
        url=url,
    )

    # Aanmelding: expliciet label > forward auth > access list > onbekend.
    if "auth" in labels:
        normalized = _normalize_auth(labels["auth"])
        if normalized:
            det.auth_method = normalized
        else:
            det.warn("label_invalid", f"Onbekende waarde voor vaultx.auth: '{labels['auth']}'")
    if det.auth_method == AuthMethod.unknown.value:
        if forward_auth:
            det.auth_method = AuthMethod.forward_auth.value
        elif has_access_list:
            det.auth_method = AuthMethod.access_list.value

    for key in labels:
        if key not in KNOWN_LABELS:
            det.warn("label_unknown", f"Onbekend label vaultx.{key} wordt genegeerd")
    if forward_auth and not any(AUTHENTIK_OUTPOST in t for t in auth_targets):
        det.warn("forward_auth_not_authentik", "auth_request wijst niet naar een Authentik-outpost")
    if forward_auth and access_list and access_list.get("satisfy_any"):
        det.warn(
            "satisfy_any",
            f"Access list '{access_list.get('name', '')}' staat op 'Satisfy Any': "
            "een toegelaten IP-adres slaat de Authentik-controle over",
        )
    if DEFAULT_LOCATION_RE.search(host_cfg):
        det.warn(
            "custom_root_location",
            "Advanced bevat een eigen 'location /': NPM laat dan zijn standaardlocatie weg, "
            "inclusief access list en websocket-headers",
        )
    meta = host.get("meta") or {}
    if meta.get("nginx_online") is False:
        det.warn("nginx_offline", "NPM meldt een nginx-fout voor deze host; hij is offline")
    if not has_cert:
        det.warn("no_tls", "Geen TLS-certificaat: verkeer naar deze host is onversleuteld")
    elif not host.get("ssl_forced"):
        det.warn("tls_not_forced", "TLS is niet afgedwongen: HTTP wordt niet doorgestuurd naar HTTPS")
    return det
