from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Uuid,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import CheckResult, DistanceType
from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class ServiceabilityCheck(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable audit record of one serviceability decision.

    APPEND-ONLY. Nothing in the application updates or deletes rows in this
    table -- the rollback runbook and every compliance question depend on the
    history being complete. There is deliberately no ``updated_at`` column.
    """

    __tablename__ = "serviceability_checks"
    __table_args__ = (
        CheckConstraint("threshold_meters > 0", name="ck_checks_threshold_positive"),
        CheckConstraint(
            "calculated_distance_meters IS NULL OR calculated_distance_meters >= 0",
            name="ck_checks_distance_non_negative",
        ),
        # The core invariant of the whole system, enforced by the database:
        # a decision of AVAILABLE or NOT_AVAILABLE can only ever be recorded
        # against a ROAD_DISTANCE measurement.
        CheckConstraint(
            "result NOT IN ('AVAILABLE', 'NOT_AVAILABLE') "
            "OR (distance_type = 'ROAD_DISTANCE' AND calculated_distance_meters IS NOT NULL)",
            name="ck_checks_decision_requires_road_distance",
        ),
        Index("ix_checks_customer_created", "customer_id", "created_at"),
        Index("ix_checks_result_created", "result", "created_at"),
    )

    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )
    nearest_service_location_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("service_locations.id", ondelete="SET NULL")
    )

    # Snapshot of the coordinates used, so the record stays explainable even
    # after the customer is later moved.
    customer_latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    customer_longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))

    calculated_distance_meters: Mapped[int | None] = mapped_column(Integer)
    distance_type: Mapped[DistanceType] = mapped_column(
        Enum(DistanceType, name="distance_type", native_enum=False, length=32),
        nullable=False,
        default=DistanceType.ROAD_DISTANCE,
    )
    routing_provider: Mapped[str | None] = mapped_column(String(64))
    route_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    route_geometry: Mapped[str | None] = mapped_column(Text)
    route_geometry_format: Mapped[str | None] = mapped_column(String(32))

    # Mandatory: never store a result without the threshold that produced it.
    threshold_meters: Mapped[int] = mapped_column(Integer, nullable=False)

    result: Mapped[CheckResult] = mapped_column(
        Enum(CheckResult, name="check_result", native_enum=False, length=48),
        nullable=False,
        index=True,
    )
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_radius_meters: Mapped[int | None] = mapped_column(Integer)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)

    request_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    response_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Kept alongside the FK so the audit trail survives user deletion.
    created_by_label: Mapped[str] = mapped_column(
        String(255), nullable=False, default="SYSTEM"
    )

    customer = relationship("Customer", lazy="selectin")
    nearest_service_location = relationship("ServiceLocation", lazy="selectin")

    @property
    def distance_km(self) -> float | None:
        if self.calculated_distance_meters is None:
            return None
        return round(self.calculated_distance_meters / 1000.0, 3)

    @property
    def margin_meters(self) -> int | None:
        """Positive = inside the limit, negative = outside."""
        if self.calculated_distance_meters is None:
            return None
        return self.threshold_meters - self.calculated_distance_meters
