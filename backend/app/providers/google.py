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
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.enums import DistanceType
from app.core.errors import (
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
_GEOCODING_V4 = "https://geocode.googleapis.com/v4/geocode"
_PLACES_V1 = "https://places.googleapis.com/v1"

# Google's Distance Matrix limit is 25 destinations per request (and 100
# elements). We chunk to stay inside it; CANDIDATE_MAX_COUNT is normally well
# below this, so chunking is a safety net rather than the usual path.
_MAX_DESTINATIONS_PER_REQUEST = 25

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

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        field_mask: str | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RoutingProviderError(
                "GOOGLE_MAPS_API_KEY is not configured.",
                code="PROVIDER_NOT_CONFIGURED",
            )
        headers = {"X-Goog-Api-Key": self.api_key}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if field_mask:
            headers["X-Goog-FieldMask"] = field_mask
        response = await self.client.request(
            method, url, params=params, json=body, headers=headers
        )
        if response.status_code == 429:
            raise RoutingRateLimitError()
        if response.status_code >= 500:
            raise RoutingProviderError(
                f"Google returned HTTP {response.status_code}.",
                code="PROVIDER_HTTP_5XX",
            )
        if response.status_code >= 400:
            try:
                error_body = response.json().get("error", {})
            except (ValueError, AttributeError):
                error_body = {}
            if response.status_code == 404 or error_body.get("status") == "NOT_FOUND":
                raise GeocodingNoResultError("Google could not find that address.")
            message = error_body.get("message") or (
                f"Google Maps request failed with HTTP {response.status_code}."
            )
            raise RoutingProviderError(
                str(message),
                code="PROVIDER_REQUEST_FAILED",
            )
        return response.json()

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
        search_body: dict[str, Any] = {
            "textQuery": address,
            "languageCode": settings.GOOGLE_MAPS_LANGUAGE,
            # Keep Google's ranked candidate set intact. Asking for only one
            # result can change which place Google ranks first for ambiguous
            # queries such as a station name plus a broad postal address.
            "maxResultCount": 8,
        }
        if settings.GOOGLE_MAPS_REGION:
            search_body["regionCode"] = settings.GOOGLE_MAPS_REGION.upper()
        search_payload = await self._request_json(
            "POST",
            f"{_PLACES_V1}/places:searchText",
            body=search_body,
            field_mask=(
                "places.id,places.displayName,places.formattedAddress,"
                "places.location,places.addressComponents,places.types,places.viewport"
            ),
        )
        places = search_payload.get("places") or []
        if places:
            return self._parse_place_result(
                places[0], address, provider="google_places_text_search"
            )

        # Text Search can return no place for a postal or administrative
        # address. Keep address geocoding as a fallback for those cases.
        address_query = quote(address, safe=",")
        payload = await self._request_json(
            "GET",
            f"{_GEOCODING_V4}/address/{address_query}",
            params={
                "languageCode": settings.GOOGLE_MAPS_LANGUAGE,
                **(
                    {"regionCode": settings.GOOGLE_MAPS_REGION.upper()}
                    if settings.GOOGLE_MAPS_REGION
                    else {}
                ),
            },
        )
        return self._parse_geocode_result(payload, address, provider=self.name)

    async def geocode_place(
        self, place_id: str, address: str, session_token: str | None = None
    ) -> GeocodeResult:
        params = {"sessionToken": session_token} if session_token else None
        payload = await self._request_json(
            "GET",
            f"{_PLACES_V1}/places/{quote(place_id, safe='')}",
            params=params,
            field_mask=(
                "id,displayName,formattedAddress,location,addressComponents,"
                "viewport,types,plusCode,googleMapsUri"
            ),
        )
        return self._parse_place_result(payload, address)

    @staticmethod
    def _components(items: list[dict[str, Any]]) -> dict[str, str]:
        components: dict[str, str] = {}
        for component in items:
            value = component.get("longText") or component.get("long_name")
            for component_type in component.get("types", []):
                if value:
                    components[component_type] = value
        return components

    @classmethod
    def _parse_geocode_result(
        cls, payload: dict[str, Any], address: str, *, provider: str
    ) -> GeocodeResult:
        results = payload.get("results") or []
        if not results:
            raise GeocodingNoResultError(
                f"No location found for: {address}",
            )

        best = results[0]
        location = best.get("location") or {}
        if "latitude" not in location or "longitude" not in location:
            raise RoutingProviderError(
                "Google returned an address without map coordinates.",
                code="PROVIDER_EMPTY_RESPONSE",
            )
        granularity = best.get("granularity", "APPROXIMATE")

        if granularity in {"ROOFTOP", "PREMISE"}:
            confidence = "high"
        elif granularity in {"RANGE_INTERPOLATED", "GEOMETRIC_CENTER"}:
            confidence = "medium"
        else:
            confidence = "low"

        components = cls._components(best.get("addressComponents", []))

        return GeocodeResult(
            latitude=float(location["latitude"]),
            longitude=float(location["longitude"]),
            formatted_address=best.get("formattedAddress", address),
            confidence=confidence,
            partial_match=confidence == "low",
            place_id=best.get("placeId"),
            components=components,
            provider=provider,
            details=best,
        )

    @classmethod
    def _parse_place_result(
        cls,
        place: dict[str, Any],
        fallback_address: str,
        *,
        provider: str = "google_places_v1",
    ) -> GeocodeResult:
        location = place.get("location") or {}
        if "latitude" not in location or "longitude" not in location:
            raise GeocodingNoResultError(f"No map location found for: {fallback_address}")
        types = place.get("types", [])
        exact_place_types = {
            "street_address",
            "premise",
            "subpremise",
            "establishment",
            "point_of_interest",
        }
        confidence = "high" if exact_place_types.intersection(types) else "medium"
        display_name = place.get("displayName", {}).get("text")
        return GeocodeResult(
            latitude=float(location["latitude"]),
            longitude=float(location["longitude"]),
            formatted_address=place.get("formattedAddress") or fallback_address,
            confidence=confidence,
            place_id=place.get("id"),
            components=cls._components(place.get("addressComponents", [])),
            provider=provider,
            details={**place, "displayNameText": display_name},
        )

    async def reverse_geocode(self, lat: float, lng: float) -> GeocodeResult:
        payload = await self._request_json(
            "GET",
            f"{_GEOCODING_V4}/location/{lat},{lng}",
            params={"languageCode": settings.GOOGLE_MAPS_LANGUAGE},
        )
        if not payload.get("results"):
            raise GeocodingNoResultError(f"No address found at {lat},{lng}")
        result = self._parse_geocode_result(
            payload, f"{lat},{lng}", provider=self.name
        )
        return GeocodeResult(
            latitude=lat,
            longitude=lng,
            formatted_address=result.formatted_address,
            confidence=result.confidence,
            partial_match=result.partial_match,
            place_id=result.place_id,
            components=result.components,
            provider=self.name,
            details=result.details,
        )

    async def autocomplete(
        self, query: str, session_token: str | None = None
    ) -> list[AddressSuggestion]:
        body: dict[str, Any] = {
            "input": query,
            "languageCode": settings.GOOGLE_MAPS_LANGUAGE,
        }
        if settings.GOOGLE_MAPS_REGION:
            body["regionCode"] = settings.GOOGLE_MAPS_REGION.upper()
        if session_token:
            body["sessionToken"] = session_token
        payload = await self._request_json(
            "POST",
            f"{_PLACES_V1}/places:autocomplete",
            body=body,
            field_mask=(
                "suggestions.placePrediction.placeId,suggestions.placePrediction.text,"
                "suggestions.placePrediction.structuredFormat"
            ),
        )

        suggestions = []
        for item in payload.get("suggestions", []):
            prediction = item.get("placePrediction")
            if not prediction:
                continue
            fmt = prediction.get("structuredFormat", {})
            main = (fmt.get("mainText") or {}).get("text", "")
            secondary = (fmt.get("secondaryText") or {}).get("text", "")
            suggestions.append(
                AddressSuggestion(
                    description=(prediction.get("text") or {}).get("text", ""),
                    place_id=prediction.get("placeId", ""),
                    main_text=main,
                    secondary_text=secondary,
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
