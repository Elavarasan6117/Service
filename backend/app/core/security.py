"""Password hashing and JWT issuing/verification."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings
from app.core.errors import UnauthenticatedError

# bcrypt is used directly rather than through passlib.
#
# passlib 1.7.4 (unmaintained since 2020) probes bcrypt.__about__.__version__,
# which bcrypt 4.1+ removed. It traps the resulting AttributeError but prints a
# full traceback at import, which looks like a security failure in the startup
# logs of every process. The wrapper bought nothing here -- one algorithm, no
# migration between schemes -- so the dependency is gone.

TokenType = Literal["access", "refresh", "stream"]


def hash_password(plain: str) -> str:
    """Hash a password with bcrypt.

    bcrypt silently truncates input beyond 72 bytes. Truncating would weaken a
    long passphrase without the user ever knowing, so an over-length password
    is rejected instead.
    """
    encoded = plain.encode("utf-8")
    if len(encoded) > 72:
        raise ValueError(
            "Password must be 72 bytes or fewer (bcrypt truncates beyond that)."
        )
    return bcrypt.hashpw(
        encoded, bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
    ).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time password check.

    Returns False rather than raising on a malformed or truncated hash: a
    corrupt row in the users table is a failed authentication, not a 500 that
    takes the login endpoint down.
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _create_token(
    subject: str,
    token_type: TokenType,
    expires_delta: timedelta,
    extra: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "jti": str(uuid.uuid4()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(user_id: str, role: str, username: str) -> str:
    return _create_token(
        user_id,
        "access",
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        {"role": role, "username": username},
    )


def create_refresh_token(user_id: str) -> str:
    return _create_token(
        user_id, "refresh", timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    )


def create_stream_token(user_id: str, role: str) -> str:
    """Short-lived token for the SSE endpoint.

    EventSource cannot set an Authorization header, so the token travels in the
    query string, where it is more exposed (proxy logs, browser history). It is
    therefore scoped to the stream only and expires in 5 minutes; the client
    refreshes it on reconnect.
    """
    return _create_token(user_id, "stream", timedelta(minutes=5), {"role": role})


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError as exc:
        raise UnauthenticatedError("Invalid or expired token.") from exc

    if payload.get("type") != expected_type:
        # Stops a refresh or stream token being replayed as an access token.
        raise UnauthenticatedError("Token is not valid for this operation.")
    if not payload.get("sub"):
        raise UnauthenticatedError("Malformed token.")
    return payload
