"""One error shape for every response, as docs/API_DESIGN.md defines:

{"error": {"code": "...", "message": "...", "details": {...}}}
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

log = logging.getLogger(__name__)


class ApiError(Exception):
    """Raise anywhere in a request to answer with a documented error code."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}
        # Set by the session code when the browser's cookies are no longer any use.
        self.clear_session = False


def error_response(status: int, code: str, message: str, details: dict[str, Any] | None = None):
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return jsonify(error=body), status


# Codes and wording for errors Flask raises itself.
_HTTP_ERRORS: dict[int, tuple[str, str]] = {
    400: ("validation_error", "The request could not be understood."),
    401: ("not_authenticated", "Please log in to continue."),
    403: ("forbidden", "You don't have access to this."),
    404: ("not_found", "We couldn't find that."),
    405: ("method_not_allowed", "That action isn't supported here."),
    # The upload endpoints raise file_too_large / unsupported_file_type themselves;
    # these two are for ordinary requests.
    413: ("payload_too_large", "That request is too large."),
    415: ("unsupported_media_type", "Send the details as JSON."),
    429: ("rate_limited", "Too many requests. Please wait a moment and try again."),
}


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def _api_error(e: ApiError):
        response, status = error_response(e.status, e.code, e.message, e.details)
        response.headers.update(e.headers)
        if e.clear_session:
            from .sessions import clear_session_cookies

            clear_session_cookies(response)
        return response, status

    @app.errorhandler(HTTPException)
    def _http_error(e: HTTPException):
        status = e.code or 500
        code, message = _HTTP_ERRORS.get(status, ("error", e.description or "Something went wrong."))
        response, status = error_response(status, code, message)
        # Keep the headers that belong to the error: Allow on 405, Retry-After on 429 and 503.
        for name, value in e.get_headers():
            if name.lower() in ("allow", "retry-after"):
                response.headers[name] = value
        return response, status

    @app.errorhandler(Exception)
    def _unexpected(e: Exception):
        # The details go to the log (with the request id), never to the client.
        log.exception("unhandled error")
        return error_response(500, "internal_error", "Something went wrong on our side. Please try again.")
