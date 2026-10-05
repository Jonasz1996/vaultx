"""Nep-Authentik voor tests, via een httpx MockTransport.

Bootst de stukken van de Authentik 2026.8-API na die VaultX gebruikt, met
dezelfde vormen en foutmeldingen als de echte API (zie e2e/authentik_npm_e2e.py
voor de test tegen echte Authentik).
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx

TOKEN = "fake-authentik-token"
EMBEDDED = "4d0d2666-94a4-4d6f-b426-0ac2178d3eff"
AUTH_FLOW = "ae0cb0b8-b9ac-4dd8-8916-2fc4f600a445"
INVALIDATION_FLOW = "da930ee2-174b-4553-a8e8-279d17c1fe70"


def _page(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {"pagination": {"next": 0, "count": len(results)}, "results": results}


class FakeAuthentik:
    def __init__(self) -> None:
        self.outposts: dict[str, dict[str, Any]] = {
            EMBEDDED: {
                "pk": EMBEDDED,
                "name": "authentik Embedded Outpost",
                "type": "proxy",
                "providers": [],
                "config": {"authentik_host": "https://auth.domain.be"},
                "managed": "goauthentik.io/outposts/embedded",
            }
        }
        self.flows = {
            "default-provider-authorization-implicit-consent": AUTH_FLOW,
            "default-provider-invalidation-flow": INVALIDATION_FLOW,
        }
        self.groups: list[dict[str, Any]] = []
        self.providers: dict[int, dict[str, Any]] = {}
        self.applications: dict[str, dict[str, Any]] = {}
        self.bindings: list[dict[str, Any]] = []
        self._next_pk = 1
        self.requests: list[httpx.Request] = []
        # "METHODE /pad-prefix" -> (status, body): die request faalt.
        self.fail: dict[str, tuple[int, Any]] = {}
        self.down = False
        self.transport = httpx.MockTransport(self._handle)

    # ------------------------------------------------------------ hulp voor tests

    def add_group(self, name: str) -> dict[str, Any]:
        g = {"pk": str(uuid.uuid4()), "name": name}
        self.groups.append(g)
        return g

    def add_provider(
        self,
        external_host: str,
        *,
        mode: str = "forward_single",
        name: str | None = None,
        cookie_domain: str = "",
        app: str | None = None,
        outpost: str | None = EMBEDDED,
    ) -> dict:
        pk = self._next_pk
        self._next_pk += 1
        p = {
            "pk": pk,
            "name": name or f"Provider {pk}",
            "mode": mode,
            "external_host": external_host,
            "cookie_domain": cookie_domain,
            "assigned_application_slug": None,
            "assigned_application_name": None,
        }
        self.providers[pk] = p
        if app:
            self.applications[app] = {
                "pk": str(uuid.uuid4()),
                "slug": app,
                "name": app.title(),
                "provider": pk,
            }
            p["assigned_application_slug"] = app
            p["assigned_application_name"] = app.title()
        if outpost:
            self.outposts[outpost]["providers"].append(pk)
        return p

    def serves(self, domain: str) -> bool:
        """Kent een outpost dit domein (provider met applicatie op een outpost)?"""
        on_outposts = {pk for o in self.outposts.values() for pk in o["providers"]}
        for p in self.providers.values():
            if p["pk"] not in on_outposts or not p["assigned_application_slug"]:
                continue
            if (urlsplit(p["external_host"]).hostname or "") == domain:
                return True
            cd = p.get("cookie_domain", "").lstrip(".")
            if p["mode"] == "forward_domain" and cd and (domain == cd or domain.endswith("." + cd)):
                return True
        return False

    def bindings_for(self, slug: str) -> list[str]:
        app = self.applications[slug]
        names = {g["pk"]: g["name"] for g in self.groups}
        return [names[b["group"]] for b in self.bindings if b["target"] == app["pk"]]

    # ------------------------------------------------------------ API

    def _json(self, request: httpx.Request) -> dict[str, Any]:
        return json.loads(request.content or b"{}")

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"detail": "Token invalid/expired"})
        path = request.url.path.removeprefix("/api/v3")
        key = f"{request.method} {path}"
        for prefix, (status, body) in self.fail.items():
            if key.startswith(prefix):
                return httpx.Response(status, json=body)
        q = request.url.params
        m = request.method

        if path == "/outposts/instances/" and m == "GET":
            return httpx.Response(
                200, json=_page([o for o in self.outposts.values() if o["type"] == q.get("type", o["type"])])
            )
        if mo := re.fullmatch(r"/outposts/instances/([^/]+)/", path):
            o = self.outposts.get(mo.group(1))
            if o is None:
                return httpx.Response(404, json={"detail": "No Outpost matches the given query."})
            if m == "PATCH":
                providers = self._json(request)["providers"]
                if any(pk not in self.providers for pk in providers):
                    return httpx.Response(400, json={"providers": ["Invalid pk - object does not exist."]})
                o["providers"] = providers
            return httpx.Response(200, json=o)
        if path == "/flows/instances/" and m == "GET":
            slug = q.get("slug")
            found = [{"pk": pk, "slug": s} for s, pk in self.flows.items() if s == slug]
            return httpx.Response(200, json=_page(found))
        if path == "/core/groups/" and m == "GET":
            search = q.get("search", "")
            return httpx.Response(200, json=_page([g for g in self.groups if search in g["name"]]))
        if path == "/providers/proxy/" and m == "GET":
            search = q.get("search")
            found = [
                p for p in self.providers.values() if not search or search in p["name"] + p["external_host"]
            ]
            return httpx.Response(200, json=_page(found))
        if path == "/providers/proxy/" and m == "POST":
            body = self._json(request)
            if any(p["name"] == body["name"] for p in self.providers.values()):
                return httpx.Response(400, json={"name": ["provider with this name already exists."]})
            if body.get("authorization_flow") not in self.flows.values():
                return httpx.Response(400, json={"authorization_flow": ["This field is required."]})
            p = self.add_provider(body["external_host"], mode=body["mode"], name=body["name"], outpost=None)
            return httpx.Response(201, json=p)
        if mp := re.fullmatch(r"/providers/proxy/(\d+)/", path):
            pk = int(mp.group(1))
            if pk not in self.providers or m != "DELETE":
                return httpx.Response(404, json={"detail": "No ProxyProvider matches the given query."})
            del self.providers[pk]
            for o in self.outposts.values():
                o["providers"] = [x for x in o["providers"] if x != pk]
            for app in self.applications.values():
                if app["provider"] == pk:
                    app["provider"] = None
            return httpx.Response(204)
        if path == "/core/applications/" and m == "POST":
            body = self._json(request)
            errors: dict[str, list[str]] = {}
            if body["slug"] in self.applications:
                errors["slug"] = ["Application with this slug already exists."]
            if any(a["provider"] == body["provider"] for a in self.applications.values()):
                errors["provider"] = ["Application with this provider already exists."]
            if errors:
                return httpx.Response(400, json=errors)
            app = {"pk": str(uuid.uuid4()), **body}
            self.applications[body["slug"]] = app
            p = self.providers[body["provider"]]
            p["assigned_application_slug"] = body["slug"]
            p["assigned_application_name"] = body["name"]
            return httpx.Response(201, json=app)
        if ma := re.fullmatch(r"/core/applications/([^/]+)/", path):
            app = self.applications.get(ma.group(1))
            if app is None or m != "DELETE":
                return httpx.Response(404, json={"detail": "No Application matches the given query."})
            del self.applications[app["slug"]]
            self.bindings = [b for b in self.bindings if b["target"] != app["pk"]]
            if app["provider"] in self.providers:
                self.providers[app["provider"]]["assigned_application_slug"] = None
                self.providers[app["provider"]]["assigned_application_name"] = None
            return httpx.Response(204)
        if path == "/policies/bindings/" and m == "POST":
            body = self._json(request)
            b = {"pk": str(uuid.uuid4()), **body}
            self.bindings.append(b)
            return httpx.Response(201, json=b)
        return httpx.Response(404, json={"detail": f"Not found: {key}"})
