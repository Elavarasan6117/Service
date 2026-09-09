"""Business reports and exports (brief §31)."""

from __future__ import annotations

import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession, require
from app.core.enums import CheckResult, Permission
from app.models.serviceability_check import ServiceabilityCheck
from app.models.user import User

router = APIRouter(prefix="/reports", tags=["reports"])


async def _fetch_rows(
    session,
    date_from: datetime | None,
    date_to: datetime | None,
    result_filter: CheckResult | None,
    limit: int,
) -> list[dict]:
    conditions = []
    if date_from:
        conditions.append(ServiceabilityCheck.created_at >= date_from)
    if date_to:
        conditions.append(ServiceabilityCheck.created_at <= date_to)
    if result_filter:
        conditions.append(ServiceabilityCheck.result == result_filter)

    query = (
        select(ServiceabilityCheck)
        .options(
            selectinload(ServiceabilityCheck.customer),
            selectinload(ServiceabilityCheck.nearest_service_location),
        )
        .where(*conditions)
        .order_by(ServiceabilityCheck.created_at.desc())
        .limit(limit)
    )
    checks = (await session.execute(query)).scalars().all()

    rows = []
    for check in checks:
        customer = check.customer
        nearest = check.nearest_service_location
        rows.append(
            {
                "Check ID": str(check.id),
                "Date/Time (UTC)": check.created_at.isoformat(),
                "Customer Code": customer.customer_code if customer else "",
                "Customer Name": customer.customer_name if customer else "(preview)",
                "Customer Area": customer.area if customer else "",
                "Customer Latitude": float(check.customer_latitude)
                if check.customer_latitude
                else None,
                "Customer Longitude": float(check.customer_longitude)
                if check.customer_longitude
                else None,
                "Nearest Service Code": nearest.service_code if nearest else "",
                "Nearest Service Name": nearest.location_name if nearest else "",
                "Service Area": nearest.service_area if nearest else "",
                "Route": nearest.route.route_code
                if nearest and nearest.route
                else "",
                "Road Distance (m)": check.calculated_distance_meters,
                "Road Distance (km)": check.distance_km,
                "Distance Type": str(check.distance_type),
                "Threshold (m)": check.threshold_meters,
                "Margin (m)": check.margin_meters,
                "Result": check.result.value,
                "Routing Provider": check.routing_provider,
                "Travel Time (s)": check.route_duration_seconds,
                "Candidates Evaluated": check.candidate_count,
                "From Cache": check.cache_hit,
                "Error Code": check.error_code or "",
                "Initiated By": check.created_by_label,
            }
        )
    return rows


@router.get("/serviceability")
async def serviceability_report(
    session: DbSession,
    user: User = Depends(require(Permission.REPORT_EXPORT)),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
    result_filter: CheckResult | None = Query(None, alias="result"),
    limit: int = Query(1000, ge=1, le=50_000),
) -> dict:
    rows = await _fetch_rows(session, date_from, date_to, result_filter, limit)
    return {"count": len(rows), "rows": rows}


@router.get("/serviceability/export")
async def export_report(
    session: DbSession,
    user: User = Depends(require(Permission.REPORT_EXPORT)),
    fmt: str = Query("csv", alias="format", pattern="^(csv|xlsx|pdf)$"),
    date_from: datetime | None = Query(None, alias="from"),
    date_to: datetime | None = Query(None, alias="to"),
    result_filter: CheckResult | None = Query(None, alias="result"),
    limit: int = Query(10_000, ge=1, le=50_000),
) -> StreamingResponse:
    import pandas as pd

    rows = await _fetch_rows(session, date_from, date_to, result_filter, limit)
    df = pd.DataFrame(rows)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M")
    buffer = io.BytesIO()

    if fmt == "csv":
        buffer.write(df.to_csv(index=False).encode("utf-8"))
        media = "text/csv"
        ext = "csv"
    elif fmt == "xlsx":
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Serviceability")
            worksheet = writer.sheets["Serviceability"]
            for idx, column in enumerate(df.columns, start=1):
                width = max(len(str(column)), 12)
                worksheet.column_dimensions[
                    worksheet.cell(row=1, column=idx).column_letter
                ].width = min(width + 4, 40)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ext = "xlsx"
    else:
        buffer = _build_pdf(rows)
        media = "application/pdf"
        ext = "pdf"

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type=media,
        headers={
            "Content-Disposition": (
                f'attachment; filename="serviceability_report_{stamp}.{ext}"'
            )
        },
    )


def _build_pdf(rows: list[dict]) -> io.BytesIO:
    """Landscape A4 summary table.

    A PDF cannot usefully carry 20 columns, so it shows the columns an operations
    manager reviews; the CSV/XLSX exports carry the full record.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title="Serviceability Report",
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Chennai Serviceability Report", styles["Title"]),
        Paragraph(
            f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} · "
            f"{len(rows)} record(s) · decisions based on actual road distance",
            styles["Normal"],
        ),
        Spacer(1, 8 * mm),
    ]

    columns = [
        "Date/Time (UTC)",
        "Customer Code",
        "Customer Name",
        "Nearest Service Code",
        "Road Distance (km)",
        "Threshold (m)",
        "Result",
    ]
    data = [columns]
    for row in rows[:600]:  # keep the document a sane size
        data.append([str(row.get(c, "") or "") for c in columns])

    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
            ]
        )
    )
    story.append(table)
    if len(rows) > 600:
        story.append(Spacer(1, 5 * mm))
        story.append(
            Paragraph(
                f"Showing the 600 most recent of {len(rows)} records. "
                "Export to CSV or Excel for the complete set.",
                styles["Italic"],
            )
        )
    doc.build(story)
    return buffer
