"""Provider selection.

Changing routing or geocoding provider is an environment-variable change plus a
restart. No import of a concrete adapter exists anywhere outside this module.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.errors import ConfigurationError
from app.core.logging import get_logger
from app.providers.fake import FakeGeocodingProvider, FakeRoutingProvider
from app.providers.google import GoogleMapsProvider
from app.providers.osrm import OsrmProvider
from app.providers.nominatim import NominatimProvider
from app.providers.photon import PhotonProvider
from app.providers.resilience import (
    ResilientGeocodingProvider,
    ResilientRoutingProvider,
)

logger = get_logger(__name__)

_ROUTING_FACTORIES: dict[str, Any] = {
    "google_maps": GoogleMapsProvider,
    "osrm": OsrmProvider,
    "fake": FakeRoutingProvider,
}

_GEOCODING_FACTORIES: dict[str, Any] = {
    "google_maps": GoogleMapsProvider,
    "nominatim": NominatimProvider,
    "photon": PhotonProvider,
    # No guessed default in the local app: unknown addresses must be placed
    # manually or resolved by a real geocoder.
    "fake": lambda: FakeGeocodingProvider(default=None),
}

_routing: ResilientRoutingProvider | None = None
_geocoding: ResilientGeocodingProvider | None = None


def _build_routing() -> ResilientRoutingProvider:
    key = settings.ROUTING_PROVIDER.strip().lower()
    factory = _ROUTING_FACTORIES.get(key)
    if factory is None:
        raise ConfigurationError(
            f"Unknown ROUTING_PROVIDER '{key}'. "
            f"Available: {', '.join(sorted(_ROUTING_FACTORIES))}."
        )

    fallback = None
    fallback_key = settings.ROUTING_FALLBACK_PROVIDER.strip().lower()
    if fallback_key and fallback_key != key:
        fallback_factory = _ROUTING_FACTORIES.get(fallback_key)
        if fallback_factory is None:
            raise ConfigurationError(
                f"Unknown ROUTING_FALLBACK_PROVIDER '{fallback_key}'."
            )
        fallback = fallback_factory()

    logger.info("routing_provider_selected", provider=key, fallback=fallback_key or None)
    return ResilientRoutingProvider(factory(), fallback=fallback)


def _build_geocoding() -> ResilientGeocodingProvider:
    key = settings.GEOCODING_PROVIDER.strip().lower()
    factory = _GEOCODING_FACTORIES.get(key)
    if factory is None:
        raise ConfigurationError(
            f"Unknown GEOCODING_PROVIDER '{key}'. "
            f"Available: {', '.join(sorted(_GEOCODING_FACTORIES))}."
        )
    logger.info("geocoding_provider_selected", provider=key)
    return ResilientGeocodingProvider(factory())


def get_routing_provider() -> ResilientRoutingProvider:
    global _routing
    if _routing is None:
        _routing = _build_routing()
    return _routing


def get_geocoding_provider() -> ResilientGeocodingProvider:
    global _geocoding
    if _geocoding is None:
        _geocoding = _build_geocoding()
    return _geocoding


def set_routing_provider(provider: Any) -> None:
    """Test seam. Not used by application code."""
    global _routing
    _routing = provider


def set_geocoding_provider(provider: Any) -> None:
    """Test seam. Not used by application code."""
    global _geocoding
    _geocoding = provider


async def close_providers() -> None:
    global _routing, _geocoding
    if _routing is not None:
        await _routing.close()
        _routing = None
    if _geocoding is not None:
        await _geocoding.close()
        _geocoding = None
