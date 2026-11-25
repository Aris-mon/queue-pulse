# V10.13.9 — Qwen3, reporting, live window and notification fixes

- Qwen3 inference results are now retried for legacy fallback/error analyses after Ollama is installed.
- Structured JSON parsing is defensive and reports missing fields precisely.
- The newest authored Outlook message is sent to the model instead of the full quoted thread body.
- Internal/engineer messages remain excluded from customer workload but genuine critical operational events can notify.
- Live Emails defaults to the last 24 hours; historical periods remain selectable.
- Custom reporting no longer falls back to Week before dates can be entered.
- AI admin metrics distinguish historical errors from errors in the last 24 hours.
- Qwen/LLM output remains subject to deterministic identity, recognition, response and severity guardrails.
