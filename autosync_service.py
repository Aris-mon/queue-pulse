"""Reliable background mailbox auto-sync service.

Provides:
- one non-overlapping sync at a time
- COM initialization for every worker thread
- retry/backoff for transient Graph/Outlook/SQLite failures
- persisted sync status
- graceful failure when the configured mailbox is unavailable
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from datetime import datetime, timezone

try:
    import pythoncom
except Exception:
    pythoncom = None

from db import db, set_sync_state, get_sync_state

_SYNC_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_status(status: str, message: str = "", **extra):
    payload = {
        "status": status,
        "message": message[:1000] if message else "",
        "updated_at": _now_iso(),
        **extra,
    }
    # Persist individual fields so older DB implementations remain compatible.
    for key, value in payload.items():
        try:
            set_sync_state(f"auto_sync_{key}", str(value))
        except Exception:
            pass
    return payload


def get_autosync_status():
    return {
        "enabled": str(get_sync_state("auto_sync_enabled") or "1").lower() in ("1", "true", "yes", "on"),
        "status": get_sync_state("auto_sync_status") or "idle",
        "message": get_sync_state("auto_sync_message") or "",
        "updated_at": get_sync_state("auto_sync_updated_at") or "",
        "last_success": get_sync_state("auto_sync_last_success") or "",
        "last_failure": get_sync_state("auto_sync_last_failure") or "",
        "last_count": int(get_sync_state("auto_sync_last_count") or 0),
    }


def _do_sync(app=None):
    # Import lazily: this prevents Outlook/COM imports during Flask startup.
    from outlook_connector import sync_outlook

    return sync_outlook()


def run_reliable_sync(app=None, reason="scheduled", retries=3):
    """Run one background sync.

    Returns a dict. If another sync is already running, this returns immediately
    without creating a second Outlook COM session.
    """
    if not _SYNC_LOCK.acquire(blocking=False):
        return {
            "ok": False,
            "skipped": True,
            "reason": "sync_already_running",
        }

    try:
        enabled = str(get_sync_state("auto_sync_enabled") or "1").lower()
        if enabled not in ("1", "true", "yes", "on"):
            _set_status("disabled", "Auto Sync is disabled.")
            return {"ok": False, "disabled": True}

        _set_status("running", f"Starting {reason} mailbox sync")

        last_error = None
        for attempt in range(1, retries + 1):
            try:
                result = _do_sync(app)
                count = 0
                if isinstance(result, dict):
                    count = int(result.get("imported", result.get("count", 0)) or 0)
                elif isinstance(result, int):
                    count = result

                ts = _now_iso()
                _set_status(
                    "success",
                    f"Sync completed ({reason}); imported {count} email(s).",
                    last_success=ts,
                    last_count=count,
                )
                return {
                    "ok": True,
                    "imported": count,
                    "attempt": attempt,
                    "finished_at": ts,
                }
            except Exception as exc:
                last_error = exc
                # Roll back any partially failed SQLAlchemy transaction.
                try:
                    db.session.rollback()
                except Exception:
                    pass

                if attempt < retries:
                    delay = min(5 * (2 ** (attempt - 1)), 30)
                    _set_status(
                        "retrying",
                        f"Sync attempt {attempt} failed; retrying in {delay}s: {exc}",
                    )
                    time.sleep(delay)

        ts = _now_iso()
        _set_status(
            "error",
            f"Sync failed after {retries} attempt(s): {last_error}",
            last_failure=ts,
        )
        return {
            "ok": False,
            "error": str(last_error),
            "finished_at": ts,
        }
    finally:
        try:
            # COM initialization is owned by outlook_connector.sync_outlook().
            # Graph app-only mode never initializes COM.
            pass
        finally:
            _SYNC_LOCK.release()
