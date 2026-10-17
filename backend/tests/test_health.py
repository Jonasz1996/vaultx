async def test_health(make_client):
    c = await make_client()
    r = await c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.headers["x-request-id"]


async def test_ready_checks_database(make_client):
    c = await make_client()
    r = await c.get("/health/ready")
    assert r.json() == {"status": "ok", "database": "up"}


async def test_openapi_available(make_client):
    c = await make_client()
    r = await c.get("/api/openapi.json")
    assert r.status_code == 200
    assert "/api/v1/organizations" in r.json()["paths"]


def test_insecure_settings_are_listed():
    from app.core.config import get_settings

    s = get_settings().model_copy(
        update={"cookie_secure": False, "public_url": "http://vaultx.lan", "oidc_verify_tls": True}
    )
    problems = s.insecure_settings()
    assert any("COOKIE_SECURE" in p for p in problems) and any("PUBLIC_URL" in p for p in problems)
    ok = s.model_copy(
        update={"cookie_secure": True, "public_url": "https://vaultx.example", "authentik_verify_tls": True}
    )
    assert ok.insecure_settings() == []


def test_log_lines_carry_the_request_id():
    import logging

    from app.core.context import current_request_id
    from app.core.logging import _RequestIdFilter

    token = current_request_id.set("abc123")
    try:
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
        assert _RequestIdFilter().filter(record) and record.request_id == "abc123"
    finally:
        current_request_id.reset(token)
