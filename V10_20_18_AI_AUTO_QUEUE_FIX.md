# V10.20.18 — AI Auto Queue Reliability Fix

## Fixed
- Customer emails imported during Outlook sync are now eligible for automatic AI enrichment even when a deterministic/hybrid AI analysis record already exists.
- Added a lightweight background AI dispatcher that checks for eligible customer emails every 5 seconds.
- Kept the Ollama/Qwen3 executor serialized at one worker to avoid CPU contention.
- Automatic queueing no longer depends solely on the sync request successfully seeding the worker.
- Manual **Analyze with AI** remains available and uses the same worker path.
- Existing retry logic for stale prompt versions and failed/fallback analyses remains intact.

## Expected behavior
1. Outlook sync imports customer emails.
2. Rules/hybrid classification can complete immediately.
3. The background dispatcher places eligible emails into the Ollama/Qwen3 queue automatically.
4. Dashboard AI queue/processing telemetry updates while the model works.
5. When one analysis finishes, the worker refills the queue automatically.

## Important
Qwen3:8b running CPU-only can take several minutes per email on a workstation. This release fixes the **queue not being automatically populated** problem; it does not make CPU inference itself faster.
