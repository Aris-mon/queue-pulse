from autosync_status_routes import autosync_bp
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta, time as dt_time
from functools import wraps
from threading import Lock
from zoneinfo import ZoneInfo
from export_routes import export_bp
import json
import re
import csv
import io
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from autosync_service import run_reliable_sync

from apscheduler.schedulers.background import BackgroundScheduler

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    abort,
    make_response,
    session,
)
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)

from sqlalchemy import or_, false, func, and_, not_

from config import Config
from db import (
    db,
    init_db,
    User,
    PDL,
    Engineer,
    EngineerGroup,
    TeamGroup,
    MonitoringSlot,
    PDLMonitoringAssignment,
    PDLMonitoringAssignmentHistory,
    MonitoringSlotReportLog,
    ExecutiveGroup,
    Email,
    Event,
    Alert,
    AIAnalysis,
    AIFeedback,
    AIModelLog,
    AITrainingExample,
    log_event,
    get_sync_state,
    set_sync_state,
    get_database_uri,
)
from intelligence import INTERNAL_DOMAINS, EMAIL_CATEGORY_LABELS
from ai.engine import health as ai_health, analyze_email_id
from ai.worker import enqueue as enqueue_ai, stats as ai_worker_stats, start_dispatcher
from ai.configuration import all_settings as ai_settings, MODEL_PRESETS
from ai.operations import daily_briefing, shift_handover, suggested_reply
from ai.training import (
    ollama_cli_status, add_training_example, build_ollama_model, training_examples,
    training_example_count, normalize_training_labels, parse_training_sample_file,
    import_training_samples,
)
from ai.rag import status as rag_status, index_all_training_examples
from network_config import list_adapters, current_network, validate_network_config, apply_network_config
from graph_connector import GraphMailClient, GraphMailboxConfig, graph_config, graph_enabled


# ============================================================
# APP
# ============================================================

app = Flask(__name__)
app.register_blueprint(autosync_bp)
app.config.from_object(Config)

# Configure database BEFORE SQLAlchemy initialization.
app.config["SQLALCHEMY_DATABASE_URI"] = get_database_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.register_blueprint(export_bp)

# SQLite connection timeout.
app.config.setdefault(
    "SQLALCHEMY_ENGINE_OPTIONS",
    {
        "connect_args": {
            # Let SQLite wait for short write contention instead of failing
            # immediately when the Outlook sync is committing.
            "timeout": 30,
            "check_same_thread": False,
        },
        "pool_pre_ping": True,
    },
)

db.init_app(app)


def _configure_sqlite_runtime():
    """Enable SQLite WAL/busy handling for concurrent Flask + sync workloads."""
    try:
        if not str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower().startswith("sqlite:"):
            return
        with app.app_context():
            conn = db.engine.connect()
            try:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL")
                conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
                conn.exec_driver_sql("PRAGMA busy_timeout=30000")
            finally:
                conn.close()
    except Exception as exc:
        app.logger.warning("SQLite runtime tuning could not be applied: %s", exc)


_configure_sqlite_runtime()

# Start one lightweight dispatcher so imported customer mail is automatically
# handed to the AI queue even when the sync request has already returned.
# The worker remains single-threaded for CPU-only Ollama/Qwen3.
try:
    start_dispatcher(app, interval_seconds=5)
except Exception:
    pass


@app.after_request
def add_security_headers(response):
    """Baseline browser hardening without changing existing application flows."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


# ============================================================
# DISPLAY HELPERS
# ============================================================

@app.template_filter("ist")
def format_ist(value, fmt="%d %b %Y %H:%M"):
    """Render stored UTC datetimes in India Standard Time."""
    if not value:
        return "—"
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(IST).strftime(fmt)
    except Exception:
        return str(value)


def as_utc(value):
    """Normalize SQLite/SQLAlchemy datetimes to aware UTC for safe arithmetic."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def email_age_text(received_at, responded=False, now=None):
    """Human-readable age of an unanswered tracked email."""
    if responded or not received_at:
        return "—"
    received = as_utc(received_at)
    if received is None:
        return "—"
    now = as_utc(now) or datetime.now(timezone.utc)
    minutes = max(0, int((now - received).total_seconds() // 60))
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


@app.template_filter("age")
def format_age(value):
    return email_age_text(value, responded=False)


def sync_setting(key, default=None):
    return get_sync_state(key) or default


def metric_range_context(default="week"):
    """Resolve dashboard/PDL KPI date ranges in IST and return UTC bounds.

    Supported values: 24h, week, month and custom. Custom dates are inclusive
    in IST; the end bound is converted to the next midnight and applied as an
    exclusive UTC bound.
    """
    choice = (request.args.get("range") or default).strip().lower()
    if choice not in {"today", "1h", "4h", "8h", "24h", "week", "month", "custom"}:
        choice = default

    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc.astimezone(IST)
    start_utc = None
    end_utc = now_utc
    custom_start = (request.args.get("start_date") or "").strip()
    custom_end = (request.args.get("end_date") or "").strip()

    if choice == "today":
        start_local = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = start_local.astimezone(timezone.utc)
        end_utc = now_utc
        label = "Today (IST)"
    elif choice == "1h":
        start_utc = now_utc - timedelta(hours=1)
        label = "Last 1 hour"
    elif choice == "4h":
        start_utc = now_utc - timedelta(hours=4)
        label = "Last 4 hours"
    elif choice == "8h":
        start_utc = now_utc - timedelta(hours=8)
        label = "Last 8 hours"
    elif choice == "24h":
        start_utc = now_utc - timedelta(hours=24)
        label = "Last 24 hours"
    elif choice == "month":
        start_utc = now_utc - timedelta(days=30)
        label = "Last 30 days"
    elif choice == "custom":
        # Selecting Custom must first open the date fields. The previous
        # implementation immediately fell back to Week when the browser had
        # not supplied dates yet, making it impossible to apply a custom range.
        if not custom_start and not custom_end:
            today = now_ist.date()
            custom_start = today.isoformat()
            custom_end = today.isoformat()
        try:
            start_day = datetime.strptime(custom_start, "%Y-%m-%d").date()
            end_day = datetime.strptime(custom_end, "%Y-%m-%d").date()
            if end_day < start_day:
                raise ValueError("end before start")
            start_local = datetime.combine(start_day, datetime.min.time(), tzinfo=IST)
            end_local = datetime.combine(end_day + timedelta(days=1), datetime.min.time(), tzinfo=IST)
            start_utc = start_local.astimezone(timezone.utc)
            end_utc = min(end_local.astimezone(timezone.utc), now_utc)
            label = f"{start_day.strftime('%d %b %Y')} – {end_day.strftime('%d %b %Y')}"
        except (TypeError, ValueError):
            # Keep the Custom UI visible instead of silently switching to a
            # completely different period. Use today's window as a safe query
            # fallback until the user supplies a valid range.
            today = now_ist.date()
            custom_start = custom_start or today.isoformat()
            custom_end = custom_end or today.isoformat()
            start_local = datetime.combine(today, datetime.min.time(), tzinfo=IST)
            start_utc = start_local.astimezone(timezone.utc)
            end_utc = now_utc
            label = "Custom — invalid date range"
    else:
        start_utc = now_utc - timedelta(days=7)
        label = "Last 7 days"

    return {
        "choice": choice,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "label": label,
        "custom_start": custom_start,
        "custom_end": custom_end,
        "today_ist": now_ist.date().isoformat(),
    }


def apply_metric_range(query, range_ctx):
    """Apply a resolved KPI range to an Email query without changing access rules."""
    return query.filter(
        Email.received_at >= range_ctx["start_utc"],
        Email.received_at <= range_ctx["end_utc"],
    )


@app.context_processor
def inject_ui_settings():
    try:
        interval = int(sync_setting(
            "sync_interval_minutes",
            str(app.config.get("SYNC_INTERVAL_MINUTES", 5)),
        ))
    except (TypeError, ValueError):
        interval = app.config.get("SYNC_INTERVAL_MINUTES", 1)
    public_api_url = get_sync_state("network:api_url")
    if not public_api_url:
        public_api_url = request.host_url.rstrip("/")
    return {
        "auto_sync_enabled": sync_setting("auto_sync_enabled", "1") == "1",
        "sync_interval_minutes": max(1, int(interval)),
        "public_api_url": public_api_url,
    }


# ============================================================
# SYNC LOCK
# ============================================================

SYNC_LOCK = Lock()
IST = ZoneInfo("Asia/Kolkata")
SCHEDULER = None


# ============================================================
# AUTO-SYNC SCHEDULER
# ============================================================

def _slot_end_local(slot, now_local):
    """Return the most recent occurrence of this slot's end time in IST."""
    today = now_local.date()
    end_today = datetime.combine(today, slot.end_time, tzinfo=IST)
    if slot.start_time <= slot.end_time:
        return end_today if now_local >= end_today else end_today - timedelta(days=1)
    # Overnight slot, e.g. 22:00–06:30.
    if now_local.time() < slot.end_time:
        return end_today
    if now_local.time() >= slot.start_time:
        return end_today + timedelta(days=1)
    return end_today


def _slot_report_rows(slot, report_date):
    """Return PDL activity rows for the calendar day on which the slot ended."""
    end_local = datetime.combine(report_date, slot.end_time, tzinfo=IST)
    if slot.start_time <= slot.end_time:
        start_local = datetime.combine(report_date, slot.start_time, tzinfo=IST)
    else:
        start_local = datetime.combine(report_date - timedelta(days=1), slot.start_time, tzinfo=IST)
    start_utc, end_utc = start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
    pdl_ids = _slot_activity_pdl_ids(slot)
    rows = []
    totals = {"received": 0, "attended": 0, "unattended": 0, "tat_breached": 0}
    for pdl_id in pdl_ids:
        pdl = PDL.query.get(pdl_id)
        if not pdl:
            continue
        q = Email.query.filter(
            Email.pdl_id == pdl.id,
            Email.monitoring_excluded.is_(False),
            Email.received_at >= start_utc,
            Email.received_at <= end_utc,
        )
        emails = q.all()
        emails = [e for e in emails if _slot_match_email(e, slot)]
        received = len(emails)
        attended = sum(1 for e in emails if e.responded)
        breached = sum(1 for e in emails if e.tat_breached)
        unattended = sum(1 for e in emails if e.response_required and not e.responded)
        row = {"pdl": pdl, "received": received, "attended": attended, "unattended": unattended, "tat_breached": breached,
               "response_rate": round(attended / received * 100, 1) if received else 0}
        rows.append(row)
        totals["received"] += received; totals["attended"] += attended; totals["unattended"] += unattended; totals["tat_breached"] += breached
    totals["response_rate"] = round(totals["attended"] / totals["received"] * 100, 1) if totals["received"] else 0
    totals["compliance"] = round((totals["received"] - totals["tat_breached"]) / totals["received"] * 100, 1) if totals["received"] else 0
    return start_local, end_local, totals, rows


def _send_monitoring_slot_end_report(slot, engineer, report_date):
    """Send the complete slot report to one engineer through Outlook COM."""
    if not getattr(Config, "MONITORING_ASSIGNMENT_NOTIFY_ENABLED", True) or not engineer or not engineer.email:
        return False, "Notification disabled or engineer email missing"
    start_local, end_local, totals, rows = _slot_report_rows(slot, report_date)
    lines = [
        f"Monitoring slot completed: {slot.name}", "", f"Group: {slot.group.name if slot.group else '—'}",
        f"Slot: {slot.name}", f"Window: {start_local.strftime('%d %b %Y %H:%M')} – {end_local.strftime('%d %b %Y %H:%M')} IST", "",
        "COMPLETE SLOT SUMMARY", f"Emails received: {totals['received']}", f"Attended / responded: {totals['attended']}",
        f"Unattended: {totals['unattended']}", f"TAT breached: {totals['tat_breached']}", f"Response rate: {totals['response_rate']}%",
        f"TAT compliance: {totals['compliance']}%", "", "PDL BREAKDOWN"
    ]
    for row in rows:
        lines.extend([
            f"- {row['pdl'].name}: received {row['received']}, attended {row['attended']}, unattended {row['unattended']}, TAT breached {row['tat_breached']}, response rate {row['response_rate']}%"
        ])
    app_url = getattr(Config, "MONITORING_APP_URL", "").strip().rstrip("/")
    # This function runs from the background scheduler, outside an HTTP request.
    # Never call url_for() here unless a request context is guaranteed.
    activity_path = f"/monitoring-slots/{slot.id}/activities"
    # Prefer an explicit public URL, then the administrator network URL, then a
    # safe local fallback. All three work without an active Flask request.
    base_url = app_url or (get_sync_state("network:api_url") or "").strip().rstrip("/")
    if not base_url:
        base_url = f"http://127.0.0.1:{getattr(Config, 'PORT', 5000)}"
    activity_url = f"{base_url}{activity_path}"
    lines.extend(["", f"Detailed activity report: {activity_url}"])
    com_initialized = False
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
        pythoncom.CoInitialize(); com_initialized = True
        outlook = win32com.client.Dispatch("Outlook.Application")
        message = outlook.CreateItem(0)
        message.To = engineer.email
        message.Subject = f"Monitoring slot report: {slot.name} | {end_local.strftime('%d %b %Y')}"
        message.Body = f"Hello {engineer.name or 'Engineer'},\n\nThe monitoring slot has ended. Below is the complete monitoring report.\n\n" + "\n".join(lines)
        message.Send()
        return True, None
    except Exception as exc:
        return False, str(exc)
    finally:
        if com_initialized:
            try: pythoncom.CoUninitialize()
            except Exception: pass


def run_monitoring_slot_end_reports():
    """Check ended slots every minute and send one report per engineer/day."""
    with app.app_context():
        now_local = datetime.now(timezone.utc).astimezone(IST)
        for slot in MonitoringSlot.query.filter_by(active=True).all():
            end_local = _slot_end_local(slot, now_local)
            if end_local > now_local:
                continue
            report_date = end_local.date()
            histories = PDLMonitoringAssignmentHistory.query.filter_by(slot_id=slot.id).all()
            engineers = {h.engineer_id: h.engineer for h in histories if h.engineer_id and h.engineer}
            for assignment in PDLMonitoringAssignment.query.filter_by(slot_id=slot.id).all():
                if assignment.engineer_id and assignment.engineer:
                    engineers[assignment.engineer_id] = assignment.engineer
            for engineer_id, engineer in engineers.items():
                existing = MonitoringSlotReportLog.query.filter_by(slot_id=slot.id, engineer_id=engineer_id, report_date=report_date).first()
                if existing and existing.status == "sent":
                    continue
                ok, error = _send_monitoring_slot_end_report(slot, engineer, report_date)
                if existing:
                    existing.sent_at = datetime.now(timezone.utc)
                    existing.status = "sent" if ok else "failed"
                    existing.error = None if ok else error
                else:
                    db.session.add(MonitoringSlotReportLog(slot_id=slot.id, engineer_id=engineer_id, report_date=report_date,
                                                           sent_at=datetime.now(timezone.utc), status="sent" if ok else "failed", error=None if ok else error))
                db.session.commit()


# ============================================================
# FIRST-RUN SETUP
# ============================================================

def _setup_required():
    try:
        return User.query.count() == 0
    except Exception:
        return False


def _write_env_values(values):
    """Update selected .env keys without destroying the existing file."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    existing = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            existing = fh.read().splitlines()

    keys = set(values)
    written = set()
    output = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in keys:
            output.append(f"{key}={values[key]}")
            written.add(key)
        else:
            output.append(line)

    for key, value in values.items():
        if key not in written:
            output.append(f"{key}={value}")

    with open(env_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(output).rstrip() + "\n")


@app.route("/setup", methods=["GET", "POST"])
def initial_setup():
    # The setup wizard is available only until the first application user is
    # created. It deliberately does not require login because there is no
    # account yet.
    if not _setup_required():
        return redirect(url_for("login"))

    # A previous unauthenticated request can leave Flask-Login's default
    # "Please sign in to continue." message in the session. The setup wizard
    # does not require authentication, so never display that message here.
    # This also prevents the first-run page from becoming filled with repeated
    # login warnings after a redirect loop or an earlier browser session.
    if request.method == "GET":
        pending = session.get("_flashes", [])
        if pending:
            login_message = "Please sign in to continue."
            cleaned = [
                item for item in pending
                if not (isinstance(item, (list, tuple)) and len(item) >= 2 and item[1] == login_message)
            ]
            if cleaned:
                session["_flashes"] = cleaned
            else:
                session.pop("_flashes", None)

    if request.method == "POST":
        mailbox = request.form.get("mailbox", "").strip()
        graph_tenant_id = request.form.get("graph_tenant_id", "").strip()
        graph_client_id = request.form.get("graph_client_id", "").strip()
        graph_client_secret = request.form.get("graph_client_secret", "")
        admin_name = request.form.get("admin_name", "").strip()
        admin_email = request.form.get("admin_email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        graph_values = {
            "tenant_id": graph_tenant_id,
            "client_id": graph_client_id,
            "client_secret": graph_client_secret,
            "mailbox": mailbox,
        }

        graph_error = None
        if not all(graph_values.values()):
            graph_error = "Enter Tenant ID, Application / Client ID, Client Secret VALUE and mailbox."
        elif "@" not in mailbox:
            graph_error = "Enter a valid dedicated mailbox address."
        else:
            try:
                _validate_graph_values(graph_values)
            except Exception as exc:
                graph_error = f"Microsoft Graph connection failed: {str(exc)[:500]}"

        if graph_error:
            flash(graph_error, "error")
        elif not admin_name:
            flash("Enter the System Administrator name.", "error")
        elif not admin_email or "@" not in admin_email:
            flash("Enter a valid System Administrator email/username.", "error")
        elif len(password) < 8:
            flash("Administrator password must be at least 8 characters.", "error")
        elif password != confirm:
            flash("Administrator password and confirmation do not match.", "error")
        else:
            existing = User.query.count()
            if existing:
                return redirect(url_for("login"))

            admin = User(name=admin_name, email=admin_email, role="admin", active=True)
            admin.set_password(password)
            db.session.add(admin)
            db.session.commit()

            # Persist Graph credentials in the encrypted application
            # configuration. The setup wizard never writes the client secret to
            # .env and never sends it back to the browser.
            GraphMailboxConfig.save(
                tenant_id=graph_tenant_id,
                client_id=graph_client_id,
                client_secret=graph_client_secret,
                mailbox=mailbox,
            )

            _write_env_values({
                "ADMIN_EMAIL": admin_email,
                "ADMIN_NAME": admin_name,
                "OUTLOOK_SYNC_ENABLED": "1",
            })
            os.environ["ADMIN_EMAIL"] = admin_email
            os.environ["ADMIN_NAME"] = admin_name
            os.environ["OUTLOOK_SYNC_ENABLED"] = "1"

            set_sync_state("auto_sync_enabled", "1")
            set_sync_state("sync_interval_minutes", str(app.config.get("SYNC_INTERVAL_MINUTES", 5)))
            set_sync_state("setup_completed", "1")
            log_event("INFO", "initial_setup", "Initial application setup completed.", mailbox)

            flash("Initial setup completed. Sign in with the System Administrator account.", "success")
            return redirect(url_for("login"))

    return render_template("initial_setup.html")


# ============================================================
# LOGIN
# ============================================================

login_manager = LoginManager()

login_manager.login_view = "login"
login_manager.login_message = "Please sign in to continue."

login_manager.init_app(app)


@login_manager.unauthorized_handler
def _handle_unauthorized():
    """Keep first-run setup independent from the authentication flow."""
    if _setup_required():
        # During first-run setup there is intentionally no authenticated user.
        # Redirect directly to /setup instead of adding login flash messages.
        return redirect(url_for("initial_setup"))

    if request.path.startswith("/api/"):
        return jsonify({
            "ok": False,
            "error": "Authentication required.",
        }), 401

    flash(login_manager.login_message or "Please sign in to continue.", "message")
    return redirect(url_for(login_manager.login_view or "login"))


@login_manager.user_loader
def load_user(user_id):

    try:
        return db.session.get(
            User,
            int(user_id),
        )
    except Exception:
        db.session.rollback()
        return None


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(403)
def forbidden(error):
    if request.path.startswith("/api/"):
        return jsonify({
            "ok": False,
            "error": "Access denied.",
        }), 403

    return render_template(
        "error.html",
        code=403,
        message="You do not have permission to access this page.",
    ), 403


@app.errorhandler(404)
def not_found(error):
    if request.path.startswith("/api/"):
        return jsonify({
            "ok": False,
            "error": "Resource not found.",
        }), 404

    return render_template(
        "error.html",
        code=404,
        message="The requested page was not found.",
    ), 404


@app.errorhandler(500)
def internal_error(error):

    try:
        db.session.rollback()
    except Exception:
        pass

    if request.path.startswith("/api/"):
        return jsonify({
            "ok": False,
            "error": "Internal server error.",
        }), 500

    try:
        return render_template(
            "error.html",
            code=500,
            message="An unexpected server error occurred.",
        ), 500
    except Exception:
        # Last-resort response: never let the custom error page hide the original
        # application failure by raising a second template exception.
        return (
            "<!doctype html><html><head><meta charset='utf-8'><title>Internal Server Error</title></head>"
            "<body style='font-family:Arial,sans-serif;padding:40px'>"
            "<h1>Internal Server Error</h1>"
            "<p>An unexpected application error occurred. Please check the application log for details.</p>"
            "</body></html>"
        ), 500


# ============================================================
# ACCESS CONTROL
# ============================================================

def admin_required(view):

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):

        if current_user.role != "admin":
            abort(403)

        return view(*args, **kwargs)

    return wrapped


def manager_or_admin_required(view):
    """Allow administrators and scoped team managers only."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user.role not in {"admin", "manager"}:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def managed_team_ids(user=None):
    user = user or current_user
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    if getattr(user, "role", "") == "admin":
        return {g.id for g in TeamGroup.query.filter_by(active=True).all()}
    if getattr(user, "role", "") != "manager":
        return set()
    return {g.id for g in (getattr(user, "managed_teams", None) or []) if g.active}


def can_manage_team(group):
    return bool(
        current_user.role == "admin"
        or (current_user.role == "manager" and group and group.id in managed_team_ids())
    )


def allowed_pdl_ids():

    if current_user.role == "admin":

        return [
            p.id
            for p in PDL.query
            .filter_by(active=True)
            .all()
        ]

    if current_user.role == "manager":
        team_ids = managed_team_ids()
        if not team_ids:
            return []
        groups = TeamGroup.query.filter(
            TeamGroup.id.in_(team_ids),
            TeamGroup.active.is_(True),
        ).all()
        return sorted({
            p.id
            for group in groups
            for p in (group.pdls or [])
            if p.active
        })

    assigned_ids = {
        p.id
        for p in current_user.pdls
        if p.active
    }
    owned_ids = {
        p.id
        for p in PDL.query.filter(
            PDL.active.is_(True),
            PDL.owner_email == current_user.email.lower(),
        ).all()
    }
    return sorted(assigned_ids | owned_ids)


def _pdl_member_sender_pairs(pdl_ids=None):
    """Return {(pdl_id, engineer_email)} for engineers explicitly authorized on each PDL.

    These addresses are workload-exempt: an engineer's outbound copy to a customer/PDL
    mailbox is evidence for response tracking, never a new customer workload item.
    """
    ids = list(pdl_ids or allowed_pdl_ids() or [])
    pairs = set()
    if not ids:
        return pairs
    try:
        pdls = PDL.query.filter(PDL.id.in_(ids)).all()
        for pdl in pdls:
            for engineer in (getattr(pdl, "authorized_engineers", None) or []):
                email = (getattr(engineer, "email", "") or "").strip().lower()
                if getattr(engineer, "active", True) and email:
                    pairs.add((pdl.id, email))
            # Preserve legacy EngineerGroup / TeamGroup authorization already honored by
            # the Outlook connector. This also cleans up older installations immediately.
            for engineer in Engineer.query.filter_by(active=True).all():
                email = (engineer.email or "").strip().lower()
                if not email:
                    continue
                try:
                    legacy = getattr(engineer, "group", None)
                    if legacy and legacy.active and legacy.pdl_id == pdl.id:
                        pairs.add((pdl.id, email)); continue
                except Exception:
                    pass
                try:
                    if any(getattr(g, "active", True) and pdl in (getattr(g, "pdls", None) or []) for g in (engineer.team_groups or [])):
                        pairs.add((pdl.id, email))
                except Exception:
                    pass
    except Exception:
        pass
    return pairs


def _exclude_pdl_member_messages(query, pdl_ids=None):
    """Exclude messages sent by an engineer who has access to that same PDL."""
    pairs = _pdl_member_sender_pairs(pdl_ids)
    if not pairs:
        return query
    clauses = [and_(Email.pdl_id == pdl_id, func.lower(Email.sender_email) == email) for pdl_id, email in pairs]
    return query.filter(not_(or_(*clauses)))


def _is_pdl_member_sender(email):
    """True when the stored email was sent by an engineer authorized for its PDL."""
    if not email or not getattr(email, "pdl_id", None):
        return False
    sender = (getattr(email, "sender_email", "") or "").strip().lower()
    if not sender:
        return False
    return (email.pdl_id, sender) in _pdl_member_sender_pairs([email.pdl_id])


def cleanup_noncritical_internal_notifications():
    """Close notifications that should never have been emitted for internal/system mail.

    Older versions could create a schedule_miss notification for an internal/system
    message even when the email was marked Not tracked. Keep genuine critical
    operational-event alerts open, but close stale ordinary alerts so they do not
    reappear in the notification center or badge.
    """
    try:
        rows = (
            Alert.query.join(Email, Alert.email_id == Email.id)
            .filter(
                Alert.read.is_(False),
                Alert.alert_type.in_(("schedule_miss", "executive_copy", "action_required", "ai_attention")),
                Email.monitoring_excluded.is_(True),
                Email.sender_type.in_(("internal", "engineer")),
            ).all()
        )
        changed = 0
        for alert in rows:
            alert.read = True
            changed += 1
        if changed:
            db.session.commit()
        return changed
    except Exception:
        db.session.rollback()
        return 0


def cleanup_pdl_member_outbound_messages():
    """Hide historical outbound copies from engineers authorized for the same PDL.

    This is intentionally a soft exclusion: the email remains in the database for
    response matching, audit and conversation history, but never appears as a new
    workload/escalation/notification item.
    """
    try:
        pdls = PDL.query.filter_by(active=True).all()
        changed = 0
        for pdl in pdls:
            pairs = _pdl_member_sender_pairs([pdl.id])
            emails = {email for _, email in pairs}
            if not emails:
                continue
            rows = Email.query.filter(Email.pdl_id == pdl.id, func.lower(Email.sender_email).in_(emails)).all()
            for row in rows:
                if not row.monitoring_excluded or row.response_required or row.engineer_action_required or row.customer_waiting:
                    row.monitoring_excluded = True
                    row.response_required = False
                    row.engineer_action_required = False
                    row.customer_waiting = False
                    changed += 1
                # PDL-member mail is excluded from workload metrics, but an open
                # CRITICAL operational-event alert must remain visible. Do not
                # blanket-close notifications just because the sender is internal.
                Alert.query.filter(
                    Alert.email_id == row.id,
                    Alert.read.is_(False),
                    Alert.alert_type != "critical_escalation",
                ).update({Alert.read: True}, synchronize_session=False)
        if changed:
            db.session.commit()
        return changed
    except Exception:
        db.session.rollback()
        return 0


def live_mailbox_query():
    """Return emails visible in the operational mailbox view.

    IMPORTANT: visibility is intentionally independent of workload exclusion.
    Informational/recognition emails can be excluded from TAT/workload metrics
    but must remain visible after AI analysis. This prevents the manual
    Analyze-with-AI action from making rows disappear.
    """
    ids = allowed_pdl_ids()
    if not ids:
        return Email.query.filter(false())
    q = Email.query.filter(
        Email.pdl_id.in_(ids),
        Email.visible_in_monitoring.is_(True),
    )
    return _exclude_pdl_member_messages(q, ids)


def dashboard_query():

    ids = allowed_pdl_ids()

    if not ids:
        return Email.query.filter(false())

    q = Email.query.filter(
        Email.pdl_id.in_(ids),
        Email.monitoring_excluded.is_(False),
    )
    return _exclude_pdl_member_messages(q, ids)

# ============================================================
# GLOBAL TEMPLATE DATA
# ============================================================

def enqueue_unanalyzed_ai(limit=None):
    """Queue AI review asynchronously without blocking the dashboard.

    Every current-day customer email is eligible in all_customer mode. Existing
    analyses created by an older prompt version are re-queued once so the new
    intelligence rules actually affect previously imported mail.
    """
    try:
        settings = ai_settings()
        provider = (settings.get("AI_PROVIDER") or "local").strip().lower()
        if provider not in {"local", "ollama"}:
            return 0
        from ai.configuration import get_ai_setting
        prompt_version = get_ai_setting("AI_PROMPT_VERSION") or "v3-context"
        queue_limit = int(limit or get_ai_setting("AI_QUEUE_LIMIT") or 8)
        queue_limit = max(1, min(queue_limit, 25))
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(hours=24)
        retry_cutoff = now - timedelta(minutes=15)
        # Queue customer mail that has never had AI analysis OR whose latest
        # analysis is only the deterministic/hybrid pass.  The old implementation
        # used `~Email.ai_analyses.any()`, which meant an email could become
        # permanently invisible to the automatic Ollama queue as soon as any
        # lightweight analysis record existed.  Manual "Analyze with AI" uses
        # force=True and therefore appeared to fix the same email.
        latest_ai_id = (
            db.session.query(func.max(AIAnalysis.id))
            .filter(AIAnalysis.email_id == Email.id)
            .correlate(Email)
            .scalar_subquery()
        )
        rows=(Email.query.filter(
                Email.sender_type == "customer",
                Email.received_at >= recent_cutoff,
                or_(
                    ~Email.ai_analyses.any(),
                    and_(
                        AIAnalysis.id == latest_ai_id,
                        AIAnalysis.status == "COMPLETE",
                        AIAnalysis.provider.in_(("hybrid", "local")),
                        AIAnalysis.fallback_used.is_(False),
                    ),
                ),
            )
            .outerjoin(AIAnalysis, AIAnalysis.email_id == Email.id)
            .filter(or_(AIAnalysis.id == latest_ai_id, AIAnalysis.id.is_(None)))
            .order_by(Email.received_at.desc()).limit(queue_limit * 3).all())
        selected=[]; seen=set()
        for row in rows:
            selected.append(row); seen.add(row.id)
            if len(selected) >= queue_limit: break
        if len(selected) < queue_limit:
            candidates=(Email.query.join(AIAnalysis, AIAnalysis.email_id == Email.id)
                .filter(Email.sender_type == "customer", Email.received_at >= recent_cutoff,
                        AIAnalysis.status == "COMPLETE", AIAnalysis.prompt_version != prompt_version,
                        AIAnalysis.created_at <= retry_cutoff)
                .order_by(Email.received_at.desc()).limit(queue_limit * 3).all())
            for row in candidates:
                if row.id in seen: continue
                selected.append(row); seen.add(row.id)
                if len(selected) >= queue_limit: break
        if len(selected) < queue_limit:
            latest_id=(db.session.query(func.max(AIAnalysis.id)).filter(AIAnalysis.email_id == Email.id).correlate(Email).scalar_subquery())
            retry_rows=(Email.query.join(AIAnalysis, AIAnalysis.email_id == Email.id)
                .filter(AIAnalysis.id == latest_id, Email.sender_type == "customer", Email.received_at >= recent_cutoff,
                        or_(AIAnalysis.fallback_used.is_(True), AIAnalysis.error.isnot(None)),
                        AIAnalysis.created_at <= retry_cutoff).order_by(Email.received_at.desc()).limit(queue_limit * 2).all())
            for row in retry_rows:
                if row.id in seen: continue
                selected.append(row); seen.add(row.id)
                if len(selected) >= queue_limit: break
        queued=0
        for row in selected:
            latest=max(row.ai_analyses, key=lambda a: a.created_at or datetime.min.replace(tzinfo=timezone.utc)) if row.ai_analyses else None
            force=bool(latest and (latest.fallback_used or latest.error or latest.prompt_version != prompt_version))
            if enqueue_ai(app, row.id, force=force): queued += 1
        return queued
    except Exception as exc:
        log_event("WARN", "ai_queue", "Unable to queue one or more AI analyses.", str(exc))
        return 0


@app.context_processor
def inject_globals():

    if current_user.is_authenticated:

        if current_user.role == "admin":
            active_pdls = PDL.query.filter_by(active=True).all()
        else:
            ids = allowed_pdl_ids()
            active_pdls = (
                PDL.query.filter(PDL.id.in_(ids)).order_by(PDL.name).all()
                if ids else []
            )

    else:
        active_pdls = []

    alert_count = 0
    recent_alerts = []
    if current_user.is_authenticated:
        ids = [p.id for p in active_pdls]
        _sync_notification_read_state(ids)
        if ids:
            # The notification center is driven by when the alert was created,
            # not when the underlying email was received. An old email can become
            # newly actionable after AI analysis or a TAT breach.
            day_start, day_end = _current_ist_day_bounds()
            alert_query = Alert.query.filter(
                Alert.pdl_id.in_(ids),
                Alert.read.is_(False),
                Alert.alert_type.in_(("ai_attention", "schedule_miss", "tat_breach", "executive_copy", "action_required", "critical_escalation")),
                Alert.created_at >= day_start, Alert.created_at < day_end,
            )
            alert_count = alert_query.count()
            recent_alerts = alert_query.order_by(Alert.created_at.desc()).limit(5).all()

    return {
        "active_pdls": active_pdls,
        "alert_count": alert_count,
        "recent_alerts": recent_alerts,
        "last_sync": get_sync_state("last_sync"),
        "last_sync_result": get_sync_state("last_sync_result"),
        "app_name": "PDL Email Intelligence",
        "app_version": app.config.get("APP_VERSION", "9.5.0"),
        "current_year": datetime.now().year,
        "EMAIL_CATEGORY_LABELS": EMAIL_CATEGORY_LABELS,
    }


# ============================================================
# AUTO-SYNC SCHEDULER
# ============================================================

def run_scheduled_sync():
    with app.app_context():
        enabled = sync_setting("auto_sync_enabled", "1") == "1"
        if not enabled or not app.config.get("OUTLOOK_SYNC_ENABLED", True):
            return

        try:
            interval = max(1, int(sync_setting(
                "sync_interval_minutes",
                str(app.config.get("SYNC_INTERVAL_MINUTES", 5)),
            )))
        except (TypeError, ValueError):
            interval = max(1, int(app.config.get("SYNC_INTERVAL_MINUTES", 5)))
        last_raw = get_sync_state("last_sync")
        if last_raw:
            try:
                last = datetime.fromisoformat(last_raw)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - last < timedelta(minutes=interval):
                    return
            except Exception:
                pass

        if not SYNC_LOCK.acquire(blocking=False):
            log_event("WARN", "auto_sync", "Scheduled Outlook sync skipped because another sync is running.")
            return

        try:
            from outlook_connector import sync_outlook
            result = sync_outlook()
            enqueue_unanalyzed_ai()
            log_event(
                "INFO",
                "auto_sync",
                "Automatic Outlook synchronization completed.",
                json.dumps(result, default=str),
            )
        except Exception as exc:
            db.session.rollback()
            log_event("ERROR", "auto_sync", "Automatic Outlook synchronization failed.", str(exc))
        finally:
            db.session.remove()
            SYNC_LOCK.release()


def start_scheduler():
    global SCHEDULER
    if SCHEDULER is not None:
        return

    # One scheduler instance only. The scheduler wakes every minute, while
    # the persisted sync interval (default 5 minutes) decides when Outlook
    # collection is actually allowed to run. Monitoring-slot reports also
    # run from this single scheduler instance.
    SCHEDULER = BackgroundScheduler(daemon=True)
    SCHEDULER.add_job(
        run_scheduled_sync,
        "interval",
        minutes=1,
        id="outlook_auto_sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )
    SCHEDULER.add_job(
        run_monitoring_slot_end_reports,
        "interval",
        minutes=1,
        id="monitoring_slot_end_reports",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )
    SCHEDULER.start()


# ============================================================
# FIRST-RUN SETUP
# ============================================================

def _setup_required():
    try:
        return User.query.count() == 0
    except Exception:
        return False


def _write_env_values(values):
    """Update selected .env keys without destroying the existing file."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    existing = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            existing = fh.read().splitlines()

    keys = set(values)
    written = set()
    output = []
    for line in existing:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            output.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in keys:
            output.append(f"{key}={values[key]}")
            written.add(key)
        else:
            output.append(line)

    for key, value in values.items():
        if key not in written:
            output.append(f"{key}={value}")

    with open(env_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(output).rstrip() + "\n")


@app.route("/login", methods=["GET", "POST"])
def login():

    if _setup_required():
        return redirect(url_for("initial_setup"))

    if current_user.is_authenticated:
        return redirect(
            url_for("dashboard")
        )

    if request.method == "POST":

        email = (
            request.form
            .get("email", "")
            .strip()
            .lower()
        )

        password = request.form.get(
            "password",
            "",
        )

        user = (
            User.query
            .filter_by(
                email=email,
                active=True,
            )
            .first()
        )

        if user and user.check_password(password):

            user.last_login_at = datetime.now(
                timezone.utc
            )

            db.session.commit()

            login_user(user)

            return redirect(
                url_for("dashboard")
            )

        flash(
            "Invalid email or password.",
            "error",
        )

    return render_template(
        "login.html"
    )


@app.route("/logout")
@login_required
def logout():

    logout_user()

    return redirect(
        url_for("login")
    )


# ============================================================
# CONVERSATION / THREAD HELPERS
# ============================================================

def _normalized_thread_subject(subject):
    value = (subject or "").strip().lower()
    value = re.sub(r"^(?:(?:re|fw|fwd)\s*:\s*)+", "", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip()


def email_conversation_key(email):
    """Stable thread key using Outlook ConversationID, with a safe subject fallback."""
    cid = (getattr(email, "conversation_id", None) or "").strip()
    if cid:
        return f"cid:{cid}"
    return f"subject:{getattr(email, 'pdl_id', None)}:{_normalized_thread_subject(getattr(email, 'subject', ''))}"


def collapse_conversations(rows):
    """Return only the latest inbound row per conversation.

    The underlying database keeps every message. The UI defaults to a single
    latest message per thread so repeated RE:/FW: copies do not flood lists.
    Each latest row receives `conversation_count` and `conversation_key` for
    the template and thread link.
    """
    grouped = {}
    for row in rows or []:
        key = email_conversation_key(row)
        current = grouped.get(key)
        if current is None or (row.received_at or datetime.min.replace(tzinfo=timezone.utc)) > (current.received_at or datetime.min.replace(tzinfo=timezone.utc)):
            grouped[key] = row
    result = sorted(grouped.values(), key=lambda r: r.received_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    counts = {}
    for row in rows or []:
        counts[email_conversation_key(row)] = counts.get(email_conversation_key(row), 0) + 1
    for row in result:
        key = email_conversation_key(row)
        row.conversation_count = counts.get(key, 1)
        row.conversation_key = key
    return result


def apply_email_status_filter(rows, status):
    """Apply the canonical workflow status after conversation collapse.

    Canonical states:
      RESPONDED
      AWAITING_RESPONSE
      INFORMATIONAL
      TAT_BREACHED

    Older URL/status names remain accepted as aliases for compatibility.
    """
    aliases = {
        "awaiting_engineer": "awaiting_response",
        "engineer_action": "awaiting_response",
        "customer_waiting": "awaiting_response",
        "pending": "awaiting_response",
        "open": "awaiting_response",
    }
    status = aliases.get(status, status)
    if not status:
        return rows

    out = []
    for email in rows:
        responded = bool(email.responded)
        awaiting = bool(email.response_required and not responded and not email.monitoring_excluded)
        informational = bool(not email.response_required and not email.monitoring_excluded)

        if status == "responded" and not responded:
            continue
        if status == "awaiting_response" and not awaiting:
            continue
        if status == "informational" and not informational:
            continue
        if status == "tat_breached" and not (email.tat_breached and email.response_required):
            continue
        out.append(email)
    return out



@app.route("/conversation/<int:email_id>")
@login_required
def conversation_view(email_id):
    """Show the complete stored inbound thread while keeping list views collapsed."""
    seed = Email.query.get_or_404(email_id)
    if seed.pdl_id not in allowed_pdl_ids():
        abort(403)
    key = email_conversation_key(seed)
    rows = Email.query.filter(Email.pdl_id == seed.pdl_id).order_by(Email.received_at.asc()).all()
    thread = [row for row in rows if email_conversation_key(row) == key]
    if not thread:
        thread = [seed]
    return render_template("conversation.html", email=seed, thread=thread, conversation_count=len(thread))


# ============================================================
# GLOBAL SEARCH
# ============================================================

@app.route("/search")
@login_required
def global_search():
    term = (request.args.get("q") or "").strip()
    results = {
        "pdls": [],
        "groups": [],
        "engineers": [],
        "slots": [],
        "executives": [],
        "users": [],
        "emails": [],
        "features": [],
    }

    feature_catalog = [
        ("Dashboard", "PDL Control Center, KPI dashboard and metric periods", "dashboard"),
        ("Live Emails", "latest monitored conversations and customer emails", "emails"),
        ("Escalations", "customer escalation candidates and critical items", "escalations"),
        ("Notifications", "unread and read action-required notifications", "alerts"),
        ("Customers", "customer-originated monitored emails", "emails?sender_type=customer"),
        ("Operations Reports", "day-wise engineer-wise PDL slot response TAT compliance reporting", "reports"),
        ("Team Management", "groups, engineers, PDL access and monitoring slots", "team_management"),
        ("PDLs", "PDL configuration and ownership", "pdls"),
        ("Users", "application users and PDL access", "users"),
        ("Events & Logs", "synchronization and operational events", "events"),
        ("Administration", "system and Outlook configuration", "admin"),
        ("Settings", "personal PDL and synchronization settings", "settings"),
        ("Monitoring Slot Activities", "slot-level received, attended and TAT reports", "team_management"),
    ]

    if term:
        like = f"%{term}%"
        ids = allowed_pdl_ids()
        if ids:
            results["pdls"] = PDL.query.filter(
                PDL.id.in_(ids),
                or_(PDL.name.ilike(like), PDL.address.ilike(like), PDL.team.ilike(like), PDL.owner_name.ilike(like), PDL.owner_email.ilike(like)),
            ).order_by(PDL.name).limit(30).all()
            results["slots"] = MonitoringSlot.query.join(TeamGroup).filter(
                or_(MonitoringSlot.name.ilike(like), TeamGroup.name.ilike(like))
            ).order_by(MonitoringSlot.name).limit(30).all()

        results["groups"] = TeamGroup.query.filter(
            or_(TeamGroup.name.ilike(like), TeamGroup.manager_name.ilike(like))
        ).order_by(TeamGroup.name).limit(30).all()
        results["engineers"] = Engineer.query.filter(
            Engineer.active.is_(True),
            or_(Engineer.name.ilike(like), Engineer.email.ilike(like), Engineer.team.ilike(like), Engineer.designation.ilike(like))
        ).order_by(Engineer.name).limit(30).all()
        results["executives"] = ExecutiveGroup.query.filter(
            or_(ExecutiveGroup.name.ilike(like), ExecutiveGroup.description.ilike(like))
        ).order_by(ExecutiveGroup.name).limit(30).all()
        if current_user.role == "admin":
            results["users"] = User.query.filter(
                or_(User.name.ilike(like), User.email.ilike(like), User.role.ilike(like))
            ).order_by(User.name).limit(30).all()
        if ids:
            results["emails"] = _exclude_pdl_member_messages(
                Email.query.filter(
                    Email.pdl_id.in_(ids),
                    Email.visible_in_monitoring.is_(True),
                    or_(Email.subject.ilike(like), Email.sender_email.ilike(like), Email.sender_name.ilike(like), Email.customer_name.ilike(like), Email.body.ilike(like))
                ), ids
            ).order_by(Email.received_at.desc()).limit(40).all()

        results["features"] = [
            item for item in feature_catalog
            if term.lower() in (item[0] + " " + item[1]).lower()
        ]

    return render_template("search.html", term=term, results=results, feature_catalog=feature_catalog)


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/")
@login_required
def dashboard():

    range_ctx = metric_range_context(default="24h")
    q = apply_metric_range(dashboard_query(), range_ctx)

    emails = collapse_conversations(
        q.order_by(Email.received_at.desc()).limit(750).all()
    )[:250]

    total = q.count()

    customer = q.filter(
        Email.sender_type == "customer"
    ).count()

    internal = q.filter(
        Email.sender_type.in_(
            ["internal", "engineer"]
        )
    ).count()

    awaiting_engineer = q.filter(
        Email.customer_waiting.is_(True),
        Email.responded.is_(False),
    ).count()

    engineer_action = q.filter(
        Email.engineer_action_required.is_(True),
        Email.responded.is_(False),
    ).count()

    breached = q.filter(
        Email.tat_breached.is_(True)
    ).count()

    critical = q.filter(or_(
        Email.severity == "critical",
        Email.escalation_level == "critical",
    )).count()

    high = q.filter(
        or_(Email.severity == "high", Email.escalation_level == "high"),
        Email.severity != "critical",
        Email.escalation_level != "critical",
    ).count()

    medium = q.filter(
        or_(Email.severity == "medium", Email.escalation_level == "medium"),
        ~Email.severity.in_(["critical", "high"]),
        ~Email.escalation_level.in_(["critical", "high"]),
    ).count()

    # Priority pie chart uses mutually exclusive severity buckets. TAT
    # breaches remain a separate operational KPI because an email can be
    # both breached and critical/high/medium.
    normal = max(0, total - critical - high - medium)

    compliance = (
        round(
            ((total - breached) / total) * 100,
            1,
        )
        if total
        else 0
    )

    # Status-distribution KPI requested for the dashboard. These values are
    # derived from existing response/TAT/escalation fields; no email workflow
    # semantics are changed.
    actionable_q = q.filter(
        Email.response_required.is_(True),
        Email.monitoring_excluded.is_(False),
    )
    actionable_total = actionable_q.count()
    received_status = actionable_total
    processed_status = actionable_q.filter(
        Email.responded.is_(True)
    ).count()
    pending_status = actionable_q.filter(
        Email.responded.is_(False)
    ).count()
    failed_status = breached
    blocked_status = critical

    pdl_rows = []

    allowed_ids = allowed_pdl_ids()

    if allowed_ids:

        pdls = (
            PDL.query
            .filter(
                PDL.id.in_(allowed_ids)
            )
            .order_by(PDL.name)
            .all()
        )

        for pdl in pdls:

            base = q.filter(
                Email.pdl_id == pdl.id
            )

            total_pdl = base.count()
            actionable_pdl = base.filter(
                Email.response_required.is_(True),
                Email.monitoring_excluded.is_(False),
            )
            actionable_total_pdl = actionable_pdl.count()
            responded_pdl = actionable_pdl.filter(Email.responded.is_(True)).count()
            # Response coverage is based on actionable workload only. Informational,
            # recognition and excluded team mail remain visible in Total Emails but
            # cannot inflate the response-rate denominator.
            unattended_pdl = actionable_pdl.filter(Email.responded.is_(False)).count()
            pdl_rows.append({
                "id": pdl.id,
                "name": pdl.name,
                "count": total_pdl,
                "responded": responded_pdl,
                "unattended": unattended_pdl,
                "response_rate": round((responded_pdl / actionable_total_pdl) * 100, 1) if actionable_total_pdl else 100.0,
                "critical": base.filter(or_(
                    Email.severity == "critical",
                    Email.escalation_level == "critical",
                )).count(),
                "breached": actionable_pdl.filter(
                    Email.tat_breached.is_(True)
                ).count(),
            })

    category_order = [
        "recognition", "escalation", "schedule_request", "update_request",
        "follow_up", "technical_support", "information_request", "general"
    ]
    category_rows = []
    for category in category_order:
        base = q.filter(Email.email_category == category)
        count = base.count()
        if not count:
            continue
        responded_count = base.filter(Email.responded.is_(True)).count()
        unattended_count = base.filter(
            Email.responded.is_(False),
        ).count()
        category_rows.append({
            "key": category,
            "name": EMAIL_CATEGORY_LABELS.get(category, category.replace("_", " ").title()),
            "count": count,
            "responded": responded_count,
            "unattended": unattended_count,
        })

    sentiment_order = [
        ("negative", "Negative"),
        ("concerned", "Concerned"),
        ("neutral", "Neutral"),
        ("positive", "Positive"),
    ]
    sentiment_rows = []
    for key, name in sentiment_order:
        base = q.filter(Email.sentiment == key)
        count = base.count()
        if count:
            sentiment_rows.append({
                "key": key,
                "name": name,
                "count": count,
                "action_required": base.filter(
                    Email.engineer_action_required.is_(True),
                    Email.responded.is_(False),
                ).count(),
            })

    recent = collapse_conversations(
        q.order_by(Email.received_at.desc()).limit(100).all()
    )[:12]

    return render_template(
        "dashboard.html",

        total=total,
        customer=customer,
        internal=internal,

        awaiting_engineer=awaiting_engineer,
        engineer_action=engineer_action,

        breached=breached,
        critical=critical,
        high=high,
        medium=medium,
        normal=normal,

        compliance=compliance,
        received_status=received_status,
        processed_status=processed_status,
        pending_status=pending_status,
        failed_status=failed_status,
        blocked_status=blocked_status,

        emails=emails,
        recent=recent,
        pdl_rows=pdl_rows,
        category_rows=category_rows,
        sentiment_rows=sentiment_rows,
        metric_range=range_ctx,
        snapshot_last_sync=get_sync_state("last_sync"),
    )



# ============================================================
# PDL DETAIL
# ============================================================

@app.route("/pdl/<int:pdl_id>")
@login_required
def pdl_detail(pdl_id):
    """Detailed operational view for a single PDL.

    The dashboard intentionally stays summary-only. All PDL-specific
    workload, response, unattended, intent, priority and TAT details
    are shown here.
    """
    if pdl_id not in allowed_pdl_ids():
        abort(403)

    pdl = PDL.query.get_or_404(pdl_id)
    range_ctx = metric_range_context(default="24h")
    base = apply_metric_range(
        _exclude_pdl_member_messages(
            Email.query.filter(
                Email.pdl_id == pdl_id,
                Email.monitoring_excluded.is_(False),
            ),
            [pdl_id],
        ),
        range_ctx,
    )

    total = base.count()
    # Workload/coverage metrics must use only emails that actually require a
    # response. Informational, recognition and configured-team mail remain
    # visible in Total Emails but cannot dilute response coverage or TAT.
    actionable = base.filter(
        Email.response_required.is_(True),
        Email.monitoring_excluded.is_(False),
    )
    actionable_total = actionable.count()
    responded = actionable.filter(Email.responded.is_(True)).count()
    unattended = actionable.filter(Email.responded.is_(False)).count()
    waiting = actionable.filter(
        Email.customer_waiting.is_(True),
        Email.responded.is_(False),
    ).count()
    action_required = actionable.filter(
        Email.engineer_action_required.is_(True),
        Email.responded.is_(False),
    ).count()
    breached = actionable.filter(Email.tat_breached.is_(True)).count()

    # Priority is the strongest of severity or escalation level. Previously
    # the PDL page only queried escalation_level, so emails classified as
    # severity=critical could disappear from Critical drill-downs.
    critical = base.filter(or_(
        Email.severity == "critical",
        Email.escalation_level == "critical",
    )).count()
    high = base.filter(
        or_(Email.severity == "high", Email.escalation_level == "high"),
        Email.severity != "critical",
        Email.escalation_level != "critical",
    ).count()
    medium = base.filter(
        or_(Email.severity == "medium", Email.escalation_level == "medium"),
        ~Email.severity.in_(["critical", "high"]),
        ~Email.escalation_level.in_(["critical", "high"]),
    ).count()
    normal = max(0, total - critical - high - medium)

    response_rate = round((responded / actionable_total) * 100, 1) if actionable_total else 100.0
    compliance = round(((actionable_total - breached) / actionable_total) * 100, 1) if actionable_total else 100.0

    category_order = [
        "recognition", "escalation", "schedule_request", "update_request",
        "follow_up", "technical_support", "information_request", "general"
    ]
    category_rows = []
    for category in category_order:
        cat = base.filter(Email.email_category == category)
        count = cat.count()
        if not count:
            continue
        cat_responded = cat.filter(Email.responded.is_(True)).count()
        cat_unattended = cat.filter(
            Email.sender_type == "customer",
            Email.responded.is_(False),
        ).count()
        category_rows.append({
            "key": category,
            "name": EMAIL_CATEGORY_LABELS.get(
                category,
                category.replace("_", " ").title(),
            ),
            "count": count,
            "responded": cat_responded,
            "unattended": cat_unattended,
        })

    recent = collapse_conversations(base.order_by(Email.received_at.desc()).limit(150).all())[:50]

    # Monitoring assignment is kept for the Actions menu. Detailed slot
    # activity is intentionally moved to a dedicated monitoring-slot page.
    assignments = PDLMonitoringAssignment.query.filter_by(pdl_id=pdl.id).order_by(PDLMonitoringAssignment.assigned_at.desc()).all()
    eligible_slots = []
    slot_engineers = {}
    for slot in MonitoringSlot.query.filter_by(active=True).order_by(MonitoringSlot.start_time, MonitoringSlot.name).all():
        if not slot.pdls or pdl in slot.pdls:
            eligible_slots.append(slot)
            slot_engineers[slot.id] = sorted(
                {e.id: e for e in slot.group.engineers if pdl in e.pdl_access}.values(),
                key=lambda e: (e.name or '').lower(),
            )

    slot_engineer_options = {}
    unique_engineers = {}
    for slot in eligible_slots:
        for engineer in slot_engineers.get(slot.id, []):
            unique_engineers.setdefault(engineer.id, {"engineer": engineer, "slot_ids": set()})["slot_ids"].add(slot.id)
    slot_engineer_options = [
        {"engineer": item["engineer"], "slot_ids": sorted(item["slot_ids"])}
        for item in sorted(unique_engineers.values(), key=lambda item: (item["engineer"].name or "").lower())
    ]

    return render_template(
        "pdl_detail.html",
        pdl=pdl,
        total=total,
        actionable_total=actionable_total,
        responded=responded,
        unattended=unattended,
        waiting=waiting,
        action_required=action_required,
        breached=breached,
        critical=critical,
        high=high,
        medium=medium,
        normal=normal,
        response_rate=response_rate,
        compliance=compliance,
        category_rows=category_rows,
        recent=recent,
        metric_range=range_ctx,
        assignments=assignments,
        eligible_slots=eligible_slots,
        slot_engineers=slot_engineers,
        slot_engineer_options=slot_engineer_options,
    )



# ============================================================
# CUSTOMER AI EVALUATION
# ============================================================

@app.route("/customer-ai-evaluation")
@login_required
def customer_ai_evaluation():
    """Customer-only AI evaluation workspace using real monitored emails."""
    range_ctx = metric_range_context(default="24h")
    q = apply_metric_range(live_mailbox_query(), range_ctx).filter(
        Email.sender_type == "customer",
    )
    pdl_id = request.args.get("pdl_id", type=int)
    sentiment = (request.args.get("sentiment") or "").strip().lower()
    category = (request.args.get("email_category") or "").strip().lower()
    level = (request.args.get("level") or "").strip().lower()
    response_state = (request.args.get("response") or "").strip().lower()
    escalation_state = (request.args.get("escalation") or "").strip().lower()
    if pdl_id:
        q = q.filter(Email.pdl_id == pdl_id)
    if sentiment in {"positive", "neutral", "concerned", "negative"}:
        q = q.filter(Email.sentiment == sentiment)
    if category:
        q = q.filter(Email.email_category == category)
    if level in {"critical", "high", "medium", "normal"}:
        q = q.filter(or_(Email.severity == level, Email.escalation_level == level))
    if response_state == "pending":
        q = q.filter(Email.response_required.is_(True), Email.responded.is_(False))
    elif response_state == "responded":
        q = q.filter(Email.responded.is_(True))
    elif response_state == "informational":
        q = q.filter(Email.response_required.is_(False))
    if escalation_state == "yes":
        q = q.filter(or_(
            Email.escalation_level.in_(["medium", "high", "critical"]),
            Email.email_category == "escalation",
        ))
    elif escalation_state == "no":
        q = q.filter(
            ~Email.escalation_level.in_(["medium", "high", "critical"]),
            Email.email_category != "escalation",
        )
    rows = collapse_conversations(q.order_by(Email.received_at.desc()).limit(500).all())[:120]
    pdls = PDL.query.filter(PDL.id.in_(allowed_pdl_ids())).order_by(PDL.name).all() if allowed_pdl_ids() else []
    training_candidates = _training_candidates()
    training_map = {}
    for item in training_candidates:
        latest = AIAnalysis.query.filter_by(email_id=item.id).order_by(AIAnalysis.created_at.desc()).first()
        result = latest.result if latest else {}
        category_value = str(result.get("category") or item.email_category or "general").lower()
        fallback_intent = {
            "recognition": "RECOGNITION", "escalation": "ESCALATION",
            "schedule_request": "SCHEDULE_REQUEST", "update_request": "STATUS_REQUEST",
            "follow_up": "FOLLOW_UP", "technical_support": "TECHNICAL_SUPPORT",
            "information_request": "INFORMATION_REQUEST", "general": "GENERAL",
        }.get(category_value, "GENERAL")
        training_map[str(item.id)] = {
            "sentiment": result.get("sentiment") or item.sentiment or "neutral",
            "priority": result.get("priority") or item.severity or "normal",
            "category": category_value,
            "intent": result.get("intent") or fallback_intent,
            "response_required": bool(result.get("response_required", item.response_required)),
            "recognition": bool(result.get("recognition", category_value == "recognition")),
            "escalation": bool(result.get("escalation", category_value == "escalation" or item.escalation_level in {"high", "critical"})),
        }
    return render_template(
        "customer_ai_evaluation.html", emails=rows, pdls=pdls, metric_range=range_ctx,
        active_pdl_id=pdl_id, active_sentiment=sentiment,
        active_email_category=category, active_level=level,
        active_response=response_state, active_escalation=escalation_state,
        training_candidates=training_candidates, training_map=training_map,
        ollama_status=ollama_cli_status(), training_count=training_example_count(),
        active_ai_model=get_sync_state("ai:AI_MODEL") or "qwen3:8b",
    )



# ============================================================
# AI TRAINING / OLLAMA
# ============================================================

def _training_candidates():
    ids = allowed_pdl_ids()
    if not ids:
        return []
    alert_ids = {
        row.email_id for row in Alert.query
        .filter(Alert.email_id.isnot(None))
        .order_by(Alert.created_at.desc())
        .limit(500).all()
    }
    q = Email.query.filter(
        Email.pdl_id.in_(ids),
        Email.sender_type == "customer",
        or_(
            Email.escalation_level.in_(["critical", "high", "medium"]),
            Email.id.in_(alert_ids) if alert_ids else false(),
        ),
    )
    return q.order_by(Email.received_at.desc()).limit(200).all()


@app.route("/customer-ai-evaluation/train", methods=["POST"])
@admin_required
def train_ai():
    email_id = request.form.get("email_id", type=int)
    email = Email.query.get_or_404(email_id)
    if email.pdl_id not in allowed_pdl_ids():
        abort(403)
    candidate_ids = {row.id for row in _training_candidates()}
    if email.id not in candidate_ids:
        flash("Only emails currently represented in Escalations/Notifications can be used for AI training.", "error")
        return redirect(url_for("customer_ai_evaluation"))

    try:
        labels = normalize_training_labels({
            "intent": request.form.get("intent"),
            "sentiment": request.form.get("sentiment"),
            "priority": request.form.get("priority"),
            "category": request.form.get("category"),
            "response_required": request.form.get("response_required"),
            "recognition": request.form.get("recognition"),
            "escalation": request.form.get("escalation"),
            "base_model": request.form.get("base_model") or "qwen3:8b",
        })
    except ValueError as exc:
        flash(f"Invalid training labels: {exc}", "error")
        return redirect(url_for("customer_ai_evaluation"))

    example = add_training_example(email, current_user.id, labels, request.form.get("notes", ""))
    # Re-run the selected email immediately so the corrected classification is
    # visible in the same application logic path used by production mail.
    enqueue_ai(app, email.id, force=True)

    build_now = request.form.get("build_model") == "on"
    if build_now:
        try:
            result = build_ollama_model(labels["base_model"], request.form.get("custom_model") or "pdl-qwen3:8b")
            set_sync_state("ai:AI_PROVIDER", "ollama")
            set_sync_state("ai:AI_MODEL", result["custom_model"])
            set_sync_state("ai:AI_BASE_MODEL", result["base_model"])
            set_sync_state("ai:AI_TRAINING_COUNT", str(training_example_count()))
            set_sync_state("ai:AI_LAST_TRAINED", datetime.now(timezone.utc).isoformat())
            log_event("INFO", "ai_training", f'Validated training example {example.id} added and Ollama model {result["custom_model"]} rebuilt.')
            flash(f"Training saved. Ollama {result['custom_model']} was rebuilt with the validated examples and is now the active AI model.", "success")
        except Exception as exc:
            log_event("WARN", "ai_training", f"Training example {example.id} saved, but Ollama model build failed: {exc}")
            flash(f"Training example saved, but the Ollama model could not be rebuilt: {exc}", "warning")
    else:
        set_sync_state("ai:AI_TRAINING_COUNT", str(training_example_count()))
        set_sync_state("ai:AI_LAST_TRAINED", datetime.now(timezone.utc).isoformat())
        flash("Training example saved. It will be included in future Qwen prompts.", "success")
    return redirect(url_for("customer_ai_evaluation"))


@app.route("/customer-ai-evaluation/training-template.csv")
@admin_required
def training_sample_template():
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "subject", "message", "sender_type", "intent", "sentiment", "priority", "category",
        "response_required", "recognition", "escalation", "notes", "base_model", "pdl_name",
    ])
    writer.writerow([
        "Status update requested", "Please share the latest status and ETA.", "customer",
        "STATUS_REQUEST", "neutral", "normal", "update_request", "true", "false", "false",
        "Validated status-request example", "qwen3:8b", "Example PDL",
    ])
    writer.writerow([
        "Great work", "Thank you for the excellent support from the team.", "customer",
        "RECOGNITION", "positive", "normal", "recognition", "false", "true", "false",
        "Validated recognition example", "qwen3:8b", "Example PDL",
    ])
    response = make_response(output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = 'attachment; filename="pdl_ai_training_samples_template.csv"'
    return response


@app.route("/customer-ai-evaluation/train/upload", methods=["POST"])
@admin_required
def upload_training_samples():
    uploaded = request.files.get("sample_file")
    if not uploaded or not uploaded.filename:
        flash("Choose a CSV or JSON training sample file.", "error")
        return redirect(url_for("customer_ai_evaluation"))
    try:
        content = uploaded.read(5 * 1024 * 1024 + 1)
        samples = parse_training_sample_file(uploaded.filename, content)
        result = import_training_samples(samples, current_user.id, uploaded.filename)
        set_sync_state("ai:AI_TRAINING_COUNT", str(training_example_count()))
        set_sync_state("ai:AI_LAST_TRAINED", datetime.now(timezone.utc).isoformat())
        # Force one reconciliation pass so stale/deactivated rows are removed and
        # all newly imported examples are visible to semantic retrieval now.
        sync_result = index_all_training_examples()
        log_event(
            "INFO", "ai_training",
            f"Imported {result['imported']} validated sample(s) from {Path(uploaded.filename).name}; "
            f"duplicates={result['duplicates']}; RAG indexed={sync_result.get('indexed', 0)}.",
        )
        warning = ""
        if result.get("index_errors") or not sync_result.get("ok", False):
            warning = " Samples were saved, but one or more RAG indexing operations reported an error; check Admin → AI Intelligence Engine."
        flash(
            f"Training import complete: {result['imported']} added, {result['duplicates']} duplicate(s) skipped.{warning}",
            "warning" if warning else "success",
        )
    except Exception as exc:
        db.session.rollback()
        log_event("WARN", "ai_training", f"Training sample import failed: {exc}")
        flash(f"Training sample import failed: {exc}", "error")
    return redirect(url_for("customer_ai_evaluation"))


@app.route("/api/v1/ai/training/status")
@admin_required
def api_ai_training_status():
    status = ollama_cli_status()
    return jsonify({
        "ok": True,
        "ollama": status,
        "active_model": get_sync_state("ai:AI_MODEL") or "qwen3:8b",
        "base_model": get_sync_state("ai:AI_BASE_MODEL") or "qwen3:8b",
        "training_count": training_example_count(),
        "last_trained": get_sync_state("ai:AI_LAST_TRAINED"),
    })


@app.route("/api/v1/ai/training/build", methods=["POST"])
@admin_required
def api_ai_training_build():
    base_model = (request.form.get("base_model") or get_sync_state("ai:AI_BASE_MODEL") or "qwen3:8b").strip()
    custom_model = (request.form.get("custom_model") or "pdl-qwen3:8b").strip()
    try:
        result = build_ollama_model(base_model, custom_model)
        set_sync_state("ai:AI_PROVIDER", "ollama")
        set_sync_state("ai:AI_MODEL", result["custom_model"])
        set_sync_state("ai:AI_BASE_MODEL", result["base_model"])
        set_sync_state("ai:AI_TRAINING_COUNT", str(training_example_count()))
        set_sync_state("ai:AI_LAST_TRAINED", datetime.now(timezone.utc).isoformat())
        log_event("INFO", "ai_training", f'Ollama model {custom_model} rebuilt from {base_model} using {result["example_count"]} validated examples.')
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


# ============================================================
# AI RAG
# ============================================================

@app.route("/api/v1/ai/rag/status")
@admin_required
def api_ai_rag_status():
    validate = str(request.args.get("validate") or "0").lower() in {"1", "true", "yes", "on"}
    return jsonify(rag_status(validate_runtime=validate))


@app.route("/api/v1/ai/rag/reindex", methods=["POST"])
@admin_required
def api_ai_rag_reindex():
    result = index_all_training_examples()
    if result.get("ok"):
        log_event("INFO", "ai_rag", f"RAG synchronized {result.get('indexed', 0)} active example(s); updated={result.get('updated', 0)}, deleted={result.get('deleted', 0)}.")
        return jsonify(result)
    log_event("WARN", "ai_rag", "Local RAG reindex failed.", result.get("error"))
    return jsonify(result), 400


@app.route("/admin/ai/rag/reindex", methods=["POST"])
@admin_required
def admin_ai_rag_reindex():
    result = index_all_training_examples()
    if result.get("ok"):
        log_event("INFO", "ai_rag", f"Admin synchronized {result.get('indexed', 0)} active example(s); updated={result.get('updated', 0)}, deleted={result.get('deleted', 0)}.")
        flash(f"Local RAG synchronized: {result.get('indexed', 0)} active example(s); {result.get('updated', 0)} updated; {result.get('deleted', 0)} stale removed.", "success")
    else:
        flash(f"RAG reindex failed: {result.get('error', 'unknown error')}", "error")
    return redirect(url_for("admin"))


# ============================================================
# EMAILS
# ============================================================

@app.route("/emails")
@login_required
def emails():

    # Live Emails is an operational view: default to the last 24 hours.
    # Users can explicitly navigate to week/month/custom when historical mail
    # is needed. This prevents a report-period selection from making the live
    # mailbox view appear to be a week-long workload.
    range_ctx = metric_range_context(default="24h")
    # Live Emails is a visibility view, not a workload-only view. Informational
    # and recognition emails remain visible after AI analysis. Workload/TAT
    # exclusion is applied only to KPI/report queries.
    q = apply_metric_range(live_mailbox_query(), range_ctx)

    search = (
        request.args
        .get("q", "")
        .strip()
    )

    sender_type = (
        request.args
        .get("sender_type", "")
        .strip()
    )

    pdl_id = request.args.get(
        "pdl_id",
        type=int,
    )

    level = (
        request.args
        .get("level", "")
        .strip()
    )

    status = (
        request.args
        .get("status", "")
        .strip()
    )

    email_category = (
        request.args
        .get("email_category", "")
        .strip()
    )
    sentiment = (
        request.args
        .get("sentiment", "")
        .strip()
        .lower()
    )

    if search:

        like = f"%{search}%"

        q = q.filter(
            or_(
                Email.subject.ilike(like),
                Email.sender_email.ilike(like),
                Email.sender_name.ilike(like),
                Email.customer_name.ilike(like),
                Email.product.ilike(like),
                Email.issue_type.ilike(like),
                Email.body.ilike(like),
            )
        )

    if sender_type:
        q = q.filter(
            Email.sender_type == sender_type
        )

    if pdl_id:
        q = q.filter(
            Email.pdl_id == pdl_id
        )

    if email_category:
        q = q.filter(
            Email.email_category == email_category
        )

    if sentiment in {"positive", "neutral", "concerned", "negative"}:
        q = q.filter(
            Email.sentiment == sentiment
        )

    if level:
        # Priority filters use either deterministic severity or escalation
        # level so the list always matches dashboard/PDL Critical counts.
        if level in {"critical", "high", "medium"}:
            q = q.filter(or_(Email.severity == level, Email.escalation_level == level))
        else:
            q = q.filter(Email.escalation_level == level)

    # Fetch a larger candidate set, collapse by conversation, then apply the
    # status to the latest message. This prevents an older responded message
    # from appearing simply because a newer unresponded message exists in the
    # same thread.
    # TAT breach is an historical email-level fact. Filter it before
    # conversation collapse; otherwise a newer non-breached message in the same
    # thread can hide the exact email that breached and make the KPI drill-down
    # appear empty.
    if status == "tat_breached":
        q = q.filter(
            Email.tat_breached.is_(True),
            Email.response_required.is_(True),
            Email.monitoring_excluded.is_(False),
        )

    rows = (
        q.order_by(Email.received_at.desc())
        .limit(1500)
        .all()
    )
    rows = collapse_conversations(rows)
    rows = apply_email_status_filter(rows, "" if status == "tat_breached" else status)[:500]

    allowed_pdls = (
        PDL.query
        .filter(
            PDL.id.in_(allowed_pdl_ids())
        )
        .order_by(PDL.name)
        .all()
        if allowed_pdl_ids()
        else []
    )

    return render_template(
        "emails.html",
        emails=rows,
        pdls=allowed_pdls,
        active_search=search,
        active_sender_type=sender_type,
        active_pdl_id=pdl_id,
        active_email_category=email_category,
        active_sentiment=sentiment,
        active_level=level,
        active_status=status,
        metric_range=range_ctx,
    )


# ============================================================
# REAL-TIME OPERATIONS / LIVE MONITORING
# ============================================================

def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _current_ist_day_bounds():
    now = datetime.now(timezone.utc).astimezone(IST)
    start_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

def build_live_snapshot():
    """Return a fast, read-only operational snapshot for browser polling.

    This endpoint never touches Outlook. Outlook collection remains the
    responsibility of the background/manual sync. The browser can therefore
    poll this endpoint frequently without putting load on the mailbox.
    """
    ids = allowed_pdl_ids()
    _sync_notification_read_state(ids)
    base = (_exclude_pdl_member_messages(Email.query.filter(Email.pdl_id.in_(ids), Email.monitoring_excluded.is_(False)), ids)
            if ids else Email.query.filter(false()))
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)

    recent = base.filter(Email.received_at >= since)
    total_24h = recent.count()
    response_required = recent.filter(
        Email.response_required.is_(True), Email.responded.is_(False)
    ).count()
    critical = recent.filter(or_(
        Email.severity == "critical",
        Email.escalation_level == "critical",
    )).count()
    high = recent.filter(
        or_(Email.severity == "high", Email.escalation_level == "high"),
        Email.severity != "critical",
        Email.escalation_level != "critical",
    ).count()
    breached = recent.filter(Email.tat_breached.is_(True)).count()
    latest_ai_id = db.session.query(func.max(AIAnalysis.id)).group_by(AIAnalysis.email_id).subquery()
    latest_ai = AIAnalysis.query.filter(AIAnalysis.id.in_(db.session.query(latest_ai_id.c[0])))
    if ids:
        latest_ai = latest_ai.join(Email, AIAnalysis.email_id == Email.id).filter(Email.pdl_id.in_(ids))
    ai_processing = latest_ai.filter(AIAnalysis.status == "PROCESSING").count()
    ai_pending = latest_ai.filter(AIAnalysis.status == "PENDING").count()
    ai_review = latest_ai.filter(AIAnalysis.status == "COMPLETE", AIAnalysis.human_review_required.is_(True)).count()

    # At-risk = open response item with <= 30 minutes remaining.
    at_risk = 0
    for email in recent.filter(
        Email.response_required.is_(True), Email.responded.is_(False),
        Email.tat_breached.is_(False)
    ).limit(500).all():
        received_at = as_utc(email.received_at)
        if not email.tat_minutes or not received_at:
            continue
        deadline = received_at + timedelta(minutes=email.tat_minutes)
        if deadline <= now + timedelta(minutes=30):
            at_risk += 1

    latest = base.order_by(Email.id.desc()).first()
    # Notification freshness is based on Alert.created_at.
    day_start, day_end = _current_ist_day_bounds()
    notification_types = ("ai_attention", "tat_breach", "schedule_miss", "executive_copy", "action_required", "critical_escalation")
    latest_alert = Alert.query.filter(
        Alert.pdl_id.in_(ids) if ids else false(),
        Alert.read.is_(False),
        Alert.alert_type.in_(notification_types),
        Alert.created_at >= day_start, Alert.created_at < day_end,
    ).order_by(Alert.id.desc()).first()
    unread_alerts = Alert.query.filter(
        Alert.pdl_id.in_(ids) if ids else false(),
        Alert.read.is_(False),
        Alert.alert_type.in_(notification_types),
        Alert.created_at >= day_start, Alert.created_at < day_end,
    ).count()

    worker_state = ai_worker_stats()
    return {
        "server_time": now.isoformat(),
        "timezone": "Asia/Kolkata",
        "last_sync": get_sync_state("last_sync"),
        "auto_sync_enabled": sync_setting("auto_sync_enabled", "1") == "1",
        "sync_interval_minutes": max(1, _safe_int(sync_setting("sync_interval_minutes", "1"), 1)),
        "metrics_24h": {
            "total": total_24h,
            "response_required": response_required,
            "critical": critical,
            "high": high,
            "tat_breached": breached,
            "tat_at_risk": at_risk,
            "ai_pending": ai_pending,
            "ai_processing": ai_processing,
            "ai_queued": max(ai_pending, int(worker_state.get("pending", 0))),
            "ai_worker_pending": int(worker_state.get("pending", 0)),
            "ai_review": ai_review,
        },
        "latest_email": ({
            "id": latest.id,
            "subject": latest.subject or "(No subject)",
            "pdl": latest.pdl_name,
            "severity": latest.severity,
            "received_at": latest.received_at.isoformat() if latest.received_at else None,
            "sender": latest.sender_name or latest.sender_email,
            "response_required": bool(latest.response_required),
            "responded": bool(latest.responded),
            "age": email_age_text(latest.received_at, latest.responded, now),
            "url": url_for("email_detail", email_id=latest.id),
        } if latest else None),
        "latest_alert": ({
            "id": latest_alert.id,
            "email_id": latest_alert.email_id,
            "severity": latest_alert.severity,
            "type": latest_alert.alert_type,
            "title": latest_alert.title,
            "message": latest_alert.message,
            "created_at": latest_alert.created_at.isoformat() if latest_alert.created_at else None,
        } if latest_alert else None),
        "unread_alerts": unread_alerts,
    }


@app.route("/api/graph/test")
@login_required
def api_graph_test():
    """Verify Entra ID app-only access to the configured dedicated mailbox."""
    if not graph_enabled():
        return jsonify({
            "success": False,
            "error": "Microsoft Graph mailbox is not configured. Open Settings > Microsoft Graph Mailbox.",
        }), 400
    try:
        folder = GraphMailClient().test_connection()
        cfg = graph_config()
        return jsonify({
            "success": True,
            "mailbox": {
                "address": cfg.get("mailbox", ""),
                "inbox_id": folder.get("id"),
                "displayName": folder.get("displayName"),
                "totalItemCount": folder.get("totalItemCount"),
                "unreadItemCount": folder.get("unreadItemCount"),
            },
        })
    except Exception as exc:
        app.logger.exception("Graph mailbox connection test failed")
        return jsonify({
            "success": False,
            "error": str(exc)[:2000],
        }), 500


@app.route("/api/v1/live")
@login_required
def api_live_snapshot():
    return jsonify({"ok": True, **build_live_snapshot()})


@app.route("/api/v1/notifications")
@login_required
def api_notifications():
    """Return unread actionable notifications newer than the browser cursor."""
    ids = allowed_pdl_ids()
    _sync_notification_read_state(ids)
    after_id = request.args.get("after_id", 0, type=int)
    # Browser notifications are reserved for genuinely actionable high/critical
    # events: escalation, missed customer commitment/call, TAT breach or
    # executive/action escalation. Routine email never reaches this endpoint.
    day_start, day_end = _current_ist_day_bounds()
    q = Alert.query.filter(
        Alert.read.is_(False),
        Alert.id > after_id,
        Alert.created_at >= day_start, Alert.created_at < day_end,
        Alert.alert_type.in_(("critical_escalation", "schedule_miss", "tat_breach", "executive_copy", "action_required")),
        Alert.severity.in_(("high", "critical")),
    )
    if ids:
        q = q.filter(or_(Alert.pdl_id.in_(ids), Alert.pdl_id.is_(None)))
    else:
        q = q.filter(false())
    rows = q.order_by(Alert.id.asc()).limit(200).all()
    # Aggregate TAT notifications by PDL. The Alert table is only the trigger;
    # the displayed count comes from the actual breached Email rows so one
    # summary alert represents the true number of emails requiring action.
    grouped = []
    tat_groups = {}
    for row in rows:
        if row.alert_type == "tat_breach":
            tat_groups.setdefault(row.pdl_id, []).append(row)
        else:
            grouped.append(row)

    for pdl_id, items in tat_groups.items():
        pdl = PDL.query.get(pdl_id) if pdl_id else None
        breached_count = 0
        if pdl_id:
            breached_rows = Email.query.filter(
                Email.pdl_id == pdl_id,
                Email.tat_breached.is_(True),
                Email.response_required.is_(True),
                Email.monitoring_excluded.is_(False),
                Email.received_at.isnot(None),
            ).all()
            breached_count = sum(
                1 for email in breached_rows
                if _email_received_today_ist(email.received_at)
            )

        # Prefer the real current count; fall back to the alert count for
        # backwards compatibility with pre-summary records.
        display_count = breached_count or max(
            len(items),
            1,
        )
        grouped.append({
            "id": max(x.id for x in items),
            "email_id": None,
            "severity": "high",
            "type": "tat_breach",
            "title": f"🔴 {pdl.name if pdl else 'PDL'} — {display_count} emails breached TAT",
            "message": "TAT-breached emails are grouped by PDL. Open Alerts to review and action the underlying email list.",
            "created_at": max(x.created_at for x in items).isoformat() if items else None,
            "url": url_for("alerts", status="unread"),
        })

    grouped.sort(key=lambda x: x.id if isinstance(x, dict) else x.id)
    items=[]
    for row in grouped:
        if isinstance(row, dict):
            items.append(row)
        else:
            items.append({
                "id": row.id, "email_id": row.email_id, "severity": row.severity,
                "type": row.alert_type, "title": row.title, "message": row.message,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "url": url_for("email_detail", email_id=row.email_id) if row.email_id else url_for("alerts"),
            })
    return jsonify({"ok": True, "items": items[:50]})


# ============================================================
# EMAIL DETAIL
# ============================================================

@app.route("/email/<int:email_id>")
@login_required
def email_detail(email_id):

    row = Email.query.get_or_404(
        email_id
    )

    if row.pdl_id not in allowed_pdl_ids():
        abort(403)

    analysis = (AIAnalysis.query.filter_by(email_id=row.id)
                .order_by(AIAnalysis.created_at.desc()).first())
    # AI Operations Intelligence must never be blank merely because Ollama has
    # not completed. The deterministic/hybrid intelligence stored on Email is
    # always available; a completed LLM result enriches/overrides these display
    # fields where appropriate.
    fallback_ai_result = {
        "intent": row.email_category or "general",
        "priority": (row.severity or row.escalation_level or "normal").upper(),
        "response_required": bool(row.response_required),
        "response_requirement": "RESPONSE REQUIRED" if row.response_required and not row.responded else ("RESPONDED" if row.responded else "NO RESPONSE REQUIRED"),
        "customer_frustration": "HIGH" if row.sentiment == "negative" else ("MEDIUM" if row.sentiment == "concerned" else "LOW"),
        "customer_risk": (row.escalation_level or "low").upper(),
        "thread_status": "RESPONDED" if row.responded else ("TAT BREACHED" if row.tat_breached else ("AWAITING RESPONSE" if row.response_required else "INFORMATIONAL")),
        "summary": row.ai_summary or "",
        "recommended_action": row.recommended_action or "Review.",
        "sentiment": row.sentiment or "neutral",
        "recognition": row.email_category == "recognition",
        "escalation": row.email_category == "escalation" or row.escalation_level in {"high", "critical"},
    }
    ai_result = dict(fallback_ai_result)
    if analysis and analysis.status == "COMPLETE":
        ai_result.update({k: v for k, v in (analysis.result or {}).items() if v not in (None, "")})
    conversation_count = Email.query.filter(Email.pdl_id == row.pdl_id).all()
    conversation_count = sum(1 for item in conversation_count if email_conversation_key(item) == email_conversation_key(row))
    row.conversation_count = conversation_count
    return render_template(
        "email_detail.html",
        email=row,
        analysis=analysis,
        ai_result=ai_result,
        ai_source=("ollama" if analysis and analysis.status == "COMPLETE" and not analysis.fallback_used else "hybrid_rules"),
    )



@app.route("/email/<int:email_id>/ai/analyze", methods=["POST"])
@login_required
def ai_analyze_email(email_id):
    row = Email.query.get_or_404(email_id)
    if row.pdl_id not in allowed_pdl_ids(): abort(403)
    # Manual AI analysis may update sentiment/category/workload classification,
    # but it must never hide the email from the mailbox or evaluation view.
    row.visible_in_monitoring = True
    db.session.commit()
    enqueue_ai(app, row.id, force=True)
    flash("AI analysis queued. The email will remain visible while the final AI classification is updated.", "success")
    return redirect(url_for("email_detail", email_id=row.id))


@app.route("/email/<int:email_id>/ai/feedback", methods=["POST"])
@login_required
def ai_feedback(email_id):
    row = Email.query.get_or_404(email_id)
    if row.pdl_id not in allowed_pdl_ids(): abort(403)
    analysis = AIAnalysis.query.filter_by(email_id=row.id).order_by(AIAnalysis.created_at.desc()).first_or_404()
    verdict = (request.form.get("verdict") or "").strip().lower()
    if verdict not in {"correct", "incorrect", "changed"}: abort(400)
    feedback = AIFeedback(analysis_id=analysis.id, email_id=row.id, user_id=current_user.id,
                          verdict=verdict, field_name=(request.form.get("field_name") or "")[:80] or None,
                          original_prediction=request.form.get("original_prediction"),
                          human_correction=request.form.get("human_correction"))
    db.session.add(feedback); db.session.commit()
    flash("AI feedback recorded for controlled evaluation; production models are not auto-retrained.", "success")
    return redirect(url_for("email_detail", email_id=row.id))


@app.route("/api/v1/ai/health")
@login_required
def api_ai_health():
    return jsonify(ai_health())


@app.route("/api/v1/ai/attention")
@login_required
def api_ai_attention():
    ids = allowed_pdl_ids()
    q = Alert.query.filter(Alert.alert_type == "ai_attention", Alert.read.is_(False))
    if ids: q = q.filter(or_(Alert.pdl_id.in_(ids), Alert.pdl_id.is_(None)))
    rows = q.order_by(Alert.created_at.desc()).limit(100).all()
    return jsonify([{"id": r.id, "email_id": r.email_id, "pdl_id": r.pdl_id, "severity": r.severity,
                     "title": r.title, "message": r.message, "created_at": r.created_at.isoformat()} for r in rows])


@app.route("/api/v1/ai/daily-briefing")
@login_required
def api_ai_daily_briefing():
    return jsonify(daily_briefing(dashboard_query(), request.args.get("hours", 24, type=int)))


@app.route("/api/v1/ai/shift-handover")
@login_required
def api_ai_shift_handover():
    return jsonify(shift_handover(dashboard_query(), request.args.get("hours", 8, type=int)))


@app.route("/api/v1/email/<int:email_id>/suggested-reply")
@login_required
def api_ai_suggested_reply(email_id):
    row = Email.query.get_or_404(email_id)
    if row.pdl_id not in allowed_pdl_ids(): abort(403)
    return jsonify(suggested_reply(row))

# ============================================================
# ESCALATIONS
# ============================================================

@app.route("/escalations")
@login_required
def escalations():

    q = (
        dashboard_query()
        .filter(
            Email.escalation_level.in_(
                [
                    "critical",
                    "high",
                    "medium",
                ]
            )
        )
    )

    rows = (
        q.order_by(
            Email.escalation_score.desc(),
            Email.received_at.desc(),
        )
        .limit(1500)
        .all()
    )
    # Escalations are customer/unauthorized-internal signals. A response or
    # outbound message from an engineer who is authorized for this PDL is never
    # an escalation candidate and must not appear as an internal mail.
    rows = [row for row in rows if not _is_pdl_member_sender(row)]
    rows = collapse_conversations(rows)[:500]

    return render_template(
        "escalations.html",
        emails=rows,
    )


# ============================================================
# PDLS
# ============================================================

@app.route("/pdls", methods=["GET", "POST"])
@admin_required
def pdls():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        address = request.form.get("address", "").strip().lower()
        team = request.form.get("team", "").strip()
        owner_name = request.form.get("owner_name", "").strip()
        owner_email = request.form.get("owner_email", "").strip().lower()
        tat = request.form.get("tat_minutes", 240, type=int)

        if not name or not address or "@" not in address:
            flash("PDL name and valid email address are required.", "error")
        elif PDL.query.filter_by(address=address).first():
            flash("That PDL already exists.", "error")
        else:
            db.session.add(PDL(
                name=name, address=address, team=team,
                owner_name=owner_name or None, owner_email=owner_email or None,
                tat_minutes=max(1, tat),
            ))
            db.session.commit()
            log_event("INFO", "pdl_create", f'PDL "{name}" created.')
            flash("PDL created.", "success")
        return redirect(url_for("pdls"))

    rows = PDL.query.order_by(PDL.name).all()
    return render_template("pdls.html", pdls=rows)


@app.route("/pdls/<int:pdl_id>/edit", methods=["POST"])
@admin_required
def edit_pdl(pdl_id):
    pdl = PDL.query.get_or_404(pdl_id)
    name = request.form.get("name", "").strip()
    address = request.form.get("address", "").strip().lower()
    team = request.form.get("team", "").strip()
    owner_name = request.form.get("owner_name", "").strip()
    owner_email = request.form.get("owner_email", "").strip().lower()
    tat = request.form.get("tat_minutes", 240, type=int)

    duplicate = PDL.query.filter(
        PDL.address == address,
        PDL.id != pdl.id,
    ).first()

    if not name or not address or "@" not in address:
        flash("PDL name and valid email address are required.", "error")
    elif duplicate:
        flash("Another PDL already uses that email address.", "error")
    else:
        old_name = pdl.name
        pdl.name, pdl.address, pdl.team = name, address, team
        pdl.owner_name = owner_name or None
        pdl.owner_email = owner_email or None
        pdl.tat_minutes = max(1, tat)
        # Keep historical email display consistent with the renamed PDL.
        for email in pdl.emails:
            email.pdl_name = name
            email.tat_minutes = pdl.tat_minutes
        db.session.commit()
        log_event("INFO", "pdl_edit", f'PDL "{old_name}" updated to "{name}".')
        flash(f'PDL "{name}" updated.', "success")
    return redirect(url_for("pdls"))


@app.route("/pdls/<int:pdl_id>/toggle", methods=["POST"])
@admin_required
def toggle_pdl(pdl_id):
    pdl = PDL.query.get_or_404(pdl_id)
    pdl.active = not pdl.active
    db.session.commit()
    log_event("INFO", "pdl_toggle", f'PDL "{pdl.name}" {"enabled" if pdl.active else "disabled"}.')
    return redirect(url_for("pdls"))


@app.route("/pdls/<int:pdl_id>/delete", methods=["POST"])
@admin_required
def delete_pdl(pdl_id):
    pdl = PDL.query.get_or_404(pdl_id)
    for email in pdl.emails:
        email.pdl_id = None
    pdl_name = pdl.name
    db.session.delete(pdl)
    db.session.commit()
    log_event("WARN", "pdl_delete", f'PDL "{pdl_name}" deleted; email history preserved.')
    flash(f'PDL "{pdl_name}" deleted. Existing email history was preserved.', "success")
    return redirect(url_for("pdls"))



# ============================================================
# MICROSOFT GRAPH MAILBOX SETTINGS
# ============================================================

def _graph_public_config():
    """Return only non-secret mailbox configuration for the UI."""
    try:
        public = GraphMailboxConfig.public()
        if public:
            return public
    except Exception:
        pass

    cfg = graph_config()
    return {
        "tenant_id": cfg.get("tenant_id", ""),
        "client_id": cfg.get("client_id", ""),
        "mailbox": cfg.get("mailbox", ""),
        "configured": bool(cfg.get("mailbox")),
    }


def _graph_form_values(source):
    return {
        "tenant_id": (source.get("tenant_id") or "").strip(),
        "client_id": (source.get("client_id") or "").strip(),
        "client_secret": source.get("client_secret") or "",
        "mailbox": (source.get("mailbox") or "").strip(),
    }


def _validate_graph_values(values):
    missing = [
        label for label, value in (
            ("Tenant ID", values["tenant_id"]),
            ("Application / Client ID", values["client_id"]),
            ("Client Secret", values["client_secret"]),
            ("Mailbox", values["mailbox"]),
        ) if not value
    ]
    if missing:
        raise ValueError("Missing: " + ", ".join(missing))

    if "@" not in values["mailbox"]:
        raise ValueError("Enter a valid dedicated mailbox address.")

    client = GraphMailClient(
        tenant_id=values["tenant_id"],
        client_id=values["client_id"],
        client_secret=values["client_secret"],
        mailbox=values["mailbox"],
    )
    client.test_connection()
    return client


@app.route("/settings/mailbox", methods=["GET"])
@admin_required
def graph_mailbox_settings():
    return render_template(
        "mailbox_settings.html",
        graph_config=_graph_public_config(),
    )


@app.route("/settings/mailbox/test", methods=["POST"])
@admin_required
def graph_mailbox_test():
    data = request.get_json(silent=True) or request.form
    values = _graph_form_values(data)

    # When editing, a blank secret means "use the currently stored secret".
    if not values["client_secret"]:
        current = graph_config()
        values["client_secret"] = current.get("client_secret", "")

    try:
        client = _validate_graph_values(values)
        folder = client.test_connection()
        return jsonify({
            "success": True,
            "message": f"Microsoft Graph connection successful for {values['mailbox']}.",
            "mailbox": {
                "address": values["mailbox"],
                "displayName": folder.get("displayName"),
                "totalItemCount": folder.get("totalItemCount"),
                "unreadItemCount": folder.get("unreadItemCount"),
            },
        })
    except Exception as exc:
        app.logger.warning("Graph mailbox test failed: %s", exc)
        return jsonify({
            "success": False,
            "message": str(exc)[:2000],
        }), 400


@app.route("/settings/mailbox/save", methods=["POST"])
@admin_required
def graph_mailbox_save():
    data = request.get_json(silent=True) or request.form
    values = _graph_form_values(data)

    # Do not force secret rotation when an administrator edits the mailbox,
    # tenant or client ID. Blank means keep the existing encrypted value.
    if not values["client_secret"]:
        current = graph_config()
        values["client_secret"] = current.get("client_secret", "")

    try:
        _validate_graph_values(values)

        GraphMailboxConfig.save(
            tenant_id=values["tenant_id"],
            client_id=values["client_id"],
            client_secret=values["client_secret"],
            mailbox=values["mailbox"],
        )

        # Keep the running process on the UI-managed configuration. Do not
        # rewrite .env and do not expose the secret in the browser.
        set_sync_state("graph_mailbox_configured", "1")
        log_event(
            "INFO",
            "graph_mailbox_config",
            "Microsoft Graph dedicated mailbox configuration updated.",
            values["mailbox"],
        )

        return jsonify({
            "success": True,
            "message": "Microsoft Graph mailbox configuration saved.",
        })
    except Exception as exc:
        app.logger.warning("Graph mailbox save failed: %s", exc)
        return jsonify({
            "success": False,
            "message": str(exc)[:2000],
        }), 400


@app.route("/settings/mailbox/remove", methods=["POST"])
@admin_required
def graph_mailbox_remove():
    GraphMailboxConfig.clear()
    set_sync_state("graph_mailbox_configured", "0")
    log_event(
        "WARN",
        "graph_mailbox_config",
        "Microsoft Graph dedicated mailbox configuration removed.",
    )
    return jsonify({
        "success": True,
        "message": "Microsoft Graph mailbox configuration removed.",
    })



# ============================================================
# IN-APPLICATION RELEASE UPDATES
# ============================================================

UPDATE_ROOT = Path(__file__).resolve().parent / "instance" / "updates"
UPDATE_STAGE_ROOT = UPDATE_ROOT / "staging"
UPDATE_STATUS_FILE = UPDATE_ROOT / "update_status.json"
MAX_UPDATE_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB compressed upload
MAX_UPDATE_EXTRACTED_BYTES = 400 * 1024 * 1024
MAX_UPDATE_FILES = 5000


def _read_update_status():
    try:
        if UPDATE_STATUS_FILE.exists():
            return json.loads(UPDATE_STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"status": "idle"}


def _safe_release_version(value):
    """Return a comparable numeric release tuple for versions like 10.20.24."""
    nums = re.findall(r"\d+", str(value or ""))
    if not nums:
        return (0,)
    return tuple(int(x) for x in nums[:4])


def _detect_release_root(stage_dir):
    """Find the actual release directory in a staged ZIP.

    Accept either:
      package/server.py
    or:
      package/PDL_Email_Intelligence_xxx/server.py
    """
    stage = Path(stage_dir).resolve()
    if (stage / "server.py").is_file() and (stage / "VERSION.txt").is_file():
        return stage

    candidates = []
    for child in stage.iterdir():
        if child.is_dir() and (child / "server.py").is_file() and (child / "VERSION.txt").is_file():
            candidates.append(child)
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError("The ZIP must contain server.py and VERSION.txt at its root or in one release folder.")


def _extract_update_zip(upload):
    """Validate and stage an administrator-provided release ZIP safely."""
    if not upload or not upload.filename:
        raise ValueError("Choose a release ZIP file.")
    if not upload.filename.lower().endswith(".zip"):
        raise ValueError("Only .zip release packages are accepted.")

    UPDATE_STAGE_ROOT.mkdir(parents=True, exist_ok=True)
    stage_dir = UPDATE_STAGE_ROOT / uuid.uuid4().hex
    stage_dir.mkdir(parents=True, exist_ok=False)

    try:
        upload.stream.seek(0, os.SEEK_END)
        size = upload.stream.tell()
        upload.stream.seek(0)
        if size > MAX_UPDATE_UPLOAD_BYTES:
            raise ValueError("Release package is larger than the 100 MB upload limit.")

        with zipfile.ZipFile(upload.stream) as zf:
            bad = zf.testzip()
            if bad:
                raise ValueError(f"Release ZIP is corrupt: {bad}")

            infos = zf.infolist()
            if len(infos) > MAX_UPDATE_FILES:
                raise ValueError(f"Release ZIP contains too many files (maximum {MAX_UPDATE_FILES}).")
            extracted_bytes = 0
            for info in infos:
                extracted_bytes += int(getattr(info, "file_size", 0) or 0)
                if extracted_bytes > MAX_UPDATE_EXTRACTED_BYTES:
                    raise ValueError("Release ZIP expands beyond the 400 MB safety limit.")
                # ZIP paths are untrusted input. Never allow absolute paths or
                # .. traversal to escape the staging directory.
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                    raise ValueError("Release ZIP contains an unsafe absolute path.")
                target = (stage_dir / name).resolve()
                if target != stage_dir and stage_dir not in target.parents:
                    raise ValueError("Release ZIP contains an unsafe path.")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info, "r") as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)

        release_root = _detect_release_root(stage_dir)
        required = ["server.py", "app.py", "requirements.txt", "VERSION.txt", "templates/base.html"]
        missing = [item for item in required if not (release_root / item).exists()]
        if missing:
            raise ValueError("Release package is missing: " + ", ".join(missing))

        version = (release_root / "VERSION.txt").read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if not version:
            raise ValueError("VERSION.txt is empty.")

        current = str(app.config.get("APP_VERSION", "0")).strip()
        if _safe_release_version(version) <= _safe_release_version(current):
            raise ValueError(f"Uploaded release {version} is not newer than the running release {current}.")

        # Optional package manifest. If present, it is informational for the
        # administrator and can be shown in the upgrade log.
        notes = release_root / f"V{version.replace('.', '_')}_RELEASE_NOTES.md"
        if not notes.exists():
            notes = release_root / "RELEASE_NOTES.md"

        return stage_dir, release_root, version, notes.read_text(encoding="utf-8", errors="ignore")[:12000] if notes.exists() else ""
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def _write_update_status(payload):
    try:
        UPDATE_ROOT.mkdir(parents=True, exist_ok=True)
        tmp = UPDATE_STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, UPDATE_STATUS_FILE)
    except Exception as exc:
        app.logger.warning("Could not write update status: %s", exc)


@app.route("/admin/update/upload", methods=["POST"])
@admin_required
def admin_update_upload():
    """Stage a release and hand the actual file replacement to a detached helper.

    The running Flask process is never asked to overwrite its own loaded Python
    modules. The helper waits for this process and its app.py launcher to exit,
    creates a backup, replaces application code, then starts app.py again.
    Runtime data (.env, instance, .venv, database and encrypted Graph secret)
    is preserved.
    """
    try:
        stage_dir, release_root, version, notes = _extract_update_zip(request.files.get("release_zip"))

        UPDATE_ROOT.mkdir(parents=True, exist_ok=True)
        current = str(app.config.get("APP_VERSION", "0"))
        launcher_pid = int(os.getenv("PDL_LAUNCHER_PID", "0") or 0)

        _write_update_status({
            "status": "scheduled",
            "current_version": current,
            "target_version": version,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "message": "Release validated and scheduled. The application will restart automatically.",
            "release_notes": notes,
        })

        helper = Path(__file__).resolve().parent / "release_updater.py"
        if not helper.exists():
            raise RuntimeError("release_updater.py is missing from the application package.")

        # Detached helper survives this Flask process and performs the
        # replacement only after both server.py and app.py have exited.
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

        subprocess.Popen(
            [
                sys.executable,
                str(helper),
                "--stage", str(release_root),
                "--stage-parent", str(stage_dir),
                "--server-pid", str(os.getpid()),
                "--launcher-pid", str(launcher_pid),
                "--current-version", current,
                "--target-version", version,
            ],
            cwd=str(Path(__file__).resolve().parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )

        def _exit_for_upgrade():
            time.sleep(0.8)
            os._exit(0)

        import threading
        threading.Thread(target=_exit_for_upgrade, daemon=True).start()

        return jsonify({
            "success": True,
            "message": f"Release {version} validated. The application is restarting to apply the update.",
            "version": version,
        })
    except Exception as exc:
        _write_update_status({
            "status": "failed",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "message": str(exc)[:2000],
        })
        app.logger.warning("Application update failed before restart: %s", exc)
        return jsonify({"success": False, "message": str(exc)[:2000]}), 400


@app.route("/admin/update/status")
@admin_required
def admin_update_status():
    return jsonify(_read_update_status())



# ============================================================
# ABOUT
# ============================================================

@app.route("/about")
@login_required
def about():
    ai_cfg = ai_settings()
    return render_template(
        "about.html",
        app_version=app.config.get("APP_VERSION", "9.5.0"),
        ai_status=ai_health(),
        ai_provider=ai_cfg.get("AI_PROVIDER", "local"),
        ai_model=ai_cfg.get("AI_MODEL", "configured"),
        outlook_timezone=app.config.get("OUTLOOK_TIMEZONE", "Asia/Kolkata"),
    )


# ============================================================
# SETTINGS
# ============================================================

@app.route(
    "/settings",
    methods=["GET", "POST"],
)
@login_required
def settings():

    pdls = (
        PDL.query
        .filter_by(active=True)
        .order_by(PDL.name)
        .all()
    )

    if request.method == "POST":

        ids = {
            int(x)
            for x in request.form
            .getlist("pdl_ids")
        }

        current_user.pdls = [
            p
            for p in pdls
            if p.id in ids
        ]

        db.session.commit()

        flash(
            "Your PDL monitoring selection has been updated.",
            "success",
        )

        return redirect(
            url_for("settings")
        )

    return render_template(
        "settings.html",
        pdls=pdls,
    )


# ============================================================
# USERS
# ============================================================

@app.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "analyst").strip().lower()
        if role not in {"admin", "manager", "analyst"}:
            role = "analyst"

        if not name or not email or not password:
            flash("Name, email and password are required.", "error")
        elif User.query.filter_by(email=email).first():
            flash("User already exists.", "error")
        else:
            user = User(name=name, email=email, role=role, active=True)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
            if role == "manager":
                team_ids = {int(x) for x in request.form.getlist("team_ids") if str(x).isdigit()}
                user.managed_teams = TeamGroup.query.filter(TeamGroup.id.in_(team_ids)).all() if team_ids else []
            db.session.commit()
            log_event("INFO", "user_create", f'User "{email}" created.')
            flash("User created.", "success")
        return redirect(url_for("users"))

    return render_template(
        "users.html",
        users=User.query.order_by(User.name).all(),
        pdls=PDL.query.order_by(PDL.name).all(),
        teams=TeamGroup.query.filter_by(active=True).order_by(TeamGroup.name).all(),
    )


@app.route("/users/<int:user_id>/edit", methods=["POST"])
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "analyst").strip().lower()
    if role not in {"admin", "manager", "analyst"}:
        role = "analyst"
    password = request.form.get("password", "")

    duplicate = User.query.filter(User.email == email, User.id != user.id).first()
    if not name or not email:
        flash("Name and email are required.", "error")
    elif duplicate:
        flash("Another user already uses that email address.", "error")
    elif user.id == current_user.id and role != "admin":
        flash("You cannot remove your own administrator role.", "error")
    else:
        user.name, user.email, user.role = name, email, role
        if role != "manager":
            user.managed_teams = []
        if password:
            user.set_password(password)
        db.session.commit()
        log_event("INFO", "user_edit", f'User "{email}" updated.')
        flash(f'User "{email}" updated.', "success")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/password", methods=["POST"])
@admin_required
def change_user_password(user_id):
    """Change a user's password from the dedicated administrator dialog."""
    user = User.query.get_or_404(user_id)
    password = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")

    if not password:
        flash("New password is required.", "error")
    elif len(password) < 8:
        flash("Password must be at least 8 characters long.", "error")
    elif password != confirm:
        flash("New password and confirmation do not match.", "error")
    else:
        user.set_password(password)
        db.session.commit()
        log_event("INFO", "user_password_change", f'Password changed for user "{user.email}".')
        flash(f'Password changed for {user.name}.', "success")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("users"))
    email = user.email
    db.session.delete(user)
    db.session.commit()
    log_event("WARN", "user_delete", f'User "{email}" deleted.')
    flash(f'User "{email}" deleted.', "success")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/pdls", methods=["POST"])
@admin_required
def assign_user_pdls(user_id):
    user = User.query.get_or_404(user_id)
    ids = {int(x) for x in request.form.getlist("pdl_ids")}
    user.pdls = PDL.query.filter(PDL.id.in_(ids)).all() if ids else []
    db.session.commit()
    log_event("INFO", "user_pdl_mapping", f'PDL access updated for user "{user.email}".')
    flash(f"PDL access updated for {user.name}.", "success")
    return redirect(url_for("users"))


@app.route("/users/<int:user_id>/teams", methods=["POST"])
@admin_required
def assign_user_teams(user_id):
    user = User.query.get_or_404(user_id)
    if user.role != "manager":
        flash("Team assignment is available only for Manager users.", "error")
        return redirect(url_for("users"))
    ids = {int(x) for x in request.form.getlist("team_ids") if str(x).isdigit()}
    user.managed_teams = TeamGroup.query.filter(
        TeamGroup.id.in_(ids),
        TeamGroup.active.is_(True),
    ).all() if ids else []
    db.session.commit()
    log_event("INFO", "user_team_mapping", f'Team scope updated for manager "{user.email}".')
    flash(f"Team scope updated for {user.name}.", "success")
    return redirect(url_for("users"))


# ============================================================
# ENGINEERS / ENGINEER MAPPING
# ============================================================

def _engineer_role_from_form():
    role = (request.form.get("contact_role") or "engineer").strip().lower()
    return role if role in {"engineer", "manager", "executive"} else "engineer"


@app.route("/engineers", methods=["GET", "POST"])
@admin_required
def engineers():
    """Deprecated standalone engineer mapping. Team Management is authoritative now."""
    flash("Engineer Mapping has been replaced by Team Management. Manage engineers from Team Management.", "info")
    return redirect(url_for("team_management"))


@app.route("/engineer-groups/<int:group_id>/edit", methods=["POST"])
@admin_required
def edit_engineer_group(group_id):
    group = EngineerGroup.query.get_or_404(group_id)
    name = request.form.get("group_name", "").strip()
    pdl_id = request.form.get("group_pdl_id", type=int)
    duplicate = EngineerGroup.query.filter(
        func.lower(EngineerGroup.name) == name.lower(), EngineerGroup.id != group.id
    ).first() if name else None
    if not name:
        flash("Group name is required.", "error")
    elif duplicate:
        flash("Another engineer group already uses that name.", "error")
    elif pdl_id and not PDL.query.get(pdl_id):
        flash("Selected PDL does not exist.", "error")
    else:
        group.name = name
        group.pdl_id = pdl_id
        db.session.commit()
        log_event("INFO", "engineer_group_edit", f'Engineer group "{name}" updated.')
        flash("Engineer group updated.", "success")
    return redirect(url_for("engineers"))


@app.route("/engineer-groups/<int:group_id>/delete", methods=["POST"])
@admin_required
def delete_engineer_group(group_id):
    group = EngineerGroup.query.get_or_404(group_id)
    name = group.name
    for engineer in list(group.engineers):
        engineer.group_id = None
    db.session.delete(group)
    db.session.commit()
    log_event("WARN", "engineer_group_delete", f'Engineer group "{name}" deleted; members retained ungrouped.')
    flash("Group deleted. Existing engineer mappings were retained.", "success")
    return redirect(url_for("engineers"))


@app.route("/engineers/<int:engineer_id>/edit", methods=["POST"])
@admin_required
def edit_engineer(engineer_id):
    engineer = Engineer.query.get_or_404(engineer_id)
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    team = request.form.get("team", "").strip()
    designation = request.form.get("designation", "").strip()
    group_id = request.form.get("group_id", type=int)
    contact_role = _engineer_role_from_form()
    is_executive = contact_role == "executive" or request.form.get("is_executive") == "1"
    duplicate = Engineer.query.filter(Engineer.email == email, Engineer.id != engineer.id).first()

    if not name or not email:
        flash("Engineer name and email are required.", "error")
    elif duplicate:
        flash("Another engineer already uses that email address.", "error")
    elif group_id and not EngineerGroup.query.get(group_id):
        flash("Selected engineer group does not exist.", "error")
    else:
        old_email = engineer.email
        engineer.name, engineer.email, engineer.team = name, email, team
        engineer.designation = designation or None
        engineer.is_executive = is_executive
        engineer.contact_role = contact_role
        engineer.group_id = group_id
        db.session.commit()
        log_event("INFO", "engineer_edit", f'Engineer mapping "{old_email}" updated to "{email}".')
        flash(f'Engineer "{name}" updated.', "success")
    return redirect(url_for("engineers"))


@app.route("/engineers/<int:engineer_id>/delete", methods=["POST"])
@admin_required
def delete_engineer(engineer_id):
    engineer = Engineer.query.get_or_404(engineer_id)
    email = engineer.email
    db.session.delete(engineer)
    db.session.commit()
    log_event("WARN", "engineer_delete", f'Engineer mapping "{email}" deleted.')
    flash(f'Engineer "{email}" deleted.', "success")
    return redirect(url_for("engineers"))



# ============================================================
# MONITORING SLOT ACTIVITY / NOTIFICATIONS
# ============================================================

def _send_monitoring_assignment_notification(pdl, slot, engineer):
    """Notify the assigned engineer through the local Outlook desktop client.

    The application does not require SMTP credentials. Because the project already
    depends on pywin32 for Outlook collection, the same Outlook profile is used to
    send a lightweight assignment notification. Failure to send never rolls back
    the monitoring assignment.
    """
    if not getattr(Config, "MONITORING_ASSIGNMENT_NOTIFY_ENABLED", True):
        return False, "Notification disabled"
    if not engineer or not engineer.email:
        return False, "Engineer has no email address"
    com_initialized = False
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore

        # Flask request handlers may execute on worker threads. pywin32 COM
        # objects require COM to be initialized on the current thread before
        # Dispatch/CreateItem/Send are called. The Outlook sync path already
        # does this, but assignment notifications are initiated from an HTTP
        # request, so initialize COM explicitly here as well.
        pythoncom.CoInitialize()
        com_initialized = True

        outlook = win32com.client.Dispatch("Outlook.Application")
        message = outlook.CreateItem(0)
        message.To = engineer.email
        message.Subject = f"Monitoring slot assigned: {pdl.name}"
        app_url = getattr(Config, "MONITORING_APP_URL", "").strip().rstrip("/")
        activity_path = f"/monitoring-slots/{slot.id}/activities"
        activity_url = f"{app_url}{activity_path}" if app_url else activity_path
        message.Body = (
            f"Hello {engineer.name or 'Engineer'},\n\n"
            f"A monitoring slot has been assigned to you.\n\n"
            f"PDL: {pdl.name}\n"
            f"Group: {slot.group.name if slot.group else '—'}\n"
            f"Monitoring slot: {slot.name}\n"
            f"Time: {slot.start_time.strftime('%H:%M')} – {slot.end_time.strftime('%H:%M')} IST\n\n"
            f"View monitoring slot activities: {activity_url}\n\n"
            "Please monitor the assigned PDL during the configured slot and ensure customer emails are attended within TAT.\n"
        )
        message.Send()
        return True, None
    except Exception as exc:
        return False, str(exc)
    finally:
        if com_initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def _slot_activity_pdl_ids(slot):
    ids = {p.id for p in slot.pdls}
    history = PDLMonitoringAssignmentHistory.query.filter_by(slot_id=slot.id).all()
    ids.update(h.pdl_id for h in history if h.pdl_id)
    return sorted(ids)


def monitoring_slot_activity_report(slot, range_ctx=None):
    pdl_ids = _slot_activity_pdl_ids(slot)
    rows = []
    totals = {"received": 0, "attended": 0, "unattended": 0, "tat_breached": 0}
    for pdl_id in pdl_ids:
        pdl = PDL.query.get(pdl_id)
        if not pdl:
            continue
        stats = monitoring_slot_stats(slot, pdl.id, range_ctx)
        stats["pdl"] = pdl
        rows.append(stats)
        for key in totals:
            totals[key] += stats[key]
    totals["response_rate"] = round(totals["attended"] / totals["received"] * 100, 1) if totals["received"] else 0
    totals["compliance"] = round((totals["received"] - totals["tat_breached"]) / totals["received"] * 100, 1) if totals["received"] else 0
    return totals, rows



def _report_assignment_for_email(email, slot, pdl_id):
    """Resolve the engineer who actually owned the PDL/slot at receipt time.

    Historical assignment history is the only source of truth for reporting.
    Never fall back to the current assignment because that rewrites past
    workload when a PDL is assigned today.
    """
    received = as_utc(email.received_at)
    if received is None:
        return None

    histories = PDLMonitoringAssignmentHistory.query.filter(
        PDLMonitoringAssignmentHistory.slot_id == slot.id,
        PDLMonitoringAssignmentHistory.pdl_id == pdl_id,
        PDLMonitoringAssignmentHistory.assigned_at <= received,
        or_(
            PDLMonitoringAssignmentHistory.ended_at.is_(None),
            PDLMonitoringAssignmentHistory.ended_at > received,
        ),
    ).order_by(PDLMonitoringAssignmentHistory.assigned_at.desc()).all()

    if histories:
        h = histories[0]
        return {
            "engineer_id": h.engineer_id,
            "engineer_name": h.engineer_name or "Unassigned",
            "engineer_email": h.engineer_email,
            "assigned_at": h.assigned_at,
        }

    # No assignment covered the receipt time. This email is NOT part of the
    # assigned monitoring workload. Do not use the current assignment.
    return None


def monitoring_report_data(range_ctx=None):
    """Build auditable slot/day/engineer reporting from the email records.

    Counts use exactly the same workload semantics as the dashboard:
    PDL-mapped, monitoring_excluded=False and PDL-member/internal messages
    excluded. Each email is counted only in slots whose configured time window
    contains the email's IST receipt time and whose PDL was assigned to that
    slot at that time.
    """
    range_ctx = range_ctx or metric_range_context()
    ids = set(allowed_pdl_ids())
    if not ids:
        return {
            "totals": {"received": 0, "responded": 0, "pending": 0, "breached": 0, "response_rate": 0, "compliance": 0},
            "daily": [], "engineers": [], "slots": [], "pdls": [],
        }

    q = Email.query.filter(
        Email.pdl_id.in_(ids),
        Email.monitoring_excluded.is_(False),
    )
    q = _exclude_pdl_member_messages(q, list(ids))
    q = apply_metric_range(q, range_ctx)
    emails = q.order_by(Email.received_at.asc()).all()

    # Keep inactive slots if they have history so historical reports remain auditable.
    slots = MonitoringSlot.query.order_by(MonitoringSlot.name).all()
    allowed_slots = []
    for slot in slots:
        pdl_ids = set(_slot_activity_pdl_ids(slot)) & ids
        if pdl_ids:
            allowed_slots.append((slot, pdl_ids))

    daily = {}
    engineer_rows = {}
    slot_rows = {}
    pdl_rows = {}
    totals = {"received": 0, "responded": 0, "pending": 0, "breached": 0}

    def add(bucket, email):
        bucket["received"] += 1
        if email.responded:
            bucket["responded"] += 1
        else:
            bucket["pending"] += 1
        if email.tat_breached:
            bucket["breached"] += 1

    for email in emails:
        # Count an email once. If overlapping slots exist, use the assignment
        # with the latest assignment timestamp rather than double-counting.
        candidates = []
        for slot, pdl_ids in allowed_slots:
            if email.pdl_id not in pdl_ids or not _slot_match_email(email, slot):
                continue
            assignment = _report_assignment_for_email(email, slot, email.pdl_id)
            if assignment:
                candidates.append((assignment.get("assigned_at"), slot, assignment))
        if not candidates:
            continue
        _, slot, assignment = max(
            candidates,
            key=lambda item: item[0] or datetime.min.replace(tzinfo=timezone.utc),
        )

        engineer_name = assignment.get("engineer_name") or "Unassigned"
        engineer_id = assignment.get("engineer_id")
        local_date = as_utc(email.received_at).astimezone(IST).date()
        pdl = email.pdl_name or f"PDL {email.pdl_id}"
        eng_key = (engineer_id, engineer_name)

        totals["received"] += 1
        if email.responded:
            totals["responded"] += 1
        else:
            totals["pending"] += 1
        if email.tat_breached:
            totals["breached"] += 1

        dkey = (local_date, slot.id, slot.name, eng_key, pdl)
        row = daily.setdefault(dkey, {
            "date": local_date, "slot_id": slot.id, "slot": slot.name,
            "engineer_id": engineer_id, "engineer": engineer_name,
            "pdl": pdl, "received": 0, "responded": 0, "pending": 0, "breached": 0,
        })
        add(row, email)

        ekey = (eng_key[0], eng_key[1])
        erow = engineer_rows.setdefault(ekey, {
            "engineer_id": engineer_id, "engineer": engineer_name,
            "received": 0, "responded": 0, "pending": 0, "breached": 0,
        })
        add(erow, email)

        skey = (slot.id, slot.name)
        srow = slot_rows.setdefault(skey, {
            "slot_id": slot.id, "slot": slot.name,
            "received": 0, "responded": 0, "pending": 0, "breached": 0,
        })
        add(srow, email)

        prow = pdl_rows.setdefault(email.pdl_id, {
            "pdl_id": email.pdl_id, "pdl": pdl,
            "received": 0, "responded": 0, "pending": 0, "breached": 0,
        })
        add(prow, email)

    def finish(rows):
        for row in rows:
            r = row["received"]
            row["response_rate"] = round(row["responded"] / r * 100, 1) if r else 0
            row["compliance"] = round((r - row["breached"]) / r * 100, 1) if r else 0
        return rows

    total_received = totals["received"]
    totals["response_rate"] = round(totals["responded"] / total_received * 100, 1) if total_received else 0
    totals["compliance"] = round((total_received - totals["breached"]) / total_received * 100, 1) if total_received else 0

    return {
        "totals": totals,
        "daily": sorted(finish(list(daily.values())), key=lambda x: (x["date"], x["slot"], x["pdl"], x["engineer"])),
        "engineers": sorted(finish(list(engineer_rows.values())), key=lambda x: x["engineer"].lower()),
        "slots": sorted(finish(list(slot_rows.values())), key=lambda x: x["slot"].lower()),
        "pdls": sorted(finish(list(pdl_rows.values())), key=lambda x: x["pdl"].lower()),
    }


@app.route("/reports")
@login_required
def reports():
    # Reports open on the current 24-hour operational window; users can switch
    # to week/month/custom for historical analysis.
    range_ctx = metric_range_context(default="24h")
    data = monitoring_report_data(range_ctx)
    return render_template("reports.html", report=data, metric_range=range_ctx)


def monitoring_report_email_rows(range_ctx=None, status="pending", engineer_id=None, slot_id=None, pdl_id=None, day=None):
    """Return the exact emails represented by a monitoring report bucket."""
    range_ctx = range_ctx or metric_range_context(default="24h")
    ids = set(allowed_pdl_ids())
    if not ids:
        return []
    q = Email.query.filter(Email.pdl_id.in_(ids), Email.monitoring_excluded.is_(False))
    q = _exclude_pdl_member_messages(q, list(ids))
    q = apply_metric_range(q, range_ctx)
    emails = q.order_by(Email.received_at.desc()).all()
    slots = MonitoringSlot.query.order_by(MonitoringSlot.name).all()
    allowed_slots = []
    for slot in slots:
        pdl_ids = set(_slot_activity_pdl_ids(slot)) & ids
        if pdl_ids:
            allowed_slots.append((slot, pdl_ids))
    wanted_day = None
    if day:
        try:
            wanted_day = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            pass
    result = []
    for email in emails:
        if status == "pending" and email.responded:
            continue
        if status == "responded" and not email.responded:
            continue
        if status == "breached" and not email.tat_breached:
            continue
        if pdl_id and email.pdl_id != pdl_id:
            continue
        local_date = as_utc(email.received_at).astimezone(IST).date() if email.received_at else None
        if wanted_day and local_date != wanted_day:
            continue
        candidates = []
        for slot, pdl_ids in allowed_slots:
            if email.pdl_id not in pdl_ids or not _slot_match_email(email, slot):
                continue
            if slot_id and slot.id != slot_id:
                continue
            assignment = _report_assignment_for_email(email, slot, email.pdl_id)
            if assignment:
                candidates.append((assignment.get("assigned_at"), slot, assignment))
        if not candidates:
            continue
        _, slot, assignment = max(candidates, key=lambda item: item[0] or datetime.min.replace(tzinfo=timezone.utc))
        if engineer_id and assignment.get("engineer_id") != engineer_id:
            continue
        result.append({"email": email, "slot": slot, "assignment": assignment, "local_date": local_date})
    return result


@app.route("/reports/emails")
@login_required
def report_email_details():
    range_ctx = metric_range_context(default="24h")
    status = (request.args.get("status") or "pending").strip().lower()
    if status not in {"pending", "responded", "breached", "all"}:
        status = "pending"
    rows = monitoring_report_email_rows(
        range_ctx, status, request.args.get("engineer_id", type=int),
        request.args.get("slot_id", type=int), request.args.get("pdl_id", type=int),
        (request.args.get("day") or "").strip(),
    )
    return render_template("report_email_details.html", rows=rows, metric_range=range_ctx, status=status)


@app.route("/ai-command-center")
@login_required
def ai_command_center():
    # Backward-compatible redirect: the former AI Command Center duplicated
    # dashboard KPIs. Reporting is now the single operational analysis page.
    return redirect(url_for("reports", range=request.args.get("range", "24h"),
                            start_date=request.args.get("start_date", ""),
                            end_date=request.args.get("end_date", "")))


@app.route("/monitoring-slots/<int:slot_id>/activities")
@login_required
def monitoring_slot_activity(slot_id):
    slot = MonitoringSlot.query.get_or_404(slot_id)
    if current_user.role != "admin":
        allowed = set(allowed_pdl_ids())
        visible_pdl_ids = set(_slot_activity_pdl_ids(slot))
        if not (allowed & visible_pdl_ids):
            abort(403)
    if current_user.role == "manager" and slot.group_id not in managed_team_ids():
        abort(403)
    range_ctx = metric_range_context(default="24h")
    totals, pdl_rows = monitoring_slot_activity_report(slot, range_ctx)
    history = PDLMonitoringAssignmentHistory.query.filter_by(slot_id=slot.id).order_by(PDLMonitoringAssignmentHistory.assigned_at.desc()).limit(200).all()
    return render_template(
        "monitoring_slot_activity.html",
        slot=slot,
        totals=totals,
        pdl_rows=pdl_rows,
        history=history,
        metric_range=range_ctx,
    )


# ============================================================
# TEAM MANAGEMENT
# ============================================================

def _parse_slot_time(value):
    try:
        return datetime.strptime((value or "").strip(), "%H:%M").time()
    except ValueError:
        return None

def _close_open_monitoring_history(pdl_id, ended_at=None, reason="removed", slot_id=None):
    ended_at = ended_at or datetime.now(timezone.utc)
    rows = PDLMonitoringAssignmentHistory.query.filter(
        PDLMonitoringAssignmentHistory.pdl_id == pdl_id,
        PDLMonitoringAssignmentHistory.ended_at.is_(None),
    ).all()
    if slot_id is not None:
        rows = [item for item in rows if item.slot_id == slot_id]
    for item in rows:
        item.ended_at = ended_at
        item.end_reason = reason
    return rows

def _record_monitoring_assignment_history(pdl, slot, engineer, assigned_at):
    return PDLMonitoringAssignmentHistory(
        pdl_id=pdl.id,
        slot_id=slot.id,
        engineer_id=engineer.id if engineer else None,
        group_id=slot.group_id,
        pdl_name=pdl.name,
        slot_name=slot.name,
        start_time=slot.start_time,
        end_time=slot.end_time,
        group_name=slot.group.name if slot.group else None,
        engineer_name=engineer.name if engineer else None,
        engineer_email=engineer.email if engineer else None,
        assigned_at=assigned_at,
    )

@app.route("/team-management", methods=["GET", "POST"])
@manager_or_admin_required
def team_management():
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "create_group":
            name = request.form.get("name", "").strip()
            manager = request.form.get("manager_name", "").strip()
            if not name:
                flash("Group name is required.", "error")
            elif TeamGroup.query.filter(func.lower(TeamGroup.name) == name.lower()).first():
                flash("That team group already exists.", "error")
            else:
                group = TeamGroup(name=name, manager_name=manager or (current_user.name if current_user.role == "manager" else None))
                db.session.add(group)
                db.session.flush()
                if current_user.role == "manager" and group not in current_user.managed_teams:
                    current_user.managed_teams.append(group)
                db.session.commit()
                flash("Team group created.", "success")
            return redirect(url_for("team_management"))
        if action == "create_executive_group":
            if current_user.role != "admin":
                abort(403)
            name = request.form.get("name", "").strip()
            if not name:
                flash("Executive group name is required.", "error")
            elif ExecutiveGroup.query.filter(func.lower(ExecutiveGroup.name) == name.lower()).first():
                flash("That executive group already exists.", "error")
            else:
                db.session.add(ExecutiveGroup(name=name, description=request.form.get("description", "").strip() or None))
                db.session.commit()
                flash("Executive group created.", "success")
            return redirect(url_for("team_management"))
    team_search = (request.args.get("q") or "").strip().lower()
    if current_user.role == "admin":
        groups = TeamGroup.query.order_by(TeamGroup.name).all()
        executive_groups = ExecutiveGroup.query.order_by(ExecutiveGroup.name).all()
        engineers = Engineer.query.filter_by(active=True).order_by(Engineer.name).all()
        pdls = PDL.query.filter_by(active=True).order_by(PDL.name).all()
    else:
        group_ids = managed_team_ids()
        groups = TeamGroup.query.filter(TeamGroup.id.in_(group_ids)).order_by(TeamGroup.name).all() if group_ids else []
        executive_groups = []
        engineer_ids = {e.id for g in groups for e in (g.engineers or [])}
        pdl_ids = {p.id for g in groups for p in (g.pdls or [])}
        engineers = Engineer.query.filter(Engineer.id.in_(engineer_ids)).order_by(Engineer.name).all() if engineer_ids else []
        pdls = PDL.query.filter(PDL.id.in_(pdl_ids)).order_by(PDL.name).all() if pdl_ids else []
    if team_search:
        def group_matches(group):
            hay = [group.name or "", group.manager_name or ""]
            hay += [p.name or "" for p in (group.pdls or [])]
            hay += [e.name or "" for e in (group.engineers or [])]
            hay += [e.email or "" for e in (group.engineers or [])]
            return team_search in " ".join(hay).lower()
        groups = [g for g in groups if group_matches(g)]
        # The global engineer/PDL lists remain useful in the page, but are also
        # narrowed when searching so a large 40-50 PDL installation stays usable.
        engineers = [e for e in engineers if team_search in ((e.name or "") + " " + (e.email or "") + " " + (e.team or "")).lower()]
        pdls = [p for p in pdls if team_search in ((p.name or "") + " " + (p.address or "")).lower()]
    return render_template("team_management.html", groups=groups, executive_groups=executive_groups, engineers=engineers, pdls=pdls, team_search=team_search)

@app.route("/team-management/<int:group_id>/engineers/template", methods=["GET"])
@manager_or_admin_required
def download_engineer_csv_template(group_id):
    group = TeamGroup.query.get_or_404(group_id)
    if not can_manage_team(group):
        abort(403)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Email", "Team", "Designation"])
    writer.writerow(["", "", "", ""])
    response = make_response("\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = f'attachment; filename="team_group_{group_id}_engineers_template.csv"'
    return response

@app.route("/team-management/<int:group_id>", methods=["GET", "POST"])
@manager_or_admin_required
def team_group_detail(group_id):
    group = TeamGroup.query.get_or_404(group_id)
    if not can_manage_team(group):
        abort(403)
    manager_pdl_ids = {p.id for p in (group.pdls or [])} if current_user.role == "manager" else None
    manager_visible_engineer_ids = (
        {
            engineer.id
            for managed_group in (current_user.managed_teams or [])
            for engineer in (managed_group.engineers or [])
        }
        if current_user.role == "manager"
        else None
    )

    def scoped_form_pdl_ids(field):
        ids = {int(x) for x in request.form.getlist(field) if str(x).isdigit()}
        if manager_pdl_ids is not None:
            ids &= manager_pdl_ids
        return ids

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "update_group":
            name = request.form.get("name", "").strip()
            manager = request.form.get("manager_name", "").strip()
            if not name:
                flash("Group name is required.", "error")
            else:
                group.name, group.manager_name = name, manager or None
                db.session.commit(); flash("Group updated.", "success")
        elif action == "add_engineer":
            engineer = Engineer.query.get(request.form.get("engineer_id", type=int))
            if current_user.role == "manager" and engineer and engineer.id not in manager_visible_engineer_ids:
                abort(403)
            if not engineer:
                name = request.form.get("engineer_name", "").strip()
                email = request.form.get("engineer_email", "").strip().lower()
                team = request.form.get("engineer_team", "").strip()
                designation = request.form.get("engineer_designation", "").strip()
                if not name or not email or "@" not in email:
                    flash("Engineer name and valid email are required.", "error")
                    return redirect(url_for("team_group_detail", group_id=group.id))
                engineer = Engineer.query.filter_by(email=email).first()
                if current_user.role == "manager" and engineer is not None and engineer.id not in manager_visible_engineer_ids:
                    flash("That email already belongs to an engineer outside your managed teams.", "error")
                    return redirect(url_for("team_group_detail", group_id=group.id))
                if engineer is None:
                    engineer = Engineer(name=name, email=email, team=team or None, designation=designation or None, contact_role="engineer")
                    db.session.add(engineer); db.session.flush()
                else:
                    engineer.name, engineer.team = name, team or engineer.team
                    if designation:
                        engineer.designation = designation
            if engineer not in group.engineers: group.engineers.append(engineer)
            pdl_ids = scoped_form_pdl_ids("pdl_access")
            selected = PDL.query.filter(PDL.id.in_(pdl_ids)).all() if pdl_ids else []
            # PDL access is additive so an engineer can serve multiple groups/PDLs.
            existing = {p.id for p in engineer.pdl_access}
            engineer.pdl_access = list(engineer.pdl_access) + [p for p in selected if p.id not in existing]
            db.session.commit(); flash(f"{engineer.name} added to {group.name}.", "success")
        elif action == "remove_engineer":
            engineer = Engineer.query.get_or_404(request.form.get("engineer_id", type=int))
            if engineer in group.engineers: group.engineers.remove(engineer); db.session.commit()
            flash("Engineer removed from group.", "success")
        elif action == "edit_engineer_member":
            engineer = Engineer.query.get_or_404(request.form.get("engineer_id", type=int))
            if engineer not in group.engineers:
                flash("That engineer is not a member of this group.", "error")
            else:
                name = request.form.get("edit_name", "").strip()
                email = request.form.get("edit_email", "").strip().lower()
                team = request.form.get("edit_team", "").strip()
                designation = request.form.get("edit_designation", "").strip()
                duplicate = Engineer.query.filter(Engineer.email == email, Engineer.id != engineer.id).first() if email else None
                if not name or not email or "@" not in email:
                    flash("Name and a valid email address are required.", "error")
                elif duplicate:
                    flash("Another engineer already uses that email address.", "error")
                else:
                    engineer.name = name
                    engineer.email = email
                    engineer.team = team or None
                    engineer.designation = designation or None
                    pdl_ids = scoped_form_pdl_ids("edit_pdl_access")
                    selected_pdls = PDL.query.filter(PDL.id.in_(pdl_ids)).all() if pdl_ids else []
                    if current_user.role == "manager":
                        # Preserve PDL grants that belong to other teams/admin scope.
                        outside_scope = [p for p in engineer.pdl_access if p.id not in manager_pdl_ids]
                        engineer.pdl_access = outside_scope + selected_pdls
                    else:
                        engineer.pdl_access = selected_pdls
                    db.session.commit()
                    flash(f"{engineer.name} updated. PDL access saved.", "success")
        elif action == "bulk_import_engineers":
            upload = request.files.get("engineers_csv")
            pdl_ids = scoped_form_pdl_ids("import_pdl_access")
            selected_pdls = PDL.query.filter(PDL.id.in_(pdl_ids)).all() if pdl_ids else []
            if not upload or not upload.filename:
                flash("Choose a CSV file to upload.", "error")
            elif not upload.filename.lower().endswith(".csv"):
                flash("Only CSV files are supported.", "error")
            else:
                try:
                    raw = upload.read().decode("utf-8-sig")
                    reader = csv.DictReader(io.StringIO(raw))
                    headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
                    required = {"name", "email", "team", "designation"}
                    if not required.issubset(headers):
                        missing = ", ".join(sorted(required - headers))
                        raise ValueError(f"CSV is missing required columns: {missing}")
                    added = updated = pdl_added = skipped = 0
                    errors = []
                    seen = set()
                    for line_no, row in enumerate(reader, start=2):
                        normalized = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                        name = normalized.get("name", "")
                        email = normalized.get("email", "").lower()
                        team = normalized.get("team", "")
                        designation = normalized.get("designation", "")
                        if not name and not email and not team and not designation:
                            continue
                        if not name or not email or "@" not in email:
                            skipped += 1
                            errors.append(f"Row {line_no}: name and valid email are required")
                            continue
                        if email in seen:
                            skipped += 1
                            errors.append(f"Row {line_no}: duplicate email {email} in this file")
                            continue
                        seen.add(email)
                        engineer = Engineer.query.filter(func.lower(Engineer.email) == email.lower()).first()
                        if current_user.role == "manager" and engineer is not None and engineer.id not in manager_visible_engineer_ids:
                            skipped += 1
                            errors.append(f"Row {line_no}: {email} belongs to another team and cannot be modified")
                            continue
                        if engineer is None:
                            engineer = Engineer(name=name, email=email, team=team or None, designation=designation or None, contact_role="engineer")
                            db.session.add(engineer)
                            db.session.flush()
                            added += 1
                        else:
                            changed = False
                            if name and engineer.name != name: engineer.name = name; changed = True
                            if team and engineer.team != team: engineer.team = team; changed = True
                            if designation and engineer.designation != designation: engineer.designation = designation; changed = True
                            if changed: updated += 1
                        if engineer not in group.engineers:
                            group.engineers.append(engineer)
                        existing_ids = {p.id for p in engineer.pdl_access}
                        new_pdls = [pdl for pdl in selected_pdls if pdl.id not in existing_ids]
                        if new_pdls:
                            engineer.pdl_access = list(engineer.pdl_access) + new_pdls
                            pdl_added += len(new_pdls)
                    db.session.commit()
                    summary = f"CSV import complete: {added} added, {updated} updated, {pdl_added} new PDL access grants, {skipped} skipped."
                    if errors:
                        summary += " " + "; ".join(errors[:5])
                        if len(errors) > 5: summary += f" (+{len(errors)-5} more)"
                        flash(summary, "warning")
                    else:
                        flash(summary, "success")
                except Exception as exc:
                    db.session.rollback()
                    flash(f"CSV import failed: {exc}", "error")
        elif action == "add_slot":
            st, et = _parse_slot_time(request.form.get("start_time")), _parse_slot_time(request.form.get("end_time"))
            name = request.form.get("slot_name", "").strip()
            pdl_ids = scoped_form_pdl_ids("slot_pdl_ids")
            selected_pdls = PDL.query.filter(PDL.id.in_(pdl_ids)).all() if pdl_ids else []
            if not name or not st or not et:
                flash("Slot name, start time and end time are required.", "error")
            else:
                slot = MonitoringSlot(group=group, name=name, start_time=st, end_time=et)
                slot.pdls = selected_pdls
                db.session.add(slot); db.session.commit();
                flash("Monitoring slot created with PDL access.", "success")
        elif action == "update_slot_pdls":
            slot = MonitoringSlot.query.filter_by(id=request.form.get("slot_id", type=int), group_id=group.id).first()
            if not slot:
                flash("Monitoring slot not found.", "error")
            else:
                pdl_ids = scoped_form_pdl_ids("slot_pdl_ids")
                slot.pdls = PDL.query.filter(PDL.id.in_(pdl_ids)).all() if pdl_ids else []
                db.session.commit()
                flash(f'PDL access updated for "{slot.name}".', "success")
        elif action == "duplicate_slot":
            source = MonitoringSlot.query.filter_by(id=request.form.get("slot_id", type=int), group_id=group.id).first()
            if not source:
                flash("Monitoring slot not found.", "error")
            else:
                copy_name = request.form.get("copy_name", "").strip() or f"{source.name} (Copy)"
                copy = MonitoringSlot(group=group, name=copy_name, start_time=source.start_time, end_time=source.end_time)
                copy.pdls = list(source.pdls)
                db.session.add(copy); db.session.commit(); flash(f'Monitoring slot copied as "{copy_name}" with the same PDL access.', "success")
        elif action == "delete_slot":
            slot = MonitoringSlot.query.filter_by(id=request.form.get("slot_id", type=int), group_id=group.id).first()
            if slot:
                now = datetime.now(timezone.utc)
                for assignment in list(slot.assignments):
                    _close_open_monitoring_history(assignment.pdl_id, now, "slot_deleted", slot_id=slot.id)
                db.session.delete(slot); db.session.commit(); flash("Monitoring slot deleted. Assignment history was preserved.", "success")
        return redirect(url_for("team_group_detail", group_id=group.id))
    member_search = (request.args.get("member_search") or "").strip()
    group_engineers = sorted(
        list(group.engineers),
        key=lambda e: (e.name or "").lower(),
    )
    if member_search:
        needle = member_search.lower()
        group_engineers = [
            e for e in group_engineers
            if needle in (e.name or "").lower()
            or needle in (e.email or "").lower()
            or needle in (e.team or "").lower()
            or needle in (e.designation or "").lower()
        ]
    group_engineer_ids = {e.id for e in group.engineers}
    available_engineers = (
        [
            e for e in Engineer.query.filter_by(active=True).order_by(Engineer.name).all()
            if e.id not in group_engineer_ids
        ]
        if current_user.role == "admin"
        else []
    )
    history = PDLMonitoringAssignmentHistory.query.filter_by(group_id=group.id).order_by(PDLMonitoringAssignmentHistory.assigned_at.desc()).limit(200).all()
    return render_template(
        "team_group_detail.html",
        group=group,
        engineers=available_engineers,
        group_engineers=group_engineers,
        pdls=(PDL.query.filter_by(active=True).order_by(PDL.name).all()
              if current_user.role == "admin"
              else sorted(list(group.pdls), key=lambda p: (p.name or "").lower())),
        assignment_history=history,
        member_search=member_search,
    )

@app.route("/team-management/<int:group_id>/delete", methods=["POST"])
@admin_required
def delete_team_group(group_id):
    group = TeamGroup.query.get_or_404(group_id); name = group.name
    db.session.delete(group); db.session.commit(); flash(f'Group "{name}" deleted.', "success")
    return redirect(url_for("team_management"))

@app.route("/team-management/executive/<int:group_id>", methods=["GET", "POST"])
@admin_required
def executive_group_detail(group_id):
    group = ExecutiveGroup.query.get_or_404(group_id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_member":
            engineer = Engineer.query.get(request.form.get("engineer_id", type=int))
            if engineer and engineer not in group.members:
                engineer.contact_role = "manager" if (engineer.designation or "").lower().find("manager") >= 0 else engineer.contact_role
                engineer.is_executive = True; group.members.append(engineer); db.session.commit(); flash("Leadership member added.", "success")
        elif action == "remove_member":
            engineer = Engineer.query.get_or_404(request.form.get("engineer_id", type=int));
            if engineer in group.members: group.members.remove(engineer); db.session.commit()
        return redirect(url_for("executive_group_detail", group_id=group.id))
    leaders = Engineer.query.filter(Engineer.active.is_(True), or_(Engineer.is_executive.is_(True), Engineer.contact_role.in_(["manager","executive"]))).order_by(Engineer.name).all()
    return render_template("executive_group_detail.html", group=group, leaders=leaders)

@app.route("/team-management/executive/<int:group_id>/delete", methods=["POST"])
@admin_required
def delete_executive_group(group_id):
    group=ExecutiveGroup.query.get_or_404(group_id); db.session.delete(group); db.session.commit(); flash("Executive group deleted.", "success"); return redirect(url_for("team_management"))

@app.route("/pdls/<int:pdl_id>/actions", methods=["POST"])
@manager_or_admin_required
def pdl_monitoring_action(pdl_id):
    pdl = PDL.query.get_or_404(pdl_id)
    if pdl.id not in set(allowed_pdl_ids()):
        abort(403)
    action = request.form.get("action")
    if action == "assign":
        slot = MonitoringSlot.query.get(request.form.get("slot_id", type=int))
        engineer = Engineer.query.get(request.form.get("engineer_id", type=int))
        if current_user.role == "manager" and (not slot or slot.group_id not in managed_team_ids()):
            abort(403)
        slot_pdls = set(slot.pdls) if slot else set()
        slot_allows_pdl = bool(slot) and (not slot_pdls or pdl in slot_pdls)
        if not slot or not engineer or engineer not in slot.group.engineers or pdl not in engineer.pdl_access or not slot_allows_pdl:
            flash("Select a slot and an engineer who belongs to that group, has PDL access, and is authorized for the slot.", "error")
        else:
            now = datetime.now(timezone.utc)
            # A PDL can have multiple slots. Only the same PDL+same slot can be
            # reassigned; a different slot creates a second independent assignment.
            row = PDLMonitoringAssignment.query.filter_by(pdl_id=pdl.id, slot_id=slot.id).first()
            if row and row.engineer_id == engineer.id:
                flash("This monitoring assignment is already active.", "info")
            else:
                if row:
                    _close_open_monitoring_history(pdl.id, now, "reassigned", slot_id=slot.id)
                    row.engineer = engineer
                    row.assigned_at = now
                else:
                    row = PDLMonitoringAssignment(pdl=pdl, slot=slot, engineer=engineer, assigned_at=now)
                    db.session.add(row)
                db.session.add(_record_monitoring_assignment_history(pdl, slot, engineer, now))
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    raise
                # Outlook notification happens only after the DB transaction
                # is closed, so COM/network work cannot hold a SQLite write lock.
                sent, notify_error = _send_monitoring_assignment_notification(pdl, slot, engineer)
                if sent:
                    flash(f"Monitoring slot assigned to {engineer.name}. Email notification sent.", "success")
                elif notify_error == "Notification disabled":
                    flash(f"Monitoring slot assigned to {engineer.name}.", "success")
                else:
                    flash(f"Monitoring slot assigned to {engineer.name}, but the assignment email could not be sent: {notify_error}", "warning")
    elif action == "remove_assignment":
        assignment = PDLMonitoringAssignment.query.get(request.form.get("assignment_id", type=int))
        if assignment and assignment.pdl_id == pdl.id:
            _close_open_monitoring_history(pdl.id, datetime.now(timezone.utc), "removed", slot_id=assignment.slot_id)
            db.session.delete(assignment); db.session.commit(); flash("Monitoring assignment removed. History was preserved.", "success")
    elif action == "delete_pdl":
        if current_user.role != "admin":
            abort(403)
        for email in pdl.emails: email.pdl_id = None
        db.session.delete(pdl); db.session.commit(); flash("PDL deleted; email history preserved.", "success"); return redirect(url_for("pdls"))
    return redirect(url_for("pdl_detail", pdl_id=pdl.id))

def _slot_match_email(email, slot):
    if not email.received_at: return False
    local = as_utc(email.received_at).astimezone(IST)
    t = local.time()
    if slot.start_time <= slot.end_time:
        return slot.start_time <= t <= slot.end_time
    return t >= slot.start_time or t <= slot.end_time

def monitoring_slot_stats(slot, pdl_id, range_ctx=None):
    """Return slot workload using assignment history at receipt time.

    An email is counted only when the PDL was actually assigned to this slot
    when the email arrived. A later/current assignment cannot pull old emails
    into the slot report.
    """
    q=_exclude_pdl_member_messages(Email.query.filter(Email.pdl_id==pdl_id, Email.monitoring_excluded.is_(False)), [pdl_id])
    if range_ctx: q=apply_metric_range(q, range_ctx)
    rows=q.all(); rows=[e for e in rows if _slot_match_email(e, slot) and _report_assignment_for_email(e, slot, pdl_id)]
    received=len(rows); attended=sum(1 for e in rows if e.responded); breached=sum(1 for e in rows if e.tat_breached); unattended=sum(1 for e in rows if not e.responded)
    return {"received":received,"attended":attended,"responded":attended,"unattended":unattended,"tat_breached":breached,"response_rate":round(attended/received*100,1) if received else 0}


# ============================================================
# AI BENCHMARK
# ============================================================

@app.route("/admin/ai-benchmark")
@admin_required
def ai_benchmark():
    """Show the controlled benchmark instructions and installed model state.

    Actual benchmark execution is intentionally CLI-based so a large benchmark
    cannot be triggered accidentally by a browser request and so administrators
    can run it on a representative, approved dataset.
    """
    from ai.configuration import MODEL_PRESETS
    installed = {}
    try:
        from ai.providers.ollama import OllamaProvider
        base_url = ai_settings().get("AI_BASE_URL", "http://127.0.0.1:11434")
        for model in MODEL_PRESETS:
            installed[model] = OllamaProvider(base_url, model, 5, 0.0, False, "0").health()
    except Exception:
        installed = {model: {"model_available": False, "error": "Unable to query Ollama"} for model in MODEL_PRESETS}
    return render_template("ai_benchmark.html", ai_model_presets=MODEL_PRESETS, installed_models=installed)


# ============================================================
# ADMIN
# ============================================================

@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    if request.method == "POST":
        action = (request.form.get("action") or "sync_settings").strip()
        if action == "network_settings":
            try:
                network = validate_network_config({
                    "adapter": request.form.get("adapter"),
                    "hostname": request.form.get("hostname"),
                    "ip_address": request.form.get("ip_address"),
                    "prefix_length": request.form.get("prefix_length"),
                    "gateway": request.form.get("gateway"),
                    "dns_servers": request.form.get("dns_servers"),
                    "ntp_servers": request.form.get("ntp_servers"),
                })
                if request.form.get("confirm_apply") != "on":
                    raise ValueError("Confirm that the Windows Server network configuration should be applied.")
                result = apply_network_config(network)
                for key, value in {
                    "adapter": network["adapter"],
                    "hostname": network["hostname"],
                    "ip_address": network["ip_address"],
                    "prefix_length": str(network["prefix_length"]),
                    "gateway": network["gateway"],
                    "dns_servers": ",".join(network["dns_servers"]),
                    "ntp_servers": ",".join(network["ntp_servers"]),
                    "api_url": f"http://{network['ip_address']}:{int(app.config.get('PORT', 5000))}",
                }.items():
                    set_sync_state(f"network:{key}", value)
                log_event("WARN", "network_configuration", f"Windows network configuration applied for adapter {network['adapter']}; API URL set to http://{network['ip_address']}:{int(app.config.get('PORT', 5000))}.")
                flash("Network settings applied. IP/DNS/NTP are active; the hostname change requires a Windows restart. Verify the new API URL after reconnecting.", "success")
            except Exception as exc:
                log_event("ERROR", "network_configuration", f"Network configuration failed: {exc}")
                flash(f"Network configuration was not applied: {exc}", "error")
            return redirect(url_for("admin"))

        if action == "ai_settings":
            allowed_providers = {"local", "ollama", "rules"}
            provider = (request.form.get("ai_provider") or "local").strip().lower()
            if provider not in allowed_providers:
                provider = "local"
            settings = {
                "AI_PROVIDER": provider,
                "AI_MODEL": (request.form.get("ai_model") or "qwen3:8b").strip()[:160],
                "AI_BASE_URL": (request.form.get("ai_base_url") or "http://127.0.0.1:11434").strip()[:500],
                "AI_TIMEOUT": str(max(300 if (request.form.get("ai_model") or "qwen3:8b").strip().lower().startswith("qwen3:8b") else 120,
                                      min(request.form.get("ai_timeout", 300, type=int), 600))),
                "AI_TEMPERATURE": str(max(0.0, min(request.form.get("ai_temperature", 0.0, type=float), 1.0))),
                "AI_THINK": (request.form.get("ai_think") or "false").strip().lower() if (request.form.get("ai_think") or "false").strip().lower() in {"true", "false", "low", "medium", "high"} else "false",
                "AI_KEEP_ALIVE": (request.form.get("ai_keep_alive") or "60m").strip()[:32],
                "AI_CONFIDENCE_THRESHOLD": str(max(0.1, min(request.form.get("ai_confidence", 0.72, type=float), 0.99))),
                "AI_FALLBACK_MODE": "rules",
                "AI_LOCAL_ONLY": "true",
                "AI_RAG_ENABLED": "true" if request.form.get("ai_rag_enabled") == "on" else "false",
                "AI_RAG_TOP_K": str(max(1, min(request.form.get("ai_rag_top_k", 4, type=int), 8))),
                "AI_RAG_MIN_SCORE": str(max(0.30, min(request.form.get("ai_rag_min_score", 0.55, type=float), 0.95))),
                "AI_RAG_SYNC_INTERVAL_SECONDS": str(max(0, min(request.form.get("ai_rag_sync_interval", 60, type=int), 3600))),
                "AI_NUM_CTX": str(max(4096, min(request.form.get("ai_num_ctx", 8192, type=int), 16384))),
                "AI_MAX_TOKENS": str(max(192, min(request.form.get("ai_max_tokens", 256, type=int), 512))),
                "AI_PROMPT_VERSION": "v5-rag-langgraph",
            }
            for key, value in settings.items():
                set_sync_state(f"ai:{key}", value)
            log_event("INFO", "ai_configuration", f"AI provider configured as {provider}; model {settings['AI_MODEL']}.")
            flash("AI configuration saved. Credentials/API keys are not stored by this form.", "success")
            return redirect(url_for("admin"))

        enabled = request.form.get("auto_sync") == "on"
        interval = request.form.get("sync_interval", 1, type=int)
        interval = max(1, min(interval, 1440))
        set_sync_state("auto_sync_enabled", "1" if enabled else "0")
        set_sync_state("sync_interval_minutes", str(interval))
        log_event("INFO", "auto_sync_settings", f"Automatic Outlook sync {'enabled' if enabled else 'disabled'}; interval {interval} minute(s).")
        flash(f"Auto-sync {'enabled' if enabled else 'disabled'} ({interval} minute interval).", "success")
        return redirect(url_for("admin"))

    # Self-heal older persisted settings. Qwen3:8b measured on CPU can take
    # well over 90 seconds, so a 60/100 second timeout guarantees false errors.
    current_ai = ai_settings()
    try:
        if str(current_ai.get("AI_MODEL", "")).lower().startswith("qwen3:8b") and int(current_ai.get("AI_TIMEOUT") or 0) < 300:
            set_sync_state("ai:AI_TIMEOUT", "300")
            current_ai["AI_TIMEOUT"] = "300"
        # Migrate older persisted AI settings to the V10.20.26 structured/RAG
        # defaults. Existing DBs keep their data; only AI runtime preferences
        # are upgraded.
        migrations = {
            "AI_NUM_CTX": "8192",
            "AI_MAX_TOKENS": "256",
            "AI_QUEUE_LIMIT": "4",
            "AI_PROMPT_VERSION": "v5-rag-langgraph",
            "AI_LOCAL_ONLY": "true",
            "AI_RAG_ENABLED": "true",
            "AI_RAG_TOP_K": "4",
            "AI_RAG_MIN_SCORE": "0.55",
            "AI_RAG_PATH": "instance/chroma",
            "AI_EMBEDDING_MODEL_PATH": "models/all-MiniLM-L6-v2",
            "AI_EMBEDDING_DEVICE": "cpu",
            "AI_RAG_SYNC_INTERVAL_SECONDS": "60",
        }
        needs_ai_migration = str(current_ai.get("AI_PROMPT_VERSION", "")) != "v5-rag-langgraph"
        if needs_ai_migration:
            for key, value in migrations.items():
                set_sync_state(f"ai:{key}", value)
                current_ai[key] = value
    except Exception:
        pass
    health_state = ai_health()
    ai_now = datetime.now(timezone.utc)
    ai_24h = ai_now - timedelta(hours=24)
    latest_ai_id = db.session.query(func.max(AIAnalysis.id)).group_by(AIAnalysis.email_id).subquery()
    latest_analyses = AIAnalysis.query.filter(AIAnalysis.id.in_(db.session.query(latest_ai_id.c[0])))
    latest_error = (latest_analyses.filter(AIAnalysis.error.isnot(None))
                    .order_by(AIAnalysis.created_at.desc()).first())
    model_stats = {
        "processed": latest_analyses.filter(AIAnalysis.status == "COMPLETE").count(),
        "model_processed": latest_analyses.filter(
            AIAnalysis.status == "COMPLETE",
            AIAnalysis.fallback_used.is_(False),
            AIAnalysis.provider.in_(("local", "ollama")),
        ).count(),
        "fallback_processed": latest_analyses.filter(
            AIAnalysis.status == "COMPLETE",
            or_(AIAnalysis.fallback_used.is_(True), AIAnalysis.provider == "hybrid"),
        ).count(),
        "processing": latest_analyses.filter(AIAnalysis.status == "PROCESSING").count(),
        "queued": latest_analyses.filter(AIAnalysis.status == "PENDING").count(),
        "errors": latest_analyses.filter(AIAnalysis.error.isnot(None)).count(),
        "errors_24h": latest_analyses.filter(AIAnalysis.error.isnot(None), AIAnalysis.created_at >= ai_24h).count(),
        "low_confidence": latest_analyses.filter(AIAnalysis.human_review_required.is_(True)).count(),
        "low_confidence_24h": latest_analyses.filter(AIAnalysis.human_review_required.is_(True), AIAnalysis.created_at >= ai_24h).count(),
        "context_aware_24h": latest_analyses.filter(
            AIAnalysis.created_at >= ai_24h,
            AIAnalysis.result_json.like('%"context_aware": true%'),
        ).count(),
        "latest_error": (latest_error.error if latest_error else None),
    }
    try:
        adapters = list_adapters()
    except Exception as exc:
        adapters = []
        app.logger.warning("Unable to enumerate Windows network adapters: %s", exc)
    network_settings = {
        "adapter": get_sync_state("network:adapter") or (adapters[0].get("Name") if adapters else ""),
        "hostname": get_sync_state("network:hostname") or os.getenv("COMPUTERNAME", ""),
        "ip_address": get_sync_state("network:ip_address") or "",
        "prefix_length": get_sync_state("network:prefix_length") or "24",
        "gateway": get_sync_state("network:gateway") or "",
        "dns_servers": get_sync_state("network:dns_servers") or "",
        "ntp_servers": get_sync_state("network:ntp_servers") or "time.windows.com",
        "api_url": get_sync_state("network:api_url") or request.host_url.rstrip("/"),
    }
    return render_template(
        "admin.html", stores=[], internal_domains=", ".join(sorted(INTERNAL_DOMAINS)),
        last_sync=get_sync_state("last_sync"), last_sync_result=get_sync_state("last_sync_result"),
        ai_config=current_ai, ai_health=health_state, ai_stats=model_stats, ai_model_presets=MODEL_PRESETS,
        ai_rag=rag_status(),
        network_adapters=adapters, network_settings=network_settings,
        app_version=app.config.get("APP_VERSION", "0"),
        update_status=_read_update_status(),
    )


# ============================================================
# EVENTS
# ============================================================

@app.route("/events")
@admin_required
def events():
    level = request.args.get("level", "").strip().upper()
    event_type = request.args.get("type", "").strip()
    q = Event.query
    if level:
        q = q.filter(Event.level == level)
    if event_type:
        q = q.filter(Event.event_type == event_type)

    rows = q.order_by(Event.created_at.desc()).limit(1000).all()
    event_types = [
        value[0] for value in
        db.session.query(Event.event_type).distinct().order_by(Event.event_type).all()
    ]
    return render_template(
        "events.html",
        events=rows,
        event_types=event_types,
        active_level=level,
        active_type=event_type,
    )


# ============================================================
# API: SYNC
# ============================================================

@app.route(
    "/api/sync",
    methods=["POST"],
)
@login_required
def api_sync():

    if not app.config.get(
        "OUTLOOK_SYNC_ENABLED",
        True,
    ):

        return jsonify({
            "ok": False,
            "error": "Outlook sync is disabled.",
        }), 400

    # Prevent two users/tabs from running
    # Outlook sync simultaneously.
    if not SYNC_LOCK.acquire(
        blocking=False
    ):

        return jsonify({
            "ok": False,
            "error": (
                "An Outlook synchronization is already running. "
                "Please wait for it to finish."
            ),
            "busy": True,
        }), 409

    try:

        set_sync_state("sync_progress", "Starting manual mailbox sync")
        from outlook_connector import sync_outlook

        # Manual sync is a recovery operation: scan the configured SYNC_DAYS
        # window instead of trusting the last incremental-sync timestamp.
        result = sync_outlook(force_full=True)
        enqueue_unanalyzed_ai()
        log_event(
            "INFO",
            "manual_sync",
            "Manual mailbox synchronization completed.",
            json.dumps(result, default=str),
        )

        return jsonify({
            "ok": True,
            "result": result,
        })

    except Exception as exc:

        try:
            db.session.rollback()
        except Exception:
            pass

        log_event("ERROR", "manual_sync", "Manual mailbox synchronization failed.", str(exc))
        return jsonify({
            "ok": False,
            "error": str(exc),
        }), 500

    finally:

        try:
            db.session.remove()
        except Exception:
            pass

        SYNC_LOCK.release()


# ============================================================
# API: DISCOVER OUTLOOK
# ============================================================

@app.route("/api/discover-outlook")
@admin_required
def api_discover_outlook():

    try:

        from outlook_connector import discover

        return jsonify({
            "ok": True,
            "stores": discover(),
        })

    except Exception as exc:

        return jsonify({
            "ok": False,
            "error": str(exc),
        }), 500


# ============================================================
# ALERTS / NOTIFICATIONS
# ============================================================

def _sync_notification_read_state(pdl_ids=None):
    """Close notification badges automatically when the linked email is responded.

    A notification is an action item, not a second response state. Once a valid
    PDL-member response has been detected by Outlook sync, all open notifications
    attached to that email are marked read. The email remains available in the
    database for audit/history, while the notification count accurately reflects
    outstanding work.
    """
    try:
        ids = list(pdl_ids or allowed_pdl_ids() or [])
        if not ids:
            return 0
        open_alerts = (
            Alert.query.join(Email, Alert.email_id == Email.id)
            .filter(
                Alert.pdl_id.in_(ids),
                Alert.read.is_(False),
                Email.responded.is_(True),
            )
            .all()
        )
        changed = 0
        for alert in open_alerts:
            alert.read = True
            changed += 1
        if changed:
            db.session.commit()
        return changed
    except Exception:
        db.session.rollback()
        return 0

@app.route("/alerts")
@login_required
def alerts():
    ids = allowed_pdl_ids()
    _sync_notification_read_state(ids)
    action_alert_types = {"critical_escalation", "ai_attention", "schedule_miss", "tat_breach", "executive_copy", "action_required"}
    status = (request.args.get("status") or "unread").strip().lower()
    if status not in {"unread", "read", "all"}:
        status = "unread"
    origin = (request.args.get("origin") or "all").strip().lower()
    if origin not in {"all", "customer", "team"}:
        origin = "all"
    # Notifications follow the same operational period convention as Reports:
    # 24 hours is the default, with historical periods available on demand.
    range_ctx = metric_range_context(default="today")

    q = Alert.query.filter(
        Alert.pdl_id.in_(ids),
        Alert.alert_type.in_(tuple(action_alert_types)),
        Alert.created_at >= range_ctx["start_utc"],
        Alert.created_at <= range_ctx["end_utc"],
    ) if ids else Alert.query.filter(false())

    if status == "unread":
        q = q.filter(Alert.read.is_(False))
    elif status == "read":
        q = q.filter(Alert.read.is_(True))
    if origin == "customer":
        q = q.filter(Email.sender_type == "customer")
    elif origin == "team":
        q = q.filter(Email.sender_type.in_(("engineer", "internal")))

    # Unread first, then newest notification time. This ordering is preserved
    # for the "all" view so action items always remain at the top.
    rows = q.order_by(Alert.read.asc(), Alert.created_at.desc()).limit(500).all()
    # Do not blanket-hide configured-team mail here. Team mail is excluded
    # from customer workload, but a genuine critical/high operational event
    # reported by a team member must remain visible as a notification.
    unread_count = sum(1 for row in rows if not row.read)
    return render_template("alerts.html", alerts=rows, alert_filter=status, alert_origin=origin, unread_count=unread_count, metric_range=range_ctx)

@app.route("/alerts/<int:alert_id>/read", methods=["POST"])
@login_required
def mark_alert_read(alert_id):
    row = Alert.query.get_or_404(alert_id)
    if row.pdl_id not in allowed_pdl_ids(): abort(403)
    row.read = True

    # A notification acknowledgement is NOT an email response. Only a real
    # reply from an engineer with PDL access can set Email.responded=True.
    # Marking a notification read must never change TAT or response timestamps.

    db.session.commit()
    return redirect(request.referrer or url_for("alerts"))

@app.route("/api/v1/pdls")
@login_required
def api_v1_pdls():
    ids = allowed_pdl_ids()
    rows = PDL.query.filter(PDL.id.in_(ids)).order_by(PDL.name).all() if ids else []
    return jsonify({"ok": True, "items": [{"id":p.id,"name":p.name,"address":p.address,"team":p.team,"tat_minutes":p.tat_minutes,"owner_name":p.owner_name,"owner_email":p.owner_email,"active":p.active} for p in rows]})

@app.route("/api/v1/pdls/<int:pdl_id>/summary")
@login_required
def api_v1_pdl_summary(pdl_id):
    if pdl_id not in allowed_pdl_ids(): abort(403)
    pdl = PDL.query.get_or_404(pdl_id); q = _exclude_pdl_member_messages(Email.query.filter(Email.pdl_id==pdl_id, Email.monitoring_excluded.is_(False)), [pdl_id])
    total=q.count(); responded=q.filter(Email.responded.is_(True)).count(); breached=q.filter(Email.tat_breached.is_(True)).count(); unattended=q.filter(Email.responded.is_(False)).count()
    return jsonify({"ok":True,"pdl":{"id":pdl.id,"name":pdl.name,"owner_name":pdl.owner_name,"owner_email":pdl.owner_email,"total_emails":total,"responded":responded,"unattended":unattended,"tat_breached":breached,"response_rate":round((responded/total)*100,1) if total else 0,"tat_compliance":round(((total-breached)/total)*100,1) if total else 0}})

@app.route("/api/v1/alerts")
@login_required
def api_v1_alerts():
    ids=allowed_pdl_ids()
    _sync_notification_read_state(ids)
    day_start, day_end = _current_ist_day_bounds()
    q=(Alert.query.filter(
        Alert.pdl_id.in_(ids), Alert.created_at >= day_start, Alert.created_at < day_end
    ) if ids else Alert.query.filter(false()))
    rows=q.order_by(Alert.created_at.desc()).limit(200).all()
    return jsonify({"ok":True,"items":[{"id":a.id,"created_at":a.created_at.isoformat() if a.created_at else None,"pdl_id":a.pdl_id,"email_id":a.email_id,"type":a.alert_type,"severity":a.severity,"title":a.title,"message":a.message,"read":a.read} for a in rows]})


@app.route("/api/v1/system/network")
@login_required
def api_system_network():
    return jsonify({
        "ok": True,
        "hostname": get_sync_state("network:hostname") or os.getenv("COMPUTERNAME", ""),
        "ip_address": get_sync_state("network:ip_address") or "",
        "gateway": get_sync_state("network:gateway") or "",
        "dns_servers": get_sync_state("network:dns_servers") or "",
        "ntp_servers": get_sync_state("network:ntp_servers") or "",
        "api_url": get_sync_state("network:api_url") or request.host_url.rstrip("/"),
    })


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    try:
        db.session.execute(
            db.text("SELECT 1")
        )

        database_status = "ok"

    except Exception:

        database_status = "error"

    return jsonify({
        "status": "ok",
        "version": app.config.get("APP_VERSION", "9.5.0"),
        "time": datetime.now(
            timezone.utc
        ).isoformat(),
        "database": database_status,
    })


# ============================================================
# BOOTSTRAP
# ============================================================

def bootstrap():

    with app.app_context():

        init_db()

        # V10.14.1: local Qwen classification is compact and should have enough
        # time for a cold model load on ordinary Windows hardware. Migrate the
        # previous 60-second/900-token settings that caused repeated timeouts.
        try:
            current_ai_timeout = get_sync_state("ai:AI_TIMEOUT")
            current_ai_tokens = get_sync_state("ai:AI_MAX_TOKENS")
            current_ai_model = get_sync_state("ai:AI_MODEL") or os.getenv("AI_MODEL", "qwen3:8b")
            if str(current_ai_model).lower() == "qwen3:8b" and str(current_ai_timeout) == "60":
                set_sync_state("ai:AI_TIMEOUT", "180")
            if str(current_ai_tokens) in {"900", "2000", "500", "320"}:
                # Keep Qwen3 CPU classification compact; the application derives
                # operational fields locally and only needs a short JSON response.
                set_sync_state("ai:AI_MAX_TOKENS", "160")
        except Exception:
            pass

        cleanup_pdl_member_outbound_messages()
        cleanup_noncritical_internal_notifications()

        # Auto-sync is enabled by default for new installations. Preserve an
        # explicit administrator choice once the setting exists.
        if get_sync_state("auto_sync_enabled") is None:
            set_sync_state("auto_sync_enabled", "1")
        if get_sync_state("sync_interval_minutes") is None:
            set_sync_state("sync_interval_minutes", str(app.config.get("SYNC_INTERVAL_MINUTES", 5)))
        elif get_sync_state("sync_interval_minutes") == "1" and get_sync_state("sync_interval_v10_12_migrated") is None:
            # V10.11 used one minute as the legacy default. Move that legacy
            # default to five minutes, but only once and only when it still
            # has the legacy value. Explicit later administrator choices are
            # preserved.
            set_sync_state("sync_interval_minutes", "5")
            set_sync_state("sync_interval_v10_12_migrated", "1")

        # V10.20.4: first-run administrator creation is handled by /setup.
        # Existing installations are left untouched.
        if User.query.count() == 0:
            print("[PDL] No application users found. Initial setup is required at /setup")

    # Auto-sync is controlled by the persisted UI setting.
    # Avoid starting two scheduler instances under Werkzeug's debug reloader.
    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_scheduler()


# ============================================================
# START
# ============================================================

bootstrap()


if __name__ == "__main__":

    # Open a local browser for workstation/server-console launches. Disable with
    # OPEN_BROWSER=0 for headless Windows Server deployments.
    if os.getenv("OPEN_BROWSER", "1").lower() in {"1", "true", "yes", "on"}:
        try:
            import threading
            import webbrowser
            with app.app_context():
                _browser_path = "/setup" if _setup_required() else "/login"
            threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{os.getenv('PORT', '5000')}{_browser_path}")).start()
        except Exception as _browser_exc:
            print(f"[PDL] Browser auto-open skipped: {_browser_exc}")

    app.run(
        host=app.config.get(
            "HOST",
            os.getenv(
                "HOST",
                "127.0.0.1",
            ),
        ),

        port=int(
            os.getenv(
                "PORT",
                "5000",
            )
        ),

        debug=(
            os.getenv(
                "FLASK_DEBUG",
                "0",
            ) == "1"
        ),

        threaded=True,
    )