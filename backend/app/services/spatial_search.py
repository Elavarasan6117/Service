"""Stage 1 — spatial candidate search.

This narrows thousands of service locations down to a handful of plausible
candidates using the PostGIS spatial index, before a single rupee is spent on
routing. It returns STRAIGHT-LINE distances and says so loudly: the values it
produces are for ordering and diagnostics only and must never reach the
serviceability decision.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DistanceType, LocationStatus
from app.core.logging import get_logger
from app.models.service_location import ServiceLocation

logger = get_logger(__name__)

EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True, slots=True)
class Candidate:
    """A service location that *might* be the nearest one.

    ``straight_line_meters`` is explicitly typed STRAIGHT_LINE. The engine reads
    only ``id``/``latitude``/``longitude`` from this object; it obtains distance
    from the routing provider.
    """

    id: uuid.UUID
    service_code: str
    location_name: str
    latitude: float
    longitude: float
    service_area: str | None
    straight_line_meters: int
    distance_type: DistanceType = DistanceType.STRAIGHT_LINE


def haversine_meters(
    lat1: float, lng1: float, lat2: float, lng2: float
) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


class SpatialSearchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def _is_postgres(self) -> bool:
        return self.session.bind.dialect.name == "postgresql"  # type: ignore[union-attr]

    async def count_active_locations(self) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(ServiceLocation)
            .where(
                ServiceLocation.status == LocationStatus.ACTIVE,
                ServiceLocation.latitude.is_not(None),
            )
        )
        return int(result.scalar_one())

    async def find_candidates(
        self, latitude: float, longitude: float, radius_meters: int, limit: int
    ) -> list[Candidate]:
        """The k nearest ACTIVE service locations within ``radius_meters``.

        Ordered by straight-line distance ascending -- which is *not* the final
        ordering. The engine re-orders by road distance in Stage 2.
        """
        if self._is_postgres:
            return await self._find_candidates_postgis(
                latitude, longitude, radius_meters, limit
            )
        return await self._find_candidates_fallback(
            latitude, longitude, radius_meters, limit
        )

    async def _find_candidates_postgis(
        self, latitude: float, longitude: float, radius_meters: int, limit: int
    ) -> list[Candidate]:
        # ST_DWithin is index-assisted and bounds the scan; the <-> operator
        # then orders using the same GiST index rather than sorting all rows.
        # Note ST_MakePoint(longitude, latitude) -- x then y.
        sql = text(
            """
            SELECT
                sl.id,
                sl.service_code,
                sl.location_name,
                sl.latitude::float8  AS latitude,
                sl.longitude::float8 AS longitude,
                sl.service_area,
                ST_Distance(sl.location, :origin::geography) AS straight_line_meters
            FROM service_locations sl
            WHERE sl.status = :active_status
              AND sl.location IS NOT NULL
              AND ST_DWithin(sl.location, :origin::geography, :radius)
            ORDER BY sl.location <-> :origin::geography
            LIMIT :limit
            """
        ).bindparams(
            origin=f"SRID=4326;POINT({longitude} {latitude})",
            active_status=LocationStatus.ACTIVE.value,
            radius=radius_meters,
            limit=limit,
        )
        result = await self.session.execute(sql)
        return [
            Candidate(
                id=row.id,
                service_code=row.service_code,
                location_name=row.location_name,
                latitude=float(row.latitude),
                longitude=float(row.longitude),
                service_area=row.service_area,
                straight_line_meters=int(round(row.straight_line_meters)),
            )
            for row in result
        ]

    async def _find_candidates_fallback(
        self, latitude: float, longitude: float, radius_meters: int, limit: int
    ) -> list[Candidate]:
        """Pure-Python haversine scan for environments without PostGIS.

        Used by the unit-test suite, which runs on SQLite. It is O(n) and does
        not scale, which is precisely why PostGIS is a production requirement.
        Guarded so it can never silently run against PostgreSQL.
        """
        if self._is_postgres:  # pragma: no cover - defensive
            raise RuntimeError(
                "The non-spatial fallback must never run against PostgreSQL. "
                "Ensure the PostGIS extension is installed."
            )

        result = await self.session.execute(
            select(ServiceLocation).where(
                ServiceLocation.status == LocationStatus.ACTIVE,
                ServiceLocation.latitude.is_not(None),
                ServiceLocation.longitude.is_not(None),
            )
        )
        rows = list(result.scalars().all())

        scored: list[tuple[float, ServiceLocation]] = []
        for row in rows:
            distance = haversine_meters(
                latitude, longitude, float(row.latitude), float(row.longitude)
            )
            if distance <= radius_meters:
                scored.append((distance, row))
        scored.sort(key=lambda pair: pair[0])

        return [
            Candidate(
                id=row.id,
                service_code=row.service_code,
                location_name=row.location_name,
                latitude=float(row.latitude),
                longitude=float(row.longitude),
                service_area=row.service_area,
                straight_line_meters=int(round(distance)),
            )
            for distance, row in scored[:limit]
        ]

    async def locations_in_bbox(
        self,
        min_lat: float,
        min_lng: float,
        max_lat: float,
        max_lng: float,
        limit: int = 5000,
    ) -> list[ServiceLocation]:
        """Viewport query for the map. Keeps payloads bounded when zoomed out."""
        stmt = (
            select(ServiceLocation)
            .where(
                ServiceLocation.latitude.between(min_lat, max_lat),
                ServiceLocation.longitude.between(min_lng, max_lng),
                ServiceLocation.status == LocationStatus.ACTIVE,
            )
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
