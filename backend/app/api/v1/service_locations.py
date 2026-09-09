from __future__ import annotations

import math
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select

from app.api.deps import DbSession, require
from app.core.enums import CoordinateSource, LocationStatus, Permission
from app.core.errors import DuplicateResourceError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.service_location import ServiceLocation
from app.providers.base import Coordinate
from app.providers.registry import get_routing_provider
from app.schemas.common import Page, PageMeta
from app.schemas.misc import (
    NearbyResponse,
    NearbyServiceLocation,
    ServiceLocationCreate,
    ServiceLocationOut,
    ServiceLocationUpdate,
)
from app.services.cache import RouteCache
from app.services.events import EventType, publish
from app.services.spatial_search import SpatialSearchService
from app.models.user import User

router = APIRouter(prefix="/service-locations", tags=["service-locations"])
logger = get_logger(__name__)


@router.get("", response_model=Page[ServiceLocationOut])
async def list_service_locations(
    session: DbSession,
    user: User = Depends(require(Permission.VIEW)),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    status_filter: LocationStatus | None = Query(None, alias="status"),
    service_area: str | None = None,
    route_id: uuid.UUID | None = None,
    q: str | None = None,
) -> Page[ServiceLocationOut]:
    conditions = []
    if status_filter:
        conditions.append(ServiceLocation.status == status_filter)
    if service_area:
        conditions.append(ServiceLocation.service_area == service_area)
    if route_id:
        conditions.append(ServiceLocation.route_id == route_id)
    if q:
        term = f"%{q}%"
        conditions.append(
            or_(
                ServiceLocation.location_name.ilike(term),
                ServiceLocation.service_code.ilike(term),
                ServiceLocation.address.ilike(term),
            )
        )

    total = await session.scalar(
        select(func.count()).select_from(ServiceLocation).where(*conditions)
    )
    result = await session.execute(
        select(ServiceLocation)
        .where(*conditions)
        .order_by(ServiceLocation.service_code)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page[ServiceLocationOut](
        items=[ServiceLocationOut.model_validate(r) for r in result.scalars().all()],
        meta=PageMeta(
            total=total or 0,
            page=page,
            page_size=page_size,
            total_pages=math.ceil((total or 0) / page_size) if total else 0,
        ),
    )


@router.get("/nearby", response_model=NearbyResponse)
async def find_nearby(
    session: DbSession,
    user: User = Depends(require(Permission.VIEW)),
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius_m: int = Query(8000, ge=1, le=100_000),
    limit: int = Query(20, ge=1, le=200),
) -> NearbyResponse:
    """Stage-1 candidate search — STRAIGHT-LINE distances only.

    Exposed for diagnostics, coverage analysis and admin tooling. The response
    states explicitly that it is not a serviceability decision, because the one
    thing this system must never do is let a geodesic distance be mistaken for
    the answer.
    """
    spatial = SpatialSearchService(session)
    candidates = await spatial.find_candidates(lat, lng, radius_m, limit)
    return NearbyResponse(
        radius_meters=radius_m,
        items=[
            NearbyServiceLocation(
                id=c.id,
                service_code=c.service_code,
                location_name=c.location_name,
                latitude=c.latitude,
                longitude=c.longitude,
                service_area=c.service_area,
                straight_line_meters=c.straight_line_meters,
            )
            for c in candidates
        ],
    )


@router.post("", response_model=ServiceLocationOut, status_code=status.HTTP_201_CREATED)
async def create_service_location(
    payload: ServiceLocationCreate,
    session: DbSession,
    user: User = Depends(require(Permission.SERVICE_LOCATION_WRITE)),
) -> ServiceLocationOut:
    existing = await session.scalar(
        select(func.count())
        .select_from(ServiceLocation)
        .where(ServiceLocation.service_code == payload.service_code)
    )
    if existing:
        raise DuplicateResourceError(
            f"A service location with code '{payload.service_code}' already exists."
        )

    # A location with no coordinates can never be a candidate, so activating it
    # would quietly shrink coverage. Hold it for verification instead.
    if payload.latitude is None and payload.status == LocationStatus.ACTIVE:
        raise ValidationError(
            "A service location cannot be ACTIVE without coordinates. "
            "Provide latitude and longitude, or create it as PENDING_VERIFICATION."
        )

    location = ServiceLocation(
        **payload.model_dump(exclude={"latitude", "longitude"}),
        latitude=Decimal(str(payload.latitude)) if payload.latitude is not None else None,
        longitude=(
            Decimal(str(payload.longitude)) if payload.longitude is not None else None
        ),
        coordinate_source=CoordinateSource.PROVIDED,
    )
    session.add(location)
    await session.commit()
    await session.refresh(location)

    await publish(
        EventType.SERVICE_LOCATION_UPDATED,
        {"id": str(location.id), "serviceCode": location.service_code, "action": "created"},
    )
    return ServiceLocationOut.model_validate(location)


@router.get("/{location_id}", response_model=ServiceLocationOut)
async def get_service_location(
    location_id: uuid.UUID,
    session: DbSession,
    user: User = Depends(require(Permission.VIEW)),
) -> ServiceLocationOut:
    location = await session.get(ServiceLocation, location_id)
    if location is None:
        raise NotFoundError(f"No service location with id {location_id}.")
    return ServiceLocationOut.model_validate(location)


@router.patch("/{location_id}", response_model=ServiceLocationOut)
async def update_service_location(
    location_id: uuid.UUID,
    payload: ServiceLocationUpdate,
    session: DbSession,
    user: User = Depends(require(Permission.SERVICE_LOCATION_WRITE)),
) -> ServiceLocationOut:
    """Update a service location.

    If the coordinates change, every cached route involving the old position is
    invalidated immediately. Without this, subsequent checks would be decided
    against distances measured to where the location used to be -- brief §25
    test 10 covers exactly this.
    """
    location = await session.get(ServiceLocation, location_id)
    if location is None:
        raise NotFoundError(f"No service location with id {location_id}.")

    old_point = None
    if location.latitude is not None and location.longitude is not None:
        old_point = Coordinate(float(location.latitude), float(location.longitude))

    data = payload.model_dump(exclude_unset=True)
    moved = False
    for field, value in data.items():
        if field in ("latitude", "longitude") and value is not None:
            moved = True
            setattr(location, field, Decimal(str(value)))
        else:
            setattr(location, field, value)

    if moved:
        location.coordinate_source = CoordinateSource.MANUAL

    await session.commit()
    await session.refresh(location)

    if moved:
        cache = RouteCache(get_routing_provider().name)
        invalidated = 0
        if old_point is not None:
            invalidated += await cache.invalidate_point(old_point)
        if location.latitude is not None:
            invalidated += await cache.invalidate_point(
                Coordinate(float(location.latitude), float(location.longitude))
            )
        logger.info(
            "service_location_moved",
            service_code=location.service_code,
            cache_entries_invalidated=invalidated,
            actor=user.username,
        )

    await publish(
        EventType.SERVICE_LOCATION_UPDATED,
        {
            "id": str(location.id),
            "serviceCode": location.service_code,
            "action": "moved" if moved else "updated",
            "latitude": float(location.latitude) if location.latitude else None,
            "longitude": float(location.longitude) if location.longitude else None,
        },
    )
    return ServiceLocationOut.model_validate(location)


@router.delete("/{location_id}", response_model=ServiceLocationOut)
async def deactivate_service_location(
    location_id: uuid.UUID,
    session: DbSession,
    user: User = Depends(require(Permission.SERVICE_LOCATION_WRITE)),
) -> ServiceLocationOut:
    """Soft delete.

    Hard deletion would break the foreign key on historic serviceability checks
    and destroy the audit trail. Deactivation removes the location from
    candidate searches while keeping every past decision explainable.
    """
    location = await session.get(ServiceLocation, location_id)
    if location is None:
        raise NotFoundError(f"No service location with id {location_id}.")

    location.status = LocationStatus.INACTIVE
    await session.commit()
    await session.refresh(location)

    if location.latitude is not None:
        await RouteCache(get_routing_provider().name).invalidate_point(
            Coordinate(float(location.latitude), float(location.longitude))
        )
    await publish(
        EventType.SERVICE_LOCATION_UPDATED,
        {"id": str(location.id), "serviceCode": location.service_code, "action": "deactivated"},
    )
    return ServiceLocationOut.model_validate(location)
