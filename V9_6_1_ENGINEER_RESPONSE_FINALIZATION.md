# V9.6.1 Engineer / Response Finalization

- Restored and expanded **Map Engineers**.
- Added **Engineer Groups** with optional PDL mapping.
- Each mapped person has an explicit role: Engineer/Responder, Manager, or Executive.
- Engineer/Responder mail is excluded from monitored email counts, but is retained transiently for response/TAT matching.
- A grouped engineer is accepted as a responder only for the PDL linked to that group. Ungrouped engineers retain backward-compatible all-PDL response matching.
- Manager/Executive messages are not hidden as engineer traffic. Concern/escalation/high-priority communication from them remains visible and is pushed to Notifications.
- Courtesy/Recognition/no-response-required messages remain excluded from response-required workload metrics.
- Existing unanswered age logic remains active for response-required emails.
- Marking a notification read is now a durable manual acknowledgement: all notifications for that email are closed, the email moves to Responded, and re-sync will not reopen it. `response_source=notification_mark_read` records this distinction.
- Browser **Enable alerts** is now a real enable/disable control. It clearly reports HTTPS/browser permission limitations while in-app Notifications remain functional.
- AI Command Center gained safe-mode loading and stronger API error handling.
- Backward-compatible SQLite schema migration adds the new group/role/response-source fields without deleting existing data.
