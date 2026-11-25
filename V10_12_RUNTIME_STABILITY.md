# V10.12 Runtime Stability

- SQLite uses WAL mode, busy timeout and normal synchronous mode.
- Outlook auto-sync default interval is 5 minutes instead of 1 minute.
- Existing installations with the untouched V10.11 default of 1 minute are migrated once to 5 minutes.
- Outlook collection becomes incremental after the first scan, using a 15-minute overlap from the last successful sync.
- Sync batch size defaults to 10 so SQLite write transactions stay short.
- Only one scheduler instance is created and it owns both Outlook sync and monitoring-slot end reports.
- Monitoring assignment commits before Outlook notification sending.
- Outlook COM remains initialized/uninitialized per worker thread.
- Flask/SQLAlchemy database connections use pre-ping and a 30-second SQLite connection timeout.
