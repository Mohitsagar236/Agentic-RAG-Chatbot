"""Hermetic API and web-service tests.

These tests use in-memory fakes so they never load an embedding model, call an
LLM, or mutate the repository's persisted vector database.
"""

from io import BytesIO

import pytest
from langchain_core.documents import Document

from web.app import create_app
from web.services import ApplicationServices


class FakeDatabase:
    def __init__(self):
        self.documents: list[Document] = [
            Document(
                page_content="Retrieval augmented generation uses retrieved context.",
                metadata={
                    "source": "seed://document/guide.txt",
                    "display_name": "guide.txt",
                    "document_id": "seed-document",
                    "content_hash": "seed-hash",
                    "chunk_index": 0,
                },
            )
        ]

    def count(self):
        return len(self.documents)

    def list_sources(self):
        return sorted({doc.metadata["source"] for doc in self.documents})

    def list_content_hashes(self):
        return {
            doc.metadata["content_hash"]
            for doc in self.documents
            if doc.metadata.get("content_hash")
        }

    def add_documents(self, documents):
        self.documents.extend(documents)
        return len(self.documents)

    def get_document_stats(self):
        grouped = {}
        for doc in self.documents:
            source = doc.metadata["source"]
            record = grouped.setdefault(
                source,
                {
                    "source": source,
                    "display_name": doc.metadata.get("display_name"),
                    "document_id": doc.metadata.get("document_id"),
                    "chunks": 0,
                },
            )
            record["chunks"] += 1
        return list(grouped.values())

    def get_chunks_for_source(self, source):
        return [
            doc for doc in self.documents if doc.metadata.get("source") == source
        ]

    def delete_source(self, source):
        before = len(self.documents)
        self.documents = [
            doc for doc in self.documents if doc.metadata.get("source") != source
        ]
        return before - len(self.documents)

    def reset(self):
        self.documents.clear()


class FakeAgent:
    def __init__(self, _database):
        self.history = []

    def chat(self, question):
        self.history.append(question)
        return {
            "answer": f"turn {len(self.history)}: {question}",
            "sources": ["seed://document/guide.txt"],
            "context": "fake context",
        }

    def clear_memory(self):
        self.history.clear()


@pytest.fixture
def fake_database():
    return FakeDatabase()


@pytest.fixture
def app(fake_database):
    services = ApplicationServices(
        database=fake_database,
        agent_factory=FakeAgent,
    )
    return create_app(
        services=services,
        test_config={"TESTING": True, "RATELIMIT_ENABLED": False},
    )


@pytest.fixture
def client(app):
    return app.test_client()


def mutate_headers():
    return {"X-RAG-Client": "web"}


def test_health_does_not_require_database_initialization(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_status_and_documents_have_stable_shapes(client):
    status = client.get("/api/status")
    documents = client.get("/api/documents")

    assert status.status_code == 200
    assert status.get_json()["chunk_count"] == 1
    assert documents.status_code == 200
    assert documents.get_json()["documents"][0]["name"] == "guide.txt"


def test_chat_memory_is_isolated_by_conversation_id(client):
    first_a = client.post(
        "/api/chat",
        json={"question": "A1", "conversation_id": "chat_a"},
        headers=mutate_headers(),
    )
    first_b = client.post(
        "/api/chat",
        json={"question": "B1", "conversation_id": "chat_b"},
        headers=mutate_headers(),
    )
    second_a = client.post(
        "/api/chat",
        json={"question": "A2", "conversation_id": "chat_a"},
        headers=mutate_headers(),
    )

    assert first_a.get_json()["answer"] == "turn 1: A1"
    assert first_b.get_json()["answer"] == "turn 1: B1"
    assert second_a.get_json()["answer"] == "turn 2: A2"
    assert first_a.get_json()["sources"][0]["name"] == "guide.txt"


def test_clear_memory_only_clears_requested_conversation(client):
    for conversation_id in ("chat_a", "chat_b"):
        client.post(
            "/api/chat",
            json={"question": "first", "conversation_id": conversation_id},
            headers=mutate_headers(),
        )

    response = client.post(
        "/api/clear-memory",
        json={"conversation_id": "chat_a"},
        headers=mutate_headers(),
    )
    next_a = client.post(
        "/api/chat",
        json={"question": "again", "conversation_id": "chat_a"},
        headers=mutate_headers(),
    )
    next_b = client.post(
        "/api/chat",
        json={"question": "again", "conversation_id": "chat_b"},
        headers=mutate_headers(),
    )

    assert response.status_code == 200
    assert next_a.get_json()["answer"] == "turn 1: again"
    assert next_b.get_json()["answer"] == "turn 2: again"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({}, "question_required"),
        ({"question": "hello"}, "conversation_required"),
        (
            {"question": "hello", "conversation_id": "../../bad"},
            "invalid_conversation",
        ),
    ],
)
def test_chat_validation_returns_structured_json(client, payload, code):
    response = client.post(
        "/api/chat",
        json=payload,
        headers=mutate_headers(),
    )
    body = response.get_json()

    assert response.status_code == 400
    assert body["error"]["code"] == code
    assert body["error"]["request_id"]


def test_mutating_routes_require_application_header(client):
    response = client.post(
        "/api/chat",
        json={"question": "hello", "conversation_id": "chat_a"},
    )
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "request_not_allowed"


def test_upload_sanitizes_filename_and_uses_opaque_source(
    client,
    fake_database,
):
    response = client.post(
        "/api/ingest",
        data={
            "files": (
                BytesIO(b"This is a useful document with enough content."),
                "../unsafe.txt",
            )
        },
        content_type="multipart/form-data",
        headers=mutate_headers(),
    )
    body = response.get_json()

    assert response.status_code == 200
    assert body["files"] == ["unsafe.txt"]
    uploaded = fake_database.documents[-1]
    assert uploaded.metadata["display_name"] == "unsafe.txt"
    assert uploaded.metadata["source"].startswith("upload://")
    assert ".." not in uploaded.metadata["source"]


def test_duplicate_upload_is_skipped(client):
    payload = b"This duplicate has enough content to pass preprocessing."
    first = client.post(
        "/api/ingest",
        data={"files": (BytesIO(payload), "first.txt")},
        content_type="multipart/form-data",
        headers=mutate_headers(),
    )
    second = client.post(
        "/api/ingest",
        data={"files": (BytesIO(payload), "renamed.txt")},
        content_type="multipart/form-data",
        headers=mutate_headers(),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()["skipped"] == ["renamed.txt"]


def test_delete_and_reset_report_real_outcomes(client, fake_database):
    missing = client.delete(
        "/api/documents?source=missing",
        headers=mutate_headers(),
    )
    deleted = client.delete(
        "/api/documents?source=seed%3A%2F%2Fdocument%2Fguide.txt",
        headers=mutate_headers(),
    )
    reset = client.post("/api/reset", headers=mutate_headers())

    assert missing.status_code == 404
    assert deleted.status_code == 200
    assert reset.status_code == 200
    assert fake_database.count() == 0


def test_removed_debug_and_secret_routes_are_not_available(client):
    assert client.get("/api/debug").status_code == 404
    assert client.get("/api/deepgram-key").status_code == 404


def test_security_headers_are_applied(client):
    response = client.get("/")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
