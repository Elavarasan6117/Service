from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import Field, field_validator, model_validator

from app.core.enums import CoordinateSource, ServiceStatus
from app.schemas.common import CamelModel

# International phone and postal-code formats are intentionally permissive.
_PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-()]{6,19}$")
_POSTAL_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\s\-]{1,11}$")


class CustomerCreate(CamelModel):
    customer_code: str | None = Field(None, min_length=1, max_length=64)
    customer_name: str = Field(..., min_length=1, max_length=255)
    phone: str | None = Field(None, max_length=32)
    address: str = Field(..., min_length=5, max_length=2000)
    area: str | None = Field(None, max_length=128)
    city: str = Field("", max_length=128)
    pincode: str | None = Field(None, max_length=16)
    service_type: str | None = Field(None, max_length=64)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)

    @field_validator("customer_code")
    @classmethod
    def _code_charset(cls, v: str | None) -> str | None:
        # Codes end up in exports, URLs and filenames; keep them boring.
        if v is None:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_\-./]+", v):
            raise ValueError(
                "customer_code may contain only letters, digits and - _ . /"
            )
        return v.upper()

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        if v and not _PHONE_RE.fullmatch(v):
            raise ValueError("Phone number format is not recognised.")
        return v

    @field_validator("pincode")
    @classmethod
    def _pincode(cls, v: str | None) -> str | None:
        if v and not _POSTAL_CODE_RE.fullmatch(v):
            raise ValueError("Postal code format is not recognised.")
        return v

    @model_validator(mode="after")
    def _coordinate_pair(self) -> "CustomerCreate":
        # A lone latitude is almost always a copy-paste slip; accepting it would
        # store a half-location that silently never matches anything.
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "Provide both latitude and longitude, or neither "
                "(the address will be geocoded)."
            )
        return self


class CustomerUpdate(CamelModel):
    customer_name: str | None = Field(None, min_length=1, max_length=255)
    phone: str | None = Field(None, max_length=32)
    address: str | None = Field(None, min_length=5, max_length=2000)
    area: str | None = Field(None, max_length=128)
    city: str | None = Field(None, max_length=128)
    pincode: str | None = Field(None, max_length=16)
    service_type: str | None = Field(None, max_length=64)


class CustomerLocationUpdate(CamelModel):
    """Body for the manual marker-drag confirmation."""

    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    reason: str | None = Field(None, max_length=500)


class NearestServiceLocationOut(CamelModel):
    id: uuid.UUID
    service_code: str
    name: str
    latitude: float | None = None
    longitude: float | None = None
    service_area: str | None = None
    distance_meters: int
    distance_km: float


class RouteOut(CamelModel):
    distance_meters: int
    duration_seconds: int | None = None
    geometry: str | None = None
    geometry_format: str | None = None


class ServiceabilityResultOut(CamelModel):
    """The canonical serviceability payload (brief §16)."""

    customer_id: str | None = None
    customer_name: str | None = None
    check_id: uuid.UUID | None = None
    status: ServiceStatus
    threshold_meters: int
    distance_type: str = "ROAD_DISTANCE"
    routing_provider: str | None = None
    nearest_service_location: NearestServiceLocationOut | None = None
    route: RouteOut | None = None
    margin_meters: int | None = None
    candidate_count: int = 0
    cache_hit: bool = False
    degraded: bool = False
    error_code: str | None = None
    message: str | None = None
    reason: str | None = None
    retryable: bool = False
    calculated_at: datetime | None = None
    duration_ms: int | None = None


class CustomerOut(CamelModel):
    id: uuid.UUID
    customer_code: str
    customer_name: str
    phone: str | None = None
    address: str
    formatted_address: str | None = None
    area: str | None = None
    city: str
    pincode: str | None = None
    service_type: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    coordinate_source: CoordinateSource | None = None
    service_status: ServiceStatus
    nearest_service_location_id: uuid.UUID | None = None
    nearest_service_distance_meters: int | None = None
    created_at: datetime
    updated_at: datetime


class CustomerCreateResponse(CamelModel):
    customer: CustomerOut
    serviceability: ServiceabilityResultOut | None = None


class ServiceabilityPreviewRequest(CamelModel):
    """Pre-qualification without creating a customer."""

    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    address: str | None = Field(None, max_length=2000)
    service_type: str | None = None

    @model_validator(mode="after")
    def _need_something(self) -> "ServiceabilityPreviewRequest":
        has_coords = self.latitude is not None and self.longitude is not None
        if not has_coords and not (self.address and self.address.strip()):
            raise ValueError("Provide either coordinates or an address.")
        return self
