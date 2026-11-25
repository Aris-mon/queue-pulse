V10.19.1

# PDL Email Intelligence – Production-oriented POC

This replacement project upgrades the original Outlook PDL dashboard into a multi-user operations control center.

## Included

- Modern HPE-style operations UI
- User login and role-based administration
- User-specific PDL monitoring
- PDL/TAT configuration
- Engineer directory
- Customer vs engineer vs internal vs unknown sender classification
- Product and issue classification
- Evidence-based escalation scoring
- Sentiment and severity
- TAT compliance
- Customer-waiting vs engineer-action-required
- Conversation details with "why this risk?" evidence
- Outlook classic desktop COM/MAPI integration
- Primary mailbox selection
- Online Archive exclusion
- Inbox + subfolder scan
- Last N days
- Read-only Outlook access
- SQLite by default
- PostgreSQL-ready DATABASE_URL
- Health endpoint

## Important Outlook behavior

The connector intentionally avoids `DispatchEx()` and first attaches to the existing Outlook COM instance with `GetActiveObject()`. If no Outlook instance exists, it falls back to normal `Dispatch()`.

The connector never calls `Outlook.Quit()` and never modifies messages.

## First setup

PowerShell:

    py -3.11 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    Copy-Item .env.example .env
python -m pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org
Edit `.env`:

    SECRET_KEY=<long-random-secret>
    OUTLOOK_MAILBOX=<main Outlook mailbox display name>
    SYNC_DAYS=7
    SYNC_LIMIT=1000
    INTERNAL_DOMAINS=hpe.com

Then:

    python app.py

Open:

    http://127.0.0.1:5000

The bootstrap admin credentials come from `.env`. Change them immediately.

## Initial configuration

1. Sign in as admin.
2. PDLs -> add each PDL with its SMTP address and TAT.
3. Engineer Map -> add internal engineer email addresses.
4. Users -> create users and assign each user only the PDLs they need.
5. My PDLs -> users can change their own monitoring scope.
6. Administration -> Discover Outlook stores.
7. Keep the main mailbox selected. Online Archive must remain excluded.
8. Start Outlook and click Sync Outlook.

## Escalation model

Escalation is not `if "escalation" in body`.

The score combines independent evidence:

- explicit escalation / management language
- production outage / business impact
- customer frustration
- repeated follow-ups
- urgency
- TAT breach
- large recipient spread
- customer-originated communication

Levels:

- 0–34: Low
- 35–59: Medium
- 60–79: High
- 80–100: Critical

The detail page shows the reasons behind each score.

## Production deployment notes

For a real shared deployment, use PostgreSQL instead of SQLite:

    DATABASE_URL=postgresql+psycopg://user:password@host:5432/pdl_intelligence

Put Flask behind IIS/nginx or another enterprise reverse proxy and use a proper WSGI server.

The current Outlook connector is Windows/Classic-Outlook dependent. For server-side production, replace the COM connector with Microsoft Graph application/delegated authentication. Keep this COM connector for the local Windows POC.

## HPE logo

The UI uses an HPE-styled placeholder mark so the project is self-contained. If your organization authorizes use of the current HPE logo, replace `static/img/hpe-logo.svg` with the approved asset from HPE's official media-assets page:

https://www.hpe.com/us/en/newsroom/media-assets.html

HPE states that its logos are trademarks and that use is subject to its logo/trademark guidelines.


## V4 enhancements

- Automatic Outlook synchronization with an Administration UI toggle and configurable interval.
- Email timestamps are displayed in India Standard Time (IST / Asia/Kolkata); database storage remains UTC.
- PDL create/edit/enable/disable/delete.
- User create/edit/delete and PDL-access mapping management.
- Engineer mapping create/edit/delete.
- Events & Logs page with synchronization and configuration audit events.
- Responsive full-screen UI that adapts to desktop/tablet/mobile resolutions.
- Light/dark theme toggle persisted in the browser.
- The existing manual Sync Outlook action remains available.


## AI-request.docx enhancement pack
Added dashboard KPI/status distribution with PDL Performance moved to the top, PDL owners and CSV export, escalation/schedule-miss/executive-copy notifications, executive designation mapping, IST clock, authenticated API v1, duplicate/OOO filtering and baseline security hardening. For 15+ PDL production use PostgreSQL, production WSGI/reverse proxy, Microsoft Graph server-side ingestion, centralized logging, backups, secrets management and load testing. A shared mailbox can work as the POC collection point, but production mailbox retention/access/privacy policies must be reviewed. These controls are not a formal compliance certification; production should add enterprise SSO, CSRF protection, TLS, vault-managed secrets, vulnerability scanning and organization-specific privacy/DLP review.

## V9.5 near-real-time monitoring

- The application UI polls `/api/v1/live` every 15 seconds.
- This browser polling updates the live 24-hour operations strip and notification badge without querying Outlook.
- Outlook auto-sync can be configured to 1 minute from Administration.
- Enable browser notifications from the top bar to receive critical/high priority and operational alert notifications.
- AI Command Center: `/ai-command-center`.
- The Engineer Map route/backend remains intact, but its sidebar navigation is intentionally hidden; AI Command Center replaces that UI slot.
- `static/images/pdl-mascot.gif` is a real 16-frame animated GIF using the supplied panda artwork and HPE green `#01A982` background.

### Important
The 15-second browser polling does not make Outlook itself real-time. It only makes the web dashboard responsive to data already collected into the database. For the fastest practical setup with Classic Outlook, enable Auto-sync and use a 1-minute sync interval.


## Administrator in-application upgrades (V10.20.24)

Production fixes/releases no longer require manually replacing the application directory.

1. Build an approved ZIP with `VERSION.txt`, `app.py`, `server.py`, `requirements.txt` and the application source/templates.
2. Sign/approve the ZIP using your normal internal release process.
3. In the running application open **Administration → Application Upgrade**.
4. Select the ZIP and confirm the upgrade.
5. The application validates the package, stages it, backs up the current code, stops the running process, replaces application files and restarts `app.py`.
6. Refresh the site after the restart. The last upgrade result is shown on the Administration page.
7. Backups are retained under `instance/updates/backups/`.

The upgrade deliberately preserves `instance/`, `.env`, `.venv`, the local database, PostgreSQL connection configuration, and the encrypted Microsoft Graph mailbox configuration/key. Do not include production secrets or database files in release ZIPs.

The release ZIP is limited to 100 MB compressed / 400 MB extracted and rejects unsafe ZIP paths. The updater is intended for the Windows Server deployment where `app.py` launches `server.py`; production should keep Flask debug mode disabled.


## V10.20.26 local AI additions
This release adds local-only RAG (ChromaDB + all-MiniLM-L6-v2) and LangGraph orchestration around the existing deterministic + Ollama intelligence engine. It does not require LangSmith or any cloud AI service.

## V10.20.27 Local RAG / training setup
See `LOCAL_RAG_COMPLETE_SETUP.md` for dependency installation, local-model staging, diagnostics, and the new CSV/JSON training sample upload workflow.
