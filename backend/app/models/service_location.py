from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import CoordinateSource, LocationStatus
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import geography_point


class ServiceLocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An existing customer/service point that defines our coverage network.

    A new customer is serviceable if the road distance to the nearest ACTIVE row
    in this table is within the configured threshold.
    """

    __tablename__ = "service_locations"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_service_locations_lat"),
        CheckConstraint(
            "longitude BETWEEN -180 AND 180", name="ck_service_locations_lng"
        ),
        CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)",
            name="ck_service_locations_latlng_pair",
        ),
        Index("ix_service_locations_area", "service_area"),
        Index("ix_service_locations_status", "status"),
    )

    service_code: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("warehouses.id", ondelete="SET NULL")
    )
    route_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("routes.id", ondelete="SET NULL"), index=True
    )
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("import_batches.id", ondelete="SET NULL")
    )

    location_name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    area: Mapped[str | None] = mapped_column(String(128))
    city: Mapped[str | None] = mapped_column(String(128), default="Chennai")
    pincode: Mapped[str | None] = mapped_column(String(16))

    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    location = mapped_column(geography_point(), nullable=True)

    service_area: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[LocationStatus] = mapped_column(
        Enum(LocationStatus, name="location_status", native_enum=False, length=32),
        nullable=False,
        default=LocationStatus.ACTIVE,
    )
    coordinate_source: Mapped[CoordinateSource] = mapped_column(
        Enum(CoordinateSource, name="coordinate_source", native_enum=False, length=32),
        nullable=False,
        default=CoordinateSource.PROVIDED,
    )

    route = relationship("Route", lazy="joined")

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None
