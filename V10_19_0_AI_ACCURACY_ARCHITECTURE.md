# V10.19.0 — AI Accuracy Architecture

- Every current-day customer email is eligible for asynchronous local-model review.
- Deterministic rules remain the immediate safety/metrics layer.
- Actual outage/service-down events are critical; planned downtime questions are high-risk/schedule context, not proof of an active outage.
- Signature/footer escalation language is excluded from escalation detection.
- Thread status uses RESPONDED / AWAITING_RESPONSE / TAT_BREACHED / INFORMATIONAL.
- Existing current-day analyses from older prompt versions are reprocessed once.
- Qwen inference remains on a single background worker so the web UI is not blocked.
