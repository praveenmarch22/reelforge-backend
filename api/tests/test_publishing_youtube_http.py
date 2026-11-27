"""The real YouTube client (milestone B6) with `requests` replaced by a fake Google, so nothing
leaves the machine: the sign-in address, the token and channel calls, the resumable upload
(headers, Content-Range, the 308 Range answer), the processing check, revoking, and how
Google's errors are mapped. Also the production settings and the database rule of one live
post per render and platform."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from reelforge_api import crypto
from reelforge_api.config import DEV_TOKEN_KEY, Settings
from reelforge_api.providers import publish as publish_module
from reelforge_api.providers.publish import (
    API,
    AUTH_URL,
    REVOKE_URL,
    SCOPES,
    TOKEN_URL,
    UPLOAD_URL,
    PublishError,
    YouTube,
    _raise_for,
    _received,
)

from .conftest import build_app
from .projects_support import get_project, run_jobs
from .publish_support import (
    BODY,
    RETURN_TO,
    callback,
    connection,
    get_post,
    published,
    rendered_project,
    result_of,
)
from .render_support import reset_mock as reset_render_mock
from .support import sign_up
from .test_renders_rules import PRODUCTION_SETTINGS
from .voice_support import no_real_voice_services  # noqa: F401 - autouse fixture

CLIENT_ID = "123-test.apps.googleusercontent.com"
CLIENT_SECRET = "test-client-secret"  # noqa: S105 - made up
REDIRECT = "http://localhost:5173/api/v1/connections/youtube/callback"
SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=session-1"


def answer(status: int, body=None, headers: dict | None = None) -> requests.Response:
    res = requests.Response()
    res.status_code = status
    res._content = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    res.headers.update(headers or {})
    res.encoding = "utf-8"
    return res


class FakeGoogle:
    """Answers like Google and YouTube, and records every request. Tests change `routes` or
    put one-off answers in `next` to make a call fail."""

    def __init__(self):
        self.calls: list[dict] = []
        self.received = 0
        self.video_privacy = "public"
        self.next: dict[str, list] = {}  # call name -> answers (or exceptions) to use first
        self.partial: int | None = None  # bytes to accept of the next piece

    # -- the recorded calls

    def named(self, name: str) -> list[dict]:
        return [c for c in self.calls if c["name"] == name]

    # -- requests' interface

    def post(self, url, **kw):
        if url == TOKEN_URL:
            return self._call("token-" + kw["data"]["grant_type"], url, "POST", kw)
        if url == REVOKE_URL:
            return self._call("revoke", url, "POST", kw)
        if url == UPLOAD_URL:
            return self._call("start", url, "POST", kw)
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url, **kw):
        if url == f"{API}/channels":
            return self._call("channels", url, "GET", kw)
        if url == f"{API}/videos":
            return self._call("videos", url, "GET", kw)
        raise AssertionError(f"unexpected GET {url}")

    def put(self, url, **kw):
        assert url == SESSION, url
        name = "status" if kw["headers"]["Content-Range"].startswith("bytes */") else "chunk"
        return self._call(name, url, "PUT", kw)

    def _call(self, name, url, method, kw):
        self.calls.append({"name": name, "url": url, "method": method, **kw})
        queued = self.next.get(name)
        if queued:
            first = queued.pop(0)
            if isinstance(first, BaseException):
                raise first
            return first
        return getattr(self, "_" + name.replace("-", "_"))(kw)

    # -- the default, successful answers

    def _token_authorization_code(self, kw):
        return answer(
            200,
            {
                "access_token": "at-1",
                "expires_in": 3599,
                "refresh_token": "rt-1",
                "scope": " ".join(SCOPES),
                "token_type": "Bearer",
            },
        )

    def _token_refresh_token(self, kw):
        return answer(200, {"access_token": "at-2", "expires_in": 3599, "scope": " ".join(SCOPES)})

    def _revoke(self, kw):
        return answer(200, {})

    def _channels(self, kw):
        return answer(
            200,
            {
                "items": [
                    {
                        "id": "UCreal",
                        "snippet": {
                            "title": "Real Channel",
                            "thumbnails": {"default": {"url": "https://yt3.example/d.jpg"}},
                        },
                    }
                ]
            },
        )

    def _start(self, kw):
        self.received = 0
        return answer(200, b"", {"Location": SESSION})

    def _chunk(self, kw):
        start, end, total = map(
            int, re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", kw["headers"]["Content-Range"]).groups()
        )
        assert start == self.received, f"piece starts at {start}, YouTube has {self.received}"
        assert len(kw["data"]) == end - start + 1 == int(kw["headers"]["Content-Length"])
        took = end - start + 1 if self.partial is None else self.partial
        self.partial = None
        self.received = start + took
        if self.received < total:
            return answer(308, b"", {"Range": f"bytes=0-{self.received - 1}"})
        return answer(200, {"id": "vid123", "status": {"uploadStatus": "uploaded"}})

    def _status(self, kw):
        total = int(kw["headers"]["Content-Range"].rsplit("/", 1)[1])
        if self.received >= total:
            return answer(200, {"id": "vid123"})
        return answer(308, b"", {"Range": f"bytes=0-{self.received - 1}"} if self.received else {})

    def _videos(self, kw):
        return answer(
            200,
            {
                "items": [
                    {
                        "id": kw["params"]["id"],
                        "status": {"uploadStatus": "processed", "privacyStatus": self.video_privacy},
                        "processingDetails": {"processingStatus": "succeeded"},
                    }
                ]
            },
        )


@pytest.fixture
def google(monkeypatch) -> FakeGoogle:
    fake = FakeGoogle()
    monkeypatch.setattr(
        publish_module, "requests", SimpleNamespace(post=fake.post, get=fake.get, put=fake.put)
    )
    return fake


@pytest.fixture(scope="module")
def real_app(app):
    """The API with the real YouTube client (its HTTP calls are answered by FakeGoogle)."""
    return build_app(
        publish_providers="youtube",
        youtube_client_id=CLIENT_ID,
        youtube_client_secret=CLIENT_SECRET,
        youtube_redirect_uri=REDIRECT,
    )


@pytest.fixture(autouse=True)
def fresh_render_mock():
    reset_render_mock()
    yield
    reset_render_mock()


def client_settings(**changes):
    return SimpleNamespace(
        youtube_client_id=CLIENT_ID,
        youtube_client_secret=CLIENT_SECRET,
        youtube_redirect_uri=REDIRECT,
        **changes,
    )


def connect_real(real_app, browser, google) -> str:
    res = browser.get("/connections/youtube/connect", query_string={"returnTo": RETURN_TO})
    assert res.status_code == 200, res.get_data(as_text=True)
    state = parse_qs(urlsplit(res.get_json()["authorizeUrl"]).query)["state"][0]
    back = callback(browser, state=state, code="4/real-looking-code")
    assert back.status_code == 302
    return back.headers["Location"]


# ---------------------------------------------------------------- signing in


def test_the_authorize_url_asks_google_for_offline_upload_access(real_app, google):
    account = sign_up(real_app)
    res = account.browser.get("/connections/youtube/connect")
    assert res.status_code == 200
    url = urlsplit(res.get_json()["authorizeUrl"])
    assert (
        f"{url.scheme}://{url.netloc}{url.path}" == AUTH_URL == "https://accounts.google.com/o/oauth2/v2/auth"
    )
    params = parse_qs(url.query)
    assert {k: v for k, v in params.items() if k != "state"} == {
        "client_id": [CLIENT_ID],
        "redirect_uri": [REDIRECT],
        "response_type": ["code"],
        "scope": [
            "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
        ],
        "access_type": ["offline"],
        "prompt": ["consent"],
        "include_granted_scopes": ["true"],
    }
    assert len(params["state"][0]) >= 32
    assert google.calls == []  # nothing is sent to Google before the creator signs in


def test_the_callback_exchanges_the_code_and_reads_the_channel(real_app, google):
    account = sign_up(real_app)
    location = connect_real(real_app, account.browser, google)
    assert result_of(location) == (RETURN_TO, {"connected": ["youtube"]})

    [token] = google.named("token-authorization_code")
    assert token["data"] == {
        "code": "4/real-looking-code",
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    assert token.get("timeout")
    [channels] = google.named("channels")
    assert channels["params"] == {"part": "snippet", "mine": "true"}
    assert channels["headers"] == {"Authorization": "Bearer at-1"}
    assert channels.get("timeout")
    youtube = connection(account.browser)
    assert (youtube["name"], youtube["pictureUrl"], youtube["status"]) == (
        "Real Channel",
        "https://yt3.example/d.jpg",
        "connected",
    )


@pytest.mark.parametrize(
    ("name", "bad", "reason"),
    [
        (
            "token-authorization_code",
            answer(400, {"error": "invalid_grant", "error_description": "Bad Request"}),
            "failed",
        ),
        ("token-authorization_code", answer(500, b"<html>oops</html>"), "failed"),
        ("token-authorization_code", requests.ConnectionError("no route"), "failed"),
        ("token-authorization_code", answer(200, {"token_type": "Bearer"}), "failed"),  # no access_token
        ("token-authorization_code", answer(200, b"not json"), "failed"),
        ("channels", answer(401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}}), "failed"),
        ("channels", answer(200, {"items": []}), "no_channel"),
        ("channels", answer(200, {}), "no_channel"),
        # The creator unticked the upload permission on Google's screen.
        (
            "token-authorization_code",
            answer(
                200, {"access_token": "at-1", "expires_in": 3599, "refresh_token": "rt-1", "scope": SCOPES[1]}
            ),
            "missing_permission",
        ),
    ],
)
def test_the_callback_when_google_answers_badly(real_app, google, name, bad, reason):
    """Updated for BUG-076 (retries on 5xx and network errors)."""
    account = sign_up(real_app)
    # A 5xx or a dropped connection is tried 3 times before it counts (BUG-076), so those
    # answers come back on every try.
    retried = isinstance(bad, Exception) or getattr(bad, "status_code", 0) >= 500
    google.next[name] = [bad] * (3 if retried else 1)
    location = connect_real(real_app, account.browser, google)
    path, query = result_of(location)
    assert path == RETURN_TO and query == {"connection_error": ["youtube"], "reason": [reason]}
    assert connection(account.browser) is None


def test_disconnecting_revokes_the_refresh_token_at_google(real_app, google):
    account = sign_up(real_app)
    connect_real(real_app, account.browser, google)
    assert account.browser.delete("/connections/youtube").status_code == 204
    [revoke] = google.named("revoke")
    assert revoke["data"] == {"token": "rt-1"} and revoke.get("timeout")
    assert connection(account.browser) is None


def test_disconnecting_when_google_is_down(real_app, google):
    account = sign_up(real_app)
    connect_real(real_app, account.browser, google)
    google.next["revoke"] = [requests.ConnectionError("down")]
    assert account.browser.delete("/connections/youtube").status_code == 204
    assert connection(account.browser) is None


# ---------------------------------------------------------------- uploading through the real client


def test_a_short_through_the_real_client(real_app, google, monkeypatch):
    from .test_publishing_publish import bigger_video

    account = sign_up(real_app)
    connect_real(real_app, account.browser, google)
    project, render_id = rendered_project(real_app, account.browser)
    size = 600_000
    data = bigger_video(real_app, _db(real_app), render_id, size)
    monkeypatch.setattr("reelforge_api.publishing.CHUNK", 256 * 1024)
    post = published(account.browser, render_id)["post"]
    run_jobs(real_app)

    done = get_post(account.browser, post["id"])
    assert done["state"] == "published" and done["url"] == "https://youtube.com/shorts/vid123"
    assert done["privateUntilVerified"] is False  # YouTube made it public as asked
    assert get_project(account.browser, project["id"])["status"] == "posted"

    [start] = google.named("start")
    assert start["params"] == {"uploadType": "resumable", "part": "snippet,status"}
    assert start["headers"] == {
        "Authorization": "Bearer at-1",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(size),
    }
    assert start["json"] == {
        "snippet": {
            "title": BODY["title"],
            "description": "Watch this.\n\n#shorts #facts",
            "tags": ["shorts", "facts"],
            "categoryId": "22",
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    pieces = google.named("chunk")
    assert [p["headers"]["Content-Range"] for p in pieces] == [
        "bytes 0-262143/600000",
        "bytes 262144-524287/600000",
        "bytes 524288-599999/600000",
    ]
    assert [p["headers"]["Content-Length"] for p in pieces] == ["262144", "262144", "75712"]
    assert b"".join(p["data"] for p in pieces) == data
    assert all("Authorization" not in p["headers"] for p in pieces)  # the session address is the credential
    [video] = google.named("videos")
    assert video["params"] == {"part": "status,processingDetails", "id": "vid123"}
    assert video["headers"] == {"Authorization": "Bearer at-1"}


def test_when_youtube_took_only_part_of_a_piece_the_next_starts_there(real_app, google, monkeypatch):
    from .test_publishing_publish import bigger_video

    account = sign_up(real_app)
    connect_real(real_app, account.browser, google)
    _, render_id = rendered_project(real_app, account.browser)
    data = bigger_video(real_app, _db(real_app), render_id, 600_000)
    monkeypatch.setattr("reelforge_api.publishing.CHUNK", 256 * 1024)
    google.partial = 100_000  # the 308 says "Range: bytes=0-99999"
    post = published(account.browser, render_id)["post"]
    run_jobs(real_app)
    starts = [int(p["headers"]["Content-Range"].split()[1].split("-")[0]) for p in google.named("chunk")]
    assert starts == [0, 100_000, 362_144]
    rebuilt = bytearray()
    for p in google.named("chunk"):
        s = int(p["headers"]["Content-Range"].split()[1].split("-")[0])
        rebuilt[s : s + len(p["data"])] = p["data"]
    assert bytes(rebuilt) == data
    assert get_post(account.browser, post["id"])["state"] == "published"


def test_a_5xx_on_a_piece_asks_youtube_how_far_it_got_then_continues(real_app, google, monkeypatch):
    from .test_publishing_publish import bigger_video

    account = sign_up(real_app)
    connect_real(real_app, account.browser, google)
    _, render_id = rendered_project(real_app, account.browser)
    bigger_video(real_app, _db(real_app), render_id, 600_000)
    monkeypatch.setattr("reelforge_api.publishing.CHUNK", 256 * 1024)
    first_ok = {"n": 0}
    real_chunk = google._chunk

    def chunk(kw):
        first_ok["n"] += 1
        if first_ok["n"] == 2:
            return answer(503, {"error": {"code": 503, "status": "UNAVAILABLE"}})
        return real_chunk(kw)

    monkeypatch.setattr(google, "_chunk", chunk)
    post = published(account.browser, render_id)["post"]
    run_jobs(real_app)
    [status] = google.named("status")
    assert status["headers"] == {"Content-Length": "0", "Content-Range": "bytes */600000"}
    assert get_post(account.browser, post["id"])["state"] == "published"


@pytest.mark.parametrize(
    "step",
    ["start", "videos"],
    ids=["starting-the-upload", "checking-processing"],
)
def test_one_5xx_from_youtube_is_retried_not_fatal(real_app, google, step):
    """Technical design: calls are "retried on 5xx and network errors with backoff, never on 4xx"."""
    account = sign_up(real_app)
    connect_real(real_app, account.browser, google)
    _, render_id = rendered_project(real_app, account.browser)
    google.next[step] = [answer(503, {"error": {"code": 503, "status": "UNAVAILABLE"}})]
    post = published(account.browser, render_id)["post"]
    run_jobs(real_app)
    assert get_post(account.browser, post["id"])["state"] == "published", get_post(
        account.browser, post["id"]
    )


def test_youtube_refusing_the_title_fails_as_invalid_metadata(real_app, google):
    account = sign_up(real_app)
    connect_real(real_app, account.browser, google)
    project, render_id = rendered_project(real_app, account.browser)
    google.next["start"] = [answer(400, _error("invalidTitle"))]
    post = published(account.browser, render_id)["post"]
    run_jobs(real_app)
    assert get_post(account.browser, post["id"])["error"] == {
        "code": "invalid_metadata",
        "message": "YouTube didn't accept the title, description or hashtags. Change them and try again.",
    }
    assert get_project(account.browser, project["id"])["status"] == "post_failed"
    assert len(google.named("start")) == 1


def test_quota_exceeded_from_youtube(real_app, google):
    account = sign_up(real_app)
    connect_real(real_app, account.browser, google)
    project, render_id = rendered_project(real_app, account.browser)
    google.next["start"] = [
        answer(
            403, {"error": {"code": 403, "errors": [{"reason": "quotaExceeded", "domain": "youtube.quota"}]}}
        )
    ]
    post = published(account.browser, render_id)["post"]
    run_jobs(real_app)
    assert get_post(account.browser, post["id"])["error"]["code"] == "quota_exceeded"
    assert len(google.named("start")) == 1  # never retried
    assert get_project(account.browser, project["id"])["status"] == "post_failed"


def test_an_expiring_token_is_refreshed_with_the_refresh_token(real_app, google):
    account = sign_up(real_app)
    connect_real(real_app, account.browser, google)
    _, render_id = rendered_project(real_app, account.browser)
    _db(real_app).run(
        "UPDATE youtube_accounts SET token_expires_at = now() + interval '1 minute' WHERE user_id = :u",
        u=account.user["id"],
    )
    post = published(account.browser, render_id)["post"]
    run_jobs(real_app)
    [refresh] = google.named("token-refresh_token")
    assert refresh["data"] == {
        "refresh_token": "rt-1",
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    assert google.named("start")[0]["headers"]["Authorization"] == "Bearer at-2"
    assert get_post(account.browser, post["id"])["state"] == "published"


def test_a_wrong_client_secret_does_not_cost_the_creator_their_connection(real_app, google):
    """QB6-005: Google's token endpoint answers a wrong client id or secret with
    `401 {"error": "invalid_client"}`. That is our server's setting, not the creator's
    connection, so it must not be marked `reconnect_required`."""
    account = sign_up(real_app)
    connect_real(real_app, account.browser, google)
    _, render_id = rendered_project(real_app, account.browser)
    _db(real_app).run(
        "UPDATE youtube_accounts SET token_expires_at = now() WHERE user_id = :u", u=account.user["id"]
    )
    google.next["token-refresh_token"] = [
        answer(401, {"error": "invalid_client", "error_description": "Unauthorized"})
    ]
    post = published(account.browser, render_id)["post"]
    run_jobs(real_app)
    assert get_post(account.browser, post["id"])["state"] == "failed"
    assert connection(account.browser)["status"] == "connected"


def test_invalid_grant_on_refresh_marks_the_connection(real_app, google):
    account = sign_up(real_app)
    connect_real(real_app, account.browser, google)
    project, render_id = rendered_project(real_app, account.browser)
    _db(real_app).run(
        "UPDATE youtube_accounts SET token_expires_at = now() WHERE user_id = :u", u=account.user["id"]
    )
    google.next["token-refresh_token"] = [
        answer(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
    ]
    post = published(account.browser, render_id)["post"]
    run_jobs(real_app)
    assert get_post(account.browser, post["id"])["error"]["code"] == "reconnect_required"
    assert connection(account.browser)["status"] == "reconnect_required"
    assert get_project(account.browser, project["id"])["status"] == "post_failed"
    assert google.named("start") == []


# ---------------------------------------------------------------- the client's pieces


def test_received_reads_the_range_header():
    assert _received(answer(308, b"", {"Range": "bytes=0-1048575"})) == 1_048_576
    assert _received(answer(308, b"", {"Range": "bytes=0-0"})) == 1
    assert _received(answer(308, b"")) == 0


def test_upload_chunk_and_uploaded_so_far(google):
    client = YouTube(client_settings())
    google.next["chunk"] = [answer(308, b"", {"Range": "bytes=0-9"}), answer(201, {"id": "vidX"})]
    assert client.upload_chunk(SESSION, b"0123456789", 0, 20) == (10, None)
    assert client.upload_chunk(SESSION, b"abcdefghij", 10, 20) == (20, "vidX")
    assert [c["headers"] for c in google.named("chunk")] == [
        {"Content-Length": "10", "Content-Range": "bytes 0-9/20"},
        {"Content-Length": "10", "Content-Range": "bytes 10-19/20"},
    ]
    google.next["status"] = [answer(308, b"", {"Range": "bytes=0-9"}), answer(200, {"id": "vidX"})]
    assert client.uploaded_so_far(SESSION, 20) == (10, None)
    assert client.uploaded_so_far(SESSION, 20) == (20, "vidX")
    google.next["status"] = [answer(404, b"Not Found")]
    with pytest.raises(PublishError) as raised:
        client.uploaded_so_far(SESSION, 20)
    assert raised.value.code == "upload_failed" and not raised.value.retryable


def test_the_client_is_configured_only_with_id_and_secret():
    assert YouTube(client_settings()).configured
    assert not YouTube(
        SimpleNamespace(youtube_client_id="", youtube_client_secret="x", youtube_redirect_uri=REDIRECT)
    ).configured
    assert not YouTube(
        SimpleNamespace(youtube_client_id="x", youtube_client_secret="", youtube_redirect_uri=REDIRECT)
    ).configured


def _error(reason: str) -> dict:
    return {"error": {"code": 403, "message": "...", "errors": [{"domain": "youtube", "reason": reason}]}}


@pytest.mark.parametrize(
    ("status", "body", "code", "retryable"),
    [
        (401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}}, "reconnect_required", False),
        (400, {"error": "invalid_grant"}, "reconnect_required", False),
        # Our own OAuth client refused: a server setting, not the creator's connection.
        (400, {"error": "unauthorized_client"}, "upload_failed", False),
        (400, {"error": "invalid_client"}, "upload_failed", False),
        (403, _error("quotaExceeded"), "quota_exceeded", False),
        (403, _error("uploadLimitExceeded"), "quota_exceeded", False),
        (403, _error("dailyLimitExceeded"), "quota_exceeded", False),
        (500, {"error": {"code": 500, "status": "INTERNAL"}}, "upload_failed", True),
        (502, b"<html>Bad Gateway</html>", "upload_failed", True),
        (503, None, "upload_failed", True),
        (429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}, "upload_failed", True),
        (403, _error("rateLimitExceeded"), "upload_failed", True),
        (403, _error("userRateLimitExceeded"), "upload_failed", True),
        (400, _error("invalidTitle"), "invalid_metadata", False),
        (400, _error("invalidDescription"), "invalid_metadata", False),
        (400, _error("invalidTags"), "invalid_metadata", False),
        (400, _error("invalidCategoryId"), "invalid_metadata", False),
        (400, _error("invalidVideoMetadata"), "invalid_metadata", False),
        (400, _error("badRequest"), "upload_failed", False),
        (403, _error("forbidden"), "upload_failed", False),
        (404, b"Not Found", "upload_failed", False),
    ],
)
def test_google_errors_are_mapped(status, body, code, retryable):
    with pytest.raises(PublishError) as raised:
        _raise_for(answer(status, body))
    assert (raised.value.code, raised.value.retryable) == (code, retryable)


def test_a_successful_answer_raises_nothing():
    _raise_for(answer(200, {}))
    _raise_for(answer(201, {}))


# ---------------------------------------------------------------- tokens and settings


def test_tokens_are_encrypted_with_the_configured_key(app):
    with app.app_context():
        sealed = crypto.encrypt("ya29.secret-token")
        assert b"ya29" not in sealed and crypto.decrypt(sealed) == "ya29.secret-token"
        other = Fernet(Fernet.generate_key()).encrypt(b"ya29.secret-token")
        assert crypto.decrypt(other) is None  # another key: counts as missing


def production(**changes) -> dict:
    return {
        **PRODUCTION_SETTINGS,
        "publish_providers": "youtube",
        "token_encryption_key": Fernet.generate_key().decode(),
        "youtube_redirect_uri": "https://app.reelforge.example/api/v1/connections/youtube/callback",
        **changes,
    }


@pytest.fixture
def no_publish_env(monkeypatch):
    for name in (
        "PUBLISH_PROVIDERS",
        "TOKEN_ENCRYPTION_KEY",
        "YOUTUBE_REDIRECT_URI",
        "YOUTUBE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


def test_production_accepts_youtube_with_a_real_key(no_publish_env):
    settings = Settings(**production(youtube_client_secret="a-real-secret"))
    assert settings.publish_provider_names == ["youtube"]
    shown = repr(settings)
    assert settings.token_encryption_key not in shown and "a-real-secret" not in shown


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"publish_providers": "mock"}, "PUBLISH_PROVIDERS"),
        ({"publish_providers": "youtube,mock"}, "PUBLISH_PROVIDERS"),
        ({"token_encryption_key": DEV_TOKEN_KEY}, "TOKEN_ENCRYPTION_KEY"),
        (
            {"youtube_redirect_uri": "http://app.reelforge.example/api/v1/connections/youtube/callback"},
            "YOUTUBE_REDIRECT_URI",
        ),
        ({"youtube_redirect_uri": ""}, "YOUTUBE_REDIRECT_URI"),
    ],
    ids=["mock", "mock-among-others", "dev-key", "http-redirect", "empty-redirect"],
)
def test_production_refuses_unsafe_publish_settings(no_publish_env, change, message):
    with pytest.raises(ValidationError, match=message):
        Settings(**production(**change))


def test_production_refuses_a_forgotten_token_key(no_publish_env):
    values = production()
    del values["token_encryption_key"]
    with pytest.raises(ValidationError, match="TOKEN_ENCRYPTION_KEY"):
        Settings(**values)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"token_encryption_key": "not-a-fernet-key"}, "TOKEN_ENCRYPTION_KEY"),
        ({"publish_providers": "tiktok"}, "PUBLISH_PROVIDERS"),
        ({"publish_providers": ""}, "PUBLISH_PROVIDERS"),
    ],
)
def test_any_environment_refuses_broken_publish_settings(no_publish_env, change, message):
    with pytest.raises(ValidationError, match=message):
        Settings(**{**production(**change), "env": "development"})


def test_development_may_use_the_mock_the_dev_key_and_http(no_publish_env):
    settings = Settings(
        **production(
            env="development",
            publish_providers="mock",
            token_encryption_key=DEV_TOKEN_KEY,
            youtube_redirect_uri=REDIRECT,
        )
    )
    assert settings.publish_provider_names == ["mock"]


# ---------------------------------------------------------------- the database rule


def _db(app):
    from .conftest import Database

    return Database(app)


def test_one_live_post_per_render_and_platform(app, database):
    account = sign_up(app)
    project, render_id = rendered_project(app, account.browser)
    insert = (
        "INSERT INTO posts (project_id, render_id, platform, caption, state) "
        "VALUES (:p, :r, :platform, '', :state)"
    )
    values = {"p": project["id"], "r": render_id}
    database.run(insert, **values, platform="youtube", state="queued")
    for state in ("queued", "uploading", "processing", "published"):
        with pytest.raises(IntegrityError, match="posts_one_per_render_platform"):
            database.run(insert, **values, platform="youtube", state=state)
    # Failed attempts don't count, and another platform has its own live post (P6).
    database.run(insert, **values, platform="youtube", state="failed")
    database.run(insert, **values, platform="youtube", state="failed")
    database.run(insert, **values, platform="instagram", state="published")
    with pytest.raises(IntegrityError):
        database.run(insert, **values, platform="instagram", state="queued")
    # A post that failed can't come back to life next to a live one.
    with pytest.raises(IntegrityError):
        database.run("UPDATE posts SET state = 'queued' WHERE platform = 'youtube' AND state = 'failed'")
    with pytest.raises(IntegrityError):
        database.run(insert, **values, platform="tiktok", state="failed")
    with pytest.raises(IntegrityError):
        database.run(
            "INSERT INTO posts (project_id, render_id, platform, caption, title, state) "
            "VALUES (:p, :r, 'youtube', '', :t, 'failed')",
            **values,
            t="x" * 101,
        )


def test_migration_0002_can_be_undone_with_youtube_posts_present(app, database):
    """The downgrade restores one live post per render; YouTube posts are deleted first."""
    from flask_migrate import downgrade, upgrade

    account = sign_up(app)
    project, render_id = rendered_project(app, account.browser)
    insert = (
        "INSERT INTO posts (project_id, render_id, platform, caption, state) "
        "VALUES (:p, :r, :platform, '', 'published')"
    )
    database.run(insert, p=project["id"], r=render_id, platform="youtube")
    database.run(insert, p=project["id"], r=render_id, platform="instagram")
    with app.app_context():
        from reelforge_api.extensions import db

        downgrade(revision="0001")
        db.session.rollback()
        upgrade()
        db.session.rollback()
    assert database.rows("SELECT platform FROM posts WHERE render_id = :r", r=render_id) == [
        {"platform": "instagram"}
    ]
