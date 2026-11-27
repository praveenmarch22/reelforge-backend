"""Shared by the publishing tests (milestone B6; docs/PUBLISHING_YOUTUBE.md, API F1 to F6).

Field names are the contract in that document (Post, Connection), written out on purpose so
that a renamed field in the code fails a test.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from reelforge_api import jobs
from reelforge_api.providers.publish import MockYouTube

from .projects_support import get_project, run_jobs
from .render_support import ready_project, started
from .support import Browser

POST_FIELDS = {
    "id",
    "renderId",
    "projectId",
    "platform",
    "state",
    "progress",
    "title",
    "caption",
    "visibility",
    "credits",
    "url",
    "privateUntilVerified",
    "error",
    "createdAt",
    "publishedAt",
}
CONNECTION_FIELDS = {"status", "name", "pictureUrl", "connectedAt"}
POST_STATES = ("queued", "uploading", "processing", "published", "failed")
POST_ERROR_CODES = (
    "quota_exceeded",
    "reconnect_required",
    "rejected",
    "invalid_metadata",
    "upload_failed",
    "cancelled",
    "timed_out",
    "internal_error",
)
DEFAULT_RETURN = "/app/settings"
# F2 always sends the browser to the web app's own address (WEB_ORIGIN in the test settings).
WEB = "http://localhost:5173"
RETURN_TO = "/app/p/x/publish"

BODY = {
    "platform": "youtube",
    "title": "My first Short",
    "caption": "Watch this.",
    "hashtags": ["shorts", "facts"],
}


def reset_mock() -> None:
    MockYouTube.fail_with, MockYouTube.no_channel, MockYouTube.processing = None, False, "processed"
    MockYouTube.uploads.clear()
    MockYouTube.revoked.clear()
    # A job a test started but didn't run must not run in the next test.
    jobs.manual_queue.clear()


@pytest.fixture
def fresh_youtube():
    reset_mock()
    yield
    reset_mock()


def authorize_url(browser: Browser, return_to: str | None = RETURN_TO, platform: str = "youtube"):
    """F1. Returns the response (the caller checks it)."""
    query = {} if return_to is None else {"returnTo": return_to}
    return browser.get(f"/connections/{platform}/connect", query_string=query)


def state_of(browser: Browser, return_to: str | None = RETURN_TO) -> str:
    res = authorize_url(browser, return_to)
    assert res.status_code == 200, res.get_data(as_text=True)
    return parse_qs(urlsplit(res.get_json()["authorizeUrl"]).query)["state"][0]


def callback(browser: Browser, platform: str = "youtube", **query):
    """F2 as Google's redirect calls it."""
    return browser.get(f"/connections/{platform}/callback", query_string=query)


def connect(browser: Browser, return_to: str | None = RETURN_TO) -> str:
    """F1 then F2 as the browser does them, with the stand-in's code. Returns the Location."""
    state = state_of(browser, return_to)
    res = callback(browser, state=state, code="mock-code")
    assert res.status_code == 302, res.get_data(as_text=True)
    location = res.headers["Location"]
    assert location.startswith(WEB + "/"), location
    return location


def result_of(location: str) -> tuple[str, dict[str, list[str]]]:
    """(path, query) of where F2 sent the browser."""
    parts = urlsplit(location)
    return parts.path, parse_qs(parts.query, keep_blank_values=True)


def connection(browser: Browser) -> dict | None:
    res = browser.get("/me")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()["user"]["connections"]["youtube"]


def rendered_project(app, browser: Browser, **body) -> tuple[dict, str]:
    """A Reel with one finished render. Returns (project, render id)."""
    project = ready_project(app, browser, **body)
    render_id = started(browser, project["id"])["render"]["id"]
    run_jobs(app)
    project = get_project(browser, project["id"])
    assert project["status"] == "rendered", project["status"]
    return project, render_id


def publish(browser: Browser, render_id: str, body: dict | None = None, **kwargs):
    return browser.post(f"/renders/{render_id}/publish", BODY if body is None else body, **kwargs)


def published(browser: Browser, render_id: str, body: dict | None = None) -> dict:
    """F5 that must be accepted; returns the 202 body ({"post", "job"})."""
    res = publish(browser, render_id, body)
    assert res.status_code == 202, res.get_data(as_text=True)
    return res.get_json()


def get_post(browser: Browser, post_id: str) -> dict:
    res = browser.get(f"/posts/{post_id}")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()["post"]
