"""
Drop-in export routes for PDL Email Intelligence V4.

Provides:
    /export/emails.csv
    /export/events.csv
    /export/report.zip

All endpoints accept:
    start=YYYY-MM-DDTHH:MM
    end=YYYY-MM-DDTHH:MM

The date/time values are interpreted using the same naive local/IST timestamp
format used by the existing application database.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, request, jsonify, abort
from flask_login import login_required, current_user
from sqlalchemy.inspection import inspect as sa_inspect

from db import Email, Event, PDL

export_bp = Blueprint("export", __name__, url_prefix="/export")

IST = ZoneInfo("Asia/Kolkata")
DATE_FORMAT = "%Y-%m-%dT%H:%M"
DISPLAY_FORMAT = "%d-%b-%Y %H:%M:%S IST"


def _parse_datetime(value: str | None, field_name: str):
    if not value:
        return None
    try:
        return datetime.strptime(value, DATE_FORMAT)
    except ValueError:
        raise ValueError(
            f"Invalid {field_name}. Use the date/time selector and try again."
        )


def _timestamp_column(model):
    """Find the timestamp column used by the model."""
    preferred = (
        "received_at",
        "created_at",
        "timestamp",
        "event_time",
        "logged_at",
        "updated_at",
    )
    columns = {column.name: getattr(model, column.name)
               for column in sa_inspect(model).columns}
    for name in preferred:
        if name in columns:
            return columns[name]
    raise RuntimeError(
        f"No supported timestamp column found for {model.__name__}. "
        f"Expected one of: {', '.join(preferred)}"
    )


def _query_model(model, start, end):
    query = model.query
    ts_column = _timestamp_column(model)

    if start is not None:
        query = query.filter(ts_column >= start)
    if end is not None:
        query = query.filter(ts_column <= end)

    return query.order_by(ts_column.asc()).all()


def _format_value(value):
    if value is None:
        return ""

    if isinstance(value, datetime):
        if value.tzinfo is None:
            # Existing application records are displayed as IST.
            value = value.replace(tzinfo=IST)
        else:
            value = value.astimezone(IST)
        return value.strftime(DISPLAY_FORMAT)

    return str(value)


def _csv_bytes(model, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)

    columns = [column.name for column in sa_inspect(model).columns]
    writer.writerow(columns)

    for row in rows:
        writer.writerow([
            _format_value(getattr(row, column, None))
            for column in columns
        ])

    return output.getvalue().encode("utf-8-sig")


def _filename(prefix, start, end, extension):
    def stamp(value, fallback):
        return value.strftime("%Y%m%d_%H%M") if value else fallback

    return (
        f"{prefix}_{stamp(start, 'all')}_to_{stamp(end, 'all')}.{extension}"
    )


def _error(message, status=400):
    if request.accept_mimetypes.best == "application/json":
        return jsonify({"success": False, "error": message}), status
    return Response(message, status=status, mimetype="text/plain")


def _read_range():
    try:
        start = _parse_datetime(request.args.get("start"), "start date/time")
        end = _parse_datetime(request.args.get("end"), "end date/time")
    except ValueError as exc:
        return None, None, _error(str(exc))

    if start and end and end < start:
        return None, None, _error("End date/time must be later than start date/time.")

    return start, end, None


@export_bp.get("/emails.csv")
@login_required
def export_emails():
    start, end, error = _read_range()
    if error:
        return error

    rows = _query_model(Email, start, end)
    payload = _csv_bytes(Email, rows)

    return Response(
        payload,
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                f'attachment; filename="{_filename("emails", start, end, "csv")}"'
        },
    )


@export_bp.get("/events.csv")
@login_required
def export_events():
    start, end, error = _read_range()
    if error:
        return error

    rows = _query_model(Event, start, end)
    payload = _csv_bytes(Event, rows)

    return Response(
        payload,
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                f'attachment; filename="{_filename("events", start, end, "csv")}"'
        },
    )


@export_bp.get("/report.zip")
@login_required
def export_report():
    start, end, error = _read_range()
    if error:
        return error

    email_rows = _query_model(Email, start, end)
    event_rows = _query_model(Event, start, end)

    memory = io.BytesIO()
    with zipfile.ZipFile(memory, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("emails.csv", _csv_bytes(Email, email_rows))
        archive.writestr("events.csv", _csv_bytes(Event, event_rows))

    memory.seek(0)

    return Response(
        memory.getvalue(),
        mimetype="application/zip",
        headers={
            "Content-Disposition":
                f'attachment; filename="{_filename("pdl_report", start, end, "zip")}"'
        },
    )


@export_bp.get("/pdl/<int:pdl_id>.csv")
@login_required
def export_pdl_csv(pdl_id):
    pdl=PDL.query.get_or_404(pdl_id); assigned_ids={item.id for item in getattr(current_user,"pdls",[])}; is_owner=bool(pdl.owner_email and pdl.owner_email.lower()==current_user.email.lower())
    if current_user.role!="admin" and not is_owner and pdl.id not in assigned_ids: abort(403)
    start,end,error=_read_range()
    if error: return error
    query=Email.query.filter(Email.pdl_id==pdl_id); ts=_timestamp_column(Email)
    if start is not None: query=query.filter(ts>=start)
    if end is not None: query=query.filter(ts<=end)
    rows=query.order_by(ts.asc()).all(); payload=_csv_bytes(Email,rows); safe_name="".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in pdl.name)[:80]
    return Response(payload,mimetype="text/csv",headers={"Content-Disposition":f'attachment; filename="{_filename("pdl_"+safe_name,start,end,"csv")}"'})

@export_bp.get("/monitoring-slot/<int:slot_id>.csv")
@login_required
def export_monitoring_slot_csv(slot_id):
    """Export a concise monitoring-slot activity report with PDL-level totals."""
    from db import MonitoringSlot, PDLMonitoringAssignmentHistory
    from app import metric_range_context, monitoring_slot_activity_report

    slot = MonitoringSlot.query.get_or_404(slot_id)
    if current_user.role != "admin":
        from app import allowed_pdl_ids
        if not (set(allowed_pdl_ids()) & set(p.id for p in slot.pdls)):
            abort(403)
    try:
        # Reuse the application's metric-range parser/context so export and UI
        # report exactly the same period.
        range_ctx = metric_range_context()
        totals, pdl_rows = monitoring_slot_activity_report(slot, range_ctx)
    except Exception as exc:
        return _error(f"Unable to build monitoring slot report: {exc}", 500)

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "Monitoring Slot", "Group", "Start Time", "End Time", "PDL",
        "Received", "Attended", "Unattended", "TAT Breached", "Response Rate %"
    ])
    for row in pdl_rows:
        writer.writerow([
            slot.name,
            slot.group.name if slot.group else "",
            slot.start_time.strftime("%H:%M IST"),
            slot.end_time.strftime("%H:%M IST"),
            row["pdl"].name,
            row["received"],
            row["attended"],
            row["unattended"],
            row["tat_breached"],
            row["response_rate"],
        ])
    writer.writerow([])
    writer.writerow(["TOTAL", "", "", "", "", totals["received"], totals["attended"], totals["unattended"], totals["tat_breached"], totals["response_rate"]])

    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in slot.name)[:80]
    payload = output.getvalue().encode("utf-8-sig")
    return Response(
        payload,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="monitoring_slot_{safe_name}.csv"'
        },
    )
