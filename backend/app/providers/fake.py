"""Deterministic in-memory providers for tests and local development.

These make the 10 acceptance scenarios in brief §25 reproducible and free: the
test suite can assert that exactly 2000 m is AVAILABLE and 2001 m is not,
without depending on a live road network that could change.

``Settings`` refuses to start in production with ROUTING_PROVIDER=fake, so this
cannot leak into a real deployment.
"""

from __future__ import annotations

import math

from app.core.enums import DistanceType
from app.core.errors import GeocodingNoResultError, RoutingProviderError
from app.providers.base import (
    AddressSuggestion,
    Coordinate,
    GeocodeResult,
    RouteDetail,
    RouteLeg,
)


def haversine_meters(a: Coordinate, b: Coordinate) -> float:
    """Great-circle distance. Used ONLY to synthesise plausible fake data."""
    r = 6_371_008.8
    p1, p2 = math.radians(a.latitude), math.radians(b.latitude)
    dp = p2 - p1
    dl = math.radians(b.longitude - a.longitude)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class FakeRoutingProvider:
    """Configurable fake.

    Three modes, matching what the tests need:
      * ``distances`` — exact metre values keyed by rounded destination coords.
      * ``detour_ratio`` — derive a road distance from the straight line.
      * ``fail`` — raise, to exercise the ROUTE_CALCULATION_ERROR path.
    """

    name = "fake"

    def __init__(
        self,
        distances: dict[tuple[float, float], int] | None = None,
        detour_ratio: float = 1.3,
        fail: bool = False,
        fail_message: str = "Simulated routing provider failure",
        unreachable: set[tuple[float, float]] | None = None,
    ) -> None:
        self.distances = distances or {}
        self.detour_ratio = detour_ratio
        self.fail = fail
        self.fail_message = fail_message
        self.unreachable = unreachable or set()
        self.call_count = 0
        self.element_count = 0

    def _key(self, c: Coordinate) -> tuple[float, float]:
        return (round(c.latitude, 6), round(c.longitude, 6))

    def _distance(self, origin: Coordinate, dest: Coordinate) -> int | None:
        key = self._key(dest)
        if key in self.unreachable:
            return None
        if key in self.distances:
            return int(self.distances[key])
        return int(round(haversine_meters(origin, dest) * self.detour_ratio))

    async def get_driving_distance(
        self, origin: Coordinate, destinations: list[Coordinate]
    ) -> list[RouteLeg | None]:
        self.call_count += 1
        self.element_count += len(destinations)
        if self.fail:
            raise RoutingProviderError(self.fail_message)

        legs: list[RouteLeg | None] = []
        for dest in destinations:
            meters = self._distance(origin, dest)
            if meters is None:
                legs.append(None)
                continue
            legs.append(
                RouteLeg(
                    distance_meters=meters,
                    duration_seconds=max(60, int(meters / 8.33)),  # ~30 km/h
                    distance_type=DistanceType.ROAD_DISTANCE,
                    provider=self.name,
                )
            )
        return legs

    async def get_driving_route(
        self, origin: Coordinate, destination: Coordinate
    ) -> RouteDetail:
        if self.fail:
            raise RoutingProviderError(self.fail_message)
        meters = self._distance(origin, destination)
        if meters is None:
            raise RoutingProviderError("No route", code="PROVIDER_NO_ROUTE")
        return RouteDetail(
            distance_meters=meters,
            duration_seconds=max(60, int(meters / 8.33)),
            geometry="fake_polyline",
            geometry_format="encoded_polyline_5",
            provider=self.name,
        )

    async def health_check(self) -> bool:
        return not self.fail

    async def close(self) -> None:
        return None


class FakeGeocodingProvider:
    name = "fake"

    def __init__(
        self,
        results: dict[str, tuple[float, float]] | None = None,
        default: tuple[float, float] | None = (13.0827, 80.2707),
        fail_for: set[str] | None = None,
        confidence: str = "high",
    ) -> None:
        self.results = results or {}
        self.default = default
        self.fail_for = fail_for or set()
        self.confidence = confidence
        self.call_count = 0

    async def geocode(self, address: str) -> GeocodeResult:
        self.call_count += 1
        if address in self.fail_for or not address.strip():
            raise GeocodingNoResultError(f"No location found for: {address}")
        if address in self.results:
            lat, lng = self.results[address]
        elif self.default is not None:
            lat, lng = self.default
        else:
            raise GeocodingNoResultError(f"No location found for: {address}")
        return GeocodeResult(
            latitude=lat,
            longitude=lng,
            formatted_address=f"{address}, Chennai, Tamil Nadu, India",
            confidence=self.confidence,
            partial_match=self.confidence == "low",
            provider=self.name,
        )

    async def reverse_geocode(self, lat: float, lng: float) -> GeocodeResult:
        return GeocodeResult(
            latitude=lat,
            longitude=lng,
            formatted_address=f"Near {lat:.4f},{lng:.4f}, Chennai",
            confidence="high",
            provider=self.name,
        )

    async def autocomplete(
        self, query: str, session_token: str | None = None
    ) -> list[AddressSuggestion]:
        return [
            AddressSuggestion(
                description=f"{query}, Chennai, Tamil Nadu, India",
                place_id=f"fake_{abs(hash(query)) % 10**8}",
                main_text=query,
                secondary_text="Chennai, Tamil Nadu, India",
            )
        ]

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None
