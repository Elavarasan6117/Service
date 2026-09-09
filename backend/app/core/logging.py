"""Structured logging.

JSON in every deployed environment so logs are queryable; pretty console output
in local development. Every log line inside a request carries the request id,
the authenticated user and the path, bound once by middleware.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_configured = False


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    global _configured
    if _configured:
        return

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    # These are noisy and rarely useful at INFO.
    for noisy in ("uvicorn.access", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_secrets,
    ]
    if fmt == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


_SECRET_KEYS = {
    "password",
    "password_hash",
    "secret",
    "secret_key",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "api_key",
    "google_maps_api_key",
    "key",
}


def _redact_secrets(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Defence in depth: never let a credential reach a log sink.

    Logging an API key would put a billable secret into log storage, backups and
    possibly a third-party log aggregator. This runs on every line.
    """
    for k in list(event_dict.keys()):
        if k.lower() in _SECRET_KEYS:
            event_dict[k] = "***redacted***"
        elif isinstance(event_dict[k], str) and "key=" in event_dict[k]:
            # Query strings that carry an API key, e.g. provider request URLs.
            import re

            event_dict[k] = re.sub(
                r"([?&](?:key|api_key|access_token)=)[^&\s]+",
                r"\1***redacted***",
                event_dict[k],
            )
    return event_dict


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
