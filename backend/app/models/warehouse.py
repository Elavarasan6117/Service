from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CheckConstraint, Enum, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import EntityStatus
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import geography_point


class Warehouse(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "warehouses"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_warehouses_lat"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_warehouses_lng"),
    )

    warehouse_code: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    warehouse_name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(128))
    pincode: Mapped[str | None] = mapped_column(String(16))

    # Numeric(10, 7) gives ~1 cm precision and avoids binary-float drift in a
    # value that appears in audit records and reports.
    latitude: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(10, 7), nullable=False)
    # Maintained by a BEFORE INSERT/UPDATE trigger -- see migration 0001.
    location = mapped_column(geography_point(), nullable=True)

    status: Mapped[EntityStatus] = mapped_column(
        Enum(EntityStatus, name="entity_status", native_enum=False, length=32),
        nullable=False,
        default=EntityStatus.ACTIVE,
    )
