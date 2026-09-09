"""FastAPI application factory and lifecycle."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.exc import IntegrityError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import middleware
from app.api.v1 import api_router
from app.api.v1 import health as health_router
from app.core.config import settings
from app.core.errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    integrity_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine
from app.providers.registry import (
    close_providers,
    get_geocoding_provider,
    get_routing_provider,
)
from app.services.cache import close_redis

configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build providers at startup so a misconfiguration fails immediately and
    # loudly, rather than on the first customer an operator tries to create.
    routing = get_routing_provider()
    geocoding = get_geocoding_provider()
    logger.info(
        "application_starting",
        environment=settings.ENVIRONMENT,
        routing_provider=routing.name,
        geocoding_provider=geocoding.name,
    )
    yield
    logger.info("application_stopping")
    await close_providers()
    await close_redis()
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        description=(
            "Determines whether a new customer is serviceable based on ACTUAL "
            "ROAD DISTANCE to the nearest existing service location. "
            "Straight-line distance is used only to pre-select candidates and "
            "never to make the decision."
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url=f"{settings.API_V1_PREFIX}/docs" if settings.ENABLE_API_DOCS else None,
        redoc_url=f"{settings.API_V1_PREFIX}/redoc" if settings.ENABLE_API_DOCS else None,
        openapi_url=(
            f"{settings.API_V1_PREFIX}/openapi.json" if settings.ENABLE_API_DOCS else None
        ),
    )

    middleware.install(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    if settings.is_production:
        app.add_middleware(
            TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list
        )

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    app.include_router(health_router.router)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    if settings.METRICS_ENABLED:

        @app.get("/metrics", include_in_schema=False)
        async def metrics(request: Request) -> PlainTextResponse:
            # Bind this to an internal network at the proxy or firewall; the
            # metric labels reveal internal endpoint structure.
            return PlainTextResponse(
                generate_latest().decode("utf-8"), media_type=CONTENT_TYPE_LATEST
            )

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "service": settings.APP_NAME,
            "version": "1.0.0",
            "docs": f"{settings.API_V1_PREFIX}/docs" if settings.ENABLE_API_DOCS else None,
            "rule": "road_distance <= threshold => AVAILABLE",
        }

    return app


app = create_app()
