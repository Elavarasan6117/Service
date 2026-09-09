from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.core.enums import ImportBatchStatus, ImportRowStatus
from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

# JSONB on Postgres, plain JSON on SQLite (test suite).
JSONType = JSONB().with_variant(JSON(), "sqlite")


class ImportBatch(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One uploaded file.

    Validation and commit are separate steps on purpose: an operator sees the
    full validation report and decides, rather than discovering afterwards that
    200 rows were silently rejected (brief §22).
    """

    __tablename__ = "import_batches"

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    total_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    geocoded_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    geocode_failed_records: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    committed_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[ImportBatchStatus] = mapped_column(
        Enum(ImportBatchStatus, name="import_batch_status", native_enum=False, length=32),
        nullable=False,
        default=ImportBatchStatus.PENDING,
    )
    report = mapped_column(JSONType, nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_label: Mapped[str] = mapped_column(String(255), nullable=False, default="SYSTEM")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rows = relationship(
        "ImportRow", back_populates="batch", cascade="all, delete-orphan", lazy="noload"
    )


class ImportRow(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "import_rows"

    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_data = mapped_column(JSONType, nullable=False)
    status: Mapped[ImportRowStatus] = mapped_column(
        Enum(ImportRowStatus, name="import_row_status", native_enum=False, length=32),
        nullable=False,
    )
    errors = mapped_column(JSONType, nullable=True)
    warnings = mapped_column(JSONType, nullable=True)
    resolved_latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    resolved_longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7))
    coordinate_source: Mapped[str | None] = mapped_column(String(32))
    created_service_location_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("service_locations.id", ondelete="SET NULL")
    )

    batch = relationship("ImportBatch", back_populates="rows")
