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
