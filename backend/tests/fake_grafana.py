"""Nep-Grafana voor tests: de SSO settings API (zoals Grafana 13.2) en wat een bezoeker op /login krijgt."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from app.services.npm_probe import ProbeResult, ProbeTarget

USER = "admin"
PASSWORD = "grafana-admin-pw"
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "name": "OAuth",
    "clientId": "some_id",
    "clientSecret": "",
    "authUrl": "",
    "autoLogin": False,
    "scopes": "user:email",
}


class FakeGrafana:
    def __init__(self, app_url: str = "https://grafana.domain.be/") -> None:
        self.app_url = app_url
        self.settings: dict[str, Any] = dict(DEFAULTS)
        self.source = "system"
        self.version = "13.2.3"
        self.requests: list[httpx.Request] = []
        # "METHODE /pad" -> (status, body): die request faalt.
        self.fail: dict[str, tuple[int, Any]] = {}
        # PUT antwoordt 204 maar bewaart niets (zoals een Grafana die het niet overneemt).
        self.ignore_put = False
        self.transport = httpx.MockTransport(self._handle)

    def _authorized(self, request: httpx.Request) -> bool:
        expected = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
        return request.headers.get("authorization") == expected

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._authorized(request):
            return httpx.Response(401, json={"message": "Invalid username or password"})
        key = f"{request.method} {request.url.path}"
        if key in self.fail:
            status, body = self.fail[key]
            return httpx.Response(status, json=body)
        if key == "GET /api/frontend/settings":
            return httpx.Response(200, json={"appUrl": self.app_url, "buildInfo": {"version": self.version}})
        if request.url.path == "/api/v1/sso-settings/generic_oauth":
            if request.method == "GET":
                masked = {
                    **self.settings,
                    "clientSecret": "*********" if self.settings["clientSecret"] else "",
                }
                return httpx.Response(
                    200,
                    json={"id": "", "provider": "generic_oauth", "settings": masked, "source": self.source},
                )
            if request.method == "PUT":
                body = json.loads(request.content)["settings"]
                if body.get("enabled") and not body.get("clientId"):
                    return httpx.Response(400, json={"message": "Client Id is required."})
                if not self.ignore_put:
                    self.settings = {**DEFAULTS, **body}
                    self.source = "database"
                return httpx.Response(204)
            if request.method == "DELETE":
                self.settings = dict(DEFAULTS)
                self.source = "system"
                return httpx.Response(204)
        return httpx.Response(404, json={"message": "Not found"})

    def probe(self, t: ProbeTarget) -> ProbeResult:
        """Grafana zelf, voor een bezoeker zonder sessie."""
        path = urlsplit(t.path).path
        s = self.settings
        if path == "/login":
            if s["enabled"] and s["autoLogin"]:
                return ProbeResult(url=t.url, status=307, location="/login/generic_oauth?redirectTo=")
            return ProbeResult(url=t.url, status=200)
        if path == "/login/generic_oauth" and s["enabled"]:
            query = urlencode(
                {
                    "client_id": s["clientId"],
                    "redirect_uri": self.app_url.rstrip("/") + "/login/generic_oauth",
                    "response_type": "code",
                    "scope": s["scopes"],
                }
            )
            return ProbeResult(url=t.url, status=302, location=f"{s['authUrl']}?{query}")
        return ProbeResult(url=t.url, status=302, location="/login")
