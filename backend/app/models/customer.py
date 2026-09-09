from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    Uuid,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import CoordinateSource, ServiceStatus
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import geography_point


class Customer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_customers_lat"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_customers_lng"),
        CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)", name="ck_customers_latlng_pair"
        ),
        CheckConstraint(
            "nearest_service_distance_meters IS NULL "
            "OR nearest_service_distance_meters >= 0",
            name="ck_customers_distance_non_negative",
        ),
        Index("ix_customers_service_status", "service_status"),
        Index("ix_customers_area", "area"),
        Index("ix_customers_created_at", "created_at"),
    )

    customer_code: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    customer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str] = mapped_column(Text, nullable=False)
    formatted_address: Mapped[str | None] = mapped_column(Text)
    area: Mapped[str | None] = mapped_column(String(128))
    city: Mapped[str] = mapped_column(String(128), nullable=False, default="Chennai")
    pincode: Mapped[str | None] = mapped_column(String(16))
    service_type: Mapped[str | None] = mapped_column(String(64))

    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    location = mapped_column(geography_point(), nullable=True)
    coordinate_source: Mapped[CoordinateSource | None] = mapped_column(
        Enum(CoordinateSource, name="coordinate_source", native_enum=False, length=32)
    )

    service_status: Mapped[ServiceStatus] = mapped_column(
        Enum(ServiceStatus, name="service_status", native_enum=False, length=48),
        nullable=False,
        default=ServiceStatus.PENDING,
    )
    # Denormalised summary of the latest check. The authoritative record is
    # always the serviceability_checks row referenced by last_check_id; these
    # columns exist so list views and the map do not need a correlated subquery.
    nearest_service_location_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("service_locations.id", ondelete="SET NULL")
    )
    nearest_service_distance_meters: Mapped[int | None] = mapped_column(Integer)
    last_check_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    nearest_service_location = relationship(
        "ServiceLocation", foreign_keys=[nearest_service_location_id], lazy="joined"
    )

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None
