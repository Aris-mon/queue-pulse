import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"

# Make sure the Flask instance directory exists
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")


class Config:
    # Application release version shown under About. VERSION.txt is the
    # release source of truth when APP_VERSION is not explicitly overridden.
    _VERSION_FILE = BASE_DIR / "VERSION.txt"
    _VERSION_DEFAULT = (
        _VERSION_FILE.read_text(encoding="utf-8").strip().splitlines()[0]
        if _VERSION_FILE.exists() and _VERSION_FILE.read_text(encoding="utf-8").strip()
        else "10.20.24"
    )
    APP_VERSION = os.getenv("APP_VERSION", _VERSION_DEFAULT)
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024
    # Windows Server deployment: listen on all interfaces; the administrator network settings page controls the public URL.
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "5000"))

    SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

    # ---------------------------------------------------------
    # Database
    # ---------------------------------------------------------
    database_url = os.getenv("DATABASE_URL", "").strip()

    if database_url.startswith("sqlite:///"):
        # Always use an absolute SQLite path.
        #
        # This prevents:
        # sqlite:///instance/pdl_intelligence.db
        # from accidentally becoming:
        # instance/instance/pdl_intelligence.db
        db_file = INSTANCE_DIR / "pdl_intelligence.db"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_file}"
    else:
        SQLALCHEMY_DATABASE_URI = database_url

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ---------------------------------------------------------
    # Mailbox / Microsoft Graph
    # ---------------------------------------------------------
    GRAPH_TENANT_ID = os.getenv("GRAPH_TENANT_ID", "").strip()
    GRAPH_CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "").strip()
    GRAPH_CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET", "").strip()
    GRAPH_MAILBOX = os.getenv("GRAPH_MAILBOX", "").strip()
    MAILBOX_PROVIDER = "graph" if GRAPH_MAILBOX else "outlook"

    # Legacy Outlook settings are retained only as a fallback for local POC use.
    # Production deployments should set GRAPH_MAILBOX and use Graph app-only auth.
    OUTLOOK_MAILBOX = os.getenv("OUTLOOK_MAILBOX", "").strip()

    SYNC_DAYS = int(
        os.getenv("SYNC_DAYS", "7")
    )

    SYNC_LIMIT = int(
        os.getenv("SYNC_LIMIT", "1000")
    )

    INTERNAL_DOMAINS = {
        x.strip().lower()
        for x in os.getenv(
            "INTERNAL_DOMAINS",
            "hpe.com"
        ).split(",")
        if x.strip()
    }

    # Outlook desktop exposes ReceivedTime as a mailbox/client-local wall clock.
    # This application displays operational timestamps in IST.
    OUTLOOK_TIMEZONE = os.getenv("OUTLOOK_TIMEZONE", "Asia/Kolkata").strip()
    OUTLOOK_TIMEZONE_OFFSET_MINUTES = int(os.getenv("OUTLOOK_TIMEZONE_OFFSET_MINUTES", "330"))

    OUTLOOK_SYNC_ENABLED = (
        os.getenv(
            "OUTLOOK_SYNC_ENABLED",
            "1"
        ).lower()
        in ("1", "true", "yes", "on")
    )

    # ---------------------------------------------------------
    # Monitoring assignment notifications
    # ---------------------------------------------------------
    MONITORING_ASSIGNMENT_NOTIFY_ENABLED = (
        os.getenv("MONITORING_ASSIGNMENT_NOTIFY_ENABLED", "1").lower()
        in ("1", "true", "yes", "on")
    )

    MONITORING_APP_URL = os.getenv("MONITORING_APP_URL", "http://127.0.0.1:5000").rstrip("/")

    # ---------------------------------------------------------
    # Scheduler
    # ---------------------------------------------------------
    SCHEDULER_ENABLED = (
        os.getenv(
            "SCHEDULER_ENABLED",
            "1"
        ).lower()
        in ("1", "true", "yes", "on")
    )

    SYNC_INTERVAL_MINUTES = int(
        os.getenv(
            "SYNC_INTERVAL_MINUTES",
            "5"
        )
    )

    # ---------------------------------------------------------
    # Bootstrap administrator
    # ---------------------------------------------------------
    ADMIN_EMAIL = os.getenv(
        "ADMIN_EMAIL",
        "admin@example.local"
    ).strip().lower()

    ADMIN_PASSWORD = os.getenv(
        "ADMIN_PASSWORD",
        "ChangeMe-Immediately"
    )

    ADMIN_NAME = os.getenv(
        "ADMIN_NAME",
        "System Administrator"
    )