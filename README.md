# Agentic RAG Chatbot

A document-grounded retrieval-augmented generation (RAG) application with a
Flask web interface, a terminal client, local document ingestion, conversation
memory, and Chroma or FAISS vector storage.

The default flow retrieves document chunks before calling the configured chat
model. Prompting and the no-context short circuit reduce unsupported answers,
but model output should still be evaluated before the application is used for
high-stakes decisions.

## Architecture

```text
PDF / TXT / CSV / Markdown
          |
          v
load -> normalize -> chunk -> embed -> Chroma or FAISS
                                         |
Browser / CLI -> conversation registry -> retrieve -> chat model -> cited answer
```

The main boundaries are:

| Area | Modules | Responsibility |
| --- | --- | --- |
| Configuration | `config.py` | Load and validate environment settings |
| Ingestion | `ingest.py`, `src/ingestion/`, `src/services/` | Load, normalize, deduplicate, chunk, and store documents |
| Embeddings | `src/embeddings/` | Create Hugging Face or OpenAI embeddings independently of the chat provider |
| Storage | `src/vectorstore/` | Provide a common Chroma/FAISS interface |
| Retrieval and chat | `src/retrieval/`, `src/agent/`, `src/memory/` | Retrieve context, call the model, and maintain bounded conversation history |
| Web application | `web/app.py`, `web/api.py`, `web/services.py` | Application factory, JSON routes, lazy services, uploads, and conversation isolation |
| Frontend | `web/templates/`, `web/static/js/` | Modular browser UI and API client |
| Production entry point | `web/wsgi.py` | Expose the Flask app to a WSGI server |

The web application is created with an application factory. Expensive
embeddings and vector-store resources are initialized lazily, while route tests
inject in-memory fakes.

## Requirements

- Python 3.10 or newer
- Enough disk space for the selected embedding/chat models and vector database
- A Groq or OpenAI API key for those chat providers
- Network access on the first Hugging Face model load, unless the model is
  already cached

## Setup

Create and activate a virtual environment:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS or Linux:

```bash
source .venv/bin/activate
```

Install runtime dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`, then set the provider and credentials:

```bash
cp .env.example .env
```

PowerShell equivalent:

```powershell
Copy-Item .env.example .env
```

Configuration is validated at startup. Process environment variables take
precedence over `.env`, which makes the same configuration suitable for local
development and managed deployments.

### Model configuration

Chat and embedding providers are independent:

```env
LLM_PROVIDER=groq
GROQ_API_KEY=replace_me

EMBEDDING_PROVIDER=huggingface
HF_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

For OpenAI chat and embeddings:

```env
LLM_PROVIDER=openai
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=replace_me
```

For a fully local chat model:

```env
LLM_PROVIDER=huggingface
EMBEDDING_PROVIDER=huggingface
```

The local path downloads Hugging Face models and can be slow on CPU. See
`.env.example` for every supported setting, including storage paths, model
names, upload limits, chunking, and the minimum retrieval relevance threshold.

### Optional Google Drive ingestion

Install the optional client libraries:

```bash
python -m pip install "google-api-python-client>=2.0,<3.0" "google-auth>=2.0,<3.0"
```

Set `GOOGLE_SERVICE_ACCOUNT_KEY` to the service-account JSON path, share the
Drive folder with that service account, and use `--gdrive` as shown below.

## Ingest documents

The repository includes 12 sample documents under `data/documents/`.

```bash
# Ingest the sample directory
python ingest.py

# Ingest one file or another directory
python ingest.py --source path/to/document.pdf
python ingest.py --source path/to/documents/

# Clear the configured vector store, then ingest
python ingest.py --reset

# Optional Google Drive folder
python ingest.py --gdrive FOLDER_ID
```

Changing the embedding provider or embedding model requires rebuilding the
vector index so document and query vectors remain compatible.

## Run the application

### Development

```bash
python web/app.py
```

Open <http://127.0.0.1:5000>. This uses Flask's development server and should
not be exposed to the internet.

The terminal client is also available:

```bash
python cli/main.py
```

CLI commands include `sources`, `clear`, and `quit`.

### Production WSGI

Waitress is included in the runtime requirements:

```bash
waitress-serve --listen=0.0.0.0:8000 web.wsgi:app
```

Put TLS, authentication, request-size enforcement, and trusted proxy handling
at the deployment edge. The built-in conversation registry, local vector
stores, and rate-limit counters are process-local; use one application process
for this local deployment shape, or externalize those concerns before scaling
to multiple processes or hosts.

The current API has no user authentication. Do not expose document upload,
delete, or reset operations to untrusted users without adding access control.

## HTTP API

The browser client calls these routes:

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Lightweight process liveness check |
| `GET` | `/api/status` | Provider, model, document, and chunk status |
| `POST` | `/api/chat` | Ask a question for a `conversation_id` |
| `POST` | `/api/ingest` | Upload one or more supported documents |
| `GET` | `/api/documents` | List indexed documents |
| `GET` | `/api/documents/content?source=...` | Preview indexed content |
| `DELETE` | `/api/documents?source=...` | Delete one indexed document |
| `POST` | `/api/clear-memory` | Clear one conversation |
| `POST` | `/api/reset` | Clear the configured knowledge base |

JSON chat example:

```bash
curl -X POST http://127.0.0.1:5000/api/chat \
  -H "Content-Type: application/json" \
  -H "X-RAG-Client: web" \
  -d '{"question":"What is retrieval-augmented generation?","conversation_id":"demo"}'
```

Mutating routes require `X-RAG-Client: web`. This custom header helps reject
unintended browser form submissions; it is not authentication or authorization.
Conversation IDs may contain letters, numbers, `_`, and `-`, up to 128
characters.

`/api/health` only confirms that the web process can answer a request. Use
`/api/status` plus a deployment-specific model/database smoke test when deeper
readiness is required.

## Testing

Install development dependencies:

```bash
python -m pip install -r requirements-dev.txt
```

Run the default hermetic suite:

```bash
python -m pytest
```

The unit and API tests use mocks or in-memory fakes and must not call a model,
download embeddings, or modify the persisted vector database. Coverage:

```bash
python -m pytest tests/test_pipeline.py tests/test_backend_hardening.py tests/test_web_api.py \
  --cov=config --cov=src --cov=web --cov-report=term-missing
```

Live end-to-end tests are marked `e2e` and skipped by default. They require a
configured provider and a pre-ingested database:

PowerShell:

```powershell
$env:RUN_E2E = "1"
python -m pytest tests/test_e2e.py -m e2e -v
```

macOS or Linux:

```bash
RUN_E2E=1 python -m pytest tests/test_e2e.py -m e2e -v
```

CI compiles the Python sources and runs only the hermetic unit/API suite with
Hugging Face and Transformers offline flags enabled.

## Storage and operational limits

- Chroma and FAISS are local stores, suitable for a single application
  deployment rather than horizontally scaled multi-tenancy.
- FAISS deserialization is disabled by default because loading an untrusted
  serialized index is unsafe.
- Conversation memory is bounded and held in process; it is not durable across
  restarts.
- Browser chat history is local to the browser profile.
- Retrieval quality depends on chunking, the embedding model, corpus quality,
  `TOP_K`, and `RETRIEVAL_MIN_RELEVANCE`. Queries below that score are refused
  before document context is sent to the chat model.
- Google Drive support requires separate credentials and optional dependencies.

For larger deployments, move documents and conversations to shared services,
use a distributed rate limiter, add authentication and authorization, and add
continuous retrieval/answer-quality evaluation.
