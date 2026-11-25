# V10.20.16 — Sync / Intelligence Import Fix

## Critical fix

V10.20.15 referenced `intel` in the TAT calculation before `build_intelligence(...)`
assigned it. The per-message exception handler then skipped every PDL-matched message,
which produced the observed combination:

- inbox_scanned > 0
- pdl_matched > 0
- imported = 0
- response_reconciled = 0

V10.20.16 builds the base intelligence result before any `intel` access.

## Response/TAT behavior

- Response evidence is still collected once per sync and reused across candidate emails.
- Recognition/appreciation messages are excluded from TAT-breach calculation.
- Configured engineer/team-member messages remain workload-exempt.
- If TAT changes the intelligence result, intelligence is rebuilt with the final TAT state.

## Privacy-safe diagnostics

Sync error entries no longer include Outlook folder names or message subjects.
They contain only a processing stage, exception type, and truncated exception text.

## Discovery behavior

Initial PDL member discovery remains separate from recurring email synchronization;
normal syncs do not rediscover PDL membership.
