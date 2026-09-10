"""Flask application factory for the target app."""

from __future__ import annotations

import os

from flask import Flask, jsonify


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("MERIDIAN_SECRET_KEY", "dev-only-not-a-real-secret")

    @app.get("/health")
    def health():  # type: ignore[no-untyped-def]
        """Readiness probe. The test harness polls this before running a test."""
        return jsonify(status="ok", app="meridian-servicing-console")

    return app
