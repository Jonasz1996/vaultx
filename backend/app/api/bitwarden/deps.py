"""Gemeenschappelijke stukken voor de Bitwarden-routes: foutformaat en authenticatie."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.api.deps import DbSession, MetaDep, SettingsDep
from app.core.errors import NotFoundError, VaultXError
from app.schemas.bitwarden import api_error_json, identity_error_json
from app.services.vault import StaleCipherError
from app.services.vault_auth import IdentityError, VaultAuthError, VaultAuthService, VaultCaller


def _validation_message(exc: RequestValidationError) -> tuple[str, dict[str, list[str]]]:
    fields: dict[str, list[str]] = {}
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()) if x not in ("body", "query", "path"))
        fields.setdefault(loc or "request", []).append(str(err.get("msg", "ongeldig")))
    first = next(iter(fields.items()), ("request", ["ongeldig"]))
    return f"{first[0]}: {first[1][0]}", fields


class BitwardenRoute(APIRoute):
    """Zet fouten om naar het formaat dat de Bitwarden-clients tonen (ErrorResponse)."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def wrapped(request: Request) -> Response:
            try:
                return await handler(request)
            except IdentityError as exc:
                return JSONResponse(
                    identity_error_json(exc.error, exc.description, exc.message), status_code=400
                )
            except VaultAuthError as exc:
                return JSONResponse(
                    api_error_json(str(exc)), status_code=401, headers={"WWW-Authenticate": "Bearer"}
                )
            except StaleCipherError:
                return JSONResponse(
                    api_error_json(
                        "De kopie van dit item op je toestel is verouderd. Synchroniseer en probeer opnieuw."
                    ),
                    status_code=400,
                )
            except RequestValidationError as exc:
                message, fields = _validation_message(exc)
                return JSONResponse(api_error_json(message, fields), status_code=400)
            except VaultXError as exc:
                status = 404 if isinstance(exc, NotFoundError) else 400
                if exc.status_code in (401, 403):
                    status = exc.status_code
                return JSONResponse(api_error_json(exc.message), status_code=status)

        return wrapped


def vault_enabled(settings: SettingsDep) -> None:
    if not settings.vault_enabled:
        raise NotFoundError("De kluis is uitgeschakeld op deze server")


async def vault_caller(request: Request, db: DbSession, settings: SettingsDep, meta: MetaDep) -> VaultCaller:
    authz = request.headers.get("authorization", "")
    if not authz.lower().startswith("bearer "):
        raise VaultAuthError("Niet ingelogd")
    return await VaultAuthService(db, settings).authenticate(authz[7:].strip(), meta)


Caller = Annotated[VaultCaller, Depends(vault_caller)]
