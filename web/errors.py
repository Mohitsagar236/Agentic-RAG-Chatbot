"""Typed API errors and JSON error response helpers."""

from dataclasses import dataclass
from typing import Any

from flask import g, jsonify


@dataclass
class ApiError(Exception):
    """An expected request error that is safe to show to the client."""

    message: str
    status_code: int = 400
    code: str = "bad_request"
    details: Any = None


def error_response(
    message: str,
    *,
    status_code: int,
    code: str,
    details: Any = None,
):
    payload = {
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(g, "request_id", None),
        }
    }
    if details is not None:
        payload["error"]["details"] = details
    return jsonify(payload), status_code
