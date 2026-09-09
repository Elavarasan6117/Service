"""OSRM adapter — self-hosted routing.

Provided as the secondary/fallback provider and as a zero-marginal-cost option
if Google spend becomes a problem. OSRM does not geocode; pair it with
Nominatim or keep Google for geocoding only.

OSRM's ``/table`` service returns a full distance matrix in one request, which
maps exactly onto the Stage-2 call the engine makes.
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.enums import DistanceType
from app.core.errors import RoutingProviderError
from app.core.logging import get_logger
from app.providers.base import Coordinate, RouteDetail, RouteLeg

logger = get_logger(__name__)


class OsrmProvider:
    name = "osrm"

    def __init__(
        self, base_url: str | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self.base_url = (base_url or settings.OSRM_BASE_URL).rstrip("/")
        self._client = client
        self._owns_client = client is None

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

        all_points = [origin, *destinations]
        dest_indices = ";".join(str(i) for i in range(1, len(all_points)))
        url = f"{self.base_url}/table/v1/driving/{self._coords(all_points)}"

        try:
            response = await self.client.get(
                url,
                params={
                    "sources": "0",
                    "destinations": dest_indices,
                    "annotations": "distance,duration",
                },
            )
        except httpx.HTTPError as exc:
            raise RoutingProviderError(f"OSRM unreachable: {exc}") from exc

        if response.status_code >= 500:
            raise RoutingProviderError(f"OSRM returned HTTP {response.status_code}.")
        response.raise_for_status()
        payload = response.json()

        if payload.get("code") != "Ok":
            raise RoutingProviderError(
                f"OSRM error: {payload.get('code')} {payload.get('message', '')}".strip()
            )

        distances = (payload.get("distances") or [[]])[0]
        durations = (payload.get("durations") or [[]])[0]
        if len(distances) != len(destinations):
            raise RoutingProviderError(
                "OSRM returned a mismatched number of distances.",
                code="PROVIDER_RESPONSE_MISALIGNED",
            )

        legs: list[RouteLeg | None] = []
        for idx, distance in enumerate(distances):
            if distance is None:
                legs.append(None)
                continue
            duration = durations[idx] if idx < len(durations) else None
            legs.append(
                RouteLeg(
                    distance_meters=int(round(float(distance))),
                    duration_seconds=int(round(float(duration)))
                    if duration is not None
                    else None,
                    distance_type=DistanceType.ROAD_DISTANCE,
                    provider=self.name,
                )
            )
        return legs

    async def get_driving_route(
        self, origin: Coordinate, destination: Coordinate
    ) -> RouteDetail:
        url = f"{self.base_url}/route/v1/driving/{self._coords([origin, destination])}"
        try:
            response = await self.client.get(
                url, params={"overview": "full", "geometries": "polyline"}
            )
        except httpx.HTTPError as exc:
            raise RoutingProviderError(f"OSRM unreachable: {exc}") from exc

        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "Ok" or not payload.get("routes"):
            raise RoutingProviderError(
                "No driving route found between these points.",
                code="PROVIDER_NO_ROUTE",
            )
        route = payload["routes"][0]
        return RouteDetail(
            distance_meters=int(round(float(route["distance"]))),
            duration_seconds=int(round(float(route.get("duration", 0)))) or None,
            geometry=route.get("geometry"),
            geometry_format="encoded_polyline_5" if route.get("geometry") else "none",
            provider=self.name,
        )

    async def health_check(self) -> bool:
        try:
            response = await self.client.get(
                f"{self.base_url}/route/v1/driving/80.2707,13.0827;80.2100,13.0850",
                params={"overview": "false"},
            )
            return response.status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.warning("osrm_health_check_failed", error=str(exc))
            return False
