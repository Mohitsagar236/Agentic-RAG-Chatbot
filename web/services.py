"""Application services used by the Flask API.

This module keeps document ingestion, vector-store access, and conversation
lifecycles outside HTTP route handlers.  The container is intentionally small
so tests can inject a fake service object without loading embedding models.
"""

from __future__ import annotations

import logging
import re
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

import config
from src.utils.helpers import file_hash

logger = logging.getLogger(__name__)

_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass
class FileOutcome:
    name: str
    status: str
    chunks: int = 0
    error: str | None = None


@dataclass
class IngestionBatch:
    outcomes: list[FileOutcome] = field(default_factory=list)
    total_chunks: int = 0

    @property
    def ingested(self) -> list[FileOutcome]:
        return [item for item in self.outcomes if item.status == "ingested"]

    @property
    def skipped(self) -> list[FileOutcome]:
        return [item for item in self.outcomes if item.status == "skipped"]

    @property
    def errors(self) -> list[FileOutcome]:
        return [item for item in self.outcomes if item.status == "error"]


@dataclass
class _ConversationEntry:
    agent: Any
    lock: threading.RLock
    last_used: float


class ConversationRegistry:
    """Bounded, thread-safe collection of conversation-scoped RAG agents."""

    def __init__(
        self,
        agent_factory: Callable[[], Any],
        *,
        max_conversations: int = 100,
        ttl_seconds: int = 6 * 60 * 60,
    ):
        self._agent_factory = agent_factory
        self._max_conversations = max(1, max_conversations)
        self._ttl_seconds = max(60, ttl_seconds)
        self._entries: OrderedDict[str, _ConversationEntry] = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def validate_id(conversation_id: str) -> str:
        candidate = (conversation_id or "").strip()
        if not _CONVERSATION_ID.fullmatch(candidate):
            raise ValueError(
                "conversation_id must contain only letters, numbers, '_' or '-' "
                "and be at most 128 characters"
            )
        return candidate

    def _prune(self, now: float) -> None:
        stale = [
            key
            for key, entry in self._entries.items()
            if now - entry.last_used > self._ttl_seconds
        ]
        for key in stale:
            self._entries.pop(key, None)
        while len(self._entries) >= self._max_conversations:
            self._entries.popitem(last=False)

    def _entry(self, conversation_id: str) -> _ConversationEntry:
        conversation_id = self.validate_id(conversation_id)
        now = time.monotonic()
        with self._lock:
            existing = self._entries.get(conversation_id)
            if existing is not None:
                existing.last_used = now
                self._entries.move_to_end(conversation_id)
                return existing
            self._prune(now)
            entry = _ConversationEntry(
                agent=self._agent_factory(),
                lock=threading.RLock(),
                last_used=now,
            )
            self._entries[conversation_id] = entry
            return entry

    def chat(self, conversation_id: str, question: str) -> dict:
        entry = self._entry(conversation_id)
        with entry.lock:
            return entry.agent.chat(question)

    def clear(self, conversation_id: str) -> bool:
        conversation_id = self.validate_id(conversation_id)
        with self._lock:
            entry = self._entries.pop(conversation_id, None)
        if entry is None:
            return False
        with entry.lock:
            entry.agent.clear_memory()
        return True

    def clear_all(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            with entry.lock:
                entry.agent.clear_memory()


class ApplicationServices:
    """Lazy application container for embeddings, storage, and chat agents."""

    def __init__(
        self,
        *,
        embeddings: Any = None,
        database: Any = None,
        agent_factory: Callable[[Any], Any] | None = None,
    ):
        self._embeddings = embeddings
        self._database = database
        self._agent_factory = agent_factory
        self._registry: ConversationRegistry | None = None
        self._init_lock = threading.RLock()
        self._mutation_lock = threading.RLock()

    @property
    def database(self):
        if self._database is None:
            with self._init_lock:
                if self._database is None:
                    from src.embeddings.embedding_generator import get_embeddings
                    from src.vectorstore.vector_db import VectorDatabase

                    self._embeddings = self._embeddings or get_embeddings()
                    self._database = VectorDatabase(self._embeddings)
        return self._database

    @property
    def conversations(self) -> ConversationRegistry:
        if self._registry is None:
            with self._init_lock:
                if self._registry is None:
                    if self._agent_factory is None:
                        from src.agent.rag_agent import RAGAgent

                        factory = lambda: RAGAgent(self.database)
                    else:
                        factory = lambda: self._agent_factory(self.database)
                    self._registry = ConversationRegistry(factory)
        return self._registry

    def status(self) -> dict:
        provider = config.LLM_PROVIDER.lower()
        if provider == "groq":
            model = config.GROQ_MODEL
        elif provider == "openai":
            model = config.OPENAI_MODEL
        else:
            model = "TinyLlama-1.1B-Chat"
        return {
            "status": "ready",
            "chunk_count": self.database.count(),
            "sources": [Path(source).name for source in self.database.list_sources()],
            "model": model,
            "provider": provider,
        }

    def chat(self, conversation_id: str, question: str) -> dict:
        return self.conversations.chat(conversation_id, question)

    def clear_memory(self, conversation_id: str) -> bool:
        return self.conversations.clear(conversation_id)

    def ingest_files(self, files: Iterable[FileStorage]) -> IngestionBatch:
        outcomes: list[FileOutcome] = []
        with self._mutation_lock:
            known_hashes = set(self.database.list_content_hashes())
            for upload in files:
                outcome = self._ingest_file(upload, known_hashes)
                outcomes.append(outcome)
            total = self.database.count()
        return IngestionBatch(outcomes=outcomes, total_chunks=total)

    def _ingest_file(
        self,
        upload: FileStorage,
        known_hashes: set[str],
    ) -> FileOutcome:
        original_name = upload.filename or ""
        display_name = secure_filename(Path(original_name).name)
        if not display_name:
            return FileOutcome(
                name=original_name or "(unnamed)",
                status="error",
                error="The file has no valid filename.",
            )

        suffix = Path(display_name).suffix.lower()
        if suffix not in config.SUPPORTED_EXTENSIONS:
            return FileOutcome(
                name=display_name,
                status="error",
                error=f"Unsupported file type '{suffix or 'none'}'.",
            )

        upload.stream.seek(0, 2)
        size = upload.stream.tell()
        upload.stream.seek(0)
        max_bytes = config.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if size <= 0:
            return FileOutcome(
                name=display_name,
                status="error",
                error="The file is empty.",
            )
        if size > max_bytes:
            return FileOutcome(
                name=display_name,
                status="error",
                error=f"File exceeds the {config.MAX_UPLOAD_SIZE_MB} MB limit.",
            )

        try:
            # Keep model-adjacent LangChain imports off the web startup path.
            # They are only needed when a user actually ingests a document.
            from src.ingestion.chunker import chunk_documents
            from src.ingestion.document_loader import load_document
            from src.ingestion.preprocessor import preprocess_documents

            with tempfile.TemporaryDirectory(prefix="rag-upload-") as temp_dir:
                local_path = Path(temp_dir) / f"{uuid.uuid4().hex}{suffix}"
                upload.save(local_path)
                content_hash = file_hash(local_path)
                if content_hash in known_hashes:
                    return FileOutcome(name=display_name, status="skipped")

                raw = load_document(local_path)
                cleaned = preprocess_documents(raw)
                chunks = chunk_documents(
                    cleaned,
                    chunk_size=config.CHUNK_SIZE,
                    chunk_overlap=config.CHUNK_OVERLAP,
                )
                if not chunks:
                    return FileOutcome(
                        name=display_name,
                        status="error",
                        error="No usable text could be extracted.",
                    )

                document_id = uuid.uuid4().hex
                source = f"upload://{document_id}/{display_name}"
                for index, chunk in enumerate(chunks):
                    chunk.metadata.update(
                        {
                            "source": source,
                            "display_name": display_name,
                            "document_id": document_id,
                            "content_hash": content_hash,
                            "chunk_index": index,
                        }
                    )

                before = self.database.count()
                self.database.add_documents(chunks)
                added = self.database.count() - before
                if added <= 0:
                    return FileOutcome(
                        name=display_name,
                        status="error",
                        error="The document could not be added to the knowledge base.",
                    )
                known_hashes.add(content_hash)
                return FileOutcome(
                    name=display_name,
                    status="ingested",
                    chunks=added,
                )
        except Exception:
            logger.exception("Failed to ingest uploaded file %s", display_name)
            return FileOutcome(
                name=display_name,
                status="error",
                error="The file could not be processed.",
            )

    def list_documents(self) -> list[dict]:
        records = []
        for item in self.database.get_document_stats():
            source = item["source"]
            name = item.get("display_name") or Path(source).name
            records.append(
                {
                    "name": name,
                    "source": source,
                    "id": item.get("document_id") or source,
                    "type": Path(name).suffix.lstrip(".").lower() or "unknown",
                    "chunks": item["chunks"],
                }
            )
        return records

    def document_content(self, source: str, *, limit: int = 15_000) -> dict | None:
        chunks = self.database.get_chunks_for_source(source)
        if not chunks:
            return None

        chunks.sort(
            key=lambda item: (
                item.metadata.get("page", 0),
                item.metadata.get("row", 0),
                item.metadata.get("chunk_index", 0),
            )
        )
        total_chars = sum(len(chunk.page_content) for chunk in chunks)
        total_chars += max(0, len(chunks) - 1) * 2
        parts: list[str] = []
        remaining = limit
        for chunk in chunks:
            if remaining <= 0:
                break
            separator = "\n\n" if parts else ""
            value = separator + chunk.page_content
            parts.append(value[:remaining])
            remaining -= len(value)

        name = chunks[0].metadata.get("display_name") or Path(source).name
        return {
            "source": source,
            "name": name,
            "type": Path(name).suffix.lstrip(".").lower() or "unknown",
            "content": "".join(parts),
            "truncated": total_chars > limit,
            "total_chars": total_chars,
            "chunks": len(chunks),
        }

    def delete_document(self, source: str) -> int:
        with self._mutation_lock:
            removed = self.database.delete_source(source)
        return removed

    def reset(self) -> None:
        with self._mutation_lock:
            self.database.reset()
            if self._registry is not None:
                self._registry.clear_all()
