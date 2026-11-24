"""JSON logs, one object per line, each carrying the request id."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from datetime import UTC, datetime

from flask import Flask, g, has_request_context, request

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_request_log = logging.getLogger("reelforge_api.request")
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
        # Celery attaches a task's arguments and result to its own log lines. Those can hold
        # an email with its sign-in link, so only the task's id and name are kept.
        task = entry.get("data")
        if isinstance(task, dict) and "args" in task:
            entry["data"] = {k: v for k, v in task.items() if k not in ("args", "kwargs", "return_value")}
            if "return_value" in task:
                entry["message"] = f"Task {task.get('name')}[{task.get('id')}] succeeded"
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def use_json_logs() -> None:
    """Everything this process logs goes to standard output as JSON lines."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def configure_logging(app: Flask) -> None:
    use_json_logs()
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
        g.request_started = time.perf_counter()

    @app.after_request
    def _expose(response):
        response.headers["X-Request-ID"] = g.get("request_id", "")
        # One line per request. The path is logged without its query string, which can
        # carry personal data. Health checks arrive every few seconds and are left out.
        if not request.path.endswith("/health"):
            started = g.get("request_started")
            user = g.get("user")
            _request_log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1) if started else None,
                    "user_id": str(user.id) if user is not None else None,
                },
            )
        return response
