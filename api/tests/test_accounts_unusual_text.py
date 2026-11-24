"""Text that JSON allows but the database or the password hasher cannot store: a NUL
character, and half of a surrogate pair (what a cut-in-half emoji looks like on the wire).

docs/API_DESIGN.md, Conventions: a body the API can't accept is 400 validation_error with the
field named in details.fields. Whatever the answer, it must never be a 500.
"""

from __future__ import annotations

import pytest

from .support import (
    NEW_PASSWORD,
    PASSWORD,
    Browser,
    assert_error,
    assert_invalid,
    last_token,
    new_email,
    sign_up,
)

pytestmark = pytest.mark.usefixtures("clean_state")

# Written as JSON escapes; the bodies below are sent as raw JSON text.
NUL = "\\u0000"
HALF_EMOJI = "\\ud83d"
both = pytest.mark.parametrize("odd", [NUL, HALF_EMOJI], ids=["NUL", "half an emoji"])


def post_raw(browser: Browser, method: str, path: str, text: str):
    return browser.call(method, path, data=text, content_type="application/json")


def assert_refused_not_crashed(res, *allowed: tuple[int, str]) -> None:
    assert res.status_code != 500, f"the API crashed: {res.get_data(as_text=True)}"
    body = res.get_json()
    assert (res.status_code, body["error"]["code"]) in allowed, res.get_data(as_text=True)


def can_log_in(app, email: str, password: str) -> bool:
    return Browser(app).post("/auth/login", {"email": email, "password": password}).status_code == 200


# ---------------------------------------------------------------- login


@both
def test_login_with_unusual_text_in_the_email_is_a_validation_error(app, odd):
    """No login needed for this one, so anybody on the internet can send it."""
    res = post_raw(
        Browser(app), "POST", "/auth/login", f'{{"email":"asha{odd}@example.com","password":"{PASSWORD}"}}'
    )
    assert_invalid(res, "email")


@both
def test_login_with_unusual_text_in_the_password_is_refused_not_crashed(app, outbox, odd):
    account = sign_up(app)
    res = post_raw(
        Browser(app), "POST", "/auth/login", f'{{"email":"{account.email}","password":"{odd}{PASSWORD}"}}'
    )
    assert_refused_not_crashed(res, (400, "validation_error"), (401, "invalid_credentials"))


# ---------------------------------------------------------------- sign-up


@both
@pytest.mark.parametrize("field", ["email", "name", "password"])
def test_signup_with_unusual_text_is_a_validation_error(app, database, outbox, field, odd):
    values = {"email": new_email(), "password": PASSWORD, "name": "Asha"}
    values[field] = odd + values[field]
    body = "{" + ",".join(f'"{key}":"{value}"' for key, value in values.items()) + "}"

    res = post_raw(Browser(app), "POST", "/auth/signup", body)

    assert_invalid(res, field)
    assert database.value("SELECT count(*) FROM users") == 0
    assert outbox == []


@both
def test_forgot_password_with_unusual_text_in_the_email_is_a_validation_error(app, outbox, odd):
    res = post_raw(Browser(app), "POST", "/auth/forgot-password", f'{{"email":"asha{odd}@example.com"}}')
    assert_invalid(res, "email")
    assert outbox == []


# ---------------------------------------------------------------- profile


@both
@pytest.mark.parametrize(
    ("body", "field"),
    [
        ('{{"name":"Asha {odd}"}}', "name"),
        ('{{"niche":"Fitness {odd}"}}', "niche"),
        ('{{"defaults":{{"voiceId":"voice-{odd}"}}}}', "defaults.voiceId"),
    ],
    ids=["name", "niche", "defaults.voiceId"],
)
def test_profile_text_with_unusual_text_is_a_validation_error(app, outbox, body, field, odd):
    account = sign_up(app)

    res = post_raw(account.browser, "PATCH", "/me", body.format(odd=odd))

    assert_invalid(res, field)
    assert account.browser.get("/me").get_json()["user"] == account.user


# ---------------------------------------------------------------- passwords


@both
def test_a_new_password_with_unusual_text_is_a_validation_error(app, outbox, odd):
    account = sign_up(app)

    res = post_raw(
        account.browser,
        "POST",
        "/me/password",
        f'{{"currentPassword":"{PASSWORD}","newPassword":"{odd}{NEW_PASSWORD}"}}',
    )

    assert_invalid(res, "newPassword")
    assert can_log_in(app, account.email, PASSWORD)


@both
def test_a_reset_password_with_unusual_text_is_a_validation_error_and_keeps_the_link(app, outbox, odd):
    account = sign_up(app)
    assert Browser(app).post("/auth/forgot-password", {"email": account.email}).status_code == 204
    token = last_token(app, account.email, "reset")

    res = post_raw(
        Browser(app),
        "POST",
        "/auth/reset-password",
        f'{{"token":"{token}","password":"{odd}{NEW_PASSWORD}"}}',
    )

    assert_invalid(res, "password")
    assert can_log_in(app, account.email, PASSWORD)
    res = Browser(app).post("/auth/reset-password", {"token": token, "password": NEW_PASSWORD})
    assert res.status_code == 204, "the link was used up by the refused attempt"


@both
def test_a_current_password_with_unusual_text_is_refused_not_crashed(app, database, outbox, odd):
    """What the user typed as their current password is only ever compared, never stored."""
    account = sign_up(app)

    changed = post_raw(
        account.browser,
        "POST",
        "/me/password",
        f'{{"currentPassword":"{odd}{PASSWORD}","newPassword":"{NEW_PASSWORD}"}}',
    )
    deleted = post_raw(account.browser, "DELETE", "/me", f'{{"password":"{odd}{PASSWORD}"}}')

    for res in (changed, deleted):
        assert_refused_not_crashed(res, (400, "validation_error"), (401, "invalid_credentials"))
    assert can_log_in(app, account.email, PASSWORD)
    assert database.value("SELECT deleted_at FROM users") is None


# ---------------------------------------------------------------- link tokens


@both
def test_link_tokens_with_unusual_text_are_refused_not_crashed(app, odd):
    verify = post_raw(Browser(app), "POST", "/auth/verify-email", f'{{"token":"abc{odd}"}}')
    assert_refused_not_crashed(verify, (400, "validation_error"), (410, "token_invalid"))
    reset = post_raw(
        Browser(app), "POST", "/auth/reset-password", f'{{"token":"abc{odd}","password":"{PASSWORD}"}}'
    )
    assert_refused_not_crashed(reset, (400, "validation_error"), (410, "token_invalid"))


# ---------------------------------------------------------------- other control characters, size


def test_control_characters_in_one_line_text_are_refused(app, outbox):
    account = sign_up(app)
    for body in ('{"name":"Asha\\nRao"}', '{"name":"Asha\\tRao"}', '{"niche":"Fit\\u0007ness"}'):
        res = post_raw(account.browser, "PATCH", "/me", body)
        assert_error(res, 400, "validation_error")
    assert account.browser.get("/me").get_json()["user"] == account.user


def test_a_body_over_one_megabyte_is_refused(app, database):
    body = '{"email":"big@example.com","password":"' + PASSWORD + '","name":"' + "x" * 1_100_000 + '"}'
    res = post_raw(Browser(app), "POST", "/auth/signup", body)
    assert_error(res, 413, "payload_too_large")
    assert database.value("SELECT count(*) FROM users") == 0
