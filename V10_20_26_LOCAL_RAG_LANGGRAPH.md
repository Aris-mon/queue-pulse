# V10.20.26 — Local RAG + LangGraph + Sync Hardening

## Changes
- Outlook folder scan no longer stops early when Outlook sorting fails.
- Candidate collection avoids unnecessary mailbox-wide in-memory accumulation.
- Ollama receives the actual JSON Schema instead of generic `format: json`.
- Local RAG uses only approved human training examples in persistent ChromaDB.
- Sentence Transformers is loaded with `local_files_only=True`; runtime does not download an embedding model.
- LangGraph orchestrates model call → validation → bounded retry.
- Deterministic Outlook/PDL/TAT/response rules remain authoritative.
- No LangSmith integration is enabled. Do not set LangSmith tracing/API variables for production.

## One-time operator setup
1. Install Python dependencies from `requirements.txt`.
2. Stage `all-MiniLM-L6-v2` into `models/all-MiniLM-L6-v2` using an approved offline/controlled download process.
3. Keep `AI_LOCAL_ONLY=true`.
4. Start the application and use Customer AI Evaluation to add human-approved examples.
5. Admin can reindex approved examples with `POST /api/v1/ai/rag/reindex`.

## Security
- Chroma is persistent local storage under `instance/chroma`.
- Embeddings are generated locally on CPU.
- Ollama defaults to `127.0.0.1:11434`.
- The application does not configure LangSmith tracing. LangGraph itself does not require a cloud endpoint for this in-process workflow.
- Network firewall policy should still deny unnecessary outbound internet traffic from the production VM.
