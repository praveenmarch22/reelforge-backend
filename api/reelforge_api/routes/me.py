"""The logged-in user's own profile (API design group A, A9 to A12)."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, make_response

from .. import accounts, ratelimit
from ..auth import current_session_id, current_user, require_user
from ..errors import ApiError
from ..extensions import db
from ..schemas import ChangePasswordBody, DeleteMeBody, UpdateMeBody, read_body, user_json
from ..security import verify_password
from ..sessions import clear_session_cookies, revoke_all_sessions

bp = Blueprint("me", __name__, url_prefix="/me")
log = logging.getLogger(__name__)


def _check_password(password: str) -> None:
    """Confirms the current password. Limited, so a borrowed session can't be used to guess it."""
    user = current_user()
    ratelimit.hit("password-check", str(user.id), limit=5, seconds=60)
    if not verify_password(user.password_hash, password):
        raise ApiError(401, "invalid_credentials", "That password isn't right.")


@bp.get("")
@require_user()
def get_me():
    """A9."""
    return jsonify(user=user_json(current_user()))


@bp.patch("")
@require_user()
def update_me():
    """A10. Only the fields that are sent change."""
    body = read_body(UpdateMeBody)
    user = current_user()
    if body.name is not None:
        user.name = body.name
    if body.niche is not None:
        user.niche = body.niche
    if body.defaults is not None:
        if body.defaults.voice_id is not None:
            user.default_voice_id = body.defaults.voice_id
        if body.defaults.caption_style is not None:
            user.default_caption_style = body.defaults.caption_style
        if body.defaults.mode is not None:
            user.default_mode = body.defaults.mode
    if body.onboarding_completed and user.onboarding_completed_at is None:
        user.onboarding_completed_at = accounts.now()
    db.session.commit()
    return jsonify(user=user_json(user))


@bp.post("/password")
@require_user()
def change_password():
    """A11. Other browsers are logged out; this one stays logged in."""
    body = read_body(ChangePasswordBody)
    _check_password(body.current_password)
    user = current_user()
    accounts.set_password(user, body.new_password)
    revoke_all_sessions(user.id, keep_family=current_session_id())
    db.session.commit()
    log.info("password changed", extra={"user_id": str(user.id)})
    return make_response("", 204)


@bp.delete("")
@require_user()
def delete_me():
    """A12. The account stops working now; its data is removed for good after 7 days."""
    body = read_body(DeleteMeBody)
    _check_password(body.password)
    user = current_user()
    accounts.soft_delete(user)
    db.session.commit()
    log.info("account deleted", extra={"user_id": str(user.id)})
    response = make_response("", 204)
    clear_session_cookies(response)
    return response
