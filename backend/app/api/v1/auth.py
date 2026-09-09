from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, login_rate_limit
from app.core.config import settings
from app.core.enums import ROLE_PERMISSIONS
from app.core.errors import UnauthenticatedError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    create_stream_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.schemas.common import MessageResponse
from app.models.user import User
from app.schemas.misc import (
    LoginRequest,
    RefreshRequest,
    StreamTokenResponse,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger(__name__)

MAX_FAILED_LOGINS = 10
LOCKOUT_MINUTES = 15

# A real bcrypt hash of a random string, computed once at import.
#
# It exists so that a login attempt for a non-existent username performs the
# same work as one for a real user. Without it, "no such user" returns in
# microseconds while a wrong password takes ~100 ms, and that difference is
# enough to enumerate valid usernames by timing alone. It must be a genuine
# hash -- a hand-built string is rejected by passlib and returns immediately,
# which defeats the entire purpose.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    session: DbSession,
    _: None = Depends(login_rate_limit),
) -> TokenResponse:
    result = await session.execute(
        select(User).where(User.username == payload.username)
    )
    user = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)

    # One generic message for every failure mode. Distinguishing "no such user"
    # from "wrong password" would let an attacker enumerate valid accounts.
    generic = UnauthenticatedError("Incorrect username or password.")

    if user is None:
        # Burn the same time a real verification would, so the response time
        # does not reveal whether the username exists.
        verify_password(payload.password, _DUMMY_PASSWORD_HASH)
        raise generic

    if user.locked_until and user.locked_until > now:
        raise UnauthenticatedError(
            "This account is temporarily locked after repeated failed sign-ins. "
            "Please try again later or contact an administrator."
        )

    if not user.is_active:
        raise generic

    if not verify_password(payload.password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            logger.warning(
                "account_locked",
                username=user.username,
                failures=user.failed_login_count,
            )
        await session.commit()
        raise generic

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    await session.commit()

    logger.info("login_success", username=user.username, role=user.role.value)

    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role.value, user.username),
        refresh_token=create_refresh_token(str(user.id)),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, session: DbSession) -> TokenResponse:
    claims = decode_token(payload.refresh_token, "refresh")
    user = await session.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise UnauthenticatedError("This account is no longer active.")

    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role.value, user.username),
        refresh_token=create_refresh_token(str(user.id)),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def logout(user: CurrentUser) -> MessageResponse:
    # Tokens are stateless and short-lived; the client discards them. A
    # server-side denylist would be the next step if immediate revocation
    # becomes a requirement.
    logger.info("logout", username=user.username)
    return MessageResponse(message="Signed out.")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        permissions=sorted(p.value for p in ROLE_PERMISSIONS.get(user.role, [])),
    )


@router.post("/stream-token", response_model=StreamTokenResponse)
async def stream_token(user: CurrentUser) -> StreamTokenResponse:
    """Short-lived token for the SSE endpoint.

    EventSource cannot send an Authorization header, so the dashboard fetches
    this with its normal bearer token and puts the result in the query string.
    Scoped to the stream and valid for five minutes.
    """
    return StreamTokenResponse(
        stream_token=create_stream_token(str(user.id), user.role.value),
        expires_in=300,
    )
