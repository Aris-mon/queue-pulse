# V10.14.2 — Qwen Reliability and Intelligence Consolidation

- Qwen3:8b effective timeout floor 240 seconds; old persisted 60/100s settings self-heal.
- Compact 8-field Qwen JSON contract to reduce inference/generation time.
- Compact system prompt and latest-message input capped at 6000 characters.
- Team context reduced to 20 members.
- Secondary risk/frustration/escalation fields derived locally.
- One asynchronous AI worker retained; dashboard/TAT do not wait for inference.
- Deterministic business-policy guardrails remain authoritative.
