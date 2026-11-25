# V10.4 Monitoring Activities, Engineer Notifications & Team-Only Engineer Management

## Changes

- Removed the user-facing **Map Engineers / Engineer Mapping** navigation option.
- `/engineers` is retained only as a compatibility redirect to **Team Management** so old bookmarks do not break.
- Team Management is now the authoritative UI for engineer onboarding and PDL access.
- Added a dedicated **Monitoring Slot Activities** page at `/monitoring-slots/<slot_id>/activities`.
- PDL detail no longer embeds detailed slot statistics/history. It provides **View monitoring slot activities** and keeps assignment controls under **Actions**.
- Monitoring slot rows and assignment-history slot links open the dedicated activity page.
- Activity page reports total **Received, Attended, Unattended, TAT Breached**, response rate and TAT compliance.
- Activity page provides a PDL-level breakdown and assignment history.
- Added **Export activity CSV** at `/export/monitoring-slot/<slot_id>.csv` with the selected metric date range.
- Monitoring assignment now sends an email notification to the assigned engineer through the local Outlook desktop client using `pywin32`.
- Assignment notification includes PDL, group, slot, IST start/end time and a link to the slot activity page.
- Assignment is committed even if Outlook is unavailable; the UI reports the notification failure rather than rolling back the assignment.
- Added configuration switches:
  - `MONITORING_ASSIGNMENT_NOTIFY_ENABLED=1`
  - `MONITORING_APP_URL=http://127.0.0.1:5000`

## Notification behavior

The notification uses the same Outlook desktop profile already required by the application's Outlook integration. No SMTP password is stored by this feature.

If notifications are disabled, assignments continue to work normally.

## Validation

- Python compilation: passed
- Jinja template compilation: passed for affected templates
- Existing intelligence tests: 8/8 passed


## COM threading fix

Monitoring assignment email notifications now explicitly call `pythoncom.CoInitialize()` on the Flask request thread before using Outlook COM, and `pythoncom.CoUninitialize()` in a `finally` block. This fixes `(-2147221008, 'CoInitialize has not been called.')` when assignments are made from a web request.
