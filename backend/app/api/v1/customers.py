from __future__ import annotations

import math
import uuid
from datetime import datetime

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy import func, or_, select

from app.api.deps import (
    CustomerServiceDep,
    DbSession,
    rate_limit,
    require,
)
from app.api.v1.mappers import outcome_to_response
from app.core.config import settings
from app.core.enums import Permission, ServiceStatus
from app.core.errors import NotFoundError
from app.models.customer import Customer
from app.models.serviceability_check import ServiceabilityCheck
from app.schemas.common import Page, PageMeta
from app.models.user import User
from app.schemas.customer import (
    CustomerCreate,
    CustomerCreateResponse,
    CustomerLocationUpdate,
    CustomerOut,
    CustomerUpdate,
    ServiceabilityResultOut,
)

router = APIRouter(prefix="/customers", tags=["customers"])


@router.post(
    "",
    response_model=CustomerCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(rate_limit(settings.RATE_LIMIT_CHECK_PER_MINUTE, "customer_create"))
    ],
)
async def create_customer(
    payload: CustomerCreate,
    session: DbSession,
    service: CustomerServiceDep,
    user: User = Depends(require(Permission.CUSTOMER_CREATE)),
) -> CustomerCreateResponse:
    """Create a customer, geocode it, and run the serviceability check.

    A geocoding failure does not fail this request and does not mark the
    customer NOT_AVAILABLE -- the customer is created with status
    LOCATION_VERIFICATION_REQUIRED so an operator can place the marker.
    """
    customer, outcome = await service.create(
        customer_code=payload.customer_code,
        customer_name=payload.customer_name,
        phone=payload.phone,
        address=payload.address,
        area=payload.area,
        city=payload.city,
        pincode=payload.pincode,
        service_type=payload.service_type,
        latitude=payload.latitude,
        longitude=payload.longitude,
        actor=user,
    )
    await session.commit()
    await session.refresh(customer)

    return CustomerCreateResponse(
        customer=CustomerOut.model_validate(customer),
        serviceability=(
            outcome_to_response(outcome, customer) if outcome is not None else None
        ),
    )


@router.get("", response_model=Page[CustomerOut])
async def list_customers(
    session: DbSession,
    user: User = Depends(require(Permission.VIEW)),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    status_filter: ServiceStatus | None = Query(None, alias="status"),
    area: str | None = None,
    q: str | None = Query(None, description="Search name, code, address or phone"),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> Page[CustomerOut]:
    conditions = []
    if status_filter:
        conditions.append(Customer.service_status == status_filter)
    if area:
        conditions.append(Customer.area == area)
    if created_from:
        conditions.append(Customer.created_at >= created_from)
    if created_to:
        conditions.append(Customer.created_at <= created_to)
    if q:
        # ILIKE with a leading wildcard cannot use a btree index. Acceptable at
        # this table's size; a pg_trgm GIN index is the upgrade path.
        term = f"%{q}%"
        conditions.append(
            or_(
                Customer.customer_name.ilike(term),
                Customer.customer_code.ilike(term),
                Customer.address.ilike(term),
                Customer.phone.ilike(term),
            )
        )

    total = await session.scalar(
        select(func.count()).select_from(Customer).where(*conditions)
    )
    result = await session.execute(
        select(Customer)
        .where(*conditions)
        .order_by(Customer.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [CustomerOut.model_validate(c) for c in result.scalars().all()]
    return Page[CustomerOut](
        items=items,
        meta=PageMeta(
            total=total or 0,
            page=page,
            page_size=page_size,
            total_pages=math.ceil((total or 0) / page_size) if total else 0,
        ),
    )


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(
    customer_id: uuid.UUID,
    session: DbSession,
    user: User = Depends(require(Permission.VIEW)),
) -> CustomerOut:
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise NotFoundError(f"No customer with id {customer_id}.")
    return CustomerOut.model_validate(customer)


@router.patch("/{customer_id}", response_model=CustomerOut)
async def update_customer(
    customer_id: uuid.UUID,
    payload: CustomerUpdate,
    session: DbSession,
    user: User = Depends(require(Permission.CUSTOMER_CREATE)),
) -> CustomerOut:
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise NotFoundError(f"No customer with id {customer_id}.")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(customer, field, value)
    await session.commit()
    await session.refresh(customer)
    return CustomerOut.model_validate(customer)


@router.patch("/{customer_id}/location", response_model=ServiceabilityResultOut)
async def update_customer_location(
    customer_id: uuid.UUID,
    payload: CustomerLocationUpdate,
    session: DbSession,
    service: CustomerServiceDep,
    user: User = Depends(require(Permission.CUSTOMER_LOCATION_ADJUST)),
) -> ServiceabilityResultOut:
    """Confirm a manually placed marker and re-run the check (brief §14).

    Address geocoding in Chennai is frequently approximate, so an operator
    correcting the point is a normal part of the workflow, not an exception.
    """
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise NotFoundError(f"No customer with id {customer_id}.")

    outcome = await service.update_location(
        customer,
        payload.latitude,
        payload.longitude,
        actor=user,
        reason=payload.reason,
    )
    await session.commit()
    return outcome_to_response(outcome, customer)


@router.post(
    "/{customer_id}/serviceability-check",
    response_model=ServiceabilityResultOut,
    dependencies=[
        Depends(rate_limit(settings.RATE_LIMIT_CHECK_PER_MINUTE, "check"))
    ],
)
async def run_serviceability_check(
    customer_id: uuid.UUID,
    session: DbSession,
    service: CustomerServiceDep,
    user: User = Depends(require(Permission.CHECK_RUN)),
    force_refresh: bool = Body(
        False,
        embed=True,
        description="Bypass the route cache and re-measure from the provider.",
    ),
) -> ServiceabilityResultOut:
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise NotFoundError(f"No customer with id {customer_id}.")

    outcome = await service.run_check(customer, actor=user, force_refresh=force_refresh)
    await session.commit()
    return outcome_to_response(outcome, customer)


@router.get("/{customer_id}/serviceability", response_model=ServiceabilityResultOut)
async def get_latest_serviceability(
    customer_id: uuid.UUID,
    session: DbSession,
    user: User = Depends(require(Permission.VIEW)),
) -> ServiceabilityResultOut:
    """The most recent stored decision. Does not re-run the calculation."""
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise NotFoundError(f"No customer with id {customer_id}.")

    result = await session.execute(
        select(ServiceabilityCheck)
        .where(ServiceabilityCheck.customer_id == customer_id)
        .order_by(ServiceabilityCheck.created_at.desc())
        .limit(1)
    )
    check = result.scalar_one_or_none()
    if check is None:
        raise NotFoundError("No serviceability check has been run for this customer.")

    from app.schemas.customer import NearestServiceLocationOut, RouteOut

    nearest = None
    route = None
    if check.nearest_service_location is not None and check.calculated_distance_meters:
        loc = check.nearest_service_location
        nearest = NearestServiceLocationOut(
            id=loc.id,
            service_code=loc.service_code,
            name=loc.location_name,
            latitude=float(loc.latitude) if loc.latitude else None,
            longitude=float(loc.longitude) if loc.longitude else None,
            service_area=loc.service_area,
            distance_meters=check.calculated_distance_meters,
            distance_km=check.distance_km or 0.0,
        )
        route = RouteOut(
            distance_meters=check.calculated_distance_meters,
            duration_seconds=check.route_duration_seconds,
            geometry=check.route_geometry,
            geometry_format=check.route_geometry_format,
        )

    return ServiceabilityResultOut(
        customer_id=customer.customer_code,
        customer_name=customer.customer_name,
        check_id=check.id,
        status=customer.service_status,
        threshold_meters=check.threshold_meters,
        distance_type=str(check.distance_type),
        routing_provider=check.routing_provider,
        nearest_service_location=nearest,
        route=route,
        margin_meters=check.margin_meters,
        candidate_count=check.candidate_count,
        cache_hit=check.cache_hit,
        degraded=check.degraded,
        error_code=check.error_code,
        reason=check.reason,
        retryable=customer.service_status.is_retryable,
        calculated_at=check.response_timestamp,
        duration_ms=check.duration_ms,
    )
