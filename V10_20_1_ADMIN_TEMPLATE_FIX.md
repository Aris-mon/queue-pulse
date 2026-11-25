# V10.20.1 Administration Page Fix

## Issue
The previous V10.20.1 package accidentally placed the contents of `app.py` into `templates/admin.html`.

When `/admin` rendered the Administration page, Flask therefore displayed Python source code instead of the Administration UI.

## Fix
- Restored `templates/admin.html` as a real Jinja/HTML template.
- Preserved the existing Outlook sync and AI configuration sections.
- Added the Windows Server Network Configuration section expected by the updated `/admin` route.
- The network section exposes adapter, hostname, IPv4, prefix length, gateway, DNS, NTP and generated API URL.
- Added an explicit confirmation before applying Windows networking changes.

## Validation
- Python compilation: passed.
- Jinja template parsing: passed.
- Existing automated tests: 41 passed.
