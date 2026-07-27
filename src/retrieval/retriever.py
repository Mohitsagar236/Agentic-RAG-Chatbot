"""Wraps the vector store retrieval and formats retrieved context."""

import logging
import math
from typing import List, Tuple

from langchain_core.documents import Document

import config
from src.vectorstore.vector_db import VectorDatabase

logger = logging.getLogger(__name__)


def _is_relevant(query: str, db: VectorDatabase) -> bool:
    threshold = config.RETRIEVAL_MIN_RELEVANCE
    if threshold < 0:
        return True

    # Inspect the class so MagicMock-based and legacy fake databases retain
    # compatibility instead of manufacturing a callable attribute on access.
    score_method = getattr(
        type(db),
        "similarity_search_with_relevance_scores",
        None,
    )
    if not callable(score_method):
        logger.debug("Vector database has no score-aware search; skipping gate.")
        return True

    try:
        results = db.similarity_search_with_relevance_scores(query, k=1)
    except (AttributeError, NotImplementedError):
        logger.debug("Score-aware search is unsupported; skipping gate.")
        return True
    if not results:
        return False

    best_score = max(float(score) for _, score in results)
    relevant = math.isfinite(best_score) and best_score >= threshold
    if not relevant:
        logger.info(
            "Retrieval abstained: best relevance %.3f is below %.3f.",
            best_score,
            threshold,
        )
    return relevant


def retrieve(
    query: str,
    db: VectorDatabase,
    k: int = None,
    method: str = "mmr",
) -> List[Document]:
    if not _is_relevant(query, db):
        return []

    if method == "mmr":
        docs = db.mmr_search(query, k=k)
        if not docs:
            logger.info(
                "MMR search returned no docs for query %r; falling back to similarity search.",
                query[:60],
            )
            docs = db.similarity_search(query, k=k)
    else:
        docs = db.similarity_search(query, k=k)
    logger.info("Retrieved %d chunks for query: %r", len(docs), query[:60])
    return docs


def format_context(docs: List[Document]) -> str:
    if not docs:
        return ""
    parts = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page", "")
        label = f"[{i}] Source: {source}" + (f", page {page}" if page else "")
        parts.append(f"{label}\n{doc.page_content.strip()}")
    return "\n\n---\n\n".join(parts)


def retrieve_with_context(
    query: str,
    db: VectorDatabase,
    k: int = None,
    method: str = "mmr",
) -> Tuple[List[Document], str]:
    docs = retrieve(query, db, k=k, method=method)
    context = format_context(docs)
    return docs, context
