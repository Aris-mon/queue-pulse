# V10.7 – PDL Engineer Response Tracking + Auto-Sync Default

## Response tracking
Engineer replies are treated as response evidence, not workload/action emails.
A reply from an active engineer is recognized when the engineer has explicit PDL access in Team Management (legacy PDL mappings remain supported).

Response evidence is now collected from:
- Primary Outlook Sent Items
- Configured/shared PDL Sent Items
- Accessible Outlook mailbox stores belonging to configured engineers
- Engineer reply copies visible in monitored PDL/shared inboxes

Matching priority:
1. In-Reply-To -> Internet Message-ID
2. Outlook ConversationID
3. Normalized subject + original customer sender is a recipient

Engineer outbound messages are never imported as new workload rows.
The original customer email is updated with responded status, first response time, responder name/email and TAT calculation.

## Auto-sync
Fresh installations now default `auto_sync_enabled` to `1` while preserving an explicit stored `0` if an administrator previously disabled it.
The scheduler continues to run every minute and honors the persisted sync interval.
