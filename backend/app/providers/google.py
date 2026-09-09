"""Google Maps Platform adapter.

APIs used, all server-side:
  * Geocoding API          — address → coordinates
  * Places Autocomplete    — address search suggestions
  * Distance Matrix API    — road distance, 1 origin × N destinations, one call
  * Directions API         — road route with geometry for the winning pair

The API key is read from settings and never leaves the backend. The browser
receives only the results of these calls.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.core.enums import DistanceType
from app.core.errors import (
    GeocodingAmbiguousError,
    GeocodingNoResultError,
    RoutingProviderError,
    RoutingRateLimitError,
)
from app.core.logging import get_logger
from app.providers.base import (
    AddressSuggestion,
    Coordinate,
    GeocodeResult,
    RouteDetail,
    RouteLeg,
)

logger = get_logger(__name__)

_BASE = "https://maps.googleapis.com/maps/api"

# Google's Distance Matrix limit is 25 destinations per request (and 100
# elements). We chunk to stay inside it; CANDIDATE_MAX_COUNT is normally well
# below this, so chunking is a safety net rather than the usual path.
_MAX_DESTINATIONS_PER_REQUEST = 25

# Location-type values that indicate the geocoder pinpointed the address rather
# than interpolating or falling back to a wider area.
_HIGH_CONFIDENCE = {"ROOFTOP"}
_MEDIUM_CONFIDENCE = {"RANGE_INTERPOLATED", "GEOMETRIC_CENTER"}


class GoogleMapsProvider:
    """Implements both RoutingProvider and GeocodingProvider."""

    name = "google_maps"

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.GOOGLE_MAPS_API_KEY
        self._client = client
        self._owns_client = client is None

    # -- plumbing --------------------------------------------------------

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.PROVIDER_TIMEOUT_SECONDS),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
                headers={"User-Agent": "ChennaiServiceability/1.0"},
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise RoutingProviderError(
                "GOOGLE_MAPS_API_KEY is not configured.",
                code="PROVIDER_NOT_CONFIGURED",
            )
        params = {**params, "key": self.api_key}
        response = await self.client.get(f"{_BASE}{path}", params=params)

        if response.status_code == 429:
            raise RoutingRateLimitError()
        if response.status_code >= 500:
            raise RoutingProviderError(
                f"Google returned HTTP {response.status_code}.",
                code="PROVIDER_HTTP_5XX",
            )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        status_value = payload.get("status")
        if status_value in ("OVER_QUERY_LIMIT", "OVER_DAILY_LIMIT"):
            # Quota exhaustion. This is the failure mode that must surface as
            # ROUTE_CALCULATION_ERROR and alert operations -- never as a
            # NOT_AVAILABLE decision.
            raise RoutingRateLimitError(
                "Google Maps quota exceeded.", code="PROVIDER_QUOTA_EXCEEDED"
            )
        if status_value in ("REQUEST_DENIED", "INVALID_REQUEST", "UNKNOWN_ERROR"):
            raise RoutingProviderError(
                f"Google Maps request failed: {status_value} "
                f"{payload.get('error_message', '')}".strip(),
                code=f"PROVIDER_{status_value}",
            )
        return payload

    # -- routing ---------------------------------------------------------

    async def get_driving_distance(
        self, origin: Coordinate, destinations: list[Coordinate]
    ) -> list[RouteLeg | None]:
        if not destinations:
            return []

        results: list[RouteLeg | None] = []
        for start in range(0, len(destinations), _MAX_DESTINATIONS_PER_REQUEST):
            chunk = destinations[start : start + _MAX_DESTINATIONS_PER_REQUEST]
            results.extend(await self._distance_matrix_chunk(origin, chunk))
        return results

    async def _distance_matrix_chunk(
        self, origin: Coordinate, destinations: list[Coordinate]
    ) -> list[RouteLeg | None]:
        payload = await self._get(
            "/distancematrix/json",
            {
                "origins": str(origin),
                "destinations": "|".join(str(d) for d in destinations),
                "mode": "driving",
                "units": "metric",
                "region": settings.GOOGLE_MAPS_REGION,
                "language": settings.GOOGLE_MAPS_LANGUAGE,
            },
        )

        rows = payload.get("rows") or []
        if not rows:
            raise RoutingProviderError(
                "Distance Matrix returned no rows.", code="PROVIDER_EMPTY_RESPONSE"
            )

        elements = rows[0].get("elements") or []
        if len(elements) != len(destinations):
            raise RoutingProviderError(
                "Distance Matrix returned a mismatched number of elements; "
                "the response cannot be aligned to the requested destinations.",
                code="PROVIDER_RESPONSE_MISALIGNED",
            )

        legs: list[RouteLeg | None] = []
        for element in elements:
            if element.get("status") != "OK":
                # ZERO_RESULTS / NOT_FOUND for one destination is a per-pair
                # condition, not a provider failure. The engine tolerates it.
                legs.append(None)
                continue
            distance = element.get("distance", {}).get("value")
            duration = element.get("duration", {}).get("value")
            if distance is None:
                legs.append(None)
                continue
            legs.append(
                RouteLeg(
                    # Round to metres exactly once, here at the boundary.
                    distance_meters=int(round(float(distance))),
                    duration_seconds=int(duration) if duration is not None else None,
                    distance_type=DistanceType.ROAD_DISTANCE,
                    provider=self.name,
                )
            )
        return legs

    async def get_driving_route(
        self, origin: Coordinate, destination: Coordinate
    ) -> RouteDetail:
        payload = await self._get(
            "/directions/json",
            {
                "origin": str(origin),
                "destination": str(destination),
                "mode": "driving",
                "units": "metric",
                "region": settings.GOOGLE_MAPS_REGION,
                "language": settings.GOOGLE_MAPS_LANGUAGE,
            },
        )
        routes = payload.get("routes") or []
        if not routes:
            raise RoutingProviderError(
                "No driving route found between these points.",
                code="PROVIDER_NO_ROUTE",
            )
        route = routes[0]
        legs = route.get("legs") or []
        distance = sum(leg.get("distance", {}).get("value", 0) for leg in legs)
        duration = sum(leg.get("duration", {}).get("value", 0) for leg in legs)
        geometry = (route.get("overview_polyline") or {}).get("points")
        return RouteDetail(
            distance_meters=int(round(float(distance))),
            duration_seconds=int(duration) if duration else None,
            geometry=geometry,
            geometry_format="encoded_polyline_5" if geometry else "none",
            provider=self.name,
        )

    # -- geocoding -------------------------------------------------------

    async def geocode(self, address: str) -> GeocodeResult:
        payload = await self._get(
            "/geocode/json",
            {
                "address": address,
                "region": settings.GOOGLE_MAPS_REGION,
                "language": settings.GOOGLE_MAPS_LANGUAGE,
                **(
                    {"components": f"country:{settings.GOOGLE_PLACES_COUNTRY_FILTER}"}
                    if settings.GOOGLE_PLACES_COUNTRY_FILTER.strip()
                    else {}
                ),
            },
        )
        status_value = payload.get("status")
        results = payload.get("results") or []

        if status_value == "ZERO_RESULTS" or not results:
            raise GeocodingNoResultError(
                f"No location found for: {address}",
            )

        best = results[0]
        geometry = best.get("geometry", {})
        location = geometry.get("location", {})
        location_type = geometry.get("location_type", "")

        if location_type in _HIGH_CONFIDENCE:
            confidence = "high"
        elif location_type in _MEDIUM_CONFIDENCE:
            confidence = "medium"
        else:
            confidence = "low"

        partial = bool(best.get("partial_match", False))

        # More than one result with an imprecise match is genuinely ambiguous:
        # picking the first one silently would place the customer at a guess.
        if len(results) > 1 and confidence == "low":
            raise GeocodingAmbiguousError(
                f"Address '{address}' matched {len(results)} locations imprecisely."
            )

        components = {
            comp["types"][0]: comp.get("long_name", "")
            for comp in best.get("address_components", [])
            if comp.get("types")
        }

        return GeocodeResult(
            latitude=float(location["lat"]),
            longitude=float(location["lng"]),
            formatted_address=best.get("formatted_address", address),
            confidence=confidence,
            partial_match=partial,
            place_id=best.get("place_id"),
            components=components,
            provider=self.name,
        )

    async def reverse_geocode(self, lat: float, lng: float) -> GeocodeResult:
        payload = await self._get(
            "/geocode/json",
            {
                "latlng": f"{lat},{lng}",
                "language": settings.GOOGLE_MAPS_LANGUAGE,
            },
        )
        results = payload.get("results") or []
        if not results:
            raise GeocodingNoResultError(f"No address found at {lat},{lng}")
        best = results[0]
        return GeocodeResult(
            latitude=lat,
            longitude=lng,
            formatted_address=best.get("formatted_address", ""),
            confidence="high",
            place_id=best.get("place_id"),
            provider=self.name,
        )

    async def autocomplete(
        self, query: str, session_token: str | None = None
    ) -> list[AddressSuggestion]:
        params: dict[str, Any] = {
            "input": query,
            "components": f"country:{settings.GOOGLE_PLACES_COUNTRY_FILTER}",
            "language": settings.GOOGLE_MAPS_LANGUAGE,
            # Bias toward Chennai without hard-restricting, so a nearby
            # suburb outside the city boundary still appears.
            "location": f"{settings.MAP_DEFAULT_CENTER_LAT},{settings.MAP_DEFAULT_CENTER_LNG}",
            "radius": 50000,
        }
        if session_token:
            # Session tokens group keystrokes into one billable session.
            params["sessiontoken"] = session_token

        payload = await self._get("/place/autocomplete/json", params)
        if payload.get("status") == "ZERO_RESULTS":
            return []

        suggestions = []
        for pred in payload.get("predictions", []):
            fmt = pred.get("structured_formatting", {})
            suggestions.append(
                AddressSuggestion(
                    description=pred.get("description", ""),
                    place_id=pred.get("place_id", ""),
                    main_text=fmt.get("main_text", ""),
                    secondary_text=fmt.get("secondary_text", ""),
                )
            )
        return suggestions

    async def health_check(self) -> bool:
        try:
            await self.geocode("Chennai Central, Chennai")
            return True
        except Exception as exc:  # noqa: BLE001 - health check must not raise
            logger.warning("google_health_check_failed", error=str(exc))
            return False
