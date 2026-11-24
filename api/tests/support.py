"""Shared by the account tests: a browser-like client and helpers for the common steps.

Cookie names, paths and lifetimes are written out here on purpose: they are the contract in
docs/API_DESIGN.md (Conventions: Auth), so a renamed cookie in the code must fail a test.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.cookies import Morsel, SimpleCookie

import jwt

from reelforge_api import mail

API = "/api/v1"
ACCESS, REFRESH, CSRF = "rf_access", "rf_refresh", "rf_csrf"
SESSION_COOKIES = (ACCESS, REFRESH, CSRF)
COOKIE_PATHS = {ACCESS: "/", REFRESH: "/api/v1/auth", CSRF: "/"}
THIRTY_DAYS = 30 * 24 * 3600
FIFTEEN_MINUTES = 15 * 60

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "another-fine-passphrase"

AUTO = object()
_numbers = itertools.count(1)


def new_email() -> str:
    return f"creator{next(_numbers)}@example.com"


class Browser:
    """One browser: its own cookie jar, and the CSRF header the web app sends.

    By default every POST, PATCH and DELETE carries X-CSRF-Token with the value of the
    rf_csrf cookie when there is one, exactly as the web app does. Pass csrf=None to leave
    the header out or csrf="..." to send something else.
    """

    def __init__(self, app, ip: str = "127.0.0.1"):
        self.app = app
        self.client = app.test_client()
        self.ip = ip

    # -- cookies

    def cookie(self, name: str) -> str | None:
        found = self.client.get_cookie(name, path=COOKIE_PATHS[name])
        return found.value if found else None

    def set_cookie(self, name: str, value: str) -> None:
        self.client.set_cookie(name, value, path=COOKIE_PATHS[name])

    def drop_cookie(self, name: str) -> None:
        self.client.delete_cookie(name, path=COOKIE_PATHS[name])

    def copy_session_from(self, other: Browser) -> Browser:
        """Takes another browser's cookies as they are now (a copied or stolen session)."""
        for name in SESSION_COOKIES:
            value = other.cookie(name)
            if value is not None:
                self.set_cookie(name, value)
        return self

    # -- requests

    def call(self, method: str, path: str, json=None, *, csrf=AUTO, headers=None, ip=None, **kwargs):
        headers = dict(headers or {})
        if csrf is AUTO:
            if method != "GET" and self.cookie(CSRF):
                headers["X-CSRF-Token"] = self.cookie(CSRF)
        elif csrf is not None:
            headers["X-CSRF-Token"] = csrf
        if json is not None:
            kwargs["json"] = json
        return self.client.open(
            API + path,
            method=method,
            headers=headers,
            environ_overrides={"REMOTE_ADDR": ip or self.ip},
            **kwargs,
        )

    def get(self, path: str, **kwargs):
        return self.call("GET", path, **kwargs)

    def post(self, path: str, json=None, **kwargs):
        return self.call("POST", path, json, **kwargs)

    def patch(self, path: str, json=None, **kwargs):
        return self.call("PATCH", path, json, **kwargs)

    def delete(self, path: str, json=None, **kwargs):
        return self.call("DELETE", path, json, **kwargs)


# ---------------------------------------------------------------- responses


def assert_error(res, status: int, code: str) -> dict:
    """The response is the documented error; returns the error object."""
    body = res.get_json(silent=True) or {}
    got = (res.status_code, (body.get("error") or {}).get("code"))
    assert got == (
        status,
        code,
    ), f"expected {status} {code}, got {res.status_code}: {res.get_data(as_text=True)}"
    assert set(body) == {"error"}
    assert set(body["error"]) <= {"code", "message", "details"}
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    return body["error"]


def assert_invalid(res, *fields: str) -> dict:
    """400 validation_error naming each of `fields` in details.fields; returns that mapping."""
    error = assert_error(res, 400, "validation_error")
    named = error["details"]["fields"]
    for field in fields:
        assert field in named, f"{field} not reported in {named}"
        assert isinstance(named[field], str) and named[field]
    return named


def set_cookies(res) -> dict[str, Morsel]:
    """The cookies a response sets, by name."""
    found: dict[str, Morsel] = {}
    for header in res.headers.getlist("Set-Cookie"):
        parsed = SimpleCookie()
        parsed.load(header)
        found.update(parsed.items())
    return found


def assert_clears_session_cookies(res) -> None:
    cookies = set_cookies(res)
    for name in SESSION_COOKIES:
        assert name in cookies, f"{name} is not cleared"
        assert cookies[name].value == ""
        assert cookies[name]["max-age"] == "0" or cookies[name]["expires"].startswith("Thu, 01 Jan 1970")
        # A cookie is only removed when the path matches the one it was set with.
        assert cookies[name]["path"] == COOKIE_PATHS[name]


def assert_sets_no_session_cookies(res) -> None:
    assert not [name for name, morsel in set_cookies(res).items() if morsel.value], "a session was started"


# ---------------------------------------------------------------- tokens


def secret(app) -> str:
    return app.extensions["settings"].secret_key


def claims_of(app, token: str) -> dict:
    """A session token's contents (signature checked, expiry not)."""
    return jwt.decode(token, secret(app), algorithms=["HS256"], options={"verify_exp": False})


def resign(app, token: str, *, key: str | None = None, **changes) -> str:
    """The same token with some claims changed, signed again with the app's key (or `key`)."""
    return jwt.encode({**claims_of(app, token), **changes}, key or secret(app), algorithm="HS256")


def expire_access_token(browser: Browser) -> None:
    """Sixteen minutes pass: the browser's access token is now out of date."""
    past = datetime.now(UTC) - timedelta(minutes=16)
    token = resign(browser.app, browser.cookie(ACCESS), iat=past, exp=past + timedelta(minutes=15))
    browser.set_cookie(ACCESS, token)


# ---------------------------------------------------------------- emails


def mails_to(email: str) -> list[mail.Email]:
    return [message for message in mail.outbox if message.to.lower() == email.lower()]


def link_token(app, message: mail.Email, page: str) -> str:
    """The token in an emailed link; `page` is "verify" or "reset"."""
    origin = app.extensions["settings"].web_origin
    match = re.search(re.escape(f"{origin}/{page}?token=") + r"([A-Za-z0-9_.~%-]+)", message.text)
    assert match, f"no {origin}/{page}?token=... link in: {message.text}"
    assert match.group(0) in message.html, "the HTML part must carry the same link"
    return match.group(1)


def last_token(app, email: str, page: str) -> str:
    return link_token(app, mails_to(email)[-1], page)


# ---------------------------------------------------------------- accounts


@dataclass
class Account:
    email: str
    password: str
    browser: Browser
    user: dict


def sign_up(app, *, email=None, password=PASSWORD, name="Asha Rao", verified=True, ip="127.0.0.1") -> Account:
    """A new account through the API, logged in on its own browser, email verified unless told not to."""
    email = email or new_email()
    browser = Browser(app, ip)
    res = browser.post("/auth/signup", {"email": email, "password": password, "name": name})
    assert res.status_code == 201, res.get_data(as_text=True)
    if verified:
        res = browser.post("/auth/verify-email", {"token": last_token(app, email, "verify")})
        assert res.status_code == 200, res.get_data(as_text=True)
    return Account(email, password, browser, res.get_json()["user"])


def log_in(app, account: Account, *, password=None, ip="127.0.0.1") -> Browser:
    """The account logged in on one more browser."""
    browser = Browser(app, ip)
    res = browser.post("/auth/login", {"email": account.email, "password": password or account.password})
    assert res.status_code == 200, res.get_data(as_text=True)
    return browser


def first_of_next_month(moment: datetime | None = None) -> str:
    moment = (moment or datetime.now(UTC)).astimezone(UTC)
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return f"{year:04d}-{month:02d}-01T00:00:00Z"


def new_user_json(user_id: str, email: str, name: str) -> dict:
    """The User object (docs/API_DESIGN.md, Shared objects) of an account that has just signed up."""
    return {
        "id": user_id,
        "email": email,
        "name": name,
        "niche": "",
        "emailVerified": False,
        "defaults": {"voiceId": "", "captionStyle": "bold-pop", "mode": "faceless"},
        "onboardingCompleted": False,
        "isAdmin": False,
        "credits": {"balance": 100, "monthlyAllowance": 100, "resetsAt": first_of_next_month()},
        "instagram": {"connected": False, "username": None, "status": None, "expiresAt": None},
    }
