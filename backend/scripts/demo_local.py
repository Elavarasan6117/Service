#!/usr/bin/env python3
"""Bootstrap a throwaway SQLite database for a no-dependency local demo.

This is a CONVENIENCE for trying the API without Docker, PostGIS, Redis or a
Google key. It is not a deployment path:

  * SQLite has no PostGIS, so Stage-1 candidate search falls back to a
    pure-Python haversine scan. Correct, but O(n) -- it does not scale.
  * The fake routing provider derives road distance from straight-line
    distance times a fixed detour ratio. Real Chennai distances will differ.

Use `docker compose up` for anything you intend to trust.

    python scripts/demo_local.py            # create ./demo.db
    ROUTING_PROVIDER=fake GEOCODING_PROVIDER=fake \\
      DATABASE_URL_OVERRIDE=sqlite+aiosqlite:///./demo.db \\
      uvicorn app.main:app --port 8000
"""

from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DB_PATH = Path(__file__).resolve().parent.parent / "demo.db"

# as_posix() matters on Windows. A native path is C:\Users\me\demo.db, and
# backslashes in a database URL are not path separators -- SQLAlchemy reads
# them as escapes and the connection fails with a confusing error. POSIX form
# (C:/Users/me/demo.db) works on every platform.
DB_URL = f"sqlite+aiosqlite:///{DB_PATH.as_posix()}"
os.environ.setdefault("DATABASE_URL_OVERRIDE", DB_URL)
os.environ.setdefault("ROUTING_PROVIDER", "fake")
os.environ.setdefault("GEOCODING_PROVIDER", "fake")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SECRET_KEY", "demo-only-secret-key-not-for-any-real-deployment-123456")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.enums import (  # noqa: E402
    ConfigValueType,
    EntityStatus,
    LocationStatus,
    UserRole,
)
from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models.config import AppConfig  # noqa: E402
from app.models.route import Route  # noqa: E402
from app.models.service_location import ServiceLocation  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.warehouse import Warehouse  # noqa: E402

# key, value, type, admin_only, min, max, description
# Mirrors the seed block in alembic/versions/0001_initial_schema.py, including
# the descriptions -- the admin screen shows them, so the demo should look like
# the real thing rather than echoing bare key names.
CONFIG = [
    ("SERVICEABILITY_RADIUS_METERS", "2000", ConfigValueType.INT, True, "100", "100000",
     "Maximum ACTUAL ROAD DISTANCE in metres for a new customer to be serviceable. "
     "A distance exactly equal to this value is AVAILABLE."),
    ("CANDIDATE_RADIUS_FACTOR", "4.0", ConfigValueType.FLOAT, True, "1.0", "20.0",
     "Stage-1 search radius as a multiple of the threshold. Must be >= 1.0; road "
     "distance is always >= straight-line distance, so a factor below 1 could "
     "discard a location that is actually within range."),
    ("CANDIDATE_MAX_COUNT", "10", ConfigValueType.INT, False, "1", "25",
     "Maximum candidates sent to the routing provider per check. Caps cost per "
     "check regardless of how many service locations exist."),
    ("ROUTE_CACHE_TTL_SECONDS", "86400", ConfigValueType.INT, False, "0", "604800",
     "How long a measured road distance between a pair of points stays cached. "
     "Moving a location invalidates its entries immediately regardless of this."),
    ("SERVICEABILITY_EMPTY_TABLE_RESULT", "NO_SERVICE_LOCATION_CONFIGURED",
     ConfigValueType.STRING, True, None, None,
     "Status returned when no ACTIVE service locations exist at all. Set to "
     "NOT_AVAILABLE if the business prefers that reading."),
    ("AUTO_CHECK_ON_CUSTOMER_CREATE", "true", ConfigValueType.BOOL, False, None, None,
     "Run the serviceability check automatically when a customer is created."),
    ("AUTOMATIC_DECISION_ENABLED", "true", ConfigValueType.BOOL, True, None, None,
     "Master switch for automated decisions. Set to false during an incident to "
     "route all new customers to manual review without taking the application down."),
]

# Real Chennai localities. Coordinates are approximate locality centroids --
# these stand in for your operational network until the real data is imported.
LOCATIONS = [
    ("SRV-ANN-001", "Sri Foods",          13.0851, 80.2103, "Anna Nagar",   "RT-NORTH-01"),
    ("SRV-ANN-002", "New Traders",        13.0879, 80.2145, "Anna Nagar",   "RT-NORTH-01"),
    ("SRV-ANN-003", "Royal Provisions",   13.0812, 80.2061, "Anna Nagar",   "RT-NORTH-01"),
    ("SRV-KIL-001", "Star Supermarket",   13.0780, 80.2411, "Kilpauk",      "RT-CENTRAL-01"),
    ("SRV-TNG-001", "Anand Bakery",       13.0418, 80.2341, "T. Nagar",     "RT-CENTRAL-03"),
    ("SRV-TNG-002", "Kumar Agencies",     13.0445, 80.2298, "T. Nagar",     "RT-CENTRAL-03"),
    ("SRV-ADY-001", "Chennai Sweets",     13.0067, 80.2570, "Adyar",        "RT-SOUTH-02"),
    ("SRV-VEL-001", "Velachery Mart",     12.9756, 80.2207, "Velachery",    "RT-SOUTH-03"),
    ("SRV-GUI-001", "Guindy Stores",      13.0067, 80.2206, "Guindy",       "RT-SOUTH-04"),
    ("SRV-POR-001", "Porur Distributors", 13.0359, 80.1567, "Porur",        "RT-WEST-01"),
]


async def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    engine = create_async_engine(os.environ["DATABASE_URL_OVERRIDE"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        for key, value, vtype, admin_only, lo, hi, description in CONFIG:
            session.add(
                AppConfig(
                    key=key, value=value, value_type=vtype, description=description,
                    requires_admin=admin_only, min_value=lo, max_value=hi,
                )
            )

        session.add(
            User(
                username="admin", email="admin@example.com",
                full_name="Demo Administrator",
                password_hash=hash_password("demo-password-1234"),
                role=UserRole.ADMIN,
            )
        )

        session.add(
            Warehouse(
                warehouse_code="WH-CHN-01",
                warehouse_name="Chennai Central Warehouse",
                address="Industrial Estate, Guindy, Chennai",
                city="Chennai", pincode="600032",
                latitude=Decimal("13.0100000"), longitude=Decimal("80.2200000"),
                status=EntityStatus.ACTIVE,
            )
        )

        routes: dict[str, Route] = {}
        for _, _, _, _, area, route_code in LOCATIONS:
            if route_code not in routes:
                route = Route(
                    route_code=route_code, route_name=f"{area} Route",
                    service_area=area, status=EntityStatus.ACTIVE,
                )
                session.add(route)
                routes[route_code] = route
        await session.flush()

        for code, name, lat, lng, area, route_code in LOCATIONS:
            session.add(
                ServiceLocation(
                    service_code=code, location_name=name,
                    address=f"{area} Main Road, {area}, Chennai",
                    area=area, city="Chennai",
                    latitude=Decimal(str(lat)), longitude=Decimal(str(lng)),
                    service_area=area, route_id=routes[route_code].id,
                    status=LocationStatus.ACTIVE,
                )
            )

        await session.commit()

    await engine.dispose()

    print(f"Demo database ready: {DB_PATH}")
    print(f"  {len(LOCATIONS)} service locations, 1 warehouse, {len(routes)} routes")
    print("  Sign in as  admin / demo-password-1234")
    print()
    print("Now start the API. Copy the block for your shell:")
    print()
    print("  PowerShell (Windows)")
    print(f'    $env:DATABASE_URL_OVERRIDE = "{DB_URL}"')
    print('    $env:ROUTING_PROVIDER = "fake"')
    print('    $env:GEOCODING_PROVIDER = "fake"')
    print('    $env:SECRET_KEY = "local-demo-key-not-for-real-use-1234567890"')
    print('    python -m uvicorn app.main:app --port 8000')
    print()
    print("  bash / zsh (macOS, Linux)")
    print(f'    export DATABASE_URL_OVERRIDE="{DB_URL}"')
    print('    export ROUTING_PROVIDER=fake GEOCODING_PROVIDER=fake')
    print('    export SECRET_KEY="local-demo-key-not-for-real-use-1234567890"')
    print('    python -m uvicorn app.main:app --port 8000')


if __name__ == "__main__":
    asyncio.run(main())
