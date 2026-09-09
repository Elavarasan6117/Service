from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from app.api.deps import ConfigDep, DbSession, require
from app.core.enums import Permission
from app.core.errors import DuplicateResourceError, NotFoundError
from app.core.security import hash_password
from app.providers.registry import get_geocoding_provider, get_routing_provider
from app.schemas.misc import (
    ConfigAuditOut,
    ConfigOut,
    ConfigUpdate,
    RoutingUsageOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services.cache import RouteCache
from app.services.events import EventType, publish
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])


# --- Configuration ------------------------------------------------------


@router.get("/config", response_model=list[ConfigOut])
async def list_config(
    config: ConfigDep,
    user: User = Depends(require(Permission.CONFIG_READ)),
) -> list[ConfigOut]:
    return [ConfigOut.model_validate(row) for row in await config.all()]


@router.put("/config/{key}", response_model=ConfigOut)
async def update_config(
    key: str,
    payload: ConfigUpdate,
    session: DbSession,
    config: ConfigDep,
    user: User = Depends(require(Permission.CONFIG_READ)),
) -> ConfigOut:
    """Change a configuration value.

    Permission is checked inside ConfigService against the key's own
    ``requires_admin`` flag, so the serviceability threshold is ADMIN-only while
    lower-risk settings are open to supervisors. A reason is mandatory and is
    written to the audit trail together with the old and new values.
    """
    await config.require_exists(key)
    row = await config.set(key, payload.value, actor=user, reason=payload.reason)
    await session.commit()
    await session.refresh(row)

    await publish(
        EventType.CONFIG_UPDATED,
        {"key": key, "value": row.value, "changedBy": user.username},
    )
    return ConfigOut.model_validate(row)


@router.get("/config/{key}/history", response_model=list[ConfigAuditOut])
async def config_history(
    key: str,
    config: ConfigDep,
    user: User = Depends(require(Permission.CONFIG_READ)),
) -> list[ConfigAuditOut]:
    return [ConfigAuditOut.model_validate(a) for a in await config.history(key)]


# --- Provider health and usage -----------------------------------------


@router.get("/routing/usage", response_model=RoutingUsageOut)
async def routing_usage(
    user: User = Depends(require(Permission.CONFIG_READ)),
) -> RoutingUsageOut:
    provider = get_routing_provider()
    cache_stats = await RouteCache(provider.name).stats()
    return RoutingUsageOut(
        provider=provider.name,
        healthy=await provider.health_check(),
        cache=cache_stats,
    )


@router.post("/routing/health")
async def routing_health(
    user: User = Depends(require(Permission.CONFIG_READ)),
) -> dict:
    routing = get_routing_provider()
    geocoding = get_geocoding_provider()
    return {
        "routing": {"provider": routing.name, "healthy": await routing.health_check()},
        "geocoding": {
            "provider": geocoding.name,
            "healthy": await geocoding.health_check(),
        },
    }


# --- Users --------------------------------------------------------------


@router.get("/users", response_model=list[UserOut])
async def list_users(
    session: DbSession,
    user: User = Depends(require(Permission.USER_MANAGE)),
) -> list[UserOut]:
    result = await session.execute(select(User).order_by(User.username))
    return [UserOut.model_validate(u) for u in result.scalars().all()]


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    session: DbSession,
    user: User = Depends(require(Permission.USER_MANAGE)),
) -> UserOut:
    existing = await session.execute(
        select(User).where(
            (User.username == payload.username) | (User.email == payload.email)
        )
    )
    if existing.scalar_one_or_none():
        raise DuplicateResourceError("That username or email is already registered.")

    new_user = User(
        username=payload.username,
        email=payload.email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    session.add(new_user)
    await session.commit()
    await session.refresh(new_user)
    return UserOut.model_validate(new_user)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    session: DbSession,
    user: User = Depends(require(Permission.USER_MANAGE)),
) -> UserOut:
    target = await session.get(User, user_id)
    if target is None:
        raise NotFoundError(f"No user with id {user_id}.")

    data = payload.model_dump(exclude_unset=True)
    password = data.pop("password", None)
    for field, value in data.items():
        setattr(target, field, value)
    if password:
        target.password_hash = hash_password(password)
        # A password reset also clears a lockout, which is the usual reason an
        # administrator is resetting it in the first place.
        target.failed_login_count = 0
        target.locked_until = None

    await session.commit()
    await session.refresh(target)
    return UserOut.model_validate(target)
