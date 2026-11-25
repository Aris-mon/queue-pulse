# V10.20.17 — Import resilience + continuous AI queue + final intent projection

## 1. Outlook import cannot be blocked by enrichment

A matched Outlook message is now guaranteed a minimal database import even when a
secondary response/TAT/intelligence/notification stage raises an exception.

- Preserves the email with privacy-safe fallback metadata.
- Records the original exception without message content.
- Prevents the observed `pdl_matched > 0` / `imported = 0` failure mode.
- The normal AI/enrichment pipeline can upgrade the fallback row later.

## 2. AI queue continuously refills

The previous worker accepted one batch (default 8) after sync. Once that batch
finished, no new jobs were submitted until another sync/manual action occurred.

V10.20.17:

- Keeps the CPU-safe single Qwen worker.
- Automatically refills the next batch after each completed job.
- Exposes separate `processing` and `queued` counts.
- Does not log or persist email content.
- Avoids pretending that `0 processing / 0 queued` means the mailbox is fully AI-reviewed.

## 3. AI intent is authoritative for Live Emails

The final AI `intent` now takes precedence over the stale sync-time
`email_category`.

Example:

- deterministic pass: `escalation`
- AI review: `SCHEDULE_REQUEST`
- Live Emails type: `schedule_request`

When AI explicitly says the message is not an escalation, a stale escalation
level/alert created by the deterministic fast pass is also retired.

This prevents a signature/footer containing the word "escalation" from keeping
the live message classified as an escalation after AI has corrected it.

## 4. Member discovery remains initial-sync-only

No recurring PDL membership discovery was added to this release. Existing
manual member mappings and discovered members continue through the same response
correlation path.

## Privacy

No email subject, body, sender, recipient, folder content, or mailbox data is
written into diagnostic messages by these fixes.
