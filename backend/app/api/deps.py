"""FastAPI dependencies: authentication, authorization, rate limiting, wiring."""

from __future__ import annotations

import time
import uuid
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import Permission
from app.core.errors import ForbiddenError, RateLimitedError, UnauthenticatedError
from app.core.logging import get_logger
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User
from app.providers.registry import get_geocoding_provider, get_routing_provider
from app.services.cache import get_redis
from app.services.config_service import ConfigService
from app.services.customer_service import CustomerService
from app.services.serviceability import ServiceabilityEngine

logger = get_logger(__name__)

_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


# --- Authentication -----------------------------------------------------


async def get_current_user(
    request: Request,
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise UnauthenticatedError("An access token is required.")

    payload = decode_token(credentials.credentials, "access")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (ValueError, KeyError) as exc:
        raise UnauthenticatedError("Malformed token subject.") from exc

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        # Covers a user deactivated after their token was issued.
        raise UnauthenticatedError("This account is no longer active.")

    # A plain copy, not a lazy attribute read off the ORM object.
    # RequestContextMiddleware logs this after call_next() returns, by which
    # point the request's DB session has closed -- and some endpoints (e.g.
    # the serviceability preview, which explicitly rolls back to discard
    # preview side effects) expire every attribute on every object still tied
    # to that session before returning. Reading user.username at that point
    # would raise DetachedInstanceError and turn an otherwise-successful
    # request into a 500 for reasons invisible to the endpoint's own code.
    request.state.username = user.username

    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require(permission: Permission):
    """Dependency factory enforcing one permission."""

    async def _dependency(user: CurrentUser) -> User:
        if not user.has(permission):
            logger.warning(
                "authorization_denied",
                username=user.username,
                role=user.role.value,
                permission=permission.value,
            )
            raise ForbiddenError(
                f"Your role ({user.role.value}) does not permit this action."
            )
        return user

    return _dependency


# --- Rate limiting ------------------------------------------------------


async def _consume_token(bucket: str, limit: int, window_seconds: int) -> None:
    """Fixed-window counter in Redis.

    Shared across API replicas, which a per-process limiter would not be. If
    Redis is unavailable the limiter fails open: throttling is a protection,
    not a correctness requirement, and a cache outage should not lock users out.
    """
    if not settings.RATE_LIMIT_ENABLED:
        return
    try:
        redis = get_redis()
        window = int(time.time() // window_seconds)
        key = f"ratelimit:{bucket}:{window}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window_seconds + 1)
        if count > limit:
            retry_after = window_seconds - int(time.time() % window_seconds)
            raise RateLimitedError(retry_after=max(1, retry_after))
    except RateLimitedError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("rate_limiter_unavailable", error=str(exc))


def rate_limit(limit_per_minute: int, scope: str):
    async def _dependency(request: Request, user: CurrentUser) -> None:
        await _consume_token(f"{scope}:{user.id}", limit_per_minute, 60)

    return _dependency


async def login_rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    await _consume_token(
        f"login:{ip}", settings.RATE_LIMIT_LOGIN_PER_15MIN, 15 * 60
    )


# --- Service wiring -----------------------------------------------------


def get_config_service(session: DbSession) -> ConfigService:
    return ConfigService(session)


def get_serviceability_engine(session: DbSession) -> ServiceabilityEngine:
    return ServiceabilityEngine(session, get_routing_provider())


def get_customer_service(session: DbSession) -> CustomerService:
    return CustomerService(
        session,
        geocoding_provider=get_geocoding_provider(),
        engine=ServiceabilityEngine(session, get_routing_provider()),
    )


ConfigDep = Annotated[ConfigService, Depends(get_config_service)]
EngineDep = Annotated[ServiceabilityEngine, Depends(get_serviceability_engine)]
CustomerServiceDep = Annotated[CustomerService, Depends(get_customer_service)]
