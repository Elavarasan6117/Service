"""Request middleware: correlation id, access logging, metrics, security headers."""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import http_request_duration_seconds, http_requests_total

logger = get_logger("http")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, bind it to the logger, time the request.

    The request id is returned in X-Request-ID and appears in every log line and
    every error envelope, so a user reporting "it failed" can be traced to the
    exact request without guesswork.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed = time.perf_counter() - start
            logger.exception("request_failed", duration_ms=round(elapsed * 1000, 2))
            raise

        elapsed = time.perf_counter() - start
        response.headers["X-Request-ID"] = request_id

        # Use the route template, not the raw path, so metric cardinality does
        # not explode with one label value per customer id.
        route = request.scope.get("route")
        endpoint = getattr(route, "path", request.url.path)

        if settings.METRICS_ENABLED:
            http_requests_total.labels(
                method=request.method,
                endpoint=endpoint,
                status_class=f"{response.status_code // 100}xx",
            ).inc()
            http_request_duration_seconds.labels(
                method=request.method, endpoint=endpoint
            ).observe(elapsed)

        if request.url.path not in ("/health", "/metrics"):
            # A plain string set by get_current_user, not a lazy read off the
            # ORM user object: some endpoints roll back their DB session
            # before returning (to discard preview-only side effects), which
            # expires every attribute on every object still attached to that
            # session -- reading user.username here, after the session has
            # closed, would raise DetachedInstanceError and turn a successful
            # request into a 500 in the middleware, invisibly to the endpoint.
            logger.info(
                "request",
                status_code=response.status_code,
                duration_ms=round(elapsed * 1000, 2),
                username=getattr(request.state, "username", None),
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(self), microphone=(), camera=()"
        )
        if settings.FORCE_HTTPS:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


def install(app: ASGIApp) -> None:  # pragma: no cover - wiring
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestContextMiddleware)
