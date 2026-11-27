"""Connecting YouTube (milestone B6): API F1 to F3, the returnTo rule, the one-use state, the
encrypted tokens, refreshing them (YT-4, YT-6), the user's `connections`, and account deletion.
Runs with the offline stand-in (PUBLISH_PROVIDERS=mock); test_publishing_youtube_http.py
covers the real client with its HTTP calls stubbed."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest
import requests

from reelforge_api import connections, crypto
from reelforge_api.providers.publish import SCOPES, Channel, MockYouTube, PublishError, Tokens

from .projects_support import ISO_UTC, creator, get_project, run_jobs
from .publish_support import (
    CONNECTION_FIELDS,
    DEFAULT_RETURN,
    RETURN_TO,
    WEB,
    authorize_url,
    callback,
    connect,
    connection,
    get_post,
    published,
    rendered_project,
    reset_mock,
    result_of,
    state_of,
)
from .support import PASSWORD, Browser, assert_error
from .voice_support import no_real_voice_services  # noqa: F401 - autouse fixture


@pytest.fixture(autouse=True)
def fresh_mock():
    reset_mock()
    yield
    reset_mock()


def redis_state(app, state: str) -> dict | None:
    saved = app.extensions["redis"].get(f"oauth:state:{state}")
    return json.loads(saved) if saved else None


def account_row(database, user_id: str) -> dict | None:
    rows = database.rows("SELECT * FROM youtube_accounts WHERE user_id = :u", u=user_id)
    return rows[0] if rows else None


def failed_to_default(location: str) -> bool:
    path, query = result_of(location)
    return (
        location.startswith(WEB + "/")
        and path == DEFAULT_RETURN
        and query == {"connection_error": ["youtube"], "reason": ["failed"]}
    )


# ---------------------------------------------------------------- F1


def test_f1_needs_a_verified_signed_in_user(app):
    assert_error(Browser(app).get("/connections/youtube/connect"), 401, "not_authenticated")
    unverified = creator(app, verified=False)
    assert_error(authorize_url(unverified.browser), 403, "email_not_verified")


def test_f1_answers_with_a_one_use_state_for_ten_minutes_tied_to_the_user(app):
    me = creator(app)
    res = authorize_url(me.browser, "/onboarding?step=3")
    assert res.status_code == 200 and set(res.get_json()) == {"authorizeUrl"}
    state = state_of(me.browser, "/onboarding?step=3")
    saved = redis_state(app, state)
    assert saved == {"user": me.id, "platform": "youtube", "returnTo": "/onboarding?step=3"}
    ttl = app.extensions["redis"].ttl(f"oauth:state:{state}")
    assert 590 <= ttl <= 600
    assert len(state) >= 32
    # Each click gets its own state.
    assert state_of(me.browser) != state


@pytest.mark.parametrize(
    ("platform", "status", "code"), [("instagram", 404, "not_available"), ("tiktok", 404, "not_found")]
)
def test_f1_for_another_platform(app, platform, status, code):
    me = creator(app)
    assert_error(authorize_url(me.browser, platform=platform), status, code)


def test_f1_without_youtube_set_up_is_503(app):
    """The real client with no client id: nothing is sent anywhere, the answer is 503."""
    from .conftest import build_app
    from .support import sign_up

    real = build_app(publish_providers="youtube", youtube_client_id="", youtube_client_secret="")
    account = sign_up(real)
    assert_error(authorize_url(account.browser), 503, "provider_unavailable")
    # Half set up is not set up either.
    half = build_app(publish_providers="youtube", youtube_client_id="cid", youtube_client_secret="")
    assert_error(authorize_url(Browser(half).copy_session_from(account.browser)), 503, "provider_unavailable")


# ---------------------------------------------------------------- the returnTo rule (pure)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "app/settings",
        "//evil.example",
        "///evil.example",
        "https://evil.example",
        "http:/evil.example",
        "javascript:alert(1)",
        "/\\evil.example",
        "/%5Cevil.example",
        "/%5cevil.example",
        "/%2Fevil.example",
        "/app\tx",
        "/app\nx",
        "/app\rx",
        "/app\x00",
        "/app\x7f",
        "/app\x1b",
        "/app%09x",
        "/app%0d%0aSet-Cookie:x=1",
        "/app%00",
        "/app%7F",
        "/" + "a" * 300,
    ],
    ids=repr,
)
def test_return_to_falls_back_to_the_default(value):
    assert connections.safe_return_to(value) == DEFAULT_RETURN


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/onboarding?step=3", "/onboarding?step=3"),
        ("/app/p/x/publish", "/app/p/x/publish"),
        ("/app/./settings", "/app/settings"),
        ("/app/x/../settings", "/app/settings"),
        ("/app/%2e%2e/settings", "/settings"),
        ("/app/%2E%2E/%2e%2e/%2e%2e/settings", "/settings"),
        ("/app#fragment", "/app"),
        ("/app?step=3#fragment", "/app?step=3"),
    ],
)
def test_return_to_keeps_paths_on_the_site_resolved(value, expected):
    assert connections.safe_return_to(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "/.//evil.example",
        "/%2e//evil.example",
        "/%2e%2e//evil.example",
        "/app/%2e%2e/%2e%2e//evil.example",
        "/./%2e/.//evil.example",
        "/%2Fevil.example",
        "/%2f%2fevil.example/x",
        "/%2F/evil.example",
    ],
)
def test_return_to_with_dot_segments_or_encoded_slashes_never_becomes_another_site(value):
    result = connections.safe_return_to(value)
    # Either the default or a path on the web app itself, never `//evil.example` (another site).
    assert result.startswith("/") and not result.startswith("//"), result


def test_return_to_up_to_300_characters_is_kept():
    exactly = "/" + "a" * 299
    assert connections.safe_return_to(exactly) == exactly
    assert connections.safe_return_to(exactly + "a") == DEFAULT_RETURN


@pytest.mark.parametrize("ours", ["connected", "connection_error", "reason", "platform", "instagram"])
def test_return_to_drops_our_own_result_parameters(ours):
    assert connections.safe_return_to(f"/onboarding?{ours}=youtube&step=3") == "/onboarding?step=3"
    assert connections.safe_return_to(f"/onboarding?{ours}=x") == "/onboarding"


def test_return_to_with_an_encoded_question_mark_can_not_smuggle_a_result():
    """A `?` written as %3F must not turn into a query that carries our own parameters."""
    value = "/app/p/x%3Fconnection_error%3Dyoutube%26reason%3Dno_channel"
    result = connections.safe_return_to(value)
    final = connections.with_result(result, connected="youtube")
    _, query = result_of(final)
    assert query.get("connected") == ["youtube"]
    assert "connection_error" not in query and "reason" not in query, final


# ---------------------------------------------------------------- F2 through HTTP


def test_return_to_through_f1_and_f2_lands_on_the_page_with_the_result(app):
    me = creator(app)
    location = connect(me.browser, "/onboarding?step=3&connected=no&reason=x#top")
    path, query = result_of(location)
    assert path == "/onboarding"
    assert query == {"step": ["3"], "connected": ["youtube"]}


@pytest.mark.parametrize(
    "evil", ["//evil.example", "/.//evil.example", "/%2e%2e//evil.example", "/\\evil.example", "/app%0aX"]
)
def test_return_to_through_f2_never_leaves_the_site(app, evil):
    me = creator(app)
    location = connect(me.browser, evil)
    parts = urlsplit(location)
    assert (parts.scheme, parts.netloc) == ("http", "localhost:5173"), location
    assert parts.path.startswith("/") and not parts.path.startswith("//"), location
    assert "\\" not in location and "\n" not in location


def test_return_to_with_non_ascii_text_gives_a_valid_location(app):
    me = creator(app)
    location = connect(me.browser, "/app/caf%C3%A9")
    assert location.isascii(), location
    assert result_of(location)[1]["connected"] == ["youtube"]


def test_f2_connects_and_stores_the_tokens_encrypted(app, database):
    me = creator(app)
    before = datetime.now(UTC)
    location = connect(me.browser)
    path, query = result_of(location)
    assert path == RETURN_TO and query == {"connected": ["youtube"]}

    row = account_row(database, me.id)
    assert row["channel_id"] == "UCmockchannel" and row["channel_title"] == "Mock Channel"
    assert row["status"] == "connected"
    assert set(row["scopes"]) == set(SCOPES)
    assert b"mock-access" not in bytes(row["access_token_enc"])
    assert b"mock-refresh" not in bytes(row["refresh_token_enc"])
    with app.app_context():
        assert crypto.decrypt(row["access_token_enc"]) == "mock-access"
        assert crypto.decrypt(row["refresh_token_enc"]) == "mock-refresh"
    expires = row["token_expires_at"]
    assert before + timedelta(seconds=3500) < expires < datetime.now(UTC) + timedelta(seconds=3700)


def test_f2_redirect_headers_keep_the_code_private(app):
    me = creator(app)
    state = state_of(me.browser)
    ok = callback(me.browser, state=state, code="mock-code")
    bad = callback(me.browser, state="nope", code="mock-code")
    for res in (ok, bad):
        assert res.status_code == 302
        assert res.headers["Cache-Control"] == "no-store"
        assert res.headers["Referrer-Policy"] == "no-referrer"
        assert "mock-code" not in res.headers["Location"]


def test_f2_state_is_used_once(app, database):
    me = creator(app)
    state = state_of(me.browser)
    assert result_of(callback(me.browser, state=state, code="mock-code").headers["Location"])[1]["connected"]
    again = callback(me.browser, state=state, code="mock-code")
    assert failed_to_default(again.headers["Location"])
    assert redis_state(app, state) is None


def test_f2_expired_state_is_refused(app, database):
    me = creator(app)
    state = state_of(me.browser)
    # Ten minutes pass: Redis drops the key (its expiry is checked in the F1 test).
    app.extensions["redis"].delete(f"oauth:state:{state}")
    res = callback(me.browser, state=state, code="mock-code")
    assert failed_to_default(res.headers["Location"])
    assert account_row(database, me.id) is None


@pytest.mark.parametrize("state", [None, "", "x" * 201, "not-a-state"])
def test_f2_missing_or_unknown_state(app, database, state):
    me = creator(app)
    query = {"code": "mock-code"} if state is None else {"code": "mock-code", "state": state}
    assert failed_to_default(callback(me.browser, **query).headers["Location"])
    assert account_row(database, me.id) is None


def test_f2_state_belongs_to_its_user(app, database):
    me, other = creator(app), creator(app)
    state = state_of(me.browser)
    res = callback(other.browser, state=state, code="mock-code")
    assert failed_to_default(res.headers["Location"])
    assert account_row(database, me.id) is None and account_row(database, other.id) is None


def test_f2_signed_out_is_refused(app, database):
    me = creator(app)
    state = state_of(me.browser)
    res = callback(Browser(app), state=state, code="mock-code")
    assert res.status_code == 302 and failed_to_default(res.headers["Location"])
    assert account_row(database, me.id) is None


def test_f2_with_a_state_made_for_another_platform(app, database):
    me = creator(app)
    state = "planted-state-for-instagram"
    app.extensions["redis"].set(
        f"oauth:state:{state}",
        json.dumps({"user": me.id, "platform": "instagram", "returnTo": RETURN_TO}),
        ex=600,
    )
    assert failed_to_default(callback(me.browser, state=state, code="mock-code").headers["Location"])
    assert account_row(database, me.id) is None
    # And the Instagram callback (not available yet) never connects anything.
    state = state_of(me.browser)
    res = callback(me.browser, "instagram", state=state, code="mock-code")
    path, query = result_of(res.headers["Location"])
    assert (
        path == DEFAULT_RETURN
        and query["connection_error"] == ["instagram"]
        and query["reason"] == ["failed"]
    )
    assert account_row(database, me.id) is None


def test_f2_access_denied_goes_back_with_denied(app, database):
    me = creator(app)
    state = state_of(me.browser)
    res = callback(me.browser, state=state, error="access_denied")
    path, query = result_of(res.headers["Location"])
    assert path == RETURN_TO and query == {"connection_error": ["youtube"], "reason": ["denied"]}
    assert account_row(database, me.id) is None
    assert redis_state(app, state) is None


def test_f2_without_a_code_goes_back_to_the_page_with_failed(app, database):
    """F2: "Always 302 to returnTo with ... connection_error=youtube&reason=failed"."""
    me = creator(app)
    state = state_of(me.browser)
    res = callback(me.browser, state=state)
    path, query = result_of(res.headers["Location"])
    assert query == {"connection_error": ["youtube"], "reason": ["failed"]}
    assert account_row(database, me.id) is None
    assert path == RETURN_TO


def test_f2_without_a_channel_stores_nothing(app, database):
    me = creator(app)
    MockYouTube.no_channel = True
    path, query = result_of(connect(me.browser))
    assert path == RETURN_TO and query == {"connection_error": ["youtube"], "reason": ["no_channel"]}
    assert account_row(database, me.id) is None


def test_f2_without_a_refresh_token_stores_nothing(app, database, monkeypatch):
    me = creator(app)
    monkeypatch.setattr(
        MockYouTube, "exchange", lambda self, code: Tokens("mock-access", 3600, None, list(SCOPES))
    )
    path, query = result_of(connect(me.browser))
    assert path == RETURN_TO and query == {"connection_error": ["youtube"], "reason": ["failed"]}
    assert account_row(database, me.id) is None


@pytest.mark.parametrize(
    "failure",
    [
        PublishError("reconnect_required"),
        PublishError("upload_failed", retryable=True),
        requests.ConnectionError("down"),
        KeyError("access_token"),
        ValueError("not JSON"),
        RuntimeError("anything else"),
    ],
    ids=lambda e: type(e).__name__,
)
@pytest.mark.parametrize("step", ["exchange", "channel"])
def test_f2_exchange_or_channel_failure_goes_back_with_failed(app, database, monkeypatch, failure, step):
    me = creator(app)

    def broken(self, *args):
        raise failure

    monkeypatch.setattr(MockYouTube, step, broken)
    res = callback(me.browser, state=state_of(me.browser), code="mock-code")
    assert res.status_code == 302
    path, query = result_of(res.headers["Location"])
    assert path == RETURN_TO and query == {"connection_error": ["youtube"], "reason": ["failed"]}
    assert account_row(database, me.id) is None


def test_f2_with_a_wrong_code(app, database):
    me = creator(app)
    res = callback(me.browser, state=state_of(me.browser), code="someone-elses-code")
    assert result_of(res.headers["Location"])[1]["reason"] == ["failed"]
    assert account_row(database, me.id) is None


def test_reconnecting_updates_the_one_account(app, database, monkeypatch):
    me = creator(app)
    connect(me.browser)
    first = account_row(database, me.id)
    database.run("UPDATE youtube_accounts SET status = 'reconnect_required' WHERE user_id = :u", u=me.id)
    monkeypatch.setattr(
        MockYouTube,
        "channel",
        lambda self, token: Channel("UCother", "Renamed Channel", "https://yt3.example/p.jpg"),
    )
    monkeypatch.setattr(
        MockYouTube, "exchange", lambda self, code: Tokens("access-2", 3600, "refresh-2", list(SCOPES))
    )
    assert result_of(connect(me.browser))[1]["connected"] == ["youtube"]
    # F2: "A second connect replaces the first and revokes its token".
    assert MockYouTube.revoked == ["mock-refresh"]
    with app.app_context():
        assert crypto.decrypt(account_row(database, me.id)["refresh_token_enc"]) == "refresh-2"
    rows = database.rows("SELECT * FROM youtube_accounts WHERE user_id = :u", u=me.id)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == first["id"] and row["status"] == "connected"
    assert (row["channel_id"], row["channel_title"], row["picture_url"]) == (
        "UCother",
        "Renamed Channel",
        "https://yt3.example/p.jpg",
    )
    assert connection(me.browser)["pictureUrl"] == "https://yt3.example/p.jpg"


def test_a_failed_reconnect_keeps_the_existing_connection(app, database):
    me = creator(app)
    connect(me.browser)
    MockYouTube.no_channel = True
    assert result_of(connect(me.browser))[1]["reason"] == ["no_channel"]
    assert connection(me.browser)["status"] == "connected"


# ---------------------------------------------------------------- the user's connections


def test_user_json_connections_shape(app):
    me = creator(app)
    user = me.browser.get("/me").get_json()["user"]
    assert user["connections"] == {"youtube": None, "instagram": None}
    assert "instagram" in user  # the older field stays until B6b
    connect(me.browser)
    youtube = connection(me.browser)
    assert set(youtube) == CONNECTION_FIELDS
    assert youtube["status"] == "connected" and youtube["name"] == "Mock Channel"
    assert youtube["pictureUrl"] is None  # the stand-in's channel has no picture
    assert ISO_UTC.match(youtube["connectedAt"])
    # A9: the login answer carries the same user.
    login = Browser(app).post("/auth/login", {"email": me.account.email, "password": PASSWORD})
    assert login.get_json()["user"]["connections"]["youtube"] == youtube


def test_user_json_shows_reconnect_required(app, database):
    me = creator(app)
    connect(me.browser)
    database.run("UPDATE youtube_accounts SET status = 'reconnect_required' WHERE user_id = :u", u=me.id)
    assert connection(me.browser)["status"] == "reconnect_required"


def test_deleting_the_account_removes_the_youtube_connection(app, database):
    me = creator(app)
    connect(me.browser)
    assert account_row(database, me.id) is not None
    res = me.browser.delete("/me", {"password": PASSWORD})
    assert res.status_code in (200, 204), res.get_data(as_text=True)
    assert account_row(database, me.id) is None
    assert MockYouTube.revoked == ["mock-refresh"]  # and revoked at Google


def test_unticking_the_upload_permission_stores_nothing(app, database, monkeypatch):
    """F2 `missing_permission`: the creator unticked "Manage your YouTube videos"."""
    me = creator(app)
    read_only = [s for s in SCOPES if s.endswith("youtube.readonly")]
    monkeypatch.setattr(
        MockYouTube, "exchange", lambda self, code: Tokens("mock-access", 3600, "mock-refresh", read_only)
    )
    path, query = result_of(connect(me.browser))
    assert path == RETURN_TO and query == {"connection_error": ["youtube"], "reason": ["missing_permission"]}
    assert account_row(database, me.id) is None
    assert MockYouTube.revoked == ["mock-refresh"]


def test_two_users_may_connect_the_same_channel(app, database):
    me, other = creator(app), creator(app)
    connect(me.browser)
    connect(other.browser)
    assert connection(me.browser)["name"] == connection(other.browser)["name"] == "Mock Channel"
    assert database.value("SELECT count(*) FROM youtube_accounts WHERE channel_id = 'UCmockchannel'") == 2


def test_the_daily_check_marks_only_refused_connections(app, database):
    fine, revoked = creator(app), creator(app)
    connect(fine.browser)
    connect(revoked.browser)
    with app.app_context():
        bad = crypto.encrypt("revoked-at-google")
    database.run("UPDATE youtube_accounts SET refresh_token_enc = :r WHERE user_id = :u", r=bad, u=revoked.id)
    with app.app_context():
        assert connections.check_connections() == 1
    assert connection(fine.browser)["status"] == "connected"
    assert connection(revoked.browser)["status"] == "reconnect_required"


# ---------------------------------------------------------------- F3


def test_f3_disconnect_revokes_deletes_and_keeps_posts(app, database):
    me = creator(app)
    connect(me.browser)
    project, render_id = rendered_project(app, me.browser)
    post = published(me.browser, render_id)["post"]
    run_jobs(app)
    assert get_post(me.browser, post["id"])["state"] == "published"

    res = me.browser.delete("/connections/youtube")
    assert res.status_code == 204 and res.get_data() == b""
    assert MockYouTube.revoked == ["mock-refresh"]
    assert account_row(database, me.id) is None
    assert connection(me.browser) is None
    # YT-12: the history stays.
    kept = get_post(me.browser, post["id"])
    assert kept["state"] == "published" and kept["url"].startswith("https://youtube.com/shorts/")
    assert get_project(me.browser, project["id"])["latestPost"]["id"] == post["id"]
    assert database.value("SELECT youtube_account_id FROM posts WHERE id = :p", p=post["id"]) is None

    # A second disconnect changes nothing and revokes nothing more.
    assert me.browser.delete("/connections/youtube").status_code == 204
    assert MockYouTube.revoked == ["mock-refresh"]


def test_f3_when_google_cannot_be_reached_still_disconnects(app, database, monkeypatch):
    me = creator(app)
    connect(me.browser)

    def down(self, token):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(MockYouTube, "revoke", down)
    assert me.browser.delete("/connections/youtube").status_code == 204
    assert account_row(database, me.id) is None


def test_f3_needs_a_login_and_a_known_platform(app):
    assert_error(Browser(app).delete("/connections/youtube"), 401, "not_authenticated")
    me = creator(app)
    assert_error(me.browser.delete("/connections/instagram"), 404, "not_available")
    assert_error(me.browser.delete("/connections/tiktok"), 404, "not_found")


def test_f3_only_removes_my_own_connection(app, database):
    me, other = creator(app), creator(app)
    connect(me.browser)
    connect(other.browser)
    assert other.browser.delete("/connections/youtube").status_code == 204
    assert account_row(database, me.id) is not None
    assert connection(me.browser)["status"] == "connected"


# ---------------------------------------------------------------- the access token (YT-4, YT-6)


def account_id(database, user_id: str):
    return database.value("SELECT id FROM youtube_accounts WHERE user_id = :u", u=user_id)


@pytest.fixture
def refreshes(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = MockYouTube.refresh

    def refresh(self, token):
        calls.append(token)
        return real(self, token)

    monkeypatch.setattr(MockYouTube, "refresh", refresh)
    return calls


def test_a_fresh_access_token_is_used_as_it_is(app, database, refreshes):
    me = creator(app)
    connect(me.browser)
    with app.app_context():
        assert connections.access_token(account_id(database, me.id)) == "mock-access"
    assert refreshes == []


def test_an_access_token_about_to_expire_is_refreshed(app, database, refreshes):
    me = creator(app)
    connect(me.browser)
    database.run(
        "UPDATE youtube_accounts SET token_expires_at = now() + interval '2 minutes' WHERE user_id = :u",
        u=me.id,
    )
    with app.app_context():
        assert connections.access_token(account_id(database, me.id)) == "mock-access"
    assert refreshes == ["mock-refresh"]
    row = account_row(database, me.id)
    assert row["token_expires_at"] > datetime.now(UTC) + timedelta(minutes=55)
    assert row["status"] == "connected"
    with app.app_context():
        assert crypto.decrypt(row["refresh_token_enc"]) == "mock-refresh"  # kept: Google sent none


def test_an_access_token_that_can_not_be_read_is_refreshed(app, database, refreshes):
    """A changed encryption key: the stored access token counts as missing."""
    me = creator(app)
    connect(me.browser)
    with app.app_context():
        refresh_enc = crypto.encrypt("mock-refresh")
    database.run(
        "UPDATE youtube_accounts SET access_token_enc = :a, refresh_token_enc = :r WHERE user_id = :u",
        a=b"not-fernet",
        r=refresh_enc,
        u=me.id,
    )
    with app.app_context():
        assert connections.access_token(account_id(database, me.id)) == "mock-access"
    assert refreshes == ["mock-refresh"]


def test_invalid_grant_marks_the_connection_reconnect_required(app, database):
    me = creator(app)
    connect(me.browser)
    with app.app_context():
        revoked = crypto.encrypt("revoked-at-google")
    database.run(
        "UPDATE youtube_accounts SET token_expires_at = now(), refresh_token_enc = :r WHERE user_id = :u",
        r=revoked,
        u=me.id,
    )
    with app.app_context(), pytest.raises(PublishError) as raised:
        connections.access_token(account_id(database, me.id))
    assert raised.value.code == "reconnect_required"
    assert account_row(database, me.id)["status"] == "reconnect_required"
    assert connection(me.browser)["status"] == "reconnect_required"


def test_a_refresh_that_fails_for_another_reason_keeps_the_connection(app, database, monkeypatch):
    me = creator(app)
    connect(me.browser)
    database.run("UPDATE youtube_accounts SET token_expires_at = now() WHERE user_id = :u", u=me.id)

    def down(self, token):
        raise PublishError("upload_failed", "Google answered 503", retryable=True)

    monkeypatch.setattr(MockYouTube, "refresh", down)
    with app.app_context(), pytest.raises(PublishError):
        connections.access_token(account_id(database, me.id))
    assert account_row(database, me.id)["status"] == "connected"


def test_a_connection_marked_reconnect_required_gives_no_token(app, database, refreshes):
    me = creator(app)
    connect(me.browser)
    database.run("UPDATE youtube_accounts SET status = 'reconnect_required' WHERE user_id = :u", u=me.id)
    with app.app_context(), pytest.raises(PublishError) as raised:
        connections.access_token(account_id(database, me.id))
    assert raised.value.code == "reconnect_required" and refreshes == []
