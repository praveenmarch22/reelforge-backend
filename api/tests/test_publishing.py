"""Publishing to YouTube Shorts (milestone B6): docs/PUBLISHING_YOUTUBE.md, API F1 to F6.
Runs with the offline YouTube stand-in (PUBLISH_PROVIDERS=mock)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from reelforge_api.providers.publish import MockYouTube

from .projects_support import creator, get_job, get_project, run_jobs
from .render_support import ready_project, started
from .support import assert_error
from .voice_support import no_real_voice_services  # noqa: F401 - autouse fixture


@pytest.fixture(autouse=True)
def fresh_mock():
    MockYouTube.fail_with, MockYouTube.no_channel, MockYouTube.processing = None, False, "processed"
    MockYouTube.uploads.clear()
    MockYouTube.revoked.clear()
    yield
    MockYouTube.fail_with, MockYouTube.no_channel, MockYouTube.processing = None, False, "processed"


def connect(browser, return_to: str = "/app/p/x/publish") -> str:
    """F1 then F2 as the browser does them. Returns where F2 sent the browser."""
    res = browser.get(f"/connections/youtube/connect?returnTo={return_to}")
    assert res.status_code == 200, res.get_data(as_text=True)
    url = urlsplit(res.get_json()["authorizeUrl"])
    back = browser.get(f"/connections/youtube/callback?{url.query}")
    assert back.status_code == 302
    location = back.headers["Location"]
    # Always the web app's own address (QB6-006), never anywhere else.
    assert location.startswith("http://localhost:5173/"), location
    return location.removeprefix("http://localhost:5173")


def rendered_project(app, browser) -> tuple[dict, str]:
    project = ready_project(app, browser)
    render_id = started(browser, project["id"])["render"]["id"]
    run_jobs(app)
    return get_project(browser, project["id"]), render_id


BODY = {
    "platform": "youtube",
    "title": "My first Short",
    "caption": "Watch this.",
    "hashtags": ["shorts", "facts"],
}


def test_connect_youtube_then_the_user_shows_the_channel(app):
    me = creator(app)
    location = connect(me.browser)
    assert location.startswith("/app/p/x/publish?") and parse_qs(urlsplit(location).query)["connected"] == [
        "youtube"
    ]
    me_json = me.browser.get("/me").get_json()["user"]
    youtube = me_json["connections"]["youtube"]
    assert youtube["status"] == "connected" and youtube["name"] == "Mock Channel"
    assert me_json["connections"]["instagram"] is None


def test_a_state_is_used_once_and_belongs_to_its_user(app):
    me, other = creator(app), creator(app)
    url = urlsplit(me.browser.get("/connections/youtube/connect").get_json()["authorizeUrl"])
    # Another signed-in person can't finish my sign-in.
    stolen = other.browser.get(f"/connections/youtube/callback?{url.query}")
    assert stolen.headers["Location"].startswith("http://localhost:5173/app/settings?")
    assert (
        "connection_error=youtube" in stolen.headers["Location"]
        and "reason=failed" in stolen.headers["Location"]
    )
    # And the state is gone after one use.
    again = me.browser.get(f"/connections/youtube/callback?{url.query}")
    assert "reason=failed" in again.headers["Location"]
    assert me.browser.get("/me").get_json()["user"]["connections"]["youtube"] is None


def test_an_account_without_a_channel_is_not_stored(app):
    me = creator(app)
    MockYouTube.no_channel = True
    location = connect(me.browser)
    assert "reason=no_channel" in location
    assert me.browser.get("/me").get_json()["user"]["connections"]["youtube"] is None


def test_return_to_never_leaves_the_site(app):
    me = creator(app)
    for evil in ("//evil.example", "/.//evil.example", "https://evil.example", "/%2e%2e//evil.example"):
        location = connect(me.browser, evil)
        assert location.startswith("/") and not location.startswith("//"), (evil, location)


def test_publish_a_render_to_youtube(app):
    me = creator(app)
    connect(me.browser)
    project, render_id = rendered_project(app, me.browser)
    res = me.browser.post(f"/renders/{render_id}/publish", BODY)
    assert res.status_code == 202, res.get_data(as_text=True)
    post = res.get_json()["post"]
    assert post["state"] == "queued" and post["platform"] == "youtube"
    assert get_project(me.browser, project["id"])["status"] == "posting"

    run_jobs(app)
    done = me.browser.get(f"/posts/{post['id']}").get_json()["post"]
    assert done["state"] == "published" and done["url"].startswith("https://youtube.com/shorts/")
    assert done["privateUntilVerified"] is True  # the mock, like YouTube before the audit
    after = get_project(me.browser, project["id"])
    assert after["status"] == "posted" and after["latestPost"]["id"] == post["id"]
    assert after["postTitle"] == "My first Short"
    sent = MockYouTube.uploads[0]["metadata"]
    assert sent["snippet"]["description"] == "Watch this.\n\n#shorts #facts"
    assert sent["status"]["privacyStatus"] == "public"
    assert_error(me.browser.post(f"/renders/{render_id}/publish", BODY), 409, "already_posted")


def test_publish_refusals(app):
    me = creator(app)
    _, render_id = rendered_project(app, me.browser)
    assert_error(me.browser.post(f"/renders/{render_id}/publish", BODY), 409, "not_connected")
    connect(me.browser)
    assert_error(
        me.browser.post(f"/renders/{render_id}/publish", {**BODY, "platform": "instagram"}),
        404,
        "not_available",
    )
    bad = me.browser.post(f"/renders/{render_id}/publish", {**BODY, "title": "a <b>"})
    assert bad.status_code == 400


def test_a_failed_upload_fails_the_post_and_can_be_tried_again(app):
    me = creator(app)
    connect(me.browser)
    project, render_id = rendered_project(app, me.browser)
    MockYouTube.fail_with = "quota_exceeded"
    first = me.browser.post(f"/renders/{render_id}/publish", BODY).get_json()
    run_jobs(app)
    failed = me.browser.get(f"/posts/{first['post']['id']}").get_json()["post"]
    assert failed["state"] == "failed" and failed["error"]["code"] == "quota_exceeded"
    assert get_project(me.browser, project["id"])["status"] == "post_failed"
    assert get_job(me.browser, first["job"]["id"])["state"] == "failed"
    second = me.browser.post(f"/renders/{render_id}/publish", BODY)
    assert second.status_code == 202
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "posted"


def test_ai_caption_writes_title_caption_and_hashtags(app):
    me = creator(app)
    project, _ = rendered_project(app, me.browser)
    res = me.browser.post(f"/projects/{project['id']}/caption/generate")
    assert res.status_code == 202, res.get_data(as_text=True)
    run_jobs(app)
    output = get_job(me.browser, res.get_json()["job"]["id"])["output"]
    assert output["title"] and output["caption"] and output["hashtags"]
    after = get_project(me.browser, project["id"])
    assert after["postTitle"] == output["title"] and after["hashtags"] == output["hashtags"]


def test_disconnect_revokes_and_keeps_posts(app):
    me = creator(app)
    connect(me.browser)
    assert me.browser.delete("/connections/youtube").status_code == 204
    assert MockYouTube.revoked == ["mock-refresh"]
    assert me.browser.get("/me").get_json()["user"]["connections"]["youtube"] is None


def test_changing_only_the_post_text_is_not_an_edit_of_the_reel(app):
    """QF7-001: autosaving the publish form must not make the render outdated."""
    me = creator(app)
    project, render_id = rendered_project(app, me.browser)
    before = get_project(me.browser, project["id"])
    res = me.browser.patch(
        f"/projects/{project['id']}", {"postTitle": "New title", "postCaption": "Text", "hashtags": ["a"]}
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    after = res.get_json()["project"]
    assert after["timelineVersion"] == before["timelineVersion"] and after["status"] == "rendered"
    assert after["latestRender"]["outdated"] is False and after["postTitle"] == "New title"
    # A real edit still counts.
    me.browser.patch(f"/projects/{project['id']}", {"title": "Other"})
    assert get_project(me.browser, project["id"])["latestRender"]["outdated"] is True
