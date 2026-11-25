# V10.13.8 — Notification Period + Internal/System Guard

- Notifications now default to the same **Last 24 hours** operational window as Reports.
- Notifications support 24h, week, month and custom periods.
- Notification badge is scoped to the current 24-hour operational window so stale unread alerts do not appear as current work.
- Browser critical-notification API is limited to critical alerts created in the last 24 hours.
- Internal/system schedule-miss messages no longer create ordinary high notifications.
- Genuine critical operational events remain eligible for critical notifications even when the sender is internal.
- Startup cleanup closes legacy non-critical notifications attached to internal/engineer emails that are already marked Not tracked.
- The example pattern “no one joined the meeting; please let us know a convenient time” is treated as internal/informational unless the independent critical-event resolver confirms real customer/business impact.
