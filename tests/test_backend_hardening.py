"""Focused stdlib tests for backend hardening behavior."""

from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

import config
from src.memory.conversation_memory import ConversationMemory
from src.retrieval.retriever import retrieve
from src.services.ingestion_service import IngestionService
from src.vectorstore.vector_db import _FAISSBackend, VectorDatabase


class FakeVectorDatabase:
    def __init__(self):
        self.documents = []
        self.reset_called = False

    def add_documents(self, documents):
        before = len(self.documents)
        self.documents.extend(documents)
        return len(self.documents) - before

    def count(self):
        return len(self.documents)

    def list_content_hashes(self):
        return {
            document.metadata["content_hash"]
            for document in self.documents
            if document.metadata.get("content_hash")
        }

    def list_sources(self):
        return sorted(
            {
                document.metadata["source"]
                for document in self.documents
            }
        )

    def delete_source(self, source):
        before = len(self.documents)
        self.documents = [
            document
            for document in self.documents
            if document.metadata.get("source") != source
        ]
        return before - len(self.documents)

    def reset(self):
        self.reset_called = True
        self.documents.clear()


class ConversationMemoryTests(unittest.TestCase):
    def test_memory_is_bounded(self):
        memory = ConversationMemory(window=2)
        for index in range(20):
            memory.add_user(f"question {index}")
            memory.add_assistant(f"answer {index}")
        self.assertEqual(len(memory), 4)
        self.assertEqual(memory.get_history()[0].content, "question 18")

    def test_invalid_window_is_rejected(self):
        with self.assertRaises(ValueError):
            ConversationMemory(window=0)

    def test_concurrent_writes_remain_bounded(self):
        memory = ConversationMemory(window=5)

        def write_messages(prefix):
            for index in range(100):
                memory.add_user(f"{prefix}-{index}")

        threads = [Thread(target=write_messages, args=(str(i),)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(memory), 10)


class IngestionServiceTests(unittest.TestCase):
    def test_empty_input_does_not_reset_existing_store(self):
        database = FakeVectorDatabase()
        database.documents.append(
            Document(page_content="existing", metadata={"source": "old.txt"})
        )
        result = IngestionService(database).ingest_documents([], reset=True)
        self.assertFalse(database.reset_called)
        self.assertEqual(result.chunks_added, 0)
        self.assertEqual(result.total_chunks, 1)

    def test_ingestion_is_deterministic_and_content_deduplicated(self):
        database = FakeVectorDatabase()
        service = IngestionService(database, chunk_size=100, chunk_overlap=10)
        document = Document(
            page_content="A sufficiently long document body for ingestion. " * 5,
            metadata={"source": "sample.txt"},
        )
        first = service.ingest_documents([document])
        first_ids = [
            chunk.metadata["chunk_id"]
            for chunk in database.documents
        ]
        second = service.ingest_documents([document])

        self.assertGreater(first.chunks_added, 0)
        self.assertEqual(first.chunks_added, first.total_chunks)
        self.assertEqual(second.chunks_added, 0)
        self.assertEqual(second.skipped_sources, ("sample.txt",))
        self.assertEqual(
            first_ids,
            [chunk.metadata["chunk_id"] for chunk in database.documents],
        )

    def test_changed_source_replaces_old_chunks(self):
        database = FakeVectorDatabase()
        service = IngestionService(database, chunk_size=500, chunk_overlap=50)
        service.ingest_documents(
            [
                Document(
                    page_content="Original document content that is long enough.",
                    metadata={"source": "sample.txt"},
                )
            ]
        )
        result = service.ingest_documents(
            [
                Document(
                    page_content="Replacement document content that is long enough.",
                    metadata={"source": "sample.txt"},
                )
            ]
        )
        self.assertEqual(result.replaced_sources, ("sample.txt",))
        self.assertIn("Replacement", database.documents[0].page_content)


class SafeFaissResetTests(unittest.TestCase):
    def test_reset_removes_only_known_index_files(self):
        with tempfile.TemporaryDirectory() as directory:
            index_path = Path(directory) / "faiss-index"
            index_path.mkdir()
            (index_path / "index.faiss").write_bytes(b"index")
            (index_path / "index.json").write_text("{}", encoding="utf-8")
            unexpected = index_path / "keep.txt"
            unexpected.write_text("keep", encoding="utf-8")

            backend = object.__new__(_FAISSBackend)
            backend._index_path = index_path
            backend._store = object()
            backend.reset()

            self.assertTrue(unexpected.exists())
            self.assertFalse((index_path / "index.faiss").exists())
            self.assertFalse((index_path / "index.json").exists())
            self.assertIsNone(backend._store)


class RelevanceGateTests(unittest.TestCase):
    def test_low_relevance_abstains_before_mmr(self):
        class LowRelevanceDatabase:
            mmr_called = False

            def similarity_search_with_relevance_scores(self, query, k):
                return [
                    (
                        Document(
                            page_content="unrelated",
                            metadata={"source": "unrelated.txt"},
                        ),
                        0.1,
                    )
                ]

            def mmr_search(self, query, k=None):
                self.mmr_called = True
                return []

            def similarity_search(self, query, k=None):
                return []

        database = LowRelevanceDatabase()
        with patch.object(config, "RETRIEVAL_MIN_RELEVANCE", 0.5):
            self.assertEqual(retrieve("question", database), [])
        self.assertFalse(database.mmr_called)


class FakeEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        lowered = text.lower()
        return [
            1.0 if "alpha" in lowered else 0.0,
            1.0 if "beta" in lowered else 0.0,
            0.5,
        ]


class ChromaFacadeTests(unittest.TestCase):
    def test_counts_scores_ordering_delete_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            database = None
            try:
                with (
                    patch.object(config, "VECTOR_DB", "chroma"),
                    patch.object(config, "CHROMA_PERSIST_DIR", directory),
                    patch.object(
                        config,
                        "COLLECTION_NAME",
                        "backend-hardening-test",
                    ),
                ):
                    database = VectorDatabase(FakeEmbeddings())
                    added = database.add_documents(
                        [
                            Document(
                                page_content="alpha content",
                                metadata={
                                    "source": "z.txt",
                                    "content_hash": "a" * 64,
                                    "chunk_id": "alpha-id",
                                },
                            ),
                            Document(
                                page_content="beta content",
                                metadata={
                                    "source": "a.txt",
                                    "content_hash": "b" * 64,
                                    "chunk_id": "beta-id",
                                },
                            ),
                        ]
                    )
                    self.assertEqual(added, 2)
                    self.assertEqual(database.count(), 2)
                    self.assertEqual(database.list_sources(), ["a.txt", "z.txt"])
                    self.assertEqual(
                        len(
                            database.similarity_search_with_relevance_scores(
                                "alpha",
                                k=1,
                            )
                        ),
                        1,
                    )
                    self.assertEqual(database.delete_source("z.txt"), 1)
                    database.reset()
                    self.assertEqual(database.count(), 0)
            finally:
                # Chroma caches clients and HNSW file handles globally. Stop this
                # test client's system before the temporary directory is removed.
                from chromadb.api.client import SharedSystemClient

                if database is not None:
                    client = database._backend._store._client
                    client._system.stop()
                database = None
                SharedSystemClient.clear_system_cache()


if __name__ == "__main__":
    unittest.main()
