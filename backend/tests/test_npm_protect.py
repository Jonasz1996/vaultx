"""Het plan voor Authentik-bescherming (pure functies, zonder NPM)."""

import copy

import pytest

from app.services.npm_detect import analyze
from app.services.npm_protect import (
    CREATED_MARK,
    MARK_BEGIN,
    applied,
    balanced,
    normalize_outpost_url,
    plan_protect,
    plan_unprotect,
    strip_block,
)
from tests.fake_npm import proxy_host

OUTPOST = "http://authentik-server:9000"


def _apply(host, plan):
    out = copy.deepcopy(host)
    out.update(copy.deepcopy(plan.after))
    return out


def test_protect_follows_official_pattern_and_creates_root_location():
    host = proxy_host(
        7,
        ["grafana.example.be"],
        forward_host="grafana",
        forward_port=3000,
        advanced_config="# vaultx.tags = monitoring\nclient_max_body_size 50m;\n",
        certificate_id=1,
        ssl_forced=True,
    )
    plan = plan_protect(host, OUTPOST)
    assert not plan.blocked and plan.has_changes, plan.checks
    cfg = plan.after["advanced_config"]
    # Bestaande config blijft vooraan staan, het VaultX-blok komt erachter.
    assert cfg.startswith("# vaultx.tags = monitoring\nclient_max_body_size 50m;\n")
    assert f"proxy_pass              {OUTPOST}/outpost.goauthentik.io;" in cfg
    assert "location @goauthentik_proxy_signin" in cfg
    assert "location / " not in cfg and "location /{" not in cfg, "geen eigen location / in Advanced"
    [root] = plan.after["locations"]
    assert (root["path"], root["forward_host"], root["forward_port"]) == ("/", "grafana", 3000)
    assert CREATED_MARK in root["advanced_config"]
    assert "auth_request     /outpost.goauthentik.io/auth/nginx;" in root["advanced_config"]
    assert "proxy_set_header X-authentik-uid $authentik_uid;" in root["advanced_config"]
    assert all(balanced(c) for c in (cfg, root["advanced_config"]))
    assert not [c for c in plan.checks if c.level == "warn"]

    # Detectie herkent het resultaat als Authentik forward auth, beheerd door VaultX, zonder waarschuwing.
    det = analyze(_apply(host, plan))
    assert det.forward_auth and det.managed and det.auth_method == "forward_auth"
    assert det.labels == {"tags": "monitoring"}
    assert not det.warnings


def test_protect_then_unprotect_restores_exact_config():
    loc = {
        "path": "/api",
        "forward_scheme": "http",
        "forward_host": "app",
        "forward_port": 8080,
        "advanced_config": "proxy_read_timeout 300;\n",
    }
    root = {"path": "/", "forward_scheme": "http", "forward_host": "app", "forward_port": 80}
    for host in (
        proxy_host(1, ["a.example.be"], advanced_config="# vaultx.app = A"),
        proxy_host(2, ["b.example.be"], locations=[copy.deepcopy(loc)]),
        proxy_host(3, ["c.example.be"], locations=[copy.deepcopy(root), copy.deepcopy(loc)]),
        proxy_host(4, ["d.example.be"]),
    ):
        protected = _apply(host, plan_protect(host, OUTPOST))
        undo = plan_unprotect(protected)
        assert not undo.blocked, undo.checks
        restored = _apply(protected, undo)
        # Op een slotregel na identiek (VaultX begint zijn blok op een nieuwe regel).
        assert restored["advanced_config"].rstrip("\n") == host["advanced_config"].rstrip("\n")

        def norm(locs):
            return [{"advanced_config": "", **loc} for loc in locs]

        assert norm(restored["locations"]) == norm(host["locations"]), host["domain_names"]


def test_existing_locations_all_get_auth_request():
    host = proxy_host(
        5,
        ["app.example.be"],
        locations=[
            {"path": "/", "forward_scheme": "http", "forward_host": "app", "forward_port": 80},
            {"path": "/api", "forward_scheme": "http", "forward_host": "api", "forward_port": 81},
        ],
    )
    plan = plan_protect(host, OUTPOST)
    assert [loc["path"] for loc in plan.after["locations"]] == ["/", "/api"]
    assert all(MARK_BEGIN in loc["advanced_config"] for loc in plan.after["locations"])
    assert all(CREATED_MARK not in loc["advanced_config"] for loc in plan.after["locations"])


def test_existing_tuning_directives_are_not_duplicated():
    host = proxy_host(6, ["x.example.be"], advanced_config="proxy_buffers 4 32k;\nport_in_redirect off;\n")
    plan = plan_protect(host, OUTPOST)
    block = strip_block(plan.after["advanced_config"])
    assert block == host["advanced_config"]
    cfg = plan.after["advanced_config"]
    assert cfg.count("proxy_buffers") == 1 and cfg.count("port_in_redirect") == 1
    assert "proxy_buffer_size 32k;" in cfg
    assert any(c.code == "tuning_kept" for c in plan.checks)


@pytest.mark.parametrize(
    ("host", "code"),
    [
        (
            proxy_host(1, ["a.be"], advanced_config="location / {\n proxy_pass http://x;\n}\n"),
            "custom_root_location",
        ),
        (
            proxy_host(1, ["a.be"], access_list={"id": 1, "name": "LAN", "satisfy_any": True}),
            "satisfy_any",
        ),
        (
            proxy_host(
                1,
                ["a.be"],
                locations=[
                    {
                        "path": "/",
                        "forward_scheme": "http",
                        "forward_host": "a",
                        "forward_port": 1,
                        "advanced_config": "auth_request /outpost.goauthentik.io/auth/nginx;",
                    }
                ],
            ),
            "already_protected",
        ),
        (proxy_host(1, ["a.be"], advanced_config="auth_request /oauth2/auth;"), "foreign_auth_request"),
        (
            proxy_host(1, ["a.be"], advanced_config="location /outpost.goauthentik.io {\n}\n"),
            "outpost_location_exists",
        ),
        (proxy_host(1, ["a.be"], enabled=False), "host_disabled"),
        (proxy_host(1, ["a.be"], nginx_online=False), "nginx_offline"),
        (proxy_host(1, ["a.be"], advanced_config="if ($x) {\n"), "syntax"),
    ],
)
def test_protect_refuses_unsafe_hosts(host, code):
    plan = plan_protect(host, OUTPOST)
    assert plan.blocked and code in {c.code for c in plan.checks}, plan.checks
    assert plan.after == plan.before


def test_protect_needs_outpost_and_is_idempotent():
    host = proxy_host(1, ["a.be"])
    assert {c.code for c in plan_protect(host, None).checks} == {"no_outpost_url"}
    protected = _apply(host, plan_protect(host, OUTPOST))
    assert {c.code for c in plan_protect(protected, OUTPOST).checks} == {"already_managed"}
    assert "not_managed" in {c.code for c in plan_unprotect(host).checks}


def test_no_tls_is_a_warning():
    plan = plan_protect(proxy_host(1, ["a.be"]), OUTPOST)
    assert not plan.blocked
    assert [c.code for c in plan.checks if c.level == "warn"] == ["no_tls", "block_exploits_http"]
    host = proxy_host(1, ["a.be"])
    host["block_exploits"] = False
    assert [c.code for c in plan_protect(host, OUTPOST).checks if c.level == "warn"] == ["no_tls"]


def test_app_with_own_login_is_a_warning():
    host = proxy_host(1, ["a.be"], advanced_config="# vaultx.auth = oidc\n", certificate_id=1)
    host["ssl_forced"] = True
    plan = plan_protect(host, OUTPOST)
    assert not plan.blocked
    assert [c.code for c in plan.checks if c.level == "warn"] == ["app_has_login"]


def test_applied_compares_what_npm_returns():
    host = proxy_host(1, ["a.be"])
    plan = plan_protect(host, OUTPOST)
    assert applied(plan, _apply(host, plan))
    assert not applied(plan, host)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://authentik-server:9000/", "http://authentik-server:9000"),
        ("https://auth.example.be", "https://auth.example.be"),
        ("http://192.168.1.5:9000", "http://192.168.1.5:9000"),
    ],
)
def test_outpost_url_accepted(url, expected):
    assert normalize_outpost_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "authentik:9000",
        "http://authentik:9000/outpost.goauthentik.io",
        "http://a;b:9000",
        "http://a b",
        'http://a"b',
        "http://a:99999",
        "ftp://a",
    ],
)
def test_outpost_url_rejected(url):
    with pytest.raises(ValueError):
        normalize_outpost_url(url)
