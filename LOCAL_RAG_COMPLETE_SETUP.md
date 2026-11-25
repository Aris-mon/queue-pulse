# PDL Local RAG + LangGraph — Complete setup and training sample import

## Runtime prerequisites

- Windows Server / Windows workstation
- Python **3.11** available through the `py` launcher
- Ollama installed locally
- Ollama model `qwen3:8b`
- Python dependencies from `requirements.txt`
- Local embedding model `sentence-transformers/all-MiniLM-L6-v2` staged at `models/all-MiniLM-L6-v2`

The RAG runtime is local-only by default. `AI_LOCAL_ONLY=true` blocks remote Ollama endpoints and the embedding model is loaded with `local_files_only=True`.

## Required Python packages

The included `requirements.txt` installs the complete application and RAG stack. RAG-specific dependencies are:

- `langgraph>=1.0,<2.0`
- `chromadb>=1.0,<2.0`
- `sentence-transformers>=5.0,<6.0`

`sentence-transformers` installs its PyTorch/Transformers dependencies transitively.

## One-time setup

From the project root in PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup_local_rag.ps1 -DownloadEmbeddingModel -PullQwenModel
```

Use `-DownloadEmbeddingModel` only on a machine/network approved to download the public embedding model. On an offline production VM, stage the model on an approved connected machine and copy the complete `models\all-MiniLM-L6-v2` folder to the same location in the production project.

Then start normally:

```powershell
.\run.ps1
```

## Verify RAG

From the project root:

```powershell
.\.venv\Scripts\python.exe tools\check_local_rag.py
```

In the application, open **Admin → AI Intelligence Engine**. Local RAG should show **READY**, and the indexed count should match the active validated example count after synchronization.

The admin API also supports a deeper runtime validation:

`/api/v1/ai/rag/status?validate=1`

## Where to upload training email samples

Sign in as a **System Administrator** and open:

**Customer AI Evaluation → Train AI → Bulk upload validated email samples**

Click **Download CSV Template**, populate your approved samples, then use **Upload & Index Samples**.

The import accepts `.csv` or `.json`, maximum 5 MB and 500 samples per upload. Imported samples are stored in `ai_uploaded_training_samples`, not in the operational `emails` table, so they do not affect Live Emails, TAT, reports, alerts, or mailbox counts.

### CSV fields

Required/validated fields:

- `message`
- `intent`
- `sentiment`
- `priority`
- `response_required`
- `recognition`
- `escalation`

Supported optional/context fields:

- `subject`
- `sender_type` (`customer`, `engineer`, `internal`, `unknown`)
- `category` (validated against intent; recommended to include)
- `notes`
- `base_model` (defaults to `qwen3:8b`)
- `pdl_name`

Boolean fields accept `true/false`, `yes/no`, or `1/0`.

### Allowed intent → category mapping

- `RECOGNITION` → `recognition`
- `SCHEDULE_REQUEST`, `SCHEDULE_CONFIRMATION`, `SCHEDULE_CHANGE`, `SCHEDULE_CANCELLATION` → `schedule_request`
- `STATUS_REQUEST` → `update_request`
- `FOLLOW_UP` → `follow_up`
- `TECHNICAL_SUPPORT` → `technical_support`
- `INFORMATION_REQUEST`, `INFORMATION_ONLY` → `information_request`
- `ESCALATION` → `escalation`
- `GENERAL` → `general`

### JSON format

Either upload a JSON array:

```json
[
  {
    "subject": "Status update requested",
    "message": "Please share the latest status and ETA.",
    "sender_type": "customer",
    "intent": "STATUS_REQUEST",
    "sentiment": "neutral",
    "priority": "normal",
    "category": "update_request",
    "response_required": true,
    "recognition": false,
    "escalation": false
  }
]
```

or an object containing a `samples` array.

## Existing synchronized-email training workflow

The original workflow remains supported:

**Customer AI Evaluation → Train AI → Email from Escalations / Notifications**

Select a synchronized customer email, correct the labels, and save it. The example is immediately persisted and indexed. You may optionally rebuild the customized Ollama model.

## RAG synchronization behavior in this release

The collection is no longer synchronized only by row count. It fingerprints the email text and labels so it detects:

- new examples
- edited examples
- label-only edits
- uploaded examples
- deactivated/deleted examples
- stale legacy Chroma IDs

Retrieval failures remain fail-safe but are visible through RAG status diagnostics. LangGraph and the linear fallback both attach the same RAG/training prompt context to Qwen.
