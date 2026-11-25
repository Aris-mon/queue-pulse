# V10.20.25 — HPE Logo/UI Patch

## Fixed
- Replaced the conflicting historical HPE logo CSS stack with one final logo override.
- Login page and application sidebar now use the same HPE logo asset.
- Removed duplicate/circular logo treatments caused by legacy `.brand-mark`, `.logo-mark`, `.hpe-exact-logo` and GIF rules.
- Prevented dark mode and global image rules from changing the logo.
- Added cache-busting (`v=10.20.25`) so browsers do not continue using the previous logo.
- Kept the existing HPE artwork already included in the application as `static/images/hpe-logo-exact.png`.
- No Graph mailbox, PostgreSQL, PDL discovery, AI, response correlation, TAT or notification logic was modified.

## Files changed
- `templates/base.html`
- `templates/login.html`
- `static/css/hpe_logo_patch.css` (new)
- `VERSION.txt`
- `V10_20_25_HPE_LOGO_PATCH.md` (new)

## Upgrade
Copy/ upload the V10.20.25 package using the application's existing Administration upgrade mechanism.

After upgrade:
1. Open the login page and verify the HPE logo.
2. Sign in and verify the sidebar logo.
3. Test light and dark mode.
4. If an old logo remains, perform one hard refresh (`Ctrl+F5`).

## Important
The uploaded `HPE-logo-full-clr-pos-rgb` image supplied with the request was not made the production asset because its pixels contain a large opaque black background and a green mark, which would introduce a black rectangle into the application. The existing `hpe-logo-exact.png` is the transparent HPE artwork already used by this release and is therefore retained as the production-safe asset.
