from fastapi import APIRouter

from app.api.v1 import app_login, audit, authentik, catalog, me, npm, organizations, users, vault

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(me.router)
api_router.include_router(vault.router)
api_router.include_router(users.router)
api_router.include_router(organizations.router)
api_router.include_router(catalog.router)
api_router.include_router(npm.router)
api_router.include_router(authentik.router)
api_router.include_router(app_login.router)
api_router.include_router(audit.router)
