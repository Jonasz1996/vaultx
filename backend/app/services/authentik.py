"""Authentik-API voor de beheerinterface: welke outposts kan een koppeling gebruiken (fase 4)."""

from __future__ import annotations

from uuid import UUID

from app.core.config import Settings
from app.core.errors import PermissionDeniedError
from app.services.authentik_client import AuthentikClient, AuthentikError
from app.services.npm_write import AuthentikFactory
from app.services.principal import Principal


class AuthentikService:
    def __init__(self, settings: Settings, factory: AuthentikFactory | None = None) -> None:
        self.settings = settings
        self.factory = factory or self._default

    def _default(self) -> AuthentikClient:
        token = self.settings.authentik_api_token
        return AuthentikClient(
            self.settings.authentik_base_url,
            token.get_secret_value() if token else "",
            verify_tls=self.settings.authentik_verify_tls,
            timeout=self.settings.authentik_http_timeout_seconds,
        )

    async def outposts(self, p: Principal, org_id: UUID) -> dict:
        if not p.can_manage_org(org_id):
            raise PermissionDeniedError("Enkel beheerders van de organisatie kiezen een Authentik-outpost")
        out: dict = {
            "configured": self.settings.authentik_api_enabled,
            "api_url": self.settings.authentik_base_url,
        }
        if not out["configured"]:
            return out
        try:
            async with self.factory() as client:
                found = await client.outposts()
        except AuthentikError as exc:
            out["error"] = exc.message
            return out
        out["outposts"] = [
            {
                "pk": str(o["pk"]),
                "name": o.get("name") or str(o["pk"]),
                "managed": o.get("managed"),
                "authentik_host": (o.get("config") or {}).get("authentik_host") or None,
                "provider_count": len(o.get("providers") or []),
            }
            for o in found
            if o.get("type") == "proxy"
        ]
        return out
