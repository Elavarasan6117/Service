"""Spatial column type.

On PostgreSQL this is a real PostGIS ``geography(Point,4326)``. On SQLite --
used only by the unit-test suite, which has no PostGIS -- it degrades to TEXT
so the schema can be created. The spatial *search* has a matching fallback in
``SpatialSearchService``, which refuses to run the fallback against PostgreSQL.
"""

from __future__ import annotations

from geoalchemy2 import Geography
from sqlalchemy import Text
from sqlalchemy.types import TypeEngine


def geography_point() -> TypeEngine:
    return Geography(
        geometry_type="POINT",
        srid=4326,
        spatial_index=False,  # indexes are declared explicitly in the migration
        nullable=True,
    ).with_variant(Text(), "sqlite")
