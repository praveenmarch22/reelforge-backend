"""Connecting a publishing account (milestone B6; API F1 to F3, YouTube first).

F1 hands the web app Google's sign-in address. Google sends the browser back to F2 with a
code, which is exchanged for tokens; the channel is read and the tokens are kept encrypted.
The `state` in between is random, used once, valid 10 minutes and tied to the signed-in user
and the page to come back to, so another person's sign-in link can never connect an account
to this user (or this user's account to theirs).
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
import secrets
from datetime import timedelta
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from flask import current_app
from sqlalchemy import select, update

from . import crypto
from .errors import ApiError
from .extensions import db
from .models import User, YoutubeAccount
from .providers.publish import PublishError, youtube
from .sessions import now

log = logging.getLogger(__name__)

UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
PLATFORMS = ("youtube", "instagram")
AVAILABLE = ("youtube",)  # Instagram arrives with B6b
STATE_SECONDS = 600
DEFAULT_RETURN = "/app/settings"
RETURN_MAX = 300
_OUR_PARAMS = {"connected", "connection_error", "platform", "reason", "instagram"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f\\]")


def check_platform(platform: str) -> None:
    if platform not in PLATFORMS:
        raise ApiError(404, "not_found", "We couldn't find that.")
    if platform not in AVAILABLE:
        raise ApiError(404, "not_available", "Publishing to Instagram is coming soon.")


def platform_enabled(platform: str) -> bool:
    """The admin's kill switch for a publishing platform (I9, QB8-003)."""
    from .models import ProviderSwitch

    switch = db.session.get(ProviderSwitch, platform)
    return switch is None or switch.enabled


def safe_return_to(value: str | None) -> str:
    """The `returnTo` rule (API design F1): a path on the web app itself, or the default."""
    if not value or len(value) > RETURN_MAX or not value.startswith("/") or value.startswith("//"):
        return DEFAULT_RETURN
    if _CONTROL.search(value) or _CONTROL.search(unquote(value)):
        return DEFAULT_RETURN
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return DEFAULT_RETURN
    path = posixpath.normpath(unquote(parts.path))
    if not path.startswith("/") or path.startswith("//"):
        return DEFAULT_RETURN
    # Encoded again, so a decoded "?" or "#" stays part of the path and can never add our own
    # result parameters to the address (BUG-077).
    path = quote(path, safe="/-_.~!$&'()*+,;=:@")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in _OUR_PARAMS]
    return path + ("?" + urlencode(query) if query else "")


def with_result(return_to: str, **params: str) -> str:
    parts = urlsplit(return_to)
    query = parse_qsl(parts.query, keep_blank_values=True) + list(params.items())
    return parts.path + "?" + urlencode(query)


def _redis():
    return current_app.extensions["redis"]


# ---------------------------------------------------------------- F1


def connect_url(user: User, platform: str, return_to: str | None) -> str:
    check_platform(platform)
    client = youtube(current_app.extensions["settings"])
    if not client.configured:
        raise ApiError(503, "provider_unavailable", "Connecting YouTube isn't set up yet.")
    state = secrets.token_urlsafe(32)
    _redis().set(
        f"oauth:state:{state}",
        json.dumps({"user": str(user.id), "platform": platform, "returnTo": safe_return_to(return_to)}),
        ex=STATE_SECONDS,
    )
    return client.authorize_url(state)


# ---------------------------------------------------------------- F2


def _take_state(state: str | None) -> dict | None:
    if not state or len(state) > 200:
        return None
    saved = _redis().getdel(f"oauth:state:{state}")  # used once
    return json.loads(saved) if saved else None


def callback(platform: str, args, signed_in: str | None) -> str:
    """F2. Returns where to send the browser (always the web app, never an error page)."""
    failed = with_result(DEFAULT_RETURN, connection_error=platform, reason="failed")
    if platform not in AVAILABLE:
        return failed
    saved = _take_state(args.get("state"))
    if saved is None or saved["platform"] != platform or saved["user"] != signed_in:
        return failed
    back = saved["returnTo"]

    def error(reason: str) -> str:
        return with_result(back, connection_error=platform, reason=reason)

    if args.get("error"):
        # Google: access_denied when the creator said no. Testing-mode apps refuse accounts
        # that aren't test users before the consent screen; that also arrives as access_denied.
        return error("denied")
    code = args.get("code")
    if not code:
        return error("failed")  # back to the page the creator started from (BUG-078)
    user = db.session.get(User, saved["user"])
    if user is None or user.deleted_at is not None:
        return failed
    client = youtube(current_app.extensions["settings"])
    try:
        tokens = client.exchange(code)
        channel = client.channel(tokens.access_token)
    except (PublishError, ValueError, KeyError) as e:
        log.warning("connecting YouTube failed", extra={"user_id": saved["user"], "error": str(e)})
        return error("failed")
    except Exception:  # noqa: BLE001 - network and the like: never a stack trace in the browser
        log.exception("connecting YouTube failed", extra={"user_id": saved["user"]})
        return error("failed")
    if channel is None:
        return error("no_channel")  # YT-3: nothing is stored
    if UPLOAD_SCOPE not in tokens.scopes:
        # The creator unticked "Manage your YouTube videos" on Google's screen (QB6-003).
        _revoke(client, tokens.refresh_token or tokens.access_token)
        return error("missing_permission")
    if not tokens.refresh_token:
        return error("failed")
    account = db.session.query(YoutubeAccount).filter_by(user_id=user.id).with_for_update().one_or_none()
    old_token = crypto.decrypt(account.refresh_token_enc) if account is not None else None
    if account is None:
        account = YoutubeAccount(user_id=user.id)
        db.session.add(account)
    account.channel_id, account.channel_title, account.picture_url = (
        channel.id,
        channel.title,
        channel.picture_url,
    )
    account.access_token_enc = crypto.encrypt(tokens.access_token)
    account.refresh_token_enc = crypto.encrypt(tokens.refresh_token)
    account.token_expires_at = now() + timedelta(seconds=tokens.expires_in)
    account.scopes = tokens.scopes
    account.status = "connected"
    db.session.commit()
    if old_token and old_token != tokens.refresh_token:
        _revoke(client, old_token)  # the earlier connection's token is no longer used (QB6-007)
    return with_result(back, connected=platform)


def _revoke(client, token: str | None) -> None:
    if not token:
        return
    try:
        client.revoke(token)
    except Exception:  # noqa: BLE001 - best effort; the token is deleted here either way
        log.warning("could not revoke a YouTube token", exc_info=True)


# ---------------------------------------------------------------- F3


def disconnect(user: User, platform: str) -> None:
    """F3. The tokens are revoked at Google (best effort) and deleted; posts are kept (YT-12)."""
    check_platform(platform)
    account = db.session.query(YoutubeAccount).filter_by(user_id=user.id).with_for_update().one_or_none()
    if account is None:
        return
    token = crypto.decrypt(account.refresh_token_enc)
    db.session.delete(account)
    db.session.commit()
    _revoke(youtube(current_app.extensions["settings"]), token)


def forget_user(user_id) -> None:
    """Account deletion: the YouTube token is revoked at Google and deleted (QB6-007).
    Caller commits."""
    account = db.session.query(YoutubeAccount).filter_by(user_id=user_id).one_or_none()
    if account is None:
        return
    token = crypto.decrypt(account.refresh_token_enc)
    db.session.delete(account)
    _revoke(youtube(current_app.extensions["settings"]), token)


def mark_reconnect(account_id) -> None:
    """YouTube refused the token while publishing: the creator must connect again (QB6-005)."""
    db.session.rollback()
    db.session.execute(
        update(YoutubeAccount).where(YoutubeAccount.id == account_id).values(status="reconnect_required")
    )
    db.session.commit()


def check_connections() -> int:
    """Daily: asks Google for a fresh token for each connection, so one that was revoked or
    expired (Testing mode: after 7 days) shows "reconnect" before the creator tries to publish
    (QB6-008). Returns how many need reconnecting."""
    ids = list(
        db.session.execute(select(YoutubeAccount.id).where(YoutubeAccount.status == "connected")).scalars()
    )
    db.session.commit()
    lost = 0
    client = youtube(current_app.extensions["settings"])
    for account_id in ids:
        account = db.session.get(YoutubeAccount, account_id)
        token = crypto.decrypt(account.refresh_token_enc) if account else None
        try:
            if not token:
                raise PublishError("reconnect_required")
            tokens = client.refresh(token)
            account.access_token_enc = crypto.encrypt(tokens.access_token)
            account.token_expires_at = now() + timedelta(seconds=tokens.expires_in)
            db.session.commit()
        except PublishError as e:
            db.session.rollback()
            if e.code == "reconnect_required":
                mark_reconnect(account_id)
                lost += 1
        except Exception:  # noqa: BLE001 - Google unreachable: try again tomorrow
            db.session.rollback()
            log.warning("could not check a YouTube connection", exc_info=True)
    return lost


# ---------------------------------------------------------------- tokens for uploads


def access_token(account_id) -> str:
    """A valid access token for this account, refreshed when it is about to expire (YT-4).
    Marks the account `reconnect_required` and raises if Google refuses (YT-6)."""
    account = db.session.get(YoutubeAccount, account_id)
    if account is None or account.status != "connected":
        raise PublishError("reconnect_required")
    if account.token_expires_at - now() > timedelta(minutes=5):
        token = crypto.decrypt(account.access_token_enc)
        if token:
            db.session.commit()
            return token
    refresh = crypto.decrypt(account.refresh_token_enc)
    try:
        if not refresh:
            raise PublishError("reconnect_required")
        tokens = youtube(current_app.extensions["settings"]).refresh(refresh)
    except PublishError as e:
        if e.code == "reconnect_required":
            account.status = "reconnect_required"
            db.session.commit()
        raise
    account.access_token_enc = crypto.encrypt(tokens.access_token)
    account.token_expires_at = now() + timedelta(seconds=tokens.expires_in)
    db.session.commit()
    return tokens.access_token


def connection_json(account: YoutubeAccount | None) -> dict | None:
    if account is None:
        return None
    return {
        "status": account.status,
        "name": account.channel_title,
        "pictureUrl": account.picture_url or None,
        "connectedAt": account.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if account.created_at else None,
    }
