"""Provider ports.

The domain layer depends on these Protocols and never on a concrete SDK. This
is what makes "do not hard-code the application tightly to one provider"
(brief §12) structurally true rather than merely intended.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.core.enums import DistanceType


@dataclass(frozen=True, slots=True)
class Coordinate:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError(f"latitude out of range: {self.latitude}")
        if not -180 <= self.longitude <= 180:
            raise ValueError(f"longitude out of range: {self.longitude}")

    def rounded(self, places: int = 6) -> "Coordinate":
        """Rounded copy used for cache keys (~11 cm at 6 dp)."""
        return Coordinate(round(self.latitude, places), round(self.longitude, places))

    def as_pair(self) -> tuple[float, float]:
        return (self.latitude, self.longitude)

    def __str__(self) -> str:
        return f"{self.latitude},{self.longitude}"


@dataclass(frozen=True, slots=True)
class RouteLeg:
    """One origin-destination measurement.

    ``distance_type`` is carried explicitly and defaults to ROAD_DISTANCE. The
    serviceability engine refuses any leg that is not ROAD_DISTANCE, so a future
    provider adapter that returns a geodesic approximation cannot quietly become
    the basis of a business decision.
    """

    distance_meters: int
    duration_seconds: int | None = None
    distance_type: DistanceType = DistanceType.ROAD_DISTANCE
    provider: str = "unknown"

    def __post_init__(self) -> None:
        if not isinstance(self.distance_meters, int):
            raise TypeError(
                "distance_meters must be an int in metres; adapters round once "
                "at the boundary so the threshold comparison is exact."
            )
        if self.distance_meters < 0:
            raise ValueError("distance_meters must be non-negative")


@dataclass(frozen=True, slots=True)
class RouteDetail:
    distance_meters: int
    duration_seconds: int | None
    geometry: str | None
    geometry_format: str  # "encoded_polyline_5" | "geojson" | "none"
    provider: str
    distance_type: DistanceType = DistanceType.ROAD_DISTANCE


@dataclass(frozen=True, slots=True)
class GeocodeResult:
    latitude: float
    longitude: float
    formatted_address: str
    confidence: str  # "high" | "medium" | "low"
    partial_match: bool = False
    place_id: str | None = None
    components: dict[str, str] = field(default_factory=dict)
    provider: str = "unknown"

    @property
    def needs_verification(self) -> bool:
        """Whether an operator should confirm this point on the map.

        A partial match or a low-confidence result is exactly the situation the
        brief calls out in §13: do not act on it silently.
        """
        return self.partial_match or self.confidence == "low"


@dataclass(frozen=True, slots=True)
class AddressSuggestion:
    description: str
    place_id: str
    main_text: str = ""
    secondary_text: str = ""


@runtime_checkable
class RoutingProvider(Protocol):
    name: str

    async def get_driving_distance(
        self, origin: Coordinate, destinations: list[Coordinate]
    ) -> list[RouteLeg | None]:
        """Road distances from one origin to many destinations.

        Returns a list the same length and order as ``destinations``. An entry
        is ``None`` when that specific destination is unreachable or was not
        returned by the provider -- distinct from the whole call failing, which
        raises RoutingProviderError.
        """
        ...

    async def get_driving_route(
        self, origin: Coordinate, destination: Coordinate
    ) -> RouteDetail:
        """A single route including geometry for map display."""
        ...

    async def health_check(self) -> bool: ...

    async def close(self) -> None: ...


@runtime_checkable
class GeocodingProvider(Protocol):
    name: str

    async def geocode(self, address: str) -> GeocodeResult: ...

    async def reverse_geocode(self, lat: float, lng: float) -> GeocodeResult: ...

    async def autocomplete(
        self, query: str, session_token: str | None = None
    ) -> list[AddressSuggestion]: ...

    async def health_check(self) -> bool: ...

    async def close(self) -> None: ...


def is_latin_text(text: str) -> bool:
    """Whether every letter in ``text`` is Latin-script (accented Latin
    included -- "Udagawacho" with a macron is still readable to an English
    speaker, unlike Greek, Cyrillic, Arabic, or CJK script).

    Requesting English place names (``GEOCODING_LANGUAGE``) only translates
    an entity that has an English name tagged in OpenStreetMap; a smaller
    street or neighbourhood often does not, and the provider silently falls
    back to whatever script that country's own name tag uses. There is no
    further translation to request in that case -- the fix is to leave that
    one segment out of the address rather than show an unreadable mix of
    scripts, which is what every caller of this helper does.
    """
    for char in text:
        if char.isalpha() and "LATIN" not in unicodedata.name(char, ""):
            return False
    return True
