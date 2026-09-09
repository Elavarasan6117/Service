"""Application error types and the single error envelope.

Every 4xx/5xx leaving this application has the same JSON shape, carries a
machine-readable code, and includes the request id that ties it to the logs.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for all deliberate application errors."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "APP_ERROR"
    message: str = "Application error"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: list[dict[str, Any]] | None = None,
        code: str | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or []
        if code:
            self.code = code
        super().__init__(self.message)


class ValidationError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "VALIDATION_ERROR"
    message = "The request failed validation."


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"
    message = "The requested resource was not found."


class DuplicateResourceError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "DUPLICATE_RESOURCE"
    message = "A resource with that identifier already exists."


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"
    message = "Your role does not permit this action."


class UnauthenticatedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHENTICATED"
    message = "Authentication is required."


class RateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "RATE_LIMITED"
    message = "Too many requests. Please slow down."

    def __init__(self, retry_after: int = 60, **kwargs: Any) -> None:
        self.retry_after = retry_after
        super().__init__(**kwargs)


# --- Provider errors ----------------------------------------------------


class ProviderError(AppError):
    """Base for external-provider failures.

    IMPORTANT: a ProviderError must never be translated into a NOT_AVAILABLE
    serviceability decision. The engine catches these explicitly and records
    ROUTE_CALCULATION_ERROR instead. See docs/04-serviceability-algorithm.md §6.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "PROVIDER_UNAVAILABLE"
    message = "An upstream provider is unavailable."
    retryable = True


class RoutingProviderError(ProviderError):
    code = "ROUTING_PROVIDER_UNAVAILABLE"
    message = "Unable to calculate driving distance. Please retry."


class RoutingTimeoutError(RoutingProviderError):
    code = "ROUTING_TIMEOUT"
    message = "The routing provider did not respond in time. Please retry."


class RoutingRateLimitError(RoutingProviderError):
    code = "ROUTING_RATE_LIMITED"
    message = "The routing provider rate limit was reached. Please retry shortly."


class CircuitOpenError(RoutingProviderError):
    code = "ROUTING_CIRCUIT_OPEN"
    message = (
        "Routing is temporarily disabled after repeated failures. Please retry shortly."
    )


class GeocodingError(ProviderError):
    code = "GEOCODING_FAILED"
    message = (
        "We could not confirm this address. "
        "Please place the marker on the map and confirm the location."
    )


class GeocodingNoResultError(GeocodingError):
    code = "GEOCODING_NO_RESULT"


class GeocodingAmbiguousError(GeocodingError):
    code = "GEOCODING_AMBIGUOUS"
    message = (
        "This address matched more than one location. "
        "Please confirm the correct point on the map."
    )


class InvalidDistanceTypeError(AppError):
    """Raised if anything tries to decide serviceability from a non-road distance.

    This is a programming-error guard, not a user-facing condition. It exists
    because the single most damaging bug this system could have is silently
    deciding on straight-line distance.
    """

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "INVALID_DISTANCE_TYPE"
    message = "Serviceability can only be decided from an actual road distance."


class ConfigurationError(AppError):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "CONFIGURATION_ERROR"
    message = "The application is misconfigured."


# --- Envelope -----------------------------------------------------------


def _envelope(
    code: str,
    message: str,
    request: Request,
    details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
            "requestId": getattr(request.state, "request_id", None),
        }
    }


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    headers = {}
    if isinstance(exc, RateLimitedError):
        headers["Retry-After"] = str(exc.retry_after)
    logger.warning(
        "app_error",
        code=exc.code,
        message=exc.message,
        path=request.url.path,
        status_code=exc.status_code,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.code, exc.message, request, exc.details),
        headers=headers,
    )


async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code_map = {
        401: "UNAUTHENTICATED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        409: "DUPLICATE_RESOURCE",
        429: "RATE_LIMITED",
    }
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(
            code_map.get(exc.status_code, "HTTP_ERROR"), str(exc.detail), request
        ),
        headers=getattr(exc, "headers", None),
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = [
        {
            "field": ".".join(str(p) for p in err.get("loc", []) if p != "body"),
            "issue": err.get("type", "invalid"),
            "message": err.get("msg", ""),
        }
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=jsonable_encoder(
            _envelope(
                "VALIDATION_ERROR", "The request failed validation.", request, details
            )
        ),
    )


async def integrity_error_handler(
    request: Request, exc: IntegrityError
) -> JSONResponse:
    # Unique-constraint violations are a client problem (409), not a server bug.
    text = str(getattr(exc, "orig", exc))
    if "unique" in text.lower() or "duplicate key" in text.lower():
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_envelope(
                "DUPLICATE_RESOURCE",
                "A record with that identifier already exists.",
                request,
            ),
        )
    logger.error("integrity_error", error=text, path=request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope("DATABASE_ERROR", "A database error occurred.", request),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Never leak internals to the client; the request id links to the full trace.
    logger.exception(
        "unhandled_exception", path=request.url.path, error=str(exc)
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope(
            "INTERNAL_ERROR",
            "An unexpected error occurred. Please contact support with the request id.",
            request,
        ),
    )
