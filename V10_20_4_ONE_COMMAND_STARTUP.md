# V10.20.4 — One-command startup and first-run setup

## Start

Run only:

`python app.py`

The launcher automatically:

1. Creates `.venv` if it does not exist.
2. Installs/updates `requirements.txt` only when its SHA-256 changes.
3. Runs the Windows pywin32 post-install step once when available.
4. Re-executes `app.py` using `.venv\\Scripts\\python.exe` on Windows.
5. Opens the application browser automatically unless `OPEN_BROWSER=0` is set.

A shell-level `activate` is not required. The application process itself runs under the virtual-environment interpreter.

## First launch

If the database has no users, `/setup` is shown automatically. The wizard collects:

- Outlook mailbox/store email
- System Administrator name
- System Administrator username/email
- Administrator password and confirmation

The mailbox is persisted to `.env` and `OUTLOOK_SYNC_ENABLED=1` is enabled. Outlook/COM authentication remains controlled by the Windows Outlook profile; the application does not store an Outlook password.

## Existing installations

Existing users are not overwritten. If users already exist, the normal login page is shown.
