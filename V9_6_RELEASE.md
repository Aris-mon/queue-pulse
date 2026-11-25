# V9.6.0 — Response Intelligence, Engineer Map & Notifications

## Fixed
- AI Command Center 500 error caused by timezone-aware vs naive datetime arithmetic.
- Restored Map Engineers navigation without changing engineer response-matching behavior.
- Browser alert button now has a working permission/notification state and polls a dedicated notification endpoint.

## Response tracking
- Added response_required, monitoring_excluded and exclusion_reason fields.
- Courtesy/acknowledgement messages such as “thank you”, “thanks”, “noted”, “FYI” and explicit “no response required” are excluded from operational workload when no real request/risk is present.
- A real request in the same message still keeps the email tracked.
- Mapped engineer messages remain excluded from inbound counts and are used as response evidence for the original customer email.
- Unresponded tracked emails now expose human-readable age such as 2h 14m or 1d 3h.

## Executive/manager intelligence
- Engineer Map supports executive/manager designation as before.
- Manager/executive recipient or sender creates a notification only when escalation, concern, negative/concerned sentiment, or high/critical operational risk is detected. Routine FYI/thank-you copies do not create notification noise.
