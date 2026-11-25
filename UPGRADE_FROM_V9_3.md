# Upgrade to V9.5.0

This release is packaged as a complete project. Back up the existing project and database before replacing files.

## Main changes

1. Near-real-time `/api/v1/live` endpoint.
2. Browser live polling every 15 seconds.
3. 1-minute recommended Outlook auto-sync.
4. Desktop browser notifications for new critical/high priority emails and operational alerts.
5. Live 24-hour operations strip on the main dashboard.
6. AI Command Center page.
7. Engineer Map backend and route retained; sidebar link hidden.
8. Supplied panda artwork converted into a real animated 16-frame GIF.
9. Existing V9.3 IST/Outlook time correction retained.
10. Dark-mode styles added for all V9.5 surfaces.

## Important database note

No new database table is required by the V9.5 live monitoring layer. Existing tables are reused.

## Startup

Use the same `.venv` and installation procedure as the existing project:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env -Force
python app.py
```

Preserve your existing `.env` values instead of overwriting a working production `.env`.

## Outlook auto-sync

Administration -> Automatic Outlook sync -> Enabled -> 1 minute.

The collector still runs through the existing Outlook/COM integration. The browser does not connect to Outlook.

## Desktop notifications

Open the application in Chrome/Edge and click `Enable alerts` once. Browser permission is required by the browser; the application does not bypass that permission.
