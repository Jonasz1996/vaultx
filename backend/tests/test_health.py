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
