"""OSRM adapter for self-hosted road routing.

Used for temporary route previews while Google remains available for
geocoding and the independently configured serviceability provider. OSRM does
not geocode; pair it with Nominatim or keep Google for geocoding.

OSRM's ``/table`` service returns a full distance matrix in one request, which
maps exactly onto the Stage-2 call the engine makes.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.enums import DistanceType
from app.core.errors import RoutingProviderError
from app.core.logging import get_logger
from app.providers.base import Coordinate, RouteDetail, RouteLeg

logger = get_logger(__name__)
_RESPONSE_CACHE_TTL_SECONDS = 60
_RESPONSE_CACHE_MAX_SIZE = 512


class OsrmProvider:
    name = "osrm"

    def __init__(
        self, base_url: str | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        configured_url = settings.OSRM_BASE_URL if base_url is None else base_url
        self.base_url = configured_url.strip().rstrip("/")
        self._client = client
        self._owns_client = client is None
        self._response_cache: dict[str, tuple[float, Any]] = {}
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._cache_lock = asyncio.Lock()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.PROVIDER_TIMEOUT_SECONDS),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _request_json(self, path: str, params: dict[str, str]) -> Any:
        """Fetch an OSRM response with short-lived caching and request coalescing.

        Cache keys contain only the route path and parameters, never credentials.
        In-flight requests are shielded so one disconnected dashboard request
        does not cancel work shared by another caller in the same worker.
        """
        self._validate_endpoint()
        cache_key = f"{path}?{json.dumps(params, sort_keys=True, separators=(',', ':'))}"
        now = time.monotonic()
        async with self._cache_lock:
            for key, (expires_at, _) in list(self._response_cache.items()):
                if expires_at <= now:
                    self._response_cache.pop(key, None)
            cached = self._response_cache.get(cache_key)
            if cached is not None:
                return cached[1]
            task = self._inflight.get(cache_key)
            if task is None:
                task = asyncio.create_task(self._fetch_and_cache(cache_key, path, params))
                # Consume the exception if all waiters disconnect before it
                # completes; normal awaiters still receive it from the task.
                task.add_done_callback(
                    lambda done: done.exception() if not done.cancelled() else None
                )
                self._inflight[cache_key] = task
        return await asyncio.shield(task)

    def _validate_endpoint(self) -> None:
        if not self.base_url:
            raise RoutingProviderError(
                "OSRM_BASE_URL is not configured. Set it to a private, reachable OSRM service in the backend environment.",
                code="PROVIDER_NOT_CONFIGURED",
            )
        parsed = urlparse(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise RoutingProviderError(
                "OSRM_BASE_URL must be an HTTP(S) URL without embedded credentials.",
                code="PROVIDER_NOT_CONFIGURED",
            )
        hostname = parsed.hostname.lower()
        if settings.ENVIRONMENT != "development" and hostname in (
            "localhost", "127.0.0.1", "::1", "router.project-osrm.org",
        ):
            raise RoutingProviderError(
                "OSRM_BASE_URL must point to a private reachable service; loopback and the public OSRM demo are not production endpoints.",
                code="PROVIDER_NOT_CONFIGURED",
            )

    async def _fetch_and_cache(
        self, cache_key: str, path: str, params: dict[str, str]
    ) -> Any:
        try:
            try:
                response = await self.client.get(f"{self.base_url}{path}", params=params)
            except httpx.ConnectError as exc:
                logger.warning(
                    "osrm_connection_failed",
                    operation=path.split("/", 2)[1] if path.startswith("/") else "unknown",
                    error_type=type(exc).__name__,
                )
                raise RoutingProviderError(
                    "Cannot connect to OSRM. Check that the private OSRM service is running and OSRM_BASE_URL is reachable from the backend.",
                    code="PROVIDER_CONNECT_ERROR",
                ) from exc
            except httpx.HTTPError as exc:
                logger.warning(
                    "osrm_request_failed",
                    operation=path.split("/", 2)[1] if path.startswith("/") else "unknown",
                    error_type=type(exc).__name__,
                )
                raise RoutingProviderError(
                    f"OSRM transport error ({type(exc).__name__})."
                ) from exc
            if response.status_code == 429:
                from app.core.errors import RoutingRateLimitError

                raise RoutingRateLimitError(
                    "OSRM request limit was reached.", code="PROVIDER_QUOTA_EXCEEDED"
                )
            if response.status_code >= 500:
                raise RoutingProviderError(
                    f"OSRM returned HTTP {response.status_code}.", code="PROVIDER_HTTP_5XX"
                )
            if response.status_code >= 400:
                raise RoutingProviderError(
                    f"OSRM rejected the routing request (HTTP {response.status_code}).",
                    code="PROVIDER_INVALID_REQUEST" if response.status_code == 400 else "PROVIDER_CLIENT_ERROR",
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise RoutingProviderError(
                    "OSRM returned invalid JSON.", code="PROVIDER_INVALID_RESPONSE"
                ) from exc
        except BaseException:
            async with self._cache_lock:
                self._inflight.pop(cache_key, None)
            raise

        if not isinstance(payload, dict):
            async with self._cache_lock:
                self._inflight.pop(cache_key, None)
            raise RoutingProviderError(
                "OSRM returned an invalid response shape.", code="PROVIDER_INVALID_RESPONSE"
            )

        async with self._cache_lock:
            if len(self._response_cache) >= _RESPONSE_CACHE_MAX_SIZE:
                oldest = min(self._response_cache, key=lambda key: self._response_cache[key][0])
                self._response_cache.pop(oldest, None)
            self._response_cache[cache_key] = (
                time.monotonic() + _RESPONSE_CACHE_TTL_SECONDS,
                payload,
            )
            self._inflight.pop(cache_key, None)
        return payload

    @staticmethod
    def _coords(points: list[Coordinate]) -> str:
        # OSRM takes lon,lat — the opposite order to Google. Getting this
        # backwards puts every Chennai point in the sea, so it is centralised
        # here and asserted in tests/test_providers.py.
        return ";".join(f"{p.longitude},{p.latitude}" for p in points)

    async def get_driving_distance(
        self, origin: Coordinate, destinations: list[Coordinate]
    ) -> list[RouteLeg | None]:
        if not destinations:
            return []
        return (await self._table([origin], destinations))[0]

    async def get_driving_distances(
        self, origins: list[Coordinate], destination: Coordinate
    ) -> list[RouteLeg | None]:
        """Use one OSRM Table API call for many candidate origins to one pin."""
        if not origins:
            return []
        matrix = await self._table(origins, [destination])
        return [row[0] for row in matrix]

    async def _table(
        self, origins: list[Coordinate], destinations: list[Coordinate]
    ) -> list[list[RouteLeg | None]]:
        points = [*origins, *destinations]
        destination_indices = list(range(len(origins), len(points)))
        payload = await self._request_json(
            f"/table/v1/driving/{self._coords(points)}",
            {
                "sources": ";".join(str(index) for index in range(len(origins))),
                "destinations": ";".join(str(index) for index in destination_indices),
                "annotations": "distance,duration",
            },
        )
        if not isinstance(payload, dict) or payload.get("code") != "Ok":
            code = payload.get("code") if isinstance(payload, dict) else "InvalidResponse"
            error_message = payload.get("message") if isinstance(payload, dict) else None
            message = error_message or code or "unknown OSRM error"
            raise RoutingProviderError(
                f"OSRM table request failed: {message}",
                code="PROVIDER_NO_ROUTE" if code == "NoTable" else "PROVIDER_INVALID_REQUEST",
            )

        distances = payload.get("distances")
        durations = payload.get("durations")
        if (
            not isinstance(distances, list)
            or len(distances) != len(origins)
            or any(not isinstance(row, list) or len(row) != len(destinations) for row in distances)
        ):
            raise RoutingProviderError(
                "OSRM returned a mismatched distance matrix.",
                code="PROVIDER_RESPONSE_MISALIGNED",
            )
        results: list[list[RouteLeg | None]] = []
        for origin_index, row in enumerate(distances):
            duration_row = (
                durations[origin_index]
                if isinstance(durations, list) and origin_index < len(durations)
                else []
            )
            if not isinstance(duration_row, list):
                duration_row = []
            legs: list[RouteLeg | None] = []
            for destination_index, distance in enumerate(row):
                if distance is None:
                    legs.append(None)
                    continue
                duration = (
                    duration_row[destination_index]
                    if destination_index < len(duration_row)
                    else None
                )
                try:
                    distance_meters = int(round(float(distance)))
                    duration_seconds = (
                        int(round(float(duration))) if duration is not None else None
                    )
                except (TypeError, ValueError, OverflowError) as exc:
                    raise RoutingProviderError(
                        "OSRM returned a non-numeric distance or duration.",
                        code="PROVIDER_INVALID_RESPONSE",
                    ) from exc
                legs.append(RouteLeg(
                    distance_meters=distance_meters,
                    duration_seconds=duration_seconds,
                    distance_type=DistanceType.ROAD_DISTANCE,
                    provider=self.name,
                ))
            results.append(legs)
        return results

    async def get_driving_route(
        self, origin: Coordinate, destination: Coordinate
    ) -> RouteDetail:
        payload = await self._request_json(
            f"/route/v1/driving/{self._coords([origin, destination])}",
            {"overview": "full", "geometries": "polyline", "steps": "false"},
        )
        if (
            not isinstance(payload, dict)
            or payload.get("code") != "Ok"
            or not isinstance(payload.get("routes"), list)
            or not payload["routes"]
        ):
            raise RoutingProviderError(
                f"No driving route found between these points ({payload.get('code', 'NoRoute') if isinstance(payload, dict) else 'InvalidResponse'}).",
                code="PROVIDER_NO_ROUTE",
            )
        route = payload["routes"][0]
        if (
            not isinstance(route, dict)
            or not isinstance(route.get("distance"), (float, int))
            or isinstance(route.get("distance"), bool)
        ):
            raise RoutingProviderError(
                "OSRM returned a route without a valid road distance.",
                code="PROVIDER_INVALID_RESPONSE",
            )
        try:
            distance_meters = int(round(float(route["distance"])))
            duration_seconds = int(round(float(route.get("duration", 0)))) or None
        except (TypeError, ValueError, OverflowError) as exc:
            raise RoutingProviderError(
                "OSRM returned a non-numeric route distance or duration.",
                code="PROVIDER_INVALID_RESPONSE",
            ) from exc
        geometry = route.get("geometry")
        if geometry is not None and not isinstance(geometry, str):
            raise RoutingProviderError(
                "OSRM returned invalid route geometry.", code="PROVIDER_INVALID_RESPONSE"
            )
        return RouteDetail(
            distance_meters=distance_meters,
            duration_seconds=duration_seconds,
            geometry=geometry,
            geometry_format="encoded_polyline_5" if geometry else "none",
            provider=self.name,
        )

    async def health_check(self) -> bool:
        try:
            self._validate_endpoint()
        except RoutingProviderError:
            return False
        try:
            response = await self.client.get(
                f"{self.base_url}/route/v1/driving/80.2707,13.0827;80.2100,13.0850",
                params={"overview": "false"},
            )
            return response.status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.warning("osrm_health_check_failed", error_type=type(exc).__name__)
            return False
