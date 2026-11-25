# V10.20.9 — AI/context, dashboard consistency and notification/UI corrections

- Hardened Ollama JSON handling for truncated/unterminated Qwen3 responses with a one-time larger-output retry.
- Increased the default local classification output budget from 180 to 320 tokens.
- Kept the contextual layer authoritative and projected corrected Qwen intent into `Email.email_category`, fixing Live Emails type changes after AI analysis.
- Strengthened signature stripping so escalation contact text in Outlook signatures cannot create false escalations.
- Unified dashboard, Live Operations and PDL detail workload/priority calculations around monitored/actionable email semantics.
- Removed TAT Breached from the Dashboard Priority Distribution; TAT remains an independent operational KPI.
- Corrected the PDL detail TAT-breached visual to a readable red/light-red semantic treatment.
- Added final AI-driven notifications for TAT breach, HIGH/CRITICAL priority, escalation and configured executive copies.
- Added compact collapsible Windows Server Network Configuration.
- Reworked Customer AI Evaluation training into a collapsible workspace with cleaner two-column controls and expanded filters.
- Added response and escalation filters to Customer AI Evaluation.
- Reduced Create User checkbox dimensions and replaced the oversized browser checkbox appearance.
