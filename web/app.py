"""Flask application factory for the Agentic RAG Chatbot."""

from __future__ import annotations

import logging
import sys
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, g, render_template, request
from werkzeug.exceptions import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from src.utils.helpers import setup_logging
from web.api import api
from web.errors import ApiError, error_response
from web.extensions import limiter
from web.services import ApplicationServices

BASE = Path(__file__).parent
logger = logging.getLogger(__name__)


def create_app(
    services: ApplicationServices | None = None,
    test_config: dict[str, Any] | None = None,
) -> Flask:
    """Create a configured Flask application.

    ``services`` is injectable so route tests do not need an embedding model,
    a persisted vector database, or a live LLM.
    """

    setup_logging("INFO")
    app = Flask(
        __name__,
        template_folder=str(BASE / "templates"),
        static_folder=str(BASE / "static"),
        static_url_path="/static",
    )
    app.config.from_mapping(
        MAX_CONTENT_LENGTH=(
            config.MAX_UPLOAD_SIZE_MB * config.MAX_UPLOAD_FILES * 1024 * 1024
        ),
        JSON_SORT_KEYS=False,
    )
    if test_config:
        app.config.update(test_config)

    app.extensions["rag_services"] = services or ApplicationServices()
    limiter.init_app(app)
    app.register_blueprint(api)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.before_request
    def assign_request_id():
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        if (
            request.path.startswith("/api/")
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.headers.get("X-RAG-Client") != "web"
        ):
            raise ApiError(
                "This request is missing the application client header.",
                status_code=403,
                code="request_not_allowed",
            )

    @app.after_request
    def secure_response(response):
        response.headers["X-Request-ID"] = g.get("request_id", "")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), geolocation=(), microphone=(self)"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ApiError)
    def handle_api_error(exc: ApiError):
        return error_response(
            exc.message,
            status_code=exc.status_code,
            code=exc.code,
            details=exc.details,
        )

    @app.errorhandler(HTTPException)
    def handle_http_error(exc: HTTPException):
        if request.path.startswith("/api/"):
            return error_response(
                exc.description or exc.name,
                status_code=exc.code or 500,
                code=(exc.name or "http_error").lower().replace(" ", "_"),
            )
        return exc

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc: Exception):
        logger.exception(
            "Unhandled request error (request_id=%s)",
            g.get("request_id", "unknown"),
        )
        if request.path.startswith("/api/"):
            return error_response(
                "The request could not be completed.",
                status_code=500,
                code="internal_error",
            )
        raise exc

    return app


if __name__ == "__main__":
    application = create_app()
    print("\n  RAG Chatbot running at http://127.0.0.1:5000\n")
    application.run(debug=False, host="127.0.0.1", port=5000)
