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

import asyncio
import re
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import GeocodingNoResultError
from app.core.logging import get_logger
from app.providers.base import (
    AddressSuggestion,
    GeocodeResult,
    is_latin_text,
    normalize_for_matching,
)

logger = get_logger(__name__)

# Photon's "type" field is coarser than Nominatim's numeric place_rank, but
# plays the same role here: higher = more specific. "other" is a named POI
# (a park, hospital, business, ...) via osm_key/osm_value, and is generally as
# specific as a house number.
_CORPORATE_SUFFIX_RE = re.compile(
    r"\b(pvt\.?\s*ltd\.?|private\s+limited|ltd\.?|limited|inc\.?|llp)\.?\b",
    re.IGNORECASE,
)


def _stem(word: str) -> str:
    """Crude plural strip ("Hospitals" -> "Hospital") so a same-brand
    candidate tagged with the singular form in OSM doesn't lose a name-match
    tie purely on pluralization -- not real stemming, just enough to stop
    that one common mismatch from deciding a result."""
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _strip_corporate_suffix(text: str) -> str:
    """Drop "Pvt Ltd" / "Private Limited" / etc.

    Near-universal boilerplate across Indian business names -- Photon's
    free-text ranking treats it as an ordinary matching word, which dilutes
    relevance enough that the actual distinctive name in a query like
    "Apollo Hospitals Pvt. Ltd." can fail to surface at all versus some
    unrelated "... Pvt Ltd." on the same road.
    """
    stripped = _CORPORATE_SUFFIX_RE.sub("", text)
    return re.sub(r"\s+", " ", stripped).strip(" ,")


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

        # Retry every fallback above with corporate boilerplate stripped, so
        # a diluted match doesn't crowd out the real one (see
        # _strip_corporate_suffix).
        queries.extend(
            stripped
            for q in list(queries)
            if (stripped := _strip_corporate_suffix(q)) and stripped != q
        )

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
            normalize_for_matching(p)
            for p in (non_numeric_parts[:-trailing_drop] if trailing_drop else non_numeric_parts)
            if len(p.strip()) > 2
        ]
        # A named business/landmark conventionally leads an Indian address
        # ("Apollo Hospitals Pvt Ltd, Greams Road, Chennai"), and is exactly
        # the part a whole-phrase substring check is too rigid to match --
        # OSM's own record is rarely word-for-word identical (plural,
        # abbreviation, missing "Pvt Ltd"). Comparing individual words
        # against the CANDIDATE'S OWN name field specifically (not its full
        # display, which includes the street/area and would let an unrelated
        # same-street business tie on "Greams Road" alone) catches that case
        # without the false-positive risk of scoring generic words anywhere
        # in the display.
        primary_name_words: set[str] = set()
        if non_numeric_parts:
            first = normalize_for_matching(_strip_corporate_suffix(non_numeric_parts[0]))
            primary_name_words = {_stem(w) for w in first.split() if len(w) > 2}

        def score(feature: dict[str, Any]) -> tuple[bool, int, int, int]:
            properties = feature.get("properties", {})
            postcode_ok = (
                target_postcode is not None
                and properties.get("postcode") == target_postcode
            )
            # Normalized the same way as distinctive_parts above, so "Ganga
            # Medical Centre & Hospitals Pvt Ltd" (typed) matches OSM's own
            # "Ganga Medical Centre and Hospitals Pvt. Ltd" -- a raw
            # substring check treats those as unrelated text.
            display = normalize_for_matching(self._display(properties))
            # COUNT how many distinctive parts show up, not just whether any
            # do -- two different places can share one name, and a candidate
            # explaining only one of several distinctive words is much weaker
            # evidence than one explaining all of them.
            match_count = sum(1 for p in distinctive_parts if p in display)
            candidate_name_words = {
                _stem(w)
                for w in normalize_for_matching(properties.get("name") or "").split()
            }
            name_match_count = len(primary_name_words & candidate_name_words)
            plausible = (
                postcode_ok
                or match_count > 0
                or name_match_count > 0
                or not (target_postcode or distinctive_parts)
            )
            return (
                plausible,
                name_match_count,
                match_count,
                _TYPE_RANK.get(properties.get("type", ""), 0),
            )

        best: dict[str, Any] | None = None
        best_score: tuple[bool, int, int, int] = (False, -1, -1, -1)
        last_exception: Exception | None = None
        any_clean_response = False

        # Every query variant is independent -- run them with bounded
        # concurrency rather than one at a time. Corporate-suffix stripping
        # roughly doubles the variant count on top of the existing
        # leading/middle/reversed fallbacks, and awaiting each sequentially
        # (a real regression caught live: one address took 25+ seconds and
        # hit Render's own gateway timeout) defeats the whole point of
        # dropping the early-exit for correctness. Firing all of them at once
        # is no better -- Photon's public instance rate-limits a burst that
        # size and starts returning 503s for most of them (also caught live:
        # latency dropped to under 3s, but most queries failed and a bad
        # fallback candidate won). A small semaphore keeps wall-clock time
        # low without looking like abuse to Photon's own throttling.
        semaphore = asyncio.Semaphore(4)

        async def _bounded_search(q: str) -> list[dict[str, Any]]:
            async with semaphore:
                return await self._search(q, limit=5)

        unique_queries = list(dict.fromkeys(queries))
        responses = await asyncio.gather(
            *(_bounded_search(q) for q in unique_queries),
            return_exceptions=True,
        )

        for query, outcome in zip(unique_queries, responses):
            if isinstance(outcome, BaseException):
                logger.warning("photon_query_failed", query=query, error=str(outcome))
                last_exception = outcome
                continue
            any_clean_response = True
            results = outcome
            if not results:
                continue

            for candidate in results:
                candidate_score = score(candidate)
                if candidate_score > best_score:
                    best, best_score = candidate, candidate_score
            # No early exit: a fallback query that drops down to one bare,
            # generic word (e.g. "Mettupalayam Road" alone) can trivially
            # satisfy "plausible + decent type rank" by matching a
            # same-named street in an entirely different town, well before
            # the query variant carrying the actual business/landmark name
            # is ever tried. Photon is free and fast enough that evaluating
            # every fallback query and keeping the true best-scoring one is
            # worth the extra requests -- this is exactly the bug that sent
            # "Ganga Medical Centre ..., Coimbatore" to a same-postcode
            # government office instead of the actual hospital.

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
