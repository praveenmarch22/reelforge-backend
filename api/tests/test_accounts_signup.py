"""Sign-up and email verification: docs/API_DESIGN.md A1, A2, A3."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from .support import (
    ACCESS,
    NEW_PASSWORD,
    PASSWORD,
    Browser,
    assert_error,
    assert_invalid,
    assert_sets_no_session_cookies,
    last_token,
    link_token,
    log_in,
    mails_to,
    new_user_json,
    sign_up,
)

pytestmark = pytest.mark.usefixtures("clean_state")

VALID = {"email": "asha@example.com", "password": PASSWORD, "name": "Asha Rao"}


def close_to(moment: datetime, expected: datetime) -> bool:
    return abs(moment - expected) < timedelta(minutes=2)


# ---------------------------------------------------------------- A1 sign-up


def test_signup_returns_the_user_object_and_logs_the_browser_in(app, outbox):
    browser = Browser(app)
    res = browser.post("/auth/signup", VALID)

    assert res.status_code == 201
    body = res.get_json()
    assert set(body) == {"user"}
    uuid.UUID(body["user"]["id"])
    assert body["user"] == new_user_json(body["user"]["id"], "asha@example.com", "Asha Rao")
    # Logged in, though not verified yet.
    me = browser.get("/me")
    assert me.status_code == 200
    assert me.get_json() == body


def test_signup_grants_the_monthly_credits_exactly_once(app, database, outbox):
    account = sign_up(app, verified=False)
    user_id = account.user["id"]

    credits = database.one("SELECT * FROM credit_accounts WHERE user_id = :id", id=user_id)
    assert (credits["balance"], credits["monthly_allowance"]) == (100, 100)
    now = datetime.now(UTC)
    next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
    assert credits["resets_at"] == next_month.replace(hour=0, minute=0, second=0, microsecond=0)

    # Everything else a new user does must not grant again.
    account.browser.post("/auth/resend-verification")
    account.browser.post("/auth/verify-email", {"token": last_token(app, account.email, "verify")})
    account.browser.post("/auth/refresh")
    log_in(app, account)

    ledger = database.rows("SELECT amount, kind FROM credit_ledger WHERE user_id = :id", id=user_id)
    assert ledger == [{"amount": 100, "kind": "grant"}]
    assert database.value("SELECT balance FROM credit_accounts WHERE user_id = :id", id=user_id) == 100
    assert account.browser.get("/me").get_json()["user"]["credits"]["balance"] == 100


def test_signup_emails_a_verification_link_and_stores_only_its_hash(app, database, outbox):
    res = Browser(app).post("/auth/signup", VALID)

    assert [message.to for message in outbox] == ["asha@example.com"]
    token = link_token(app, outbox[0], "verify")
    assert f"http://localhost:5173/verify?token={token}" in outbox[0].text
    assert token not in res.get_data(as_text=True), "the link token must only travel by email"

    stored = database.one("SELECT * FROM auth_tokens")
    assert stored["kind"] == "verify_email"
    assert stored["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in " ".join(str(value) for value in stored.values())
    assert stored["used_at"] is None
    assert close_to(stored["expires_at"], datetime.now(UTC) + timedelta(hours=24))


def test_the_password_is_stored_as_an_argon2id_hash_and_never_returned(app, database):
    res = Browser(app).post("/auth/signup", VALID)

    stored = database.value("SELECT password_hash FROM users")
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored
    text = res.get_data(as_text=True)
    assert PASSWORD not in text and stored not in text and "password" not in text.lower()


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"email": None}, "email"),
        ({"password": None}, "password"),
        ({"name": None}, "name"),
        ({"email": "not-an-email"}, "email"),
        ({"email": "asha@"}, "email"),
        ({"email": 42}, "email"),
        ({"email": "a" * 250 + "@example.com"}, "email"),
        ({"password": "seven77"}, "password"),
        ({"password": "p" * 129}, "password"),
        ({"password": "password123"}, "password"),
        ({"password": "PassWord123"}, "password"),
        ({"password": 12345678}, "password"),
        ({"password": ["correct-horse-battery"]}, "password"),
        ({"name": ""}, "name"),
        ({"name": "   "}, "name"),
        ({"name": "n" * 81}, "name"),
        ({"name": 7}, "name"),
        ({"role": "admin"}, "role"),
        ({"isAdmin": True}, "isAdmin"),
        ({"emailVerified": True}, "emailVerified"),
    ],
)
def test_signup_refuses_invalid_details(app, database, outbox, change, field):
    body = {key: value for key, value in {**VALID, **change}.items() if value is not None}
    res = Browser(app).post("/auth/signup", body)

    assert_invalid(res, field)
    assert_sets_no_session_cookies(res)
    assert database.value("SELECT count(*) FROM users") == 0
    assert outbox == []


@pytest.mark.parametrize(
    "send",
    [
        {},
        {"data": "[1, 2]", "content_type": "application/json"},
        {"data": '"text"', "content_type": "application/json"},
        {"data": "{not json", "content_type": "application/json"},
    ],
    ids=["no body", "a list", "a string", "broken JSON"],
)
def test_signup_refuses_a_body_that_is_not_a_json_object(app, database, send):
    res = Browser(app).post("/auth/signup", **send)

    assert_error(res, 400, "validation_error")
    assert database.value("SELECT count(*) FROM users") == 0


AS_JSON = '{"email":"asha@example.com","password":"correct-horse-battery","name":"Asha Rao"}'


@pytest.mark.parametrize(
    "send",
    [
        {"data": "email=asha@example.com", "content_type": "application/x-www-form-urlencoded"},
        {"data": {"email": "asha@example.com"}, "content_type": "multipart/form-data"},
        {"data": AS_JSON, "content_type": "text/plain"},
        {"data": AS_JSON},
    ],
    ids=["a form", "a multipart form", "JSON sent as plain text", "JSON with no content type"],
)
def test_signup_refuses_a_body_that_is_not_sent_as_json(app, database, send):
    """docs/API_DESIGN.md, error codes: `415 unsupported_media_type`, "A body that isn't JSON"."""
    res = Browser(app).post("/auth/signup", **send)

    assert_error(res, 415, "unsupported_media_type")
    assert database.value("SELECT count(*) FROM users") == 0


def test_every_endpoint_that_reads_a_body_refuses_a_form_the_same_way(app, outbox):
    account = sign_up(app)
    form = {"data": "name=Asha", "content_type": "application/x-www-form-urlencoded"}

    for path in ("/auth/login", "/auth/verify-email", "/auth/forgot-password", "/auth/reset-password"):
        assert_error(Browser(app).post(path, **form), 415, "unsupported_media_type")
    assert_error(account.browser.patch("/me", **form), 415, "unsupported_media_type")
    assert_error(account.browser.post("/me/password", **form), 415, "unsupported_media_type")
    assert_error(account.browser.delete("/me", **form), 415, "unsupported_media_type")
    assert account.browser.get("/me").get_json()["user"] == account.user


def test_json_with_a_charset_in_its_content_type_is_read(app, outbox):
    res = Browser(app).post("/auth/signup", data=AS_JSON, content_type="application/json; charset=utf-8")
    assert res.status_code == 201


@pytest.mark.parametrize(
    "email", ["not-an-email", "asha@", "a@b", "", "asha@reelforge.test", "a b@example.com"]
)
def test_an_invalid_email_is_explained_in_plain_words(app, outbox, email):
    """Sign-up and forgot-password check the address the same way and say the same thing
    (QB2-002: these used to be a 500)."""
    signup = Browser(app).post("/auth/signup", {**VALID, "email": email})
    forgot = Browser(app).post("/auth/forgot-password", {"email": email})

    for res in (signup, forgot):
        assert assert_invalid(res, "email") == {"email": "Enter a valid email address"}
    assert outbox == []


@pytest.mark.parametrize(
    "email",
    [
        "Asha Rao <asha@example.com>",
        "<asha@example.com>",
        '"Asha Rao" <asha@example.com>',
        '"asha rao"@example.com',
        " asha@example.com",
        "asha@example.com ",
        "asha@example.com\n",
        "\tasha@example.com",
        "asha@example.com, other@example.com",
    ],
)
def test_only_a_bare_address_is_accepted_as_an_email(app, database, outbox, email):
    """The email library also reads `Asha Rao <asha@example.com>` and keeps the address, but
    login compares the text as typed, so an account made that way could not be logged in to
    with what the person typed (BUG-030). Spaces around the address are refused too: the web
    app removes them before sending."""
    account = sign_up(app, email="asha@example.com")
    outbox.clear()

    signup = Browser(app).post("/auth/signup", {**VALID, "email": email})
    forgot = Browser(app).post("/auth/forgot-password", {"email": email})

    for res in (signup, forgot):
        assert assert_invalid(res, "email") == {"email": "Enter a valid email address"}
    assert outbox == [], "a reset email was sent to the address inside the text"
    assert database.value("SELECT count(*) FROM users") == 1
    assert account.browser.get("/me").status_code == 200


@pytest.mark.parametrize(
    "change",
    [{"password": "8letters"}, {"password": "p4" * 64}, {"name": "A"}, {"name": "n" * 80}],
    ids=["password of 8", "password of 128", "name of 1", "name of 80"],
)
def test_signup_accepts_values_at_the_limits(app, change):
    body = {**VALID, **change}
    browser = Browser(app)
    assert browser.post("/auth/signup", body).status_code == 201
    # The password works exactly as typed.
    assert (
        Browser(app).post("/auth/login", {"email": body["email"], "password": body["password"]}).status_code
        == 200
    )


@pytest.mark.parametrize("second_email", ["asha@example.com", "ASHA@Example.COM"])
def test_signing_up_twice_with_one_email_is_refused(app, database, outbox, second_email):
    assert Browser(app).post("/auth/signup", VALID).status_code == 201

    res = Browser(app).post("/auth/signup", {**VALID, "email": second_email, "password": NEW_PASSWORD})

    assert_error(res, 409, "email_taken")
    assert_sets_no_session_cookies(res)
    assert database.value("SELECT count(*) FROM users") == 1
    assert database.value("SELECT count(*) FROM credit_ledger") == 1
    assert len(outbox) == 1
    # The first account is untouched: its password still works, the second one doesn't.
    assert (
        Browser(app).post("/auth/login", {"email": VALID["email"], "password": PASSWORD}).status_code == 200
    )
    assert (
        Browser(app).post("/auth/login", {"email": VALID["email"], "password": NEW_PASSWORD}).status_code
        == 401
    )


def test_email_addresses_are_case_insensitive(app, outbox):
    assert Browser(app).post("/auth/signup", {**VALID, "email": "Asha.Rao@Example.COM"}).status_code == 201

    for typed in ("asha.rao@example.com", "ASHA.RAO@EXAMPLE.COM"):
        res = Browser(app).post("/auth/login", {"email": typed, "password": PASSWORD})
        assert res.status_code == 200, typed
    assert Browser(app).post("/auth/forgot-password", {"email": "ASHA.RAO@example.com"}).status_code == 204
    assert len(mails_to("asha.rao@example.com")) == 2  # verification and reset


# ---------------------------------------------------------------- A2 verify email


def test_verifying_marks_the_email_verified_and_signs_in_any_browser(app, database, outbox):
    account = sign_up(app, verified=False)
    token = last_token(app, account.email, "verify")
    other_device = Browser(app)

    res = other_device.post("/auth/verify-email", {"token": token})

    assert res.status_code == 200
    assert set(res.get_json()) == {"user"}
    assert res.get_json()["user"] == {**account.user, "emailVerified": True}
    assert other_device.get("/me").get_json()["user"]["emailVerified"] is True
    # The browser that signed up sees it too.
    assert account.browser.get("/me").get_json()["user"]["emailVerified"] is True
    assert database.value("SELECT used_at FROM auth_tokens") is not None


def test_a_verification_link_works_once(app, outbox):
    account = sign_up(app, verified=False)
    token = last_token(app, account.email, "verify")
    assert Browser(app).post("/auth/verify-email", {"token": token}).status_code == 200

    res = Browser(app).post("/auth/verify-email", {"token": token})

    assert_error(res, 410, "token_invalid")
    assert_sets_no_session_cookies(res)


def test_a_verification_link_expires_after_24_hours(app, database, outbox):
    account = sign_up(app, verified=False)
    token = last_token(app, account.email, "verify")

    database.run("UPDATE auth_tokens SET expires_at = now() - interval '1 second'")
    res = Browser(app).post("/auth/verify-email", {"token": token})

    assert_error(res, 410, "token_invalid")
    assert_sets_no_session_cookies(res)
    assert account.browser.get("/me").get_json()["user"]["emailVerified"] is False


def test_a_newer_verification_link_replaces_the_older_one(app, outbox):
    account = sign_up(app, verified=False)
    first = last_token(app, account.email, "verify")
    assert account.browser.post("/auth/resend-verification").status_code == 204
    second = last_token(app, account.email, "verify")
    assert second != first

    assert_error(Browser(app).post("/auth/verify-email", {"token": first}), 410, "token_invalid")
    assert Browser(app).post("/auth/verify-email", {"token": second}).status_code == 200


def test_a_password_reset_token_cannot_verify_an_email(app, outbox):
    account = sign_up(app, verified=False)
    Browser(app).post("/auth/forgot-password", {"email": account.email})
    reset_token = last_token(app, account.email, "reset")

    res = Browser(app).post("/auth/verify-email", {"token": reset_token})

    assert_error(res, 410, "token_invalid")
    assert account.browser.get("/me").get_json()["user"]["emailVerified"] is False
    # And trying did not use the reset link up.
    assert (
        Browser(app)
        .post("/auth/reset-password", {"token": reset_token, "password": NEW_PASSWORD})
        .status_code
        == 204
    )


def test_an_unknown_verification_token_is_refused(app):
    assert_error(Browser(app).post("/auth/verify-email", {"token": "nope"}), 410, "token_invalid")


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "token"),
        ({"token": 12}, "token"),
        ({"token": ""}, "token"),
        ({"token": "x", "next": "/"}, "next"),
    ],
)
def test_verify_email_validates_its_body(app, body, field):
    assert_invalid(Browser(app).post("/auth/verify-email", body), field)


def test_a_disabled_account_is_not_signed_in_by_its_verification_link(app, database, outbox):
    account = sign_up(app, verified=False)
    database.run("UPDATE users SET disabled_at = now()")

    res = Browser(app).post("/auth/verify-email", {"token": last_token(app, account.email, "verify")})

    assert_error(res, 403, "account_disabled")
    assert_sets_no_session_cookies(res)


def test_a_deleted_account_cannot_be_verified(app, outbox):
    account = sign_up(app, verified=False)
    token = last_token(app, account.email, "verify")
    assert account.browser.delete("/me", {"password": PASSWORD}).status_code == 204

    res = Browser(app).post("/auth/verify-email", {"token": token})

    assert_error(res, 410, "token_invalid")
    assert_sets_no_session_cookies(res)


def test_verifying_on_a_browser_logged_in_as_someone_else_switches_accounts_cleanly(app, outbox):
    """No mixing of sessions: afterwards the browser is the verified user and nobody else."""
    newcomer = sign_up(app, verified=False)
    other = sign_up(app)
    old_access = other.browser.cookie(ACCESS)

    res = other.browser.post("/auth/verify-email", {"token": last_token(app, newcomer.email, "verify")})

    assert res.status_code == 200
    assert other.browser.get("/me").get_json()["user"]["email"] == newcomer.email
    assert other.browser.cookie(ACCESS) != old_access


# ---------------------------------------------------------------- A3 resend verification


def test_resend_sends_a_fresh_link_to_the_logged_in_users_address(app, outbox):
    account = sign_up(app, verified=False)
    bystander = sign_up(app, verified=False)
    outbox.clear()

    res = account.browser.post("/auth/resend-verification")

    assert res.status_code == 204
    assert res.get_data() == b""
    assert [message.to for message in outbox] == [account.email]
    token = link_token(app, outbox[0], "verify")
    assert (
        Browser(app).post("/auth/verify-email", {"token": token}).get_json()["user"]["id"]
        == account.user["id"]
    )
    assert bystander.browser.get("/me").get_json()["user"]["emailVerified"] is False


def test_resend_ignores_an_email_in_the_body(app, outbox):
    """The address comes from the session, so a body can't redirect the link to someone else."""
    account = sign_up(app, verified=False)
    outbox.clear()

    res = account.browser.post("/auth/resend-verification", {"email": "attacker@example.com"})

    assert res.status_code in (204, 400)
    assert mails_to("attacker@example.com") == []


def test_resend_when_already_verified_is_refused(app, outbox):
    account = sign_up(app)
    outbox.clear()

    assert_error(account.browser.post("/auth/resend-verification"), 409, "already_verified")
    assert outbox == []


def test_resend_needs_a_login(app, outbox):
    assert_error(Browser(app).post("/auth/resend-verification"), 401, "not_authenticated")
    assert outbox == []
