# V10.13.3 — Critical Event Intelligence

## Key behavior changes

- A configured PDL/team member is no longer discarded before intelligence analysis. Their messages remain excluded from customer workload/TAT metrics, but customer-facing operational events are analyzed.
- Missed customer calls / engineer no-show events can be classified as CRITICAL even when the sender is internal/engineer.
- Recognition/courtesy messages cannot generate critical/high notifications simply because of stale severity or positive wording.
- Quoted history no longer drives current-message escalation/severity decisions.
- Browser/desktop notifications are reserved for unread `critical_escalation` alerts. Other operational alerts remain visible in the in-app Notifications/Reports pages.
- Critical notifications are deduplicated at conversation/thread level while an earlier critical alert remains unread.
- The AI prompt receives the configured PDL team roster so sender identity and operational role are explicit context.
- Default local model is now configurable with Qwen3 8B as the recommended starting model; Llama 3.1 8B and Mistral 7B remain optional benchmark candidates.
