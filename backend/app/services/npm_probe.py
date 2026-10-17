"""Controle van een proxy host van buitenaf: wat krijgt een bezoeker zonder sessie te zien?

VaultX stuurt een gewone GET naar NPM (poort 80 of 443) met de domeinnaam van
de host als Host-header en, bij HTTPS, als SNI. Er gaan geen cookies of
credentials mee. Na het zetten van Authentik-bescherming hoort een bezoeker
zonder sessie een doorverwijzing naar ``/outpost.goauthentik.io/start`` te
krijgen; een 5xx betekent dat nginx de outpost niet bereikt.

Het certificaat wordt niet gecontroleerd: VaultX leest enkel de statuscode en
de Location-header, en vertrouwt niets van de inhoud.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from app.services.npm_protect import Action

OUTPOST_START = "/outpost.goauthentik.io/start"
REDIRECTS = {301, 302, 303, 307, 308}


@dataclass(frozen=True, slots=True)
class ProbeTarget:
    scheme: str  # http | https
    address: str  # IP of hostnaam waarop NPM luistert
    port: int
    domain: str
    path: str = "/"

    @property
    def url(self) -> str:
        return f"{self.scheme}://{self.domain}{self.path} via {self.address}:{self.port}"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    url: str
    status: int | None = None
    location: str | None = None
    error: str | None = None

    @property
    def authentik_redirect(self) -> bool:
        return self.status in REDIRECTS and OUTPOST_START in (self.location or "")

    @property
    def server_error(self) -> bool:
        return self.status is not None and self.status >= 500

    def summary(self) -> str:
        if self.error:
            return f"niet bereikbaar ({self.error})"
        if self.authentik_redirect:
            return f"{self.status}, doorverwezen naar Authentik"
        if self.status in REDIRECTS:
            return f"{self.status} naar {self.location}"
        return str(self.status)

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "summary": self.summary()}


Prober = Callable[[ProbeTarget], Awaitable[ProbeResult]]


def make_prober(timeout: float = 10.0) -> Prober:
    async def probe(t: ProbeTarget) -> ProbeResult:
        host = f"[{t.address}]" if ":" in t.address else t.address
        url = f"{t.scheme}://{host}:{t.port}{t.path}"
        extensions = {"sni_hostname": t.domain} if t.scheme == "https" else {}
        try:
            # Geen uitgaande proxy uit de omgeving: NPM staat in het eigen netwerk.
            async with httpx.AsyncClient(
                verify=False,  # noqa: S501 - enkel statuscode en Location worden gelezen
                trust_env=False,
                follow_redirects=False,
                timeout=timeout,
            ) as client:
                r = await client.get(
                    url,
                    headers={"Host": t.domain, "User-Agent": "VaultX-NPM-check"},
                    extensions=extensions,
                )
        except httpx.TimeoutException:
            return ProbeResult(url=t.url, error="time-out")
        except httpx.HTTPError as exc:
            return ProbeResult(url=t.url, error=exc.__class__.__name__)
        return ProbeResult(url=t.url, status=r.status_code, location=r.headers.get("location"))

    return probe


def judge(
    action: Action, before: ProbeResult | None, after: ProbeResult, *, provider_by_vaultx: bool = False
) -> str | None:
    """None als het resultaat in orde is, anders de reden om terug te zetten.

    provider_by_vaultx: VaultX zette net zelf de provider op de outpost (fase 4); een 5xx wijst dan
    niet op een ontbrekende provider.
    """
    if after.error:
        return f"De host is na de wijziging niet meer bereikbaar: {after.error}."
    if action == "protect":
        if after.authentik_redirect:
            return None
        if after.server_error and provider_by_vaultx:
            return (
                f"De host geeft {after.status} in plaats van door te verwijzen naar Authentik. "
                "Waarschijnlijk bereikt nginx in NPM de outpost niet (controleer de outpost-URL), of laadde "
                "de outpost de nieuwe provider niet op tijd."
            )
        if after.server_error:
            return (
                f"De host geeft {after.status} in plaats van door te verwijzen naar Authentik. "
                "Waarschijnlijk bereikt nginx in NPM de outpost niet (controleer de outpost-URL), of kent "
                "de outpost dit domein niet (maak in Authentik een Proxy Provider in forward-auth-modus)."
            )
        return (
            f"Verwacht: doorverwijzing naar Authentik voor een bezoeker zonder sessie. "
            f"Gekregen: {after.summary()}."
        )
    if after.server_error and not (before and before.server_error):
        earlier = before.summary() if before else "?"
        return f"De host geeft {after.status} na het weghalen van de bescherming (ervoor: {earlier})."
    return None
