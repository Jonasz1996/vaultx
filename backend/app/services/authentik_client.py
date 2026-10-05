"""Minimale client voor de REST API van Authentik (getest tegen 2026.8.3).

Enkel wat fase 4 en 5 nodig hebben: outposts lezen en hun providerlijst
bijwerken, proxy providers, OAuth2/OpenID-providers en applicaties aanmaken en
verwijderen, groepen zoeken en een groep aan een applicatie binden, en de
standaard scope mappings en het ondertekeningscertificaat vinden. Aanmelden gaat met een API-token van een
serviceaccount (Authorization: Bearer). Welke rechten dat account nodig heeft,
staat in docs/npm.md (fase 4) en docs/autologin.md (fase 5).

Applicaties zoekt VaultX bewust niet via ``GET /core/applications/``: die lijst
filtert op wat de aanroeper zelf mag openen (onderzoek 02, KB-37). De
provider-endpoints geven ``assigned_application_slug`` wel volledig terug.
"""

from __future__ import annotations

from typing import Any

import httpx

MAX_ERROR_DETAIL = 300
PAGE_SIZE = 100


class AuthentikError(Exception):
    def __init__(self, message: str, status: int | None = None, fields: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        # Validatiefouten per veld, zoals Authentik ze teruggeeft bij een 400.
        self.fields = fields or {}

    def field_has(self, name: str, text: str) -> bool:
        return any(text in str(m) for m in self.fields.get(name) or [])


def _detail(response: httpx.Response) -> tuple[str, dict[str, Any]]:
    try:
        data = response.json()
    except ValueError:
        return response.text.strip()[:MAX_ERROR_DETAIL], {}
    if isinstance(data, dict):
        if "detail" in data:
            return str(data["detail"])[:MAX_ERROR_DETAIL], {}
        fields = {k: v if isinstance(v, list) else [v] for k, v in data.items()}
        text = "; ".join(f"{k}: {' '.join(map(str, v))}" for k, v in fields.items())
        return text[:MAX_ERROR_DETAIL], fields
    return str(data)[:MAX_ERROR_DETAIL], {}


class AuthentikClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        verify_tls: bool = True,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v3",
            verify=verify_tls,
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "VaultX-Authentik-connector",
            },
        )

    async def __aenter__(self) -> AuthentikClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            r = await self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise AuthentikError(f"Authentik antwoordt niet op tijd ({self.base_url})") from exc
        except httpx.HTTPError as exc:
            raise AuthentikError(
                f"Authentik is niet bereikbaar op {self.base_url}: {exc.__class__.__name__}"
            ) from exc
        if r.status_code == 401:
            raise AuthentikError("Authentik weigert het API-token (401). Controleer het token.", 401)
        if r.status_code == 403:
            text, _ = _detail(r)
            raise AuthentikError(
                f"Het Authentik-serviceaccount mag dit niet ({method} {path}): {text}. "
                "Zie docs/npm.md voor de nodige rechten.",
                403,
            )
        if r.is_redirect:
            raise AuthentikError(f"Authentik stuurt door naar {r.headers.get('location')}: controleer de URL")
        if r.status_code >= 400:
            text, fields = _detail(r)
            raise AuthentikError(f"Authentik gaf {r.status_code} op {path}: {text}", r.status_code, fields)
        if r.status_code == 204 or not r.content:
            return None
        try:
            return r.json()
        except ValueError as exc:
            raise AuthentikError(f"Authentik gaf geen JSON terug op {path}: is dit de juiste URL?") from exc

    async def _list(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Alle resultaten van een gepagineerde lijst (stopt na 20 pagina's)."""
        out: list[dict[str, Any]] = []
        page = 1
        while page <= 20:
            data = await self._request(
                "GET", path, params={**(params or {}), "page": page, "page_size": PAGE_SIZE}
            )
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise AuthentikError(f"Onverwacht antwoord van Authentik op {path}")
            out += [r for r in data["results"] if isinstance(r, dict)]
            if not (data.get("pagination") or {}).get("next"):
                break
            page += 1
        return out

    # ------------------------------------------------------------ outposts

    async def outposts(self) -> list[dict[str, Any]]:
        return await self._list("/outposts/instances/", {"type": "proxy"})

    async def outpost(self, pk: str) -> dict[str, Any]:
        return await self._request("GET", f"/outposts/instances/{pk}/")

    async def set_outpost_providers(self, pk: str, providers: list[int]) -> dict[str, Any]:
        return await self._request("PATCH", f"/outposts/instances/{pk}/", json={"providers": providers})

    # ------------------------------------------------------------ flows en groepen

    async def flow_pk(self, slug: str) -> str | None:
        found = await self._list("/flows/instances/", {"slug": slug})
        return next((str(f["pk"]) for f in found if f.get("slug") == slug), None)

    async def groups(self, search: str) -> list[dict[str, Any]]:
        return await self._list("/core/groups/", {"search": search, "include_users": "false"})

    # ------------------------------------------------------------ providers en applicaties

    async def proxy_providers(self, search: str | None = None) -> list[dict[str, Any]]:
        return await self._list("/providers/proxy/", {"search": search} if search else None)

    async def create_proxy_provider(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/providers/proxy/", json=body)

    async def delete_proxy_provider(self, pk: int) -> None:
        await self._request("DELETE", f"/providers/proxy/{int(pk)}/")

    async def oauth2_providers(self, search: str | None = None) -> list[dict[str, Any]]:
        return await self._list("/providers/oauth2/", {"search": search} if search else None)

    async def create_oauth2_provider(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/providers/oauth2/", json=body)

    async def delete_oauth2_provider(self, pk: int) -> None:
        await self._request("DELETE", f"/providers/oauth2/{int(pk)}/")

    async def scope_mappings(self) -> list[dict[str, Any]]:
        return await self._list("/propertymappings/provider/scope/")

    async def certificate_keypairs(self) -> list[dict[str, Any]]:
        return await self._list("/crypto/certificatekeypairs/", {"has_key": "true"})

    async def create_application(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/core/applications/", json=body)

    async def delete_application(self, slug: str) -> None:
        await self._request("DELETE", f"/core/applications/{slug}/")

    async def create_binding(self, target: str, group: str, order: int) -> dict[str, Any]:
        body = {
            "target": target,
            "group": group,
            "order": order,
            "enabled": True,
            "negate": False,
            "timeout": 30,
        }
        return await self._request("POST", "/policies/bindings/", json=body)
