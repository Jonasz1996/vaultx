"""End-to-end test van de NPM-connector tegen een echte Nginx Proxy Manager.

1. Zet een reeks proxy hosts in NPM (domeinen onder .vaultx-e2e.test), met het
   officiële Authentik-patroon, een access list, labels en een kapotte host.
2. Leest ze terug met de VaultX-client en controleert wat VaultX eruit afleidt.

Raakt enkel hosts onder .vaultx-e2e.test aan. Zie e2e/README.md.

    cd backend && python ../e2e/npm_e2e.py http://localhost:81 admin@example.com wachtwoord
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.npm_client import NPMClient  # noqa: E402
from app.services.npm_detect import analyze  # noqa: E402

SUFFIX = ".vaultx-e2e.test"
OUTPOST = "http://127.0.0.1:9000"

# Officieel Authentik-patroon voor NPM (docs 2026.8): outpost en signin in Advanced
# van de host, de auth_request-directives in een custom location "/".
AUTHENTIK_HOST_ADVANCED = f"""proxy_buffers 8 16k;
proxy_buffer_size 32k;
port_in_redirect off;

location /outpost.goauthentik.io {{
    proxy_pass              {OUTPOST}/outpost.goauthentik.io;
    proxy_set_header        Host $host;
    proxy_set_header        X-Original-URL $scheme://$http_host$request_uri;
    add_header              Set-Cookie $auth_cookie;
    auth_request_set        $auth_cookie $upstream_http_set_cookie;
    proxy_pass_request_body off;
    proxy_set_header        Content-Length "";
}}

location @goauthentik_proxy_signin {{
    internal;
    add_header Set-Cookie $auth_cookie;
    return 302 /outpost.goauthentik.io/start?rd=$scheme://$http_host$request_uri;
}}
"""

AUTHENTIK_LOCATION_ADVANCED = """auth_request     /outpost.goauthentik.io/auth/nginx;
error_page       401 = @goauthentik_proxy_signin;
auth_request_set $auth_cookie $upstream_http_set_cookie;
add_header       Set-Cookie $auth_cookie;
auth_request_set $authentik_username $upstream_http_x_authentik_username;
auth_request_set $authentik_groups $upstream_http_x_authentik_groups;
auth_request_set $authentik_email $upstream_http_x_authentik_email;
proxy_set_header X-authentik-username $authentik_username;
proxy_set_header X-authentik-groups $authentik_groups;
proxy_set_header X-authentik-email $authentik_email;
"""


def authentik_location(port: int) -> list[dict]:
    return [
        {
            "path": "/",
            "forward_scheme": "http",
            "forward_host": "127.0.0.1",
            "forward_port": port,
            "advanced_config": AUTHENTIK_LOCATION_ADVANCED,
        }
    ]


def demo_hosts(lan_acl: int) -> list[dict]:
    d = SUFFIX
    return [
        {
            "domain_names": [f"grafana{d}"],
            "forward_host": "127.0.0.1",
            "forward_port": 3000,
            "advanced_config": "# vaultx.auth = oidc\n# vaultx.tags = monitoring\n",
        },
        {
            "domain_names": [f"paperless{d}"],
            "forward_host": "127.0.0.1",
            "forward_port": 8000,
            "advanced_config": AUTHENTIK_HOST_ADVANCED + "# vaultx.tags = documenten\n",
            "locations": authentik_location(8000),
        },
        {
            "domain_names": [f"ha{d}"],
            "forward_host": "127.0.0.1",
            "forward_port": 8123,
            "access_list_id": lan_acl,
            "advanced_config": AUTHENTIK_HOST_ADVANCED,
            "locations": authentik_location(8123),
        },
        {"domain_names": [f"nas{d}"], "forward_host": "127.0.0.1", "forward_port": 5000},
        {
            "domain_names": [f"jellyfin{d}", f"media{d}"],
            "forward_host": "127.0.0.1",
            "forward_port": 8096,
            "advanced_config": "# vaultx.auth = app\n# vaultx.description = Films en series\n",
        },
        # Onbestaande upstream in een custom location (NPM's eigen location gebruikt variabelen
        # en lost niet op bij nginx -t): de config-test faalt en NPM zet de host offline.
        {
            "domain_names": [f"legacy{d}"],
            "forward_host": "legacy-app",
            "forward_port": 8080,
            "locations": [
                {"path": "/", "forward_scheme": "http", "forward_host": "legacy-app", "forward_port": 8080}
            ],
        },
        {
            "domain_names": [f"test{d}"],
            "forward_host": "127.0.0.1",
            "forward_port": 9999,
            "advanced_config": "# vaultx.ignore = true\n",
        },
    ]


def seed(base: str, identity: str, secret: str) -> None:
    with httpx.Client(base_url=f"{base.rstrip('/')}/api", timeout=60) as c:
        if not c.get("/").raise_for_status().json().get("setup", True):
            # Verse NPM (2.13+): de eerste gebruiker wordt zonder login aangemaakt.
            c.post(
                "/users",
                json={
                    "name": "Admin",
                    "nickname": "admin",
                    "email": identity,
                    "roles": ["admin"],
                    "is_disabled": False,
                    "auth": {"type": "password", "secret": secret},
                },
            ).raise_for_status()
        token = c.post("/tokens", json={"identity": identity, "secret": secret}).raise_for_status().json()
        c.headers["Authorization"] = f"Bearer {token['token']}"
        for h in c.get("/nginx/proxy-hosts").raise_for_status().json():
            if any(dn.endswith(SUFFIX) for dn in h["domain_names"]):
                c.delete(f"/nginx/proxy-hosts/{h['id']}").raise_for_status()
        acls = c.get("/nginx/access-lists").raise_for_status().json()
        acl = next((a for a in acls if a["name"] == "VaultX e2e LAN"), None)
        if acl is None:
            acl = (
                c.post(
                    "/nginx/access-lists",
                    json={
                        "name": "VaultX e2e LAN",
                        "satisfy_any": True,
                        "pass_auth": False,
                        "items": [],
                        "clients": [{"address": "192.168.0.0/16", "directive": "allow"}],
                    },
                )
                .raise_for_status()
                .json()
            )
        for host in demo_hosts(acl["id"]):
            body = {"forward_scheme": "http", "block_exploits": True, "allow_websocket_upgrade": True, **host}
            r = c.post("/nginx/proxy-hosts", json=body)
            if r.status_code >= 400:
                raise SystemExit(f"Aanmaken van {host['domain_names'][0]} mislukt: {r.status_code} {r.text}")


async def check(base: str, identity: str, secret: str) -> None:
    async with NPMClient(base) as client:
        await client.login(identity, secret)
        version = await client.version()
        hosts = [h for h in await client.proxy_hosts() if h["domain_names"][0].endswith(SUFFIX)]
    by_name = {h["domain_names"][0].split(".")[0]: (h, analyze(h)) for h in hosts}
    print(f"NPM {version}: {len(hosts)} e2e-hosts gelezen")
    for name, (h, det) in sorted(by_name.items()):
        codes = ",".join(w["code"] for w in det.warnings)
        online = (h.get("meta") or {}).get("nginx_online")
        print(f"  {name:10} {det.name:16} {det.app_type or '-':15} {det.auth_method:13} online={online} [{codes}]")

    def det(n):
        return by_name[n][1]

    def warns(n):
        return {w["code"] for w in det(n).warnings}

    assert version and version.startswith("2."), version
    assert len(hosts) == 7, len(hosts)
    assert (det("grafana").app_type, det("grafana").auth_method, det("grafana").tags) == (
        "grafana",
        "oidc",
        ["monitoring"],
    )
    assert det("paperless").forward_auth and det("paperless").auth_method == "forward_auth"
    assert "custom_root_location" not in warns("paperless"), "officieel patroon mag niet waarschuwen"
    assert det("ha").app_type == "home-assistant" and det("ha").auth_method == "forward_auth"
    assert "satisfy_any" in warns("ha")
    assert det("nas").auth_method == "unknown" and "no_tls" in warns("nas")
    assert det("jellyfin").auth_method == "app" and det("jellyfin").description == "Films en series"
    assert by_name["legacy"][0]["meta"].get("nginx_online") is False and "nginx_offline" in warns("legacy")
    assert det("test").ignore
    print("OK: detectie klopt tegen echte NPM")


def main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit("gebruik: npm_e2e.py <npm-url> <e-mail> <wachtwoord> [--seed-only]")
    base, identity, secret = sys.argv[1:4]
    seed(base, identity, secret)
    if "--seed-only" not in sys.argv:
        asyncio.run(check(base, identity, secret))


if __name__ == "__main__":
    main()
