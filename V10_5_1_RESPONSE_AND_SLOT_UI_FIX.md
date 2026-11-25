# V10.5.1 — PDL-member response detection and monitoring-slot UI fix

## PDL-member-aware response detection
- Team Management `Engineer -> PDL access` is the authoritative responder permission.
- Any active engineer/leadership member with explicit access to the PDL can close the inbound email when their outbound reply is detected.
- Monitoring-slot assignment is intentionally independent of response recognition.
- An engineer without explicit PDL access cannot mark the email responded merely by sending a similarly titled message.
- Response attribution is stored as `response_engineer_name` and `response_engineer_email`.
- Email detail shows **Responded by** when a qualifying PDL-member reply is found.
- Supports replies found in local Sent Items, shared PDL Sent Items, and monitored mailbox copies using In-Reply-To, ConversationID, and safe subject/recipient fallback.

## Monitoring-slot creation UI
- The Add slot action is moved below the PDL access selector.
- The action spans the form width and remains visible when the Team Management right column becomes narrow because of browser zoom.
- Responsive rules prevent clipping/overflow at desktop zoom and smaller viewports.

## Preserved V10.5 behavior
- Multiple active slots can monitor the same PDL.
- Only the same PDL + same slot is reassigned.
- Different slots remain independent.
- Slot PDL access, assignment history, slot activities and end-of-slot reports are preserved.
