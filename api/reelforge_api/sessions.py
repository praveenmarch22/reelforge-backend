"""Login sessions: signed tokens in cookies (API design, Conventions: Auth).

Three cookies:
  rf_access   short-lived signed token (15 min), httpOnly. Proves who is calling.
  rf_refresh  long-lived signed token (30 days), httpOnly, sent only to /api/v1/auth.
              Exchanged for a new pair by POST /auth/refresh, and replaced every time.
  rf_csrf     random value readable by the web app, which sends it back in X-CSRF-Token.

Every refresh token is recorded in refresh_tokens. All tokens that descend from one login
share a family_id. Presenting a token that was already exchanged means it was copied, so
the whole family is revoked (reuse detection).
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from flask import Response, current_app, request
from sqlalchemy import select, update

from .errors import ApiError
from .extensions import db
from .models import RefreshToken, User

log = logging.getLogger(__name__)

ACCESS_COOKIE = "rf_access"
REFRESH_COOKIE = "rf_refresh"
CSRF_COOKIE = "rf_csrf"
REFRESH_PATH = "/api/v1/auth"
_ALGORITHM = "HS256"
# Two requests from the same browser can try to refresh at the same moment (two tabs).
# The loser presents a token revoked a moment ago; that is not theft.
_REUSE_GRACE = timedelta(seconds=10)


def _settings():
    return current_app.extensions["settings"]


def now() -> datetime:
    return datetime.now(UTC)


def _not_authenticated(clear: bool = True) -> ApiError:
    error = ApiError(401, "not_authenticated", "Please log in to continue.")
    error.clear_session = clear
    return error


# ---------------------------------------------------------------- tokens


def _encode(claims: dict[str, Any]) -> str:
    return jwt.encode(claims, _settings().secret_key, algorithm=_ALGORITHM)


def _decode(token: str, kind: str) -> dict[str, Any]:
    """Raises jwt.ExpiredSignatureError or jwt.InvalidTokenError."""
    claims = jwt.decode(
        token,
        _settings().secret_key,
        algorithms=[_ALGORITHM],
        options={"require": ["exp", "sub", "sid", "typ"]},
    )
    if claims.get("typ") != kind:
        raise jwt.InvalidTokenError("wrong token type")
    return claims


def read_access_token() -> dict[str, Any]:
    """The claims of the caller's access token, or the documented 401."""
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        # No access cookie but a CSRF cookie: nothing to refresh from here; plain "log in".
        raise _not_authenticated(clear=False)
    try:
        claims = _decode(token, "access")
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "token_expired", "Your session needs refreshing.") from None
    except jwt.InvalidTokenError:
        raise _not_authenticated() from None
    if _family_is_revoked(claims["sid"]):
        raise _not_authenticated()
    return claims


# ---------------------------------------------------------------- cookies


def _set_cookies(response: Response, access: str, refresh: str, csrf: str) -> None:
    s = _settings()
    lifetime = int(timedelta(days=s.refresh_token_days).total_seconds())
    common = {"max_age": lifetime, "secure": s.cookies_secure, "samesite": "Lax"}
    # The access cookie outlives the token inside it on purpose: an expired token is answered
    # with 401 token_expired, which tells the web app to refresh. A missing cookie could not.
    response.set_cookie(ACCESS_COOKIE, access, httponly=True, path="/", **common)
    response.set_cookie(REFRESH_COOKIE, refresh, httponly=True, path=REFRESH_PATH, **common)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, path="/", domain=s.cookie_domain or None, **common)


def clear_session_cookies(response: Response) -> None:
    s = _settings()
    common = {"secure": s.cookies_secure, "samesite": "Lax"}
    response.delete_cookie(ACCESS_COOKIE, path="/", httponly=True, **common)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH, httponly=True, **common)
    response.delete_cookie(CSRF_COOKIE, path="/", domain=s.cookie_domain or None, **common)


# ---------------------------------------------------------------- issuing and rotating


def _issue(response: Response, user: User, family_id: uuid.UUID) -> None:
    """Adds a new refresh token row for this family and sets all three cookies. Caller commits."""
    s = _settings()
    issued = now()
    refresh_expires = issued + timedelta(days=s.refresh_token_days)
    jti = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    db.session.add(
        RefreshToken(
            user_id=user.id,
            jti=jti,
            family_id=family_id,
            user_agent=(request.user_agent.string or "")[:300],
            ip_address=request.remote_addr,
            expires_at=refresh_expires,
        )
    )
    base = {"sub": str(user.id), "sid": str(family_id), "iat": issued}
    access = _encode(
        {**base, "typ": "access", "csrf": csrf, "exp": issued + timedelta(minutes=s.access_token_minutes)}
    )
    refresh = _encode({**base, "typ": "refresh", "jti": jti, "exp": refresh_expires})
    _set_cookies(response, access, refresh, csrf)


def start_session(response: Response, user: User) -> None:
    """A new login on this browser. Any session the browser already had is ended first."""
    end_current_session()
    user.last_login_at = now()
    _issue(response, user, uuid.uuid4())


def rotate_session(response: Response) -> User:
    """POST /auth/refresh: exchanges the refresh cookie for a new pair. Caller commits."""
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise _not_authenticated()
    try:
        claims = _decode(token, "refresh")
    except jwt.InvalidTokenError:  # includes expired
        raise _not_authenticated() from None

    row = db.session.execute(
        select(RefreshToken).where(RefreshToken.jti == claims.get("jti", "")).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.expires_at <= now():
        raise _not_authenticated()
    if row.revoked_at is not None:
        if now() - row.revoked_at <= _REUSE_GRACE:
            # Lost a race with another tab of the same browser, which now holds the new
            # cookies. They must be left alone: clearing them here would log the browser out.
            raise _not_authenticated(clear=False)
        still_active = db.session.execute(
            select(RefreshToken.id)
            .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
            .limit(1)
        ).first()
        if still_active:
            # An already-exchanged token is presented while its replacement is in use:
            # one of the two holders is not the user. End the whole login.
            log.warning(
                "refresh token reuse", extra={"user_id": str(row.user_id), "family": str(row.family_id)}
            )
            revoke_family(row.family_id)
            db.session.commit()
        # Otherwise the login was simply ended earlier (logout, password change).
        raise _not_authenticated()

    user = db.session.get(User, row.user_id)
    if user is None or user.deleted_at is not None:
        raise _not_authenticated()
    if user.disabled_at is not None:
        revoke_family(row.family_id)
        db.session.commit()
        error = ApiError(403, "account_disabled", "This account has been disabled. Contact support.")
        error.clear_session = True
        raise error

    row.revoked_at = now()
    _issue(response, user, row.family_id)
    return user


# ---------------------------------------------------------------- revoking


def _revoked_key(family_id: uuid.UUID | str) -> str:
    return f"revoked_session:{family_id}"


def _family_is_revoked(family_id: str) -> bool:
    try:
        return bool(current_app.extensions["redis"].exists(_revoked_key(family_id)))
    except Exception:  # noqa: BLE001
        # Redis down: access tokens stay valid for their remaining minutes (at most 15).
        log.warning("revoked-session check failed", exc_info=True)
        return False


def _remember_revoked(family_ids: list[uuid.UUID]) -> None:
    """Access tokens can't be recalled, so revoked logins are noted until those tokens expire."""
    seconds = _settings().access_token_minutes * 60 + 60
    try:
        pipe = current_app.extensions["redis"].pipeline()
        for family_id in family_ids:
            pipe.set(_revoked_key(family_id), "1", ex=seconds)
        pipe.execute()
    except Exception:  # noqa: BLE001
        log.warning("could not record revoked sessions", exc_info=True)


def revoke_family(family_id: uuid.UUID | str) -> None:
    """Ends one login (this browser). Caller commits."""
    family_id = uuid.UUID(str(family_id))
    db.session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now())
    )
    _remember_revoked([family_id])


def revoke_all_sessions(user_id: uuid.UUID, keep_family: uuid.UUID | str | None = None) -> None:
    """Ends every login of a user, optionally keeping the current one. Caller commits."""
    keep = uuid.UUID(str(keep_family)) if keep_family else None
    active = RefreshToken.revoked_at.is_(None) & (RefreshToken.user_id == user_id)
    if keep:
        active = active & (RefreshToken.family_id != keep)
    families = list(db.session.execute(select(RefreshToken.family_id).where(active).distinct()).scalars())
    if families:
        db.session.execute(update(RefreshToken).where(active).values(revoked_at=now()))
        _remember_revoked(families)


def end_current_session() -> None:
    """Revokes whatever login the request's cookies belong to, if any. Never raises."""
    for cookie, kind in ((REFRESH_COOKIE, "refresh"), (ACCESS_COOKIE, "access")):
        token = request.cookies.get(cookie)
        if not token:
            continue
        try:
            # An expired access token still names its login correctly.
            claims = jwt.decode(
                token, _settings().secret_key, algorithms=[_ALGORITHM], options={"verify_exp": False}
            )
        except jwt.InvalidTokenError:
            continue
        if claims.get("typ") == kind and claims.get("sid"):
            try:
                revoke_family(claims["sid"])
            except ValueError:
                continue
            return
