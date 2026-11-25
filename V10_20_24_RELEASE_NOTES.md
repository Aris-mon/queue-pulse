# V10.20.24 — UI repair and in-application release updater

## Fixed
- Repaired the corrupted `templates/settings.html` that contained repeated/nested Jinja blocks and duplicate Microsoft Graph Mailbox cards.
- Added the missing `templates/mailbox_settings.html`, fixing the HTTP 503 raised when opening Microsoft Graph Mailbox.
- Kept Graph tenant/client/mailbox fields UI-managed; the Client Secret VALUE remains encrypted locally and is never returned to the browser.
- Renamed the primary sync button to **Sync Mailbox** because production synchronization uses the dedicated Microsoft Graph mailbox.
- Added an Administration → Application Upgrade panel.
- Administrators can upload an approved release ZIP, validate it, create a code backup, apply the release and automatically restart the application.
- Upgrade processing runs in a detached helper so the running Flask process does not overwrite its own loaded Python files.
- Runtime data is preserved during upgrades: `instance/`, database files, encrypted Graph mailbox configuration/key, `.env`, `.venv`, and Git metadata.
- Release ZIP path traversal is rejected and the upload is capped at 100 MB.
- Upgrade status is persisted in `instance/updates/update_status.json` so the result is visible after restart.
- `VERSION.txt` is now the default application version source when `APP_VERSION` is not explicitly set.

## Upgrade package requirements
A release ZIP must contain these files at its release root (or one enclosing release folder):
- `VERSION.txt`
- `app.py`
- `server.py`
- `requirements.txt`
- `templates/base.html`

## Production upgrade workflow
1. Open **Administration → Application Upgrade**.
2. Select the approved release ZIP.
3. Confirm the upgrade.
4. Click **Validate & Install Release**.
5. The application restarts automatically.
6. Reopen the site and check the Application Upgrade status.
7. The previous code is retained under `instance/updates/backups/`.

Do not upload `.env`, production database files, or the `instance/` directory in a release package.
