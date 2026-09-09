"""Domain -> API response mapping.

Keeping this out of the routers means the canonical serviceability payload is
built in exactly one place, so every endpoint that returns a decision returns
an identical shape.
"""

from __future__ import annotations

from app.models.customer import Customer
from app.models.serviceability_check import ServiceabilityCheck
from app.schemas.customer import (
    NearestServiceLocationOut,
    RouteOut,
    ServiceabilityResultOut,
)
from app.schemas.misc import ServiceabilityCheckOut
from app.services.serviceability import ServiceabilityOutcome


def outcome_to_response(
    outcome: ServiceabilityOutcome, customer: Customer | None = None
) -> ServiceabilityResultOut:
    nearest = None
    route = None

    if outcome.nearest is not None:
        candidate = outcome.nearest.candidate
        nearest = NearestServiceLocationOut(
            id=candidate.id,
            service_code=candidate.service_code,
            name=candidate.location_name,
            latitude=candidate.latitude,
            longitude=candidate.longitude,
            service_area=candidate.service_area,
            distance_meters=outcome.nearest.distance_meters,
            distance_km=round(outcome.nearest.distance_meters / 1000.0, 3),
        )
        route = RouteOut(
            distance_meters=outcome.nearest.distance_meters,
            duration_seconds=outcome.nearest.duration_seconds,
            geometry=outcome.route_geometry,
            geometry_format=outcome.route_geometry_format,
        )

    return ServiceabilityResultOut(
        customer_id=customer.customer_code if customer else None,
        customer_name=customer.customer_name if customer else None,
        check_id=outcome.check_id,
        status=outcome.status,
        threshold_meters=outcome.threshold_meters,
        distance_type="ROAD_DISTANCE",
        routing_provider=outcome.nearest.provider if outcome.nearest else None,
        nearest_service_location=nearest,
        route=route,
        margin_meters=outcome.margin_meters,
        candidate_count=outcome.candidate_count,
        cache_hit=outcome.cache_hit,
        degraded=outcome.degraded,
        error_code=outcome.error_code,
        message=outcome.message,
        reason=outcome.reason,
        retryable=outcome.retryable,
        calculated_at=outcome.calculated_at,
        duration_ms=outcome.duration_ms,
    )


def check_to_response(check: ServiceabilityCheck) -> ServiceabilityCheckOut:
    return ServiceabilityCheckOut(
        id=check.id,
        customer_id=check.customer_id,
        customer_code=check.customer.customer_code if check.customer else None,
        customer_name=check.customer.customer_name if check.customer else None,
        nearest_service_location_id=check.nearest_service_location_id,
        nearest_service_code=(
            check.nearest_service_location.service_code
            if check.nearest_service_location
            else None
        ),
        customer_latitude=(
            float(check.customer_latitude) if check.customer_latitude else None
        ),
        customer_longitude=(
            float(check.customer_longitude) if check.customer_longitude else None
        ),
        calculated_distance_meters=check.calculated_distance_meters,
        distance_km=check.distance_km,
        distance_type=str(check.distance_type),
        routing_provider=check.routing_provider,
        route_duration_seconds=check.route_duration_seconds,
        threshold_meters=check.threshold_meters,
        result=check.result,
        candidate_count=check.candidate_count,
        cache_hit=check.cache_hit,
        degraded=check.degraded,
        error_code=check.error_code,
        reason=check.reason,
        request_timestamp=check.request_timestamp,
        response_timestamp=check.response_timestamp,
        duration_ms=check.duration_ms,
        created_by_label=check.created_by_label,
        created_at=check.created_at,
    )


def customer_to_map(customer: Customer) -> dict:
    return {
        "id": customer.id,
        "customer_code": customer.customer_code,
        "customer_name": customer.customer_name,
        "latitude": float(customer.latitude),
        "longitude": float(customer.longitude),
        "service_status": customer.service_status,
        "nearest_service_location_id": customer.nearest_service_location_id,
        "nearest_service_distance_meters": customer.nearest_service_distance_meters,
    }
