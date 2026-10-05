"""App publiceren (fase 6): het plan voor een nieuwe proxy host in NPM.

Pure functies zonder database of netwerk, net als npm_protect.py. De service
(app_publish.py) leest NPM en Authentik, laat hier het plan maken en voert het uit.

Wat VaultX in NPM aanmaakt:

- de host zelf: domein, upstream, certificaat, TLS afdwingen, HSTS en HTTP/2 als
  er een certificaat is, websockets, "Block Common Exploits";
- in *Advanced* van de host: labels voor de catalogus (``# vaultx.app = ...``) en,
  met Authentik, de outpost-location en de aanmeldredirect (npm_protect.host_block);
- een custom location ``/`` met, met Authentik, de ``auth_request``-directives
  (npm_protect.location_block) en daarna de app-instellingen: het CSS-thema
  (``sub_filter``) en de beveiligingsheaders.

Dat volgt het patroon dat veel NPM-gebruikers met de hand in Advanced zetten
(``location / { ... }`` met thema, headers en forward auth), met één verschil:
de ``location /`` is een custom location van NPM in plaats van een eigen blok in
Advanced. Zo genereert NPM zelf proxy_pass, Host-header, websockets, HSTS en
access lists, en blijven die schakelaars in NPM werken.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.npm_protect import Check, plan_protect, sanitize_location

DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$"
)
UPSTREAM_HOST_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
    r"|\d{1,3}(?:\.\d{1,3}){3})$"
)
# Komt letterlijk in een nginx-string tussen enkele aanhalingstekens: geen quotes, spaties of ;.
THEME_URL_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._~%/+-]*)?$")
LABEL_VALUE_RE = re.compile(r"[\r\n#;{}]")
EXPIRY_WARN = timedelta(days=14)

# Zelfde headers als in het gangbare Authentik-NPM-sjabloon. HSTS zet NPM zelf (hsts_enabled),
# ook in custom locations; twee keer dezelfde header zou dubbel naar de browser gaan.
SECURITY_HEADERS = (
    'add_header X-Xss-Protection "1; mode=block" always;',
    'add_header X-Content-Type-Options "nosniff" always;',
    'add_header X-Frame-Options "SAMEORIGIN" always;',
    'add_header Referrer-Policy "no-referrer";',
    "add_header Content-Security-Policy \"frame-ancestors 'self'\";",
)
APP_MARK_BEGIN = "# >>> vaultx:app (thema en headers, ingesteld bij publiceren)"
APP_MARK_END = "# <<< vaultx:app"


@dataclass(slots=True)
class PublishInput:
    name: str
    domain: str
    forward_scheme: str
    forward_host: str
    forward_port: int
    certificate_id: int = 0
    ssl_forced: bool = True
    websockets: bool = True
    block_exploits: bool = True
    security_headers: bool = True
    theme_css_url: str | None = None
    description: str | None = None
    protect: bool = True


@dataclass(slots=True)
class PublishPlan:
    domain: str
    checks: list[Check] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    # Wat VaultX in NPM POST (zonder meta), of {} als het plan geblokkeerd is.
    body: dict[str, Any] = field(default_factory=dict)
    certificate: dict[str, Any] | None = None

    @property
    def blocked(self) -> bool:
        return any(c.level == "block" for c in self.checks)

    def block(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "block", message))

    def warn(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "warn", message))

    def info(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "info", message))


def normalize_domain(domain: str) -> str:
    return domain.strip().rstrip(".").lower()


def theme_url(template: str | None, app_name: str) -> str | None:
    """'https://css.example.be/{app}.css' + 'Proxmox VE' -> 'https://css.example.be/proxmox-ve.css'."""
    if not template:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", app_name.lower()).strip("-") or "app"
    return template.replace("{app}", slug)


def valid_theme_template(template: str) -> bool:
    return bool(THEME_URL_RE.fullmatch(template.replace("{app}", "app")))


def covers(cert_domains: list[str], domain: str) -> bool:
    """Dekt een certificaat met deze namen het domein? Een wildcard dekt één niveau."""
    for name in cert_domains:
        name = str(name).lower()
        if name == domain:
            return True
        if name.startswith("*."):
            suffix = name[1:]  # ".example.be"
            if domain.endswith(suffix) and "." not in domain[: -len(suffix)]:
                return True
    return False


def _parse_expiry(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def domain_in_use(hosts: list[dict[str, Any]], domain: str) -> dict[str, Any] | None:
    for h in hosts:
        if domain in [str(d).lower() for d in h.get("domain_names") or []]:
            return h
    return None


def label_lines(data: PublishInput) -> str:
    def clean(v: str) -> str:
        return LABEL_VALUE_RE.sub(" ", v).strip()[:200]

    lines = ["# Gepubliceerd door VaultX", f"# vaultx.app = {clean(data.name)}"]
    if data.description:
        lines.append(f"# vaultx.description = {clean(data.description)}")
    return "\n".join(lines) + "\n"


def app_block(data: PublishInput) -> str:
    """Thema en beveiligingsheaders voor de location '/' (leeg als geen van beide gevraagd is)."""
    lines: list[str] = []
    if data.theme_css_url:
        link = f'<link rel="stylesheet" type="text/css" href="{data.theme_css_url}">'
        lines += [
            f"sub_filter '</head>' '{link}</head>';",
            "sub_filter_once on;",
            # Anders stuurt de app gzip terug en vindt sub_filter </head> niet.
            'proxy_set_header Accept-Encoding "";',
        ]
    if data.security_headers:
        lines += SECURITY_HEADERS
    if not lines:
        return ""
    return "\n".join([APP_MARK_BEGIN, *lines, APP_MARK_END]) + "\n"


def plan_publish(
    data: PublishInput,
    *,
    existing_hosts: list[dict[str, Any]],
    certificates: list[dict[str, Any]],
    outpost_url: str | None,
    now: datetime | None = None,
) -> PublishPlan:
    domain = normalize_domain(data.domain)
    plan = PublishPlan(domain=domain)
    now = now or datetime.now(UTC)

    if not DOMAIN_RE.fullmatch(domain):
        plan.block("domain_invalid", "Geef een volledige domeinnaam op, bv. app.example.be (geen wildcard).")
    elif (other := domain_in_use(existing_hosts, domain)) is not None:
        plan.block(
            "domain_in_use",
            f"{domain} staat al in NPM (proxy host #{other.get('id')}). Kies een ander domein, of bescherm "
            "die host vanuit de lijst met hosts.",
        )
    if data.forward_scheme not in ("http", "https"):
        plan.block("upstream_scheme", "Het schema van de app moet http of https zijn.")
    if not UPSTREAM_HOST_RE.fullmatch(data.forward_host or ""):
        plan.block("upstream_host", "Geef de app op als hostnaam of IPv4-adres, zonder schema of poort.")
    if not 0 < data.forward_port < 65536:
        plan.block("upstream_port", "Ongeldige poort voor de app.")
    if data.theme_css_url and not THEME_URL_RE.fullmatch(data.theme_css_url):
        plan.block(
            "theme_invalid",
            "Het CSS-thema moet een gewone http(s)-URL zijn, zonder spaties of aanhalingstekens.",
        )
    if not data.name.strip():
        plan.block("name_missing", "Geef de app een naam.")

    cert: dict[str, Any] | None = None
    if data.certificate_id:
        cert = next((c for c in certificates if c.get("id") == data.certificate_id), None)
        if cert is None:
            plan.block("certificate_missing", "Dat certificaat bestaat niet (meer) in NPM.")
        else:
            plan.certificate = {
                k: cert.get(k) for k in ("id", "nice_name", "provider", "domain_names", "expires_on")
            }
            names = [str(d) for d in cert.get("domain_names") or []]
            expires = _parse_expiry(cert.get("expires_on"))
            if DOMAIN_RE.fullmatch(domain) and not covers(names, domain):
                plan.block(
                    "certificate_mismatch",
                    f"Certificaat '{cert.get('nice_name') or cert.get('id')}' geldt voor {', '.join(names)}, "
                    f"niet voor {domain}.",
                )
            if expires and expires <= now:
                plan.block("certificate_expired", f"Het certificaat is verlopen op {cert.get('expires_on')}.")
            elif expires and expires - now < EXPIRY_WARN:
                plan.warn(
                    "certificate_expiring", f"Het certificaat verloopt binnenkort ({cert.get('expires_on')})."
                )
    https = cert is not None

    if not data.protect:
        plan.warn(
            "unprotected",
            "Zonder Authentik komt iedereen die de host bereikt meteen bij de app, tenzij de app zelf een "
            "login heeft.",
        )
    if plan.blocked:
        return plan

    host: dict[str, Any] = {
        "id": 0,
        "domain_names": [domain],
        "forward_scheme": data.forward_scheme,
        "forward_host": data.forward_host,
        "forward_port": data.forward_port,
        "certificate_id": cert["id"] if cert else 0,
        "ssl_forced": https and data.ssl_forced,
        "hsts_enabled": https and data.ssl_forced,
        "hsts_subdomains": False,
        "http2_support": https,
        "block_exploits": data.block_exploits,
        "caching_enabled": False,
        "allow_websocket_upgrade": data.websockets,
        "access_list_id": 0,
        "enabled": True,
        "advanced_config": label_lines(data),
        "locations": [],
        "meta": {},
    }
    plan.steps.append(
        f"NPM: proxy host {domain} aanmaken naar {data.forward_scheme}://{data.forward_host}:"
        f"{data.forward_port}"
        + (
            f", met certificaat '{cert.get('nice_name') or cert['id']}'"
            + (", TLS afgedwongen, HSTS" if data.ssl_forced else "")
            if cert
            else ", zonder certificaat (enkel http)"
        )
        + "."
    )

    extra = app_block(data)
    if data.protect:
        protect = plan_protect(host, outpost_url)
        for c in protect.checks:
            if c.code not in {"already_managed", "app_has_login"}:
                plan.checks.append(c)
        if protect.blocked:
            return plan
        host["advanced_config"] = protect.after["advanced_config"]
        host["locations"] = protect.after["locations"]
        plan.steps.append(
            "NPM: Authentik forward auth in Advanced (outpost-location, aanmeldredirect) en in de custom "
            "location '/' (auth_request en X-authentik-*-headers)."
        )
    elif extra:
        host["locations"] = [
            {
                "path": "/",
                "forward_scheme": data.forward_scheme,
                "forward_host": data.forward_host,
                "forward_port": data.forward_port,
                "advanced_config": "",
            }
        ]
    if extra:
        root = host["locations"][-1]
        root["advanced_config"] = (root.get("advanced_config") or "") + extra
        what = [
            x
            for x, on in (("CSS-thema", data.theme_css_url), ("beveiligingsheaders", data.security_headers))
            if on
        ]
        plan.steps.append(f"NPM: {' en '.join(what)} in de custom location '/'.")
    if not https and not data.protect:
        plan.warn("no_tls", "Zonder certificaat is de app enkel over http bereikbaar.")
    plan.steps.append("VaultX: de app in de catalogus zetten.")
    plan.body = {k: v for k, v in host.items() if k not in ("id", "meta")}
    plan.body["meta"] = {"letsencrypt_agree": False, "dns_challenge": False}
    return plan


def host_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    """Wat er nodig is om een host met de hand opnieuw aan te maken (journaal bij depubliceren)."""
    keep = (
        "domain_names",
        "forward_scheme",
        "forward_host",
        "forward_port",
        "certificate_id",
        "ssl_forced",
        "hsts_enabled",
        "hsts_subdomains",
        "http2_support",
        "block_exploits",
        "caching_enabled",
        "allow_websocket_upgrade",
        "access_list_id",
        "advanced_config",
        "enabled",
    )
    snap = {k: raw.get(k) for k in keep if k in raw}
    locations = raw.get("locations") or []
    snap["locations"] = [sanitize_location(loc) for loc in locations if isinstance(loc, dict)]
    return snap
