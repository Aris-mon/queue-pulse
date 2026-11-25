# PDL AI Intelligence Engine

This revision adds a provider-agnostic hybrid intelligence layer while preserving the existing Outlook collector and deterministic intelligence.

## Runtime flow
Outlook collector -> database -> background AI queue -> rule pre-classification -> optional local Ollama LLM -> JSON validation -> confidence/human review -> AI audit record -> attention alert.

The Flask request path does not wait for the LLM. If Ollama/local inference is unavailable, the rule result is stored with `fallback_used=true` and email monitoring continues.

## Local AI setup
1. Install and run an Ollama-compatible server on the same server or an approved internal host.
2. Pull/configure an instruction model suitable for your environment.
3. Configure Administration > AI Configuration, or `.env` using `AI_PROVIDER`, `AI_MODEL`, `AI_BASE_URL`, `AI_TIMEOUT`, `AI_TEMPERATURE`, and `AI_CONFIDENCE_THRESHOLD`.
4. Use `AI_PROVIDER=rules` for deterministic-only operation.

No API key is hard-coded. Email content is handled as untrusted input and prompt-injection-like text is recorded in the AI analysis audit record.

## New data
- `ai_analysis`: structured AI result, confidence, human-review state, fallback status, prompt-injection signal and processing time.
- `ai_feedback`: user correctness/correction feedback for controlled evaluation.
- `ai_model_logs`: provider/model/prompt version, latency, confidence, fallback and error audit.

## New endpoints
- `POST /email/<id>/ai/analyze`
- `POST /email/<id>/ai/feedback`
- `GET /api/v1/ai/health`
- `GET /api/v1/ai/attention`

## Safety
The AI layer never sends, deletes or modifies mailbox content. Suggested/recommended actions are advisory. Outlook collection remains read-only.
