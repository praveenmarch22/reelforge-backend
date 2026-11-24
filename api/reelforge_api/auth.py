"""Who is calling, and what they may do (API design: auth levels User, Verified, Admin)."""

from __future__ import annotations

import hmac
import uuid
from functools import wraps

from flask import g, request

from . import ratelimit
from .errors import ApiError
from .extensions import db
from .models import User
from .sessions import read_access_token

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def current_user() -> User:
    return g.user


def current_session_id() -> str:
    """The login (refresh-token family) this request belongs to."""
    return g.session_claims["sid"]


def _authenticate() -> User:
    claims = read_access_token()
    try:
        user = db.session.get(User, uuid.UUID(claims["sub"]))
    except ValueError:
        user = None
    if user is None or user.deleted_at is not None:
        error = ApiError(401, "not_authenticated", "Please log in to continue.")
        error.clear_session = True
        raise error
    if user.disabled_at is not None:
        raise ApiError(403, "account_disabled", "This account has been disabled. Contact support.")

    # Changes need the value from the rf_csrf cookie in a header. Another site can make the
    # browser send our cookies, but it can't read them, so it can't produce this header.
    if request.method not in _SAFE_METHODS:
        sent = request.headers.get("X-CSRF-Token", "")
        if not sent or not hmac.compare_digest(sent.encode(), str(claims.get("csrf", "")).encode()):
            raise ApiError(
                403, "csrf_failed", "Your session could not be confirmed. Reload the page and try again."
            )

    g.user = user
    g.session_claims = claims
    return user


def require_user(*, verified: bool = False, admin: bool = False):
    """Route decorator. `verified` = email must be verified; `admin` = is_admin."""

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            user = _authenticate()
            ratelimit.hit("user", str(user.id), limit=300, seconds=60)
            if verified and user.email_verified_at is None:
                raise ApiError(403, "email_not_verified", "Verify your email address to do this.")
            if admin and not user.is_admin:
                raise ApiError(403, "forbidden", "You don't have access to this.")
            return view(*args, **kwargs)

        return wrapper

    return decorator
