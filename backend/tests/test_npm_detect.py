from app.core.security import open_secret, seal_secret
from app.services.npm_client import normalize_base_url
from app.services.npm_detect import analyze, fingerprint, parse_labels
from tests.fake_npm import proxy_host

AUTHENTIK_LOCATION = {
    "path": "/",
    "forward_scheme": "http",
    "forward_host": "grafana",
    "forward_port": 3000,
    "advanced_config": (
        "auth_request /outpost.goauthentik.io/auth/nginx;\n"
        "error_page 401 = @goauthentik_proxy_signin;\n"
        "auth_request_set $authentik_username $upstream_http_x_authentik_username;\n"
    ),
}


def codes(det):
    return {w["code"] for w in det.warnings}


def test_labels_from_comments_first_wins():
    labels = parse_labels(
        "proxy_buffers 8 16k;\n# vaultx.app = Grafana\n#vaultx.auth: oidc\n  # VaultX.Tags = a, b \n",
        "# vaultx.app = Other",
    )
    assert labels == {"app": "Grafana", "auth": "oidc", "tags": "a, b"}


def test_example_from_jonas_grafana_with_oidc_label():
    det = analyze(
        proxy_host(
            1,
            ["grafana.domain.be"],
            forward_host="10.0.0.5",
            forward_port=3000,
            advanced_config="# vaultx.auth = oidc\n",
            certificate_id=3,
            ssl_forced=True,
        )
    )
    assert (det.name, det.app_type, det.auth_method, det.url) == (
        "Grafana",
        "grafana",
        "oidc",
        "https://grafana.domain.be",
    )
    assert det.warnings == []


def test_authentik_forward_auth_in_custom_location():
    det = analyze(proxy_host(2, ["dash.example.be"], forward_host="grafana", locations=[AUTHENTIK_LOCATION]))
    assert det.forward_auth
    assert det.auth_method == "forward_auth"
    assert det.app_type == "grafana"  # via forward host, het subdomein zegt niets
    assert "no_tls" in codes(det)


def test_commented_auth_request_does_not_count():
    det = analyze(
        proxy_host(3, ["x.example.be"], advanced_config="# auth_request /outpost.goauthentik.io/auth;")
    )
    assert not det.forward_auth
    assert det.auth_method == "unknown"


def test_auth_request_off_does_not_count():
    det = analyze(proxy_host(3, ["x.example.be"], advanced_config="location /api { auth_request off; }"))
    assert not det.forward_auth


def test_satisfy_any_and_custom_root_location_warnings():
    det = analyze(
        proxy_host(
            4,
            ["ha.example.be"],
            forward_port=8123,
            advanced_config="location / {\n  auth_request /outpost.goauthentik.io/auth/nginx;\n}\n",
            access_list={"id": 1, "name": "LAN", "satisfy_any": True, "pass_auth": False},
            certificate_id=1,
        )
    )
    assert det.app_type == "home-assistant"
    assert {"satisfy_any", "custom_root_location", "tls_not_forced"} <= codes(det)


def test_access_list_without_forward_auth():
    det = analyze(proxy_host(5, ["files.example.be"], access_list={"id": 2, "name": "Familie"}))
    assert det.auth_method == "access_list"
    assert det.name == "Files"


def test_invalid_and_unknown_labels_warn():
    det = analyze(
        proxy_host(6, ["a.example.be"], advanced_config="# vaultx.auth = magic\n# vaultx.colour = red")
    )
    assert det.auth_method == "unknown"
    assert {"label_invalid", "label_unknown"} <= codes(det)


def test_ignore_label_and_offline_host():
    det = analyze(
        proxy_host(7, ["old.example.be"], advanced_config="# vaultx.ignore = ja", nginx_online=False)
    )
    assert det.ignore
    assert "nginx_offline" in codes(det)


def test_fingerprint_order_and_ports():
    assert fingerprint(["node-red.lan"], "x", 1).key == "node-red"
    assert fingerprint(["random.lan"], "jellyfin", 1).key == "jellyfin"
    assert fingerprint(["random.lan"], "10.0.0.3", 8006).key == "proxmox"
    assert fingerprint(["random.lan"], "10.0.0.3", 3000) is None
    assert fingerprint(["*.wild.lan"], "x", 1) is None


def test_base_url_normalization():
    assert normalize_base_url(" http://npm:81/api/ ") == "http://npm:81"
    assert normalize_base_url("https://npm.lan") == "https://npm.lan"


def test_secret_box_roundtrip_and_context_binding():
    sealed = seal_secret("k" * 40, "geheim", "npm:1")
    assert b"geheim" not in sealed
    assert open_secret("k" * 40, sealed, "npm:1") == "geheim"
    for key, ctx in (("k" * 40, "npm:2"), ("x" * 40, "npm:1")):
        try:
            open_secret(key, sealed, ctx)
        except Exception:
            continue
        raise AssertionError("ontsleutelen had moeten mislukken")
