"""Create the configured embedding model independently of the chat provider."""

import logging

from langchain_core.embeddings import Embeddings

import config


logger = logging.getLogger(__name__)


def get_embeddings() -> Embeddings:
    """Return the explicitly configured embedding implementation."""
    if config.EMBEDDING_PROVIDER == "openai":
        if not config.OPENAI_API_KEY:
            raise RuntimeError(
                "EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY to be set."
            )

        from langchain_openai import OpenAIEmbeddings

        logger.info("Using OpenAI embeddings: %s", config.OPENAI_EMBEDDING_MODEL)
        return OpenAIEmbeddings(
            model=config.OPENAI_EMBEDDING_MODEL,
            openai_api_key=config.OPENAI_API_KEY,
        )

    return _hf_embeddings()


def _hf_embeddings() -> Embeddings:
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
    except ImportError:
        from langchain_community.embeddings import HuggingFaceEmbeddings

    logger.info("Using HuggingFace local embeddings: %s", config.HF_EMBEDDING_MODEL)
    return HuggingFaceEmbeddings(
        model_name=config.HF_EMBEDDING_MODEL,
        model_kwargs={"device": config.HF_EMBEDDING_DEVICE},
        encode_kwargs={"normalize_embeddings": True},
    )
