# V10.14.0 — Qwen performance, structured-output and intelligence hardening

## Fixes
- Reduced Ollama structured JSON output from 30+ fields to the core classification fields. This lowers generation time and reduces structured-output failures on Qwen3 8B.
- Reduced default AI output budget to 900 tokens; the model is explicitly instructed not to emit chain-of-thought.
- Increased Ollama keep-alive to 30 minutes to avoid repeated model reloads during active monitoring.
- Ollama worker concurrency reduced to one local inference at a time to avoid CPU/RAM contention on typical Windows test machines.
- Added compatibility retry for Ollama builds that reject the `think` option.
- Nullable schema fields changed to simple strings for broader Ollama/JSON-schema compatibility.
- AI queue is now bounded to 25 recent emails and only retries recent failed/fallback analyses after 15 minutes. It no longer reprocesses hundreds of historical emails during every sync.
- AI queue only auto-processes the last 24 hours; deterministic rules remain immediately available for older mail.
- AI statistics now report the latest analysis per email instead of counting every historical retry/error log as a current error.
- Sentiment now uses the newest authored message for the local guardrail and explicitly treats courtesy-only messages as neutral.
- Strong deterministic sentiment evidence prevents Qwen from overriding a high-confidence sentiment classification.
- Existing engineer/internal workload guardrails remain active while genuine internal critical operational events can still generate notifications.

## Expected result
- Outlook/live dashboard remains responsive while Qwen runs asynchronously.
- Qwen3 8B should produce shorter, faster structured responses.
- The AI page should no longer show a growing historical error count caused by repeated retries of the same email.
- Sentiment should be more stable for courtesy, technical-fact, positive-recognition and explicit dissatisfaction messages.
