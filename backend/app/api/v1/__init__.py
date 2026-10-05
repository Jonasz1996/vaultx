from fastapi import APIRouter

from app.api.v1 import audit, me, organizations, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(me.router)
api_router.include_router(users.router)
api_router.include_router(organizations.router)
api_router.include_router(audit.router)
