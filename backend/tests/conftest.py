"""Testinfrastructuur: echte PostgreSQL + fake OIDC-provider over HTTP.

Vereist een PostgreSQL-database in VAULTX_TEST_DATABASE_URL (zie README).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
import pytest_asyncio

from tests.fake_npm import FakeNPM
from tests.fake_oidc import FakeOIDCProvider

BACKEND = Path(__file__).resolve().parents[1]
TEST_DB = os.environ.get(
    "VAULTX_TEST_DATABASE_URL", "postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_test"
)
PUBLIC_URL = "http://testserver"

FAKE = FakeOIDCProvider()
FAKE.start()

os.environ.update(
    {
        "VAULTX_DATABASE_URL": TEST_DB,
        "VAULTX_SECRET_KEY": "test-secret-key-test-secret-key-0123456789",
        "VAULTX_PUBLIC_URL": PUBLIC_URL,
        "VAULTX_OIDC_ISSUER": FAKE.issuer,
        "VAULTX_OIDC_CLIENT_ID": FAKE.client_id,
        "VAULTX_OIDC_CLIENT_SECRET": FAKE.client_secret,
        "VAULTX_OIDC_ADMIN_GROUPS": "vaultx-admins",
        "VAULTX_COOKIE_SECURE": "false",
        "VAULTX_LOG_LEVEL": "WARNING",
        "VAULTX_NPM_SYNC_INTERVAL_MINUTES": "0",
    }
)

from app.core.config import get_settings  # noqa: E402
from app.core.db import get_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.npm_client import NPMClient  # noqa: E402
from app.services.oidc import OIDCProvider  # noqa: E402

TABLES = (
    "vault_ciphers, vault_folders, vault_devices, vault_accounts, "
    "npm_hosts, npm_connections, applications, "
    "user_sessions, memberships, teams, organizations, users, audit_logs"
)


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        check=True,
        env={**os.environ, "VAULTX_DATABASE_URL": TEST_DB},
        capture_output=True,
    )


@pytest_asyncio.fixture(autouse=True)
async def clean_db(migrated_db: None):
    from sqlalchemy import text

    async with get_engine().begin() as conn:
        # De audit-trigger verbiedt TRUNCATE; enkel in tests schakelen we triggers uit.
        await conn.execute(text("SET LOCAL session_replication_role = replica"))
        await conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    yield


@pytest.fixture(scope="session")
def fake_oidc() -> FakeOIDCProvider:
    return FAKE


@pytest.fixture
def fake_npm() -> FakeNPM:
    return FakeNPM()


@pytest_asyncio.fixture
async def app(fake_npm: FakeNPM):
    application = create_app()
    application.state.oidc = OIDCProvider(get_settings())
    application.state.npm_client_factory = lambda conn: NPMClient(
        conn.base_url, verify_tls=conn.verify_tls, transport=fake_npm.transport
    )
    yield application
    await application.state.oidc.aclose()


class VaultXClient(httpx.AsyncClient):
    """httpx-client tegen de ASGI-app, met hulpfuncties voor login."""

    fake: FakeOIDCProvider

    async def login(
        self,
        sub: str,
        *,
        email: str | None = None,
        groups: list[str] | None = None,
        name: str | None = None,
        next: str = "/",
    ) -> httpx.Response:
        self.fake.next_claims = {
            "sub": sub,
            "email": email or f"{sub}@example.com",
            "email_verified": True,
            "preferred_username": sub,
            "name": name or sub.title(),
            "groups": groups or [],
        }
        r = await self.get("/auth/login", params={"next": next})
        assert r.status_code == 303, r.text
        async with httpx.AsyncClient() as idp:
            r2 = await idp.get(r.headers["location"])
        assert r2.status_code == 302
        callback = urlsplit(r2.headers["location"])
        return await self.get(f"{callback.path}?{callback.query}")

    def mutating_headers(self) -> dict[str, str]:
        return {"X-VaultX-CSRF": "1"}

    async def post_json(self, url: str, data: Any) -> httpx.Response:
        return await self.post(url, json=data, headers=self.mutating_headers())

    async def patch_json(self, url: str, data: Any) -> httpx.Response:
        return await self.patch(url, json=data, headers=self.mutating_headers())

    async def delete_(self, url: str) -> httpx.Response:
        return await self.delete(url, headers=self.mutating_headers())


@pytest_asyncio.fixture
async def make_client(app):
    clients: list[VaultXClient] = []

    async def factory() -> VaultXClient:
        c = VaultXClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC_URL)
        c.fake = FAKE
        clients.append(c)
        return c

    yield factory
    for c in clients:
        await c.aclose()


@pytest_asyncio.fixture
async def admin(make_client) -> VaultXClient:
    c = await make_client()
    r = await c.login("admin", groups=["vaultx-admins"])
    assert r.status_code == 303
    return c
