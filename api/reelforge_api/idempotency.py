"""The Idempotency-Key header (API design 1), for endpoints marked **idempotent**.

The web app sends a fresh key with each click. If the request is sent again with the same key
(a retry after a dropped connection), the first answer is returned instead of starting the
work twice. The same key with a different body is refused with 409 idempotency_conflict.
Only successful answers are kept, for 24 hours; a refused request can be retried with its key.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from functools import wraps

from flask import current_app, request

from .auth import current_user
from .errors import ApiError

HEADER = "Idempotency-Key"
KEEP_SECONDS = 24 * 3600
PENDING_SECONDS = 60
_VALID = re.compile(r"[A-Za-z0-9_\-:.]{1,100}")


def idempotent(view: Callable) -> Callable:
    """Wrap a view that already requires a signed-in user (put it under @require_user)."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        key = request.headers.get(HEADER)
        if key is None:
            return view(*args, **kwargs)
        if not _VALID.fullmatch(key):
            raise ApiError(
                400, "validation_error", "Some details need another look.",
                {"fields": {HEADER: "Use up to 100 letters, digits, dashes, underscores, colons or dots"}},
            )  # fmt: skip
        redis = current_app.extensions["redis"]
        slot = f"idem:{current_user().id}:{request.method}:{request.path}:{key}"
        body_hash = hashlib.sha256(request.get_data()).hexdigest()

        if not redis.set(slot, json.dumps({"hash": body_hash}), nx=True, ex=PENDING_SECONDS):
            saved = json.loads(redis.get(slot) or "{}")
            if saved.get("hash") not in (None, body_hash):
                raise ApiError(
                    409, "idempotency_conflict", "This request key was already used for a different request."
                )
            if "status" not in saved:
                raise ApiError(409, "already_running", "This is already in progress.")
            return current_app.response_class(
                saved["body"], status=saved["status"], mimetype="application/json"
            )

        try:
            answer = current_app.make_response(view(*args, **kwargs))
        except BaseException:
            redis.delete(slot)
            raise
        if 200 <= answer.status_code < 300 and answer.mimetype == "application/json":
            saved = {"hash": body_hash, "status": answer.status_code, "body": answer.get_data(as_text=True)}
            redis.set(slot, json.dumps(saved), ex=KEEP_SECONDS)
        else:
            redis.delete(slot)
        return answer

    return wrapper
