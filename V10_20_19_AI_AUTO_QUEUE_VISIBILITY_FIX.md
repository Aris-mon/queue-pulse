# V10.20.19 — AI Auto-Queue Visibility & Reliability Fix

## Root cause found in V10.20.18

The automatic queue could be working while the UI appeared to show no AI activity.
`analyze_email_id()` completed the deterministic/hybrid pass immediately, then spent
minutes inside Ollama/Qwen3. Because the same `AIAnalysis` row was already marked
`COMPLETE`, Admin/Live telemetry could report zero processing/model analyses until
Ollama finally returned. Manual **Analyze with AI** therefore appeared to "fix" the
email even though the underlying problem was queue/processing visibility.

## V10.20.19 changes

- Keep an AI analysis row in `PROCESSING` while Ollama/Qwen3 is actually running.
- Retain the deterministic result in `result_json` and project it to the Email row
  immediately, so the dashboard remains usable during slow local inference.
- Complete the row only after model enrichment finishes, or after a deterministic
  fallback is recorded because the model failed.
- Stop publishing priority/TAT notifications twice: the interim deterministic pass
  no longer emits alerts; the final result emits them once.
- Automatic dispatcher/queue refill errors are now written to Events & Logs instead
  of being silently swallowed.
- Manual **Analyze with AI** continues to use the exact same worker path.
- CPU-only Qwen3 remains serialized intentionally; this release does not pretend to
  make 8B inference faster.

## Expected behavior

1. Customer email sync completes.
2. Dispatcher finds eligible customer email automatically.
3. The worker enters `PROCESSING` and the UI shows active AI processing.
4. Qwen3 analyzes one email at a time.
5. On completion, the result becomes `COMPLETE` and the next queued email is started.
6. If Ollama fails, the record becomes a completed deterministic fallback with the
   error retained for diagnostics; the queue continues.

## Important for the current workstation

Your measured Qwen3:8b CPU request took about 420 seconds (~7 minutes). With a single
worker, 43 emails can therefore take several hours for full LLM enrichment. The new
release makes that activity visible and keeps the queue moving; it does not change
the hardware inference limit.
