"""Rate limits: docs/API_DESIGN.md section 4 and Conventions (429 rate_limited, Retry-After).

These run against an app with limits switched on; every test starts with empty counters.
"""

from __future__ import annotations

import pytest

from .conftest import build_app
from .support import NEW_PASSWORD, PASSWORD, Browser, assert_error, mails_to, new_email, sign_up

pytestmark = pytest.mark.usefixtures("clean_state")

HOUR, MINUTE = 3600, 60


def assert_rate_limited(res, window: int) -> int:
    """429 with Retry-After in seconds and the same number in details.retryAfterSeconds."""
    error = assert_error(res, 429, "rate_limited")
    seconds = error["details"]["retryAfterSeconds"]
    assert res.headers["Retry-After"] == str(seconds)
    assert isinstance(seconds, int) and window - 30 <= seconds <= window
    return seconds


def signup_body() -> dict:
    return {"email": new_email(), "password": PASSWORD, "name": "Asha"}


# ---------------------------------------------------------------- A1 sign-up: 3 per hour per IP


def test_signup_allows_three_an_hour_from_one_address(limited_app, database, outbox):
    home = "203.0.113.7"
    for _ in range(3):
        assert Browser(limited_app, home).post("/auth/signup", signup_body()).status_code == 201

    refused = signup_body()
    res = Browser(limited_app, home).post("/auth/signup", refused)

    assert_rate_limited(res, HOUR)
    assert database.value("SELECT count(*) FROM users") == 3
    assert mails_to(refused["email"]) == []
    # Someone at another address is not held back.
    assert Browser(limited_app, "203.0.113.8").post("/auth/signup", signup_body()).status_code == 201


def test_signup_attempts_that_fail_validation_are_not_counted(limited_app, outbox):
    browser = Browser(limited_app, "203.0.113.9")
    for _ in range(6):
        assert browser.post("/auth/signup", {**signup_body(), "password": "short"}).status_code == 400
    for _ in range(3):
        assert Browser(limited_app, "203.0.113.9").post("/auth/signup", signup_body()).status_code == 201


def test_a_forwarded_for_header_cannot_dodge_the_per_address_limit(limited_app, outbox):
    """With no trusted proxy configured, the address is the connection's, not what a header says."""
    for n in range(3):
        res = Browser(limited_app, "203.0.113.10").post(
            "/auth/signup", signup_body(), headers={"X-Forwarded-For": f"198.51.100.{n}"}
        )
        assert res.status_code == 201

    res = Browser(limited_app, "203.0.113.10").post(
        "/auth/signup", signup_body(), headers={"X-Forwarded-For": "198.51.100.99"}
    )
    assert_rate_limited(res, HOUR)


# ---------------------------------------------------------------- A4 login: 5 per minute per IP and per email


def test_login_allows_five_a_minute_from_one_address(limited_app, outbox):
    account = sign_up(limited_app, ip="203.0.113.20")
    attacker = "203.0.113.21"
    for _ in range(5):
        res = Browser(limited_app, attacker).post(
            "/auth/login", {"email": new_email(), "password": "guess-guess"}
        )
        assert_error(res, 401, "invalid_credentials")

    # The sixth is refused whatever it carries, even the right password of a real account.
    res = Browser(limited_app, attacker).post("/auth/login", {"email": account.email, "password": PASSWORD})
    assert_rate_limited(res, MINUTE)
    assert "Set-Cookie" not in res.headers

    # The owner, at their own address, logs in as usual.
    owner = Browser(limited_app, "203.0.113.20").post(
        "/auth/login", {"email": account.email, "password": PASSWORD}
    )
    assert owner.status_code == 200


@pytest.mark.parametrize("exists", [True, False], ids=["a real account", "an unknown email"])
def test_login_allows_five_a_minute_for_one_email_from_any_address(limited_app, outbox, exists):
    """Guessing one account's password from many addresses is limited too, and the limit
    behaves the same whether or not the account exists."""
    email = sign_up(limited_app, ip="203.0.113.30").email if exists else new_email()
    for n in range(5):
        res = Browser(limited_app, f"198.51.100.{n}").post(
            "/auth/login", {"email": email, "password": "guess-guess"}
        )
        assert_error(res, 401, "invalid_credentials")

    res = Browser(limited_app, "198.51.100.50").post(
        "/auth/login", {"email": email.upper(), "password": "guess-guess"}
    )

    assert_rate_limited(res, MINUTE)
    # Other accounts are not affected.
    other = sign_up(limited_app, ip="203.0.113.31")
    res = Browser(limited_app, "198.51.100.51").post(
        "/auth/login", {"email": other.email, "password": PASSWORD}
    )
    assert res.status_code == 200


# ---------------------------------------------------------------- A3, A7 emails: 3 per hour


def test_resend_verification_allows_three_an_hour(limited_app, outbox):
    account = sign_up(limited_app, verified=False)
    outbox.clear()
    for _ in range(3):
        assert account.browser.post("/auth/resend-verification").status_code == 204

    res = account.browser.post("/auth/resend-verification")

    assert_rate_limited(res, HOUR)
    assert len(outbox) == 3


@pytest.mark.parametrize("exists", [True, False], ids=["a real account", "an unknown email"])
def test_forgot_password_allows_three_an_hour_per_email(limited_app, outbox, exists):
    email = sign_up(limited_app, ip="203.0.113.40").email if exists else new_email()
    outbox.clear()
    for n in range(3):
        res = Browser(limited_app, f"198.51.100.{n}").post("/auth/forgot-password", {"email": email})
        assert (res.status_code, res.get_data()) == (204, b"")

    res = Browser(limited_app, "198.51.100.60").post("/auth/forgot-password", {"email": email.upper()})

    # The limit can't be used to find out which addresses have accounts.
    assert_rate_limited(res, HOUR)
    assert len(outbox) == (3 if exists else 0)
    # Another address is still served.
    assert (
        Browser(limited_app, "198.51.100.60")
        .post("/auth/forgot-password", {"email": new_email()})
        .status_code
        == 204
    )


def test_resend_and_forgot_password_are_counted_separately(limited_app, outbox):
    """Otherwise anyone who knows an address could use up its owner's verification emails by
    asking for password resets, and the other way round (QB2-010)."""
    account = sign_up(limited_app, verified=False)
    outbox.clear()
    for _ in range(3):
        assert account.browser.post("/auth/resend-verification").status_code == 204
    assert_rate_limited(account.browser.post("/auth/resend-verification"), HOUR)

    # The address has had its three verification emails; its three reset emails are untouched.
    for n in range(3):
        res = Browser(limited_app, f"198.51.100.{n}").post("/auth/forgot-password", {"email": account.email})
        assert res.status_code == 204
    res = Browser(limited_app, "198.51.100.70").post("/auth/forgot-password", {"email": account.email})
    assert_rate_limited(res, HOUR)

    subjects = [message.subject for message in outbox]
    assert len(subjects) == 6 and len(set(subjects)) == 2
    assert len([message for message in outbox if "/reset?token=" in message.text]) == 3


def test_forgot_password_requests_do_not_use_up_the_verification_emails(limited_app, outbox):
    account = sign_up(limited_app, verified=False)
    for n in range(4):
        Browser(limited_app, f"198.51.100.{n}").post("/auth/forgot-password", {"email": account.email})

    assert account.browser.post("/auth/resend-verification").status_code == 204


# ---------------------------------------------------------------- current-password checks: 5 per minute


def test_guessing_the_current_password_is_limited_across_change_and_delete(limited_app, database, outbox):
    """A borrowed session can't be used to brute-force the password it needs for A11 and A12."""
    account = sign_up(limited_app)
    browser = account.browser
    for n in range(5):
        if n % 2:
            res = browser.delete("/me", {"password": f"guess-number-{n}"})
        else:
            res = browser.post(
                "/me/password", {"currentPassword": f"guess-number-{n}", "newPassword": NEW_PASSWORD}
            )
        assert_error(res, 401, "invalid_credentials")

    # Now even the right password is refused for the rest of the minute.
    changed = browser.post("/me/password", {"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD})
    assert_rate_limited(changed, MINUTE)
    assert_rate_limited(browser.delete("/me", {"password": PASSWORD}), MINUTE)
    assert database.value("SELECT deleted_at FROM users") is None
    # The session itself still works, and another user is not affected.
    assert browser.get("/me").status_code == 200
    other = sign_up(limited_app)
    res = other.browser.post("/me/password", {"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD})
    assert res.status_code == 204


# ---------------------------------------------------------------- everything else: 300 per minute per user


def test_a_user_gets_300_requests_a_minute(limited_app, outbox):
    account = sign_up(limited_app, verified=False)  # sign-up itself needs no session, so isn't counted
    other = sign_up(limited_app, verified=False)
    statuses = [account.browser.get("/me").status_code for _ in range(300)]
    assert set(statuses) == {200}

    assert_rate_limited(account.browser.get("/me"), MINUTE)
    assert_rate_limited(account.browser.patch("/me", {"name": "X"}), MINUTE)
    # Per user, not per address: the other user shares the address and is fine.
    assert other.browser.get("/me").status_code == 200


def test_limits_can_be_switched_off(app, outbox):
    """RATE_LIMITS_ENABLED=false (what the rest of the test suite runs with)."""
    for _ in range(7):
        res = Browser(app).post("/auth/login", {"email": "nobody@example.com", "password": "guess-guess"})
        assert_error(res, 401, "invalid_credentials")


def test_behind_a_load_balancer_the_address_is_the_one_the_balancer_reports(app, outbox):
    """TRUSTED_PROXY_HOPS=1: the last X-Forwarded-For entry is the balancer's and is used;
    anything the client put in front of it is ignored."""
    balanced = build_app(rate_limits_enabled=True, trusted_proxy_hops=1)
    balancer = "10.0.0.5"

    def signup(forwarded_for: str):
        headers = {"X-Forwarded-For": forwarded_for}
        return Browser(balanced, balancer).post("/auth/signup", signup_body(), headers=headers)

    for n in range(3):
        assert signup(f"1.1.1.{n}, 203.0.113.50").status_code == 201
    assert_rate_limited(signup("1.1.1.9, 203.0.113.50"), HOUR)
    # A different client behind the same balancer is counted on its own.
    assert signup("203.0.113.51").status_code == 201
