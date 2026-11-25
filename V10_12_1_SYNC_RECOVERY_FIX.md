# V10.12.1 Sync Recovery Fix

## What changed
- Manual **Sync Outlook** now performs a full `SYNC_DAYS` scan instead of using only the last-sync timestamp.
- Scheduled sync remains incremental but now uses a configurable rolling overlap (`SYNC_OVERLAP_MINUTES`, default 120 minutes).
- Zero-candidate scans are written to Events with Outlook source diagnostics.
- Sync result now reports `force_full` and `overlap_minutes`.
- Existing HPE GIF/logo changes are retained.

## Recommended `.env`
```text
OUTLOOK_SYNC_ENABLED=1
SYNC_DAYS=7
SYNC_LIMIT=1000
SYNC_OVERLAP_MINUTES=120
SYNC_INTERVAL_MINUTES=5
```

## Recovery
1. Start Classic Outlook and make sure the target mailbox is visible.
2. Start `python app.py`.
3. Click **Sync Outlook**.
4. The manual sync now scans the full configured 7-day window and reuses the existing EntryID/Message-ID upsert logic, so duplicates are not created.

If Outlook is the **New Outlook** client only, the existing COM connector cannot read the mailbox; Classic Outlook must be installed/configured for this connector.
