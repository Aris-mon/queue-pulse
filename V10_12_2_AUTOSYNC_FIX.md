# V10.12.2 Auto Sync Reliability Fix

This build adds a dedicated reliable background sync service.

## Behavior
- Auto Sync defaults to ON.
- Only one Outlook sync can run at a time.
- Scheduler/background threads initialize and uninitialize Outlook COM.
- Transient Outlook/SQLite errors retry up to 3 times with exponential backoff.
- Failed SQLAlchemy transactions are rolled back.
- Sync status is persisted.
- Status endpoint: GET /api/auto-sync/status

## Recommended .env
OUTLOOK_SYNC_ENABLED=1
SCHEDULER_ENABLED=1
SYNC_INTERVAL_MINUTES=5
SYNC_DAYS=7
SYNC_LIMIT=1000
SYNC_OVERLAP_MINUTES=120

## Important
Classic Outlook desktop/MAPI must be installed and the mailbox profile must
be available to the Windows account running the application. New Outlook
does not provide the same COM automation interface.

## Startup
    python app.py
