"""Schema's voor NPM volledig beheren: hosts, redirects, 404-hosts, streams, access lists, certificaten."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.common import ORMModel

KindName = Literal["proxy_host", "redirection_host", "dead_host", "stream", "access_list", "certificate"]
ActionName = Literal["create", "update", "delete", "enable", "disable", "renew", "upload"]
HOSTNAME_RE = r"^[A-Za-z0-9._:\[\]-]+$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocationData(_Strict):
    id: int | None = None
    path: str = Field(min_length=1, max_length=255)
    forward_scheme: Literal["http", "https"] = "http"
    forward_host: str = Field(min_length=1, max_length=255, pattern=HOSTNAME_RE)
    forward_port: int = Field(ge=1, le=65535)
    forward_path: str | None = Field(default=None, max_length=255)
    advanced_config: str = Field(default="", max_length=65536)
    access_list_id: int | None = Field(default=None, ge=0)


class ProxyHostData(_Strict):
    domain_names: list[str] | None = Field(default=None, max_length=100)
    forward_scheme: Literal["http", "https"] | None = None
    forward_host: str | None = Field(default=None, min_length=1, max_length=255, pattern=HOSTNAME_RE)
    forward_port: int | None = Field(default=None, ge=1, le=65535)
    certificate_id: int | None = Field(default=None, ge=0)
    ssl_forced: bool | None = None
    hsts_enabled: bool | None = None
    hsts_subdomains: bool | None = None
    trust_forwarded_proto: bool | None = None
    http2_support: bool | None = None
    block_exploits: bool | None = None
    caching_enabled: bool | None = None
    allow_websocket_upgrade: bool | None = None
    access_list_id: int | None = Field(default=None, ge=0)
    advanced_config: str | None = Field(default=None, max_length=65536)
    locations: list[LocationData] | None = Field(default=None, max_length=50)


class RedirectionHostData(_Strict):
    domain_names: list[str] | None = Field(default=None, max_length=100)
    forward_scheme: Literal["auto", "http", "https"] | None = None
    forward_http_code: int | None = Field(default=None, ge=300, le=308)
    forward_domain_name: str | None = Field(default=None, min_length=1, max_length=253)
    preserve_path: bool | None = None
    certificate_id: int | None = Field(default=None, ge=0)
    ssl_forced: bool | None = None
    hsts_enabled: bool | None = None
    hsts_subdomains: bool | None = None
    http2_support: bool | None = None
    block_exploits: bool | None = None
    advanced_config: str | None = Field(default=None, max_length=65536)


class DeadHostData(_Strict):
    domain_names: list[str] | None = Field(default=None, max_length=100)
    certificate_id: int | None = Field(default=None, ge=0)
    ssl_forced: bool | None = None
    hsts_enabled: bool | None = None
    hsts_subdomains: bool | None = None
    http2_support: bool | None = None
    advanced_config: str | None = Field(default=None, max_length=65536)


class StreamData(_Strict):
    incoming_port: int | None = Field(default=None, ge=1, le=65535)
    forwarding_host: str | None = Field(default=None, min_length=1, max_length=255, pattern=HOSTNAME_RE)
    forwarding_port: int | None = Field(default=None, ge=1, le=65535)
    tcp_forwarding: bool | None = None
    udp_forwarding: bool | None = None
    certificate_id: int | None = Field(default=None, ge=0)


class AccessListUser(_Strict):
    username: str = Field(min_length=1, max_length=255, pattern=r"^[^:\s]+$")
    # Leeg bij bewerken = wachtwoord ongewijzigd (zoals NPM zelf).
    password: str = Field(default="", max_length=255)


class AccessListClient(_Strict):
    address: str = Field(min_length=1, max_length=64, pattern=r"^(all|[0-9A-Fa-f.:/]+)$")
    directive: Literal["allow", "deny"] = "allow"


class AccessListData(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    satisfy_any: bool | None = None
    pass_auth: bool | None = None
    items: list[AccessListUser] | None = Field(default=None, max_length=200)
    clients: list[AccessListClient] | None = Field(default=None, max_length=200)


class CertificateData(_Strict):
    provider: Literal["letsencrypt", "other"] | None = None
    nice_name: str | None = Field(default=None, max_length=255)
    domain_names: list[str] | None = Field(default=None, max_length=100)
    dns_challenge: bool | None = None
    dns_provider: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_-]+$")
    dns_provider_credentials: str | None = Field(default=None, max_length=8192)
    propagation_seconds: int | None = Field(default=None, ge=0, le=3600)
    key_type: Literal["rsa", "ecdsa"] | None = None
    certificate: str | None = Field(default=None, max_length=65536)
    certificate_key: str | None = Field(default=None, max_length=65536)
    intermediate_certificate: str | None = Field(default=None, max_length=65536)


DATA_MODELS: dict[str, type[_Strict]] = {
    "proxy_host": ProxyHostData,
    "redirection_host": RedirectionHostData,
    "dead_host": DeadHostData,
    "stream": StreamData,
    "access_list": AccessListData,
    "certificate": CertificateData,
}


def parse_data(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """Valideert de velden voor dit soort object; enkel wat meegegeven is, telt."""
    try:
        model = DATA_MODELS[kind].model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(x) for x in first.get("loc", ()))
        raise ValueError(f"{where}: {first.get('msg')}") from exc
    out = model.model_dump(exclude_unset=True)
    if "locations" in out and out["locations"] is not None:
        out["locations"] = [
            {k: v for k, v in loc.items() if v is not None and not (k == "id" and v is None)}
            for loc in out["locations"]
        ]
    return {k: v for k, v in out.items() if v is not None or k in ("certificate_id", "access_list_id")}


class ManageRequest(BaseModel):
    action: ActionName
    npm_id: int | None = Field(default=None, ge=1)
    data: dict[str, Any] = Field(default_factory=dict)
    expected_modified_on: str | None = None
    # "Beschermde regels mogen weg": auth_request, sub_filter, add_header of de outpost uit Advanced halen.
    confirm_removals: bool = False
    verify: bool = True


class CheckOut(BaseModel):
    code: str
    level: Literal["block", "warn", "info"]
    message: str


class ManagePlanOut(BaseModel):
    kind: KindName
    action: ActionName
    npm_id: int | None
    label: str
    modified_on: str | None
    can_apply: bool
    checks: list[CheckOut]
    steps: list[str]
    changes: list[dict[str, Any]]
    before: dict[str, Any]
    after: dict[str, Any]
    expect: str | None
    probe_url: str | None
    certificate_check: dict[str, Any] | None = None


class ManageChangeOut(ORMModel):
    id: UUID
    connection_id: UUID
    host_id: UUID | None
    object_type: KindName
    npm_id: int
    domain: str
    action: str
    status: Literal["running", "applied", "rolled_back", "rollback_failed", "refused", "interrupted"]
    verified: bool
    actor_label: str | None
    message: str | None
    nginx_error: str | None
    before: dict[str, Any]
    after: dict[str, Any] | None
    probe_before: dict[str, Any] | None
    probe_after: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None


class DnsProviderOut(BaseModel):
    id: str
    name: str | None
    credentials: str | None
