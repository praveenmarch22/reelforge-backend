"""Sign-up, login and password recovery (API design group A, A1 to A8)."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, make_response
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from .. import accounts, mail, ratelimit
from ..auth import current_session_id, current_user, require_user
from ..errors import ApiError
from ..extensions import db
from ..models import AuthToken, User
from ..schemas import (
    ForgotPasswordBody,
    LoginBody,
    ResetPasswordBody,
    SignupBody,
    TokenBody,
    read_body,
    user_json,
)
from ..security import hash_password, needs_rehash, verify_password
from ..sessions import (
    clear_session_cookies,
    revoke_all_sessions,
    revoke_family,
    rotate_session,
    signed_in_user_id,
    start_session,
)

bp = Blueprint("auth", __name__, url_prefix="/auth")
log = logging.getLogger(__name__)

HOUR = 3600
_TOKEN_INVALID = "This link has expired or was already used. Ask for a new one."  # noqa: S105


def _email_taken() -> ApiError:
    return ApiError(
        409, "email_taken", "An account with this email already exists. Log in or reset your password."
    )


def _no_content():
    return make_response("", 204)


@bp.post("/signup")
def signup():
    """A1. Creates the account, logs the browser in (unverified) and emails the verification link."""
    body = read_body(SignupBody)
    # Counted after the form is valid, so typing mistakes don't use up the three attempts.
    ratelimit.hit("signup-ip", ratelimit.client_ip(), limit=3, seconds=HOUR)
    if accounts.find_user_by_email(body.email) is not None:
        raise _email_taken()
    try:
        user = accounts.create_user(body.email, body.password, body.name)
    except IntegrityError:
        # Two sign-ups with one email at the same moment, or an account deleted in the last
        # 7 days that still holds the address.
        db.session.rollback()
        raise _email_taken() from None
    token = accounts.issue_token(user, accounts.VERIFY_EMAIL)
    response = make_response(jsonify(user=user_json(user)), 201)
    start_session(response, user)
    db.session.commit()
    mail.deliver(mail.verification_email(user.email, token))
    log.info("signup", extra={"user_id": str(user.id)})
    return response


@bp.post("/verify-email")
def verify_email():
    """A2. Opening the emailed link verifies the address.

    It also signs the browser in, unless somebody else is signed in there: a link must not
    be able to move a person into the account of whoever sent it to them.
    """
    ratelimit.hit("token-ip", ratelimit.client_ip(), limit=30, seconds=HOUR)
    body = read_body(TokenBody)
    user = accounts.use_token(body.token, accounts.VERIFY_EMAIL)
    if user is None:
        db.session.rollback()
        raise ApiError(410, "token_invalid", _TOKEN_INVALID)
    if user.disabled_at is not None:
        db.session.rollback()
        raise ApiError(403, "account_disabled", "This account has been disabled. Contact support.")
    if user.email_verified_at is None:
        user.email_verified_at = accounts.now()

    signed_in = signed_in_user_id()
    if signed_in is not None and signed_in != user.id:
        # Somebody else's browser: the address is verified, their session is left alone,
        # and the answer describes them, not the account the link belongs to.
        other = db.session.get(User, signed_in)
        db.session.commit()
        if other is not None and other.deleted_at is None and other.disabled_at is None:
            return jsonify(user=user_json(other), verifiedOtherAccount=True)
        # A deleted or disabled account's session is of no use to anyone (every other
        # endpoint refuses it), so the browser counts as signed out and the link signs in.
        signed_in = None

    response = make_response(jsonify(user=user_json(user)), 200)
    if signed_in is None:
        start_session(response, user)
    # Already signed in as this user: the session they have stays as it is.
    db.session.commit()
    return response


@bp.post("/resend-verification")
@require_user()
def resend_verification():
    """A3. Sends a fresh verification link to the logged-in user's address."""
    user = current_user()
    if user.email_verified_at is not None:
        raise ApiError(409, "already_verified", "Your email is already verified.")
    ratelimit.hit("resend-email", user.email, limit=3, seconds=HOUR)
    token = accounts.issue_token(user, accounts.VERIFY_EMAIL)
    db.session.commit()
    mail.deliver(mail.verification_email(user.email, token))
    return _no_content()


@bp.post("/login")
def login():
    """A4. The same answer for a wrong password and an unknown email."""
    body = read_body(LoginBody)
    email = body.email.strip().lower()
    ratelimit.hit("login-ip", ratelimit.client_ip(), limit=5, seconds=60)
    ratelimit.hit("login-email", email, limit=5, seconds=60)

    user = accounts.find_user_by_email(email)
    if not verify_password(user.password_hash if user else None, body.password):
        raise ApiError(401, "invalid_credentials", "That email or password isn't right.")
    if user.disabled_at is not None:
        raise ApiError(403, "account_disabled", "This account has been disabled. Contact support.")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    response = make_response(jsonify(user=user_json(user)), 200)
    start_session(response, user)
    db.session.commit()
    ratelimit.reset("login-email", email)
    return response


@bp.post("/refresh")
def refresh():
    """A5. Exchanges the refresh cookie for a new access and refresh pair."""
    response = make_response()
    user = rotate_session(response)
    db.session.commit()
    body = jsonify(user=user_json(user))
    response.set_data(body.get_data())
    response.mimetype = "application/json"
    return response


@bp.post("/logout")
@require_user()
def logout():
    """A6. Ends this browser's login."""
    revoke_family(current_session_id())
    db.session.commit()
    response = _no_content()
    clear_session_cookies(response)
    return response


@bp.post("/forgot-password")
def forgot_password():
    """A7. Always 204, so the answer never says whether an account exists."""
    body = read_body(ForgotPasswordBody)
    ratelimit.hit("forgot-ip", ratelimit.client_ip(), limit=10, seconds=HOUR)
    ratelimit.hit("forgot-email", body.email, limit=3, seconds=HOUR)
    # Looking the account up and sending happen in the background, so the request does the
    # same work, and takes the same time, whether or not the account exists.
    mail.request_password_reset(body.email)
    return _no_content()


@bp.post("/reset-password")
def reset_password():
    """A8. Sets a new password from an emailed link and ends every login of the account."""
    ratelimit.hit("token-ip", ratelimit.client_ip(), limit=30, seconds=HOUR)
    body = read_body(ResetPasswordBody)
    user = accounts.use_token(body.token, accounts.RESET_PASSWORD)
    if user is None:
        db.session.rollback()
        raise ApiError(410, "token_invalid", _TOKEN_INVALID)
    accounts.set_password(user, body.password)
    # A reset is what someone does when the account may be in the wrong hands, so an
    # unused verification link (which can sign a browser in) stops working too.
    db.session.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.used_at.is_(None))
        .values(used_at=accounts.now())
    )
    revoke_all_sessions(user.id)
    db.session.commit()
    ratelimit.reset("login-email", user.email)
    response = _no_content()
    clear_session_cookies(response)
    log.info("password reset", extra={"user_id": str(user.id)})
    return response
