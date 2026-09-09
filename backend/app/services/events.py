"""Live dashboard events over Redis pub/sub + SSE.

Redis is the fan-out so that any API replica can publish and every connected
dashboard receives it, regardless of which replica holds the SSE connection.
With a single replica this is redundant; with two or more it is required, and
building it in now costs nothing.

Publishing must never break the request that triggered it: a Redis outage
degrades the dashboard to manual refresh, it does not fail a customer creation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.core.metrics import sse_connections, sse_events_published_total
from app.services.cache import get_redis

logger = get_logger(__name__)

CHANNEL = "serviceability:events"
HEARTBEAT_SECONDS = 15


class EventType:
    CUSTOMER_CREATED = "customer.created"
    CHECK_STARTED = "check.started"
    CHECK_COMPLETED = "check.completed"
    CHECK_ERROR = "check.error"
    CUSTOMER_LOCATION_UPDATED = "customer.location_updated"
    SERVICE_LOCATION_UPDATED = "service_location.updated"
    CONFIG_UPDATED = "config.updated"
    IMPORT_COMPLETED = "import.completed"
    HEARTBEAT = "heartbeat"


async def publish(event_type: str, payload: dict[str, Any]) -> None:
    message = json.dumps(
        {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": payload,
        },
        default=str,
    )
    try:
        await get_redis().publish(CHANNEL, message)
        sse_events_published_total.labels(event_type=event_type).inc()
    except Exception as exc:  # noqa: BLE001 - never break the caller
        logger.warning("event_publish_failed", event_type=event_type, error=str(exc))


def _sse_frame(event_type: str, data: str) -> str:
    return f"event: {event_type}\ndata: {data}\n\n"


async def event_stream() -> AsyncGenerator[str, None]:
    """SSE generator for one connected dashboard."""
    sse_connections.inc()
    pubsub = None
    try:
        redis = get_redis()
        pubsub = redis.pubsub()
        await pubsub.subscribe(CHANNEL)

        # Tell the client to back off if it reconnects, and prove the stream
        # is live immediately rather than after the first business event.
        yield "retry: 5000\n\n"
        yield _sse_frame(
            EventType.HEARTBEAT,
            json.dumps({"timestamp": datetime.now(timezone.utc).isoformat()}),
        )

        last_heartbeat = asyncio.get_event_loop().time()
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message.get("type") == "message":
                raw = message["data"]
                try:
                    parsed = json.loads(raw)
                    yield _sse_frame(parsed.get("type", "message"), raw)
                except json.JSONDecodeError:
                    logger.warning("malformed_event_dropped")

            now = asyncio.get_event_loop().time()
            if now - last_heartbeat >= HEARTBEAT_SECONDS:
                # Keeps intermediate proxies from closing an idle connection
                # and lets the client detect a dead stream.
                yield _sse_frame(
                    EventType.HEARTBEAT,
                    json.dumps({"timestamp": datetime.now(timezone.utc).isoformat()}),
                )
                last_heartbeat = now
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("sse_stream_error", error=str(exc))
    finally:
        sse_connections.dec()
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(CHANNEL)
                await pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass
