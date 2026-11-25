V10.19.1

# AI-request.docx Enhancement Notes

The existing application structure, routes and workflows have been retained. Enhancements were added around the existing functions rather than replacing the original application design.

## Implemented

1. Dashboard Email Status Distribution KPI and PDL Performance moved above the KPI section.
2. Existing PDL detail metrics retained: total, unattended and TAT-breached email visibility.
3. In-tool Notifications page and badge for high/critical escalations, explicit schedule/call misses, and executive-copy risk.
4. Engineer Mapping extended with designation and Executive-level flag, with add/edit/delete preserved.
5. Executive-copy intelligence uses context/sentiment plus configured executive recipients; executive presence alone does not force a critical classification.
6. PDL configuration extended with owner name and owner email.
7. PDL detail CSV export added. Admins, assigned users and configured PDL owners can export permitted PDL data.
8. Live date/time in IST shown next to the signed-in user.
9. Dark-mode contrast additions for the new controls/KPI/notification UI.
10. Authenticated read-only API v1 endpoints added for PDLs, PDL summary and alerts.
11. Duplicate filtering added using Internet Message-ID across multiple PDL copies while preserving same-EntryID resync/upsert behavior.
12. Automatic Reply / Out of Office / delivery-status messages are ignored before intelligence scoring/import.
13. Export endpoints now require authentication and baseline browser security headers were added.
14. Existing SQLite upgrade path extended to add only the new schema fields while preserving current data.

## Production guidance captured in README

- A shared monitoring mailbox can work for the Windows/Classic Outlook POC if it receives the messages and is selected as the Outlook store.
- For production and 15+ PDLs, PostgreSQL, production WSGI/reverse proxy, Microsoft Graph server-side ingestion, centralized monitoring/logging, backups, secrets management and load testing are recommended.
- Security additions are hardening controls, not a compliance certification. Organization-specific privacy, DLP, retention, TLS, SSO, CSRF, vulnerability-management and mailbox-permission reviews remain required.

## Validation performed

- Python syntax compilation passed for `app.py`, `db.py`, `intelligence.py`, `outlook_connector.py`, and `export_routes.py`.
- All Jinja templates parsed successfully.
- Flask test-client validation passed for login, dashboard, PDL configuration, Engineer Mapping, Notifications, API v1 PDL endpoint, PDL detail, and PDL CSV export.

## V5 add-ons (13 Aug 2026)

### Metrics date selector
- Dashboard and per-PDL KPI numbers now support **24H**, **Week**, **Month**, and **Custom** date range.
- Custom dates are interpreted in IST and applied to the existing email query layer.
- Existing access-control filtering remains in place.

### Embedded Local Intelligence Agent
- Added `local_ai_agent.py` using Python standard library only.
- No OpenAI/cloud AI/API/model download is required.
- Analyzes customer emotion, negative/unhappy language, escalation wording, urgency and customer requirement.
- Existing deterministic intelligence remains intact; the local agent enriches and can promote risk when clear customer dissatisfaction/escalation is detected.
- Negative/unhappy customer emails generate a notification; concerned mail generates one when escalation context is also present.
- This is an explainable local NLP/rule agent, not a generative LLM. A true LLM-level semantic model would require a local model runtime/model files or a remote AI service.

### UI cleanup
- Reworked PDL Monitoring layout for clearer labels, consistent field grid, owner/TAT visibility, status, history count and aligned actions.
- Moved CSV/report Export into the Email Intelligence header action area.
- Corrected export button/popover alignment and kept responsive/dark-mode support.
