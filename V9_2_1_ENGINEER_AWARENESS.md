# v9.2.1 — Engineer Awareness Addition

This build makes one targeted change on top of v9.2.

## Map Engineers is now authoritative

Active email addresses configured under **Map Engineers** are treated as known engineers.

- Emails originating from a mapped engineer are **not imported as monitored/actionable PDL emails**.
- Those messages are still retained transiently during sync as **response evidence**.
- A mapped engineer reply can mark the original customer email as `responded=True`, set `first_response_at`, calculate `response_minutes`, and validate TAT.
- Response matching uses, in order, Outlook conversation identity, `In-Reply-To` / Internet Message-ID linkage, and a conservative fallback of normalized subject plus the original customer being a recipient.
- Response candidates are isolated by PDL so a reply in one PDL cannot satisfy an unrelated email in another PDL.
- Other internal-domain senders who are not in Map Engineers remain classified as `internal`; mapped addresses are the explicit engineer identity list.
- No Outlook/Exchange/Entra write operation is introduced. The feature only changes local monitoring/classification and response tracking.

## Sync diagnostics

The sync result now reports:

- `engineer_ignored` — mapped-engineer messages excluded from monitoring in that scan.
- `engineer_response_candidates` — mapped-engineer messages available as response evidence.

## Existing cached rows

The change prevents mapped-engineer emails from being imported on future syncs. It intentionally does not silently delete historical rows already stored in the local application database. If testing against an old database, use the application's normal data-reset/fresh-sync workflow if you want a completely clean view.
