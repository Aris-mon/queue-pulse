# V10.20.27 — RAG reliability and sample import

- Fixed RAG context being omitted by the linear LangGraph fallback.
- Added RAG runtime diagnostics instead of silently hiding retrieval/index errors.
- Replaced count-only Chroma synchronization with fingerprint-based synchronization.
- Removes stale/deactivated Chroma entries and detects label-only/text edits.
- Added direct CSV/JSON human-validated training sample upload.
- Uploaded samples are isolated from operational Email rows and metrics.
- Added duplicate detection for uploaded samples.
- Added `RECOGNITION` to the human training intent contract.
- Fixed the Customer AI Evaluation JavaScript that lowercased intent values.
- Added explicit telemetry distinguishing semantic RAG from legacy prompt fallback.
- Added setup/preflight scripts and a downloadable CSV template.
