# V10.20.23 — Initial Setup UI Fix

## Fixed
- Removed the global application stylesheet from the first-run setup page so shared UI rules cannot distort the setup wizard.
- Rebuilt the setup page as a self-contained responsive layout.
- Prevented stale/repeated `Please sign in to continue.` Flask-Login messages from flooding the setup page.
- Added a first-run unauthorized handler that redirects directly to `/setup` while no administrator exists.
- API unauthorized requests now return HTTP 401 JSON after setup is complete.
- Kept Graph app-only mailbox configuration and encrypted secret handling unchanged.
- Added clearer Graph connection and administrator sections.

## Upgrade
Replace the application directory with this release ZIP and start with:

```powershell
python app.py
```

Existing database and encrypted Graph configuration are preserved when the same application data directory is retained.
