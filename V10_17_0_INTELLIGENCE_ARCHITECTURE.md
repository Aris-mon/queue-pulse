# V10.17.0 — Intelligence Architecture & Operational Accuracy

## Root cause addressed

Earlier releases stored Qwen results in `AIAnalysis` but the operational UI,
reports and filters continued reading the deterministic fields on `Email`.
Therefore Qwen could scan an email without changing the visible classification.

V10.17.0 makes the final hybrid decision authoritative and projects it back to
the Email record before commit.

## Architecture

- Outlook sync remains fast and deterministic for mailbox ingestion, sender identity,
  PDL membership, response evidence, TAT and basic workload facts.
- Qwen3 8B is an asynchronous contextual enrichment layer.
- Qwen is invoked only for semantic/high-risk/ambiguous cases, not every routine
  schedule/FYI email.
- A single bounded worker prevents the local 8B model from slowing the web app.
- Final AI results update Email sentiment/category/severity/escalation/response
  requirement and summary fields used by the dashboard and reports.
- Engineer/internal workload exclusion remains deterministic and cannot be
  overridden by the model.
- Current-day notifications use the email's received date in IST, not alert
  creation time.

## Recognition

- "Thank you", "Thanks", "Noted" and courtesy-only messages are not recognition.
- "Successful completion" alone is not recognition.
- Recognition requires substantive appreciation/praise of work, service, engineer,
  delivery or outcome.
- A real schedule/update/follow-up/technical request always outranks a polite closing.
- Words such as call, meeting, installation or support alone do not imply a schedule
  request.

## Canonical workflow status

- Responded
- Awaiting Response
- Informational
- TAT Breached

Legacy status URLs remain accepted as aliases.

## Notifications

- Old emails reprocessed today cannot create a current-day notification.
- Critical notifications require a genuine critical escalation/operational event.
- Engineer/internal messages can generate a critical operational alert only when
  they describe a genuine customer-impacting event.
- Duplicate critical alerts remain thread/email deduplicated.

## Validation

- Python compilation: PASS
- Intelligence regression tests: 20/20 PASS
- Tested examples include schedule vs recognition, courtesy, engineer exclusion,
  missed customer call, and negative customer dissatisfaction.
