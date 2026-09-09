from __future__ import annotations

import math
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, EngineDep, require
from app.api.v1.mappers import check_to_response, outcome_to_response
from app.core.enums import CheckResult, Permission
from app.core.errors import GeocodingError, NotFoundError
from app.models.serviceability_check import ServiceabilityCheck
from app.models.customer import Customer
from app.providers.registry import get_geocoding_provider
from app.schemas.common import Page, PageMeta
from app.schemas.customer import (
    ServiceabilityPreviewRequest,
    ServiceabilityResultOut,
)
from app.schemas.common import MessageResponse
from app.schemas.misc import ServiceabilityCheckOut
from app.models.user import User

router = APIRouter(prefix="/serviceability", tags=["serviceability"])


@router.get("/checks", response_model=Page[ServiceabilityCheckOut])
async def list_checks(
    session: DbSession,
    user: User = Depends(require(Permission.VIEW)),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    customer_id: uuid.UUID | None = None,
    result_filter: CheckResult | None = Query(None, alias="result"),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
) -> Page[ServiceabilityCheckOut]:
    """The audit trail. Append-only; nothing here is ever edited or removed."""
    conditions = []
    if customer_id:
        conditions.append(ServiceabilityCheck.customer_id == customer_id)
    if result_filter:
        conditions.append(ServiceabilityCheck.result == result_filter)
    if date_from:
        conditions.append(ServiceabilityCheck.created_at >= date_from)
    if date_to:
        conditions.append(ServiceabilityCheck.created_at <= date_to)

    total = await session.scalar(
        select(func.count()).select_from(ServiceabilityCheck).where(*conditions)
    )
    result = await session.execute(
        select(ServiceabilityCheck)
        .options(
            selectinload(ServiceabilityCheck.customer),
            selectinload(ServiceabilityCheck.nearest_service_location),
        )
        .where(*conditions)
        .order_by(ServiceabilityCheck.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page[ServiceabilityCheckOut](
        items=[check_to_response(c) for c in result.scalars().all()],
        meta=PageMeta(
            total=total or 0,
            page=page,
            page_size=page_size,
            total_pages=math.ceil((total or 0) / page_size) if total else 0,
        ),
    )


@router.get("/checks/{check_id}", response_model=ServiceabilityCheckOut)
async def get_check(
    check_id: uuid.UUID,
    session: DbSession,
    user: User = Depends(require(Permission.VIEW)),
) -> ServiceabilityCheckOut:
    result = await session.execute(
        select(ServiceabilityCheck)
        .options(
            selectinload(ServiceabilityCheck.customer),
            selectinload(ServiceabilityCheck.nearest_service_location),
        )
        .where(ServiceabilityCheck.id == check_id)
    )
    check = result.scalar_one_or_none()
    if check is None:
        raise NotFoundError(f"No serviceability check with id {check_id}.")
    return check_to_response(check)


@router.delete("/checks/{check_id}", response_model=MessageResponse)
async def delete_check(
    check_id: uuid.UUID,
    session: DbSession,
    user: User = Depends(require(Permission.AUDIT_DELETE)),
) -> MessageResponse:
    """Remove a selected check and its customer marker when applicable."""
    check = await session.get(ServiceabilityCheck, check_id)
    if check is None:
        raise NotFoundError(f"No serviceability check with id {check_id}.")

    if check.customer_id is not None:
        customer_id = check.customer_id
        # A customer-linked task represents the map marker. Remove its related
        # checks as well so the deleted customer cannot leave orphaned history
        # rows looking like active work.
        await session.execute(
            delete(ServiceabilityCheck).where(
                ServiceabilityCheck.customer_id == customer_id
            )
        )
        customer = await session.get(Customer, customer_id)
        if customer is not None:
            await session.delete(customer)
    else:
        await session.delete(check)
    await session.commit()
    return MessageResponse(message="Serviceability check and map customer deleted.")


@router.post("/preview", response_model=ServiceabilityResultOut)
async def preview(
    payload: ServiceabilityPreviewRequest,
    session: DbSession,
    engine: EngineDep,
    user: User = Depends(require(Permission.CHECK_RUN)),
) -> ServiceabilityResultOut:
    """Preview a location without creating a customer or audit row.

    The dashboard calls this while a user moves the draft marker. Persisting
    each preview would create duplicate history when the customer is saved;
    only the final customer check is recorded.
    """
    latitude, longitude = payload.latitude, payload.longitude

    if latitude is None or longitude is None:
        try:
            geocoded = await get_geocoding_provider().geocode(payload.address or "")
            latitude, longitude = geocoded.latitude, geocoded.longitude
        except GeocodingError:
            latitude = longitude = None

    outcome = await engine.evaluate(
        latitude=latitude,
        longitude=longitude,
        customer_id=None,
        actor=user,
        service_type=payload.service_type,
    )
    await session.rollback()
    return outcome_to_response(outcome)
