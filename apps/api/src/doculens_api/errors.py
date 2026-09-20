"""Consistent API error format (SPECIFICATIONS.md §35).

Every failing response, whatever raised it, has the shape::

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}

``details`` is present only for request validation failures. Internal exception details are never
returned; unexpected errors are logged with their traceback and answered with a generic message.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Self

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from doculens.domain.errors import (
    ConflictError,
    DomainError,
    InvalidInputError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    UnauthenticatedError,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

DOMAIN_ERROR_STATUS: Mapping[type[DomainError], HTTPStatus] = {
    InvalidInputError: HTTPStatus.BAD_REQUEST,
    UnauthenticatedError: HTTPStatus.UNAUTHORIZED,
    NotFoundError: HTTPStatus.NOT_FOUND,
    ConflictError: HTTPStatus.CONFLICT,
    PermissionDeniedError: HTTPStatus.FORBIDDEN,
    RateLimitedError: HTTPStatus.TOO_MANY_REQUESTS,
}
BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}
DEFAULT_DOMAIN_ERROR_STATUS = HTTPStatus.BAD_REQUEST


class ErrorDetail(BaseModel):
    location: str = Field(description="Dotted path to the offending field, e.g. `query.limit`.")
    message: str
    type: str


class ErrorBody(BaseModel):
    code: str = Field(description="Stable, machine-readable error code.")
    message: str = Field(description="Safe, user-facing description.")
    request_id: str = Field(description="Correlation ID, also returned in the response header.")
    details: list[ErrorDetail] | None = Field(default=None, description="Validation problems.")


class ErrorResponse(BaseModel):
    error: ErrorBody


DEFAULT_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    HTTPStatus.UNPROCESSABLE_ENTITY: {
        "model": ErrorResponse,
        "description": "Request validation failed.",
    },
    HTTPStatus.INTERNAL_SERVER_ERROR: {
        "model": ErrorResponse,
        "description": "Unexpected server error.",
    },
}


@dataclass(frozen=True, slots=True)
class ApiError:
    """What the client is told: a status, a stable code and a safe message."""

    status_code: int
    code: str
    message: str

    @classmethod
    def from_domain_error(cls, error: DomainError) -> Self:
        return cls(status_code=status_for(error), code=error.code, message=error.message)

    @classmethod
    def from_status(cls, status: HTTPStatus) -> Self:
        return cls(status_code=status, code=status.name, message=status.phrase)


VALIDATION_ERROR = ApiError(
    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
    code="VALIDATION_ERROR",
    message="Request validation failed.",
)
INTERNAL_ERROR = ApiError(
    status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
    code="INTERNAL_ERROR",
    message="An unexpected error occurred.",
)


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def status_for(error: DomainError) -> HTTPStatus:
    for error_type in type(error).__mro__:
        if error_type in DOMAIN_ERROR_STATUS:
            return DOMAIN_ERROR_STATUS[error_type]
    return DEFAULT_DOMAIN_ERROR_STATUS


def error_response(
    request: Request,
    error: ApiError,
    *,
    header_name: str,
    details: list[ErrorDetail] | None = None,
    extra_headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = request_id_of(request)
    body = ErrorResponse(
        error=ErrorBody(
            code=error.code, message=error.message, request_id=request_id, details=details
        )
    )
    headers = {**(extra_headers or {}), header_name: request_id}
    return JSONResponse(
        status_code=error.status_code,
        content=body.model_dump(exclude_none=True),
        headers=headers,
    )


def register_error_handlers(app: FastAPI, *, header_name: str) -> None:
    async def handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
        error = exc if isinstance(exc, DomainError) else DomainError()
        logger.info("domain error", operation="http.error", error_code=error.code)
        api_error = ApiError.from_domain_error(error)
        extra_headers: dict[str, str] | None = None
        if api_error.status_code == HTTPStatus.UNAUTHORIZED:
            extra_headers = BEARER_CHALLENGE
        elif isinstance(error, RateLimitedError):
            extra_headers = {"Retry-After": str(error.retry_after_seconds)}
        return error_response(
            request, api_error, header_name=header_name, extra_headers=extra_headers
        )

    async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
        details = [
            ErrorDetail(
                location=".".join(str(part) for part in item["loc"]),
                message=str(item["msg"]),
                type=str(item["type"]),
            )
            for item in (exc.errors() if isinstance(exc, RequestValidationError) else [])
        ]
        return error_response(request, VALIDATION_ERROR, header_name=header_name, details=details)

    async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, StarletteHTTPException):
            # Starlette validates the status code on construction, so HTTPStatus() cannot fail.
            error = ApiError.from_status(HTTPStatus(exc.status_code))
            extra_headers = exc.headers
        else:
            error = ApiError.from_status(HTTPStatus.INTERNAL_SERVER_ERROR)
            extra_headers = None
        return error_response(request, error, header_name=header_name, extra_headers=extra_headers)

    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled error",
            operation="http.error",
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return error_response(request, INTERNAL_ERROR, header_name=header_name)

    app.add_exception_handler(DomainError, handle_domain_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected_error)
