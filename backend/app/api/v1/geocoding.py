"""Geocoding proxy.

Every one of these endpoints exists so that the Google API key stays on the
server. The browser calls us; we call Google. This is what makes "do not expose
routing API keys in frontend code" (brief §20) enforceable rather than a
convention someone can forget.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import rate_limit, require
from app.core.config import settings
from app.core.enums import Permission
from app.core.errors import GeocodingError
from app.core.metrics import geocoding_results_total
from app.providers.registry import get_geocoding_provider
from app.models.user import User
from app.schemas.misc import (
    AutocompleteItem,
    GeocodeRequest,
    GeocodeResponse,
    ReverseGeocodeRequest,
)

router = APIRouter(prefix="/geocoding", tags=["geocoding"])


@router.get(
    "/autocomplete",
    response_model=list[AutocompleteItem],
    dependencies=[
        Depends(
            rate_limit(settings.RATE_LIMIT_AUTOCOMPLETE_PER_MINUTE, "autocomplete")
        )
    ],
)
async def autocomplete(
    q: str = Query(..., min_length=3, max_length=300),
    session_token: str | None = Query(
        None,
        description=(
            "Opaque per-search token from the client. Groups keystrokes into a "
            "single billable Places session."
        ),
    ),
    user: User = Depends(require(Permission.VIEW)),
) -> list[AutocompleteItem]:
    try:
        suggestions = await get_geocoding_provider().autocomplete(q, session_token)
    except GeocodingError:
        # An autocomplete failure should degrade to "no suggestions", not break
        # the form. The user can still type the address and submit it.
        return []

    return [
        AutocompleteItem(
            description=s.description,
            place_id=s.place_id,
            main_text=s.main_text,
            secondary_text=s.secondary_text,
        )
        for s in suggestions
    ]


@router.post("/geocode", response_model=GeocodeResponse)
async def geocode(
    payload: GeocodeRequest,
    user: User = Depends(require(Permission.VIEW)),
) -> GeocodeResponse:
    result = await get_geocoding_provider().geocode(payload.address)
    geocoding_results_total.labels(
        outcome="partial" if result.needs_verification else "success"
    ).inc()
    return GeocodeResponse(
        latitude=result.latitude,
        longitude=result.longitude,
        formatted_address=result.formatted_address,
        confidence=result.confidence,
        partial_match=result.partial_match,
        needs_verification=result.needs_verification,
        provider=result.provider,
    )


@router.post("/reverse", response_model=GeocodeResponse)
async def reverse_geocode(
    payload: ReverseGeocodeRequest,
    user: User = Depends(require(Permission.VIEW)),
) -> GeocodeResponse:
    """Address for a point. Used to label the marker after an operator drags it."""
    result = await get_geocoding_provider().reverse_geocode(
        payload.latitude, payload.longitude
    )
    return GeocodeResponse(
        latitude=result.latitude,
        longitude=result.longitude,
        formatted_address=result.formatted_address,
        confidence=result.confidence,
        partial_match=result.partial_match,
        needs_verification=False,
        provider=result.provider,
    )
