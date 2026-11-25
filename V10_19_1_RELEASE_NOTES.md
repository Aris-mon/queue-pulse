# V10.19.1 — Visibility / AI Analysis Correction

## Critical fix
Manual AI analysis no longer causes an email to disappear from Live Emails.

### Root cause
`monitoring_excluded` was being used both for workload/TAT exclusion and UI visibility.
When AI classified a customer message as informational or recognition, the AI projection set `monitoring_excluded=True`; Live Emails filtered that row out.

### Correct model
- `visible_in_monitoring`: whether the email remains visible in Live Emails / Customer AI Evaluation.
- `monitoring_excluded`: whether the email contributes to workload/TAT metrics.
- `response_required`: whether an engineer action is required.

Informational and recognition emails therefore remain visible but do not inflate workload/TAT metrics.

### Existing database
Startup migration adds `visible_in_monitoring` with default `1` and repairs NULL values. Existing email records are preserved.

### Manual AI Analyze
The manual Analyze with AI route explicitly preserves visibility before queuing analysis.
