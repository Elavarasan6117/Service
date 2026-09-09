from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import Field

from app.core.enums import (
    CheckResult,
    ConfigValueType,
    CoordinateSource,
    EntityStatus,
    LocationStatus,
    ServiceStatus,
    UserRole,
)
from app.schemas.common import CamelModel

# --- Auth ---------------------------------------------------------------


class LoginRequest(CamelModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(CamelModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(CamelModel):
    refresh_token: str


class StreamTokenResponse(CamelModel):
    stream_token: str
    expires_in: int


class UserOut(CamelModel):
    id: uuid.UUID
    username: str
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    last_login_at: datetime | None = None
    permissions: list[str] = []


class UserCreate(CamelModel):
    username: str = Field(..., min_length=3, max_length=64)
    email: str = Field(..., max_length=255)
    full_name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=12, max_length=72)
    role: UserRole


class UserUpdate(CamelModel):
    full_name: str | None = None
    email: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(None, min_length=12, max_length=72)


# --- Service locations --------------------------------------------------


class ServiceLocationCreate(CamelModel):
    service_code: str = Field(..., min_length=1, max_length=64)
    location_name: str = Field(..., min_length=1, max_length=255)
    address: str | None = Field(None, max_length=2000)
    area: str | None = Field(None, max_length=128)
    city: str = "Chennai"
    pincode: str | None = Field(None, max_length=16)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    service_area: str | None = Field(None, max_length=128)
    route_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    status: LocationStatus = LocationStatus.ACTIVE


class ServiceLocationUpdate(CamelModel):
    location_name: str | None = None
    address: str | None = None
    area: str | None = None
    pincode: str | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    service_area: str | None = None
    route_id: uuid.UUID | None = None
    status: LocationStatus | None = None


class ServiceLocationOut(CamelModel):
    id: uuid.UUID
    service_code: str
    location_name: str
    address: str | None = None
    area: str | None = None
    city: str | None = None
    pincode: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    service_area: str | None = None
    route_id: uuid.UUID | None = None
    status: LocationStatus
    coordinate_source: CoordinateSource
    created_at: datetime
    updated_at: datetime


class NearbyServiceLocation(CamelModel):
    id: uuid.UUID
    service_code: str
    location_name: str
    latitude: float
    longitude: float
    service_area: str | None = None
    straight_line_meters: int


class NearbyResponse(CamelModel):
    """Candidate search results.

    The two flags below are not decoration. This endpoint returns straight-line
    distances, and the brief is emphatic that straight-line distance must never
    be the serviceability decision. Any consumer reading this payload is told
    so in the payload itself.
    """

    distance_type: str = "STRAIGHT_LINE"
    is_serviceability_decision: bool = False
    note: str = (
        "Straight-line distances for candidate selection only. "
        "Serviceability is decided from actual road distance by "
        "POST /customers/{id}/serviceability-check."
    )
    radius_meters: int
    items: list[NearbyServiceLocation]


# --- Warehouses and routes ---------------------------------------------


class WarehouseCreate(CamelModel):
    warehouse_code: str = Field(..., max_length=64)
    warehouse_name: str = Field(..., max_length=255)
    address: str | None = None
    city: str = "Chennai"
    pincode: str | None = None
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    status: EntityStatus = EntityStatus.ACTIVE


class WarehouseUpdate(CamelModel):
    warehouse_name: str | None = Field(None, max_length=255)
    address: str | None = None
    city: str | None = None
    pincode: str | None = None
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    status: EntityStatus | None = None


class WarehouseOut(CamelModel):
    id: uuid.UUID
    warehouse_code: str
    warehouse_name: str
    address: str | None = None
    city: str | None = None
    pincode: str | None = None
    latitude: float
    longitude: float
    status: EntityStatus


class RouteCreate(CamelModel):
    route_code: str = Field(..., max_length=64)
    route_name: str = Field(..., max_length=255)
    service_area: str | None = None
    status: EntityStatus = EntityStatus.ACTIVE


class RouteOut(CamelModel):
    id: uuid.UUID
    route_code: str
    route_name: str
    service_area: str | None = None
    status: EntityStatus


# --- Checks / audit -----------------------------------------------------


class ServiceabilityCheckOut(CamelModel):
    id: uuid.UUID
    customer_id: uuid.UUID | None = None
    customer_code: str | None = None
    customer_name: str | None = None
    nearest_service_location_id: uuid.UUID | None = None
    nearest_service_code: str | None = None
    customer_latitude: float | None = None
    customer_longitude: float | None = None
    calculated_distance_meters: int | None = None
    distance_km: float | None = None
    distance_type: str
    routing_provider: str | None = None
    route_duration_seconds: int | None = None
    threshold_meters: int
    result: CheckResult
    candidate_count: int
    cache_hit: bool
    degraded: bool
    error_code: str | None = None
    reason: str | None = None
    request_timestamp: datetime
    response_timestamp: datetime | None = None
    duration_ms: int | None = None
    created_by_label: str
    created_at: datetime


# --- Map ----------------------------------------------------------------


class MapCustomer(CamelModel):
    id: uuid.UUID
    customer_code: str
    customer_name: str
    address: str
    latitude: float
    longitude: float
    service_status: ServiceStatus
    nearest_service_location_id: uuid.UUID | None = None
    nearest_service_distance_meters: int | None = None


class MapOperationsResponse(CamelModel):
    threshold_meters: int
    center: dict[str, float]
    zoom: int
    tile_url: str
    tile_attribution: str
    warehouses: list[WarehouseOut]
    service_locations: list[ServiceLocationOut]
    customers: list[MapCustomer]
    generated_at: datetime


# --- Dashboard ----------------------------------------------------------


class AreaCount(CamelModel):
    area: str
    count: int


class DashboardMetrics(CamelModel):
    date: date
    new_customers: int
    service_available: int
    service_not_available: int
    location_verification_required: int
    route_calculation_errors: int
    pending_review: int
    average_distance_meters: int | None = None
    median_distance_meters: int | None = None
    threshold_meters: int
    by_area: list[AreaCount]
    total_checks: int
    cache_hit_rate: float | None = None


# --- Geocoding ----------------------------------------------------------


class GeocodeRequest(CamelModel):
    address: str = Field(..., min_length=3, max_length=2000)


class GeocodeResponse(CamelModel):
    latitude: float
    longitude: float
    formatted_address: str
    confidence: str
    partial_match: bool
    needs_verification: bool
    provider: str


class ReverseGeocodeRequest(CamelModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class AutocompleteItem(CamelModel):
    description: str
    place_id: str
    main_text: str = ""
    secondary_text: str = ""


# --- Admin config -------------------------------------------------------


class ConfigOut(CamelModel):
    key: str
    value: str
    value_type: ConfigValueType
    description: str | None = None
    requires_admin: bool
    min_value: str | None = None
    max_value: str | None = None
    updated_at: datetime


class ConfigUpdate(CamelModel):
    value: str = Field(..., max_length=2000)
    reason: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Why this change is being made. Recorded in the audit trail.",
    )


class ConfigAuditOut(CamelModel):
    id: uuid.UUID
    config_key: str
    old_value: str | None = None
    new_value: str
    changed_by_label: str
    reason: str
    created_at: datetime


class RoutingUsageOut(CamelModel):
    provider: str
    healthy: bool
    cache: dict
    note: str = (
        "Per-operation call counts and latency are exposed on /metrics for "
        "Prometheus; this endpoint gives an at-a-glance view."
    )
