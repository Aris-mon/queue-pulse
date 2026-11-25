# V10.5 — Multi-slot monitoring fix

## Monitoring assignments
- A PDL can now have multiple active monitoring slots simultaneously.
- The active assignment key is `(PDL, slot)`, not just `PDL`.
- Assigning a different slot no longer overwrites or reassigns an existing slot.
- Reassigning the same slot to another engineer closes only the previous assignment for that same slot.
- PDL detail Actions now lists all active slot assignments and supports removing each one independently.

## Engineer selector
- Engineer options are de-duplicated across eligible slots.
- Engineers are filtered by the selected monitoring slot and explicit PDL access.

## Slot UI
- Monitoring slot form is responsive at smaller widths/zoom levels.
- Slot action buttons wrap instead of being clipped.
- Every slot shows PDL authorization and current PDL → engineer assignments.

## End-of-slot report
- A scheduler job checks slots every minute.
- At slot end, each engineer who was assigned to that slot receives a complete monitoring report through Outlook COM.
- The report includes received, attended/responded, unattended, TAT breached, response rate, TAT compliance, and PDL-level breakdown.
- A report log prevents duplicate sends and retries failed sends.

## Database migration
- Existing V10.4 SQLite databases that contain the accidental unique `pdl_id` constraint are automatically rebuilt without losing assignment rows.
- PostgreSQL unique `pdl_id` constraint is removed automatically.
