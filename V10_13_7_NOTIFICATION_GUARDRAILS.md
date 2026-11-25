# V10.13.7 — Engineer Notification Guardrails

- Configured engineer/team-member emails can never create ordinary schedule-miss or executive/priority notifications from their own coordination mail.
- Engineer emails remain excluded from customer response-required/TAT workload.
- Engineer-originated messages can still create a critical notification when the operational event is independently validated as genuinely critical (for example, a missed customer call with deployment blocked).
- AI merge forcibly preserves `response_required=false` and `monitoring_excluded=true` for configured engineer senders, preventing LLM output from reclassifying them as customer workload.
- Existing critical notification thread de-duplication remains active.
