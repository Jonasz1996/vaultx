"""VaultX phase-0: FastAPI-applicatie."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import auth, health
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.context import RequestIdMiddleware
from app.core.db import dispose_engine
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.services.oidc import OIDCProvider


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if not hasattr(app.state, "oidc"):
        app.state.oidc = OIDCProvider(settings)
    try:
        yield
    finally:
        await app.state.oidc.aclose()
        await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="VaultX",
        version=__version__,
        description=(
            "VaultX phase-0: identiteit (Authentik/OIDC), multi-tenancy en audit. "
            "Nog geen kluisfunctionaliteit."
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(api_router)
    return app


app = create_app()
