# V10.20.10 — monitoring URL, AI queue metrics, notifications and collapsible UI

- Fixed background monitoring-slot report URL generation outside Flask request context.
- Fixed AI processing metric so PROCESSING and PENDING/queued are counted separately using the latest AI analysis per email.
- Administration AI analyzed count now includes all completed analyses; model/hybrid breakdown is shown separately.
- Notification center, live badge, API and browser notification polling now use `Alert.created_at` as notification freshness.
- AI-generated TAT/escalation/high/critical/executive alerts are no longer rejected because the source email is older.
- Evaluation filter and Train AI controls use Smart Filters styling.
- Events & Logs filters are collapsible.
- Configured PDL cards are individually collapsible.
