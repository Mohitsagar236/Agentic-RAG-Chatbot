"""WSGI entry point for production process managers."""

from web.app import create_app


app = create_app()
