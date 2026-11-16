"""CORS enkel voor de lees-API voor dashboards (/api/v1/stats).

De rest van VaultX staat CORS bewust niet toe (de CSRF-bescherming steunt daarop). Voor
/api/v1/stats mag een dashboard op een andere website (VAULTX_DASHBOARD_ORIGINS) de cijfers
vanuit de browser ophalen, met een API-sleutel in de Authorization-header. Cookies gaan nooit
mee (geen Access-Control-Allow-Credentials), dus een andere site kan niet met jouw sessie lezen.
"""

from __future__ import annotations

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

CORS_PATHS = ("/api/v1/stats",)


class DashboardCORSMiddleware:
    def __init__(self, app: ASGIApp, origins: list[str]) -> None:
        self.app = app
        self.cors = (
            CORSMiddleware(
                app,
                allow_origins=origins,
                allow_methods=["GET"],
                allow_headers=["Authorization"],
                allow_credentials=False,
                max_age=600,
            )
            if origins
            else None
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.cors is not None and scope["type"] == "http" and scope["path"] in CORS_PATHS:
            await self.cors(scope, receive, send)
        else:
            await self.app(scope, receive, send)
