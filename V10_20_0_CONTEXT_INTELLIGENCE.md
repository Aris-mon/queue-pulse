# V10.20.0 — Context Intelligence

V10.20.0 keeps V10.19.1 as the baseline and adds a context layer for local Qwen testing.

## Processing model

1. Outlook ingestion remains deterministic and does not wait for Qwen.
2. The email is immediately available to Live Emails.
3. A compact operational context is built in the background:
   - latest authored message
   - recent messages in the same Outlook conversation
   - customer/internal/engineer identity
   - mapped PDL and authorized engineers
   - response/first-response/last-response facts
   - TAT and TAT breach
   - planned maintenance vs active outage signals
   - blocker, missed-call, urgency and explicit escalation signals
4. Qwen3:8B receives the compact context and interprets meaning.
5. Deterministic decision guardrails reconcile the model result.
6. The final result is projected back onto the Email row so the dashboard/reporting/notifications use the AI result.

## Important accuracy changes

- Stale `Email.severity` is no longer the primary priority input during AI re-analysis.
- Planned downtime is separated from active outage.
- Active outage cannot remain NEUTRAL.
- A signature such as `For any escalations, please contact...` is excluded from escalation analysis.
- Engineer/internal identity remains an application fact.
- Engineer/internal messages can still be CRITICAL when they report a genuine customer-impacting event.
- Recognition requires substantive appreciation; courtesy alone is not recognition.
- Current message intent outranks old thread labels.
- Manual AI analysis does not hide the email because visibility remains independent of workload exclusion.

## Performance design

The context layer is asynchronous through the existing AI worker. It does not block mailbox ingestion or the Live Emails page. Qwen receives a bounded context instead of the entire mailbox/thread history.

For the current test machine, keep `AI_THINK=false` and use a small structured response. The GPU VM can later increase concurrency/model capacity without changing the business decision architecture.
