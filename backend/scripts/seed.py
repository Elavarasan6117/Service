#!/usr/bin/env python3
"""Seed the database.

Creates the bootstrap admin, a warehouse, routes and a set of Chennai service
locations so the application is demonstrable end to end before the real data
arrives.

IMPORTANT: the service locations here are SYNTHETIC. They are plausible points
in real Chennai localities, not the operational network. Replace them with the
real data via the importer before UAT sign-off, and never run this against
production with --demo.

Usage:
    python scripts/seed.py                 # admin + warehouse + routes only
    python scripts/seed.py --demo          # also create synthetic locations
    python scripts/seed.py --demo --count 200
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.enums import (  # noqa: E402
    CoordinateSource,
    EntityStatus,
    LocationStatus,
    UserRole,
)
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.models.route import Route  # noqa: E402
from app.models.service_location import ServiceLocation  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.warehouse import Warehouse  # noqa: E402

configure_logging(settings.LOG_LEVEL, "console")
logger = get_logger("seed")

# Real Chennai localities with approximate centroids. Synthetic points are
# scattered within a small radius of each so the data looks and behaves like a
# genuine urban distribution rather than a uniform grid.
CHENNAI_AREAS: list[tuple[str, float, float, str]] = [
    ("Anna Nagar",        13.0850, 80.2101, "RT-NORTH-01"),
    ("Ambattur",          13.1143, 80.1548, "RT-NORTH-02"),
    ("Perambur",          13.1170, 80.2330, "RT-NORTH-03"),
    ("Kilpauk",           13.0780, 80.2410, "RT-CENTRAL-01"),
    ("Egmore",            13.0732, 80.2609, "RT-CENTRAL-02"),
    ("T. Nagar",          13.0418, 80.2341, "RT-CENTRAL-03"),
    ("Nungambakkam",      13.0569, 80.2425, "RT-CENTRAL-04"),
    ("Mylapore",          13.0339, 80.2698, "RT-SOUTH-01"),
    ("Adyar",             13.0067, 80.2570, "RT-SOUTH-02"),
    ("Velachery",         12.9756, 80.2207, "RT-SOUTH-03"),
    ("Guindy",            13.0067, 80.2206, "RT-SOUTH-04"),
    ("Tambaram",          12.9249, 80.1000, "RT-SOUTH-05"),
    ("Porur",             13.0359, 80.1567, "RT-WEST-01"),
    ("Vadapalani",        13.0500, 80.2121, "RT-WEST-02"),
    ("Ashok Nagar",       13.0358, 80.2114, "RT-WEST-03"),
    ("Thiruvanmiyur",     12.9830, 80.2594, "RT-SOUTH-06"),
    ("Sholinganallur",    12.9010, 80.2279, "RT-OMR-01"),
    ("Perungudi",         12.9650, 80.2450, "RT-OMR-02"),
    ("Royapuram",         13.1067, 80.2930, "RT-NORTH-04"),
    ("Washermanpet",      13.1170, 80.2860, "RT-NORTH-05"),
]

BUSINESS_TYPES = [
    "Foods", "Traders", "Stores", "Enterprises", "Provisions", "Mart",
    "Agencies", "Distributors", "Supermarket", "Bakery", "Sweets", "Hotel",
]


def _build_area_prefixes() -> dict[str, str]:
    """3-character service-code prefix per area, disambiguated so two areas
    never collide -- a plain area[:3] gives "Perambur" and "Perungudi" the
    same "PER" prefix, which corrupts every service_code past that point."""
    used: set[str] = set()
    prefixes: dict[str, str] = {}
    for area, _, _, _ in CHENNAI_AREAS:
        base = "".join(ch for ch in area.upper() if ch.isalpha())[:3]
        candidate = base
        suffix = 1
        while candidate in used:
            candidate = f"{base[:2]}{suffix}"
            suffix += 1
        used.add(candidate)
        prefixes[area] = candidate
    return prefixes


AREA_PREFIXES = _build_area_prefixes()


async def seed_admin(session) -> User:
    result = await session.execute(
        select(User).where(User.username == settings.BOOTSTRAP_ADMIN_USERNAME)
    )
    admin = result.scalar_one_or_none()
    if admin:
        logger.info("admin_exists", username=admin.username)
        return admin

    if not settings.BOOTSTRAP_ADMIN_PASSWORD:
        raise SystemExit(
            "BOOTSTRAP_ADMIN_PASSWORD is not set. Set it in .env before seeding; "
            "this script will not invent a password for an administrator account."
        )
    if len(settings.BOOTSTRAP_ADMIN_PASSWORD) < 12:
        raise SystemExit("BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters.")

    admin = User(
        username=settings.BOOTSTRAP_ADMIN_USERNAME,
        email=settings.BOOTSTRAP_ADMIN_EMAIL,
        full_name="System Administrator",
        password_hash=hash_password(settings.BOOTSTRAP_ADMIN_PASSWORD),
        role=UserRole.ADMIN,
    )
    session.add(admin)
    await session.flush()
    logger.info("admin_created", username=admin.username)
    return admin


async def seed_warehouse(session) -> Warehouse:
    result = await session.execute(
        select(Warehouse).where(Warehouse.warehouse_code == "WH-CHN-01")
    )
    warehouse = result.scalar_one_or_none()
    if warehouse:
        return warehouse

    warehouse = Warehouse(
        warehouse_code="WH-CHN-01",
        warehouse_name="Chennai Central Warehouse",
        address="Industrial Estate, Guindy, Chennai",
        city="Chennai",
        pincode="600032",
        latitude=Decimal("13.0100000"),
        longitude=Decimal("80.2200000"),
        status=EntityStatus.ACTIVE,
    )
    session.add(warehouse)
    await session.flush()
    logger.info("warehouse_created", code=warehouse.warehouse_code)
    return warehouse


async def seed_routes(session) -> dict[str, Route]:
    routes: dict[str, Route] = {}
    for area, _, _, route_code in CHENNAI_AREAS:
        result = await session.execute(select(Route).where(Route.route_code == route_code))
        route = result.scalar_one_or_none()
        if route is None:
            route = Route(
                route_code=route_code,
                route_name=f"{area} Route",
                service_area=area,
                status=EntityStatus.ACTIVE,
            )
            session.add(route)
            await session.flush()
        routes[route_code] = route
    logger.info("routes_ready", count=len(routes))
    return routes


async def seed_service_locations(session, warehouse, routes, count: int) -> int:
    existing = await session.scalar(select(func.count()).select_from(ServiceLocation))
    if existing:
        logger.info("service_locations_exist", count=existing, action="skipped")
        return 0

    rng = random.Random(20260904)  # fixed seed -> reproducible demo data
    created = 0
    per_area = max(1, count // len(CHENNAI_AREAS))

    for area, lat, lng, route_code in CHENNAI_AREAS:
        for i in range(per_area):
            # ~0.012 degrees is roughly 1.3 km; clusters look like real
            # commercial density rather than an evenly spaced lattice.
            jitter_lat = rng.gauss(0, 0.010)
            jitter_lng = rng.gauss(0, 0.010)
            code = f"SRV-{AREA_PREFIXES[area]}-{i + 1:03d}"
            session.add(
                ServiceLocation(
                    service_code=code,
                    location_name=(
                        f"{rng.choice(['Sri', 'New', 'Royal', 'Star', 'Anand', 'Kumar'])} "
                        f"{rng.choice(BUSINESS_TYPES)}"
                    ),
                    address=f"{rng.randint(1, 180)}, {area} Main Road, {area}, Chennai",
                    area=area,
                    city="Chennai",
                    latitude=Decimal(f"{lat + jitter_lat:.7f}"),
                    longitude=Decimal(f"{lng + jitter_lng:.7f}"),
                    service_area=area,
                    route_id=routes[route_code].id,
                    warehouse_id=warehouse.id,
                    status=LocationStatus.ACTIVE,
                    coordinate_source=CoordinateSource.IMPORTED,
                )
            )
            created += 1

    await session.flush()
    logger.info("service_locations_created", count=created)
    return created


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the serviceability database.")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Also create SYNTHETIC Chennai service locations for demonstration.",
    )
    parser.add_argument("--count", type=int, default=200)
    args = parser.parse_args()

    if args.demo and settings.is_production:
        raise SystemExit(
            "Refusing to create synthetic service locations in production. "
            "Import the real data instead."
        )

    async with session_scope() as session:
        await seed_admin(session)
        warehouse = await seed_warehouse(session)
        routes = await seed_routes(session)
        if args.demo:
            await seed_service_locations(session, warehouse, routes, args.count)
        else:
            logger.info(
                "demo_data_skipped",
                hint="Pass --demo for synthetic locations, or use POST /api/v1/imports.",
            )

    logger.info("seed_complete")


if __name__ == "__main__":
    asyncio.run(main())
