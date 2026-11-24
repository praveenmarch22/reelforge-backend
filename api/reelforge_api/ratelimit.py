"""Rate limits kept in Redis (API design section 4).

A fixed window: the first request starts a window of `seconds`; once `limit` requests have
been counted in it, further ones are refused until the window ends.
"""

from __future__ import annotations

import hashlib
import logging

from flask import current_app, request

from .errors import ApiError

log = logging.getLogger(__name__)


def client_ip() -> str:
    return request.remote_addr or "unknown"


def _key(scope: str, subject: str) -> str:
    # Subjects can be emails; hashing keeps personal data out of Redis key names.
    digest = hashlib.sha256(subject.lower().encode("utf-8")).hexdigest()[:32]
    return f"rl:{scope}:{digest}"


def hit(scope: str, subject: str, limit: int, seconds: int) -> None:
    """Counts one request. Raises 429 rate_limited (with Retry-After) when over the limit."""
    if not current_app.extensions["settings"].rate_limits_enabled:
        return
    r = current_app.extensions["redis"]
    key = _key(scope, subject)
    try:
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = pipe.execute()
        if ttl < 0:  # new window (or a key left without an expiry)
            r.expire(key, seconds)
            ttl = seconds
    except Exception:  # noqa: BLE001
        # If Redis is down, requests are let through: a short gap in rate limiting is
        # better than nobody being able to log in. The health check reports Redis.
        log.warning("rate limit check failed", extra={"scope": scope}, exc_info=True)
        return
    if count > limit:
        raise ApiError(
            429,
            "rate_limited",
            "Too many attempts. Please wait a little and try again.",
            {"retryAfterSeconds": ttl},
            headers={"Retry-After": str(ttl)},
        )


def reset(scope: str, subject: str) -> None:
    try:
        current_app.extensions["redis"].delete(_key(scope, subject))
    except Exception:  # noqa: BLE001
        log.warning("rate limit reset failed", extra={"scope": scope}, exc_info=True)
