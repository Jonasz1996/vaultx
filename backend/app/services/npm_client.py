"""Minimale client voor de REST API van Nginx Proxy Manager (getest tegen 2.16).

Enkel lezen: aanmelden, versie en proxy hosts. VaultX schrijft in deze fase
niets naar NPM. Een mislukte schrijfactie zet een host in NPM meteen offline
(zie onderzoek 03, A9), dus dat komt pas met pre-validatie en rollback.
"""

from __future__ import annotations

from typing import Any

import httpx

MAX_ERROR_DETAIL = 300


class NPMError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def normalize_base_url(url: str) -> str:
    """'https://npm.local:81/', 'https://npm.local:81/api' -> 'https://npm.local:81'."""
    url = url.strip().rstrip("/")
    if url.endswith("/api"):
        url = url[: -len("/api")]
    return url


def _detail(response: httpx.Response) -> str:
    try:
        data = response.json()
        msg = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else None
        text = msg or response.text
    except ValueError:
        text = response.text
    return text.strip()[:MAX_ERROR_DETAIL]


class NPMClient:
    def __init__(
        self,
        base_url: str,
        *,
        verify_tls: bool = True,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self._http = httpx.AsyncClient(
            base_url=f"{self.base_url}/api",
            verify=verify_tls,
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "VaultX-NPM-connector"},
        )
        self._token: str | None = None

    async def __aenter__(self) -> NPMClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = kwargs.pop("headers", {})
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            r = await self._http.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise NPMError(f"NPM antwoordt niet op tijd ({self.base_url})") from exc
        except httpx.HTTPError as exc:
            raise NPMError(f"NPM is niet bereikbaar op {self.base_url}: {exc.__class__.__name__}") from exc
        if r.status_code in (401, 403):
            raise NPMError(f"NPM weigert de toegang ({r.status_code}): {_detail(r)}")
        if r.is_redirect:
            raise NPMError(f"NPM stuurt door naar {r.headers.get('location')}: controleer de URL")
        if r.status_code >= 400:
            raise NPMError(f"NPM gaf {r.status_code} op {path}: {_detail(r)}")
        try:
            return r.json()
        except ValueError as exc:
            raise NPMError(f"NPM gaf geen JSON terug op {path}: is dit wel de NPM-beheerpoort?") from exc

    async def login(self, identity: str, secret: str) -> None:
        try:
            data = await self._request("POST", "/tokens", json={"identity": identity, "secret": secret})
        except NPMError as exc:
            raise NPMError(f"Aanmelden bij NPM mislukt: {exc.message}") from exc
        if isinstance(data, dict) and data.get("requires_2fa"):
            raise NPMError(
                "Dit NPM-account heeft tweestapsverificatie. Gebruik een apart serviceaccount zonder 2FA."
            )
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise NPMError("NPM gaf geen token terug")
        self._token = token

    async def version(self) -> str | None:
        """Versie uit GET /api/ (bv. '2.16.0'); None als NPM ze niet meegeeft."""
        data = await self._request("GET", "/")
        v = data.get("version") if isinstance(data, dict) else None
        if isinstance(v, dict) and {"major", "minor", "revision"} <= v.keys():
            return f"{v['major']}.{v['minor']}.{v['revision']}"
        return None

    async def proxy_hosts(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/nginx/proxy-hosts", params={"expand": "access_list"})
        if not isinstance(data, list):
            raise NPMError("Onverwacht antwoord van NPM op /nginx/proxy-hosts")
        return [h for h in data if isinstance(h, dict) and isinstance(h.get("id"), int)]
