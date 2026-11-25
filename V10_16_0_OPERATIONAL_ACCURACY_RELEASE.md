# V10.16.0 — Operational Accuracy & Notification Release

## Key fixes
- Clarified live email states: Response Pending / Responded / Informational / TAT Breached.
- PDL response coverage now uses response-required workload only.
- PDL TAT compliance now uses actionable workload only.
- TAT breach drill-down filters before conversation collapse and preserves selected date range.
- Priority/Critical drill-down uses both severity and escalation level.
- Critical count uses strongest operational severity/escalation signal.
- Notifications auto-close when a real response is detected across page/live/API queries.
- Removed blanket hiding of team-member notifications; critical operational events remain visible.
- Browser notifications include actionable high/critical missed-call, TAT, escalation and executive events.
- AI Operations Intelligence always shows deterministic/hybrid intelligence while Ollama is pending/unavailable.
- Added missed-call language variants such as "nobody has joined" / "no one has joined".
- Missed customer calls with urgent/waiting context classify as Concerned + Critical.
- Added regression tests for the real escalation/waiting-call case and schedule-vs-recognition.
