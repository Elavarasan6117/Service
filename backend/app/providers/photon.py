"""Komoot Photon geocoder.

Photon (https://photon.komoot.io) indexes the same OpenStreetMap data as
Nominatim, but is built specifically as a public search-as-you-type API: no
API key, and (unlike a raw self-hosted or the osm.org Nominatim instance,
whose usage policy is explicit that it is not meant for application traffic)
a rate limit generous enough for real interactive use. Prefer this over
``nominatim`` for anything beyond very light manual testing against the raw
OSM Nominatim endpoint.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import GeocodingNoResultError
from app.core.logging import get_logger
from app.providers.base import AddressSuggestion, GeocodeResult, is_latin_text

logger = get_logger(__name__)

# Photon's "type" field is coarser than Nominatim's numeric place_rank, but
# plays the same role here: higher = more specific. "other" is a named POI
# (a park, hospital, business, ...) via osm_key/osm_value, and is generally as
# specific as a house number.
_TYPE_RANK = {
    "house": 30,
    "other": 28,
    "street": 26,
    "locality": 20,
    "district": 18,
    "city": 14,
    "county": 10,
    "state": 6,
    "country": 2,
}
_GOOD_ENOUGH_RANK = 26


class PhotonProvider:
    name = "photon"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.PROVIDER_TIMEOUT_SECONDS),
                headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _search(self, query: str, limit: int = 1) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "q": query,
            "limit": limit,
            # Soft bias toward Chennai, same role as the Nominatim viewbox:
            # ranks nearby matches higher without excluding a genuinely
            # distant, correct match (a customer can be outside Chennai).
            "lat": settings.MAP_DEFAULT_CENTER_LAT,
            "lon": settings.MAP_DEFAULT_CENTER_LNG,
            # Place names in English (or the caller's chosen language) where a
            # translation/transliteration exists, rather than whichever local
            # script the place's own country happens to use.
            "lang": settings.GEOCODING_LANGUAGE,
        }
        response = await self.client.get(
            settings.PHOTON_BASE_URL.rstrip("/") + "/api/", params=params
        )
        response.raise_for_status()
        return response.json().get("features", [])

    @staticmethod
    def _display(properties: dict[str, Any]) -> str:
        seen: set[str] = set()
        parts: list[str] = []
        for key in (
            "name",
            "street",
            "locality",
            "district",
            "city",
            "county",
            "state",
            "postcode",
            "country",
        ):
            value = properties.get(key)
            # Requesting English (GEOCODING_LANGUAGE) only translates a field
            # that has an English name tagged in OSM; a smaller street or
            # neighbourhood often does not, and silently falls back to that
            # country's own script (seen in practice: Greek street names
            # coming through untranslated even with English requested).
            # Leaving the segment out entirely reads better than mixing
            # scripts the operator likely can't read.
            if value and value not in seen and is_latin_text(value):
                seen.add(value)
                parts.append(value)
        return ", ".join(parts)

    @classmethod
    def _result(cls, feature: dict[str, Any]) -> GeocodeResult:
        properties = feature.get("properties", {})
        lng, lat = feature["geometry"]["coordinates"]
        place_type = properties.get("type", "")
        confidence = "high" if place_type in {"house", "other"} else "medium"
        return GeocodeResult(
            latitude=float(lat),
            longitude=float(lng),
            formatted_address=cls._display(properties),
            confidence=confidence,
            partial_match=confidence != "high",
            place_id=str(properties.get("osm_id", "")) or None,
            components=properties,
            provider="photon",
        )

    async def geocode(self, address: str) -> GeocodeResult:
        # A trailing "State-123456" (hyphen glued to the pincode, no comma) is
        # common free-text input but reads as one unmatchable token. Split it
        # back into "State, 123456" before anything else runs.
        address = re.sub(r"-(\d{5,6})\b", r", \1", address)
        cleaned = ", ".join(part.strip() for part in address.split(",") if part.strip())
        parts = [part.strip() for part in cleaned.split(",") if part.strip()]
        queries = [cleaned]
        # Same fallback strategy proven out against Nominatim: drop leading
        # parts one at a time (front of an address is the most specific and
        # least likely to be indexed; the back is the most general and most
        # reliably resolvable), drop exactly one middle word at a time (two
        # places can share a village/town name -- the correct disambiguating
        # word can be anywhere, not just at an end), then fall back to each
        # part alone.
        if len(parts) > 1:
            queries.extend(", ".join(parts[start:]) for start in range(1, len(parts)))
            if len(parts) > 2:
                queries.extend(
                    ", ".join(parts[:i] + parts[i + 1 :]) for i in range(len(parts))
                )
            queries.extend(reversed(parts))

        pincode_match = re.search(r"\b(\d{6})\b", cleaned)
        target_postcode = pincode_match.group(1) if pincode_match else None
        non_numeric_parts = [p for p in parts if not p.strip().isdigit()]
        # Drop the trailing, most-generic-sounding segment(s) before checking
        # for a distinctive match -- but how many to drop depends on how long
        # the address is. A long Indian address ("Landmark, Locality, City,
        # State, PIN, Country") has state+country as genuinely generic
        # trailing noise; a short international one ("Eiffel Tower, Paris,
        # France") does not -- dropping 2 there removes the CITY, which is
        # exactly the word that disambiguates one same-named landmark from
        # another (seen in practice: "Eiffel Tower, Paris, France" resolving
        # to a same-named place in Alberta, Canada once "Paris" was dropped).
        if len(non_numeric_parts) > 4:
            trailing_drop = 2
        elif len(non_numeric_parts) > 1:
            trailing_drop = 1
        else:
            trailing_drop = 0
        distinctive_parts = [
            p.strip()
            for p in (non_numeric_parts[:-trailing_drop] if trailing_drop else non_numeric_parts)
            if len(p.strip()) > 2
        ]

        def score(feature: dict[str, Any]) -> tuple[bool, int, int]:
            properties = feature.get("properties", {})
            postcode_ok = (
                target_postcode is not None
                and properties.get("postcode") == target_postcode
            )
            display = self._display(properties).lower()
            # COUNT how many distinctive parts show up, not just whether any
            # do -- two different places can share one name, and a candidate
            # explaining only one of several distinctive words is much weaker
            # evidence than one explaining all of them.
            match_count = sum(1 for p in distinctive_parts if p.lower() in display)
            plausible = (
                postcode_ok or match_count > 0 or not (target_postcode or distinctive_parts)
            )
            return (plausible, match_count, _TYPE_RANK.get(properties.get("type", ""), 0))

        best: dict[str, Any] | None = None
        best_score: tuple[bool, int, int] = (False, -1, -1)
        last_exception: Exception | None = None
        any_clean_response = False

        for query in dict.fromkeys(queries):
            try:
                results = await self._search(query)
            except Exception as exc:  # noqa: BLE001 - one bad candidate must not sink the rest
                logger.warning("photon_query_failed", query=query, error=str(exc))
                last_exception = exc
                continue
            any_clean_response = True
            if not results:
                continue

            candidate = results[0]
            candidate_score = score(candidate)
            if candidate_score > best_score:
                best, best_score = candidate, candidate_score
            if best_score[0] and best_score[2] >= _GOOD_ENOUGH_RANK:
                break

        if best is None:
            if not any_clean_response and last_exception is not None:
                raise last_exception
            raise GeocodingNoResultError(f"No location found for: {address}")
        return self._result(best)

    async def reverse_geocode(self, lat: float, lng: float) -> GeocodeResult:
        response = await self.client.get(
            settings.PHOTON_BASE_URL.rstrip("/") + "/reverse",
            params={"lat": lat, "lon": lng, "lang": settings.GEOCODING_LANGUAGE},
        )
        response.raise_for_status()
        features = response.json().get("features", [])
        if not features:
            raise GeocodingNoResultError(f"No address found at {lat},{lng}")
        result = self._result(features[0])
        # Reverse geocoding of a point the operator already placed is always
        # treated as confirmed -- there is no "partial match" concept when
        # the coordinate, not the text, is the source of truth.
        return GeocodeResult(
            latitude=lat,
            longitude=lng,
            formatted_address=result.formatted_address,
            confidence="high",
            place_id=result.place_id,
            components=result.components,
            provider=self.name,
        )

    async def autocomplete(
        self, query: str, session_token: str | None = None
    ) -> list[AddressSuggestion]:
        results = await self._search(query, limit=5)
        suggestions = []
        for feature in results:
            properties = feature.get("properties", {})
            description = self._display(properties)
            name = properties.get("name")
            main = (
                name if name and is_latin_text(name) else description.split(", ")[0]
            )
            secondary = ", ".join(
                part for part in description.split(", ") if part != main
            )
            suggestions.append(
                AddressSuggestion(
                    description=description,
                    place_id=str(properties.get("osm_id", "")),
                    main_text=main,
                    secondary_text=secondary,
                )
            )
        return suggestions

    async def health_check(self) -> bool:
        try:
            await self._search("Chennai, Tamil Nadu, India")
            return True
        except Exception:
            return False
