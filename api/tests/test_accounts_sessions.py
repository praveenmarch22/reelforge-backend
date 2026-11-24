"""Login, session cookies, CSRF, refresh and logout: docs/API_DESIGN.md Conventions (Auth), A4, A5, A6."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from .conftest import build_app
from .support import (
    ACCESS,
    COOKIE_PATHS,
    CSRF,
    FIFTEEN_MINUTES,
    NEW_PASSWORD,
    PASSWORD,
    REFRESH,
    SESSION_COOKIES,
    THIRTY_DAYS,
    Browser,
    assert_clears_session_cookies,
    assert_error,
    assert_invalid,
    assert_sets_no_session_cookies,
    claims_of,
    expire_access_token,
    last_token,
    log_in,
    new_email,
    resign,
    set_cookies,
    sign_up,
)

pytestmark = pytest.mark.usefixtures("clean_state")


def copy_of(browser: Browser) -> Browser:
    """A second holder of the browser's current cookies (another tab, or a thief)."""
    return Browser(browser.app).copy_session_from(browser)


def is_logged_in(browser: Browser) -> bool:
    return browser.get("/me").status_code == 200


def make_rotated_tokens_older_than_the_grace_window(database) -> None:
    database.run(
        "UPDATE refresh_tokens SET revoked_at = revoked_at - interval '11 seconds' "
        "WHERE revoked_at IS NOT NULL"
    )


# ---------------------------------------------------------------- A4 login


def test_login_returns_the_user_and_starts_a_session(app, outbox):
    account = sign_up(app)
    browser = Browser(app)

    res = browser.post("/auth/login", {"email": account.email, "password": PASSWORD})

    assert res.status_code == 200
    assert res.get_json() == {"user": account.user}
    assert browser.get("/me").get_json() == {"user": account.user}


def test_an_unverified_account_can_log_in(app, outbox):
    account = sign_up(app, verified=False)
    res = Browser(app).post("/auth/login", {"email": account.email, "password": PASSWORD})
    assert res.status_code == 200
    assert res.get_json()["user"]["emailVerified"] is False


def test_login_gives_one_answer_for_unknown_email_wrong_password_and_deleted_account(app, outbox):
    """Nothing in the answer may say whether an account exists (account enumeration)."""
    account = sign_up(app)
    deleted = sign_up(app)
    assert deleted.browser.delete("/me", {"password": PASSWORD}).status_code == 204

    answers = {
        "wrong password": Browser(app).post(
            "/auth/login", {"email": account.email, "password": NEW_PASSWORD}
        ),
        "unknown email": Browser(app).post("/auth/login", {"email": new_email(), "password": PASSWORD}),
        "deleted account": Browser(app).post("/auth/login", {"email": deleted.email, "password": PASSWORD}),
    }

    for res in answers.values():
        assert_error(res, 401, "invalid_credentials")
        assert_sets_no_session_cookies(res)
    bodies = {name: res.get_data(as_text=True) for name, res in answers.items()}
    assert len(set(bodies.values())) == 1, bodies
    headers = {name: sorted(key for key, _ in res.headers) for name, res in answers.items()}
    assert len({tuple(names) for names in headers.values()}) == 1, headers


def test_a_disabled_account_cannot_log_in(app, database, outbox):
    account = sign_up(app)
    database.run("UPDATE users SET disabled_at = now()")

    res = Browser(app).post("/auth/login", {"email": account.email, "password": PASSWORD})
    assert_error(res, 403, "account_disabled")
    assert_sets_no_session_cookies(res)
    # Only someone who knows the password learns that the account is disabled.
    wrong = Browser(app).post("/auth/login", {"email": account.email, "password": NEW_PASSWORD})
    assert_error(wrong, 401, "invalid_credentials")


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"password": PASSWORD}, "email"),
        ({"email": "asha@example.com"}, "password"),
        ({"email": 5, "password": PASSWORD}, "email"),
        ({"email": "asha@example.com", "password": 12345678}, "password"),
        ({"email": "asha@example.com", "password": None}, "password"),
        ({"email": "asha@example.com", "password": PASSWORD, "remember": True}, "remember"),
    ],
)
def test_login_validates_its_body(app, body, field):
    assert_invalid(Browser(app).post("/auth/login", body), field)


def test_logging_in_again_ends_the_session_the_browser_had(app, database, outbox):
    """A session id planted in or left on a browser never survives a login (session fixation)."""
    first = sign_up(app)
    second = sign_up(app)
    browser = first.browser
    left_behind = copy_of(browser)
    before = {name: browser.cookie(name) for name in SESSION_COOKIES}

    assert browser.post("/auth/login", {"email": second.email, "password": PASSWORD}).status_code == 200

    after = {name: browser.cookie(name) for name in SESSION_COOKIES}
    assert all(after[name] and after[name] != before[name] for name in SESSION_COOKIES)
    assert claims_of(app, after[ACCESS])["sid"] != claims_of(app, before[ACCESS])["sid"]
    assert browser.get("/me").get_json()["user"]["email"] == second.email
    assert_error(left_behind.get("/me"), 401, "not_authenticated")
    assert_error(left_behind.post("/auth/refresh"), 401, "not_authenticated")


# ---------------------------------------------------------------- cookies


def start_by_signup(app) -> tuple:
    browser = Browser(app)
    return browser, browser.post("/auth/signup", {"email": new_email(), "password": PASSWORD, "name": "A"})


def start_by_login(app) -> tuple:
    account = sign_up(app)
    browser = Browser(app)
    return browser, browser.post("/auth/login", {"email": account.email, "password": PASSWORD})


def start_by_verifying(app) -> tuple:
    account = sign_up(app, verified=False)
    browser = Browser(app)
    return browser, browser.post("/auth/verify-email", {"token": last_token(app, account.email, "verify")})


def start_by_refresh(app) -> tuple:
    account = sign_up(app)
    return account.browser, account.browser.post("/auth/refresh")


STARTS = [start_by_signup, start_by_login, start_by_verifying, start_by_refresh]


@pytest.mark.parametrize("start", STARTS)
def test_session_cookies_have_the_documented_attributes(app, outbox, start):
    _, res = start(app)
    assert res.status_code in (200, 201)
    cookies = set_cookies(res)

    assert set(cookies) == set(SESSION_COOKIES)
    for name in SESSION_COOKIES:
        assert cookies[name].value
        assert cookies[name]["path"] == COOKIE_PATHS[name]
        assert cookies[name]["samesite"] == "Lax"
        # rf_access is kept for 30 days too, so an expired token can be told from no session.
        assert cookies[name]["max-age"] == str(THIRTY_DAYS)
    assert cookies[ACCESS]["httponly"] and cookies[REFRESH]["httponly"]
    assert not cookies[CSRF]["httponly"], "the web app has to read rf_csrf"
    # The tokens are in cookies only.
    text = res.get_data(as_text=True)
    assert not any(cookies[name].value in text for name in SESSION_COOKIES)


@pytest.mark.parametrize("start", STARTS)
def test_tokens_last_15_minutes_and_30_days(app, outbox, start):
    browser, _ = start(app)
    access, refresh = claims_of(app, browser.cookie(ACCESS)), claims_of(app, browser.cookie(REFRESH))

    assert access["exp"] - access["iat"] == FIFTEEN_MINUTES
    assert refresh["exp"] - refresh["iat"] == THIRTY_DAYS
    assert abs(access["iat"] - datetime.now(UTC).timestamp()) < 60


def test_cookies_are_secure_and_csrf_is_set_for_the_parent_domain_when_configured(app, outbox):
    secure_app = build_app(cookie_secure=True, cookie_domain="reelforge.example")
    browser = Browser(secure_app)
    res = browser.post("/auth/signup", {"email": new_email(), "password": PASSWORD, "name": "A"})
    assert res.status_code == 201

    cookies = set_cookies(res)
    assert all(cookies[name]["secure"] for name in SESSION_COOKIES)
    assert cookies[CSRF]["domain"] == "reelforge.example"
    assert (
        not cookies[ACCESS]["domain"] and not cookies[REFRESH]["domain"]
    ), "session tokens stay on the API host"


def test_the_refresh_cookie_is_only_sent_to_the_auth_endpoints(app, outbox):
    account = sign_up(app)
    account.browser.drop_cookie(ACCESS)
    account.browser.drop_cookie(CSRF)
    # Only rf_refresh is left: /me doesn't see it, /auth/refresh does.
    assert_error(account.browser.get("/me"), 401, "not_authenticated")
    assert account.browser.post("/auth/refresh").status_code == 200


# ---------------------------------------------------------------- CSRF

PROTECTED = [
    ("POST", "/auth/resend-verification", None),
    ("POST", "/auth/logout", None),
    ("PATCH", "/me", {"name": "Changed"}),
    ("POST", "/me/password", {"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD}),
    ("DELETE", "/me", {"password": PASSWORD}),
]


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
@pytest.mark.parametrize("sent", ["nothing", "empty", "wrong", "another session's", "own previous session's"])
def test_changes_need_this_sessions_csrf_token(app, outbox, method, path, body, sent):
    account = sign_up(app, verified=False)
    browser = account.browser
    previous = browser.cookie(CSRF)
    assert browser.post("/auth/login", {"email": account.email, "password": PASSWORD}).status_code == 200
    header = {
        "nothing": None,
        "empty": "",
        "wrong": "x" * len(browser.cookie(CSRF)),
        "another session's": sign_up(app).browser.cookie(CSRF),
        "own previous session's": previous,
    }[sent]
    outbox.clear()

    res = browser.call(method, path, body, csrf=header)

    assert_error(res, 403, "csrf_failed")
    # Nothing happened: same session, same name, same password, account still there, no email.
    assert browser.get("/me").get_json()["user"] == account.user
    assert Browser(app).post("/auth/login", {"email": account.email, "password": PASSWORD}).status_code == 200
    assert outbox == []
    # With the value from the rf_csrf cookie the same request goes through.
    assert browser.call(method, path, body).status_code in (200, 204)


def test_reading_needs_no_csrf_token(app, outbox):
    account = sign_up(app)
    assert account.browser.get("/me", csrf=None).status_code == 200


def test_a_request_without_a_session_is_401_before_csrf_is_looked_at(app):
    assert_error(Browser(app).patch("/me", {"name": "X"}, csrf=None), 401, "not_authenticated")
    assert_error(Browser(app).post("/auth/logout", csrf="anything"), 401, "not_authenticated")


def test_public_endpoints_ignore_the_csrf_header(app, outbox):
    """Before the first login there is no rf_csrf cookie, so these can't ask for it."""
    email = new_email()
    browser = Browser(app)
    stale = {"csrf": "left-over-or-missing"}

    assert (
        browser.post("/auth/signup", {"email": email, "password": PASSWORD, "name": "A"}, **stale).status_code
        == 201
    )
    token = last_token(app, email, "verify")
    assert browser.post("/auth/verify-email", {"token": token}, csrf=None).status_code == 200
    assert browser.post("/auth/refresh", **stale).status_code == 200
    assert browser.post("/auth/refresh", csrf=None).status_code == 200
    assert browser.post("/auth/login", {"email": email, "password": PASSWORD}, csrf=None).status_code == 200
    assert browser.post("/auth/forgot-password", {"email": email}, csrf=None).status_code == 204
    reset = {"token": last_token(app, email, "reset"), "password": NEW_PASSWORD}
    assert browser.post("/auth/reset-password", reset, **stale).status_code == 204


def test_refreshing_replaces_the_csrf_token(app, outbox):
    account = sign_up(app)
    browser = account.browser
    old = browser.cookie(CSRF)

    assert browser.post("/auth/refresh").status_code == 200

    assert browser.cookie(CSRF) != old
    assert_error(browser.patch("/me", {"name": "X"}, csrf=old), 403, "csrf_failed")
    assert browser.patch("/me", {"name": "X"}).status_code == 200


# ---------------------------------------------------------------- access tokens


def test_no_session_is_not_authenticated(app):
    assert_error(Browser(app).get("/me"), 401, "not_authenticated")


def test_an_expired_access_token_says_token_expired_and_refreshing_recovers(app, outbox):
    account = sign_up(app)
    browser = account.browser
    expire_access_token(browser)

    res = browser.get("/me")
    assert_error(res, 401, "token_expired")
    assert set_cookies(res) == {}, "the cookies are still needed for the refresh"
    assert_error(browser.patch("/me", {"name": "X"}), 401, "token_expired")

    assert browser.post("/auth/refresh").status_code == 200
    assert browser.get("/me").get_json() == {"user": account.user}


def forged(app, browser: Browser, how: str) -> str:
    token = browser.cookie(ACCESS)
    if how == "garbage":
        return "not-a-token"
    if how == "signed with another key":
        return resign(app, token, key="k" * 40)
    if how == "unsigned":
        return jwt.encode(claims_of(app, token), None, algorithm="none")
    if how == "refresh token":
        return browser.cookie(REFRESH)
    if how == "someone who does not exist":
        return resign(app, token, sub=str(uuid.uuid4()))
    if how == "no expiry":
        claims = claims_of(app, token)
        del claims["exp"]
        return jwt.encode(claims, app.extensions["settings"].secret_key, algorithm="HS256")
    raise AssertionError(how)


@pytest.mark.parametrize(
    "how",
    [
        "garbage",
        "signed with another key",
        "unsigned",
        "refresh token",
        "someone who does not exist",
        "no expiry",
    ],
)
def test_a_token_that_is_not_a_valid_access_token_is_not_authenticated(app, outbox, how):
    account = sign_up(app)
    intruder = Browser(app)
    intruder.set_cookie(ACCESS, forged(app, account.browser, how))
    intruder.set_cookie(CSRF, account.browser.cookie(CSRF))

    assert_error(intruder.get("/me"), 401, "not_authenticated")
    assert_error(intruder.patch("/me", {"name": "Intruder"}), 401, "not_authenticated")
    assert account.browser.get("/me").get_json()["user"]["name"] == account.user["name"]


def test_a_disabled_account_is_shut_out_of_its_open_sessions(app, database, outbox):
    account = sign_up(app)
    database.run("UPDATE users SET disabled_at = now()")

    assert_error(account.browser.get("/me"), 403, "account_disabled")
    assert_error(account.browser.patch("/me", {"name": "X"}), 403, "account_disabled")
    assert_error(account.browser.post("/auth/refresh"), 403, "account_disabled")


# ---------------------------------------------------------------- A5 refresh


def test_refresh_returns_the_user_and_replaces_all_three_cookies(app, outbox):
    account = sign_up(app)
    browser = account.browser
    before = {name: browser.cookie(name) for name in SESSION_COOKIES}

    res = browser.post("/auth/refresh")

    assert res.status_code == 200
    assert res.mimetype == "application/json"
    assert res.get_json() == {"user": account.user}
    after = {name: browser.cookie(name) for name in SESSION_COOKIES}
    assert all(after[name] and after[name] != before[name] for name in SESSION_COOKIES)
    assert is_logged_in(browser)
    # Same login, so logging out later ends every token that came from it.
    assert claims_of(app, after[REFRESH])["sid"] == claims_of(app, before[REFRESH])["sid"]


def test_refresh_can_be_repeated_with_each_new_token(app, outbox):
    account = sign_up(app)
    for _ in range(3):
        assert account.browser.post("/auth/refresh").status_code == 200
    assert is_logged_in(account.browser)


def test_refresh_works_when_the_access_token_has_expired_or_is_gone(app, outbox):
    account = sign_up(app)
    expire_access_token(account.browser)
    assert account.browser.post("/auth/refresh").status_code == 200
    account.browser.drop_cookie(ACCESS)
    assert account.browser.post("/auth/refresh").status_code == 200
    assert is_logged_in(account.browser)


def test_refresh_without_a_refresh_cookie_is_not_authenticated(app, outbox):
    assert_error(Browser(app).post("/auth/refresh"), 401, "not_authenticated")
    account = sign_up(app)
    account.browser.drop_cookie(REFRESH)
    assert_error(account.browser.post("/auth/refresh"), 401, "not_authenticated")


@pytest.mark.parametrize(
    "how", ["garbage", "signed with another key", "unsigned", "access token", "unknown id"]
)
def test_refresh_refuses_a_token_that_is_not_a_valid_refresh_token(app, outbox, how):
    account = sign_up(app)
    token = account.browser.cookie(REFRESH)
    forged_token = {
        "garbage": lambda: "not-a-token",
        "signed with another key": lambda: resign(app, token, key="k" * 40),
        "unsigned": lambda: jwt.encode(claims_of(app, token), None, algorithm="none"),
        "access token": lambda: account.browser.cookie(ACCESS),
        "unknown id": lambda: resign(app, token, jti="never-issued"),
    }[how]()
    intruder = Browser(app)
    intruder.set_cookie(REFRESH, forged_token)

    res = intruder.post("/auth/refresh")

    assert_error(res, 401, "not_authenticated")
    assert_sets_no_session_cookies(res)
    # The real session is unharmed.
    assert account.browser.post("/auth/refresh").status_code == 200


def test_a_refresh_token_stops_working_after_30_days(app, database, outbox):
    account = sign_up(app)
    stored = database.one("SELECT expires_at, created_at FROM refresh_tokens WHERE revoked_at IS NULL")
    assert abs(stored["expires_at"] - datetime.now(UTC) - timedelta(days=30)) < timedelta(minutes=2)

    # The server's own record says it has run out, whatever the token claims.
    database.run("UPDATE refresh_tokens SET expires_at = now() - interval '1 second'")
    res = account.browser.post("/auth/refresh")

    assert_error(res, 401, "not_authenticated")
    assert_sets_no_session_cookies(res)


def test_a_refresh_token_past_its_own_expiry_is_refused(app, outbox):
    account = sign_up(app)
    past = datetime.now(UTC) - timedelta(seconds=5)
    stale = Browser(app)
    stale.set_cookie(REFRESH, resign(app, account.browser.cookie(REFRESH), exp=past))

    assert_error(stale.post("/auth/refresh"), 401, "not_authenticated")


def test_reusing_an_exchanged_refresh_token_revokes_that_whole_login(app, database, outbox):
    """A copied token: once it is used after the real browser has moved on, everybody is out."""
    account = sign_up(app)
    browser = account.browser
    other_login = log_in(app, account)
    thief = copy_of(browser)
    assert browser.post("/auth/refresh").status_code == 200
    assert browser.post("/auth/refresh").status_code == 200
    make_rotated_tokens_older_than_the_grace_window(database)
    second_tab = copy_of(browser)

    res = thief.post("/auth/refresh")

    assert_error(res, 401, "not_authenticated")
    assert_sets_no_session_cookies(res)
    # The newest tokens of that login are dead too: refresh and, at once, access.
    assert_error(browser.get("/me"), 401, "not_authenticated")
    assert_error(second_tab.post("/auth/refresh"), 401, "not_authenticated")
    assert database.value("SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NULL") == 1
    # "All sessions of that login": the same user's separate login elsewhere goes on.
    assert is_logged_in(other_login)
    assert other_login.post("/auth/refresh").status_code == 200


def test_the_real_browser_reusing_its_old_token_after_theft_also_revokes_the_login(app, database, outbox):
    """The other order: the thief refreshed first, then the owner's now-old token arrives."""
    account = sign_up(app)
    thief = copy_of(account.browser)
    assert thief.post("/auth/refresh").status_code == 200
    thiefs_other_tab = copy_of(thief)
    make_rotated_tokens_older_than_the_grace_window(database)

    assert_error(account.browser.post("/auth/refresh"), 401, "not_authenticated")

    assert_error(thief.get("/me"), 401, "not_authenticated")
    assert_error(thiefs_other_tab.post("/auth/refresh"), 401, "not_authenticated")


def test_two_tabs_refreshing_at_once_does_not_revoke_the_login(app, outbox):
    """Within 10 seconds of the exchange the loser gets 401, and the winner's tokens keep working."""
    account = sign_up(app)
    winner = account.browser
    loser = copy_of(winner)
    assert winner.post("/auth/refresh").status_code == 200

    res = loser.post("/auth/refresh")

    assert_error(res, 401, "not_authenticated")
    assert is_logged_in(winner)
    assert winner.post("/auth/refresh").status_code == 200
    assert is_logged_in(winner)


def test_the_tab_that_loses_the_refresh_race_does_not_log_the_browser_out(app, outbox):
    """Both tabs share one cookie jar. If the loser's 401 clears the cookies, it wipes the
    pair the winner has just been given and the user is logged out after all."""
    account = sign_up(app)
    browser = account.browser
    before = {name: browser.cookie(name) for name in SESSION_COOKIES}
    assert browser.post("/auth/refresh").status_code == 200  # tab 1 wins
    fresh = {name: browser.cookie(name) for name in SESSION_COOKIES}

    # Tab 2's request left with the old cookies; its answer lands in the same jar afterwards.
    for name in SESSION_COOKIES:
        browser.set_cookie(name, before[name])
    res = browser.post("/auth/refresh")
    assert_error(res, 401, "not_authenticated")
    cleared = sorted(name for name, morsel in set_cookies(res).items() if not morsel.value)

    assert cleared == [], f"the losing tab's answer deletes {cleared}, logging the browser out"
    for name in SESSION_COOKIES:
        browser.set_cookie(name, fresh[name])
    assert is_logged_in(browser)


def reuse_warnings(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == "refresh token reuse"]


def test_reuse_of_an_exchanged_token_clears_the_cookies_and_is_logged_once(app, database, outbox, caplog):
    """The warning is what an alarm will watch, so it names the user and the login and is
    written once per theft, not once per request."""
    account = sign_up(app)
    thief = copy_of(account.browser)
    assert account.browser.post("/auth/refresh").status_code == 200
    make_rotated_tokens_older_than_the_grace_window(database)
    stolen = {name: thief.cookie(name) for name in SESSION_COOKIES}

    with caplog.at_level(logging.WARNING):
        first = thief.post("/auth/refresh")
        for name in SESSION_COOKIES:
            thief.set_cookie(name, stolen[name])
        second = thief.post("/auth/refresh")

    for res in (first, second):
        assert_error(res, 401, "not_authenticated")
        assert_clears_session_cookies(res)
    (warning,) = reuse_warnings(caplog)
    assert warning.levelno == logging.WARNING
    assert warning.user_id == account.user["id"]
    assert warning.family == claims_of(app, stolen[REFRESH])["sid"]


@pytest.mark.parametrize("ended_by", ["logout", "a password change elsewhere", "a password reset"])
def test_refreshing_a_login_that_was_ended_is_a_plain_401_and_not_a_reuse_warning(
    app, database, outbox, caplog, ended_by
):
    """A browser left with the cookies of a login that was ended on purpose is not a thief
    (QB2-013). It is told to log in, its dead cookies are removed, and nothing else changes."""
    account = sign_up(app)
    left_behind = copy_of(account.browser)  # still holds the cookies of the login about to end
    other_login = log_in(app, account)
    new_password = PASSWORD
    if ended_by == "logout":
        assert account.browser.post("/auth/logout").status_code == 204
    elif ended_by == "a password change elsewhere":
        new_password = NEW_PASSWORD
        changed = other_login.post("/me/password", {"currentPassword": PASSWORD, "newPassword": new_password})
        assert changed.status_code == 204
    else:
        new_password = NEW_PASSWORD
        assert Browser(app).post("/auth/forgot-password", {"email": account.email}).status_code == 204
        token = last_token(app, account.email, "reset")
        res = Browser(app).post("/auth/reset-password", {"token": token, "password": new_password})
        assert res.status_code == 204
    make_rotated_tokens_older_than_the_grace_window(database)
    active_before = database.value("SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NULL")

    with caplog.at_level(logging.WARNING):
        res = left_behind.post("/auth/refresh")

    assert_error(res, 401, "not_authenticated")
    assert_clears_session_cookies(res)
    assert reuse_warnings(caplog) == []
    # No other login of the user is touched by it.
    assert database.value("SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NULL") == active_before
    if ended_by != "a password reset":  # a reset ends every login, this one included
        assert is_logged_in(other_login)
    assert log_in(app, account, password=new_password).get("/me").status_code == 200


def test_refresh_is_refused_for_a_deleted_account(app, database, outbox):
    account = sign_up(app)
    # Deleted behind the session's back (as the purge or an admin would), tokens untouched.
    database.run("UPDATE users SET deleted_at = now()")
    res = account.browser.post("/auth/refresh")
    assert_error(res, 401, "not_authenticated")
    assert_sets_no_session_cookies(res)
    assert_error(account.browser.get("/me"), 401, "not_authenticated")


# ---------------------------------------------------------------- A6 logout


def test_logout_ends_this_login_at_once_and_clears_the_cookies(app, outbox):
    account = sign_up(app)
    browser = account.browser
    elsewhere = log_in(app, account)
    copied = copy_of(browser)

    res = browser.post("/auth/logout")

    assert res.status_code == 204
    assert res.get_data() == b""
    assert_clears_session_cookies(res)
    assert [browser.cookie(name) for name in SESSION_COOKIES] == [None, None, None]
    assert_error(browser.get("/me"), 401, "not_authenticated")
    # Copies of the old tokens are useless immediately, not in 15 minutes.
    assert_error(copied.get("/me"), 401, "not_authenticated")
    assert_error(copied.post("/auth/refresh"), 401, "not_authenticated")
    # Only this browser was logged out.
    assert is_logged_in(elsewhere)
    assert elsewhere.post("/auth/refresh").status_code == 200


def test_logout_covers_tokens_from_before_a_refresh(app, outbox):
    account = sign_up(app)
    old = copy_of(account.browser)
    assert account.browser.post("/auth/refresh").status_code == 200

    assert account.browser.post("/auth/logout").status_code == 204

    assert_error(old.get("/me"), 401, "not_authenticated")


def test_logout_needs_a_login(app):
    assert_error(Browser(app).post("/auth/logout"), 401, "not_authenticated")
