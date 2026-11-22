"""NPM volledig beheren: het plan voor één wijziging aan één NPM-object.

Pure functies zonder database of netwerk, net als npm_protect.py. De service
(npm_manage.py) leest de objecten live uit NPM, laat hier een plan maken,
toont dat als voorbeeld en voert het daarna uit met controle en rollback.

Objecttypes: proxy hosts, redirection hosts, 404-hosts, streams, access lists
en certificaten. Acties: aanmaken, bewerken, aan- of uitzetten, verwijderen,
en voor certificaten ook vernieuwen (Let's Encrypt) en een nieuw bestand
opladen (eigen certificaat).

Wat nooit stuk mag (vraag van Jonas): de Advanced-config van zijn hosts, met
het CSS-thema (sub_filter), security headers en Authentik forward auth.
Daarom:

- blokken die VaultX beheert (``# >>> vaultx:<naam>`` tot ``# <<< vaultx:<naam>``)
  moeten byte voor byte blijven staan;
- verdwijnen er regels met ``auth_request``, de Authentik-outpost,
  ``sub_filter`` of ``add_header``, dan weigert VaultX, tenzij je dat
  uitdrukkelijk bevestigt;
- accolades moeten in evenwicht zijn.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from app.services.npm_detect import DEFAULT_LOCATION_RE
from app.services.npm_protect import Check, balanced, sanitize_location

Kind = Literal["proxy_host", "redirection_host", "dead_host", "stream", "access_list", "certificate"]
ManageAction = Literal["create", "update", "delete", "enable", "disable", "renew", "upload"]

REDACTED = "••••••"
MAX_DOMAINS = 100
DOMAIN_RE = re.compile(r"^(\*\.)?[A-Za-z0-9]([A-Za-z0-9-]{0,62})(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}))*$")
# Poorten die NPM zelf gebruikt; een stream erop maakt NPM onbereikbaar.
RESERVED_PORTS = {80, 81, 443}


@dataclass(frozen=True, slots=True)
class KindInfo:
    key: Kind
    path: str  # pad in de NPM-API onder /nginx/
    label: str  # enkelvoud, voor berichten
    fields: tuple[str, ...]  # velden die VaultX mag schrijven
    toggle: bool = False  # heeft /enable en /disable
    domains: bool = False  # heeft domain_names en kan een domein bezetten
    expand: str | None = None


PROXY_FIELDS = (
    "domain_names",
    "forward_scheme",
    "forward_host",
    "forward_port",
    "certificate_id",
    "ssl_forced",
    "hsts_enabled",
    "hsts_subdomains",
    "trust_forwarded_proto",
    "http2_support",
    "block_exploits",
    "caching_enabled",
    "allow_websocket_upgrade",
    "access_list_id",
    "advanced_config",
    "locations",
)
REDIRECTION_FIELDS = (
    "domain_names",
    "forward_scheme",
    "forward_http_code",
    "forward_domain_name",
    "preserve_path",
    "certificate_id",
    "ssl_forced",
    "hsts_enabled",
    "hsts_subdomains",
    "http2_support",
    "block_exploits",
    "advanced_config",
)
DEAD_FIELDS = (
    "domain_names",
    "certificate_id",
    "ssl_forced",
    "hsts_enabled",
    "hsts_subdomains",
    "http2_support",
    "advanced_config",
)
STREAM_FIELDS = (
    "incoming_port",
    "forwarding_host",
    "forwarding_port",
    "tcp_forwarding",
    "udp_forwarding",
    "certificate_id",
)
ACCESS_LIST_FIELDS = ("name", "satisfy_any", "pass_auth", "items", "clients")
CERTIFICATE_FIELDS = ("provider", "nice_name", "domain_names", "meta")

KINDS: dict[str, KindInfo] = {
    "proxy_host": KindInfo(
        "proxy_host", "proxy-hosts", "proxy host", PROXY_FIELDS, toggle=True, domains=True
    ),
    "redirection_host": KindInfo(
        "redirection_host",
        "redirection-hosts",
        "redirection host",
        REDIRECTION_FIELDS,
        toggle=True,
        domains=True,
    ),
    "dead_host": KindInfo("dead_host", "dead-hosts", "404-host", DEAD_FIELDS, toggle=True, domains=True),
    "stream": KindInfo("stream", "streams", "stream", STREAM_FIELDS, toggle=True),
    "access_list": KindInfo(
        "access_list", "access-lists", "access list", ACCESS_LIST_FIELDS, expand="items,clients"
    ),
    "certificate": KindInfo("certificate", "certificates", "certificaat", CERTIFICATE_FIELDS),
}
HOST_KINDS = ("proxy_host", "redirection_host", "dead_host")
ACTIONS_BY_KIND: dict[str, set[str]] = {
    "proxy_host": {"create", "update", "delete", "enable", "disable"},
    "redirection_host": {"create", "update", "delete", "enable", "disable"},
    "dead_host": {"create", "update", "delete", "enable", "disable"},
    "stream": {"create", "update", "delete", "enable", "disable"},
    "access_list": {"create", "update", "delete"},
    "certificate": {"create", "delete", "renew", "upload"},
}

# Regels in de Advanced-config die niet zomaar mogen verdwijnen.
PROTECTED_DIRECTIVES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("auth_request", "Authentik forward auth (auth_request)", re.compile(r"^\s*auth_request\s", re.M)),
    ("outpost", "de Authentik-outpost (outpost.goauthentik.io)", re.compile(r"outpost\.goauthentik\.io")),
    ("sub_filter", "het CSS-thema (sub_filter)", re.compile(r"^\s*sub_filter\s", re.M)),
    ("add_header", "security headers (add_header)", re.compile(r"^\s*(add_header|more_set_headers)\s", re.M)),
)
MANAGED_BLOCK_RE = re.compile(
    r"^[ \t]*# >>> vaultx:([\w.-]+)[^\n]*\n.*?^[ \t]*# <<< vaultx:\1[ \t]*$", re.M | re.S
)


# ---------------------------------------------------------------- inventaris


@dataclass(slots=True)
class Inventory:
    """Alles wat in NPM staat (zoals NPM het geeft, certificaten zonder meta), plus wat VaultX weet."""

    objects: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # npm_id's van proxy hosts die VaultX publiceerde (fase 6) of beschermde met Authentik (fase 3/4).
    published: set[int] = field(default_factory=set)
    protected: set[int] = field(default_factory=set)

    def of(self, kind: str) -> list[dict[str, Any]]:
        return self.objects.get(kind) or []

    def find(self, kind: str, npm_id: int | None) -> dict[str, Any] | None:
        if npm_id is None:
            return None
        return next((o for o in self.of(kind) if o.get("id") == npm_id), None)

    def certificate_users(self, cert_id: int) -> list[str]:
        users = []
        for kind in (*HOST_KINDS, "stream"):
            for o in self.of(kind):
                if o.get("certificate_id") == cert_id:
                    users.append(f"{KINDS[kind].label} {label_of(kind, o)}")
        return users

    def access_list_users(self, list_id: int) -> list[dict[str, Any]]:
        users = []
        for h in self.of("proxy_host"):
            if h.get("access_list_id") == list_id or any(
                (loc or {}).get("access_list_id") == list_id for loc in h.get("locations") or []
            ):
                users.append(h)
        return users


def label_of(kind: str, obj: dict[str, Any]) -> str:
    if kind in HOST_KINDS:
        domains = obj.get("domain_names") or []
        return ", ".join(domains) if domains else f"#{obj.get('id')}"
    if kind == "stream":
        return f":{obj.get('incoming_port')} → {obj.get('forwarding_host')}:{obj.get('forwarding_port')}"
    if kind == "access_list":
        return str(obj.get("name") or f"#{obj.get('id')}")
    if kind == "certificate":
        name = obj.get("nice_name") or ", ".join(obj.get("domain_names") or [])
        return str(name or f"#{obj.get('id')}")
    return f"#{obj.get('id')}"


# ---------------------------------------------------------------- momentopnames


def snapshot(kind: str, obj: dict[str, Any]) -> dict[str, Any]:
    """De schrijfbare velden van een object, in een vorm die NPM terug aanvaardt."""
    info = KINDS[kind]
    snap: dict[str, Any] = {}
    for f in info.fields:
        if kind == "certificate" and f == "meta":
            continue
        if f not in obj:
            continue
        value = copy.deepcopy(obj[f])
        if f == "locations":
            value = [sanitize_location(loc) for loc in value or [] if isinstance(loc, dict)]
        elif f == "items":
            value = [
                {"username": i.get("username", ""), "password": ""}
                for i in value or []
                if isinstance(i, dict)
            ]
        elif f == "clients":
            value = [
                {"address": c.get("address", ""), "directive": c.get("directive", "allow")}
                for c in value or []
                if isinstance(c, dict)
            ]
        elif f == "advanced_config":
            value = value or ""
        elif f == "certificate_id":
            value = value or 0
        snap[f] = value
    return snap


def redact(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """Kopie zonder wachtwoorden, sleutels of DNS-gegevens (voor journaal, auditlog en voorbeeld)."""
    out = copy.deepcopy(data)
    if kind == "access_list" and isinstance(out.get("items"), list):
        for item in out["items"]:
            if isinstance(item, dict) and item.get("password"):
                item["password"] = REDACTED
    if kind == "certificate":
        meta = out.get("meta")
        if isinstance(meta, dict) and meta.get("dns_provider_credentials"):
            meta["dns_provider_credentials"] = REDACTED
        for k in ("certificate", "certificate_key", "intermediate_certificate"):
            if out.get(k):
                out[k] = REDACTED
    return out


# ---------------------------------------------------------------- het plan


@dataclass(slots=True)
class ManagePlan:
    kind: str
    action: str
    npm_id: int | None
    label: str
    modified_on: str | None = None
    checks: list[Check] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    before: dict[str, Any] = field(default_factory=dict)  # volledige momentopname (leeg bij aanmaken)
    after: dict[str, Any] = field(default_factory=dict)  # zoals het object na de wijziging hoort te zijn
    body: dict[str, Any] = field(default_factory=dict)  # wat naar NPM gaat (bij bewerken enkel wat wijzigt)
    changes: list[dict[str, Any]] = field(default_factory=list)
    # Wat de controle van buitenaf hoort te zien; None = niet van buitenaf te controleren.
    expect: str | None = None

    @property
    def blocked(self) -> bool:
        return any(c.level == "block" for c in self.checks)

    @property
    def can_apply(self) -> bool:
        return not self.blocked

    def block(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "block", message))

    def warn(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "warn", message))

    def info(self, code: str, message: str) -> None:
        self.checks.append(Check(code, "info", message))


def _diff(kind: str, before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    rb, ra = redact(kind, before), redact(kind, after)
    out = []
    for f in KINDS[kind].fields:
        changed = f in after and before.get(f) != after.get(f)
        if changed or (kind == "access_list" and f == "items" and _new_passwords(after)):
            out.append({"field": f, "before": rb.get(f), "after": ra.get(f)})
    return out


def _new_passwords(data: dict[str, Any]) -> bool:
    return any(isinstance(i, dict) and i.get("password") for i in data.get("items") or [])


def make_plan(
    kind: str,
    action: str,
    inv: Inventory,
    *,
    npm_id: int | None = None,
    data: dict[str, Any] | None = None,
    confirm_removals: bool = False,
) -> ManagePlan:
    info = KINDS[kind]
    data = copy.deepcopy(data or {})
    current = inv.find(kind, npm_id) if action != "create" else None
    plan = ManagePlan(
        kind=kind,
        action=action,
        npm_id=npm_id if action != "create" else None,
        label=label_of(kind, current) if current else label_of(kind, data) if data else info.label,
    )
    if action not in ACTIONS_BY_KIND[kind]:
        plan.block("action_unsupported", f"Deze actie kan niet op een {info.label}.")
        return plan
    if action != "create" and current is None:
        plan.block("not_found", f"Deze {info.label} staat niet (meer) in NPM.")
        return plan
    if current is not None:
        plan.modified_on = current.get("modified_on")
        plan.before = snapshot(kind, current)

    if action == "create":
        _plan_create(plan, inv, data, confirm_removals)
    elif action == "update":
        assert current is not None
        _plan_update(plan, inv, current, data, confirm_removals)
    elif action == "delete":
        assert current is not None
        _plan_delete(plan, inv, current)
    elif action in ("enable", "disable"):
        assert current is not None
        _plan_toggle(plan, inv, current)
    elif action == "renew":
        assert current is not None
        if current.get("provider") != "letsencrypt":
            plan.block("not_letsencrypt", "Enkel Let's Encrypt-certificaten kan VaultX laten vernieuwen.")
        plan.steps.append(f"Certificaat {plan.label} laten vernieuwen bij Let's Encrypt via NPM")
    elif action == "upload":
        assert current is not None
        if current.get("provider") != "other":
            plan.block("not_custom", "Een nieuw bestand opladen kan enkel bij een eigen certificaat.")
        _check_pem(plan, data)
        plan.steps.append(f"Nieuw certificaatbestand opladen voor {plan.label}")
        users = inv.certificate_users(int(current["id"]))
        if users:
            plan.info("cert_in_use", f"NPM herlaadt nginx voor: {', '.join(users)}.")
    return plan


# ---------------------------------------------------------------- aanmaken / bewerken


DEFAULTS: dict[str, dict[str, Any]] = {
    "proxy_host": {
        "forward_scheme": "http",
        "certificate_id": 0,
        "ssl_forced": False,
        "hsts_enabled": False,
        "hsts_subdomains": False,
        "http2_support": False,
        "block_exploits": True,
        "caching_enabled": False,
        "allow_websocket_upgrade": True,
        "access_list_id": 0,
        "advanced_config": "",
        "locations": [],
    },
    "redirection_host": {
        "forward_scheme": "auto",
        "forward_http_code": 301,
        "preserve_path": True,
        "certificate_id": 0,
        "ssl_forced": False,
        "hsts_enabled": False,
        "hsts_subdomains": False,
        "http2_support": False,
        "block_exploits": True,
        "advanced_config": "",
    },
    "dead_host": {
        "certificate_id": 0,
        "ssl_forced": False,
        "hsts_enabled": False,
        "hsts_subdomains": False,
        "http2_support": False,
        "advanced_config": "",
    },
    "stream": {"tcp_forwarding": True, "udp_forwarding": False, "certificate_id": 0},
    "access_list": {"satisfy_any": False, "pass_auth": False, "items": [], "clients": []},
}
REQUIRED: dict[str, tuple[str, ...]] = {
    "proxy_host": ("domain_names", "forward_host", "forward_port"),
    "redirection_host": ("domain_names", "forward_domain_name"),
    "dead_host": ("domain_names",),
    "stream": ("incoming_port", "forwarding_host", "forwarding_port"),
    "access_list": ("name",),
    "certificate": ("provider",),
}
FIELD_LABELS = {
    "domain_names": "domeinnamen",
    "forward_host": "doelhost",
    "forward_port": "doelpoort",
    "forward_domain_name": "doeldomein",
    "incoming_port": "inkomende poort",
    "forwarding_host": "doelhost",
    "forwarding_port": "doelpoort",
    "name": "naam",
    "provider": "soort certificaat",
}


def _plan_create(plan: ManagePlan, inv: Inventory, data: dict[str, Any], confirm: bool) -> None:
    kind = plan.kind
    info = KINDS[kind]
    missing = [FIELD_LABELS.get(f, f) for f in REQUIRED[kind] if data.get(f) in (None, "", [])]
    if missing:
        plan.block("missing", f"Vul nog in: {', '.join(missing)}.")
    if kind == "certificate":
        _plan_certificate_create(plan, inv, data)
        return
    after = {**DEFAULTS.get(kind, {}), **{k: v for k, v in data.items() if k in info.fields}}
    plan.after = after
    plan.body = copy.deepcopy(after)
    plan.changes = _diff(kind, {}, after)
    plan.label = label_of(kind, after)
    _check_object(plan, inv, None, after, confirm)
    plan.steps.append(f"{info.label.capitalize()} {plan.label} aanmaken in NPM")
    if kind in HOST_KINDS or kind == "stream":
        plan.steps.append(
            "Nagaan dat nginx in NPM de nieuwe config aanvaardt; anders meteen weer verwijderen"
        )
    _set_expect(plan, after)


def _plan_update(
    plan: ManagePlan, inv: Inventory, current: dict[str, Any], data: dict[str, Any], confirm: bool
) -> None:
    kind = plan.kind
    info = KINDS[kind]
    before = plan.before
    after = copy.deepcopy(before)
    for k, v in data.items():
        if k in info.fields:
            after[k] = v
    plan.after = after
    plan.body = {k: after[k] for k in info.fields if k in after and after.get(k) != before.get(k)}
    if kind == "access_list" and _new_passwords(after) and "items" not in plan.body:
        plan.body["items"] = after["items"]
    plan.changes = _diff(kind, before, after)
    if not plan.body:
        plan.block("no_changes", "Er is niets gewijzigd.")
        return
    if kind == "access_list" and "items" in plan.body:
        # NPM vervangt de hele lijst: altijd alle gebruikers meesturen, lege wachtwoorden = ongewijzigd.
        plan.body["items"] = after.get("items") or []
        plan.body.setdefault("clients", after.get("clients") or [])
    if kind == "access_list" and "clients" in plan.body:
        plan.body.setdefault("items", after.get("items") or [])
    _check_object(plan, inv, current, after, confirm)
    if kind == "proxy_host" and "domain_names" in plan.body and int(current["id"]) in inv.protected:
        plan.warn(
            "protected_domain_change",
            "VaultX zette Authentik-bescherming op deze host met de oude domeinnaam. Haal de bescherming weg "
            "en zet ze opnieuw, zodat de Authentik-provider de nieuwe domeinnaam kent.",
        )
    names = ", ".join(c["field"] for c in plan.changes)
    plan.steps.append(f"{info.label.capitalize()} {plan.label} bijwerken in NPM ({names})")
    if kind in HOST_KINDS or kind == "stream":
        plan.steps.append("Nagaan dat nginx in NPM de config aanvaardt; anders de vorige config terugzetten")
    if kind == "access_list":
        users = inv.access_list_users(int(current["id"]))
        if users:
            plan.steps.append(
                f"Nagaan dat de {len(users)} host(s) met deze access list online blijven; anders terugzetten"
            )
    _set_expect(plan, after)


def _plan_delete(plan: ManagePlan, inv: Inventory, current: dict[str, Any]) -> None:
    kind = plan.kind
    npm_id = int(current["id"])
    if kind == "proxy_host":
        if npm_id in inv.published:
            plan.block(
                "published_by_vaultx",
                "VaultX publiceerde deze host als app. Gebruik 'Depubliceren' bij de app, zodat VaultX ook "
                "Authentik en de catalogus opruimt.",
            )
        elif npm_id in inv.protected:
            plan.block(
                "protected_by_vaultx",
                "VaultX zette Authentik-bescherming op deze host. Haal die eerst weg op de host, zodat "
                "VaultX ook de provider en applicatie in Authentik opruimt.",
            )
    if kind == "certificate":
        users = inv.certificate_users(npm_id)
        if users:
            plan.block(
                "certificate_in_use",
                f"Dit certificaat is nog in gebruik door {', '.join(users)}. NPM zou het toch verwijderen "
                "en die hosts naar een onbestaand bestand laten wijzen. Kies eerst een ander certificaat.",
            )
    if kind == "access_list":
        users = inv.access_list_users(npm_id)
        if users:
            names = ", ".join(label_of("proxy_host", h) for h in users)
            plan.block(
                "access_list_in_use",
                f"Deze access list beschermt nog {names}. NPM zou die hosts bij het verwijderen stil "
                "openzetten voor iedereen. Kies eerst een andere access list op die hosts.",
            )
    plan.steps.append(f"{KINDS[kind].label.capitalize()} {plan.label} verwijderen uit NPM")
    if kind != "certificate":
        plan.info(
            "restore_possible",
            "Het journaal bewaart de volledige config, zodat je het object later opnieuw kunt aanmaken"
            + (" (wachtwoorden van gebruikers niet)." if kind == "access_list" else "."),
        )


def _plan_toggle(plan: ManagePlan, inv: Inventory, current: dict[str, Any]) -> None:
    enable = plan.action == "enable"
    if bool(current.get("enabled", True)) == enable:
        plan.block("no_changes", "Staat al aan." if enable else "Staat al uit.")
        return
    word = "aanzetten" if enable else "uitzetten"
    plan.steps.append(f"{KINDS[plan.kind].label.capitalize()} {plan.label} {word} in NPM")
    if enable:
        plan.steps.append("Nagaan dat nginx in NPM de config aanvaardt; anders weer uitzetten")
        _set_expect(plan, plan.before)
    elif plan.kind == "proxy_host" and int(current["id"]) in inv.protected:
        plan.info(
            "protected_disabled", "Uitgezet blijft de Authentik-config staan; aanzetten herstelt alles."
        )


def _set_expect(plan: ManagePlan, after: dict[str, Any]) -> None:
    if plan.kind == "proxy_host":
        plan.expect = "geen nginx-fout (geen 5xx en bereikbaar)"
    elif plan.kind == "redirection_host":
        plan.expect = f"{after.get('forward_http_code', 301)} naar {after.get('forward_domain_name')}"
    elif plan.kind == "dead_host":
        plan.expect = "404"


# ---------------------------------------------------------------- controles per object


def _check_object(
    plan: ManagePlan, inv: Inventory, current: dict[str, Any] | None, after: dict[str, Any], confirm: bool
) -> None:
    kind = plan.kind
    own_id = int(current["id"]) if current else None
    if kind in HOST_KINDS:
        _check_domains(plan, inv, own_id, after.get("domain_names") or [])
        _check_certificate_ref(plan, inv, after)
        before_cfg = (current and plan.before.get("advanced_config")) or ""
        guard_config(plan, before_cfg, after.get("advanced_config") or "", "Advanced", confirm)
        if kind == "proxy_host":
            _check_proxy(plan, inv, after, confirm)
            if not DEFAULT_LOCATION_RE.search(before_cfg) and DEFAULT_LOCATION_RE.search(
                after.get("advanced_config") or ""
            ):
                plan.warn(
                    "own_location_root",
                    "Een eigen 'location /' in Advanced vervangt de location die NPM maakt: de access list, "
                    "websockets en cache-instelling van NPM werken dan niet meer voor '/'.",
                )
        if kind == "redirection_host":
            target = str(after.get("forward_domain_name") or "")
            if target and target in (after.get("domain_names") or []):
                plan.block("redirect_loop", "De redirect wijst naar zichzelf.")
            code = after.get("forward_http_code")
            if code is not None and not 300 <= int(code) <= 308:
                plan.block("bad_code", "De HTTP-code moet tussen 300 en 308 liggen.")
    elif kind == "stream":
        _check_stream(plan, inv, own_id, after)
    elif kind == "access_list":
        _check_access_list(plan, inv, current, after)


def _check_domains(plan: ManagePlan, inv: Inventory, own_id: int | None, domains: list[str]) -> None:
    if not domains:
        return
    if len(domains) > MAX_DOMAINS:
        plan.block("too_many_domains", f"Maximaal {MAX_DOMAINS} domeinnamen per host.")
    bad = [d for d in domains if not isinstance(d, str) or not DOMAIN_RE.fullmatch(d)]
    if bad:
        plan.block("bad_domain", f"Ongeldige domeinnaam: {', '.join(map(str, bad))}.")
    if len({d.lower() for d in domains if isinstance(d, str)}) != len(domains):
        plan.block("duplicate_domain", "Een domeinnaam staat er twee keer in.")
    wanted = {d.lower() for d in domains if isinstance(d, str)}
    for kind in HOST_KINDS:
        for o in inv.of(kind):
            if kind == plan.kind and o.get("id") == own_id:
                continue
            taken = wanted & {str(d).lower() for d in o.get("domain_names") or []}
            if taken:
                plan.block(
                    "domain_in_use",
                    f"{', '.join(sorted(taken))} is al in gebruik door "
                    f"{KINDS[kind].label} {label_of(kind, o)}.",
                )


def _check_certificate_ref(plan: ManagePlan, inv: Inventory, after: dict[str, Any]) -> None:
    cert_id = after.get("certificate_id") or 0
    if not cert_id:
        if after.get("ssl_forced") or after.get("hsts_enabled") or after.get("http2_support"):
            plan.warn(
                "ssl_without_certificate",
                "Force SSL, HSTS en HTTP/2 doen niets zonder certificaat; NPM negeert ze dan.",
            )
        return
    cert = inv.find("certificate", int(cert_id))
    if cert is None:
        plan.block("certificate_missing", f"Certificaat #{cert_id} bestaat niet in NPM.")
        return
    names = [str(n).lower() for n in cert.get("domain_names") or []]
    uncovered = [d for d in after.get("domain_names") or [] if not _covered(str(d).lower(), names)]
    if uncovered and names:
        plan.warn(
            "certificate_mismatch",
            f"Het certificaat {label_of('certificate', cert)} dekt {', '.join(uncovered)} niet; browsers "
            "geven daar een certificaatfout.",
        )
    if after.get("hsts_enabled") and not after.get("ssl_forced"):
        plan.warn("hsts_without_force", "HSTS zonder Force SSL: bezoekers op http krijgen geen HSTS.")


def _covered(domain: str, names: list[str]) -> bool:
    for n in names:
        if n == domain:
            return True
        if n.startswith("*.") and domain.count(".") == n.count(".") and domain.endswith(n[1:]):
            return True
    return False


def _check_proxy(plan: ManagePlan, inv: Inventory, after: dict[str, Any], confirm: bool) -> None:
    port = after.get("forward_port")
    if port is not None and not 1 <= int(port) <= 65535:
        plan.block("bad_port", "De doelpoort moet tussen 1 en 65535 liggen.")
    list_id = after.get("access_list_id") or 0
    if list_id and inv.find("access_list", int(list_id)) is None:
        plan.block("access_list_missing", f"Access list #{list_id} bestaat niet in NPM.")
    if list_id and DEFAULT_LOCATION_RE.search(after.get("advanced_config") or ""):
        plan.warn(
            "access_list_ignored",
            "De Advanced-config heeft een eigen 'location /': nginx gebruikt die in plaats van de "
            "location van NPM, dus de access list geldt niet voor '/'. Zet de regels in een custom "
            "location '/' als de access list moet werken.",
        )
    before_locs = {loc.get("path"): loc for loc in plan.before.get("locations") or []}
    paths: set[str] = set()
    for loc in after.get("locations") or []:
        path = loc.get("path")
        if path in paths:
            plan.block("duplicate_location", f"Custom location '{path}' staat er twee keer in.")
        paths.add(path)
        loc_list = loc.get("access_list_id") or 0
        if loc_list and inv.find("access_list", int(loc_list)) is None:
            plan.block(
                "access_list_missing", f"Access list #{loc_list} (location {path}) bestaat niet in NPM."
            )
        old = before_locs.get(path) or {}
        guard_config(
            plan,
            old.get("advanced_config") or "",
            loc.get("advanced_config") or "",
            f"location {path}",
            confirm,
        )
    for path, old in before_locs.items():
        if path not in paths:
            guard_config(plan, old.get("advanced_config") or "", "", f"location {path} (verwijderd)", confirm)


def _check_stream(plan: ManagePlan, inv: Inventory, own_id: int | None, after: dict[str, Any]) -> None:
    port = after.get("incoming_port")
    if port is None:
        return
    port = int(port)
    if port in RESERVED_PORTS:
        plan.block("reserved_port", f"Poort {port} gebruikt NPM zelf; kies een andere inkomende poort.")
    if not after.get("tcp_forwarding") and not after.get("udp_forwarding"):
        plan.block("no_protocol", "Zet TCP of UDP (of beide) aan.")
    for o in inv.of("stream"):
        if o.get("id") == own_id or int(o.get("incoming_port") or 0) != port:
            continue
        overlap = (after.get("tcp_forwarding") and o.get("tcp_forwarding")) or (
            after.get("udp_forwarding") and o.get("udp_forwarding")
        )
        if overlap:
            plan.block(
                "port_in_use",
                f"Poort {port} is al in gebruik door stream {label_of('stream', o)}; nginx zou weigeren.",
            )


def _check_access_list(
    plan: ManagePlan, inv: Inventory, current: dict[str, Any] | None, after: dict[str, Any]
) -> None:
    items = after.get("items") or []
    clients = after.get("clients") or []
    names = [i.get("username") for i in items]
    if len(set(names)) != len(names):
        plan.block("duplicate_user", "Een gebruikersnaam staat er twee keer in.")
    if current is None and any(not i.get("password") for i in items):
        plan.block("missing_password", "Geef elke nieuwe gebruiker een wachtwoord.")
    if current is not None:
        known = {i.get("username") for i in snapshot("access_list", current).get("items") or []}
        new_without = [
            i.get("username") for i in items if i.get("username") not in known and not i.get("password")
        ]
        if new_without:
            plan.block("missing_password", f"Geef een wachtwoord aan: {', '.join(map(str, new_without))}.")
    if not items and not clients:
        plan.warn("empty_list", "Zonder gebruikers of adressen laat deze access list iedereen binnen.")
    if current is not None and after.get("satisfy_any") and not plan.before.get("satisfy_any"):
        forward_auth = [
            h
            for h in inv.access_list_users(int(current["id"]))
            if re.search(r"^\s*auth_request\s", _all_config(h), re.M)
        ]
        if forward_auth:
            plan.block(
                "satisfy_any_forward_auth",
                "'Satisfy Any' zou Authentik forward auth omzeilen op "
                f"{', '.join(label_of('proxy_host', h) for h in forward_auth)}.",
            )
    if current is not None and "items" in plan.body:
        old_names = {i["username"] for i in snapshot("access_list", current).get("items") or []}
        removed = old_names - set(names)
        if removed:
            plan.info("users_removed", f"Gebruikers die verdwijnen: {', '.join(sorted(map(str, removed)))}.")


def _all_config(host: dict[str, Any]) -> str:
    return "\n".join(
        [host.get("advanced_config") or ""]
        + [(loc or {}).get("advanced_config") or "" for loc in host.get("locations") or []]
    )


# ---------------------------------------------------------------- certificaten


def _check_pem(plan: ManagePlan, data: dict[str, Any]) -> None:
    cert = str(data.get("certificate") or "")
    key = str(data.get("certificate_key") or "")
    if "-----BEGIN CERTIFICATE-----" not in cert:
        plan.block("bad_certificate", "Plak het certificaat in PEM-vorm (-----BEGIN CERTIFICATE-----).")
    if "PRIVATE KEY-----" not in key:
        plan.block("bad_key", "Plak de private key in PEM-vorm (-----BEGIN PRIVATE KEY-----).")
    chain = str(data.get("intermediate_certificate") or "")
    if chain and "-----BEGIN CERTIFICATE-----" not in chain:
        plan.block("bad_chain", "De tussencertificaten moeten in PEM-vorm staan.")


def _plan_certificate_create(plan: ManagePlan, inv: Inventory, data: dict[str, Any]) -> None:
    provider = data.get("provider")
    domains = [str(d) for d in data.get("domain_names") or []]
    nice = (data.get("nice_name") or "").strip()
    if provider == "letsencrypt":
        if not domains:
            plan.block("missing", "Vul de domeinnamen in.")
        _check_domain_syntax(plan, domains)
        dns = bool(data.get("dns_challenge"))
        if any(d.startswith("*.") for d in domains) and not dns:
            plan.block("wildcard_needs_dns", "Een wildcard-certificaat kan enkel met een DNS-challenge.")
        if dns and not data.get("dns_provider"):
            plan.block("missing_dns_provider", "Kies de DNS-provider voor de DNS-challenge.")
        if dns and not data.get("dns_provider_credentials"):
            plan.block("missing_dns_credentials", "Vul de gegevens voor de DNS-provider in.")
        meta: dict[str, Any] = {"dns_challenge": dns, "key_type": data.get("key_type") or "rsa"}
        if dns:
            meta["dns_provider"] = data.get("dns_provider")
            meta["dns_provider_credentials"] = data.get("dns_provider_credentials")
            if data.get("propagation_seconds") is not None:
                meta["propagation_seconds"] = int(data["propagation_seconds"])
        plan.body = {"provider": "letsencrypt", "domain_names": domains, "meta": meta}
        if nice:
            plan.body["nice_name"] = nice
        plan.label = nice or ", ".join(domains)
        plan.steps.append(f"Let's Encrypt-certificaat aanvragen via NPM voor {', '.join(domains)}")
        if not dns:
            plan.info(
                "http_challenge",
                "Let's Encrypt controleert via http://<domein>/.well-known/acme-challenge/: de domeinen "
                "moeten van op het internet op poort 80 bij deze NPM uitkomen.",
            )
        existing = [
            label_of("certificate", c)
            for c in inv.of("certificate")
            if {str(n).lower() for n in c.get("domain_names") or []} == {d.lower() for d in domains}
        ]
        if existing:
            plan.warn(
                "duplicate_certificate", f"Er bestaat al een certificaat voor deze domeinen: {existing[0]}."
            )
    elif provider == "other":
        if not nice:
            plan.block("missing_name", "Geef het certificaat een naam.")
        _check_pem(plan, data)
        plan.body = {"provider": "other", "nice_name": nice}
        plan.label = nice or "eigen certificaat"
        plan.steps += [
            f"Eigen certificaat '{nice}' aanmaken in NPM",
            "Certificaat en sleutel opladen; lukt dat niet, dan verwijdert VaultX het lege certificaat weer",
        ]
    else:
        plan.block("bad_provider", "Kies Let's Encrypt of een eigen certificaat.")
    plan.after = {k: v for k, v in plan.body.items() if k != "meta"} | (
        {"meta": plan.body["meta"]} if "meta" in plan.body else {}
    )
    plan.changes = _diff("certificate", {}, plan.after)


def _check_domain_syntax(plan: ManagePlan, domains: list[str]) -> None:
    bad = [d for d in domains if not DOMAIN_RE.fullmatch(d)]
    if bad:
        plan.block("bad_domain", f"Ongeldige domeinnaam: {', '.join(bad)}.")


# ---------------------------------------------------------------- Advanced-config bewaken


def managed_blocks(cfg: str) -> Counter[str]:
    return Counter(m.group(0).rstrip() for m in MANAGED_BLOCK_RE.finditer(cfg or ""))


def _active(cfg: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in (cfg or "").splitlines())


def guard_config(plan: ManagePlan, before: str, after: str, where: str, confirm: bool) -> None:
    """Weigert wat de bestaande Advanced-config zou breken (zie de uitleg bovenaan)."""
    if before == after:
        return
    lost_blocks = managed_blocks(before) - managed_blocks(after)
    for block in lost_blocks:
        name = MANAGED_BLOCK_RE.match(block)
        label = name.group(1) if name else "?"
        plan.block(
            "managed_block_changed",
            f"Het VaultX-blok 'vaultx:{label}' in {where} werd gewijzigd of weggehaald. Dat blok beheert "
            "VaultX zelf (Authentik-bescherming of app publiceren); wijzig het daar.",
        )
    if not balanced(after):
        plan.block("unbalanced", f"De accolades in {where} zijn niet in evenwicht.")
    active_before, active_after = _active(before), _active(after)
    for code, label, pattern in PROTECTED_DIRECTIVES:
        n_before = len(pattern.findall(active_before))
        n_after = len(pattern.findall(active_after))
        if n_after < n_before:
            message = f"In {where} verdwijnt {label} ({n_before} → {n_after} regels)."
            if confirm:
                plan.warn(f"removes_{code}", message + " Je bevestigde dat dit de bedoeling is.")
            else:
                plan.block(
                    f"removes_{code}",
                    message + " Bevestig 'beschermde regels mogen weg' als dat echt de bedoeling is.",
                )
