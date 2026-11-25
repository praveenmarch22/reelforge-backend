"""The logged-in user's profile, password and account deletion: docs/API_DESIGN.md A9 to A12,
and the clean-up tasks behind them."""

from __future__ import annotations

import pytest

from reelforge_api.tasks.accounts import clean_tokens, purge_deleted

from .support import (
    NEW_PASSWORD,
    PASSWORD,
    SESSION_COOKIES,
    Browser,
    assert_clears_session_cookies,
    assert_error,
    assert_invalid,
    last_token,
    log_in,
    new_user_json,
    sign_up,
)

pytestmark = pytest.mark.usefixtures("clean_state")


def can_log_in(app, email: str, password: str) -> bool:
    return Browser(app).post("/auth/login", {"email": email, "password": password}).status_code == 200


def me(browser: Browser) -> dict:
    res = browser.get("/me")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()["user"]


def run_task(app, task):
    with app.app_context():
        return task.run()


# ---------------------------------------------------------------- A9 GET /me


def test_me_returns_exactly_the_user_object(app, outbox):
    account = sign_up(app, email="asha@example.com", name="Asha Rao", verified=False)

    res = account.browser.get("/me")

    assert res.status_code == 200
    assert res.get_json() == {"user": new_user_json(account.user["id"], "asha@example.com", "Asha Rao")}


def test_me_shows_a_connected_instagram_account_and_current_credits(app, database, outbox):
    account = sign_up(app)
    database.run(
        "INSERT INTO instagram_accounts (user_id, ig_user_id, username, account_type, access_token_enc,"
        " token_expires_at) VALUES (:id, '1789', 'asha.fit', 'BUSINESS', :token, '2027-01-09T00:00:00+00')",
        id=account.user["id"],
        token=b"encrypted-token",
    )
    database.run("UPDATE credit_accounts SET balance = 86")
    database.run("UPDATE users SET is_admin = true")

    user = me(account.browser)

    assert user["instagram"] == {
        "connected": True,
        "username": "asha.fit",
        "status": "connected",
        "expiresAt": "2027-01-09T00:00:00Z",
    }
    assert user["credits"]["balance"] == 86
    assert user["isAdmin"] is True
    assert "encrypted-token" not in str(user)


def test_me_only_ever_shows_the_callers_own_account(app, outbox):
    first, second = sign_up(app), sign_up(app)
    assert me(first.browser)["email"] == first.email
    assert me(second.browser)["email"] == second.email


# ---------------------------------------------------------------- A10 PATCH /me


def test_patch_changes_only_the_fields_that_are_sent(app, outbox):
    account = sign_up(app)
    browser = account.browser

    res = browser.patch("/me", {"name": "Asha R.", "niche": "Fitness, Finance"})
    assert res.status_code == 200
    assert res.get_json() == {"user": {**account.user, "name": "Asha R.", "niche": "Fitness, Finance"}}

    res = browser.patch("/me", {"defaults": {"captionStyle": "neon"}})
    assert res.get_json()["user"]["defaults"] == {"voiceId": "", "captionStyle": "neon", "mode": "faceless"}
    res = browser.patch("/me", {"defaults": {"voiceId": "JBFqnCBsd6RMkjVDRZzb", "mode": "full_ai"}})
    assert res.get_json()["user"]["defaults"] == {
        "voiceId": "JBFqnCBsd6RMkjVDRZzb",
        "captionStyle": "neon",
        "mode": "full_ai",
    }

    res = browser.patch("/me", {})
    assert res.status_code == 200
    expected = {
        **account.user,
        "name": "Asha R.",
        "niche": "Fitness, Finance",
        "defaults": {"voiceId": "JBFqnCBsd6RMkjVDRZzb", "captionStyle": "neon", "mode": "full_ai"},
    }
    assert res.get_json() == {"user": expected}
    assert me(browser) == expected


def test_onboarding_can_be_completed_but_not_undone(app, outbox):
    account = sign_up(app)
    browser = account.browser
    assert account.user["onboardingCompleted"] is False

    assert (
        browser.patch("/me", {"onboardingCompleted": True}).get_json()["user"]["onboardingCompleted"] is True
    )
    assert browser.patch("/me", {"onboardingCompleted": True}).status_code == 200  # repeating is harmless

    assert_invalid(browser.patch("/me", {"onboardingCompleted": False}), "onboardingCompleted")
    assert_invalid(browser.patch("/me", {"onboardingCompleted": "true"}), "onboardingCompleted")
    assert me(browser)["onboardingCompleted"] is True


@pytest.mark.parametrize("value", [1, 1.0])
def test_onboarding_completed_must_be_the_json_value_true(app, outbox, value):
    """ "true only": a number is the wrong type, like every other field given a wrong type."""
    account = sign_up(app)
    assert_invalid(account.browser.patch("/me", {"onboardingCompleted": value}), "onboardingCompleted")
    assert me(account.browser)["onboardingCompleted"] is False


@pytest.mark.parametrize(
    "niche",
    [
        "Fitness",
        "Fitness, Finance, gardening",
        "t" * 40,
        "\N{FLEXED BICEPS}" * 40,
        ", ".join(["a" * 38, "b" * 38, "c" * 40]),  # 120 characters in all
    ],
    ids=["one topic", "three topics", "topic of 40", "40 emoji", "120 characters"],
)
def test_niche_accepts_up_to_three_topics(app, outbox, niche):
    account = sign_up(app)
    res = account.browser.patch("/me", {"niche": niche})
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["user"]["niche"] == niche
    assert me(account.browser)["niche"] == niche


@pytest.mark.parametrize(
    "niche",
    [
        "Fitness, Finance, gardening, cooking",
        "Fitness, Finance, Fitness",
        "Fitness, fitness",
        "n" * 121,
        "t" * 41,
        "Fitness, " + "\N{FLEXED BICEPS}" * 41,
        7,
        ["Fitness", "Finance"],
    ],
    ids=["four topics", "a repeat", "a repeat in another case", "121 characters", "topic of 41", "41 emoji"]
    + ["a number", "a list"],
)
def test_niche_refuses_what_the_rules_forbid(app, outbox, niche):
    account = sign_up(app)
    assert account.browser.patch("/me", {"niche": "Fitness"}).status_code == 200

    assert_invalid(account.browser.patch("/me", {"niche": niche}), "niche")
    assert me(account.browser)["niche"] == "Fitness"


def test_an_empty_niche_clears_it(app, outbox):
    account = sign_up(app)
    assert account.browser.patch("/me", {"niche": "Fitness"}).get_json()["user"]["niche"] == "Fitness"
    assert account.browser.patch("/me", {"niche": ""}).get_json()["user"]["niche"] == ""
    assert me(account.browser)["niche"] == ""


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"name": ""}, "name"),
        ({"name": "  "}, "name"),
        ({"name": "n" * 81}, "name"),
        ({"name": 5}, "name"),
        ({"defaults": {"captionStyle": "comic"}}, "defaults.captionStyle"),
        ({"defaults": {"mode": "avatar"}}, "defaults.mode"),
        ({"defaults": {"voiceId": 3}}, "defaults.voiceId"),
        ({"defaults": {"font": "serif"}}, "defaults.font"),
        ({"defaults": "neon"}, "defaults"),
        # Nothing but the documented fields can be changed here.
        ({"email": "other@example.com"}, "email"),
        ({"isAdmin": True}, "isAdmin"),
        ({"emailVerified": True}, "emailVerified"),
        ({"credits": {"balance": 9999}}, "credits"),
        ({"password": "another-fine-passphrase"}, "password"),
        ({"id": "00000000-0000-0000-0000-000000000000"}, "id"),
    ],
)
def test_patch_refuses_invalid_and_unknown_fields_and_changes_nothing(app, outbox, body, field):
    account = sign_up(app, verified=False)

    assert_invalid(account.browser.patch("/me", {"name": "Would Be Saved", **body}), field)

    assert me(account.browser) == account.user


def test_patch_accepts_names_at_the_limits(app, outbox):
    account = sign_up(app)
    for name in ("A", "n" * 80):
        assert account.browser.patch("/me", {"name": name}).get_json()["user"]["name"] == name


@pytest.mark.parametrize(
    "body",
    [
        {"onboarding_completed": True},
        {"defaults": {"caption_style": "neon"}},
        {"defaults": {"voice_id": "JBFqnCBsd6RMkjVDRZzb"}},
    ],
)
def test_field_names_are_camel_case_only(app, outbox, body):
    """JSON field names are camelCase and unknown fields are refused (Conventions)."""
    account = sign_up(app)
    res = account.browser.patch("/me", body)
    assert_error(res, 400, "validation_error")
    assert me(account.browser) == account.user


def test_snake_case_names_are_refused_when_changing_the_password(app, outbox):
    account = sign_up(app)
    res = account.browser.post("/me/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert_invalid(res, "currentPassword", "newPassword")
    assert can_log_in(app, account.email, PASSWORD)


def test_trying_to_change_the_email_is_told_the_field_is_not_accepted(app, outbox):
    """Not "Enter a valid email address": the address is fine, it just can't be changed here
    (QB2-011)."""
    account = sign_up(app)

    fields = assert_invalid(account.browser.patch("/me", {"email": "other@example.com"}), "email")

    assert fields == {"email": "This field isn't accepted"}
    assert me(account.browser)["email"] == account.email


def test_patch_needs_a_login(app):
    assert_error(Browser(app).patch("/me", {"name": "X"}), 401, "not_authenticated")


# ---------------------------------------------------------------- A11 change password


def test_changing_the_password_logs_out_other_browsers_and_keeps_this_one(app, outbox):
    account = sign_up(app)
    here = account.browser
    phone, tablet = log_in(app, account), log_in(app, account)

    res = here.post("/me/password", {"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD})

    assert res.status_code == 204
    assert res.get_data() == b""
    # The others are out immediately: access token and refresh token.
    assert_error(phone.get("/me"), 401, "not_authenticated")
    assert_error(tablet.post("/auth/refresh"), 401, "not_authenticated")
    # This browser carries on, now and after its next refresh.
    assert me(here)["email"] == account.email
    assert here.post("/auth/refresh").status_code == 200
    assert me(here)["email"] == account.email
    assert not can_log_in(app, account.email, PASSWORD)
    assert can_log_in(app, account.email, NEW_PASSWORD)


def test_a_wrong_current_password_changes_nothing(app, outbox):
    account = sign_up(app)
    phone = log_in(app, account)

    res = account.browser.post(
        "/me/password", {"currentPassword": "not-my-password", "newPassword": NEW_PASSWORD}
    )

    assert_error(res, 401, "invalid_credentials")
    assert can_log_in(app, account.email, PASSWORD)
    assert not can_log_in(app, account.email, NEW_PASSWORD)
    assert me(phone)["email"] == account.email
    assert me(account.browser)["email"] == account.email, "a typing mistake must not log the user out"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"currentPassword": PASSWORD}, "newPassword"),
        ({"newPassword": NEW_PASSWORD}, "currentPassword"),
        ({"currentPassword": PASSWORD, "newPassword": "short"}, "newPassword"),
        ({"currentPassword": PASSWORD, "newPassword": "p" * 129}, "newPassword"),
        ({"currentPassword": PASSWORD, "newPassword": "letmein123"}, "newPassword"),
        ({"currentPassword": PASSWORD, "newPassword": 12345678}, "newPassword"),
        ({"currentPassword": 1, "newPassword": NEW_PASSWORD}, "currentPassword"),
        ({"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD, "logoutAll": True}, "logoutAll"),
    ],
)
def test_change_password_validates_its_body(app, outbox, body, field):
    account = sign_up(app)
    assert_invalid(account.browser.post("/me/password", body), field)
    assert can_log_in(app, account.email, PASSWORD)


def test_change_password_needs_a_login(app):
    res = Browser(app).post("/me/password", {"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD})
    assert_error(res, 401, "not_authenticated")


# ---------------------------------------------------------------- A12 delete account


def test_deleting_the_account_ends_everything_at_once(app, database, outbox):
    account = sign_up(app)
    here, phone = account.browser, log_in(app, account)
    here_copy = Browser(app).copy_session_from(here)
    bystander = sign_up(app)
    outbox.clear()

    res = here.delete("/me", {"password": PASSWORD})

    assert res.status_code == 204
    assert res.get_data() == b""
    assert_clears_session_cookies(res)
    assert [here.cookie(name) for name in SESSION_COOKIES] == [None, None, None]
    # No session of the account works any more.
    assert_error(phone.get("/me"), 401, "not_authenticated")
    assert_error(here_copy.post("/auth/refresh"), 401, "not_authenticated")
    # Logging in answers exactly as it does for an address that never had an account.
    assert_error(
        Browser(app).post("/auth/login", {"email": account.email, "password": PASSWORD}),
        401,
        "invalid_credentials",
    )
    assert Browser(app).post("/auth/forgot-password", {"email": account.email}).status_code == 204
    assert outbox == []
    # Soft delete: the row stays for the 7 days, marked.
    assert database.value("SELECT deleted_at FROM users WHERE id = :id", id=account.user["id"]) is not None
    # Other people are not affected.
    assert me(bystander.browser)["email"] == bystander.email


def test_deleting_disconnects_instagram_and_cancels_running_jobs(app, database, outbox):
    account, bystander = sign_up(app), sign_up(app)
    for user in (account, bystander):
        database.run(
            "INSERT INTO instagram_accounts (user_id, ig_user_id, username, account_type, access_token_enc,"
            " token_expires_at) VALUES (:id, '1789', 'someone', 'BUSINESS', :token, '2027-01-09 00:00+00')",
            id=user.user["id"],
            token=b"encrypted-token",
        )
        for job_type, state in (("ideas", "queued"), ("caption", "running"), ("hooks", "succeeded")):
            database.run(
                "INSERT INTO jobs (user_id, type, state) VALUES (:id, :type, :state)",
                id=user.user["id"],
                type=job_type,
                state=state,
            )

    assert account.browser.delete("/me", {"password": PASSWORD}).status_code == 204

    def jobs_of(user) -> dict:
        rows = database.rows(
            "SELECT type, state, finished_at FROM jobs WHERE user_id = :id", id=user.user["id"]
        )
        return {row["type"]: row for row in rows}

    mine, theirs = jobs_of(account), jobs_of(bystander)
    assert {kind: row["state"] for kind, row in mine.items()} == {
        "ideas": "cancelled",
        "caption": "cancelled",
        "hooks": "succeeded",
    }
    assert mine["ideas"]["finished_at"] is not None and mine["caption"]["finished_at"] is not None
    assert {kind: row["state"] for kind, row in theirs.items()} == {
        "ideas": "queued",
        "caption": "running",
        "hooks": "succeeded",
    }
    remaining = database.rows("SELECT user_id FROM instagram_accounts")
    assert [str(row["user_id"]) for row in remaining] == [bystander.user["id"]]


def test_delete_with_a_wrong_password_deletes_nothing(app, database, outbox):
    account = sign_up(app)

    res = account.browser.delete("/me", {"password": "not-my-password"})

    assert_error(res, 401, "invalid_credentials")
    assert me(account.browser)["email"] == account.email
    assert database.value("SELECT deleted_at FROM users") is None


@pytest.mark.parametrize(
    ("body", "field"),
    [({}, "password"), ({"password": 5}, "password"), ({"password": PASSWORD, "reason": "bored"}, "reason")],
)
def test_delete_validates_its_body(app, outbox, body, field):
    account = sign_up(app)
    assert_invalid(account.browser.delete("/me", body), field)
    assert me(account.browser)["email"] == account.email


def test_delete_needs_a_login(app):
    assert_error(Browser(app).delete("/me", {"password": PASSWORD}), 401, "not_authenticated")


def test_links_emailed_before_deletion_stop_working(app, outbox):
    account = sign_up(app, verified=False)
    verify_token = last_token(app, account.email, "verify")
    Browser(app).post("/auth/forgot-password", {"email": account.email})
    reset_token = last_token(app, account.email, "reset")
    assert account.browser.delete("/me", {"password": PASSWORD}).status_code == 204

    assert_error(Browser(app).post("/auth/verify-email", {"token": verify_token}), 410, "token_invalid")
    reset = Browser(app).post("/auth/reset-password", {"token": reset_token, "password": NEW_PASSWORD})
    assert_error(reset, 410, "token_invalid")


# ---------------------------------------------------------------- A12: the address is free at once


def placeholder_for(user_id: str) -> str:
    """What a deleted account's row holds instead of the address (docs/API_DESIGN.md A12)."""
    return f"deleted-{user_id}@deleted.invalid"


def test_deleting_releases_the_address_and_leaves_a_placeholder_on_the_row(app, database, outbox):
    account = sign_up(app)
    outbox.clear()
    assert account.browser.delete("/me", {"password": PASSWORD}).status_code == 204

    row = database.one("SELECT * FROM users")
    assert row["email"] == placeholder_for(account.user["id"])
    assert account.email not in " ".join(str(value) for value in row.values())
    # Until somebody signs up with it again, the address behaves like one that never existed.
    assert not can_log_in(app, account.email, PASSWORD)
    assert Browser(app).post("/auth/forgot-password", {"email": account.email}).status_code == 204
    assert outbox == []
    assert database.value("SELECT count(*) FROM auth_tokens WHERE used_at IS NULL") == 0
    # And the placeholder is not a way back in.
    res = Browser(app).post("/auth/login", {"email": row["email"], "password": PASSWORD})
    assert_error(res, 401, "invalid_credentials")


def test_signing_up_again_right_after_deleting_gives_a_brand_new_account(app, database, outbox):
    old = sign_up(app)
    changed = old.browser.patch("/me", {"name": "Old Name", "niche": "Fitness", "onboardingCompleted": True})
    assert changed.status_code == 200
    database.run("UPDATE credit_accounts SET balance = 7")
    forgotten_tab = Browser(app).copy_session_from(old.browser)
    assert old.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    outbox.clear()

    browser = Browser(app)
    res = browser.post("/auth/signup", {"email": old.email, "password": NEW_PASSWORD, "name": "Asha Again"})

    assert res.status_code == 201, res.get_data(as_text=True)
    new = res.get_json()["user"]
    assert new["id"] != old.user["id"]
    # Nothing comes over from the old account: profile, verification, onboarding, credits.
    assert new == new_user_json(new["id"], old.email, "Asha Again")
    ledger = database.rows("SELECT amount, kind FROM credit_ledger WHERE user_id = :id", id=new["id"])
    assert ledger == [{"amount": 100, "kind": "grant"}]
    assert [message.to for message in outbox] == [
        old.email
    ], "the new account gets its own verification email"
    # The address now opens the new account only, with the new password only.
    assert not can_log_in(app, old.email, PASSWORD)
    logged_in = Browser(app).post("/auth/login", {"email": old.email, "password": NEW_PASSWORD})
    assert logged_in.get_json()["user"]["id"] == new["id"]
    assert me(browser)["id"] == new["id"]
    # A browser still holding the old account's cookies gets nowhere.
    assert_error(forgotten_tab.get("/me"), 401, "not_authenticated")
    assert_error(forgotten_tab.post("/auth/refresh"), 401, "not_authenticated")
    emails = {str(row["id"]): row["email"] for row in database.rows("SELECT id, email FROM users")}
    assert emails == {old.user["id"]: placeholder_for(old.user["id"]), new["id"]: old.email}


def test_forgot_password_after_signing_up_again_resets_only_the_new_account(app, database, outbox):
    old = sign_up(app)
    assert old.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    new = sign_up(app, email=old.email, password=NEW_PASSWORD)
    outbox.clear()

    assert Browser(app).post("/auth/forgot-password", {"email": old.email}).status_code == 204

    assert [message.to for message in outbox] == [old.email]
    owners = [
        str(row["user_id"]) for row in database.rows("SELECT user_id FROM auth_tokens WHERE used_at IS NULL")
    ]
    assert owners == [new.user["id"]]
    token = last_token(app, old.email, "reset")
    res = Browser(app).post("/auth/reset-password", {"token": token, "password": "a-third-passphrase-here"})
    assert res.status_code == 204
    logged_in = Browser(app).post("/auth/login", {"email": old.email, "password": "a-third-passphrase-here"})
    assert logged_in.get_json()["user"]["id"] == new.user["id"]
    old_hash = database.value("SELECT password_hash FROM users WHERE id = :id", id=old.user["id"])
    assert old_hash.startswith("$argon2id$"), "the deleted row is not touched by the reset"
    assert database.value("SELECT deleted_at FROM users WHERE id = :id", id=old.user["id"]) is not None


def test_the_purge_removes_the_old_row_and_leaves_the_account_that_took_its_address(app, database, outbox):
    old = sign_up(app)
    assert old.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    new = sign_up(app, email=old.email, password=NEW_PASSWORD)
    assert run_task(app, purge_deleted) == 0, "nothing is purged before the 7 days are over"
    database.run("UPDATE users SET deleted_at = now() - interval '8 days' WHERE deleted_at IS NOT NULL")

    assert run_task(app, purge_deleted) == 1

    assert [str(row["id"]) for row in database.rows("SELECT id FROM users")] == [new.user["id"]]
    assert database.value("SELECT count(*) FROM credit_ledger WHERE user_id = :id", id=old.user["id"]) == 0
    assert me(new.browser) == {**new.user, "email": old.email}
    assert me(new.browser)["credits"]["balance"] == 100
    assert can_log_in(app, old.email, NEW_PASSWORD)


def test_one_address_can_be_deleted_and_signed_up_again_more_than_once(app, database, outbox):
    first = sign_up(app)
    assert first.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    second = sign_up(app, email=first.email)
    assert second.browser.delete("/me", {"password": PASSWORD}).status_code == 204

    third = sign_up(app, email=first.email)

    assert len({first.user["id"], second.user["id"], third.user["id"]}) == 3
    assert database.value("SELECT count(*) FROM users WHERE deleted_at IS NOT NULL") == 2
    assert me(third.browser)["email"] == first.email


def test_nobody_can_sign_up_with_a_placeholder_address(app, database, outbox):
    account = sign_up(app)
    body = {"email": placeholder_for(account.user["id"]), "password": NEW_PASSWORD, "name": "Squatter"}

    assert_invalid(Browser(app).post("/auth/signup", body), "email")

    # Deleting the real account still works afterwards.
    assert account.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    assert database.value("SELECT count(*) FROM users") == 1


# ---------------------------------------------------------------- purge and token clean-up


def test_purge_removes_accounts_deleted_more_than_seven_days_ago_with_all_their_data(app, database, outbox):
    old, recent, active = sign_up(app), sign_up(app), sign_up(app)
    Browser(app).post("/auth/forgot-password", {"email": old.email})
    for account in (old, recent):
        assert account.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    database.run(
        "UPDATE users SET deleted_at = now() - interval '7 days 1 minute' WHERE id = :id", id=old.user["id"]
    )
    database.run(
        "UPDATE users SET deleted_at = now() - interval '6 days 23 hours' WHERE id = :id",
        id=recent.user["id"],
    )

    assert run_task(app, purge_deleted) == 1

    left = {str(row["id"]) for row in database.rows("SELECT id FROM users")}
    assert left == {recent.user["id"], active.user["id"]}
    for table in ("auth_tokens", "refresh_tokens", "credit_accounts", "credit_ledger"):
        count = database.value(
            f"SELECT count(*) FROM {table} WHERE user_id = :id",  # noqa: S608 - fixed table names
            id=old.user["id"],
        )
        assert count == 0, f"{table} still holds the purged user's data"
        assert database.value(f"SELECT count(*) FROM {table}") > 0  # noqa: S608
    assert me(active.browser)["email"] == active.email
    assert run_task(app, purge_deleted) == 0


def test_a_purged_address_can_sign_up_again_as_a_new_account(app, database, outbox):
    old = sign_up(app)
    assert old.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    database.run("UPDATE users SET deleted_at = now() - interval '8 days'")
    run_task(app, purge_deleted)

    again = sign_up(app, email=old.email, password=NEW_PASSWORD)

    assert again.user["id"] != old.user["id"]
    assert again.user["credits"]["balance"] == 100
    assert not can_log_in(app, old.email, PASSWORD)


def test_token_cleanup_removes_only_long_expired_link_tokens(app, database, outbox):
    account = sign_up(app, verified=False)
    Browser(app).post("/auth/forgot-password", {"email": account.email})
    database.run("UPDATE auth_tokens SET expires_at = now() - interval '31 days' WHERE kind = 'verify_email'")

    run_task(app, clean_tokens)

    assert [row["kind"] for row in database.rows("SELECT kind FROM auth_tokens")] == ["reset_password"]
    token = last_token(app, account.email, "reset")
    assert (
        Browser(app).post("/auth/reset-password", {"token": token, "password": NEW_PASSWORD}).status_code
        == 204
    )


def test_token_cleanup_keeps_link_tokens_for_7_days_after_expiry(app, database, outbox):
    """Used or expired links are kept a week (for support questions), then removed. The
    database design's Data lifecycle table does not list them yet; 7 days is the rule the
    task states."""
    account = sign_up(app, verified=False)
    Browser(app).post("/auth/forgot-password", {"email": account.email})
    database.run(
        "UPDATE auth_tokens SET expires_at = now() - interval '7 days 1 hour' WHERE kind = 'verify_email'"
    )
    database.run(
        "UPDATE auth_tokens SET expires_at = now() - interval '6 days 23 hours' WHERE kind = 'reset_password'"
    )

    run_task(app, clean_tokens)

    assert [row["kind"] for row in database.rows("SELECT kind FROM auth_tokens")] == ["reset_password"]


def test_token_cleanup_keeps_refresh_tokens_for_30_days_after_expiry(app, database, outbox):
    """docs/DB_DESIGN.md, Data lifecycle: expired or revoked refresh tokens are kept 30 days
    after expiry (they are what reuse detection and the sessions list read)."""
    account = sign_up(app)
    log_in(app, account)
    log_in(app, account)
    ids = [row["id"] for row in database.rows("SELECT id FROM refresh_tokens ORDER BY created_at, id")]
    assert len(ids) >= 3
    long_gone, recently_expired = ids[0], ids[1]
    database.run(
        "UPDATE refresh_tokens SET expires_at = now() - interval '31 days' WHERE id = :id", id=long_gone
    )
    database.run(
        "UPDATE refresh_tokens SET expires_at = now() - interval '29 days' WHERE id = :id",
        id=recently_expired,
    )

    run_task(app, clean_tokens)

    left = {row["id"] for row in database.rows("SELECT id FROM refresh_tokens")}
    assert long_gone not in left
    assert recently_expired in left, "a refresh token that expired 29 days ago was removed"
    assert left == set(ids) - {long_gone}
