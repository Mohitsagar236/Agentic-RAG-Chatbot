"""Provider-agnostic vector database facade with safe persistence semantics."""

import json
import logging
import os
from pathlib import Path
from threading import RLock
from typing import List, Set

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

import config


logger = logging.getLogger(__name__)

# Backwards-compatible module attributes.
VECTOR_DB = config.VECTOR_DB
FAISS_INDEX_PATH = config.FAISS_INDEX_PATH


def _document_ids(documents: List[Document]) -> list[str] | None:
    ids = [document.metadata.get("chunk_id") for document in documents]
    if ids and all(isinstance(value, str) and value for value in ids):
        return ids
    return None


class _ChromaBackend:
    def __init__(self, embeddings: Embeddings):
        from langchain_chroma import Chroma

        self._store = Chroma(
            collection_name=config.COLLECTION_NAME,
            embedding_function=embeddings,
            persist_directory=config.CHROMA_PERSIST_DIR,
        )

    def add_documents(self, documents: List[Document]) -> None:
        ids = _document_ids(documents)
        if ids is None:
            self._store.add_documents(documents)
        else:
            self._store.add_documents(documents, ids=ids)

    def similarity_search(self, query: str, k: int) -> List[Document]:
        safe_k = min(k, self.count())
        if safe_k == 0:
            return []
        return self._store.similarity_search(query, k=safe_k)

    def similarity_search_with_relevance_scores(
        self,
        query: str,
        k: int,
    ) -> list[tuple[Document, float]]:
        safe_k = min(k, self.count())
        if safe_k == 0:
            return []
        return self._store.similarity_search_with_relevance_scores(
            query,
            k=safe_k,
        )

    def mmr_search(self, query: str, k: int, fetch_k: int) -> List[Document]:
        count = self.count()
        safe_k = min(k, count)
        if safe_k == 0:
            return []
        return self._store.max_marginal_relevance_search(
            query,
            k=safe_k,
            fetch_k=min(fetch_k, count),
        )

    def as_retriever(self, search_type: str, k: int):
        kwargs = {"k": k}
        if search_type == "mmr":
            kwargs["fetch_k"] = k * 3
        return self._store.as_retriever(
            search_type=search_type,
            search_kwargs=kwargs,
        )

    def _get(self, **kwargs) -> dict:
        return self._store.get(**kwargs)

    def count(self) -> int:
        return len(self._get(include=[])["ids"])

    def list_sources(self) -> List[str]:
        result = self._get(include=["metadatas"])
        metadatas = result.get("metadatas") or []
        return sorted(
            {
                (metadata or {}).get("source", "unknown")
                for metadata in metadatas
            }
        )

    def get_document_stats(self) -> List[dict]:
        result = self._get(include=["metadatas"])
        counts: dict[str, int] = {}
        for metadata in result.get("metadatas") or []:
            source = (metadata or {}).get("source", "unknown")
            counts[source] = counts.get(source, 0) + 1
        return [
            {"source": source, "chunks": count}
            for source, count in sorted(counts.items())
        ]

    def get_chunks_for_source(self, source: str) -> List[Document]:
        result = self._get(
            where={"source": source},
            include=["documents", "metadatas"],
        )
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        return [
            Document(page_content=content, metadata=metadata or {})
            for content, metadata in zip(documents, metadatas)
        ]

    def list_content_hashes(self) -> Set[str]:
        result = self._get(include=["metadatas"])
        return {
            metadata["content_hash"]
            for metadata in result.get("metadatas") or []
            if isinstance(metadata, dict) and metadata.get("content_hash")
        }

    def delete_source(self, source: str) -> int:
        matches = self._get(where={"source": source}, include=[])
        ids = matches.get("ids") or []
        if ids:
            self._store.delete(ids=ids)
        return len(ids)

    def reset(self) -> None:
        ids = self._get(include=[]).get("ids") or []
        batch_size = 5_000
        for offset in range(0, len(ids), batch_size):
            self._store.delete(ids=ids[offset:offset + batch_size])


class _FAISSBackend:
    """FAISS adapter using JSON for docstore metadata instead of pickle."""

    _INDEX_FILE = "index.faiss"
    _DOCSTORE_FILE = "index.json"
    _LEGACY_PICKLE_FILE = "index.pkl"

    def __init__(self, embeddings: Embeddings):
        try:
            import faiss
            from langchain_community.vectorstores import FAISS
        except ImportError as exc:
            raise ImportError("Install faiss-cpu: pip install faiss-cpu") from exc

        self._embeddings = embeddings
        self._faiss = faiss
        self._FAISS = FAISS
        self._store = None
        self._index_path = Path(config.FAISS_INDEX_PATH).resolve()
        self._validate_index_path()
        self._load_if_present()

    def _validate_index_path(self) -> None:
        path = self._index_path
        anchor = Path(path.anchor).resolve()
        protected = {anchor, Path(config.BASE_DIR).resolve()}
        try:
            protected.add(Path.home().resolve())
        except RuntimeError:
            pass
        if path in protected:
            raise ValueError(f"Unsafe FAISS_INDEX_PATH: {path}")

    def _load_if_present(self) -> None:
        index_file = self._index_path / self._INDEX_FILE
        docstore_file = self._index_path / self._DOCSTORE_FILE
        legacy_pickle = self._index_path / self._LEGACY_PICKLE_FILE

        if index_file.exists() and docstore_file.exists():
            self._store = self._load_safe(index_file, docstore_file)
            logger.info("Loaded safe FAISS index from %s", self._index_path)
            return

        if index_file.exists() and legacy_pickle.exists():
            if not config.FAISS_ALLOW_DANGEROUS_DESERIALIZATION:
                raise RuntimeError(
                    "A legacy pickle-based FAISS index was found at "
                    f"{self._index_path}. Refusing unsafe deserialization. "
                    "Re-ingest the documents, or explicitly set "
                    "FAISS_ALLOW_DANGEROUS_DESERIALIZATION=true only if the "
                    "index is trusted."
                )
            logger.warning(
                "Loading trusted legacy FAISS pickle from %s because "
                "FAISS_ALLOW_DANGEROUS_DESERIALIZATION is enabled.",
                self._index_path,
            )
            self._store = self._FAISS.load_local(
                str(self._index_path),
                self._embeddings,
                allow_dangerous_deserialization=True,
            )

    def _load_safe(self, index_file: Path, docstore_file: Path):
        from langchain_community.docstore.in_memory import InMemoryDocstore

        payload = json.loads(docstore_file.read_text(encoding="utf-8"))
        documents = {
            doc_id: Document(
                page_content=value["page_content"],
                metadata=value.get("metadata") or {},
            )
            for doc_id, value in payload["documents"].items()
        }
        mapping = {
            int(position): doc_id
            for position, doc_id in payload["index_to_docstore_id"].items()
        }
        return self._FAISS(
            embedding_function=self._embeddings,
            index=self._faiss.read_index(str(index_file)),
            docstore=InMemoryDocstore(documents),
            index_to_docstore_id=mapping,
        )

    def _save_safe(self) -> None:
        if self._store is None:
            return
        self._index_path.mkdir(parents=True, exist_ok=True)
        index_file = self._index_path / self._INDEX_FILE
        docstore_file = self._index_path / self._DOCSTORE_FILE
        tmp_index = self._index_path / f".{self._INDEX_FILE}.tmp"
        tmp_docstore = self._index_path / f".{self._DOCSTORE_FILE}.tmp"

        documents = {
            doc_id: {
                "page_content": document.page_content,
                "metadata": document.metadata,
            }
            for doc_id, document in self._store.docstore._dict.items()
        }
        payload = {
            "index_to_docstore_id": self._store.index_to_docstore_id,
            "documents": documents,
        }

        self._faiss.write_index(self._store.index, str(tmp_index))
        tmp_docstore.write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        os.replace(tmp_index, index_file)
        os.replace(tmp_docstore, docstore_file)

        legacy_pickle = self._index_path / self._LEGACY_PICKLE_FILE
        if legacy_pickle.exists():
            legacy_pickle.unlink()

    def add_documents(self, documents: List[Document]) -> None:
        ids = _document_ids(documents)
        if self._store is None:
            kwargs = {"ids": ids} if ids is not None else {}
            self._store = self._FAISS.from_documents(
                documents,
                self._embeddings,
                **kwargs,
            )
        else:
            kwargs = {"ids": ids} if ids is not None else {}
            self._store.add_documents(documents, **kwargs)
        self._save_safe()

    def similarity_search(self, query: str, k: int) -> List[Document]:
        if self._store is None:
            return []
        return self._store.similarity_search(query, k=min(k, self.count()))

    def similarity_search_with_relevance_scores(
        self,
        query: str,
        k: int,
    ) -> list[tuple[Document, float]]:
        if self._store is None:
            return []
        return self._store.similarity_search_with_relevance_scores(
            query,
            k=min(k, self.count()),
        )

    def mmr_search(self, query: str, k: int, fetch_k: int) -> List[Document]:
        if self._store is None:
            return []
        return self._store.max_marginal_relevance_search(
            query,
            k=min(k, self.count()),
            fetch_k=min(fetch_k, self.count()),
        )

    def as_retriever(self, search_type: str, k: int):
        if self._store is None:
            raise RuntimeError("FAISS store is empty. Run ingest.py first.")
        kwargs = {"k": k}
        if search_type == "mmr":
            kwargs["fetch_k"] = k * 3
        return self._store.as_retriever(
            search_type=search_type,
            search_kwargs=kwargs,
        )

    def count(self) -> int:
        return 0 if self._store is None else int(self._store.index.ntotal)

    def _documents(self) -> list[Document]:
        if self._store is None:
            return []
        return list(self._store.docstore._dict.values())

    def list_sources(self) -> List[str]:
        return sorted(
            {
                document.metadata.get("source", "unknown")
                for document in self._documents()
            }
        )

    def list_content_hashes(self) -> Set[str]:
        return {
            document.metadata["content_hash"]
            for document in self._documents()
            if document.metadata.get("content_hash")
        }

    def get_document_stats(self) -> List[dict]:
        counts: dict[str, int] = {}
        for document in self._documents():
            source = document.metadata.get("source", "unknown")
            counts[source] = counts.get(source, 0) + 1
        return [
            {"source": source, "chunks": count}
            for source, count in sorted(counts.items())
        ]

    def get_chunks_for_source(self, source: str) -> List[Document]:
        return [
            document
            for document in self._documents()
            if document.metadata.get("source") == source
        ]

    def delete_source(self, source: str) -> int:
        raise NotImplementedError(
            "Per-document deletion is not supported with FAISS. "
            "Use reset() to clear all documents."
        )

    def reset(self) -> None:
        self._validate_index_path()
        for filename in (
            self._INDEX_FILE,
            self._DOCSTORE_FILE,
            self._LEGACY_PICKLE_FILE,
            f".{self._INDEX_FILE}.tmp",
            f".{self._DOCSTORE_FILE}.tmp",
        ):
            file_path = self._index_path / filename
            if file_path.is_file():
                file_path.unlink()
        try:
            self._index_path.rmdir()
        except OSError:
            # Preserve unexpected user files rather than recursively deleting them.
            pass
        self._store = None


class VectorDatabase:
    """Thread-safe facade over the configured vector-store backend."""

    def __init__(self, embeddings: Embeddings):
        self._lock = RLock()
        if config.VECTOR_DB == "faiss":
            logger.info("Using FAISS vector store.")
            self._backend = _FAISSBackend(embeddings)
        else:
            logger.info("Using ChromaDB vector store.")
            self._backend = _ChromaBackend(embeddings)

    def add_documents(self, documents: List[Document]) -> int:
        """Add documents and return the number of newly created chunks."""
        if not documents:
            logger.warning("No documents to add.")
            return 0
        with self._lock:
            before = self._backend.count()
            self._backend.add_documents(documents)
            after = self._backend.count()
        added = max(0, after - before)
        logger.info(
            "Added %d chunk(s); vector store now has %d chunks.",
            added,
            after,
        )
        return added

    def similarity_search(self, query: str, k: int = None) -> List[Document]:
        with self._lock:
            return self._backend.similarity_search(query, k=k or config.TOP_K)

    def similarity_search_with_relevance_scores(
        self,
        query: str,
        k: int = None,
    ) -> list[tuple[Document, float]]:
        with self._lock:
            return self._backend.similarity_search_with_relevance_scores(
                query,
                k=k or config.TOP_K,
            )

    def mmr_search(
        self,
        query: str,
        k: int = None,
        fetch_k: int = None,
    ) -> List[Document]:
        k = k or config.TOP_K
        with self._lock:
            return self._backend.mmr_search(
                query,
                k=k,
                fetch_k=fetch_k or k * 3,
            )

    def as_retriever(self, search_type: str = "mmr", k: int = None):
        with self._lock:
            return self._backend.as_retriever(
                search_type=search_type,
                k=k or config.TOP_K,
            )

    def count(self) -> int:
        with self._lock:
            return self._backend.count()

    def list_sources(self) -> List[str]:
        with self._lock:
            return self._backend.list_sources()

    def list_content_hashes(self) -> Set[str]:
        with self._lock:
            return self._backend.list_content_hashes()

    def get_document_stats(self) -> List[dict]:
        with self._lock:
            return self._backend.get_document_stats()

    def get_chunks_for_source(self, source: str) -> List[Document]:
        with self._lock:
            return self._backend.get_chunks_for_source(source)

    def delete_source(self, source: str) -> int:
        with self._lock:
            removed = self._backend.delete_source(source)
        logger.info("Deleted source %s (%d chunks removed).", source, removed)
        return removed

    def reset(self) -> None:
        with self._lock:
            self._backend.reset()
        logger.info("Vector store cleared.")
