"""Test fixtures.

The suite runs against SQLite with the fake providers, which makes it fast,
free and deterministic -- important because these tests assert exact metre
boundaries that a live road network would not reproduce.

What SQLite cannot exercise is PostGIS itself. The Stage-1 search therefore
falls back to the pure-Python haversine implementation, which is guarded so it
can never run against PostgreSQL. ``tests/test_spatial_postgis.py`` covers the
real spatial path and is skipped unless TEST_POSTGRES_URL is set.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from decimal import Decimal

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("ROUTING_PROVIDER", "fake")
os.environ.setdefault("GEOCODING_PROVIDER", "fake")
os.environ.setdefault("DATABASE_URL_OVERRIDE", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-tests-1234567890")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.enums import (  # noqa: E402
    ConfigValueType,
    EntityStatus,
    LocationStatus,
    UserRole,
)
from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models.config import AppConfig  # noqa: E402
from app.models.customer import Customer  # noqa: E402
from app.models.service_location import ServiceLocation  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.warehouse import Warehouse  # noqa: E402
from app.providers.fake import FakeGeocodingProvider, FakeRoutingProvider  # noqa: E402
from app.services.config_service import ConfigService  # noqa: E402
from app.services.serviceability import ServiceabilityEngine  # noqa: E402

# Chennai reference points used across tests.
ANNA_NAGAR = (13.0850, 80.2101)
T_NAGAR = (13.0418, 80.2341)


# Mirrors the seed block in alembic/versions/0001_initial_schema.py, including
# the min/max bounds -- tests must exercise the same guard rails production has.
# (key, value, type, requires_admin, min, max)
DEFAULT_CONFIG = [
    ("SERVICEABILITY_RADIUS_METERS", "2000", ConfigValueType.INT, True, "100", "100000"),
    ("CANDIDATE_RADIUS_FACTOR", "4.0", ConfigValueType.FLOAT, True, "1.0", "20.0"),
    ("CANDIDATE_MAX_COUNT", "10", ConfigValueType.INT, False, "1", "25"),
    ("ROUTE_CACHE_TTL_SECONDS", "86400", ConfigValueType.INT, False, "0", "604800"),
    (
        "SERVICEABILITY_EMPTY_TABLE_RESULT",
        "NO_SERVICE_LOCATION_CONFIGURED",
        ConfigValueType.STRING,
        True,
        None,
        None,
    ),
    ("AUTO_CHECK_ON_CUSTOMER_CREATE", "true", ConfigValueType.BOOL, False, None, None),
    ("AUTOMATIC_DECISION_ENABLED", "true", ConfigValueType.BOOL, True, None, None),
]


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """Fail Redis fast.

    Every Redis touchpoint in the application already degrades gracefully; this
    just avoids a 2-second connect timeout on each of them. That the suite
    passes with Redis hard-down is itself a useful assertion.
    """

    def _boom():
        raise ConnectionError("redis disabled in tests")

    monkeypatch.setattr("app.services.cache.get_redis", _boom)
    monkeypatch.setattr("app.services.events.get_redis", _boom)


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as sess:
        for key, value, value_type, requires_admin, lo, hi in DEFAULT_CONFIG:
            sess.add(
                AppConfig(
                    key=key,
                    value=value,
                    value_type=value_type,
                    description=key,
                    requires_admin=requires_admin,
                    min_value=lo,
                    max_value=hi,
                )
            )
        await sess.commit()
        yield sess


@pytest_asyncio.fixture
async def admin_user(session) -> User:
    user = User(
        username="admin",
        email="admin@example.com",
        full_name="Test Admin",
        password_hash=hash_password("test-password-1234"),
        role=UserRole.ADMIN,
    )
    session.add(user)
    await session.commit()
    return user


@pytest_asyncio.fixture
async def ops_user(session) -> User:
    user = User(
        username="ops",
        email="ops@example.com",
        full_name="Test Operations",
        password_hash=hash_password("test-password-1234"),
        role=UserRole.OPERATIONS,
    )
    session.add(user)
    await session.commit()
    return user


@pytest_asyncio.fixture
async def viewer_user(session) -> User:
    user = User(
        username="viewer",
        email="viewer@example.com",
        full_name="Test Viewer",
        password_hash=hash_password("test-password-1234"),
        role=UserRole.VIEW_ONLY,
    )
    session.add(user)
    await session.commit()
    return user


@pytest_asyncio.fixture
async def warehouse(session) -> Warehouse:
    wh = Warehouse(
        warehouse_code="WH-TEST-01",
        warehouse_name="Test Warehouse",
        latitude=Decimal("13.0100000"),
        longitude=Decimal("80.2200000"),
        status=EntityStatus.ACTIVE,
    )
    session.add(wh)
    await session.commit()
    return wh


class LocationFactory:
    """Creates service locations at controlled offsets from a base point.

    Offsets are in degrees so the straight-line ordering is predictable, while
    the *road* distance is supplied separately by the fake routing provider.
    That separation is what lets the tests prove the decision uses road
    distance rather than the geodesic one.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._n = 0

    async def create(
        self,
        latitude: float,
        longitude: float,
        *,
        code: str | None = None,
        name: str | None = None,
        status: LocationStatus = LocationStatus.ACTIVE,
    ) -> ServiceLocation:
        self._n += 1
        location = ServiceLocation(
            service_code=code or f"SRV-{self._n:03d}",
            location_name=name or f"Service Location {self._n}",
            address=f"{self._n} Test Road, Chennai",
            area="Test Area",
            city="Chennai",
            latitude=Decimal(str(latitude)),
            longitude=Decimal(str(longitude)),
            service_area="Test Area",
            status=status,
        )
        self.session.add(location)
        await self.session.commit()
        return location


@pytest_asyncio.fixture
async def locations(session) -> LocationFactory:
    return LocationFactory(session)


@pytest_asyncio.fixture
async def customer(session) -> Customer:
    cust = Customer(
        customer_code="CUST-TEST-001",
        customer_name="ABC Foods",
        address="1 Test Street, Anna Nagar, Chennai",
        area="Anna Nagar",
        city="Chennai",
        latitude=Decimal(str(ANNA_NAGAR[0])),
        longitude=Decimal(str(ANNA_NAGAR[1])),
    )
    session.add(cust)
    await session.commit()
    return cust


def make_engine(
    session: AsyncSession, routing: FakeRoutingProvider | None = None
) -> ServiceabilityEngine:
    return ServiceabilityEngine(
        session,
        routing_provider=routing or FakeRoutingProvider(),
        config=ConfigService(session),
    )


@pytest.fixture
def routing() -> FakeRoutingProvider:
    return FakeRoutingProvider()


@pytest.fixture
def geocoding() -> FakeGeocodingProvider:
    return FakeGeocodingProvider()


@pytest_asyncio.fixture
async def client(engine, session) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client wired to the same in-memory database as ``session``."""
    from app.db.session import get_db
    from app.main import app as fastapi_app
    from app.providers import registry

    registry.set_routing_provider(FakeRoutingProvider())
    registry.set_geocoding_provider(FakeGeocodingProvider())

    async def _override_db():
        yield session

    fastapi_app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()


def auth_headers(user: User) -> dict[str, str]:
    from app.core.security import create_access_token

    token = create_access_token(str(user.id), user.role.value, user.username)
    return {"Authorization": f"Bearer {token}"}
