"""Uniform error envelope. Public responses never carry stack traces, provider details or input."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from wta_common.logging import get_logger
from wta_common.protocol import ErrorBody, ErrorResponse

from wta_api.middleware import SECURITY_HEADERS

log = get_logger("wta_api.errors")


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(code)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


def error_response(
    request: Request, status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code, message=message, request_id=getattr(request.state, "request_id", None)
        )
    )
    # Unhandled errors are rendered by Starlette's outermost middleware, which sits outside ours,
    # so these headers are added here to cover every error path.
    merged = {**SECURITY_HEADERS, **(headers or {})}
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        merged["X-Request-ID"] = request_id
    origin = getattr(request.state, "cors_origin", None)
    if origin:  # let an allowed widget origin read the error body
        merged.update({"Access-Control-Allow-Origin": origin, "Vary": "Origin"})
    return JSONResponse(body.model_dump(), status_code=status, headers=merged)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default echoes the submitted input back; we return a fixed message instead.
        return error_response(request, 400, "invalid_request", "The request is not valid.")

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed"}
        return error_response(
            request, exc.status_code, codes.get(exc.status_code, "http_error"), "Request failed."
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        return error_response(request, 500, "internal_error", "Something went wrong.")
