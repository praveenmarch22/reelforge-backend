"""JSON logs, one object per line, each carrying the request id."""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from datetime import UTC, datetime

from flask import Flask, g, has_request_context, request

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if has_request_context():
            entry["request_id"] = getattr(g, "request_id", None)
        # Anything passed as logging's extra={...} becomes its own field.
        entry.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD})
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(app: Flask) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    app.logger.handlers = []
    app.logger.propagate = True
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def register_request_id(app: Flask) -> None:
    """Every request gets an id, returned in X-Request-ID and written on every log line."""

    @app.before_request
    def _assign():
        supplied = request.headers.get("X-Request-ID", "")
        # Accept a caller's id only if it is plain; it ends up in logs and a response header.
        g.request_id = supplied if _SAFE_REQUEST_ID.match(supplied) else uuid.uuid4().hex

    @app.after_request
    def _expose(response):
        response.headers["X-Request-ID"] = g.get("request_id", "")
        return response
