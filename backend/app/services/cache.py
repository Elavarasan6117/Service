"""Route-distance cache and Redis access.

Cost control (brief §19): the same origin/destination pair is not re-routed
within the TTL. Because road networks change slowly and a moved location
invalidates eagerly, a 24-hour default is a safe trade.

Every method degrades to a no-op if Redis is unavailable. A cache outage must
slow the system down, never break a serviceability decision.
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.enums import DistanceType
from app.core.logging import get_logger
from app.core.metrics import route_cache_events_total
from app.providers.base import Coordinate, RouteLeg

logger = get_logger(__name__)

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def _pair_key(provider: str, origin: Coordinate, dest: Coordinate) -> str:
    o, d = origin.rounded(6), dest.rounded(6)
    return f"route:{provider}:{o.latitude},{o.longitude}:{d.latitude},{d.longitude}"


def _point_index_key(provider: str, point: Coordinate) -> str:
    """Reverse index: which cached pairs involve this point.

    Needed so that moving a service location can invalidate its cached
    distances immediately (brief §25 test 10) instead of waiting out the TTL.
    """
    p = point.rounded(6)
    return f"routeidx:{provider}:{p.latitude},{p.longitude}"


class RouteCache:
    def __init__(self, provider_name: str, ttl_seconds: int | None = None) -> None:
        self.provider_name = provider_name
        self.ttl = ttl_seconds or settings.DEFAULT_ROUTE_CACHE_TTL_SECONDS

    async def get_many(
        self, origin: Coordinate, destinations: list[Coordinate]
    ) -> dict[int, RouteLeg]:
        """Return {index_into_destinations: RouteLeg} for cache hits."""
        if not destinations:
            return {}
        try:
            redis = get_redis()
            keys = [_pair_key(self.provider_name, origin, d) for d in destinations]
            values = await redis.mget(keys)
        except Exception as exc:  # noqa: BLE001 - cache must never break a check
            logger.warning("route_cache_unavailable", error=str(exc))
            return {}

        hits: dict[int, RouteLeg] = {}
        for idx, raw in enumerate(values):
            if not raw:
                route_cache_events_total.labels(event="miss").inc()
                continue
            try:
                data: dict[str, Any] = json.loads(raw)
                hits[idx] = RouteLeg(
                    distance_meters=int(data["distance_meters"]),
                    duration_seconds=data.get("duration_seconds"),
                    distance_type=DistanceType(
                        data.get("distance_type", DistanceType.ROAD_DISTANCE)
                    ),
                    provider=data.get("provider", self.provider_name),
                )
                route_cache_events_total.labels(event="hit").inc()
            except (ValueError, KeyError, TypeError) as exc:
                # A corrupt entry is a miss, not an error.
                logger.warning("route_cache_corrupt_entry", error=str(exc))
                route_cache_events_total.labels(event="miss").inc()
        return hits

    async def set_many(
        self,
        origin: Coordinate,
        pairs: list[tuple[Coordinate, RouteLeg]],
    ) -> None:
        if not pairs:
            return
        try:
            redis = get_redis()
            pipe = redis.pipeline()
            for dest, leg in pairs:
                key = _pair_key(self.provider_name, origin, dest)
                pipe.setex(
                    key,
                    self.ttl,
                    json.dumps(
                        {
                            "distance_meters": leg.distance_meters,
                            "duration_seconds": leg.duration_seconds,
                            "distance_type": str(leg.distance_type),
                            "provider": leg.provider,
                        }
                    ),
                )
                # Index both endpoints so either can invalidate the pair.
                for point in (origin, dest):
                    idx_key = _point_index_key(self.provider_name, point)
                    pipe.sadd(idx_key, key)
                    pipe.expire(idx_key, self.ttl + 3600)
            await pipe.execute()
            route_cache_events_total.labels(event="store").inc(len(pairs))
        except Exception as exc:  # noqa: BLE001
            logger.warning("route_cache_store_failed", error=str(exc))

    async def invalidate_point(self, point: Coordinate) -> int:
        """Drop every cached pair involving this point.

        Called when a service location or customer moves. Serving a distance to
        a location's *old* position would be a silently wrong decision, so this
        runs synchronously in the same request as the move.
        """
        try:
            redis = get_redis()
            idx_key = _point_index_key(self.provider_name, point)
            keys = await redis.smembers(idx_key)
            if keys:
                await redis.delete(*keys)
            await redis.delete(idx_key)
            route_cache_events_total.labels(event="invalidate").inc(len(keys))
            logger.info(
                "route_cache_invalidated", point=str(point), entries=len(keys)
            )
            return len(keys)
        except Exception as exc:  # noqa: BLE001
            logger.warning("route_cache_invalidate_failed", error=str(exc))
            return 0

    async def stats(self) -> dict[str, Any]:
        try:
            redis = get_redis()
            info = await redis.info("stats")
            hits = int(info.get("keyspace_hits", 0))
            misses = int(info.get("keyspace_misses", 0))
            total = hits + misses
            return {
                "keyspace_hits": hits,
                "keyspace_misses": misses,
                "hit_rate": round(hits / total, 4) if total else None,
            }
        except Exception:  # noqa: BLE001
            return {"available": False}
