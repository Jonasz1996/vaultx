"""Authentik-bescherming op een NPM proxy host zetten of weghalen: het plan.

Pure functies zonder database of netwerk, net als npm_detect.py. De service
(npm_write.py) haalt de host live uit NPM, laat hier een plan maken, voert het
uit en zet bij problemen de oude config terug.

Wat VaultX schrijft volgt het officiële Authentik-patroon voor NPM (Authentik-
docs 2026.8, onderzoek 03 C1):

- in *Advanced* van de host: de outpost-location en ``@goauthentik_proxy_signin``;
- in een **custom location** ``/``: de ``auth_request``-directives.

Zo blijft NPM de location zelf genereren (proxy_pass, access list, websockets),
in plaats van een eigen ``location /`` in Advanced, die de access list zou
uitschakelen. Bestaan er andere custom locations, dan krijgen die dezelfde
directives: nginx past ``auth_request`` per location toe.

Alles wat VaultX toevoegt staat tussen markeringen, zodat VaultX het later
precies kan terugvinden en weghalen zonder de rest van de config te raken::

    # >>> vaultx:authentik (beheerd door VaultX, niet met de hand wijzigen)
    ...
    # <<< vaultx:authentik
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

from app.services.npm_detect import (
    AUTH_REQUEST_RE,
    AUTHENTIK_OUTPOST,
    DEFAULT_LOCATION_RE,
    MANAGED_MARK,
    analyze,
)

MARK_BEGIN = f"{MANAGED_MARK} (beheerd door VaultX, niet met de hand wijzigen)"
MARK_END = "# <<< vaultx:authentik"
# In een custom location "/" die VaultX zelf aanmaakte; bij weghalen verdwijnt die location weer.
CREATED_MARK = "# vaultx:created-location"
BLOCK_RE = re.compile(
    r"^[ \t]*# >>> vaultx:authentik[^\n]*\n.*?^[ \t]*# <<< vaultx:authentik[ \t]*\n?", re.M | re.S
)
OUTPOST_LOCATION_RE = re.compile(r"^[^#\n]*\blocation\s+[=~^ ]*/outpost\.goauthentik\.io\b", re.M)
# Velden die NPM 2.16 accepteert in een location (schema proxy-host-object.json).
LOCATION_FIELDS = (
    "id",
    "path",
    "forward_scheme",
    "forward_host",
    "forward_port",
    "forward_path",
    "advanced_config",
    "access_list_id",
)
# Optionele serverdirectives uit het Authentik-patroon. Staan ze al in Advanced, dan laat
# VaultX ze weg: twee keer dezelfde directive is voor nginx een fout ("is duplicate").
SERVER_TUNING = {
    "proxy_buffers": "proxy_buffers 8 16k;",
    "proxy_buffer_size": "proxy_buffer_size 32k;",
    "port_in_redirect": "port_in_redirect off;",
}
OUTPOST_URL_RE = re.compile(r"^https?://[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(:\d{1,5})?$")

Action = Literal["protect", "unprotect"]


@dataclass(frozen=True, slots=True)
class Check:
    code: str
    level: Literal["block", "warn", "info"]
    message: str


@dataclass(slots=True)
class Plan:
    """Wat VaultX in NPM zou wijzigen, en of dat veilig kan."""

    action: Action
    npm_id: int
    modified_on: str | None
    checks: list[Check] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    # Huidige en nieuwe waarde van de velden die VaultX wijzigt (advanced_config, locations).
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return any(c.level == "block" for c in self.checks)

    @property
    def has_changes(self) -> bool:
        return not self.blocked and self.before != self.after

    def block(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "block", message))

    def warn(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "warn", message))

    def info(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "info", message))


def normalize_outpost_url(url: str) -> str:
    """'http://authentik-server:9000/' -> 'http://authentik-server:9000'; ValueError als ongeldig.

    Bewust streng (geen pad, query, spaties, aanhalingstekens of puntkomma's): de
    waarde komt letterlijk in de nginx-config.
    """
    url = url.strip().rstrip("/")
    if not OUTPOST_URL_RE.fullmatch(url):
        raise ValueError(
            "Outpost-URL moet de vorm http(s)://host[:poort] hebben, zonder pad, bv. http://authentik-server:9000"
        )
    port = urlsplit(url).port
    if port is not None and not 0 < port < 65536:
        raise ValueError("Ongeldige poort in de outpost-URL")
    return url


def has_block(cfg: str | None) -> bool:
    return bool(BLOCK_RE.search(cfg or ""))


def strip_block(cfg: str | None) -> str:
    """Advanced-config zonder het VaultX-blok (de rest blijft byte voor byte gelijk)."""
    return BLOCK_RE.sub("", cfg or "")


def is_managed(host: dict[str, Any]) -> bool:
    """Staat er een door VaultX beheerde Authentik-config op deze host?"""
    return has_block(host.get("advanced_config")) or any(
        has_block(loc.get("advanced_config")) for loc in _locations(host)
    )


def _active(cfg: str) -> list[str]:
    """Regels zonder commentaar, voor zoeken naar directives."""
    return [line.split("#", 1)[0] for line in cfg.splitlines()]


def _has_directive(cfg: str, name: str) -> bool:
    return any(re.match(rf"\s*{re.escape(name)}\s", line) for line in _active(cfg))


def balanced(cfg: str) -> bool:
    """Ruwe syntaxcontrole: accolades in evenwicht, buiten commentaar en aanhalingstekens."""
    depth = 0
    for line in cfg.splitlines():
        quote: str | None = None
        for ch in line:
            if quote:
                if ch == quote:
                    quote = None
                continue
            if ch in "\"'":
                quote = ch
            elif ch == "#":
                break
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    return False
    return depth == 0


def _locations(host: dict[str, Any]) -> list[dict[str, Any]]:
    return [loc for loc in host.get("locations") or [] if isinstance(loc, dict)]


def sanitize_location(loc: dict[str, Any]) -> dict[str, Any]:
    """Enkel de velden die NPM terug aanvaardt (anders 400 door additionalProperties: false)."""
    return {k: loc[k] for k in LOCATION_FIELDS if k in loc and loc[k] is not None}


def _append(cfg: str, block: str) -> str:
    """Blok achteraan toevoegen; de bestaande tekst blijft ongewijzigd (op een slotregel na)."""
    if cfg and not cfg.endswith("\n"):
        cfg += "\n"
    return cfg + block


def host_block(outpost_url: str, existing: str) -> str:
    tuning = [line for name, line in SERVER_TUNING.items() if not _has_directive(existing, name)]
    lines = [
        MARK_BEGIN,
        *tuning,
        "",
        "location /outpost.goauthentik.io {",
        f"    proxy_pass              {outpost_url}/outpost.goauthentik.io;",
        "    proxy_set_header        Host $host;",
        "    proxy_set_header        X-Original-URL $scheme://$http_host$request_uri;",
        "    add_header              Set-Cookie $auth_cookie;",
        "    auth_request_set        $auth_cookie $upstream_http_set_cookie;",
        "    proxy_pass_request_body off;",
        '    proxy_set_header        Content-Length "";',
        "}",
        "",
        "location @goauthentik_proxy_signin {",
        "    internal;",
        "    add_header Set-Cookie $auth_cookie;",
        "    return 302 /outpost.goauthentik.io/start?rd=$scheme://$http_host$request_uri;",
        "}",
        MARK_END,
    ]
    return "\n".join(lines) + "\n"


AUTHENTIK_HEADERS = ("username", "groups", "entitlements", "email", "name", "uid")


def location_block(created: bool = False) -> str:
    lines = [MARK_BEGIN]
    if created:
        lines.append(CREATED_MARK)
    lines += [
        "auth_request     /outpost.goauthentik.io/auth/nginx;",
        "error_page       401 = @goauthentik_proxy_signin;",
        "auth_request_set $auth_cookie $upstream_http_set_cookie;",
        "add_header       Set-Cookie $auth_cookie;",
    ]
    lines += [f"auth_request_set $authentik_{h} $upstream_http_x_authentik_{h};" for h in AUTHENTIK_HEADERS]
    lines += [f"proxy_set_header X-authentik-{h} $authentik_{h};" for h in AUTHENTIK_HEADERS]
    lines.append(MARK_END)
    return "\n".join(lines) + "\n"


def _snapshot(host: dict[str, Any]) -> dict[str, Any]:
    return {
        "advanced_config": host.get("advanced_config") or "",
        "locations": [sanitize_location(loc) for loc in _locations(host)],
    }


def _common_checks(plan: Plan, host: dict[str, Any]) -> None:
    if not host.get("enabled", True):
        plan.block("host_disabled", "De host staat uit in NPM. Zet hem eerst aan.")
    if (host.get("meta") or {}).get("nginx_online") is False:
        plan.block(
            "nginx_offline",
            "NPM meldt al een nginx-fout voor deze host. Los die eerst op in NPM; anders kan VaultX "
            "niet vaststellen of zijn wijziging werkt.",
        )


def plan_protect(host: dict[str, Any], outpost_url: str | None) -> Plan:
    plan = Plan(action="protect", npm_id=int(host["id"]), modified_on=host.get("modified_on"))
    plan.before = _snapshot(host)
    plan.after = _snapshot(host)
    _common_checks(plan, host)
    if not outpost_url:
        plan.block(
            "no_outpost_url",
            "Stel eerst de Authentik-outpost-URL in op de NPM-koppeling (hoe NPM de outpost bereikt).",
        )
        return plan
    if is_managed(host):
        plan.block("already_managed", "VaultX heeft deze host al met Authentik beschermd.")
        return plan

    host_cfg = host.get("advanced_config") or ""
    locations = _locations(host)
    auth_targets = [
        m
        for cfg in (host_cfg, *(loc.get("advanced_config") or "" for loc in locations))
        for m in AUTH_REQUEST_RE.findall(cfg)
    ]
    if any(AUTHENTIK_OUTPOST in t for t in auth_targets):
        plan.block(
            "already_protected",
            "Deze host is al met de hand met Authentik beschermd. VaultX laat die config ongemoeid.",
        )
    elif auth_targets:
        plan.block(
            "foreign_auth_request",
            "Er staat al een auth_request naar een andere dienst. nginx staat er maar één per location toe.",
        )
    if OUTPOST_LOCATION_RE.search(host_cfg):
        plan.block(
            "outpost_location_exists",
            "Advanced bevat al een 'location /outpost.goauthentik.io'. Haal die eerst weg in NPM.",
        )
    if DEFAULT_LOCATION_RE.search(host_cfg):
        plan.block(
            "custom_root_location",
            "Advanced bevat een eigen 'location /'. Daarnaast kan VaultX geen custom location '/' zetten; "
            "verplaats die config eerst naar een custom location in NPM.",
        )
    access_list = host.get("access_list") if isinstance(host.get("access_list"), dict) else None
    if access_list and access_list.get("satisfy_any"):
        plan.block(
            "satisfy_any",
            f"Access list '{access_list.get('name', '')}' staat op 'Satisfy Any': een toegelaten IP-adres "
            "zou de Authentik-controle overslaan. Zet 'Satisfy Any' uit of koppel de access list los.",
        )
    if not all(balanced(c) for c in (host_cfg, *(loc.get("advanced_config") or "" for loc in locations))):
        plan.block("syntax", "De bestaande config heeft onevenwichtige accolades; VaultX past ze niet aan.")
    if any(not isinstance(loc.get("path"), str) for loc in locations):
        plan.block("location_invalid", "Een custom location heeft geen pad; VaultX past deze host niet aan.")
    if plan.blocked:
        return plan

    if not host.get("certificate_id"):
        plan.warn(
            "no_tls",
            "De host heeft geen TLS-certificaat. De Authentik-sessiecookie gaat dan onversleuteld over het "
            "netwerk, en Authentik zet ze standaard enkel over HTTPS.",
        )
    elif not host.get("ssl_forced"):
        plan.warn(
            "tls_not_forced", "TLS is niet afgedwongen: wie via http binnenkomt, krijgt geen sessiecookie."
        )
    if host.get("block_exploits") and not (host.get("certificate_id") and host.get("ssl_forced")):
        # block-exploits.conf van NPM weigert een query string met "=http://" (403). De aanmeldredirect
        # van Authentik draagt ?rd=http://... zodra iemand de host over http opent.
        plan.warn(
            "block_exploits_http",
            "'Block Common Exploits' staat aan en de host is over http bereikbaar. NPM weigert dan de "
            "aanmeldredirect van Authentik (?rd=http://…) met 403. Dwing TLS af, of zet die optie uit.",
        )
    own_login = analyze(host).auth_method
    if own_login in {"oidc", "saml"}:
        plan.warn(
            "app_has_login",
            f"De app meldt zelf al aan via {own_login.upper()}. Met forward auth ervoor gaat een gebruiker "
            "eerst langs Authentik en daarna nog eens langs de app.",
        )

    new_host_cfg = _append(host_cfg, host_block(outpost_url, host_cfg))
    plan.after["advanced_config"] = new_host_cfg
    plan.steps.append("Advanced van de host: outpost-location en aanmeldredirect toevoegen.")
    skipped = [n for n in SERVER_TUNING if _has_directive(host_cfg, n)]
    if skipped:
        plan.info("tuning_kept", f"Bestaande {', '.join(skipped)} in Advanced blijven staan.")

    new_locations: list[dict[str, Any]] = []
    has_root = False
    for loc in locations:
        loc = sanitize_location(loc)
        has_root = has_root or loc.get("path") == "/"
        # Vooraan, zodat bestaande directives van de location erna blijven gelden.
        loc["advanced_config"] = location_block() + (loc.get("advanced_config") or "")
        new_locations.append(loc)
        plan.steps.append(f"Custom location '{loc['path']}': auth_request naar de outpost toevoegen.")
    if not has_root:
        root = {
            "path": "/",
            "forward_scheme": host.get("forward_scheme") or "http",
            "forward_host": host.get("forward_host"),
            "forward_port": host.get("forward_port"),
            "advanced_config": location_block(created=True),
        }
        new_locations.append(root)
        plan.steps.append(
            "Custom location '/' aanmaken naar "
            f"{root['forward_scheme']}://{root['forward_host']}:{root['forward_port']}, met auth_request. "
            "NPM neemt daarin zelf de access list en websocket-instellingen van de host over."
        )
    plan.after["locations"] = new_locations
    _check_syntax(plan)
    return plan


def plan_unprotect(host: dict[str, Any]) -> Plan:
    plan = Plan(action="unprotect", npm_id=int(host["id"]), modified_on=host.get("modified_on"))
    plan.before = _snapshot(host)
    plan.after = _snapshot(host)
    _common_checks(plan, host)
    if not is_managed(host):
        plan.block("not_managed", "Op deze host staat geen Authentik-config van VaultX.")
    if plan.blocked:
        return plan

    host_cfg = host.get("advanced_config") or ""
    if has_block(host_cfg):
        plan.after["advanced_config"] = strip_block(host_cfg)
        plan.steps.append("Advanced van de host: VaultX-blok (outpost-location en redirect) weghalen.")
    new_locations: list[dict[str, Any]] = []
    for loc in _locations(host):
        loc = sanitize_location(loc)
        cfg = loc.get("advanced_config") or ""
        if CREATED_MARK in cfg and not strip_block(cfg).strip():
            plan.steps.append(f"Custom location '{loc.get('path')}' die VaultX aanmaakte, weghalen.")
            continue
        if has_block(cfg):
            loc["advanced_config"] = strip_block(cfg)
            plan.steps.append(f"Custom location '{loc.get('path')}': auth_request van VaultX weghalen.")
        new_locations.append(loc)
    plan.after["locations"] = new_locations
    plan.warn(
        "unprotected_after",
        "Na het weghalen is de host niet meer via Authentik beschermd; iedereen die hem bereikt, komt binnen "
        "tenzij de applicatie zelf een login heeft.",
    )
    _check_syntax(plan)
    return plan


def _check_syntax(plan: Plan) -> None:
    configs = [plan.after.get("advanced_config") or ""] + [
        loc.get("advanced_config") or "" for loc in plan.after.get("locations") or []
    ]
    if not all(balanced(c) for c in configs):
        plan.block("syntax", "De nieuwe config heeft onevenwichtige accolades; VaultX past ze niet toe.")
        plan.after = copy.deepcopy(plan.before)


def make_plan(action: Action, host: dict[str, Any], outpost_url: str | None) -> Plan:
    return plan_protect(host, outpost_url) if action == "protect" else plan_unprotect(host)


def applied(plan: Plan, host: dict[str, Any]) -> bool:
    """Staat in NPM nu wat het plan wou (na de PUT opnieuw gelezen)?"""
    return _snapshot(host) == {
        "advanced_config": plan.after["advanced_config"],
        "locations": [sanitize_location(loc) for loc in plan.after["locations"]],
    }
