"""Validated application configuration.

Environment variables take precedence over the local ``.env`` file. Chat-model,
embedding-model, and vector-store selection are intentionally independent.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)


def _choice(name: str, default: str, allowed: set[str]) -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {choices}; got {value!r}")
    return value


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean, got {raw!r}")


def _path(name: str, default: Path) -> str:
    value = Path(os.getenv(name, str(default))).expanduser()
    if not value.is_absolute():
        value = BASE_DIR / value
    return str(value.resolve())


# Chat model
LLM_PROVIDER = _choice("LLM_PROVIDER", "groq", {"groq", "openai", "huggingface"})
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
HF_LLM_MODEL = os.getenv("HF_LLM_MODEL", "TinyLlama/TinyLlama-1.1B-Chat-v1.0")

# Embeddings (independent of LLM_PROVIDER)
EMBEDDING_PROVIDER = _choice(
    "EMBEDDING_PROVIDER",
    "huggingface",
    {"huggingface", "openai"},
)
OPENAI_EMBEDDING_MODEL = os.getenv(
    "OPENAI_EMBEDDING_MODEL",
    "text-embedding-3-small",
)
HF_EMBEDDING_MODEL = os.getenv(
    "HF_EMBEDDING_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2",
)
HF_EMBEDDING_DEVICE = os.getenv("HF_EMBEDDING_DEVICE", "cpu")

# Generation
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "1024"))

# Vector store (independent of model providers)
VECTOR_DB = _choice("VECTOR_DB", "chroma", {"chroma", "faiss"})
CHROMA_PERSIST_DIR = _path("CHROMA_PERSIST_DIR", BASE_DIR / "chroma_db")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "rag_documents").strip()
FAISS_INDEX_PATH = _path("FAISS_INDEX_PATH", BASE_DIR / "faiss_index")
FAISS_ALLOW_DANGEROUS_DESERIALIZATION = _bool(
    "FAISS_ALLOW_DANGEROUS_DESERIALIZATION",
    False,
)

# Chunking and retrieval
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))
TOP_K = int(os.getenv("TOP_K", "5"))
# Set below zero to disable relevance gating. Scores use a 0..1 higher-is-better scale.
RETRIEVAL_MIN_RELEVANCE = float(
    os.getenv("RETRIEVAL_MIN_RELEVANCE", "0.20")
)

# UI / display
SNIPPET_MAX_CHARS = int(os.getenv("SNIPPET_MAX_CHARS", "200"))
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "20"))
MAX_UPLOAD_FILES = int(os.getenv("MAX_UPLOAD_FILES", "10"))

# Paths and formats
DOCUMENTS_DIR = _path("DOCUMENTS_DIR", BASE_DIR / "data" / "documents")
SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".csv", ".md"}


if not (0 < CHUNK_SIZE <= 10_000):
    raise ValueError(f"CHUNK_SIZE must be between 1 and 10000, got {CHUNK_SIZE}")
if not (0 <= CHUNK_OVERLAP < CHUNK_SIZE):
    raise ValueError(
        "CHUNK_OVERLAP must be >= 0 and < "
        f"CHUNK_SIZE ({CHUNK_SIZE}), got {CHUNK_OVERLAP}"
    )
if not (1 <= TOP_K <= 50):
    raise ValueError(f"TOP_K must be between 1 and 50, got {TOP_K}")
if not (-1.0 <= RETRIEVAL_MIN_RELEVANCE <= 1.0):
    raise ValueError(
        "RETRIEVAL_MIN_RELEVANCE must be between -1.0 (disabled) and 1.0, "
        f"got {RETRIEVAL_MIN_RELEVANCE}"
    )
if not (0.0 <= LLM_TEMPERATURE <= 2.0):
    raise ValueError(
        f"LLM_TEMPERATURE must be between 0.0 and 2.0, got {LLM_TEMPERATURE}"
    )
if not (1 <= MAX_TOKENS <= 32_768):
    raise ValueError(f"MAX_TOKENS must be between 1 and 32768, got {MAX_TOKENS}")
if not COLLECTION_NAME:
    raise ValueError("COLLECTION_NAME must not be empty")
if MAX_UPLOAD_SIZE_MB <= 0:
    raise ValueError(
        f"MAX_UPLOAD_SIZE_MB must be positive, got {MAX_UPLOAD_SIZE_MB}"
    )
if not (1 <= MAX_UPLOAD_FILES <= 100):
    raise ValueError(
        f"MAX_UPLOAD_FILES must be between 1 and 100, got {MAX_UPLOAD_FILES}"
    )
