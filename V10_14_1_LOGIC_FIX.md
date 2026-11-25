# V10.14.1 — Logic, Notification Date, Recognition and Qwen Reliability

- Qwen output schema reduced to 13 fields; operational facts remain deterministic.
- Qwen timeout migrated from legacy 60s to 180s for qwen3:8b and token cap to 500.
- AI remains asynchronous and single-worker; historical bulk reprocessing is bounded.
- Recognition cannot override schedule/update/follow-up/support/information intent.
- Stored email_category is never trusted during re-analysis; newest message is recomputed.
- Critical notifications are created only for emails received on the current IST date and are deduplicated per email.
- Notification badge/API/page filter by email received date, preventing old emails reprocessed today from becoming current notifications.
- Alerts page defaults to Today (IST); Live Emails remains Last 24 hours.
