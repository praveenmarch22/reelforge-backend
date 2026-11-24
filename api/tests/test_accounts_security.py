"""Cross-cutting security checks for accounts: auth levels, what reaches the logs, CORS,
and the emailed links (docs/API_DESIGN.md Conventions; docs/TECHNICAL_DESIGN.md Security)."""

from __future__ import annotations

import logging

import pytest
from flask import jsonify

from reelforge_api import mail
from reelforge_api.auth import require_user
from reelforge_api.log import JsonFormatter

from .conftest import build_app
from .support import (
    ACCESS,
    CSRF,
    NEW_PASSWORD,
    PASSWORD,
    REFRESH,
    Browser,
    assert_error,
    expire_access_token,
    last_token,
    new_email,
    sign_up,
)

pytestmark = pytest.mark.usefixtures("clean_state")


# ---------------------------------------------------------------- auth levels


@pytest.fixture(scope="module")
def gated_app(app):
    """The API plus one endpoint per auth level that has no real endpoint yet."""
    gated = build_app()

    @gated.route("/api/v1/_test/verified", methods=["GET", "POST"])
    @require_user(verified=True)
    def _verified():
        return jsonify(ok=True)

    @gated.route("/api/v1/_test/admin", methods=["GET", "POST"])
    @require_user(admin=True)
    def _admin():
        return jsonify(ok=True)

    return gated


def test_verified_endpoints_refuse_until_the_email_is_verified(gated_app, outbox):
    account = sign_up(gated_app, verified=False)
    browser = account.browser

    assert_error(browser.get("/_test/verified"), 403, "email_not_verified")
    assert_error(browser.post("/_test/verified"), 403, "email_not_verified")

    token = last_token(gated_app, account.email, "verify")
    assert browser.post("/auth/verify-email", {"token": token}).status_code == 200
    assert browser.get("/_test/verified").status_code == 200
    assert browser.post("/_test/verified").status_code == 200


def test_admin_endpoints_refuse_everyone_but_admins(gated_app, database, outbox):
    account = sign_up(gated_app)
    admin = sign_up(gated_app)
    database.run("UPDATE users SET is_admin = true WHERE id = :id", id=admin.user["id"])

    assert_error(account.browser.get("/_test/admin"), 403, "forbidden")
    assert admin.browser.get("/_test/admin").status_code == 200
    assert admin.browser.post("/_test/admin").status_code == 200


def test_gated_endpoints_check_the_session_first(gated_app, outbox):
    assert_error(Browser(gated_app).get("/_test/admin"), 401, "not_authenticated")
    assert_error(Browser(gated_app).get("/_test/verified"), 401, "not_authenticated")

    account = sign_up(gated_app)
    assert_error(account.browser.post("/_test/verified", csrf=None), 403, "csrf_failed")
    expire_access_token(account.browser)
    assert_error(account.browser.get("/_test/verified"), 401, "token_expired")


# ---------------------------------------------------------------- logs


def test_no_password_token_or_cookie_value_is_written_to_the_log(app, database, outbox, caplog):
    """A whole account lifetime, then every log line is searched for the secrets involved."""
    caplog.set_level(logging.DEBUG)
    secrets: dict[str, str] = {"password": PASSWORD, "new password": NEW_PASSWORD}
    email = new_email()
    browser = Browser(app)

    def note_cookies(step: str) -> None:
        for name in (ACCESS, REFRESH, CSRF):
            if browser.cookie(name):
                secrets[f"{name} after {step}"] = browser.cookie(name)

    browser.post("/auth/signup", {"email": email, "password": PASSWORD, "name": "Asha"})
    note_cookies("signup")
    secrets["verification token"] = last_token(app, email, "verify")
    browser.post("/auth/verify-email", {"token": "wrong-" + secrets["verification token"]})
    browser.post("/auth/verify-email", {"token": secrets["verification token"]})
    note_cookies("verify")
    browser.post("/auth/login", {"email": email, "password": "wrong-" + PASSWORD})
    browser.post("/auth/login", {"email": email, "password": PASSWORD})
    note_cookies("login")
    stolen = Browser(app).copy_session_from(browser)
    browser.post("/auth/refresh")
    note_cookies("refresh")
    database.run(
        "UPDATE refresh_tokens SET revoked_at = revoked_at - interval '1 hour' WHERE revoked_at IS NOT NULL"
    )
    assert_error(stolen.post("/auth/refresh"), 401, "not_authenticated")  # logs "refresh token reuse"
    browser.post("/auth/login", {"email": email, "password": PASSWORD})
    note_cookies("second login")
    browser.post("/auth/forgot-password", {"email": email})
    secrets["reset token"] = last_token(app, email, "reset")
    browser.post("/auth/reset-password", {"token": secrets["reset token"], "password": "short"})
    browser.post("/auth/reset-password", {"token": secrets["reset token"], "password": NEW_PASSWORD})
    browser.post("/auth/login", {"email": email, "password": NEW_PASSWORD})
    note_cookies("third login")
    browser.post("/me/password", {"currentPassword": NEW_PASSWORD, "newPassword": PASSWORD})
    browser.delete("/me", {"password": PASSWORD})

    formatter = JsonFormatter()
    written = "\n".join(formatter.format(record) for record in caplog.records)
    assert "refresh token reuse" in written, "the scenario should have produced log lines to search"
    assert '"logger": "reelforge_api.request"' in written, "the request lines should be searched too"
    leaked = sorted(name for name, value in secrets.items() if value in written)
    assert leaked == []


# ---------------------------------------------------------------- emails


def test_emails_go_only_to_the_account_and_carry_no_password(app, outbox):
    account = sign_up(app, verified=False)
    Browser(app).post("/auth/forgot-password", {"email": account.email})

    assert [message.to for message in outbox] == [account.email, account.email]
    for message in outbox:
        assert PASSWORD not in message.text + message.html
    verification, reset = outbox
    assert "/verify?token=" in verification.text and "/reset?token=" not in verification.text
    assert "/reset?token=" in reset.text and "/verify?token=" not in reset.text


def test_emailed_links_point_at_the_configured_web_app(app, outbox):
    elsewhere = build_app(web_origin="https://app.reelforge.example")
    email = new_email()
    Browser(elsewhere).post("/auth/signup", {"email": email, "password": PASSWORD, "name": "A"})
    Browser(elsewhere).post("/auth/forgot-password", {"email": email})

    verification, reset = mail.outbox
    assert "https://app.reelforge.example/verify?token=" in verification.text
    assert "https://app.reelforge.example/reset?token=" in reset.text
    assert "localhost" not in verification.text + verification.html + reset.text + reset.html


def test_the_host_header_cannot_change_where_an_emailed_link_points(app, outbox):
    """Password-reset poisoning: the link is built from configuration, never from the request."""
    account = sign_up(app)
    outbox.clear()

    Browser(app).post("/auth/forgot-password", {"email": account.email}, headers={"Host": "evil.example"})
    Browser(app).post(
        "/auth/forgot-password",
        {"email": account.email},
        headers={"X-Forwarded-Host": "evil.example", "Origin": "https://evil.example"},
    )

    assert len(outbox) == 2
    for message in outbox:
        assert "evil.example" not in message.text + message.html
        assert "http://localhost:5173/reset?token=" in message.text


# ---------------------------------------------------------------- CORS


def test_only_the_web_app_origin_may_call_with_cookies(app, outbox):
    account = sign_up(app)

    ours = account.browser.get("/me", headers={"Origin": "http://localhost:5173"})
    assert ours.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert ours.headers["Access-Control-Allow-Credentials"] == "true"

    theirs = account.browser.get("/me", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in theirs.headers
    assert "Access-Control-Allow-Credentials" not in theirs.headers


def test_the_web_app_can_send_the_csrf_header_and_read_retry_after(app):
    preflight = Browser(app).call(
        "OPTIONS",
        "/me",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type, x-csrf-token",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert "x-csrf-token" in preflight.headers["Access-Control-Allow-Headers"].lower()
    assert "PATCH" in preflight.headers["Access-Control-Allow-Methods"]

    res = Browser(app).get("/me", headers={"Origin": "http://localhost:5173"})
    assert "retry-after" in res.headers["Access-Control-Expose-Headers"].lower()
