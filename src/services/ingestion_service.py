"""Reusable, deterministic document-ingestion orchestration."""

from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
from typing import Iterable, List

from langchain_core.documents import Document

import config
from src.ingestion.chunker import chunk_documents
from src.ingestion.document_loader import load_directory, load_document
from src.ingestion.preprocessor import preprocess_documents
from src.vectorstore.vector_db import VectorDatabase


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionResult:
    loaded_segments: int
    chunks_prepared: int
    chunks_added: int
    skipped_sources: tuple[str, ...]
    replaced_sources: tuple[str, ...]
    total_chunks: int


class IngestionService:
    """Load, normalize, chunk, deduplicate, and persist documents."""

    def __init__(
        self,
        db: VectorDatabase,
        chunk_size: int = config.CHUNK_SIZE,
        chunk_overlap: int = config.CHUNK_OVERLAP,
    ):
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if not 0 <= chunk_overlap < chunk_size:
            raise ValueError("chunk_overlap must be >= 0 and less than chunk_size")
        self._db = db
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def ingest_path(self, source: str | Path, reset: bool = False) -> IngestionResult:
        path = Path(source).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Source not found: {path}")
        if path.is_file():
            documents = load_document(path)
        elif path.is_dir():
            documents = load_directory(path)
        else:
            raise ValueError(f"Source is not a regular file or directory: {path}")
        return self.ingest_documents(documents, reset=reset)

    def ingest_google_drive(
        self,
        folder_id: str,
        reset: bool = False,
        recursive: bool = True,
    ) -> IngestionResult:
        from src.ingestion.gdrive_loader import load_from_google_drive

        documents = load_from_google_drive(folder_id, recursive=recursive)
        return self.ingest_documents(documents, reset=reset)

    def ingest_documents(
        self,
        documents: Iterable[Document],
        reset: bool = False,
    ) -> IngestionResult:
        raw_documents = [
            Document(
                page_content=document.page_content,
                metadata=dict(document.metadata),
            )
            for document in documents
        ]
        prepared = self._prepare_by_source(raw_documents)
        chunks_prepared = sum(len(chunks) for _, _, chunks in prepared)

        # Never destroy a usable index until the full input validates and chunks.
        if not prepared or chunks_prepared == 0:
            logger.warning("No usable documents were prepared; vector store unchanged.")
            return IngestionResult(
                loaded_segments=len(raw_documents),
                chunks_prepared=0,
                chunks_added=0,
                skipped_sources=(),
                replaced_sources=(),
                total_chunks=self._db.count(),
            )

        if reset:
            self._db.reset()

        existing_hashes = set() if reset else self._db.list_content_hashes()
        existing_sources = set() if reset else set(self._db.list_sources())
        seen_hashes = set(existing_hashes)
        selected_chunks: List[Document] = []
        skipped_sources: List[str] = []
        replaced_sources: List[str] = []

        for source, content_hash, chunks in prepared:
            if content_hash in seen_hashes:
                skipped_sources.append(source)
                continue

            if source in existing_sources:
                try:
                    self._db.delete_source(source)
                except NotImplementedError as exc:
                    raise RuntimeError(
                        f"Source {source!r} has changed, but the configured vector "
                        "store cannot replace individual sources. Re-run ingestion "
                        "with reset=True."
                    ) from exc
                replaced_sources.append(source)

            selected_chunks.extend(chunks)
            seen_hashes.add(content_hash)

        chunks_added = self._db.add_documents(selected_chunks)
        result = IngestionResult(
            loaded_segments=len(raw_documents),
            chunks_prepared=chunks_prepared,
            chunks_added=chunks_added,
            skipped_sources=tuple(sorted(skipped_sources)),
            replaced_sources=tuple(sorted(replaced_sources)),
            total_chunks=self._db.count(),
        )
        logger.info(
            "Ingestion finished: %d added, %d total, %d source(s) skipped.",
            result.chunks_added,
            result.total_chunks,
            len(result.skipped_sources),
        )
        return result

    def _prepare_by_source(
        self,
        documents: List[Document],
    ) -> list[tuple[str, str, List[Document]]]:
        grouped: dict[str, List[Document]] = {}
        for document in documents:
            source = str(document.metadata.get("source") or "unknown")
            document.metadata["source"] = source
            grouped.setdefault(source, []).append(document)

        prepared: list[tuple[str, str, List[Document]]] = []
        for source in sorted(grouped):
            source_documents = grouped[source]
            content_hash = self._content_hash(source_documents)
            for document in source_documents:
                document.metadata["content_hash"] = content_hash

            cleaned = preprocess_documents(source_documents)
            chunks = chunk_documents(
                cleaned,
                chunk_size=self._chunk_size,
                chunk_overlap=self._chunk_overlap,
            )
            deterministic_chunks: List[Document] = []
            for index, chunk in enumerate(chunks):
                metadata = dict(chunk.metadata)
                metadata.update(
                    {
                        "source": source,
                        "content_hash": content_hash,
                        "chunk_index": index,
                    }
                )
                metadata["chunk_id"] = self._chunk_id(
                    source,
                    content_hash,
                    index,
                    chunk.page_content,
                )
                deterministic_chunks.append(
                    Document(
                        page_content=chunk.page_content,
                        metadata=metadata,
                    )
                )
            if deterministic_chunks:
                prepared.append((source, content_hash, deterministic_chunks))
        return prepared

    @staticmethod
    def _content_hash(documents: List[Document]) -> str:
        declared = {
            str(document.metadata["content_hash"])
            for document in documents
            if document.metadata.get("content_hash")
        }
        if len(declared) == 1:
            value = declared.pop()
            if len(value) == 64:
                return value.lower()

        digest = hashlib.sha256()
        for document in documents:
            digest.update(document.page_content.encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()

    @staticmethod
    def _chunk_id(
        source: str,
        content_hash: str,
        index: int,
        content: str,
    ) -> str:
        digest = hashlib.sha256()
        for value in (source, content_hash, str(index), content):
            digest.update(value.encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()
