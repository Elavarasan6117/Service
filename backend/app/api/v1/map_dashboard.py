"""Map data, dashboard metrics, warehouses and routes."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import Numeric, cast, func, select

from app.api.deps import ConfigDep, DbSession, require
from app.core.config import settings
from app.core.enums import EntityStatus, LocationStatus, Permission, ServiceStatus
from app.core.errors import NotFoundError, ProviderError
from app.models.customer import Customer
from app.models.route import Route
from app.models.service_location import ServiceLocation
from app.models.serviceability_check import ServiceabilityCheck
from app.models.warehouse import Warehouse
from app.schemas.misc import (
    AreaCount,
    DashboardMetrics,
    MapCustomer,
    MapOperationsResponse,
    RouteCreate,
    RouteOut,
    ServiceLocationOut,
    WarehouseCreate,
    WarehouseOut,
    WarehouseUpdate,
)
from app.services.config_service import KEY_THRESHOLD
from app.services.spatial_search import SpatialSearchService
from app.models.user import User
from app.providers.base import Coordinate
from app.providers.registry import get_route_preview_provider
from app.schemas.misc import RoutePreviewRequest, RoutePreviewResponse, PreviewOrigin, PreviewRoute
import math

router = APIRouter(tags=["map-and-dashboard"])

IST = ZoneInfo(settings.TIMEZONE)


@router.post("/routes/preview", response_model=RoutePreviewResponse)
async def preview_vehicle_routes(
    payload: RoutePreviewRequest,
    session: DbSession,
    user: User = Depends(require(Permission.VIEW)),
) -> RoutePreviewResponse:
    """Return temporary four-wheeler route options from nearby active records."""
    destination = Coordinate(payload.latitude, payload.longitude)
    warehouses = (await session.execute(select(Warehouse).where(
        Warehouse.status == EntityStatus.ACTIVE
    ))).scalars().all()
    customers = (await session.execute(select(Customer).where(
        Customer.latitude.is_not(None), Customer.longitude.is_not(None),
        *([Customer.id != payload.exclude_customer_id] if payload.exclude_customer_id else []),
    ))).scalars().all()

    def distance_km(lat: float, lng: float) -> float:
        lat1, lat2 = math.radians(payload.latitude), math.radians(lat)
        dlat, dlng = lat2 - lat1, math.radians(lng - payload.longitude)
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
        return 6371.0088 * 2 * math.asin(math.sqrt(a))

    candidates: list[tuple[PreviewOrigin, Coordinate, float]] = []
    for item in warehouses:
        lat, lng = float(item.latitude), float(item.longitude)
        air = distance_km(lat, lng)
        if air <= payload.search_radius_km:
            candidates.append((PreviewOrigin(id=item.id, name=item.warehouse_name, kind="WAREHOUSE", latitude=lat, longitude=lng, distance_meters=0, address=item.address), Coordinate(lat, lng), air))
    for item in customers:
        if item.latitude is None or item.longitude is None:
            continue
        lat, lng = float(item.latitude), float(item.longitude)
        air = distance_km(lat, lng)
        if air <= payload.search_radius_km:
            candidates.append((PreviewOrigin(id=item.id, name=item.customer_name, kind="CUSTOMER", latitude=lat, longitude=lng, distance_meters=0, address=item.address), Coordinate(lat, lng), air))

    # Keep a broad geographic shortlist for each origin type. Road distances,
    # never straight-line distance, determine the displayed ranking. Keeping
    # the types separately prevents many nearby customers from hiding a warehouse.
    candidates = [
        row for kind in ("WAREHOUSE", "CUSTOMER")
        for row in sorted(
            (candidate for candidate in candidates if candidate[0].kind == kind),
            key=lambda candidate: candidate[2],
        )[:12]
    ]
    routes: list[PreviewRoute] = []
    road_ranked: list[tuple[PreviewOrigin, Coordinate, int]] = []
    provider = get_route_preview_provider()
    provider_message = None
    route_status = {"WAREHOUSE": "NO_NEARBY_LOCATION", "CUSTOMER": "NO_NEARBY_LOCATION"}
    route_message: dict[str, str | None] = {"WAREHOUSE": None, "CUSTOMER": None}
    for kind in ("WAREHOUSE", "CUSTOMER"):
        if any(row[0].kind == kind for row in candidates):
            route_status[kind] = "NO_DRIVING_ROUTE"
    if candidates:
        try:
            legs = await provider.get_driving_distances(
                [row[1] for row in candidates], destination
            )
        except ProviderError:
            legs = []
            provider_message = "Route service is temporarily unavailable. Please try again."
            for kind in ("WAREHOUSE", "CUSTOMER"):
                if any(row[0].kind == kind for row in candidates):
                    route_status[kind] = "ROUTE_UNAVAILABLE"
                    route_message[kind] = provider_message
        for (origin, coordinate, _), leg in zip(candidates, legs):
            if leg is not None:
                road_ranked.append((origin.model_copy(update={"distance_meters": leg.distance_meters}), coordinate, leg.distance_meters))

    # Keep the nearest road-accessible warehouse and customer, then request
    # Directions alternatives for each selected origin.
    selected: list[tuple[PreviewOrigin, Coordinate, int]] = []
    for kind in ("WAREHOUSE", "CUSTOMER"):
        match = next((row for row in sorted(road_ranked, key=lambda row: row[2]) if row[0].kind == kind), None)
        if match:
            selected.append(match)
    for origin, coordinate, _ in selected:
        try:
            alternatives = await provider.get_driving_routes(coordinate, destination)
        except ProviderError as exc:
            if exc.code == "PROVIDER_NO_ROUTE":
                route_status[origin.kind] = "NO_DRIVING_ROUTE"
                route_message[origin.kind] = "No four-wheeler driving route is available."
            else:
                route_status[origin.kind] = "ROUTE_UNAVAILABLE"
                route_message[origin.kind] = "Driving route could not be calculated."
            continue
        if not alternatives:
            route_status[origin.kind] = "NO_DRIVING_ROUTE"
            route_message[origin.kind] = "No four-wheeler driving route is available."
            continue
        route_status[origin.kind] = "CALCULATED_UNVERIFIED"
        for index, route in enumerate(alternatives):
            routes.append(PreviewRoute(
                id=f"{origin.kind.lower()}-{origin.id}-{index}", origin=origin,
                distance_meters=route.distance_meters,
                duration_seconds=route.duration_seconds, geometry=route.geometry,
                geometry_format=route.geometry_format, provider=route.provider,
            ))
    routes.sort(key=lambda route: route.distance_meters)
    return RoutePreviewResponse(
        destination_latitude=payload.latitude, destination_longitude=payload.longitude,
        nearest_warehouse=next((row[0] for row in selected if row[0].kind == "WAREHOUSE"), None),
        nearest_customer=next((row[0] for row in selected if row[0].kind == "CUSTOMER"), None),
        routes=routes,
        warehouse_route_status=route_status["WAREHOUSE"],
        customer_route_status=route_status["CUSTOMER"],
        warehouse_route_message=route_message["WAREHOUSE"],
        customer_route_message=route_message["CUSTOMER"],
        message=provider_message or ("No nearby road-accessible locations found." if not routes else None),
    )


# --- Map ----------------------------------------------------------------


@router.get("/map/operations", response_model=MapOperationsResponse)
async def map_operations(
    session: DbSession,
    config: ConfigDep,
    user: User = Depends(require(Permission.VIEW)),
    bbox: str | None = Query(
        None,
        description="minLng,minLat,maxLng,maxLat — restricts service locations to the viewport",
    ),
    customer_limit: int = Query(200, ge=1, le=2000),
    service_location_limit: int = Query(2000, ge=1, le=10000),
    since: datetime | None = Query(
        None, description="Only customers created at or after this timestamp"
    ),
) -> MapOperationsResponse:
    """Everything the operations map needs, in one request.

    Bounded deliberately: at 10,000+ service locations, an unbounded response
    would be tens of megabytes and would freeze the browser. The viewport
    filter plus limits keep the payload predictable.
    """
    threshold = await config.get_int(KEY_THRESHOLD)

    warehouses = (
        (
            await session.execute(
                select(Warehouse).where(Warehouse.status == EntityStatus.ACTIVE)
            )
        )
        .scalars()
        .all()
    )

    spatial = SpatialSearchService(session)
    if bbox:
        try:
            min_lng, min_lat, max_lng, max_lat = (float(p) for p in bbox.split(","))
        except ValueError as exc:
            raise NotFoundError(
                "bbox must be four comma-separated numbers: minLng,minLat,maxLng,maxLat"
            ) from exc
        locations = await spatial.locations_in_bbox(
            min_lat, min_lng, max_lat, max_lng, limit=service_location_limit
        )
    else:
        locations = list(
            (
                await session.execute(
                    select(ServiceLocation)
                    .where(ServiceLocation.status == LocationStatus.ACTIVE)
                    .limit(service_location_limit)
                )
            )
            .scalars()
            .all()
        )

    customer_conditions = [Customer.latitude.is_not(None)]
    if since:
        customer_conditions.append(Customer.created_at >= since)

    customers = (
        (
            await session.execute(
                select(Customer)
                .where(*customer_conditions)
                .order_by(Customer.created_at.desc())
                .limit(customer_limit)
            )
        )
        .scalars()
        .all()
    )

    return MapOperationsResponse(
        threshold_meters=threshold,
        center={
            "latitude": settings.MAP_DEFAULT_CENTER_LAT,
            "longitude": settings.MAP_DEFAULT_CENTER_LNG,
        },
        zoom=settings.MAP_DEFAULT_ZOOM,
        tile_url=settings.MAP_TILE_URL,
        tile_attribution=settings.MAP_TILE_ATTRIBUTION,
        warehouses=[WarehouseOut.model_validate(w) for w in warehouses],
        service_locations=[ServiceLocationOut.model_validate(s) for s in locations],
        customers=[
            MapCustomer(
                id=c.id,
                customer_code=c.customer_code,
                customer_name=c.customer_name,
                address=c.address,
                latitude=float(c.latitude),
                longitude=float(c.longitude),
                service_status=c.service_status,
                nearest_service_location_id=c.nearest_service_location_id,
                nearest_service_distance_meters=c.nearest_service_distance_meters,
            )
            for c in customers
        ],
        generated_at=datetime.now(timezone.utc),
    )


# --- Dashboard ----------------------------------------------------------


@router.get("/dashboard/metrics", response_model=DashboardMetrics)
async def dashboard_metrics(
    session: DbSession,
    config: ConfigDep,
    user: User = Depends(require(Permission.VIEW)),
    for_date: date | None = Query(None, alias="date"),
) -> DashboardMetrics:
    """Today's operational counters.

    The day boundary is Asia/Kolkata, not UTC. Using UTC would put the 05:30
    IST cutover in the middle of the Chennai working day and make "today"
    meaningless to the operations team.
    """
    target = for_date or datetime.now(IST).date()
    day_start = datetime.combine(target, time.min, tzinfo=IST).astimezone(timezone.utc)
    day_end = day_start + timedelta(days=1)

    threshold = await config.get_int(KEY_THRESHOLD)

    new_customers = await session.scalar(
        select(func.count())
        .select_from(Customer)
        .where(Customer.created_at >= day_start, Customer.created_at < day_end)
    )

    status_counts = dict(
        (
            await session.execute(
                select(Customer.service_status, func.count())
                .where(Customer.created_at >= day_start, Customer.created_at < day_end)
                .group_by(Customer.service_status)
            )
        ).all()
    )

    checks_today = [
        ServiceabilityCheck.created_at >= day_start,
        ServiceabilityCheck.created_at < day_end,
    ]

    total_checks = await session.scalar(
        select(func.count()).select_from(ServiceabilityCheck).where(*checks_today)
    )

    avg_distance = await session.scalar(
        select(func.avg(ServiceabilityCheck.calculated_distance_meters)).where(
            *checks_today,
            ServiceabilityCheck.calculated_distance_meters.is_not(None),
        )
    )

    median_distance = await session.scalar(
        select(
            func.percentile_cont(0.5).within_group(
                cast(ServiceabilityCheck.calculated_distance_meters, Numeric)
            )
        ).where(
            *checks_today,
            ServiceabilityCheck.calculated_distance_meters.is_not(None),
        )
    ) if session.bind.dialect.name == "postgresql" else None  # type: ignore[union-attr]

    cache_hits = await session.scalar(
        select(func.count())
        .select_from(ServiceabilityCheck)
        .where(*checks_today, ServiceabilityCheck.cache_hit.is_(True))
    )

    # Built once and reused in both select() and group_by(): two separate
    # func.coalesce(...) calls compile to two distinct bind parameters even
    # though they're textually identical, and Postgres (unlike SQLite) then
    # rejects the query as grouping on an expression absent from the SELECT.
    area_expr = func.coalesce(Customer.area, "Unspecified")
    by_area_rows = (
        await session.execute(
            select(area_expr, func.count())
            .where(Customer.created_at >= day_start, Customer.created_at < day_end)
            .group_by(area_expr)
            .order_by(func.count().desc())
            .limit(25)
        )
    ).all()

    return DashboardMetrics(
        date=target,
        new_customers=new_customers or 0,
        service_available=status_counts.get(ServiceStatus.AVAILABLE, 0),
        service_not_available=status_counts.get(ServiceStatus.NOT_AVAILABLE, 0),
        location_verification_required=status_counts.get(
            ServiceStatus.LOCATION_VERIFICATION_REQUIRED, 0
        ),
        route_calculation_errors=status_counts.get(
            ServiceStatus.ROUTE_CALCULATION_ERROR, 0
        ),
        pending_review=status_counts.get(ServiceStatus.PENDING_REVIEW, 0),
        average_distance_meters=int(avg_distance) if avg_distance else None,
        median_distance_meters=int(median_distance) if median_distance else None,
        threshold_meters=threshold,
        by_area=[AreaCount(area=area, count=count) for area, count in by_area_rows],
        total_checks=total_checks or 0,
        cache_hit_rate=(
            round((cache_hits or 0) / total_checks, 4) if total_checks else None
        ),
    )


# --- Warehouses ---------------------------------------------------------


@router.get("/warehouses", response_model=list[WarehouseOut])
async def list_warehouses(
    session: DbSession,
    user: User = Depends(require(Permission.VIEW)),
) -> list[WarehouseOut]:
    result = await session.execute(select(Warehouse).order_by(Warehouse.warehouse_code))
    return [WarehouseOut.model_validate(w) for w in result.scalars().all()]


@router.post(
    "/warehouses", response_model=WarehouseOut, status_code=status.HTTP_201_CREATED
)
async def create_warehouse(
    payload: WarehouseCreate,
    session: DbSession,
    user: User = Depends(require(Permission.CONFIG_WRITE_GENERAL)),
) -> WarehouseOut:
    warehouse = Warehouse(
        **payload.model_dump(exclude={"latitude", "longitude"}),
        latitude=Decimal(str(payload.latitude)),
        longitude=Decimal(str(payload.longitude)),
    )
    session.add(warehouse)
    await session.commit()
    await session.refresh(warehouse)
    return WarehouseOut.model_validate(warehouse)


@router.patch("/warehouses/{warehouse_id}", response_model=WarehouseOut)
async def update_warehouse(
    warehouse_id: uuid.UUID,
    payload: WarehouseUpdate,
    session: DbSession,
    user: User = Depends(require(Permission.CONFIG_WRITE_GENERAL)),
) -> WarehouseOut:
    warehouse = await session.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise NotFoundError(f"No warehouse with id {warehouse_id}.")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        if field in ("latitude", "longitude") and value is not None:
            setattr(warehouse, field, Decimal(str(value)))
        else:
            setattr(warehouse, field, value)

    await session.commit()
    await session.refresh(warehouse)
    return WarehouseOut.model_validate(warehouse)


@router.delete("/warehouses/{warehouse_id}", response_model=WarehouseOut)
async def deactivate_warehouse(
    warehouse_id: uuid.UUID,
    session: DbSession,
    user: User = Depends(require(Permission.CONFIG_WRITE_GENERAL)),
) -> WarehouseOut:
    """Soft delete, same convention as service locations: a warehouse can be
    referenced by historic service locations, so the row is deactivated
    rather than removed.
    """
    warehouse = await session.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise NotFoundError(f"No warehouse with id {warehouse_id}.")

    warehouse.status = EntityStatus.INACTIVE
    await session.commit()
    await session.refresh(warehouse)
    return WarehouseOut.model_validate(warehouse)


# --- Routes -------------------------------------------------------------


@router.get("/routes", response_model=list[RouteOut])
async def list_routes(
    session: DbSession,
    user: User = Depends(require(Permission.VIEW)),
) -> list[RouteOut]:
    result = await session.execute(select(Route).order_by(Route.route_code))
    return [RouteOut.model_validate(r) for r in result.scalars().all()]


@router.post("/routes", response_model=RouteOut, status_code=status.HTTP_201_CREATED)
async def create_route(
    payload: RouteCreate,
    session: DbSession,
    user: User = Depends(require(Permission.CONFIG_WRITE_GENERAL)),
) -> RouteOut:
    route = Route(**payload.model_dump())
    session.add(route)
    await session.commit()
    await session.refresh(route)
    return RouteOut.model_validate(route)
