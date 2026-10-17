from uuid import UUID

from fastapi import APIRouter, Request

from app.api.deps import CurrentPrincipal, SettingsDep
from app.schemas.catalog import AuthentikOutpostsOut
from app.services.authentik import AuthentikService

router = APIRouter(prefix="/organizations/{org_id}/authentik", tags=["authentik"])


@router.get(
    "/outposts",
    response_model=AuthentikOutpostsOut,
    summary="Proxy-outposts in Authentik, om op een NPM-koppeling te kiezen",
)
async def list_outposts(org_id: UUID, request: Request, p: CurrentPrincipal, settings: SettingsDep):
    # Tests zetten app.state.authentik_client_factory om tegen een nep-Authentik te praten.
    svc = AuthentikService(settings, getattr(request.app.state, "authentik_client_factory", None))
    return await svc.outposts(p, org_id)
