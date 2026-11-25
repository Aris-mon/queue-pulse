# Local RAG + LangGraph setup

## Current release
V10.20.26

## What is included
- Outlook scan hardening without changing the mailbox discovery architecture.
- Ollama JSON Schema enforcement.
- Local ChromaDB persistent vector store.
- Local `sentence-transformers/all-MiniLM-L6-v2` embeddings.
- LangGraph model-call/validation/retry workflow.
- Strict `AI_LOCAL_ONLY=true` endpoint enforcement.

## Do you need to download anything now?
Not to keep the existing application running. The new RAG path is designed to fail safe to the existing training-prompt/deterministic path when dependencies or the local embedding model are not staged.

To actually enable RAG, the VM needs these Python packages:
- `langgraph`
- `chromadb`
- `sentence-transformers`

The embedding model must also exist locally at `models/all-MiniLM-L6-v2` (or the configured `AI_EMBEDDING_MODEL_PATH`). Sentence Transformers supports loading a local model with `local_files_only=True`; this release uses that mode so runtime inference does not download a model.

The one-time model staging can be done with `download_embedding_model.ps1` on an approved network, then the model directory can be copied to the production VM if the VM has no internet access.

## Security
- Keep `AI_LOCAL_ONLY=true`.
- Keep `AI_BASE_URL=http://127.0.0.1:11434`.
- Do not configure LangSmith tracing/API keys.
- Do not configure Graph mailbox synchronization unless your security architecture explicitly permits Microsoft Graph.
- Use Windows firewall/egress controls to deny unnecessary internet access from the production VM.

## Existing human examples
New examples added from Customer AI Evaluation are indexed automatically after the database commit. Existing approved examples can be indexed from Admin using `Reindex approved RAG examples`.

## V10.20.27 note
For the hardened setup, dependency preflight, fingerprint-based synchronization, and CSV/JSON training sample import, use `LOCAL_RAG_COMPLETE_SETUP.md` and `setup_local_rag.ps1`.
