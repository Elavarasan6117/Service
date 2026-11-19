"""Importer, provider adapter and cache behaviour."""

from __future__ import annotations


import pytest
from sqlalchemy import func, select

from app.core.enums import ImportBatchStatus, ImportRowStatus, LocationStatus
from app.core.errors import RoutingProviderError, ValidationError
from app.models.imports import ImportRow
from app.models.service_location import ServiceLocation
from app.providers.base import Coordinate
from app.providers.fake import FakeGeocodingProvider
from app.services.import_service import ImportService

CSV_HEADER = "Customer ID,Customer Name,Address,Area,Latitude,Longitude,Route,Status\n"


def csv_bytes(rows: list[str]) -> bytes:
    return (CSV_HEADER + "\n".join(rows) + "\n").encode("utf-8")


# --- Import validation --------------------------------------------------


async def test_import_validates_without_writing_service_locations(session, admin_user):
    content = csv_bytes(
        [
            "C101,Sri Foods,12 Main Road,Anna Nagar,13.0850,80.2101,RT-N-01,ACTIVE",
            "C102,New Traders,44 Second Ave,Anna Nagar,13.0870,80.2110,RT-N-01,ACTIVE",
        ]
    )
    service = ImportService(session, FakeGeocodingProvider())
    batch = await service.validate_file(content, "locations.csv", actor=admin_user)
    await session.commit()

    assert batch.status is ImportBatchStatus.VALIDATED
    assert batch.total_records == 2
    assert batch.valid_records == 2

    count = await session.scalar(select(func.count()).select_from(ServiceLocation))
    assert count == 0, (
        "Validation must not create service locations. Commit is a separate, "
        "deliberate step after a human has read the report."
    )


async def test_import_flags_every_invalid_condition(session, admin_user):
    content = csv_bytes(
        [
            "C201,Good Store,1 Main Road,Anna Nagar,13.0850,80.2101,RT-N-01,ACTIVE",
            ",Missing Code,2 Main Road,Anna Nagar,13.0851,80.2102,RT-N-01,ACTIVE",
            "C203,Bad Latitude,3 Main Road,Anna Nagar,999,80.2103,RT-N-01,ACTIVE",
            "C201,Duplicate Code,4 Main Road,Anna Nagar,13.0853,80.2104,RT-N-01,ACTIVE",
            "C205,Half Coordinate,5 Main Road,Anna Nagar,13.0855,,RT-N-01,ACTIVE",
            "C206,Far Away,6 Main Road,Somewhere,28.6139,77.2090,RT-N-01,ACTIVE",
        ]
    )
    service = ImportService(session, FakeGeocodingProvider())
    batch = await service.validate_file(content, "messy.csv", actor=admin_user)
    await session.commit()

    assert batch.total_records == 6
    # C201 and the out-of-Chennai C206 are both VALID; C206 carries a warning
    # rather than a rejection (see the assertion on row 7 below).
    assert batch.valid_records == 2
    assert batch.duplicate_records == 1

    rows = (
        (await session.execute(select(ImportRow).order_by(ImportRow.row_number)))
        .scalars()
        .all()
    )
    by_row = {r.row_number: r for r in rows}

    assert by_row[2].status is ImportRowStatus.VALID
    assert by_row[3].status is ImportRowStatus.INVALID       # missing code
    assert by_row[4].status is ImportRowStatus.INVALID       # latitude 999
    assert by_row[5].status is ImportRowStatus.DUPLICATE     # repeated C201
    assert by_row[6].status is ImportRowStatus.INVALID       # only one coordinate

    # Delhi coordinates are outside Chennai: a warning, not a rejection,
    # because expansion beyond Chennai is a stated future requirement.
    assert by_row[7].status is ImportRowStatus.VALID
    assert any(
        w["issue"] == "outside_expected_chennai_area" for w in (by_row[7].warnings or [])
    )

    report = batch.report
    assert report["invalid_records"] == 3
    assert report["duplicate_records"] == 1
    assert report["out_of_bounds"] == 1
    assert report["sample_errors"], "Operators need concrete examples, not just counts."


async def test_import_geocodes_rows_without_coordinates(session, admin_user):
    content = csv_bytes(
        [
            "C301,Needs Geocoding,10 Anna Nagar Main Road,Anna Nagar,,,RT-N-01,ACTIVE",
        ]
    )
    geocoding = FakeGeocodingProvider(default=(13.0850, 80.2101))
    batch = await ImportService(session, geocoding).validate_file(
        content, "no_coords.csv", actor=admin_user
    )
    await session.commit()

    assert batch.geocoded_records == 1
    assert batch.valid_records == 1
    row = (await session.execute(select(ImportRow))).scalars().first()
    assert row.coordinate_source == "GEOCODED"
    assert float(row.resolved_latitude) == pytest.approx(13.0850)


async def test_import_records_geocoding_failure_without_guessing(session, admin_user):
    content = csv_bytes(
        [
            "C401,Unfindable,Nowhere At All,Unknown,,,RT-N-01,ACTIVE",
        ]
    )
    geocoding = FakeGeocodingProvider(default=None)
    batch = await ImportService(session, geocoding).validate_file(
        content, "ungeocodable.csv", actor=admin_user
    )
    await session.commit()

    assert batch.valid_records == 0
    assert batch.geocode_failed_records == 1
    row = (await session.execute(select(ImportRow))).scalars().first()
    assert row.status is ImportRowStatus.GEOCODE_FAILED
    assert row.resolved_latitude is None


async def test_import_commit_creates_only_valid_rows(session, admin_user):
    content = csv_bytes(
        [
            "C501,Good One,1 Main Road,Anna Nagar,13.0850,80.2101,RT-N-01,ACTIVE",
            ",Bad One,2 Main Road,Anna Nagar,13.0851,80.2102,RT-N-01,ACTIVE",
            "C503,Good Two,3 Main Road,Anna Nagar,13.0852,80.2103,RT-N-02,INACTIVE",
        ]
    )
    service = ImportService(session, FakeGeocodingProvider())
    batch = await service.validate_file(content, "mixed.csv", actor=admin_user)
    await session.commit()

    committed, skipped = await service.commit_batch(batch.id, actor=admin_user)
    await session.commit()

    assert committed == 2
    assert skipped == 1

    locations = (await session.execute(select(ServiceLocation))).scalars().all()
    codes = {location.service_code for location in locations}
    assert codes == {"C501", "C503"}

    inactive = next(location for location in locations if location.service_code == "C503")
    assert inactive.status is LocationStatus.INACTIVE
    # Routes referenced in the file are created rather than blocking the import.
    assert inactive.route_id is not None


async def test_import_cannot_be_committed_twice(session, admin_user):
    content = csv_bytes(
        ["C601,Once Only,1 Main Road,Anna Nagar,13.0850,80.2101,RT-N-01,ACTIVE"]
    )
    service = ImportService(session, FakeGeocodingProvider())
    batch = await service.validate_file(content, "once.csv", actor=admin_user)
    await session.commit()

    await service.commit_batch(batch.id, actor=admin_user)
    await session.commit()

    with pytest.raises(ValidationError, match="already been committed"):
        await service.commit_batch(batch.id, actor=admin_user)


async def test_import_accepts_alternative_column_headings(session, admin_user):
    """Real CRM exports rarely match a spec exactly."""
    content = (
        "code,name,full_address,locality,lat,lon,route_code,state\n"
        "C701,Alias Store,9 Main Road,Anna Nagar,13.0850,80.2101,RT-N-01,ACTIVE\n"
    ).encode("utf-8")
    batch = await ImportService(session, FakeGeocodingProvider()).validate_file(
        content, "aliases.csv", actor=admin_user
    )
    await session.commit()
    assert batch.valid_records == 1


async def test_import_rejects_file_with_no_recognisable_columns(session, admin_user):
    content = b"alpha,beta,gamma\n1,2,3\n"
    with pytest.raises(ValidationError, match="missing required column"):
        await ImportService(session, FakeGeocodingProvider()).validate_file(
            content, "wrong.csv", actor=admin_user
        )


# --- Provider adapters --------------------------------------------------


def test_osrm_uses_lon_lat_order():
    """OSRM takes lon,lat while Google takes lat,lng.

    Getting this backwards would place every Chennai point in the Indian Ocean
    and silently produce nonsense distances, so it is asserted explicitly.
    """
    from app.providers.osrm import OsrmProvider

    coords = OsrmProvider._coords([Coordinate(13.0827, 80.2707)])
    assert coords == "80.2707,13.0827"


def test_route_preview_can_use_osrm_without_changing_google_serviceability_provider(monkeypatch):
    from app.core.config import settings
    from app.providers.osrm import OsrmProvider
    from app.providers.registry import get_route_preview_provider
    import app.providers.registry as registry

    monkeypatch.setattr(settings, "ROUTE_PREVIEW_PROVIDER", "osrm")
    monkeypatch.setattr(settings, "ROUTING_PROVIDER", "google_maps")
    monkeypatch.setattr(registry, "_route_preview", None)
    monkeypatch.setattr(registry, "_routing", None)

    preview_provider = get_route_preview_provider()
    assert isinstance(preview_provider.inner, OsrmProvider)
    assert settings.ROUTING_PROVIDER == "google_maps"
    assert registry._routing is None

    registry._route_preview = None


async def test_google_adapter_parses_distance_matrix(monkeypatch):
    from app.providers.google import GoogleMapsProvider

    provider = GoogleMapsProvider(api_key="test-key")

    async def fake_get(path, params):
        assert path == "/distancematrix/json"
        return {
            "status": "OK",
            "rows": [
                {
                    "elements": [
                        {
                            "status": "OK",
                            "distance": {"value": 1450.6},
                            "duration": {"value": 420},
                        },
                        {"status": "ZERO_RESULTS"},
                    ]
                }
            ],
        }

    monkeypatch.setattr(provider, "_get", fake_get)
    legs = await provider.get_driving_distance(
        Coordinate(13.08, 80.27),
        [Coordinate(13.09, 80.28), Coordinate(13.10, 80.29)],
    )

    assert len(legs) == 2
    assert legs[0].distance_meters == 1451  # rounded once, at the boundary
    assert isinstance(legs[0].distance_meters, int)
    assert legs[0].distance_type.value == "ROAD_DISTANCE"
    assert legs[1] is None, "ZERO_RESULTS for one pair is not a provider failure."


async def test_osrm_batches_origin_table_caches_and_returns_encoded_route():
    import asyncio
    import httpx

    from app.providers.osrm import OsrmProvider

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "/table/" in request.url.path:
            return httpx.Response(200, json={
                "code": "Ok",
                "distances": [[3400], [None]],
                "durations": [[480], [None]],
            })
        return httpx.Response(200, json={"code": "Ok", "routes": [{
            "distance": 3400,
            "duration": 480,
            "geometry": "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
        }]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OsrmProvider(base_url="http://osrm.internal", client=client)
    origins = [Coordinate(13.01, 80.22), Coordinate(13.02, 80.23)]
    destination = Coordinate(13.08, 80.27)

    first, duplicate = await asyncio.gather(
        provider.get_driving_distances(origins, destination),
        provider.get_driving_distances(origins, destination),
    )
    assert [leg.distance_meters if leg else None for leg in first] == [3400, None]
    assert [leg.duration_seconds if leg else None for leg in first] == [480, None]
    assert [leg.distance_meters if leg else None for leg in duplicate] == [3400, None]
    assert len(requests) == 1, "Concurrent duplicate matrix calls should share one OSRM request."
    assert requests[0].url.path.endswith(
        "/table/v1/driving/80.22,13.01;80.23,13.02;80.27,13.08"
    )
    assert requests[0].url.params["sources"] == "0;1"
    assert requests[0].url.params["destinations"] == "2"
    await provider.get_driving_distances(origins, destination)
    assert len(requests) == 1, "Recent matching table responses should be cached."

    route = await provider.get_driving_route(origins[0], destination)
    assert route.distance_meters == 3400
    assert route.duration_seconds == 480
    assert route.geometry == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert route.geometry_format == "encoded_polyline_5"
    assert len(requests) == 2
    assert requests[1].url.params["overview"] == "full"
    assert requests[1].url.params["geometries"] == "polyline"
    await client.aclose()


async def test_osrm_missing_or_public_production_endpoint_fails_with_configuration_error(monkeypatch):
    from app.core.config import settings
    from app.providers.osrm import OsrmProvider

    monkeypatch.setattr(settings, "ENVIRONMENT", "qa")
    missing = OsrmProvider(base_url="")
    with pytest.raises(RoutingProviderError, match="OSRM_BASE_URL") as missing_error:
        await missing.get_driving_distances([Coordinate(13.01, 80.22)], Coordinate(13.08, 80.27))
    assert missing_error.value.code == "PROVIDER_NOT_CONFIGURED"

    public_demo = OsrmProvider(base_url="https://router.project-osrm.org")
    with pytest.raises(RoutingProviderError, match="public OSRM demo") as public_error:
        await public_demo.get_driving_distances([Coordinate(13.01, 80.22)], Coordinate(13.08, 80.27))
    assert public_error.value.code == "PROVIDER_NOT_CONFIGURED"


async def test_osrm_connect_error_reports_private_endpoint_guidance_without_url_leak():
    import httpx

    from app.providers.osrm import OsrmProvider

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed for http://user:secret@osrm.private", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OsrmProvider(base_url="http://osrm.private", client=client)
    with pytest.raises(RoutingProviderError, match="Cannot connect to OSRM") as error:
        await provider.get_driving_distances([Coordinate(13.01, 80.22)], Coordinate(13.08, 80.27))
    assert error.value.code == "PROVIDER_CONNECT_ERROR"
    assert "secret" not in str(error.value)
    await client.aclose()


async def test_google_adapter_raises_on_misaligned_response(monkeypatch):
    """A response we cannot align to our request must fail, not be guessed at."""
    from app.providers.google import GoogleMapsProvider

    provider = GoogleMapsProvider(api_key="test-key")

    async def fake_get(path, params):
        return {"status": "OK", "rows": [{"elements": [{"status": "OK", "distance": {"value": 100}}]}]}

    monkeypatch.setattr(provider, "_get", fake_get)
    with pytest.raises(RoutingProviderError, match="misaligned|mismatched"):
        await provider.get_driving_distance(
            Coordinate(13.08, 80.27),
            [Coordinate(13.09, 80.28), Coordinate(13.10, 80.29)],
        )


async def test_google_quota_exhaustion_is_a_rate_limit_error(monkeypatch):
    """Quota exhaustion must surface as a retryable provider error.

    It must never reach the engine as anything that could look like a distance.
    """
    import httpx

    from app.core.errors import RoutingRateLimitError
    from app.providers.google import GoogleMapsProvider

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "OVER_QUERY_LIMIT"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GoogleMapsProvider(api_key="test-key", client=client)

    with pytest.raises(RoutingRateLimitError):
        await provider.get_driving_distance(
            Coordinate(13.08, 80.27), [Coordinate(13.09, 80.28)]
        )
    await client.aclose()


async def test_google_routes_api_returns_matrix_distances_and_driving_geometry():
    import httpx

    from app.providers.google import GoogleMapsProvider

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("computeRouteMatrix"):
            return httpx.Response(200, json=[{
                "originIndex": 0,
                "destinationIndex": 0,
                "condition": "ROUTE_EXISTS",
                "status": {},
                "distanceMeters": 1850,
                "duration": "420s",
            }])
        return httpx.Response(200, json={"routes": [{
            "distanceMeters": 1850,
            "duration": "420s",
            "polyline": {"encodedPolyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"},
        }]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GoogleMapsProvider(api_key="test-secret-key", client=client)
    origin, destination = Coordinate(13.08, 80.27), Coordinate(13.09, 80.28)

    legs = await provider.get_driving_distances([origin], destination)
    routes = await provider.get_driving_routes(origin, destination)

    assert legs[0].distance_meters == 1850
    assert legs[0].duration_seconds == 420
    assert routes[0].distance_meters == 1850
    assert routes[0].duration_seconds == 420
    assert routes[0].geometry == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert requests[0].url == "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
    assert requests[0].headers["X-Goog-FieldMask"] == (
        "originIndex,destinationIndex,duration,distanceMeters,status,condition"
    )
    assert requests[1].url == "https://routes.googleapis.com/directions/v2:computeRoutes"
    assert requests[1].headers["X-Goog-FieldMask"] == (
        "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline"
    )
    assert requests[0].headers["X-Goog-Api-Key"] == "test-secret-key"
    assert b"key=" not in requests[0].url.query
    await client.aclose()


async def test_google_routes_api_400_is_non_retryable(monkeypatch):
    import httpx

    from app.core.errors import RoutingProviderError
    from app.providers.google import GoogleMapsProvider
    from app.providers.resilience import ResilientRoutingProvider

    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(400, json={"error": {
            "code": 400,
            "status": "INVALID_ARGUMENT",
            "message": "Origin and destination must be set.",
        }})

    monkeypatch.setattr("app.core.config.settings.PROVIDER_MAX_RETRIES", 3)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ResilientRoutingProvider(GoogleMapsProvider(api_key="test-key", client=client))
    with pytest.raises(RoutingProviderError) as error:
        await provider.get_driving_routes(Coordinate(13.08, 80.27), Coordinate(13.09, 80.28))
    assert error.value.code == "PROVIDER_INVALID_REQUEST"
    assert calls["count"] == 1
    await client.aclose()


async def test_google_routes_api_key_error_and_quota_error_are_classified():
    import httpx

    from app.core.errors import RoutingProviderError, RoutingRateLimitError
    from app.providers.google import GoogleMapsProvider

    responses = [
        httpx.Response(403, json={"error": {
            "code": 403, "status": "PERMISSION_DENIED", "message": "API key not valid."
        }}),
        httpx.Response(429, json={"error": {
            "code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded."
        }}),
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GoogleMapsProvider(api_key="test-key", client=client)
    with pytest.raises(RoutingProviderError) as key_error:
        await provider.get_driving_routes(Coordinate(13.08, 80.27), Coordinate(13.09, 80.28))
    assert key_error.value.code == "PROVIDER_REQUEST_DENIED"
    with pytest.raises(RoutingRateLimitError) as quota_error:
        await provider.get_driving_routes(Coordinate(13.08, 80.27), Coordinate(13.09, 80.28))
    assert quota_error.value.code == "PROVIDER_QUOTA_EXCEEDED"
    await client.aclose()


async def test_google_routes_provider_reports_missing_api_key():
    from app.core.errors import RoutingProviderError
    from app.providers.google import GoogleMapsProvider

    provider = GoogleMapsProvider(api_key="")
    with pytest.raises(RoutingProviderError) as error:
        await provider.get_driving_routes(Coordinate(13.08, 80.27), Coordinate(13.09, 80.28))
    assert error.value.code == "PROVIDER_NOT_CONFIGURED"


async def test_routes_api_timeout_is_retried(monkeypatch):
    import httpx

    from app.core.errors import RoutingTimeoutError
    from app.providers.google import GoogleMapsProvider
    from app.providers.resilience import ResilientRoutingProvider

    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr("app.core.config.settings.PROVIDER_MAX_RETRIES", 2)
    monkeypatch.setattr("app.core.config.settings.PROVIDER_BACKOFF_BASE_SECONDS", 0)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = ResilientRoutingProvider(GoogleMapsProvider(api_key="test-key", client=client))
    with pytest.raises(RoutingTimeoutError):
        await provider.get_driving_routes(Coordinate(13.08, 80.27), Coordinate(13.09, 80.28))
    assert calls["count"] == 2
    await client.aclose()


async def test_circuit_breaker_recovers_with_one_half_open_probe(monkeypatch):
    from app.providers.resilience import CircuitBreaker

    breaker = CircuitBreaker("test-provider", fail_threshold=1, reset_seconds=0)
    await breaker.before_call()
    await breaker.on_failure()
    assert breaker.state == breaker.HALF_OPEN

    await breaker.before_call()
    with pytest.raises(Exception, match="checking recovery"):
        await breaker.before_call()
    await breaker.on_success()
    assert breaker.state == breaker.CLOSED

    await breaker.before_call()
    await breaker.on_failure()
    await breaker.before_call()
    await breaker.on_non_retryable_failure()
    assert breaker.state == breaker.CLOSED


async def test_resilient_wrapper_retries_then_raises(monkeypatch):
    from app.providers.resilience import ResilientRoutingProvider

    calls = {"n": 0}

    class FlakyProvider:
        name = "flaky"

        async def get_driving_distance(self, origin, destinations):
            calls["n"] += 1
            raise RoutingProviderError("upstream down")

        async def get_driving_route(self, origin, destination):
            raise RoutingProviderError("upstream down")

        async def health_check(self):
            return False

        async def close(self):
            return None

    monkeypatch.setattr("app.core.config.settings.PROVIDER_MAX_RETRIES", 3)
    monkeypatch.setattr("app.core.config.settings.PROVIDER_BACKOFF_BASE_SECONDS", 0.001)

    provider = ResilientRoutingProvider(FlakyProvider())
    with pytest.raises(RoutingProviderError):
        await provider.get_driving_distance(
            Coordinate(13.08, 80.27), [Coordinate(13.09, 80.28)]
        )
    assert calls["n"] == 3, "Should have retried up to the configured limit."


async def test_resilient_wrapper_falls_back_to_secondary_provider(monkeypatch):
    from app.providers.fake import FakeRoutingProvider
    from app.providers.resilience import ResilientRoutingProvider

    class DeadProvider:
        name = "dead"

        async def get_driving_distance(self, origin, destinations):
            raise RoutingProviderError("primary is down")

        async def get_driving_route(self, origin, destination):
            raise RoutingProviderError("primary is down")

        async def health_check(self):
            return False

        async def close(self):
            return None

    monkeypatch.setattr("app.core.config.settings.PROVIDER_MAX_RETRIES", 1)
    monkeypatch.setattr("app.core.config.settings.PROVIDER_BACKOFF_BASE_SECONDS", 0.001)

    provider = ResilientRoutingProvider(DeadProvider(), fallback=FakeRoutingProvider())
    legs = await provider.get_driving_distance(
        Coordinate(13.08, 80.27), [Coordinate(13.09, 80.28)]
    )
    assert legs[0] is not None
    assert legs[0].provider == "fake"


async def test_google_geocoder_uses_photon_fallback_with_verification(monkeypatch):
    from app.providers.registry import _build_geocoding

    monkeypatch.setattr("app.core.config.settings.GEOCODING_PROVIDER", "google_maps")
    provider = _build_geocoding()

    assert provider.name == "google_maps"
    assert provider.inner.name == "google_maps"
    assert provider.fallback.name == "photon"
    await provider.close()


async def test_geocoding_provider_fallback_places_pin_as_approximate(monkeypatch):
    from app.core.errors import RoutingProviderError
    from app.providers.fake import FakeGeocodingProvider
    from app.providers.resilience import ResilientGeocodingProvider

    class UnavailableGeocoder:
        name = "google_maps"

        async def geocode(self, address):
            raise RoutingProviderError("Google API key is not configured")

        async def geocode_place(self, place_id, address, session_token=None):
            raise RoutingProviderError("Google API key is not configured")

        async def reverse_geocode(self, lat, lng):
            raise RoutingProviderError("Google API key is not configured")

        async def autocomplete(self, query, session_token=None):
            raise RoutingProviderError("Google API key is not configured")

        async def health_check(self):
            return False

        async def close(self):
            return None

    monkeypatch.setattr("app.core.config.settings.PROVIDER_MAX_RETRIES", 1)
    monkeypatch.setattr("app.core.config.settings.PROVIDER_CIRCUIT_FAIL_THRESHOLD", 10)
    fallback = FakeGeocodingProvider()
    provider = ResilientGeocodingProvider(UnavailableGeocoder(), fallback=fallback)

    result = await provider.geocode("Guest House Road, Chennai")
    suggestions = await provider.autocomplete("Guest House Road")

    assert result.provider == "fake"
    assert result.needs_verification
    assert result.confidence == "low"
    assert suggestions[0].description.startswith("Guest House Road")


# --- Coordinate value object -------------------------------------------


def test_coordinate_rejects_impossible_values():
    with pytest.raises(ValueError):
        Coordinate(95.0, 80.0)
    with pytest.raises(ValueError):
        Coordinate(13.0, 200.0)


def test_coordinate_rounding_matches_cache_key_precision():
    c = Coordinate(13.08271234, 80.27079876)
    assert c.rounded(6).latitude == 13.082712
    assert str(c.rounded(6)) == "13.082712,80.270799"
