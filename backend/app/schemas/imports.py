from __future__ import annotations

import uuid
from datetime import datetime

from app.core.enums import ImportBatchStatus, ImportRowStatus
from app.schemas.common import CamelModel


class ImportRowOut(CamelModel):
    id: uuid.UUID
    row_number: int
    status: ImportRowStatus
    raw_data: dict
    errors: list | None = None
    warnings: list | None = None
    resolved_latitude: float | None = None
    resolved_longitude: float | None = None
    coordinate_source: str | None = None


class ImportValidationReport(CamelModel):
    """Never import invalid records silently (brief §22)."""

    total_records: int
    valid_records: int
    invalid_records: int
    duplicate_records: int
    geocoded_records: int
    geocode_failed_records: int
    missing_coordinates: int
    invalid_addresses: int
    out_of_bounds: int
    error_summary: dict[str, int]
    warnings_summary: dict[str, int]
    sample_errors: list[dict]


class ImportBatchOut(CamelModel):
    id: uuid.UUID
    filename: str
    source_type: str
    status: ImportBatchStatus
    total_records: int
    valid_records: int
    invalid_records: int
    duplicate_records: int
    geocoded_records: int
    geocode_failed_records: int
    committed_records: int
    report: dict | None = None
    created_by_label: str
    created_at: datetime
    completed_at: datetime | None = None


class ImportCommitResponse(CamelModel):
    batch_id: uuid.UUID
    committed: int
    skipped: int
    message: str
