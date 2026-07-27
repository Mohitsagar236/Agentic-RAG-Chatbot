#!/usr/bin/env python
"""Command-line entry point for the reusable document-ingestion service."""

import argparse
import logging
from pathlib import Path
from typing import Optional

import config
from src.embeddings.embedding_generator import get_embeddings
from src.services.ingestion_service import IngestionService
from src.utils.helpers import setup_logging
from src.vectorstore.vector_db import VectorDatabase


def run_ingestion(
    source: Optional[str] = None,
    gdrive_folder_id: Optional[str] = None,
    reset: bool = False,
) -> int:
    """Ingest one source and return the number of newly added chunks.

    Source validation and chunk preparation occur before a requested reset.
    Exceptions are raised to callers rather than terminating the process.
    """
    setup_logging()
    logger = logging.getLogger(__name__)

    if source and gdrive_folder_id:
        raise ValueError("Choose either a local source or Google Drive, not both.")

    local_source: Path | None = None
    if not gdrive_folder_id:
        local_source = Path(source or config.DOCUMENTS_DIR).expanduser()
        if not local_source.exists():
            raise FileNotFoundError(f"Source not found: {local_source}")
    elif not gdrive_folder_id.strip():
        raise ValueError("Google Drive folder ID must not be empty.")

    embeddings = get_embeddings()
    database = VectorDatabase(embeddings)
    service = IngestionService(database)

    if gdrive_folder_id:
        logger.info("Ingesting Google Drive folder %s", gdrive_folder_id)
        result = service.ingest_google_drive(
            gdrive_folder_id,
            reset=reset,
            recursive=True,
        )
    else:
        logger.info("Ingesting local source %s", local_source)
        result = service.ingest_path(local_source, reset=reset)

    if result.skipped_sources:
        logger.info(
            "Skipped %d unchanged source(s).",
            len(result.skipped_sources),
        )
    if result.replaced_sources:
        logger.info(
            "Replaced %d changed source(s).",
            len(result.replaced_sources),
        )
    logger.info(
        "Ingestion complete: %d new chunks; %d chunks total.",
        result.chunks_added,
        result.total_chunks,
    )
    return result.chunks_added


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest documents into the RAG vector store.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python ingest.py
  python ingest.py --source my_docs/
  python ingest.py --source report.pdf
  python ingest.py --gdrive DRIVE_FOLDER_ID
  python ingest.py --reset
        """,
    )
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument(
        "--source",
        default=None,
        help="Local file or directory (default: data/documents/)",
    )
    sources.add_argument(
        "--gdrive",
        default=None,
        metavar="FOLDER_ID",
        help="Google Drive folder ID (requires service-account setup)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear existing vector data only after the source is prepared",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        run_ingestion(
            source=args.source,
            gdrive_folder_id=args.gdrive,
            reset=args.reset,
        )
    except (FileNotFoundError, ValueError, RuntimeError, ImportError) as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 1
    except KeyboardInterrupt:
        logging.getLogger(__name__).warning("Ingestion cancelled.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
