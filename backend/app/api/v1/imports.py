from __future__ import annotations

import io
import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import DbSession, require
from app.core.enums import ImportRowStatus, Permission
from app.core.errors import NotFoundError, ValidationError
from app.models.imports import ImportBatch, ImportRow
from app.providers.registry import get_geocoding_provider
from app.schemas.imports import ImportBatchOut, ImportCommitResponse, ImportRowOut
from app.services.events import EventType, publish
from app.services.import_service import ImportService
from app.models.user import User

router = APIRouter(prefix="/imports", tags=["imports"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
ALLOWED_SUFFIXES = (".csv", ".xlsx", ".xls")


@router.post("", response_model=ImportBatchOut, status_code=status.HTTP_201_CREATED)
async def upload_import(
    session: DbSession,
    file: UploadFile = File(...),
    user: User = Depends(require(Permission.IMPORT_RUN)),
) -> ImportBatchOut:
    """Upload and VALIDATE an existing-service-location file.

    This never writes to ``service_locations``. It produces a validation report
    for review; ``POST /imports/{id}/commit`` is the separate, deliberate step
    that creates records — and only for rows marked VALID.
    """
    filename = file.filename or "upload"
    if not filename.lower().endswith(ALLOWED_SUFFIXES):
        raise ValidationError(
            f"Unsupported file type. Upload one of: {', '.join(ALLOWED_SUFFIXES)}."
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"File is {len(content) / 1_048_576:.1f} MB; the limit is 20 MB."
        )
    if not content:
        raise ValidationError("The uploaded file is empty.")

    service = ImportService(session, get_geocoding_provider())
    batch = await service.validate_file(content, filename, actor=user)
    await session.commit()
    await session.refresh(batch)
    return ImportBatchOut.model_validate(batch)


@router.get("", response_model=list[ImportBatchOut])
async def list_batches(
    session: DbSession,
    user: User = Depends(require(Permission.IMPORT_RUN)),
    limit: int = Query(50, ge=1, le=200),
) -> list[ImportBatchOut]:
    result = await session.execute(
        select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(limit)
    )
    return [ImportBatchOut.model_validate(b) for b in result.scalars().all()]


@router.get("/{batch_id}", response_model=ImportBatchOut)
async def get_batch(
    batch_id: uuid.UUID,
    session: DbSession,
    user: User = Depends(require(Permission.IMPORT_RUN)),
) -> ImportBatchOut:
    batch = await session.get(ImportBatch, batch_id)
    if batch is None:
        raise NotFoundError(f"No import batch with id {batch_id}.")
    return ImportBatchOut.model_validate(batch)


@router.get("/{batch_id}/rows", response_model=list[ImportRowOut])
async def get_batch_rows(
    batch_id: uuid.UUID,
    session: DbSession,
    user: User = Depends(require(Permission.IMPORT_RUN)),
    status_filter: ImportRowStatus | None = Query(None, alias="status"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> list[ImportRowOut]:
    conditions = [ImportRow.batch_id == batch_id]
    if status_filter:
        conditions.append(ImportRow.status == status_filter)
    result = await session.execute(
        select(ImportRow)
        .where(*conditions)
        .order_by(ImportRow.row_number)
        .offset(offset)
        .limit(limit)
    )
    return [ImportRowOut.model_validate(r) for r in result.scalars().all()]


@router.post("/{batch_id}/commit", response_model=ImportCommitResponse)
async def commit_batch(
    batch_id: uuid.UUID,
    session: DbSession,
    user: User = Depends(require(Permission.IMPORT_RUN)),
) -> ImportCommitResponse:
    service = ImportService(session, get_geocoding_provider())
    committed, skipped = await service.commit_batch(batch_id, actor=user)
    await session.commit()

    await publish(
        EventType.IMPORT_COMPLETED,
        {"batchId": str(batch_id), "committed": committed, "skipped": skipped},
    )
    return ImportCommitResponse(
        batch_id=batch_id,
        committed=committed,
        skipped=skipped,
        message=(
            f"Imported {committed} service location(s). "
            f"{skipped} row(s) were not imported because they were invalid, "
            f"duplicated, or could not be geocoded. "
            f"Review them at GET /imports/{batch_id}/rows?status=INVALID."
        ),
    )


@router.get("/{batch_id}/report")
async def download_report(
    batch_id: uuid.UUID,
    session: DbSession,
    user: User = Depends(require(Permission.IMPORT_RUN)),
    fmt: str = Query("csv", pattern="^(csv|xlsx)$"),
) -> StreamingResponse:
    """Row-by-row validation report, for circulating before committing."""
    import pandas as pd

    batch = await session.get(ImportBatch, batch_id)
    if batch is None:
        raise NotFoundError(f"No import batch with id {batch_id}.")

    rows = (
        (
            await session.execute(
                select(ImportRow)
                .where(ImportRow.batch_id == batch_id)
                .order_by(ImportRow.row_number)
            )
        )
        .scalars()
        .all()
    )

    records = []
    for row in rows:
        data = row.raw_data or {}
        records.append(
            {
                "row_number": row.row_number,
                "status": row.status.value,
                "service_code": data.get("service_code"),
                "location_name": data.get("location_name"),
                "address": data.get("address"),
                "resolved_latitude": float(row.resolved_latitude)
                if row.resolved_latitude
                else None,
                "resolved_longitude": float(row.resolved_longitude)
                if row.resolved_longitude
                else None,
                "coordinate_source": row.coordinate_source,
                "errors": "; ".join(
                    f"{e.get('field')}:{e.get('issue')}" for e in (row.errors or [])
                ),
                "warnings": "; ".join(
                    f"{w.get('field')}:{w.get('issue')}" for w in (row.warnings or [])
                ),
            }
        )

    df = pd.DataFrame(records)
    buffer = io.BytesIO()
    if fmt == "xlsx":
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Validation Report")
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        buffer.write(df.to_csv(index=False).encode("utf-8"))
        media = "text/csv"
    buffer.seek(0)

    stem = batch.filename.rsplit(".", 1)[0]
    return StreamingResponse(
        buffer,
        media_type=media,
        headers={
            "Content-Disposition": (
                f'attachment; filename="import_report_{stem}.{fmt}"'
            )
        },
    )
