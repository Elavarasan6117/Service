"""OpenStreetMap Nominatim geocoder for local development.

Nominatim supplies real address coordinates without exposing a provider key to
 the browser. It is intended for low-volume local use; production deployments
 should use Google Maps or another contracted geocoder.
"""

from __future__ import annotations

from typing import Any
import re

import httpx
from openlocationcode import openlocationcode as olc

from app.core.config import settings
from app.core.errors import GeocodingNoResultError
from app.core.logging import get_logger
from app.providers.base import AddressSuggestion, GeocodeResult, is_latin_text

logger = get_logger(__name__)

# Nominatim's own specificity score for a result: 0 = continent, ~16 = city,
# ~21 = postcode/suburb, 26+ = street, 30 = a building or POI. Below this, a
# fallback query keeps searching for something more precise rather than
# settling for the first (typically too-broad) match.
_GOOD_ENOUGH_PLACE_RANK = 26


class NominatimProvider:
    name = "nominatim"

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

    def _viewbox_params(self) -> dict[str, Any]:
        pad = settings.NOMINATIM_VIEWBOX_DEGREES
        params: dict[str, Any] = {
            "viewbox": (
                f"{settings.MAP_DEFAULT_CENTER_LNG - pad},"
                f"{settings.MAP_DEFAULT_CENTER_LAT + pad},"
                f"{settings.MAP_DEFAULT_CENTER_LNG + pad},"
                f"{settings.MAP_DEFAULT_CENTER_LAT - pad}"
            ),
            "bounded": 0,
            # Place names in English (or the caller's chosen language) where a
            # translation/transliteration exists, rather than whichever local
            # script the place's own country happens to use.
            "accept-language": settings.GEOCODING_LANGUAGE,
        }
        if settings.NOMINATIM_COUNTRY_CODES.strip():
            params["countrycodes"] = settings.NOMINATIM_COUNTRY_CODES.strip()
        return params

    async def _search_structured(
        self, street: str | None, postalcode: str | None, limit: int = 1
    ) -> list[dict[str, Any]]:
        """Nominatim's structured mode: named fields instead of one guessed
        string. This is the same trick a well-built geocoder (Google's
        included) leans on -- when the caller already knows which part of an
        address is the street and which is the PIN code, saying so directly
        resolves cases a free-text guess over the whole string gets wrong
        (see the fallback prefixes in geocode() -- a bare PIN code can win the
        free-text race before the actual landmark is ever tried).
        """
        params: dict[str, Any] = {
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": limit,
            **self._viewbox_params(),
        }
        if street:
            params["street"] = street
        if postalcode:
            params["postalcode"] = postalcode
        response = await self.client.get(
            settings.NOMINATIM_BASE_URL.rstrip("/") + "/search",
            params=params,
        )
        response.raise_for_status()
        return response.json()

    async def _search(self, query: str, limit: int = 1) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "q": query,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": limit,
            **self._viewbox_params(),
        }
        response = await self.client.get(
            settings.NOMINATIM_BASE_URL.rstrip("/") + "/search",
            params=params,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _format_display(display_name: str) -> str:
        # Requesting English (GEOCODING_LANGUAGE) only translates a segment
        # that has an English name tagged in OSM; a smaller street or
        # neighbourhood often does not, and silently falls back to that
        # country's own script. Leaving the segment out entirely reads better
        # than mixing scripts the operator likely can't read.
        return ", ".join(
            part.strip()
            for part in display_name.split(",")
            if part.strip() and is_latin_text(part)
        )

    @classmethod
    def _result(cls, item: dict[str, Any]) -> GeocodeResult:
        address_type = item.get("type", "")
        confidence = "high" if address_type in {"house", "building", "address"} else "medium"
        return GeocodeResult(
            latitude=float(item["lat"]),
            longitude=float(item["lon"]),
            formatted_address=cls._format_display(item.get("display_name", "")),
            confidence=confidence,
            partial_match=confidence != "high",
            place_id=str(item.get("place_id", "")) or None,
            components=item.get("address", {}),
            provider="nominatim",
        )

    async def geocode(self, address: str) -> GeocodeResult:
        # A trailing "State-123456" (hyphen glued to the pincode, no comma) is
        # common free-text input but reads to Nominatim as one unmatchable
        # token. Split it back into "State, 123456" before anything else runs.
        address = re.sub(r"-(\d{5,6})\b", r", \1", address)
        cleaned = ", ".join(part.strip() for part in address.split(",") if part.strip())
        plus_code_match = re.search(
            r"\b[23456789CFGHJMPQRVWX]{4,8}\+[23456789CFGHJMPQRVWX]{2,}\b",
            cleaned.upper(),
        )
        if plus_code_match:
            # Short Plus Codes need a nearby point to restore their omitted
            # area prefix. Use the named locality, then decode the code exactly.
            locality_parts = [
                part
                for part in cleaned.split(",")
                if "+" not in part and not re.search(r"\b\d{6}\b", part)
            ]
            reference_results: list[dict[str, Any]] = []
            for locality in reversed(locality_parts):
                if locality.lower() in {"tamil nadu", "india", "chennai"}:
                    continue
                reference_results = await self._search(locality, limit=1)
                if reference_results:
                    break
            if reference_results:
                reference = reference_results[0]
                full_code = olc.recoverNearest(
                    plus_code_match.group(0),
                    float(reference["lat"]),
                    float(reference["lon"]),
                )
                decoded = olc.decode(full_code)
                return GeocodeResult(
                    latitude=decoded.latitudeCenter,
                    longitude=decoded.longitudeCenter,
                    formatted_address=cleaned,
                    confidence="high",
                    place_id=full_code,
                    provider="nominatim-plus-code",
                )
        parts = [part.strip() for part in cleaned.split(",") if part.strip()]
        queries = [cleaned]
        # A long address may include a floor number, building label, or local
        # spelling that is absent from the map index. Drop leading parts one
        # at a time -- keeping the trailing locality/state/pincode context
        # intact -- rather than truncating from the back: the front of an
        # address is the most specific (and least likely to be indexed) part,
        # the back is the most general (and most reliably resolvable) part.
        # Truncating from the back tries exactly the wrong fragments first --
        # a generic leading phrase like "3rd Floor" can spuriously match some
        # unrelated place before the real city/state is ever tried.
        if len(parts) > 1:
            queries.extend(", ".join(parts[start:]) for start in range(1, len(parts)))
            # A single word can also be the problem in the MIDDLE of the
            # address rather than at either end: two places in India can
            # share a village/town name (seen in practice: querying "Mettur,
            # Salem, Tamil Nadu" returns a same-named Mettur in Andhra
            # Pradesh -- Nominatim's own text matching can weight an exact
            # name hit over the correct surrounding district). Dropping
            # exactly that one word while keeping everything else is what
            # resolves it; front/back truncation alone never produces that
            # combination.
            if len(parts) > 2:
                queries.extend(
                    ", ".join(parts[:i] + parts[i + 1 :]) for i in range(len(parts))
                )
            # Last resort: each comma-separated part on its own, most specific
            # first. A floor number or building label earlier in the address
            # can otherwise sink every combined query even though the plain
            # city/state name would have resolved fine by itself.
            queries.extend(reversed(parts))

        # Nominatim occasionally answers a garbled multi-word query with some
        # unrelated high-importance landmark elsewhere in the same state
        # (seen in practice: a rural Telangana village query returning the
        # Telangana High Court, ~100 km away) rather than failing cleanly.
        # place_rank alone cannot catch this -- the wrong landmark is a real
        # building, so it scores as "specific" as a correct one.
        #
        # Two independent plausibility checks catch it, either one sufficing:
        #   - postcode agreement, when the input names a PIN code;
        #   - the candidate's own display name mentioning one of the input's
        #     distinctive place-name segments (excluding the trailing
        #     state/country, which is too generic to prove anything -- every
        #     candidate in the same state mentions it).
        # Postcode agreement alone is too strict on its own: a genuinely
        # better, more specific match can legitimately carry a different PIN
        # code than the one the operator typed (post-code boundaries do not
        # always track physical proximity, and a newly built landmark may not
        # even have a settled one). The containment check is what actually
        # distinguishes "a real match for this place" from "some unrelated
        # landmark that merely happens to be in the same state" when the two
        # disagree.
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

        def score(candidate: dict[str, Any]) -> tuple[bool, int, int]:
            postcode_ok = (
                target_postcode is not None
                and candidate.get("address", {}).get("postcode") == target_postcode
            )
            display = candidate.get("display_name", "").lower()
            # COUNT how many distinctive parts show up, not just whether any
            # do: two different places can share one name (seen in practice:
            # a same-named "Mettur" village in Andhra Pradesh, not the actual
            # Salem-district one). A candidate whose text explains only one of
            # several distinctive words is far weaker evidence than one that
            # explains all of them, even when both technically match on
            # something -- this is what actually tells the real match apart
            # from a same-named coincidence elsewhere, which place_rank alone
            # cannot.
            match_count = sum(1 for p in distinctive_parts if p.lower() in display)
            plausible = (
                postcode_ok or match_count > 0 or not (target_postcode or distinctive_parts)
            )
            return (plausible, match_count, candidate.get("place_rank") or 0)

        best: dict[str, Any] | None = None
        best_score: tuple[bool, int, int] = (False, -1, -1)
        last_exception: Exception | None = None
        any_clean_response = False

        # The free-text chain runs first: it is the well-tested path, and it
        # is the only one that has ever been verified to land correctly for a
        # messy real-world address. place_rank is Nominatim's own specificity
        # score (0 = continent, 30 = a building/POI) -- the first query to
        # return ANYTHING is not necessarily the best one (a generic trailing
        # fragment like a bare "State, PIN code" is much easier to match than
        # the actual landmark), so keep searching until something
        # building/street-level turns up, but always remember the best match
        # seen in case nothing better ever does.
        for query in dict.fromkeys(queries):
            # A single fallback candidate failing outright (a malformed
            # response, a transient block, a slow timeout) must not sink every
            # later, more-reliable candidate in the chain -- record it and keep
            # going. Only surface it if nothing in the whole chain ever got a
            # clean answer.
            try:
                results = await self._search(query)
            except Exception as exc:  # noqa: BLE001 - see comment above
                logger.warning(
                    "nominatim_query_failed", query=query, error=str(exc)
                )
                last_exception = exc
                continue
            any_clean_response = True
            if not results:
                continue

            candidate = results[0]
            candidate_score = score(candidate)
            if candidate_score > best_score:
                best, best_score = candidate, candidate_score
            if best_score[0] and best_score[2] >= _GOOD_ENOUGH_PLACE_RANK:
                break

        # Structured mode is consulted only as a supplement, and only when the
        # free-text chain above never found a postcode-agreeing, sufficiently
        # specific match: it can succeed where free text fails (a bare PIN
        # code plus the leading segment sometimes resolves a specific
        # landmark free text never reaches), but parts[0] is often not really
        # a street (a floor or building label), and structured mode can
        # silently ignore a bad "street" value and match on the postcode alone
        # -- giving a confidently-wrong result in the wrong city. It must
        # never be allowed to outrank an already-good free-text match, only to
        # improve on a poor one.
        if (
            target_postcode
            and parts
            and not (best_score[0] and best_score[2] >= _GOOD_ENOUGH_PLACE_RANK)
        ):
            try:
                structured_results = await self._search_structured(
                    parts[0], target_postcode
                )
            except Exception as exc:  # noqa: BLE001 - see comment above
                logger.warning(
                    "nominatim_structured_query_failed", error=str(exc)
                )
                last_exception = exc
            else:
                any_clean_response = True
                if structured_results:
                    candidate = structured_results[0]
                    candidate_score = score(candidate)
                    if candidate_score > best_score:
                        best, best_score = candidate, candidate_score

        if best is None:
            if not any_clean_response and last_exception is not None:
                raise last_exception
            raise GeocodingNoResultError(f"No location found for: {address}")
        return self._result(best)

    async def reverse_geocode(self, lat: float, lng: float) -> GeocodeResult:
        response = await self.client.get(
            settings.NOMINATIM_BASE_URL.rstrip("/") + "/reverse",
            params={
                "lat": lat,
                "lon": lng,
                "format": "jsonv2",
                "addressdetails": 1,
                "accept-language": settings.GEOCODING_LANGUAGE,
            },
        )
        response.raise_for_status()
        return self._result(response.json())

    async def autocomplete(
        self, query: str, session_token: str | None = None
    ) -> list[AddressSuggestion]:
        results = await self._search(query, limit=5)
        suggestions = []
        for item in results:
            display = self._format_display(item.get("display_name", "")) or query
            segments = display.split(", ")
            suggestions.append(
                AddressSuggestion(
                    description=display,
                    place_id=str(item.get("place_id", "")),
                    main_text=segments[0],
                    secondary_text=", ".join(segments[1:3]),
                )
            )
        return suggestions

    async def health_check(self) -> bool:
        try:
            await self._search("Chennai, Tamil Nadu, India")
            return True
        except Exception:
            return False
