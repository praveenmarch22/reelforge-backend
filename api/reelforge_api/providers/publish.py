"""YouTube for publishing (milestone B6; docs/PUBLISHING_YOUTUBE.md). Plain HTTPS calls, no
Google client library. `MockYouTube` answers offline the same way (tests, and development
without keys): its sign-in sends the browser straight back with a made-up code.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an address, not a secret
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
CHUNK = 8 * 1024 * 1024  # a multiple of 256 KB, as resumable uploads require
TIMEOUT = (10, 120)


class PublishError(Exception):
    """A call to the platform failed. `code` is one of the post error codes."""

    def __init__(self, code: str, message: str = "", *, retryable: bool = False):
        super().__init__(message or code)
        self.code = code
        self.retryable = retryable


@dataclass
class Tokens:
    access_token: str
    expires_in: int
    refresh_token: str | None
    scopes: list[str]


@dataclass
class Channel:
    id: str
    title: str
    picture_url: str


def _error_reason(answer: requests.Response) -> str:
    try:
        body = answer.json()
    except ValueError:
        return ""
    error = body.get("error")
    if isinstance(error, dict):
        reasons = [e.get("reason", "") for e in error.get("errors", []) if isinstance(e, dict)]
        return (reasons[0] if reasons else "") or str(error.get("status", ""))
    return str(error or "")


def _raise_for(answer: requests.Response) -> None:
    if answer.ok:
        return
    reason = _error_reason(answer)
    # Our own OAuth client is wrong (a server setting), not the creator's connection (QB6-005).
    # Google answers 401 for it, so this comes before the 401 check (BUG-073).
    if reason in ("invalid_client", "unauthorized_client"):
        raise PublishError("upload_failed", f"the OAuth client was refused ({reason})")
    # The creator's permission is gone (revoked, expired in Testing mode): connect again.
    if answer.status_code == 401 or reason == "invalid_grant":
        raise PublishError("reconnect_required")
    if reason in ("quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"):
        raise PublishError("quota_exceeded")
    if reason in ("rateLimitExceeded", "userRateLimitExceeded") or answer.status_code == 429:
        raise PublishError("upload_failed", "YouTube asked to slow down", retryable=True)
    if reason in (
        "invalidTitle",
        "invalidDescription",
        "invalidTags",
        "invalidCategoryId",
        "invalidVideoMetadata",
    ):
        raise PublishError("invalid_metadata")
    if answer.status_code >= 500:
        raise PublishError("upload_failed", f"YouTube answered {answer.status_code}", retryable=True)
    raise PublishError("upload_failed", f"YouTube refused the request ({answer.status_code} {reason})")


CALL_TRIES = 3


def _retrying(call):
    """Retries one call on a 5xx, a rate limit or a dropped connection, with a short wait
    (technical design: calls are retried on 5xx and network errors; BUG-076)."""

    def wrapper(self, *args, **kwargs):
        for attempt in range(1, CALL_TRIES + 1):
            try:
                return call(self, *args, **kwargs)
            except PublishError as e:
                if not e.retryable or attempt == CALL_TRIES:
                    raise
            except requests.RequestException:
                if attempt == CALL_TRIES:
                    raise PublishError("upload_failed", "YouTube couldn't be reached") from None
            if self.wait:
                time.sleep(self.wait * 2**attempt)
        raise AssertionError("unreachable")

    return wrapper


class YouTube:
    def __init__(self, settings):
        self.client_id = settings.youtube_client_id
        self.client_secret = settings.youtube_client_secret
        self.redirect_uri = settings.youtube_redirect_uri
        self.wait = 0 if getattr(settings, "env", "test") == "test" else 1

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    # -------------------------------------------------------------- signing in

    def authorize_url(self, state: str) -> str:
        return (
            AUTH_URL
            + "?"
            + urlencode(
                {
                    "client_id": self.client_id,
                    "redirect_uri": self.redirect_uri,
                    "response_type": "code",
                    "scope": " ".join(SCOPES),
                    "access_type": "offline",  # a refresh token, so uploads work after an hour
                    "prompt": "consent",  # always ask, so Google always sends the refresh token
                    "include_granted_scopes": "true",
                    "state": state,
                }
            )
        )

    @_retrying
    def _token(self, data: dict) -> Tokens:
        answer = requests.post(
            TOKEN_URL,
            data={**data, "client_id": self.client_id, "client_secret": self.client_secret},
            timeout=TIMEOUT,
        )
        _raise_for(answer)
        body = answer.json()
        return Tokens(
            access_token=body["access_token"],
            expires_in=int(body.get("expires_in", 3600)),
            refresh_token=body.get("refresh_token"),
            scopes=str(body.get("scope", "")).split(),
        )

    def exchange(self, code: str) -> Tokens:
        return self._token(
            {"code": code, "grant_type": "authorization_code", "redirect_uri": self.redirect_uri}
        )

    def refresh(self, refresh_token: str) -> Tokens:
        return self._token({"refresh_token": refresh_token, "grant_type": "refresh_token"})

    def revoke(self, token: str) -> None:
        requests.post(REVOKE_URL, data={"token": token}, timeout=TIMEOUT)

    @_retrying
    def channel(self, access_token: str) -> Channel | None:
        answer = requests.get(
            f"{API}/channels",
            params={"part": "snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=TIMEOUT,
        )
        _raise_for(answer)
        items = answer.json().get("items") or []
        if not items:
            return None  # a Google account without a YouTube channel (YT-3)
        snippet = items[0].get("snippet", {})
        thumbs = snippet.get("thumbnails", {})
        picture = (thumbs.get("default") or thumbs.get("medium") or {}).get("url", "")
        return Channel(id=items[0]["id"], title=snippet.get("title", ""), picture_url=picture)

    # -------------------------------------------------------------- uploading

    @_retrying
    def start_upload(self, access_token: str, metadata: dict, size: int) -> str:
        """Opens a resumable upload; returns its address (kept on the post, YT-8)."""
        answer = requests.post(
            UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(size),
            },
            json=metadata,
            timeout=TIMEOUT,
        )
        _raise_for(answer)
        return answer.headers["Location"]

    def upload_chunk(self, session_url: str, chunk: bytes, start: int, total: int) -> tuple[int, str | None]:
        """Sends one piece. Returns (bytes YouTube now has, the video id once complete)."""
        end = start + len(chunk) - 1
        answer = requests.put(
            session_url,
            data=chunk,
            headers={"Content-Length": str(len(chunk)), "Content-Range": f"bytes {start}-{end}/{total}"},
            timeout=TIMEOUT,
        )
        if answer.status_code == 308:
            return _received(answer), None
        _raise_for(answer)
        return total, answer.json()["id"]

    def uploaded_so_far(self, session_url: str, total: int) -> tuple[int, str | None]:
        """After a dropped connection: how much YouTube has (YT-8)."""
        answer = requests.put(
            session_url, headers={"Content-Length": "0", "Content-Range": f"bytes */{total}"}, timeout=TIMEOUT
        )
        if answer.status_code == 308:
            return _received(answer), None
        if answer.status_code == 404:
            raise PublishError("upload_failed", "the upload expired")  # older than a week
        _raise_for(answer)
        return total, answer.json()["id"]

    @_retrying
    def video(self, access_token: str, video_id: str) -> dict[str, Any] | None:
        answer = requests.get(
            f"{API}/videos",
            params={"part": "status,processingDetails", "id": video_id},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=TIMEOUT,
        )
        _raise_for(answer)
        items = answer.json().get("items") or []
        return items[0] if items else None


def _received(answer: requests.Response) -> int:
    # "Range: bytes=0-1048575" means bytes up to 1048575 arrived; no header means none.
    value = answer.headers.get("Range", "")
    return int(value.rsplit("-", 1)[1]) + 1 if "-" in value else 0


class MockYouTube:
    """Offline stand-in. Knobs for tests: `fail_with` (a PublishError code for the next upload),
    `no_channel`, `processing` (the uploadStatus the video reports), `uploads` (what was sent)."""

    fail_with: str | None = None
    no_channel = False
    processing = "processed"
    uploads: list[dict] = []
    revoked: list[str] = []

    def __init__(self, settings):
        self.redirect_uri = settings.youtube_redirect_uri

    configured = True

    def authorize_url(self, state: str) -> str:
        return self.redirect_uri + "?" + urlencode({"code": "mock-code", "state": state})

    def exchange(self, code: str) -> Tokens:
        if code != "mock-code":
            raise PublishError("reconnect_required")
        return Tokens("mock-access", 3600, "mock-refresh", list(SCOPES))

    def refresh(self, refresh_token: str) -> Tokens:
        if refresh_token != "mock-refresh":  # noqa: S105 - the mock's made-up token
            raise PublishError("reconnect_required")
        return Tokens("mock-access", 3600, None, list(SCOPES))

    def revoke(self, token: str) -> None:
        MockYouTube.revoked.append(token)

    def channel(self, access_token: str) -> Channel | None:  # noqa: ARG002
        if MockYouTube.no_channel:
            return None
        return Channel(id="UCmockchannel", title="Mock Channel", picture_url="")

    def start_upload(self, access_token: str, metadata: dict, size: int) -> str:  # noqa: ARG002
        if MockYouTube.fail_with:
            code, MockYouTube.fail_with = MockYouTube.fail_with, None
            raise PublishError(code)
        MockYouTube.uploads.append({"metadata": metadata, "size": size, "received": 0})
        return f"mock://upload/{len(MockYouTube.uploads)}"

    def upload_chunk(self, session_url: str, chunk: bytes, start: int, total: int) -> tuple[int, str | None]:
        upload = MockYouTube.uploads[int(session_url.rsplit("/", 1)[1]) - 1]
        upload["received"] = start + len(chunk)
        done = upload["received"] >= total
        return upload["received"], (f"mockvid{len(MockYouTube.uploads)}" if done else None)

    def uploaded_so_far(self, session_url: str, total: int) -> tuple[int, str | None]:
        upload = MockYouTube.uploads[int(session_url.rsplit("/", 1)[1]) - 1]
        done = upload["received"] >= total
        return upload["received"], (f"mockvid{len(MockYouTube.uploads)}" if done else None)

    def video(self, access_token: str, video_id: str) -> dict[str, Any] | None:  # noqa: ARG002
        return {
            "id": video_id,
            "status": {"uploadStatus": MockYouTube.processing, "privacyStatus": "private"},
            "processingDetails": {"processingStatus": "succeeded"},
        }


def youtube(settings):
    return MockYouTube(settings) if "mock" in settings.publish_provider_names else YouTube(settings)


def pause(settings, seconds: float) -> None:
    if settings.env != "test":
        time.sleep(seconds)
