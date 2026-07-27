"""JSON API routes for the RAG application."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

import config
from web.errors import ApiError
from web.extensions import limiter
from web.services import ApplicationServices

api = Blueprint("api", __name__, url_prefix="/api")


def _services() -> ApplicationServices:
    return current_app.extensions["rag_services"]


def _json_object() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(
            "A JSON object is required.",
            status_code=400,
            code="invalid_json",
        )
    return data


@api.get("/health")
def health():
    return jsonify({"status": "ok"})


@api.get("/status")
def status():
    return jsonify(_services().status())


@api.post("/chat")
@limiter.limit("30 per minute")
def chat():
    data = _json_object()
    question = str(data.get("question", "")).strip()
    conversation_id = str(data.get("conversation_id", "")).strip()
    if not question:
        raise ApiError("Question is required.", code="question_required")
    if len(question) > 2_000:
        raise ApiError(
            "Question is too long (maximum 2,000 characters).",
            code="question_too_long",
        )
    if not conversation_id:
        raise ApiError(
            "conversation_id is required.",
            code="conversation_required",
        )

    try:
        _services().conversations.validate_id(conversation_id)
    except ValueError as exc:
        raise ApiError(str(exc), code="invalid_conversation") from exc

    if _services().database.count() == 0:
        return jsonify(
            {
                "answer": "No documents are available yet. Upload a document first.",
                "sources": [],
                "conversation_id": conversation_id,
            }
        )

    result = _services().chat(conversation_id, question)
    sources = [
        {"name": Path(source).name, "source": source}
        for source in result.get("sources", [])
    ]
    return jsonify(
        {
            "answer": str(result.get("answer", "")),
            "sources": sources,
            "conversation_id": conversation_id,
        }
    )


@api.post("/ingest")
@limiter.limit("10 per minute")
def ingest():
    files = [item for item in request.files.getlist("files") if item.filename]
    if not files:
        raise ApiError("No files were uploaded.", code="files_required")
    if len(files) > config.MAX_UPLOAD_FILES:
        raise ApiError(
            f"Upload at most {config.MAX_UPLOAD_FILES} files at a time.",
            code="too_many_files",
        )

    result = _services().ingest_files(files)
    if not result.ingested and not result.skipped:
        raise ApiError(
            "No files could be ingested.",
            code="ingestion_failed",
            details=[
                {"name": item.name, "message": item.error}
                for item in result.errors
            ],
        )

    added_chunks = sum(item.chunks for item in result.ingested)
    message = (
        f"Ingested {len(result.ingested)} file(s) and added "
        f"{added_chunks} chunk(s)."
    )
    if result.skipped:
        message += f" Skipped {len(result.skipped)} duplicate file(s)."
    if result.errors:
        message += f" {len(result.errors)} file(s) could not be processed."

    return jsonify(
        {
            "message": message,
            "files": [item.name for item in result.ingested],
            "skipped": [item.name for item in result.skipped],
            "errors": [
                {"name": item.name, "message": item.error}
                for item in result.errors
            ],
            "new_chunks": added_chunks,
            "total_chunks": result.total_chunks,
        }
    )


@api.get("/documents")
def list_documents():
    return jsonify({"documents": _services().list_documents()})


@api.get("/documents/content")
def document_content():
    source = request.args.get("source", "").strip()
    if not source:
        raise ApiError(
            "The source query parameter is required.",
            code="source_required",
        )
    document = _services().document_content(source)
    if document is None:
        raise ApiError(
            "No content was found for this document.",
            status_code=404,
            code="document_not_found",
        )
    return jsonify(document)


@api.delete("/documents")
@limiter.limit("20 per minute")
def delete_document():
    source = request.args.get("source", "").strip()
    if not source:
        raise ApiError(
            "The source query parameter is required.",
            code="source_required",
        )
    removed = _services().delete_document(source)
    if removed <= 0:
        raise ApiError(
            "The document no longer exists.",
            status_code=404,
            code="document_not_found",
        )
    return jsonify(
        {
            "message": f"Deleted {Path(source).name} ({removed} chunk(s) removed).",
            "chunks_removed": removed,
        }
    )


@api.post("/clear-memory")
def clear_memory():
    data = _json_object()
    conversation_id = str(data.get("conversation_id", "")).strip()
    if not conversation_id:
        raise ApiError(
            "conversation_id is required.",
            code="conversation_required",
        )
    try:
        cleared = _services().clear_memory(conversation_id)
    except ValueError as exc:
        raise ApiError(str(exc), code="invalid_conversation") from exc
    return jsonify(
        {
            "message": "Conversation memory cleared.",
            "cleared": cleared,
            "conversation_id": conversation_id,
        }
    )


@api.post("/reset")
@limiter.limit("5 per minute")
def reset_database():
    _services().reset()
    return jsonify({"message": "Knowledge base cleared."})
