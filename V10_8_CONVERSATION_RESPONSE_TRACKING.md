# V10.8 — Conversation View + PDL Member Response Timestamp

## Conversation view
- Email, escalation, dashboard and PDL recent-message lists collapse repeated messages into one row per Outlook ConversationID.
- If ConversationID is unavailable, normalized subject + PDL is used as a fallback.
- The latest message in each thread is shown in operational lists.
- Each thread with more than one stored message exposes `Show conversation (N)`.
- `/conversation/<email_id>` expands all stored messages in that thread.
- Underlying email rows are never deleted or merged in the database.

## Response tracking
- First qualifying response remains the TAT anchor.
- `last_response_at` records the latest qualifying response from a PDL-authorized engineer.
- `last_response_engineer_name/email` identify the latest responder.
- Existing rows are backfilled from first-response fields and corrected on the next Outlook sync.
- UI shows Responded + responded timestamp and responder.
- Engineer responses remain monitoring evidence and are not imported as new action items.

## PDL authority
Team Management engineer PDL access remains the authoritative response permission. Monitoring-slot assignment does not restrict whether an engineer's valid PDL response closes an email.
