"""The verification link on a browser where somebody may already be signed in:
docs/API_DESIGN.md A2 (decision on QB2-005).

The link always verifies its own account. It signs the browser in only when nobody is signed
in there, so a link sent to another person can never move them into the sender's account.
"""

from __future__ import annotations

import pytest

from .support import (
    ACCESS,
    CSRF,
    NEW_PASSWORD,
    PASSWORD,
    REFRESH,
    SESSION_COOKIES,
    Account,
    Browser,
    assert_error,
    assert_sets_no_session_cookies,
    expire_access_token,
    last_token,
    log_in,
    resign,
    set_cookies,
    sign_up,
)

pytestmark = pytest.mark.usefixtures("clean_state")


def unverified(app) -> tuple[Account, str]:
    """A new account that has not opened its link yet, and the token in that link."""
    account = sign_up(app, verified=False)
    return account, last_token(app, account.email, "verify")


def me(browser: Browser) -> dict:
    res = browser.get("/me")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()["user"]


def cookies_of(browser: Browser) -> dict:
    return {name: browser.cookie(name) for name in SESSION_COOKIES}


def is_verified(database, account: Account) -> bool:
    return (
        database.value("SELECT email_verified_at FROM users WHERE id = :id", id=account.user["id"])
        is not None
    )


def assert_signed_in_as_the_links_account(res, browser: Browser, newcomer: Account) -> None:
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json() == {"user": {**newcomer.user, "emailVerified": True}}
    assert {name for name, morsel in set_cookies(res).items() if morsel.value} == set(SESSION_COOKIES)
    assert me(browser)["id"] == newcomer.user["id"]


def assert_stays_signed_in_as(res, browser: Browser, person: Account, before: dict) -> None:
    """The answer describes the person who is signed in and leaves their session exactly as it was."""
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json() == {"user": me(browser), "verifiedOtherAccount": True}
    assert res.get_json()["user"]["id"] == person.user["id"]
    assert set_cookies(res) == {}, "no cookie may be set or cleared"
    assert cookies_of(browser) == before


# ---------------------------------------------------------------- the same user is signed in


def test_opening_your_own_link_while_signed_in_verifies_and_keeps_the_session(app, database, outbox):
    account, token = unverified(app)
    another_tab = Browser(app).copy_session_from(account.browser)
    before = cookies_of(account.browser)
    logins_before = database.value("SELECT count(*) FROM refresh_tokens")

    res = account.browser.post("/auth/verify-email", {"token": token})

    assert res.status_code == 200
    assert res.get_json() == {"user": {**account.user, "emailVerified": True}}
    assert set_cookies(res) == {}, "the session is kept as it is: no cookie is set or cleared"
    assert cookies_of(account.browser) == before
    assert database.value("SELECT count(*) FROM refresh_tokens") == logins_before
    assert database.value("SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NOT NULL") == 0
    # Both tabs go on working, and the session can still be refreshed.
    assert me(another_tab)["emailVerified"] is True
    assert account.browser.post("/auth/refresh").status_code == 200
    assert me(account.browser)["emailVerified"] is True


def test_your_own_link_on_your_other_login_keeps_both_logins(app, database, outbox):
    account, token = unverified(app)
    phone = log_in(app, account)

    res = phone.post("/auth/verify-email", {"token": token})

    assert res.status_code == 200 and set(res.get_json()) == {"user"}
    assert set_cookies(res) == {}
    assert me(phone)["emailVerified"] is True and me(account.browser)["emailVerified"] is True


# ---------------------------------------------------------------- a different user is signed in


def test_someone_elses_link_verifies_their_account_and_leaves_you_signed_in_as_yourself(
    app, database, outbox
):
    """The attack in QB2-005: sign up, keep the link unopened, send it to somebody else. They
    must not land in the sender's account (where "connect Instagram" is the next step)."""
    sender, token = unverified(app)
    victim = sign_up(app)
    before = cookies_of(victim.browser)
    logins_before = database.value("SELECT count(*) FROM refresh_tokens")

    res = victim.browser.post("/auth/verify-email", {"token": token})

    assert_stays_signed_in_as(res, victim.browser, victim, before)
    assert res.get_json()["user"] == victim.user
    # Nothing about the link's account is told to the person who opened it.
    text = res.get_data(as_text=True)
    assert sender.email not in text and sender.user["id"] not in text
    # The link did its own job, once.
    assert is_verified(database, sender)
    assert me(sender.browser)["emailVerified"] is True
    assert_error(Browser(app).post("/auth/verify-email", {"token": token}), 410, "token_invalid")
    # No login was started or ended for anybody.
    assert database.value("SELECT count(*) FROM refresh_tokens") == logins_before
    assert database.value("SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NOT NULL") == 0
    assert victim.browser.post("/auth/refresh").status_code == 200
    assert me(victim.browser)["id"] == victim.user["id"]


@pytest.mark.parametrize(
    "state",
    [
        "the access token has passed its 15 minutes",
        "only the refresh cookie is left",
        "only the access cookie",
    ],
)
def test_a_browser_whose_login_can_still_be_refreshed_counts_as_signed_in(app, database, outbox, state):
    sender, token = unverified(app)
    victim = sign_up(app)
    if state == "the access token has passed its 15 minutes":
        expire_access_token(victim.browser)
    elif state == "only the refresh cookie is left":
        victim.browser.drop_cookie(ACCESS)
        victim.browser.drop_cookie(CSRF)
    else:
        expire_access_token(victim.browser)
        victim.browser.drop_cookie(REFRESH)
    before = cookies_of(victim.browser)

    res = victim.browser.post("/auth/verify-email", {"token": token})

    assert res.status_code == 200
    body = res.get_json()
    assert body == {"user": victim.user, "verifiedOtherAccount": True}
    assert set_cookies(res) == {}
    assert cookies_of(victim.browser) == before
    assert is_verified(database, sender)
    if victim.browser.cookie(REFRESH):
        assert victim.browser.post("/auth/refresh").status_code == 200
        assert me(victim.browser)["id"] == victim.user["id"]


# ---------------------------------------------------------------- cookies that are no longer a login


@pytest.mark.parametrize(
    "ended_by",
    [
        "logout",
        "a password change elsewhere",
        "the 30 days running out",
        "token reuse",
        "cookies that are not ours",
    ],
)
def test_a_browser_holding_the_cookies_of_a_dead_login_counts_as_nobody(app, database, outbox, ended_by):
    """Then the link signs the browser in, as it does on a browser with no cookies at all."""
    newcomer, token = unverified(app)
    former = sign_up(app)
    browser = Browser(app).copy_session_from(former.browser)  # keeps the cookies whatever happens next
    if ended_by == "logout":
        assert former.browser.post("/auth/logout").status_code == 204
    elif ended_by == "a password change elsewhere":
        elsewhere = log_in(app, former)
        changed = elsewhere.post("/me/password", {"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD})
        assert changed.status_code == 204
    elif ended_by == "the 30 days running out":
        database.run(
            "UPDATE refresh_tokens SET expires_at = now() - interval '1 minute' WHERE user_id = :id",
            id=former.user["id"],
        )
    elif ended_by == "token reuse":
        thief = Browser(app).copy_session_from(former.browser)
        assert former.browser.post("/auth/refresh").status_code == 200
        database.run("UPDATE refresh_tokens SET revoked_at = revoked_at - interval '11 seconds'")
        assert_error(thief.post("/auth/refresh"), 401, "not_authenticated")
    else:
        for name in (ACCESS, REFRESH):
            browser.set_cookie(
                name, resign(app, browser.cookie(name), key="somebody-elses-signing-key-0123456789")
            )

    res = browser.post("/auth/verify-email", {"token": token})

    assert_signed_in_as_the_links_account(res, browser, newcomer)
    assert browser.post("/auth/refresh").status_code == 200


def test_garbage_in_the_cookies_counts_as_nobody(app, outbox):
    newcomer, token = unverified(app)
    browser = Browser(app)
    for name in (ACCESS, REFRESH):
        browser.set_cookie(name, "not-a-token")
    browser.set_cookie(CSRF, "whatever")

    res = browser.post("/auth/verify-email", {"token": token})

    assert_signed_in_as_the_links_account(res, browser, newcomer)


def test_an_access_token_cannot_pose_as_the_refresh_token_of_another_user(app, outbox):
    """Each cookie is only believed as what it is: one user's access token placed in the
    refresh cookie does not make them "signed in"."""
    newcomer, token = unverified(app)
    other = sign_up(app)
    browser = Browser(app)
    browser.set_cookie(REFRESH, other.browser.cookie(ACCESS))
    assert other.browser.post("/auth/logout").status_code == 204

    res = browser.post("/auth/verify-email", {"token": token})

    assert_signed_in_as_the_links_account(res, browser, newcomer)


# ---------------------------------------------------------------- the signed-in account is gone or blocked


def test_when_the_signed_in_account_was_deleted_the_link_signs_in_its_own_account(app, database, outbox):
    newcomer, token = unverified(app)
    gone = sign_up(app)
    # Deleted behind the session's back (as the purge or an admin would), tokens untouched.
    database.run("UPDATE users SET deleted_at = now() WHERE id = :id", id=gone.user["id"])

    res = gone.browser.post("/auth/verify-email", {"token": token})

    assert_signed_in_as_the_links_account(res, gone.browser, newcomer)
    assert gone.user["email"] not in res.get_data(as_text=True)


def test_a_disabled_account_is_not_presented_as_the_signed_in_person(app, database, outbox):
    """Everywhere else a disabled account is answered with 403 account_disabled and its User
    object is not given out; this public endpoint must not be the exception."""
    newcomer, token = unverified(app)
    blocked = sign_up(app)
    database.run("UPDATE users SET disabled_at = now() WHERE id = :id", id=blocked.user["id"])
    assert_error(blocked.browser.get("/me"), 403, "account_disabled")

    res = blocked.browser.post("/auth/verify-email", {"token": token})

    assert res.status_code != 500
    assert is_verified(database, newcomer), "the link still verifies its own account"
    described = (res.get_json() or {}).get("user") or {}
    assert described.get("id") != blocked.user["id"], "the disabled account's User object is returned"


def test_a_disabled_links_account_is_refused_whoever_is_signed_in(app, database, outbox):
    newcomer, token = unverified(app)
    bystander = sign_up(app)
    before = cookies_of(bystander.browser)
    database.run("UPDATE users SET disabled_at = now() WHERE id = :id", id=newcomer.user["id"])

    res = bystander.browser.post("/auth/verify-email", {"token": token})

    assert_error(res, 403, "account_disabled")
    assert set_cookies(res) == {} and cookies_of(bystander.browser) == before
    assert not is_verified(database, newcomer)
    assert me(bystander.browser)["id"] == bystander.user["id"]


def test_a_dead_link_changes_nothing_for_the_person_signed_in(app, outbox):
    bystander = sign_up(app)
    before = cookies_of(bystander.browser)

    res = bystander.browser.post("/auth/verify-email", {"token": "not-a-real-token"})

    assert_error(res, 410, "token_invalid")
    assert_sets_no_session_cookies(res)
    assert set_cookies(res) == {} and cookies_of(bystander.browser) == before
    assert me(bystander.browser)["id"] == bystander.user["id"]


# ---------------------------------------------------------------- A8 and pending links


def test_a_password_reset_cancels_a_verification_link_that_was_never_opened(app, database, outbox):
    """A reset is what someone does when the account may be in the wrong hands, and an unopened
    verification link can sign a browser in (QB2-005, second part)."""
    account, verify_token = unverified(app)
    assert Browser(app).post("/auth/forgot-password", {"email": account.email}).status_code == 204
    reset_token = last_token(app, account.email, "reset")
    res = Browser(app).post("/auth/reset-password", {"token": reset_token, "password": NEW_PASSWORD})
    assert res.status_code == 204

    stale = Browser(app)
    res = stale.post("/auth/verify-email", {"token": verify_token})

    assert_error(res, 410, "token_invalid")
    assert_sets_no_session_cookies(res)
    assert_error(stale.get("/me"), 401, "not_authenticated")
    assert not is_verified(database, account)
    # The owner, with the new password, asks for a fresh link and that one works.
    owner = log_in(app, account, password=NEW_PASSWORD)
    assert owner.post("/auth/resend-verification").status_code == 204
    res = owner.post("/auth/verify-email", {"token": last_token(app, account.email, "verify")})
    assert res.status_code == 200 and res.get_json()["user"]["emailVerified"] is True


def test_a_refused_reset_leaves_the_verification_link_working(app, outbox):
    account, verify_token = unverified(app)
    assert Browser(app).post("/auth/forgot-password", {"email": account.email}).status_code == 204
    reset_token = last_token(app, account.email, "reset")
    res = Browser(app).post("/auth/reset-password", {"token": reset_token, "password": "short"})
    assert res.status_code == 400

    assert Browser(app).post("/auth/verify-email", {"token": verify_token}).status_code == 200
