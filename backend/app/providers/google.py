"""Google Maps Platform adapter.

APIs used, all server-side:
  * Geocoding API          — address → coordinates
  * Places Autocomplete    — address search suggestions
  * Routes API             — computeRouteMatrix road distance ranking and
                             computeRoutes duration, alternatives and geometry

The API key is read from settings and never leaves the backend. The browser
receives only the results of these calls.
"""

from __future__ import annotations

import json
import re
import time
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
_ROUTES_V2 = "https://routes.googleapis.com"

# Keep requests comfortably within Routes API matrix element limits. The
# serviceability candidate limit is normally much lower; chunking is a safety
# net and preserves the provider interface's ability to accept larger lists.
_MAX_DESTINATIONS_PER_REQUEST = 25


def _lat_lng(point: Coordinate) -> dict[str, float]:
    # Coordinate validates the API's numeric bounds in __post_init__.
    return {"latitude": float(point.latitude), "longitude": float(point.longitude)}


def _duration_seconds(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)s", value)
    return int(round(float(match.group(1)))) if match else None


def _safe_google_error_body(response: httpx.Response, payload: Any) -> str:
    """Keep useful Google diagnostics while never logging credential fields."""
    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: ("[redacted]" if re.search(r"key|token|credential|secret", key, re.I) else redact(item))
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, str):
            value = re.sub(r"AIza[0-9A-Za-z_-]{20,}", "[redacted]", value)
            return re.sub(r"(?i)([?&](?:key|token|access_token)=)[^&\s]+", r"\1[redacted]", value)
        return value

    if isinstance(payload, dict):
        body: Any = payload.get("error", payload)
    else:
        body = response.text[:1000]
    return json.dumps(redact(body), ensure_ascii=True, default=str)[:1500]

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
            logger.error("google_maps_request_not_configured", operation=path.strip("/"), api_key_configured=False)
            raise RoutingProviderError(
                "GOOGLE_MAPS_API_KEY is not configured.",
                code="PROVIDER_NOT_CONFIGURED",
            )
        params = {**params, "key": self.api_key}
        started = time.perf_counter()
        response = await self.client.get(f"{_BASE}{path}", params=params)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        try:
            diagnostic_payload = response.json()
        except ValueError:
            diagnostic_payload = None
        error = diagnostic_payload.get("error", {}) if isinstance(diagnostic_payload, dict) else {}
        status_value = diagnostic_payload.get("status") if isinstance(diagnostic_payload, dict) else None
        logger.info(
            "google_maps_request",
            operation=path.strip("/"),
            http_status=response.status_code,
            error_code=error.get("status") or status_value or error.get("code"),
            duration_ms=elapsed_ms,
            api_key_configured=True,
            response_body=_safe_google_error_body(response, diagnostic_payload) if response.is_error or status_value not in (None, "OK") else None,
        )

        if response.status_code == 429:
            raise RoutingRateLimitError(code="PROVIDER_QUOTA_EXCEEDED")
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
                code={
                    "REQUEST_DENIED": "PROVIDER_REQUEST_DENIED",
                    "INVALID_REQUEST": "PROVIDER_INVALID_REQUEST",
                    "UNKNOWN_ERROR": "PROVIDER_HTTP_5XX",
                }[status_value],
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
    ) -> Any:
        if not self.api_key:
            logger.error("google_maps_request_not_configured", operation=url.rsplit("/", 1)[-1], api_key_configured=False)
            raise RoutingProviderError(
                "GOOGLE_MAPS_API_KEY is not configured.",
                code="PROVIDER_NOT_CONFIGURED",
            )
        headers = {"X-Goog-Api-Key": self.api_key}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if field_mask:
            headers["X-Goog-FieldMask"] = field_mask
        started = time.perf_counter()
        try:
            response = await self.client.request(
                method, url, params=params, json=body, headers=headers
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "google_maps_request_failed",
                operation=url.rsplit("/", 1)[-1],
                duration_ms=round((time.perf_counter() - started) * 1000),
                error_type=type(exc).__name__,
                api_key_configured=True,
            )
            raise

        elapsed_ms = round((time.perf_counter() - started) * 1000)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        matrix_errors = (
            [element.get("status", {}) for element in payload
             if isinstance(element, dict) and element.get("status", {}).get("code", 0)]
            if isinstance(payload, list) else []
        )
        api_error_code = error.get("status") or error.get("code") or (
            [item.get("code") for item in matrix_errors] or None
        )
        has_api_error = response.is_error or bool(matrix_errors)
        response_body = _safe_google_error_body(response, payload) if has_api_error else None
        logger.info(
            "google_maps_request",
            operation=url.rsplit("/", 1)[-1],
            http_status=response.status_code,
            error_code=api_error_code,
            duration_ms=elapsed_ms,
            api_key_configured=True,
            response_body=response_body,
        )

        if response.status_code == 429 or api_error_code in ("RESOURCE_EXHAUSTED", "RATE_LIMIT_EXCEEDED") or (
            isinstance(api_error_code, list) and 8 in api_error_code
        ):
            raise RoutingRateLimitError(
                "Google Routes API quota or rate limit was reached.",
                code="PROVIDER_QUOTA_EXCEEDED",
            )
        if response.status_code >= 500:
            raise RoutingProviderError(
                f"Google Maps returned HTTP {response.status_code} ({api_error_code or 'upstream error'}).",
                code="PROVIDER_HTTP_5XX",
            )
        if response.status_code >= 400:
            if response.status_code == 404 and ("geocode" in url or "places" in url):
                raise GeocodingNoResultError("Google could not find that address.")
            google_message = str(error.get("message") or "No error message was returned.")
            api_label = (
                "Google Routes API" if url.startswith(_ROUTES_V2)
                else "Google Maps Platform API"
            )
            safe_message = (
                f"{api_label} returned HTTP {response.status_code} "
                f"({error.get('status') or error.get('code') or 'HTTP_ERROR'}): "
                f"{google_message}"
            )
            if response.status_code == 401:
                code = "PROVIDER_UNAUTHENTICATED"
            elif response.status_code == 403:
                code = "PROVIDER_REQUEST_DENIED"
            elif response.status_code == 400:
                code = "PROVIDER_INVALID_REQUEST"
            else:
                code = "PROVIDER_CLIENT_ERROR"
            raise RoutingProviderError(safe_message[:500], code=code)
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

    async def get_driving_distances(
        self, origins: list[Coordinate], destination: Coordinate
    ) -> list[RouteLeg | None]:
        """Road distances for the preview shortlist using Routes API matrix."""
        if not origins:
            return []
        payload = await self._request_json(
            "POST",
            f"{_ROUTES_V2}/distanceMatrix/v2:computeRouteMatrix",
            body={
                "origins": [
                    {"waypoint": {"location": {"latLng": _lat_lng(origin)}}}
                    for origin in origins
                ],
                "destinations": [
                    {"waypoint": {"location": {"latLng": _lat_lng(destination)}}}
                ],
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_UNAWARE",
            },
            field_mask="originIndex,destinationIndex,duration,distanceMeters,status,condition",
        )
        if not isinstance(payload, list):
            raise RoutingProviderError(
                "Google Routes API returned an invalid route matrix response.",
                code="PROVIDER_INVALID_RESPONSE",
            )
        results: list[RouteLeg | None] = [None] * len(origins)
        for element in payload:
            if not isinstance(element, dict):
                continue
            origin_index = element.get("originIndex")
            if not isinstance(origin_index, int) or not 0 <= origin_index < len(origins):
                continue
            element_status = element.get("status", {})
            status_code = element_status.get("code", 0)
            if status_code:
                error_name = element_status.get("status")
                if status_code == 3:
                    raise RoutingProviderError(
                        str(element_status.get("message") or "Google rejected a route matrix coordinate."),
                        code="PROVIDER_INVALID_REQUEST",
                    )
                if status_code in (7, 16):
                    raise RoutingProviderError(
                        str(element_status.get("message") or "Google denied Routes API access."),
                        code="PROVIDER_REQUEST_DENIED" if status_code == 7 else "PROVIDER_UNAUTHENTICATED",
                    )
                if status_code == 8:
                    raise RoutingRateLimitError(
                        str(element_status.get("message") or "Google Routes API quota was exceeded."),
                        code="PROVIDER_QUOTA_EXCEEDED",
                    )
                if status_code == 14:
                    raise RoutingProviderError(
                        str(element_status.get("message") or "Google Routes API is unavailable."),
                        code="PROVIDER_HTTP_5XX",
                    )
                logger.warning("google_routes_matrix_element_unavailable", status=error_name, status_code=status_code)
                continue
            if element.get("condition") != "ROUTE_EXISTS":
                continue
            distance = element.get("distanceMeters")
            if distance is None:
                continue
            results[origin_index] = RouteLeg(
                distance_meters=int(distance),
                duration_seconds=_duration_seconds(element.get("duration")),
                distance_type=DistanceType.ROAD_DISTANCE,
                provider=self.name,
            )
        return results

    async def _distance_matrix_chunk(
        self, origin: Coordinate, destinations: list[Coordinate]
    ) -> list[RouteLeg | None]:
        payload = await self._request_json(
            "POST",
            f"{_ROUTES_V2}/distanceMatrix/v2:computeRouteMatrix",
            body={
                "origins": [
                    {"waypoint": {"location": {"latLng": _lat_lng(origin)}}}
                ],
                "destinations": [
                    {"waypoint": {"location": {"latLng": _lat_lng(point)}}}
                    for point in destinations
                ],
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_UNAWARE",
            },
            field_mask="originIndex,destinationIndex,duration,distanceMeters,status,condition",
        )
        if not isinstance(payload, list):
            raise RoutingProviderError(
                "Google Routes API returned an invalid route matrix response.",
                code="PROVIDER_INVALID_RESPONSE",
            )
        if len(payload) != len(destinations):
            raise RoutingProviderError(
                "Google Routes API returned a mismatched number of matrix elements; "
                "the response cannot be aligned to the requested destinations.",
                code="PROVIDER_RESPONSE_MISALIGNED",
            )

        legs: list[RouteLeg | None] = [None] * len(destinations)
        seen_destinations: set[int] = set()
        for element in payload:
            if not isinstance(element, dict) or element.get("originIndex", 0) != 0:
                continue
            destination_index = element.get("destinationIndex")
            if not isinstance(destination_index, int) or not 0 <= destination_index < len(destinations):
                continue
            if destination_index in seen_destinations:
                raise RoutingProviderError(
                    "Google Routes API returned duplicate matrix elements.",
                    code="PROVIDER_RESPONSE_MISALIGNED",
                )
            seen_destinations.add(destination_index)

            status = element.get("status") or {}
            status_code = status.get("code", 0)
            if status_code == 3:
                raise RoutingProviderError(
                    str(status.get("message") or "Google rejected a route matrix coordinate."),
                    code="PROVIDER_INVALID_REQUEST",
                )
            if status_code in (7, 16):
                raise RoutingProviderError(
                    str(status.get("message") or "Google denied Routes API access."),
                    code="PROVIDER_REQUEST_DENIED" if status_code == 7 else "PROVIDER_UNAUTHENTICATED",
                )
            if status_code == 8:
                raise RoutingRateLimitError(
                    str(status.get("message") or "Google Routes API quota was exceeded."),
                    code="PROVIDER_QUOTA_EXCEEDED",
                )
            if status_code in (4, 13, 14):
                raise RoutingProviderError(
                    str(status.get("message") or "Google Routes API is temporarily unavailable."),
                    code="PROVIDER_HTTP_5XX",
                )
            if status_code:
                logger.warning(
                    "google_routes_matrix_element_unavailable",
                    status=status.get("status"),
                    status_code=status_code,
                    destination_index=destination_index,
                )
                continue
            if element.get("condition") != "ROUTE_EXISTS":
                # ROUTE_NOT_FOUND is a per-pair outcome; other candidates may
                # still produce a valid nearest service location.
                continue
            distance = element.get("distanceMeters")
            if distance is None:
                continue
            legs[destination_index] = RouteLeg(
                distance_meters=int(distance),
                duration_seconds=_duration_seconds(element.get("duration")),
                distance_type=DistanceType.ROAD_DISTANCE,
                provider=self.name,
            )
        if len(seen_destinations) != len(destinations):
            raise RoutingProviderError(
                "Google Routes API returned an incomplete route matrix.",
                code="PROVIDER_RESPONSE_MISALIGNED",
            )
        return legs

    async def get_driving_route(
        self, origin: Coordinate, destination: Coordinate
    ) -> RouteDetail:
        return (await self.get_driving_routes(origin, destination))[0]

    async def get_driving_routes(
        self, origin: Coordinate, destination: Coordinate
    ) -> list[RouteDetail]:
        payload = await self._request_json(
            "POST",
            f"{_ROUTES_V2}/directions/v2:computeRoutes",
            body={
                "origin": {"location": {"latLng": _lat_lng(origin)}},
                "destination": {"location": {"latLng": _lat_lng(destination)}},
                "travelMode": "DRIVE",
                "routingPreference": "TRAFFIC_UNAWARE",
                "computeAlternativeRoutes": True,
                "polylineQuality": "OVERVIEW",
                "polylineEncoding": "ENCODED_POLYLINE",
                "units": "METRIC",
                "languageCode": settings.GOOGLE_MAPS_LANGUAGE,
                **({"regionCode": settings.GOOGLE_MAPS_REGION.upper()} if settings.GOOGLE_MAPS_REGION else {}),
            },
            field_mask="routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline",
        )
        routes = payload.get("routes") or [] if isinstance(payload, dict) else []
        if not routes:
            raise RoutingProviderError(
                "No driving route found between these points.",
                code="PROVIDER_NO_ROUTE",
            )
        details = []
        for route in routes:
            distance = route.get("distanceMeters", 0)
            duration = _duration_seconds(route.get("duration"))
            geometry = (route.get("polyline") or {}).get("encodedPolyline")
            details.append(RouteDetail(
                distance_meters=int(round(float(distance))),
                duration_seconds=duration,
                geometry=geometry,
                geometry_format="encoded_polyline_5" if geometry else "none",
                provider=self.name,
            ))
        return details

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
