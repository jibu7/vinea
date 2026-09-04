"""One error envelope for the whole API (ADR-11): `{code, message, field_errors}`."""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        field_errors: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.field_errors = field_errors or {}


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


def _envelope(
    code: str, message: str, field_errors: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    return {"code": code, "message": message, "field_errors": field_errors or {}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.field_errors),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        field_errors: dict[str, list[str]] = {}
        for error in exc.errors():
            location = [str(part) for part in error["loc"] if part not in ("body", "query", "path")]
            field_errors.setdefault(".".join(location) or "__root__", []).append(error["msg"])
        return JSONResponse(
            status_code=422,  # starlette renamed its 422 constant; the number is stable
            content=_envelope("validation_error", "Request validation failed", field_errors),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            status.HTTP_401_UNAUTHORIZED: "unauthenticated",
            status.HTTP_403_FORBIDDEN: "permission_denied",
            status.HTTP_404_NOT_FOUND: "not_found",
            status.HTTP_409_CONFLICT: "conflict",
        }.get(exc.status_code, "http_error")
        return JSONResponse(status_code=exc.status_code, content=_envelope(code, str(exc.detail)))
