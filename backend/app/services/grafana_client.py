"""Minimale client voor de HTTP API van Grafana (getest tegen Grafana 13.2).

Enkel wat fase 5 nodig heeft: de publieke URL van Grafana lezen (``appUrl``,
daaruit bouwt Grafana de redirect URI) en de generic OAuth-login lezen, zetten
en terugzetten via de SSO settings API. Die API vraagt een Grafana-
serverbeheerder: een serviceaccount-token krijgt er 403 (``settings:read``),
daarom meldt VaultX zich aan met gebruikersnaam en wachtwoord van een beheerder.
VaultX bewaart die gegevens niet; ze dienen enkel voor de ene actie.
"""

from __future__ import annotations

from typing import Any

import httpx

MAX_ERROR_DETAIL = 300
PROVIDER = "generic_oauth"


class GrafanaError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


def _detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text.strip()[:MAX_ERROR_DETAIL]
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])[:MAX_ERROR_DETAIL]
    return str(data)[:MAX_ERROR_DETAIL]


class GrafanaClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        verify_tls: bool = True,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=f"{self.base_url}/api",
            auth=(username, password),
            verify=verify_tls,
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "VaultX-Grafana-connector"},
        )

    async def __aenter__(self) -> GrafanaClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            r = await self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise GrafanaError(f"Grafana antwoordt niet op tijd ({self.base_url})") from exc
        except httpx.HTTPError as exc:
            raise GrafanaError(
                f"Grafana is niet bereikbaar op {self.base_url}: {exc.__class__.__name__}"
            ) from exc
        if r.status_code == 401:
            raise GrafanaError("Grafana weigert de gebruikersnaam of het wachtwoord (401).", 401)
        if r.status_code == 403:
            raise GrafanaError(
                "Dit Grafana-account mag de aanmeldinstellingen niet wijzigen (403). Gebruik een "
                "Grafana-serverbeheerder (bv. admin); een serviceaccount volstaat niet.",
                403,
            )
        if r.is_redirect:
            raise GrafanaError(
                f"Grafana stuurt door naar {r.headers.get('location')}: staat er nog iets voor Grafana "
                "(bv. Authentik forward auth)? Geef dan het interne adres van Grafana op."
            )
        if r.status_code >= 400:
            raise GrafanaError(f"Grafana gaf {r.status_code} op {path}: {_detail(r)}", r.status_code)
        if r.status_code == 204 or not r.content:
            return None
        try:
            return r.json()
        except ValueError as exc:
            raise GrafanaError(f"Grafana gaf geen JSON terug op {path}: is dit de juiste URL?") from exc

    async def frontend_settings(self) -> dict[str, Any]:
        data = await self._request("GET", "/frontend/settings")
        if not isinstance(data, dict):
            raise GrafanaError("Onverwacht antwoord van Grafana op /api/frontend/settings")
        return data

    async def sso_settings(self) -> dict[str, Any]:
        data = await self._request("GET", f"/v1/sso-settings/{PROVIDER}")
        if not isinstance(data, dict) or not isinstance(data.get("settings"), dict):
            raise GrafanaError(
                "Onverwacht antwoord van de SSO settings API van Grafana (Grafana 11 of nieuwer?)"
            )
        return data

    async def put_sso_settings(self, settings: dict[str, Any]) -> None:
        await self._request("PUT", f"/v1/sso-settings/{PROVIDER}", json={"settings": settings})

    async def reset_sso_settings(self) -> None:
        """Haalt de instellingen uit de Grafana-database: weer wat grafana.ini zegt (standaard uit)."""
        try:
            await self._request("DELETE", f"/v1/sso-settings/{PROVIDER}")
        except GrafanaError as exc:
            if exc.status != 404:  # 404: er stond niets in de database
                raise
