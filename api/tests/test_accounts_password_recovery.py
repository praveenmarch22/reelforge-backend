"""Forgotten passwords: docs/API_DESIGN.md A7 and A8."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from .support import (
    NEW_PASSWORD,
    PASSWORD,
    SESSION_COOKIES,
    Browser,
    assert_clears_session_cookies,
    assert_error,
    assert_invalid,
    assert_sets_no_session_cookies,
    last_token,
    link_token,
    log_in,
    new_email,
    sign_up,
)

pytestmark = pytest.mark.usefixtures("clean_state")


def can_log_in(app, email: str, password: str) -> bool:
    return Browser(app).post("/auth/login", {"email": email, "password": password}).status_code == 200


def reset_link_for(app, email: str) -> str:
    assert Browser(app).post("/auth/forgot-password", {"email": email}).status_code == 204
    return last_token(app, email, "reset")


# ---------------------------------------------------------------- A7 forgot password


def test_forgot_password_emails_a_one_hour_reset_link_and_stores_only_its_hash(app, database, outbox):
    account = sign_up(app)
    outbox.clear()

    res = Browser(app).post("/auth/forgot-password", {"email": account.email})

    assert res.status_code == 204
    assert res.get_data() == b""
    assert_sets_no_session_cookies(res)
    assert [message.to for message in outbox] == [account.email]
    token = link_token(app, outbox[0], "reset")
    assert f"http://localhost:5173/reset?token={token}" in outbox[0].text

    stored = database.one("SELECT * FROM auth_tokens WHERE kind = 'reset_password'")
    assert stored["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in " ".join(str(value) for value in stored.values())
    assert abs(stored["expires_at"] - datetime.now(UTC) - timedelta(hours=1)) < timedelta(minutes=2)
    # Asking changes nothing until the link is used.
    assert can_log_in(app, account.email, PASSWORD)


def test_forgot_password_answers_the_same_for_an_unknown_email_and_sends_nothing(app, database, outbox):
    account = sign_up(app)
    outbox.clear()

    known = Browser(app).post("/auth/forgot-password", {"email": account.email})
    unknown = Browser(app).post("/auth/forgot-password", {"email": new_email()})

    assert (unknown.status_code, unknown.get_data()) == (known.status_code, known.get_data()) == (204, b"")
    assert sorted(key for key, _ in unknown.headers) == sorted(key for key, _ in known.headers)
    assert [message.to for message in outbox] == [account.email]
    assert database.value("SELECT count(*) FROM auth_tokens WHERE kind = 'reset_password'") == 1


@pytest.mark.parametrize("state", ["disabled", "deleted"])
def test_forgot_password_sends_nothing_for_a_disabled_or_deleted_account(app, database, outbox, state):
    account = sign_up(app)
    database.run(f"UPDATE users SET {state}_at = now()")  # noqa: S608 - a fixed column name
    outbox.clear()

    res = Browser(app).post("/auth/forgot-password", {"email": account.email})

    assert (res.status_code, res.get_data()) == (204, b"")
    assert outbox == []


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "email"),
        ({"email": 7}, "email"),
        ({"email": "not-an-email"}, "email"),
        ({"email": "asha@example.com", "name": "x"}, "name"),
    ],
)
def test_forgot_password_validates_its_body(app, outbox, body, field):
    assert_invalid(Browser(app).post("/auth/forgot-password", body), field)
    assert outbox == []


# ---------------------------------------------------------------- A8 reset password


def test_reset_sets_the_new_password_and_revokes_every_session(app, outbox):
    account = sign_up(app)
    laptop, phone = account.browser, log_in(app, account)
    first_copy, second_copy = (Browser(app).copy_session_from(laptop) for _ in range(2))
    token = reset_link_for(app, account.email)

    res = laptop.post("/auth/reset-password", {"token": token, "password": NEW_PASSWORD})

    assert res.status_code == 204
    assert res.get_data() == b""
    assert_clears_session_cookies(res)
    assert [laptop.cookie(name) for name in SESSION_COOKIES] == [None, None, None]
    # Every login is over at once, access tokens included.
    assert_error(phone.post("/auth/refresh"), 401, "not_authenticated")
    assert_error(first_copy.post("/auth/refresh"), 401, "not_authenticated")
    assert_error(second_copy.get("/me"), 401, "not_authenticated")
    assert not can_log_in(app, account.email, PASSWORD)
    assert can_log_in(app, account.email, NEW_PASSWORD)


def test_reset_also_ends_the_access_tokens_of_other_browsers_immediately(app, outbox):
    account = sign_up(app)
    phone = log_in(app, account)
    token = reset_link_for(app, account.email)

    assert (
        Browser(app).post("/auth/reset-password", {"token": token, "password": NEW_PASSWORD}).status_code
        == 204
    )

    assert_error(phone.get("/me"), 401, "not_authenticated")
    assert_error(account.browser.get("/me"), 401, "not_authenticated")


def test_a_reset_link_works_once(app, outbox):
    account = sign_up(app)
    token = reset_link_for(app, account.email)
    assert (
        Browser(app).post("/auth/reset-password", {"token": token, "password": NEW_PASSWORD}).status_code
        == 204
    )

    res = Browser(app).post("/auth/reset-password", {"token": token, "password": "third-password-here"})

    assert_error(res, 410, "token_invalid")
    assert can_log_in(app, account.email, NEW_PASSWORD)
    assert not can_log_in(app, account.email, "third-password-here")


def test_a_reset_link_expires_after_an_hour(app, database, outbox):
    account = sign_up(app)
    token = reset_link_for(app, account.email)
    database.run(
        "UPDATE auth_tokens SET expires_at = now() - interval '1 second' WHERE kind = 'reset_password'"
    )

    res = Browser(app).post("/auth/reset-password", {"token": token, "password": NEW_PASSWORD})

    assert_error(res, 410, "token_invalid")
    assert can_log_in(app, account.email, PASSWORD)
    assert account.browser.get("/me").status_code == 200, "a refused reset must not end sessions"


def test_a_newer_reset_link_replaces_the_older_one(app, outbox):
    account = sign_up(app)
    first = reset_link_for(app, account.email)
    second = reset_link_for(app, account.email)
    assert first != second

    assert_error(
        Browser(app).post("/auth/reset-password", {"token": first, "password": NEW_PASSWORD}),
        410,
        "token_invalid",
    )
    assert (
        Browser(app).post("/auth/reset-password", {"token": second, "password": NEW_PASSWORD}).status_code
        == 204
    )


def test_a_verification_token_cannot_reset_a_password(app, outbox):
    account = sign_up(app, verified=False)
    verify_token = last_token(app, account.email, "verify")

    res = Browser(app).post("/auth/reset-password", {"token": verify_token, "password": NEW_PASSWORD})

    assert_error(res, 410, "token_invalid")
    assert can_log_in(app, account.email, PASSWORD)
    assert Browser(app).post("/auth/verify-email", {"token": verify_token}).status_code == 200


def test_an_unknown_reset_token_is_refused(app):
    res = Browser(app).post("/auth/reset-password", {"token": "nope", "password": NEW_PASSWORD})
    assert_error(res, 410, "token_invalid")


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"password": "short"}, "password"),
        ({"password": "p" * 129}, "password"),
        ({"password": "qwertyuiop"}, "password"),
        ({"password": 12345678}, "password"),
        ({"password": None}, "password"),
        ({"token": None}, "token"),
        ({"token": 5}, "token"),
        ({"email": "someone-else@example.com"}, "email"),
    ],
)
def test_reset_validates_its_body_without_using_the_link_up(app, outbox, change, field):
    account = sign_up(app)
    token = reset_link_for(app, account.email)
    body = {**{"token": token, "password": NEW_PASSWORD}, **change}
    body = {key: value for key, value in body.items() if value is not None}

    assert_invalid(Browser(app).post("/auth/reset-password", body), field)

    assert can_log_in(app, account.email, PASSWORD)
    assert (
        Browser(app).post("/auth/reset-password", {"token": token, "password": NEW_PASSWORD}).status_code
        == 204
    )


def test_a_reset_link_requested_before_a_password_change_stops_working(app, outbox):
    """Otherwise a link sitting in a mailbox someone else can read would undo the change."""
    account = sign_up(app)
    token = reset_link_for(app, account.email)
    changed = account.browser.post("/me/password", {"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD})
    assert changed.status_code == 204

    res = Browser(app).post("/auth/reset-password", {"token": token, "password": "third-password-here"})

    assert_error(res, 410, "token_invalid")
    assert can_log_in(app, account.email, NEW_PASSWORD)


def test_a_reset_link_of_a_deleted_account_is_refused(app, outbox):
    account = sign_up(app)
    token = reset_link_for(app, account.email)
    assert account.browser.delete("/me", {"password": PASSWORD}).status_code == 204

    res = Browser(app).post("/auth/reset-password", {"token": token, "password": NEW_PASSWORD})

    assert_error(res, 410, "token_invalid")
    assert not can_log_in(app, account.email, NEW_PASSWORD)


def test_resetting_does_not_log_the_browser_in(app, outbox):
    account = sign_up(app)
    browser = Browser(app)
    token = reset_link_for(app, account.email)

    res = browser.post("/auth/reset-password", {"token": token, "password": NEW_PASSWORD})

    assert res.status_code == 204
    assert_sets_no_session_cookies(res)
    assert_error(browser.get("/me"), 401, "not_authenticated")
