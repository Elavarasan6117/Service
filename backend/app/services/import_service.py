"""Existing service-location import (brief §22, §23).

Two phases, always:

  1. VALIDATE — parse, normalise, geocode missing coordinates, detect
     duplicates, and produce a report. Nothing is written to
     ``service_locations``.
  2. COMMIT — create rows for the VALID entries only, after a human has seen
     the report.

The separation is the point. "Do not import invalid records silently" means the
operator must be shown what would be rejected *before* anything lands in the
table that defines service coverage.
"""

from __future__ import annotations

import io
import uuid
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import (
    CoordinateSource,
    ImportBatchStatus,
    ImportRowStatus,
    LocationStatus,
)
from app.core.errors import GeocodingError, ValidationError
from app.core.logging import get_logger
from app.core.metrics import import_rows_total
from app.models.imports import ImportBatch, ImportRow
from app.models.route import Route
from app.models.service_location import ServiceLocation
from app.models.user import User

logger = get_logger(__name__)

# Accepted spellings for each field. Real CRM and ERP exports rarely match a
# spec exactly -- "Customer ID", "customer_id" and "CustomerID" all turn up --
# so the importer normalises the header rather than rejecting the file.
#
# Aliases are written in underscore form; `_canonical_header` reduces any
# incoming header to that form before matching, and also tries the
# underscore-free spelling. Order matters within a canonical group only in that
# every alias must be unambiguous across groups.
COLUMN_ALIASES: dict[str, set[str]] = {
    "service_code": {
        "service_code", "servicecode", "customer_id", "customerid",
        "customer_code", "customercode", "code", "id",
    },
    "location_name": {
        "location_name", "locationname", "customer_name", "customername",
        "name", "business_name", "shop_name",
    },
    "address": {"address", "full_address", "addr", "street_address", "location"},
    "area": {"area", "locality", "zone", "neighbourhood", "neighborhood"},
    "city": {"city", "town"},
    "pincode": {"pincode", "pin", "pin_code", "postal_code", "zip", "zipcode", "postcode"},
    "latitude": {"latitude", "lat", "y", "y_coord", "gps_lat"},
    "longitude": {"longitude", "lng", "lon", "long", "x", "x_coord", "gps_lng"},
    "service_area": {"service_area", "servicearea", "region", "territory"},
    "route": {"route", "route_code", "routecode", "route_name", "beat"},
    "status": {"status", "state", "active", "is_active", "record_status"},
}

REQUIRED_FIELDS = ("service_code", "location_name")


def _canonical_header(raw: object) -> str:
    """Reduce a spreadsheet header to a comparable token.

    'Customer ID', 'customer-id', ' CUSTOMER_ID ' all become 'customer_id'.
    """
    text = str(raw).strip().lower()
    for separator in ("-", " ", ".", "/"):
        text = text.replace(separator, "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapping: dict[str, str] = {}
    claimed: set[str] = set()
    for column in df.columns:
        key = _canonical_header(column)
        compact = key.replace("_", "")
        for canonical, aliases in COLUMN_ALIASES.items():
            if canonical in claimed:
                continue
            if key in aliases or compact in {a.replace("_", "") for a in aliases}:
                mapping[column] = canonical
                claimed.add(canonical)
                break
    return df.rename(columns=mapping)


def _clean(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _parse_coordinate(value: Any) -> Decimal | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


class ImportService:
    def __init__(self, session: AsyncSession, geocoding_provider) -> None:
        self.session = session
        self.geocoding = geocoding_provider

    # ------------------------------------------------------------------
    # Phase 1 — validate
    # ------------------------------------------------------------------

    async def validate_file(
        self, content: bytes, filename: str, *, actor: User
    ) -> ImportBatch:
        source_type = "XLSX" if filename.lower().endswith((".xlsx", ".xls")) else "CSV"

        try:
            if source_type == "XLSX":
                df = pd.read_excel(io.BytesIO(content), dtype=str)
            else:
                df = pd.read_csv(io.BytesIO(content), dtype=str)
        except Exception as exc:  # noqa: BLE001
            raise ValidationError(f"Could not read '{filename}': {exc}") from exc

        if df.empty:
            raise ValidationError("The uploaded file contains no data rows.")
        if len(df) > settings.IMPORT_MAX_ROWS:
            raise ValidationError(
                f"The file has {len(df):,} rows; the limit is "
                f"{settings.IMPORT_MAX_ROWS:,}. Split it into smaller files."
            )

        df = _normalise_columns(df)

        missing_required = [f for f in REQUIRED_FIELDS if f not in df.columns]
        if missing_required:
            raise ValidationError(
                f"The file is missing required column(s): {', '.join(missing_required)}. "
                f"Recognised headers include: {', '.join(sorted(COLUMN_ALIASES))}."
            )

        batch = ImportBatch(
            filename=filename,
            source_type=source_type,
            total_records=len(df),
            status=ImportBatchStatus.VALIDATING,
            created_by=actor.id,
            created_by_label=actor.label,
        )
        self.session.add(batch)
        await self.session.flush()

        existing_codes = set(
            (await self.session.execute(select(ServiceLocation.service_code)))
            .scalars()
            .all()
        )
        seen_in_file: set[str] = set()

        error_counter: Counter[str] = Counter()
        warning_counter: Counter[str] = Counter()
        sample_errors: list[dict] = []
        counts = Counter()

        for index, raw_row in df.iterrows():
            row_number = int(index) + 2  # +1 for zero-index, +1 for the header row
            record = {
                key: _clean(raw_row.get(key))
                for key in COLUMN_ALIASES
                if key in df.columns
            }

            errors: list[dict] = []
            warnings: list[dict] = []

            service_code = record.get("service_code")
            if not service_code:
                errors.append({"field": "service_code", "issue": "missing"})
            if not record.get("location_name"):
                errors.append({"field": "location_name", "issue": "missing"})

            status_value = ImportRowStatus.VALID
            latitude = _parse_coordinate(raw_row.get("latitude"))
            longitude = _parse_coordinate(raw_row.get("longitude"))
            coordinate_source: str | None = None

            # Duplicates
            if service_code:
                if service_code in existing_codes:
                    errors.append(
                        {"field": "service_code", "issue": "already_exists_in_database"}
                    )
                    status_value = ImportRowStatus.DUPLICATE
                elif service_code in seen_in_file:
                    errors.append(
                        {"field": "service_code", "issue": "duplicated_within_file"}
                    )
                    status_value = ImportRowStatus.DUPLICATE
                else:
                    seen_in_file.add(service_code)

            # Coordinates
            if latitude is not None and longitude is not None:
                coordinate_source = CoordinateSource.IMPORTED.value
                if not (-90 <= float(latitude) <= 90):
                    errors.append({"field": "latitude", "issue": "out_of_range"})
                if not (-180 <= float(longitude) <= 180):
                    errors.append({"field": "longitude", "issue": "out_of_range"})
            elif latitude is not None or longitude is not None:
                errors.append(
                    {"field": "latitude/longitude", "issue": "only_one_coordinate_supplied"}
                )
            else:
                # Missing coordinates — geocode from the address.
                address = record.get("address")
                if not address:
                    errors.append(
                        {
                            "field": "address",
                            "issue": "missing_and_no_coordinates",
                        }
                    )
                    status_value = ImportRowStatus.INVALID
                else:
                    query = ", ".join(
                        p
                        for p in (
                            address,
                            record.get("area"),
                            record.get("city") or "Chennai",
                            record.get("pincode"),
                            "Tamil Nadu, India",
                        )
                        if p
                    )
                    try:
                        result = await self.geocoding.geocode(query)
                        latitude = Decimal(str(result.latitude))
                        longitude = Decimal(str(result.longitude))
                        coordinate_source = CoordinateSource.GEOCODED.value
                        counts["geocoded"] += 1
                        if result.needs_verification:
                            warnings.append(
                                {
                                    "field": "address",
                                    "issue": "low_confidence_geocode",
                                    "detail": (
                                        "Confirm this location on the map before "
                                        "relying on it for coverage."
                                    ),
                                }
                            )
                    except GeocodingError as exc:
                        errors.append(
                            {
                                "field": "address",
                                "issue": "geocoding_failed",
                                "detail": exc.code,
                            }
                        )
                        if status_value == ImportRowStatus.VALID:
                            status_value = ImportRowStatus.GEOCODE_FAILED
                        counts["geocode_failed"] += 1

            # Soft geographic sanity check — a warning, not a rejection, because
            # expansion beyond Chennai is an explicit future requirement.
            # Skipped when the coordinates are already invalid: telling an
            # operator that latitude 999 is "outside Chennai" is noise on top of
            # the real error, and would double-count the row in the report.
            coordinates_are_sane = (
                latitude is not None
                and longitude is not None
                and -90 <= float(latitude) <= 90
                and -180 <= float(longitude) <= 180
            )
            if (
                settings.IMPORT_BBOX_ENABLED
                and coordinates_are_sane
                and not (
                    settings.IMPORT_BBOX_MIN_LAT <= float(latitude) <= settings.IMPORT_BBOX_MAX_LAT
                    and settings.IMPORT_BBOX_MIN_LNG <= float(longitude) <= settings.IMPORT_BBOX_MAX_LNG
                )
            ):
                warnings.append(
                    {
                        "field": "latitude/longitude",
                        "issue": "outside_expected_chennai_area",
                        "detail": f"{latitude},{longitude}",
                    }
                )
                counts["out_of_bounds"] += 1

            if errors and status_value == ImportRowStatus.VALID:
                status_value = ImportRowStatus.INVALID
            if status_value == ImportRowStatus.VALID and (
                latitude is None or longitude is None
            ):
                errors.append(
                    {"field": "latitude/longitude", "issue": "could_not_be_resolved"}
                )
                status_value = ImportRowStatus.INVALID

            for err in errors:
                error_counter[f"{err['field']}:{err['issue']}"] += 1
                if len(sample_errors) < 25:
                    sample_errors.append({"row": row_number, **err})
            for warn in warnings:
                warning_counter[f"{warn['field']}:{warn['issue']}"] += 1

            counts[status_value.value] += 1
            import_rows_total.labels(status=status_value.value).inc()

            self.session.add(
                ImportRow(
                    batch_id=batch.id,
                    row_number=row_number,
                    raw_data={k: v for k, v in record.items() if v is not None},
                    status=status_value,
                    errors=errors or None,
                    warnings=warnings or None,
                    resolved_latitude=latitude,
                    resolved_longitude=longitude,
                    coordinate_source=coordinate_source,
                )
            )

        batch.valid_records = counts[ImportRowStatus.VALID.value]
        batch.invalid_records = counts[ImportRowStatus.INVALID.value]
        batch.duplicate_records = counts[ImportRowStatus.DUPLICATE.value]
        batch.geocode_failed_records = counts[ImportRowStatus.GEOCODE_FAILED.value]
        batch.geocoded_records = counts["geocoded"]
        batch.status = ImportBatchStatus.VALIDATED
        batch.report = {
            "total_records": batch.total_records,
            "valid_records": batch.valid_records,
            "invalid_records": batch.invalid_records,
            "duplicate_records": batch.duplicate_records,
            "geocoded_records": batch.geocoded_records,
            "geocode_failed_records": batch.geocode_failed_records,
            "missing_coordinates": counts["geocoded"] + counts["geocode_failed"],
            "invalid_addresses": counts["geocode_failed"],
            "out_of_bounds": counts["out_of_bounds"],
            "error_summary": dict(error_counter),
            "warnings_summary": dict(warning_counter),
            "sample_errors": sample_errors,
        }
        await self.session.flush()

        logger.info(
            "import_validated",
            batch_id=str(batch.id),
            filename=filename,
            total=batch.total_records,
            valid=batch.valid_records,
            invalid=batch.invalid_records,
            duplicates=batch.duplicate_records,
        )
        return batch

    # ------------------------------------------------------------------
    # Phase 2 — commit
    # ------------------------------------------------------------------

    async def commit_batch(self, batch_id: uuid.UUID, *, actor: User) -> tuple[int, int]:
        batch = await self.session.get(ImportBatch, batch_id)
        if batch is None:
            raise ValidationError(f"No import batch with id {batch_id}.")
        if batch.status is ImportBatchStatus.COMMITTED:
            raise ValidationError("This batch has already been committed.")
        if batch.status is not ImportBatchStatus.VALIDATED:
            raise ValidationError(
                f"Batch is {batch.status.value}; only a VALIDATED batch can be committed."
            )

        rows = (
            (
                await self.session.execute(
                    select(ImportRow).where(
                        ImportRow.batch_id == batch_id,
                        ImportRow.status == ImportRowStatus.VALID,
                    )
                )
            )
            .scalars()
            .all()
        )

        route_cache: dict[str, uuid.UUID] = {}
        committed = 0

        for row in rows:
            data = row.raw_data or {}
            route_id = None
            route_code = data.get("route")
            if route_code:
                if route_code not in route_cache:
                    existing = await self.session.execute(
                        select(Route).where(Route.route_code == route_code)
                    )
                    route = existing.scalar_one_or_none()
                    if route is None:
                        # Auto-create referenced routes: rejecting an otherwise
                        # valid location because its route is not set up yet
                        # would block a migration for a bookkeeping reason.
                        route = Route(
                            route_code=route_code,
                            route_name=route_code,
                            service_area=data.get("service_area"),
                        )
                        self.session.add(route)
                        await self.session.flush()
                    route_cache[route_code] = route.id
                route_id = route_cache[route_code]

            raw_status = (data.get("status") or "ACTIVE").strip().upper()
            location_status = (
                LocationStatus.ACTIVE
                if raw_status in ("ACTIVE", "A", "YES", "TRUE", "1", "GREEN")
                else LocationStatus.INACTIVE
            )

            location = ServiceLocation(
                service_code=data["service_code"],
                location_name=data["location_name"],
                address=data.get("address"),
                area=data.get("area"),
                city=data.get("city") or "Chennai",
                pincode=data.get("pincode"),
                latitude=row.resolved_latitude,
                longitude=row.resolved_longitude,
                service_area=data.get("service_area"),
                route_id=route_id,
                status=location_status,
                coordinate_source=CoordinateSource(
                    row.coordinate_source or CoordinateSource.IMPORTED.value
                ),
                import_batch_id=batch.id,
            )
            self.session.add(location)
            await self.session.flush()

            row.status = ImportRowStatus.COMMITTED
            row.created_service_location_id = location.id
            committed += 1

        batch.status = ImportBatchStatus.COMMITTED
        batch.committed_records = committed
        batch.completed_at = datetime.now(timezone.utc)
        await self.session.flush()

        skipped = batch.total_records - committed
        logger.info(
            "import_committed",
            batch_id=str(batch.id),
            committed=committed,
            skipped=skipped,
            actor=actor.username,
        )
        return committed, skipped
