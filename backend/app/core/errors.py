"""Domeinfouten. Services gooien deze; de API vertaalt ze naar HTTP-statussen."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class VaultXError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code


class NotFoundError(VaultXError):
    status_code = 404
    code = "not_found"


class ConflictError(VaultXError):
    status_code = 409
    code = "conflict"


class PermissionDeniedError(VaultXError):
    status_code = 403
    code = "forbidden"


class AuthenticationError(VaultXError):
    status_code = 401
    code = "unauthenticated"


class InvalidOperationError(VaultXError):
    status_code = 422
    code = "invalid_operation"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(VaultXError)
    async def _handle(request: Request, exc: VaultXError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "detail": exc.message},
            headers=headers,
        )
