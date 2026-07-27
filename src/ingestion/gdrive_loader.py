"""Google Drive source adapter using a read-only service account."""

import io
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import List

from langchain_core.documents import Document


logger = logging.getLogger(__name__)

SUPPORTED_MIME_TYPES = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/vnd.google-apps.document": ".txt",
}
EXPORT_MIME = {
    "application/vnd.google-apps.document": "text/plain",
}
FOLDER_MIME = "application/vnd.google-apps.folder"


def _get_drive_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ImportError(
            "Google API libraries not installed. Run:\n"
            "  pip install google-api-python-client google-auth"
        ) from exc

    key_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
    if not key_path or not Path(key_path).is_file():
        raise FileNotFoundError(
            "GOOGLE_SERVICE_ACCOUNT_KEY not set or file not found. "
            "Set it to a service account JSON key path."
        )
    credentials = service_account.Credentials.from_service_account_file(
        key_path,
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _api_call_with_retry(fn, max_retries: int = 3):
    """Execute a Google API callable with bounded exponential backoff."""
    from googleapiclient.errors import HttpError

    for attempt in range(max_retries):
        try:
            return fn()
        except HttpError as exc:
            retryable = exc.resp.status in (429, 500, 502, 503, 504)
            if not retryable or attempt == max_retries - 1:
                raise
            wait_seconds = 2 ** attempt
            logger.warning(
                "Google API error %s; retrying in %ds (attempt %d/%d)",
                exc.resp.status,
                wait_seconds,
                attempt + 1,
                max_retries,
            )
            time.sleep(wait_seconds)
    raise RuntimeError("Google API retry loop exited unexpectedly")


def _list_files(service, folder_id: str) -> List[dict]:
    files: List[dict] = []
    page_token = None
    escaped_folder_id = folder_id.replace("\\", "\\\\").replace("'", "\\'")
    query = f"'{escaped_folder_id}' in parents and trashed=false"
    while True:
        response = _api_call_with_retry(
            lambda: service.files().list(
                q=query,
                pageSize=100,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
                pageToken=page_token,
            ).execute()
        )
        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return files


def _load_folder(
    service,
    folder_id: str,
    recursive: bool,
    temp_dir: Path,
    visited: set[str],
) -> List[Document]:
    from googleapiclient.http import MediaIoBaseDownload

    if folder_id in visited:
        logger.warning("Skipping already-visited Drive folder %s", folder_id)
        return []
    visited.add(folder_id)

    files = _list_files(service, folder_id)
    logger.info("Found %d items in Drive folder %s", len(files), folder_id)
    all_documents: List[Document] = []

    for metadata in files:
        mime_type = metadata["mimeType"]
        name = metadata["name"]
        file_id = metadata["id"]

        if mime_type == FOLDER_MIME:
            if recursive:
                all_documents.extend(
                    _load_folder(
                        service,
                        file_id,
                        recursive=True,
                        temp_dir=temp_dir,
                        visited=visited,
                    )
                )
            continue

        if mime_type not in SUPPORTED_MIME_TYPES:
            logger.debug("Skipping unsupported MIME type: %s (%s)", mime_type, name)
            continue

        suffix = SUPPORTED_MIME_TYPES[mime_type]
        local_path = temp_dir / f"{file_id}{suffix}"
        try:
            if mime_type in EXPORT_MIME:
                request = service.files().export_media(
                    fileId=file_id,
                    mimeType=EXPORT_MIME[mime_type],
                )
            else:
                request = service.files().get_media(fileId=file_id)

            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = _api_call_with_retry(downloader.next_chunk)
            local_path.write_bytes(buffer.getvalue())

            from src.ingestion.document_loader import load_document

            documents = load_document(local_path)
            for document in documents:
                document.metadata.update(
                    {
                        "source": f"gdrive://{file_id}/{name}",
                        "source_name": name,
                        "drive_file_id": file_id,
                        "drive_modified_time": metadata.get("modifiedTime", ""),
                    }
                )
            all_documents.extend(documents)
            logger.info("Downloaded and loaded Drive file: %s", name)
        except Exception:
            logger.exception("Failed to download or load Drive file %s", name)

    return all_documents


def load_from_google_drive(
    folder_id: str,
    recursive: bool = False,
) -> List[Document]:
    """Download supported files from a Drive folder into Documents."""
    folder_id = folder_id.strip()
    if not folder_id:
        raise ValueError("folder_id must not be empty")

    service = _get_drive_service()
    with tempfile.TemporaryDirectory(prefix="rag-gdrive-") as directory:
        documents = _load_folder(
            service,
            folder_id,
            recursive=recursive,
            temp_dir=Path(directory),
            visited=set(),
        )
    logger.info("Loaded %d document segments from Google Drive.", len(documents))
    return documents
