"""PostGIS-backed spatial tests.

Skipped unless TEST_POSTGRES_URL points at a PostgreSQL database with PostGIS:

    TEST_POSTGRES_URL=postgresql+asyncpg://user:pass@localhost:5432/svc_test \\
        python -m pytest -m postgis

These matter because the rest of the suite runs on SQLite and therefore
exercises the pure-Python haversine fallback rather than the real
``ST_DWithin`` + KNN path that production uses. Everything here is about
verifying the spatial layer itself: the geometry trigger, the coordinate
order, index usage, and the correctness of the candidate query.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.enums import LocationStatus
from app.db.base import Base
from app.models.service_location import ServiceLocation
from app.services.spatial_search import SpatialSearchService

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.postgis,
    pytest.mark.skipif(
        not POSTGRES_URL, reason="TEST_POSTGRES_URL is not set; PostGIS tests skipped"
    ),
]

CHENNAI_CENTRAL = (13.0827, 80.2707)


@pytest_asyncio.fixture
async def pg_session():
    engine = create_async_engine(POSTGRES_URL, poolclass=None)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        # The application relies on this trigger to keep `location` in step
        # with latitude/longitude; create_all does not install it.
        await conn.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION sync_location_from_latlng() RETURNS trigger AS $$
                BEGIN
                    IF NEW.latitude IS NULL OR NEW.longitude IS NULL THEN
                        NEW.location := NULL;
                    ELSE
                        NEW.location := ST_SetSRID(
                            ST_MakePoint(NEW.longitude::float8, NEW.latitude::float8), 4326
                        )::geography;
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TRIGGER trg_service_locations_sync_location
                BEFORE INSERT OR UPDATE OF latitude, longitude ON service_locations
                FOR EACH ROW EXECUTE FUNCTION sync_location_from_latlng();
                """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_service_locations_location_gist "
                "ON service_locations USING GIST (location)"
            )
        )

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _add(session: AsyncSession, code: str, lat: float, lng: float) -> None:
    session.add(
        ServiceLocation(
            service_code=code,
            location_name=code,
            latitude=Decimal(str(lat)),
            longitude=Decimal(str(lng)),
            status=LocationStatus.ACTIVE,
        )
    )
    await session.commit()


async def test_trigger_populates_geography_in_lng_lat_order(pg_session):
    """ST_MakePoint takes (x, y) = (longitude, latitude).

    If this were reversed, every Chennai location would land in the Indian
    Ocean off Somalia and every distance would be nonsense. Asserting the
    round-trip catches it immediately.
    """
    await _add(pg_session, "SRV-TRIG", 13.0827, 80.2707)

    row = (
        await pg_session.execute(
            text(
                "SELECT ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng "
                "FROM service_locations WHERE service_code='SRV-TRIG'"
            )
        )
    ).one()

    assert row.lat == pytest.approx(13.0827, abs=1e-6)
    assert row.lng == pytest.approx(80.2707, abs=1e-6)


async def test_trigger_updates_geometry_when_coordinates_change(pg_session):
    await _add(pg_session, "SRV-MOVE", 13.0827, 80.2707)

    await pg_session.execute(
        text(
            "UPDATE service_locations SET latitude=13.0900, longitude=80.2800 "
            "WHERE service_code='SRV-MOVE'"
        )
    )
    await pg_session.commit()

    row = (
        await pg_session.execute(
            text(
                "SELECT ST_Y(location::geometry) AS lat FROM service_locations "
                "WHERE service_code='SRV-MOVE'"
            )
        )
    ).one()
    assert row.lat == pytest.approx(13.0900, abs=1e-6)


async def test_trigger_nulls_geometry_when_coordinates_cleared(pg_session):
    """A location without coordinates must drop out of candidate searches."""
    await _add(pg_session, "SRV-CLEAR", 13.0827, 80.2707)
    await pg_session.execute(
        text(
            "UPDATE service_locations SET latitude=NULL, longitude=NULL "
            "WHERE service_code='SRV-CLEAR'"
        )
    )
    await pg_session.commit()

    row = (
        await pg_session.execute(
            text(
                "SELECT location IS NULL AS is_null FROM service_locations "
                "WHERE service_code='SRV-CLEAR'"
            )
        )
    ).one()
    assert row.is_null is True


async def test_st_distance_on_geography_returns_metres(pg_session):
    """geography, not geometry: distances come back in metres with no projection."""
    await _add(pg_session, "SRV-DIST", 13.0827, 80.2707)

    # ~1 km due north.
    row = (
        await pg_session.execute(
            text(
                "SELECT ST_Distance(location, ST_SetSRID(ST_MakePoint(80.2707, 13.0917), 4326)::geography) AS m "
                "FROM service_locations WHERE service_code='SRV-DIST'"
            )
        )
    ).one()
    assert 950 < row.m < 1050, f"expected ~1000 m, got {row.m}"


async def test_candidate_search_orders_by_distance_and_respects_radius(pg_session):
    lat, lng = CHENNAI_CENTRAL
    await _add(pg_session, "SRV-500M", lat + 0.0045, lng)     # ~500 m
    await _add(pg_session, "SRV-2KM", lat + 0.018, lng)       # ~2 km
    await _add(pg_session, "SRV-5KM", lat + 0.045, lng)       # ~5 km
    await _add(pg_session, "SRV-30KM", lat + 0.27, lng)       # ~30 km

    spatial = SpatialSearchService(pg_session)
    candidates = await spatial.find_candidates(lat, lng, radius_meters=8000, limit=10)

    codes = [c.service_code for c in candidates]
    assert codes == ["SRV-500M", "SRV-2KM", "SRV-5KM"], (
        "Candidates must be ordered nearest-first and exclude anything beyond "
        "the radius."
    )
    assert candidates[0].straight_line_meters == pytest.approx(500, abs=60)
    # The value is labelled honestly so it cannot be mistaken for a decision.
    assert candidates[0].distance_type.value == "STRAIGHT_LINE"


async def test_candidate_search_excludes_inactive_locations(pg_session):
    lat, lng = CHENNAI_CENTRAL
    await _add(pg_session, "SRV-ACTIVE", lat + 0.004, lng)

    session_obj = ServiceLocation(
        service_code="SRV-INACTIVE",
        location_name="Inactive",
        latitude=Decimal(str(lat + 0.003)),
        longitude=Decimal(str(lng)),
        status=LocationStatus.INACTIVE,
    )
    pg_session.add(session_obj)
    await pg_session.commit()

    candidates = await SpatialSearchService(pg_session).find_candidates(
        lat, lng, 8000, 10
    )
    assert [c.service_code for c in candidates] == ["SRV-ACTIVE"], (
        "An INACTIVE location must not define coverage, even though it is "
        "physically closer."
    )


async def test_candidate_search_respects_limit(pg_session):
    lat, lng = CHENNAI_CENTRAL
    for i in range(25):
        await _add(pg_session, f"SRV-{i:03d}", lat + 0.001 * (i + 1), lng)

    candidates = await SpatialSearchService(pg_session).find_candidates(
        lat, lng, 8000, 10
    )
    assert len(candidates) == 10, "CANDIDATE_MAX_COUNT caps the cost per check."
    assert candidates[0].service_code == "SRV-000", "Kept candidates are the closest."


async def test_candidate_query_uses_the_spatial_index(pg_session):
    """Guards the single most performance-critical property of the system.

    A sequential scan here is invisible at 200 locations and fatal at 20,000.
    """
    lat, lng = CHENNAI_CENTRAL
    for i in range(600):
        await _add(pg_session, f"SRV-IDX-{i:04d}", lat + 0.0005 * i, lng + 0.0003 * i)

    await pg_session.execute(text("ANALYZE service_locations"))

    plan_rows = (
        await pg_session.execute(
            text(
                """
                EXPLAIN (FORMAT TEXT)
                SELECT id FROM service_locations
                WHERE status = 'ACTIVE'
                  AND ST_DWithin(location, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, 8000)
                ORDER BY location <-> ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
                LIMIT 10
                """
            ).bindparams(lat=lat, lng=lng)
        )
    ).all()

    plan = "\n".join(row[0] for row in plan_rows)
    assert "Index Scan" in plan or "Bitmap Index Scan" in plan, (
        f"Candidate search is not using the GiST index. Plan was:\n{plan}"
    )
