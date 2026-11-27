"""Publishing a render to YouTube (milestone B6): the AI post text (F4), publishing (F5), the
`publish` job (resumable upload, processing, YT-1, YT-5 to YT-10, YT-13), cancelling (G3), the
project's status, and reading posts (F6). Runs with the offline stand-in (PUBLISH_PROVIDERS=mock).
"""

from __future__ import annotations

import os

import pytest
import requests

from reelforge_api import crypto
from reelforge_api.providers.llm import MockLLM
from reelforge_api.providers.publish import Channel, MockYouTube, PublishError
from reelforge_api.storage import get_storage

from .projects_support import (
    ISO_UTC,
    balance,
    creator,
    get_job,
    get_project,
    ledger_rows,
    run_jobs,
    scripted_project,
    start_project,
)
from .publish_support import (
    BODY,
    POST_FIELDS,
    connect,
    connection,
    get_post,
    publish,
    published,
    rendered_project,
    reset_mock,
)
from .render_support import (
    MockRenderService,
    get_render,
    in_another_thread,
    listed,
    ready_project,
    start_render,
)
from .render_support import reset_mock as reset_render_mock
from .support import Browser, assert_error, assert_invalid
from .voice_support import no_real_voice_services  # noqa: F401 - autouse fixture


@pytest.fixture(autouse=True)
def fresh_mock():
    reset_mock()
    reset_render_mock()
    yield
    reset_mock()
    reset_render_mock()


class Chunks:
    """Every piece the worker sent to the stand-in, and a hook run before each one."""

    def __init__(self):
        self.sent: list[tuple[int, bytes, int]] = []
        self.before = None  # called with the 0-based index of the piece about to be sent

    def data(self) -> bytes:
        """The file as YouTube put it together (later pieces overwrite from their start)."""
        out = bytearray()
        for start, piece, _ in self.sent:
            out[start : start + len(piece)] = piece
        return bytes(out)


@pytest.fixture
def chunks(monkeypatch) -> Chunks:
    spy = Chunks()
    real = MockYouTube.upload_chunk

    def upload_chunk(self, session_url, chunk, start, total):
        if spy.before is not None:
            spy.before(len(spy.sent))
        spy.sent.append((start, bytes(chunk), total))
        return real(self, session_url, chunk, start, total)

    monkeypatch.setattr(MockYouTube, "upload_chunk", upload_chunk)
    return spy


def bigger_video(app, database, render_id: str, size: int) -> bytes:
    """Replaces the stand-in's 27-byte MP4 with `size` random bytes, so it takes several pieces."""
    key = database.value(
        "SELECT a.s3_key FROM renders r JOIN assets a ON a.id = r.asset_id WHERE r.id = :r", r=render_id
    )
    data = os.urandom(size)
    with app.app_context():
        get_storage().put_bytes(key, data, "video/mp4")
    database.run("UPDATE assets SET size_bytes = :s WHERE s3_key = :k", s=size, k=key)
    return data


def post_row(database, post_id: str) -> dict:
    return database.one("SELECT * FROM posts WHERE id = :p", p=post_id)


def ready_to_publish(app, browser: Browser) -> tuple[dict, str]:
    connect(browser)
    return rendered_project(app, browser)


# ---------------------------------------------------------------- F4: the AI post text


def test_f4_is_free_and_saves_title_caption_and_hashtags(app, database):
    me = creator(app)
    project, _ = rendered_project(app, me.browser)
    before = balance(me.browser)
    res = me.browser.post(f"/projects/{project['id']}/caption/generate")
    assert res.status_code == 202, res.get_data(as_text=True)
    job = res.get_json()["job"]
    assert set(res.get_json()) == {"job"}
    assert job["type"] == "caption" and job["creditsReserved"] == 0 and job["state"] == "queued"
    run_jobs(app)
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded"
    output = done["output"]
    assert set(output) == {"title", "caption", "hashtags"}
    assert 0 < len(output["title"]) <= 100 and "<" not in output["title"] and ">" not in output["title"]
    assert len(output["caption"]) <= 2200
    assert 1 <= len(output["hashtags"]) <= 30 and all(not h.startswith("#") for h in output["hashtags"])
    after = get_project(me.browser, project["id"])
    assert (after["postTitle"], after["postCaption"], after["hashtags"]) == (
        output["title"],
        output["caption"],
        output["hashtags"],
    )
    assert balance(me.browser) == before
    assert [r for r in ledger_rows(database, me.id) if str(r["job_id"]) == job["id"]] == []


def test_f4_without_a_script_is_409_no_script(app):
    me = creator(app)
    project, _ = start_project(me.browser, topic="[fail] a topic the model can't write")
    run_jobs(app)
    project = get_project(me.browser, project["id"])
    assert project["status"] != "scripting" and project["scenes"] == [], project["status"]
    assert_error(me.browser.post(f"/projects/{project['id']}/caption/generate"), 409, "no_script")


def test_f4_while_the_script_is_written_is_409(app):
    me = creator(app)
    project, _ = start_project(me.browser)
    assert_error(me.browser.post(f"/projects/{project['id']}/caption/generate"), 409, "already_running")


def test_f4_refused_by_the_model_saves_nothing(app):
    me = creator(app)
    project = scripted_project(app, me.browser)
    res = me.browser.patch(f"/projects/{project['id']}", {"title": "[refuse] something unsafe"})
    assert res.status_code == 200, res.get_data(as_text=True)
    before = get_project(me.browser, project["id"])  # the script job wrote a caption already
    job = me.browser.post(f"/projects/{project['id']}/caption/generate").get_json()["job"]
    run_jobs(app)
    failed = get_job(me.browser, job["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "content_refused"
    after = get_project(me.browser, project["id"])
    fields = ("postTitle", "postCaption", "hashtags")
    assert [after[f] for f in fields] == [before[f] for f in fields]


def test_f4_access(app):
    owner, other = creator(app), creator(app)
    project = scripted_project(app, owner.browser)
    path = f"/projects/{project['id']}/caption/generate"
    assert_error(Browser(app).post(path), 401, "not_authenticated")
    assert_error(other.browser.post(path), 404, "not_found")
    unverified = creator(app, verified=False)
    assert_error(unverified.browser.post(path), 403, "email_not_verified")


def test_f4_output_can_always_be_published_as_it_is(app, monkeypatch):
    """The AI text is "editable" but must also be usable as written: a model answer with < > in
    it must not leave the project with a post text that F5 then refuses."""
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    monkeypatch.setattr(
        MockLLM,
        "_caption",
        lambda self, c: {"title": "<<>>", "caption": "A <b>bold</b> claim.", "hashtags": ["<tag>", "facts"]},
    )
    job = me.browser.post(f"/projects/{project['id']}/caption/generate").get_json()["job"]
    run_jobs(app)
    if get_job(me.browser, job["id"])["state"] != "succeeded":
        return  # refusing such an answer is fine too
    saved = get_project(me.browser, project["id"])
    res = publish(
        me.browser,
        render_id,
        {
            "platform": "youtube",
            "title": saved["postTitle"],
            "caption": saved["postCaption"],
            "hashtags": saved["hashtags"],
        },
    )
    assert res.status_code == 202, (saved["postTitle"], saved["hashtags"], res.get_data(as_text=True))


# ---------------------------------------------------------------- F5: the main flow


def test_publish_the_whole_contract(app, database):
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    before = balance(me.browser)
    body = {**BODY, "hashtags": ["#shorts", "facts", "Facts"]}  # "#" dropped, duplicates removed
    res = published(me.browser, render_id, body)
    assert set(res) == {"post", "job"}
    post, job = res["post"], res["job"]
    assert set(post) == POST_FIELDS
    assert post["renderId"] == render_id and post["projectId"] == project["id"]
    assert (post["platform"], post["state"], post["progress"]) == ("youtube", "queued", 0)
    assert (post["title"], post["caption"], post["visibility"]) == ("My first Short", "Watch this.", "public")
    assert post["url"] is None and post["error"] is None and post["publishedAt"] is None
    assert post["credits"] == []  # the stand-in's voices and no music: nothing to credit
    assert post["privateUntilVerified"] is False and ISO_UTC.match(post["createdAt"])
    assert job["type"] == "publish" and job["creditsReserved"] == 0
    after = get_project(me.browser, project["id"])
    assert after["status"] == "posting" and after["latestPost"]["id"] == post["id"]
    assert (after["postTitle"], after["postCaption"], after["hashtags"]) == (
        "My first Short",
        "Watch this.",
        ["shorts", "facts"],
    )

    run_jobs(app)
    done = get_post(me.browser, post["id"])
    assert done["state"] == "published" and done["progress"] == 100 and done["error"] is None
    video_id = post_row(database, post["id"])["media_id"]
    assert video_id and done["url"] == f"https://youtube.com/shorts/{video_id}"
    assert ISO_UTC.match(done["publishedAt"])
    finished = get_job(me.browser, job["id"])
    assert finished["state"] == "succeeded"
    assert finished["output"] == {"postId": post["id"], "url": done["url"], "privateUntilVerified": True}
    project_after = get_project(me.browser, project["id"])
    assert project_after["status"] == "posted" and project_after["progress"] is None
    assert listed(me.browser, project["id"])["status"] == "posted"
    assert me.browser.get("/projects").get_json()["counts"]["posted"] == 1
    assert balance(me.browser) == before  # publishing is free

    [upload] = MockYouTube.uploads
    assert upload["metadata"] == {
        "snippet": {
            "title": "My first Short",
            "description": "Watch this.\n\n#shorts #facts",
            "tags": ["shorts", "facts"],
            "categoryId": "22",
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    assert upload["size"] == database.value(
        "SELECT a.size_bytes FROM renders r JOIN assets a ON a.id = r.asset_id WHERE r.id = :r", r=render_id
    )


def test_credit_lines_end_the_description(app, database):
    """QB6-001: a Reel voiced by ElevenLabs is credited "Voice: elevenlabs.io"."""
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    database.run("UPDATE assets SET provider = 'elevenlabs' WHERE kind = 'voice' AND user_id = :u", u=me.id)
    post = published(me.browser, render_id)["post"]
    assert post["credits"] == ["Voice: elevenlabs.io"]
    run_jobs(app)
    description = MockYouTube.uploads[0]["metadata"]["snippet"]["description"]
    assert description == "Watch this.\n\n#shorts #facts\n\nVoice: elevenlabs.io"
    assert get_post(me.browser, post["id"])["credits"] == ["Voice: elevenlabs.io"]


def test_publish_without_caption_or_hashtags(app, database):
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    post = published(me.browser, render_id, {"platform": "youtube", "title": "Just a title"})["post"]
    assert post["caption"] == "" and post["visibility"] == "public"
    run_jobs(app)
    assert MockYouTube.uploads[0]["metadata"]["snippet"]["description"] == ""
    assert MockYouTube.uploads[0]["metadata"]["snippet"]["tags"] == []


@pytest.mark.parametrize(
    ("chosen", "youtube_says", "kept_private"),
    [
        ("public", "private", True),  # YT-1: YouTube locked it
        ("unlisted", "private", True),
        ("public", "public", False),  # the creator's choice was honoured
        ("unlisted", "unlisted", False),
        ("private", "private", False),  # private was asked for
    ],
)
def test_private_until_verified_only_when_youtube_kept_it_private(
    app, monkeypatch, chosen, youtube_says, kept_private
):
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    monkeypatch.setattr(
        MockYouTube,
        "video",
        lambda self, token, vid: {
            "id": vid,
            "status": {"uploadStatus": "processed", "privacyStatus": youtube_says},
        },
    )
    post = published(me.browser, render_id, {**BODY, "visibility": chosen})["post"]
    run_jobs(app)
    done = get_post(me.browser, post["id"])
    assert done["state"] == "published" and done["visibility"] == chosen
    assert MockYouTube.uploads[0]["metadata"]["status"]["privacyStatus"] == chosen
    assert done["privateUntilVerified"] is kept_private


# ---------------------------------------------------------------- F5: refusals


def test_f5_not_connected(app):
    me = creator(app)
    _, render_id = rendered_project(app, me.browser)
    assert_error(publish(me.browser, render_id), 409, "not_connected")


def test_f5_reconnect_required(app, database):
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    database.run("UPDATE youtube_accounts SET status = 'reconnect_required' WHERE user_id = :u", u=me.id)
    assert_error(publish(me.browser, render_id), 409, "reconnect_required")
    assert get_project(me.browser, project["id"])["status"] == "rendered"


def test_f5_instagram_is_not_available_yet(app):
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    assert_error(publish(me.browser, render_id, {**BODY, "platform": "instagram"}), 404, "not_available")
    assert get_project(me.browser, project["id"])["status"] == "rendered"


def test_f5_already_running_then_already_posted(app):
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    published(me.browser, render_id)
    assert_error(publish(me.browser, render_id), 409, "already_running")
    run_jobs(app)
    assert_error(publish(me.browser, render_id), 409, "already_posted")
    assert len(MockYouTube.uploads) == 1


def test_f5_another_render_of_a_reel_that_is_posting_is_409(app, database):
    me = creator(app)
    project, first = ready_to_publish(app, me.browser)
    # A second finished render of the same Reel.
    second = start_render(me.browser, project["id"]).get_json()["render"]["id"]
    run_jobs(app)
    published(me.browser, first)
    assert_error(publish(me.browser, second), 409, "already_running")


def test_f5_render_outdated_needs_confirming(app):
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    scene = project["scenes"][0]
    res = me.browser.patch(f"/scenes/{scene['id']}", {"onScreenText": "Edited after the render"})
    assert res.status_code == 200, res.get_data(as_text=True)
    assert get_render(me.browser, render_id)["outdated"] is True
    assert_error(publish(me.browser, render_id), 409, "render_outdated")
    assert len(MockYouTube.uploads) == 0
    post = published(me.browser, render_id, {**BODY, "confirmOutdated": True})["post"]
    run_jobs(app)
    assert get_post(me.browser, post["id"])["state"] == "published"


def test_f5_a_render_that_is_not_finished_is_409_not_ready(app):
    me = creator(app)
    connect(me.browser)
    project = ready_project(app, me.browser)
    render_id = start_render(me.browser, project["id"]).get_json()["render"]["id"]
    assert_error(publish(me.browser, render_id), 409, "not_ready")  # still queued
    MockRenderService.fail_next = 2
    run_jobs(app)
    assert get_render(me.browser, render_id)["state"] == "failed"
    assert_error(publish(me.browser, render_id), 409, "not_ready")


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"title": ""}, "title"),
        ({"title": "   "}, "title"),
        ({"title": "x" * 101}, "title"),
        ({"title": "a <b> title"}, "title"),
        ({"title": "more > less"}, "title"),
        ({"title": "two\nlines"}, "title"),
        ({"caption": "x" * 2201}, "caption"),
        ({"caption": "a <script> caption"}, "caption"),
        ({"hashtags": [f"tag{i}" for i in range(31)]}, "hashtags"),
        ({"hashtags": ["two words"]}, "hashtags"),
        ({"hashtags": ["x" * 101]}, "hashtags"),
        ({"visibility": "secret"}, "visibility"),
        ({"platform": "tiktok"}, "platform"),
        ({"title": None}, "title"),
        ({"confirmOutdated": "yes please"}, "confirmOutdated"),
    ],
)
def test_f5_validation(app, change, field):
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    body = {k: v for k, v in {**BODY, **change}.items() if v is not None}
    assert_invalid(publish(me.browser, render_id, body), field)
    assert MockYouTube.uploads == []


def test_f5_angle_brackets_in_hashtags_are_refused(app):
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    res = publish(me.browser, render_id, {**BODY, "hashtags": ["<tag>"]})
    assert_error(res, 400, "validation_error")


def test_f5_limits_that_are_allowed(app):
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    body = {**BODY, "title": "x" * 100, "caption": "y" * 2200, "hashtags": [f"t{i}" for i in range(30)]}
    post = published(me.browser, render_id, body)["post"]
    assert len(post["title"]) == 100 and len(post["caption"]) == 2200


def test_f5_description_byte_limit(app, database):
    """YouTube's description is at most 5,000 bytes: caption, a blank line and the hashtags."""
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    tags = [
        f"{i:02d}" + "t" * 98 for i in range(30)
    ]  # 30 tags of 100 characters: 3,059 bytes with # and spaces
    exactly = {**BODY, "caption": "c" * 1939, "hashtags": tags}  # 1,939 + 2 + 3,059 = 5,000 bytes
    over = {**BODY, "caption": "c" * 1940, "hashtags": tags}
    euros = {**BODY, "caption": "€" * 1700, "hashtags": []}  # 1,700 characters, 5,100 bytes
    assert_invalid(publish(me.browser, render_id, over), "caption")
    assert_invalid(publish(me.browser, render_id, euros), "caption")
    res = publish(me.browser, render_id, exactly)
    assert res.status_code == 202, res.get_data(as_text=True)
    run_jobs(app)
    description = MockYouTube.uploads[0]["metadata"]["snippet"]["description"]
    assert len(description.encode("utf-8")) == 5000
    assert len(" ".join(MockYouTube.uploads[0]["metadata"]["snippet"]["tags"])) <= 500


def test_f5_access(app):
    owner, other = creator(app), creator(app)
    _, render_id = ready_to_publish(app, owner.browser)
    connect(other.browser)
    assert_error(Browser(app).post(f"/renders/{render_id}/publish", BODY), 401, "not_authenticated")
    assert_error(publish(other.browser, render_id), 404, "not_found")
    assert_error(publish(other.browser, "not-a-uuid"), 404, "not_found")
    assert_error(publish(other.browser, "00000000-0000-0000-0000-000000000000"), 404, "not_found")
    unverified = creator(app, verified=False)
    assert_error(publish(unverified.browser, render_id), 403, "email_not_verified")
    assert MockYouTube.uploads == []


def test_f5_a_deleted_reel_can_not_be_published(app):
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    assert me.browser.delete(f"/projects/{project['id']}").status_code in (200, 204)
    assert_error(publish(me.browser, render_id), 404, "not_found")


def test_f5_is_idempotent(app, database):
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    key = {"Idempotency-Key": "publish-click-1"}
    first = publish(me.browser, render_id, headers=key)
    assert first.status_code == 202
    again = publish(me.browser, render_id, headers=key)
    assert again.status_code == 202 and again.get_json() == first.get_json()
    run_jobs(app)
    later = publish(me.browser, render_id, headers=key)
    assert later.status_code == 202 and later.get_json()["post"]["id"] == first.get_json()["post"]["id"]
    assert database.value("SELECT count(*) FROM posts WHERE render_id = :r", r=render_id) == 1
    assert len(MockYouTube.uploads) == 1
    assert_error(
        publish(me.browser, render_id, {**BODY, "title": "Another"}, headers=key), 409, "idempotency_conflict"
    )


# ---------------------------------------------------------------- the job: uploading


def test_a_large_video_is_sent_in_pieces_with_progress(app, database, chunks, monkeypatch):
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    data = bigger_video(app, database, render_id, 10_000)
    monkeypatch.setattr("reelforge_api.publishing.CHUNK", 4096)
    post = published(me.browser, render_id)["post"]
    seen: list[tuple[str, int, str]] = []

    def look(index):
        if index > 0:
            now = in_another_thread(get_post, me.browser, post["id"])
            status = in_another_thread(get_project, me.browser, project["id"])["status"]
            seen.append((now["state"], now["progress"], status))

    chunks.before = look
    run_jobs(app)
    assert [(start, len(piece), total) for start, piece, total in chunks.sent] == [
        (0, 4096, 10_000),
        (4096, 4096, 10_000),
        (8192, 1808, 10_000),
    ]
    assert chunks.data() == data
    assert [s for s, _, _ in seen] == ["uploading", "uploading"]
    assert [st for _, _, st in seen] == ["posting", "posting"]
    progress = [p for _, p, _ in seen]
    assert progress == sorted(progress) and 10 < progress[0] < progress[1] <= 80
    assert MockYouTube.uploads[0]["size"] == 10_000
    assert get_post(me.browser, post["id"])["state"] == "published"


def test_the_project_shows_the_posts_progress_while_posting(app, database, chunks, monkeypatch):
    """API design: a project's `progress` is 0-100 while `posting` (like rendering, it follows the work)."""
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    bigger_video(app, database, render_id, 10_000)
    monkeypatch.setattr("reelforge_api.publishing.CHUNK", 4096)
    post = published(me.browser, render_id)["post"]
    seen: list[tuple[int, int]] = []

    def look(index):
        if index == 2:
            seen.append(
                (
                    in_another_thread(get_post, me.browser, post["id"])["progress"],
                    in_another_thread(get_project, me.browser, project["id"])["progress"],
                )
            )

    chunks.before = look
    run_jobs(app)
    [(post_progress, project_progress)] = seen
    assert post_progress > 10
    assert project_progress == post_progress


@pytest.mark.parametrize(
    "failure",
    [
        PublishError("upload_failed", "YouTube answered 503", retryable=True),
        requests.ConnectionError("dropped"),
    ],
    ids=["retryable-5xx", "dropped-connection"],
)
def test_an_interrupted_upload_continues_where_it_stopped(app, database, chunks, monkeypatch, failure):
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    data = bigger_video(app, database, render_id, 10_000)
    monkeypatch.setattr("reelforge_api.publishing.CHUNK", 4096)
    asked: list[int] = []
    real_so_far = MockYouTube.uploaded_so_far

    def so_far(self, session_url, total):
        answer = real_so_far(self, session_url, total)
        asked.append(answer[0])
        return answer

    monkeypatch.setattr(MockYouTube, "uploaded_so_far", so_far)

    def drop_second(index):
        if index == 1 and not getattr(drop_second, "done", False):
            drop_second.done = True
            chunks.sent.append((-1, b"", 0))  # marks the attempt; removed below
            raise failure

    chunks.before = drop_second
    post = published(me.browser, render_id)["post"]
    run_jobs(app)
    chunks.sent[:] = [c for c in chunks.sent if c[0] >= 0]
    assert asked == [4096]  # YouTube was asked how far it got
    assert [start for start, _, _ in chunks.sent] == [0, 4096, 8192]
    assert chunks.data() == data
    assert len(MockYouTube.uploads) == 1  # the same upload went on
    assert get_post(me.browser, post["id"])["state"] == "published"


def test_an_upload_that_keeps_failing_fails_the_post(app, database, chunks, monkeypatch):
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    bigger_video(app, database, render_id, 10_000)
    monkeypatch.setattr("reelforge_api.publishing.CHUNK", 4096)

    def always(index):
        chunks.sent.append((-1, b"", 0))
        raise requests.ConnectionError("dropped")

    chunks.before = always
    post = published(me.browser, render_id)["post"]
    run_jobs(app)
    assert len(chunks.sent) == 3  # up to 3 tries (YT-8)
    failed = get_post(me.browser, post["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "upload_failed"
    assert failed["error"]["message"] == "The upload to YouTube didn't go through. Please try again."
    assert get_project(me.browser, project["id"])["status"] == "post_failed"
    assert get_render(me.browser, render_id)["state"] == "succeeded"  # the render is never lost


def test_quota_exceeded_fails_at_once(app, chunks):
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)

    def quota(index):
        chunks.sent.append((-1, b"", 0))
        raise PublishError("quota_exceeded")

    chunks.before = quota
    before = balance(me.browser)
    post = published(me.browser, render_id)["post"]
    run_jobs(app)
    assert len(chunks.sent) == 1  # a 4xx is never retried
    failed = get_post(me.browser, post["id"])
    assert failed["error"] == {
        "code": "quota_exceeded",
        "message": "YouTube's daily upload limit is reached. Try again tomorrow.",
    }
    assert get_project(me.browser, project["id"])["status"] == "post_failed"
    assert balance(me.browser) == before


def test_quota_exceeded_when_the_upload_starts(app):
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    MockYouTube.fail_with = "quota_exceeded"
    post = published(me.browser, render_id)["post"]
    run_jobs(app)
    assert get_post(me.browser, post["id"])["error"]["code"] == "quota_exceeded"
    assert get_project(me.browser, project["id"])["latestPost"]["state"] == "failed"


def test_a_revoked_connection_fails_the_post_and_asks_to_reconnect(app, database):
    """YT-6: Google refuses the refresh token (invalid_grant). The connection is marked, the
    post fails with reconnect_required, and after connecting again the post can be retried."""
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    with app.app_context():
        revoked = crypto.encrypt("revoked-at-google")
    database.run(
        "UPDATE youtube_accounts SET token_expires_at = now(), refresh_token_enc = :r WHERE user_id = :u",
        r=revoked,
        u=me.id,
    )
    post = published(me.browser, render_id)["post"]
    run_jobs(app)
    failed = get_post(me.browser, post["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "reconnect_required"
    assert failed["error"]["message"] == (
        "Your YouTube connection has expired. Connect YouTube again, then try again."
    )
    assert connection(me.browser)["status"] == "reconnect_required"
    assert get_project(me.browser, project["id"])["status"] == "post_failed"
    assert MockYouTube.uploads == []
    assert_error(publish(me.browser, render_id), 409, "reconnect_required")

    connect(me.browser)
    assert connection(me.browser)["status"] == "connected"
    retry = published(me.browser, render_id)["post"]
    run_jobs(app)
    assert get_post(me.browser, retry["id"])["state"] == "published"
    assert get_project(me.browser, project["id"])["status"] == "posted"


def test_disconnecting_while_a_post_waits_fails_it_with_reconnect_required(app):
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    post = published(me.browser, render_id)["post"]
    assert me.browser.delete("/connections/youtube").status_code == 204
    run_jobs(app)
    failed = get_post(me.browser, post["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "reconnect_required"
    assert MockYouTube.uploads == []


# ---------------------------------------------------------------- the job: YouTube's processing


@pytest.mark.parametrize("verdict", ["rejected", "failed", "deleted"])
def test_youtube_refusing_the_video_fails_the_post_as_rejected(app, verdict):
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    MockYouTube.processing = verdict
    post = published(me.browser, render_id)["post"]
    run_jobs(app)
    failed = get_post(me.browser, post["id"])
    assert failed["state"] == "failed" and failed["url"] is None
    assert failed["error"] == {
        "code": "rejected",
        "message": "YouTube couldn't process this video. Render it again and try once more.",
    }
    assert get_project(me.browser, project["id"])["status"] == "post_failed"
    assert get_render(me.browser, render_id)["state"] == "succeeded"


def test_still_processing_after_the_wait_is_published(app, monkeypatch):
    """Design: after 10 minutes of processing the Short is on YouTube and finishes there."""
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    polls: list[str] = []
    monkeypatch.setattr(
        MockYouTube,
        "video",
        lambda self, token, vid: polls.append(vid)
        or {"id": vid, "status": {"uploadStatus": "uploaded", "privacyStatus": "public"}},
    )
    post = published(me.browser, render_id)["post"]
    run_jobs(app)
    assert len(polls) == 60
    assert get_post(me.browser, post["id"])["state"] == "published"


def test_a_retry_after_the_worker_died_past_the_upload_does_not_upload_twice(app, database, monkeypatch):
    """YT-9: the video id is saved as soon as YouTube returns it, and a retry of a post with a
    video id only re-checks processing. Here the worker dies on its first processing check."""
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    real_video = MockYouTube.video
    calls = {"n": 0}

    def video(self, token, vid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the worker process died here")
        return real_video(self, token, vid)

    monkeypatch.setattr(MockYouTube, "video", video)
    first = published(me.browser, render_id)["post"]
    run_jobs(app)
    assert get_post(me.browser, first["id"])["state"] == "failed"
    saved_video = post_row(database, first["id"])["media_id"]
    assert saved_video  # YT-9: the id was saved before the crash

    retry = published(me.browser, render_id)["post"]  # Try again
    run_jobs(app)
    done = get_post(me.browser, retry["id"])
    assert done["state"] == "published"
    assert len(MockYouTube.uploads) == 1, "the same video was uploaded to the channel a second time"
    assert done["url"] == f"https://youtube.com/shorts/{saved_video}"


def test_try_again_continues_an_interrupted_upload(app, database, chunks, monkeypatch):
    """YT-8 across attempts (QB6-002): the upload failed after the first piece; Try again sends
    only the rest, on the same YouTube upload."""
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    data = bigger_video(app, database, render_id, 10_000)
    monkeypatch.setattr("reelforge_api.publishing.CHUNK", 4096)

    def drop_after_first(index):
        if index >= 1 and drop_after_first.on:
            chunks.sent.append((-1, b"", 0))
            raise requests.ConnectionError("dropped")

    drop_after_first.on = True
    chunks.before = drop_after_first
    first = published(me.browser, render_id)["post"]
    run_jobs(app)
    assert get_post(me.browser, first["id"])["error"]["code"] == "upload_failed"
    drop_after_first.on = False
    chunks.sent[:] = [c for c in chunks.sent if c[0] >= 0]
    retry = published(me.browser, render_id)["post"]
    run_jobs(app)
    assert get_post(me.browser, retry["id"])["state"] == "published"
    assert [start for start, _, _ in chunks.sent] == [0, 4096, 8192]  # 0 once, then the rest
    assert chunks.data() == data
    assert len(MockYouTube.uploads) == 1


def test_try_again_after_connecting_another_channel_does_not_reuse_the_old_channels_video(
    app, database, monkeypatch
):
    """The creator connected channel A; the upload reached YouTube, then the post failed. They
    connect channel B (F2: "A second connect replaces the first") and try again. The Short
    must go to B: the post's link must not be A's video."""
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    real_video = MockYouTube.video
    calls = {"n": 0}

    def video(self, token, vid):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the worker process died here")
        return real_video(self, token, vid)

    monkeypatch.setattr(MockYouTube, "video", video)
    first = published(me.browser, render_id)["post"]
    run_jobs(app)
    old_video = post_row(database, first["id"])["media_id"]
    assert old_video and get_post(me.browser, first["id"])["state"] == "failed"

    monkeypatch.setattr(MockYouTube, "channel", lambda self, token: Channel("UCchannelB", "Channel B", ""))
    connect(me.browser)
    assert connection(me.browser)["name"] == "Channel B"
    retry = published(me.browser, render_id)["post"]
    run_jobs(app)
    done = get_post(me.browser, retry["id"])
    assert done["state"] == "published"
    assert done["url"] != f"https://youtube.com/shorts/{old_video}", "the link is to the video on channel A"
    assert len(MockYouTube.uploads) == 2


def test_a_video_youtube_no_longer_has_is_not_reported_published(app, monkeypatch):
    """videos.list answers with no item (the creator deleted it on YouTube while it was
    processing): the post must not end `published` with a link to nothing."""
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    monkeypatch.setattr(MockYouTube, "video", lambda self, token, vid: None)
    post = published(me.browser, render_id)["post"]
    run_jobs(app)
    done = get_post(me.browser, post["id"])
    assert done["state"] == "failed", done


def test_running_out_of_time_mid_upload_is_timed_out_not_a_dropped_connection(app, chunks):
    """QB6-004: Celery's soft time limit is not retried as if the connection had dropped."""
    from celery.exceptions import SoftTimeLimitExceeded

    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)

    def out_of_time(index):
        chunks.sent.append((-1, b"", 0))
        raise SoftTimeLimitExceeded()

    chunks.before = out_of_time
    post = published(me.browser, render_id)["post"]
    run_jobs(app)
    assert len(chunks.sent) == 1
    failed = get_post(me.browser, post["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "timed_out"
    assert get_project(me.browser, project["id"])["status"] == "post_failed"


def test_try_again_after_youtube_rejected_the_video_uploads_it_again(app):
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    MockYouTube.processing = "rejected"
    first = published(me.browser, render_id)["post"]
    run_jobs(app)
    assert get_post(me.browser, first["id"])["error"]["code"] == "rejected"
    MockYouTube.processing = "processed"
    retry = published(me.browser, render_id)["post"]
    run_jobs(app)
    assert get_post(me.browser, retry["id"])["state"] == "published"
    assert len(MockYouTube.uploads) == 2  # a fresh upload, not the refused video


def test_the_music_licence_is_credited(app, database):
    me = creator(app)
    _, render_id = ready_to_publish(app, me.browser)
    database.run(
        # The stand-in's Reel has no music (null): give its render a library track's licence line.
        "UPDATE renders SET timeline = jsonb_set(timeline, '{music}', "
        "jsonb_build_object('license', CAST(:l AS text))) WHERE id = :r",
        l='"Morning" by Someone (CC BY 4.0)',
        r=render_id,
    )
    post = published(me.browser, render_id)["post"]
    assert post["credits"] == ['Music: "Morning" by Someone (CC BY 4.0)']
    run_jobs(app)
    description = MockYouTube.uploads[0]["metadata"]["snippet"]["description"]
    assert description.endswith('\n\nMusic: "Morning" by Someone (CC BY 4.0)')


# ---------------------------------------------------------------- cancelling (G3) and the project's status


def cancel(browser: Browser, job_id: str):
    return browser.post(f"/jobs/{job_id}/cancel")


def test_cancelling_a_queued_post(app):
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    started = published(me.browser, render_id)
    res = cancel(me.browser, started["job"]["id"])
    assert res.status_code == 200 and res.get_json()["job"]["state"] == "cancelled"
    run_jobs(app)
    assert MockYouTube.uploads == []
    stopped = get_post(me.browser, started["post"]["id"])
    assert stopped["state"] == "failed"
    assert stopped["error"] == {"code": "cancelled", "message": "Publishing was stopped."}
    assert get_project(me.browser, project["id"])["status"] == "rendered"
    assert_error(cancel(me.browser, started["job"]["id"]), 409, "job_finished")
    # It can be published afterwards.
    again = published(me.browser, render_id)["post"]
    run_jobs(app)
    assert get_post(me.browser, again["id"])["state"] == "published"


def test_cancelling_during_the_upload_stops_sending(app, database, chunks, monkeypatch):
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    bigger_video(app, database, render_id, 10_000)
    monkeypatch.setattr("reelforge_api.publishing.CHUNK", 4096)
    started = published(me.browser, render_id)

    def stop(index):
        if index == 1:
            res = in_another_thread(cancel, me.browser, started["job"]["id"])
            assert res.status_code == 200, res.get_data(as_text=True)

    chunks.before = stop
    run_jobs(app)
    assert len(chunks.sent) == 2  # the piece in flight, then nothing more
    assert get_job(me.browser, started["job"]["id"])["state"] == "cancelled"
    post = get_post(me.browser, started["post"]["id"])
    assert post["state"] == "failed" and post["url"] is None
    assert get_project(me.browser, project["id"])["status"] == "rendered"


def test_cancelling_during_processing_counts_as_published(app, monkeypatch):
    """Design: "A publish stopped after the upload counts as published: the Short is on YouTube"."""
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    started = published(me.browser, render_id)
    polls: list[str] = []

    def video(self, token, vid):
        if not polls:
            res = in_another_thread(cancel, me.browser, started["job"]["id"])
            assert res.status_code == 200, res.get_data(as_text=True)
        polls.append(vid)
        return {"id": vid, "status": {"uploadStatus": "uploaded", "privacyStatus": "public"}}

    monkeypatch.setattr(MockYouTube, "video", video)
    run_jobs(app)
    assert len(polls) == 1
    assert get_job(me.browser, started["job"]["id"])["state"] == "cancelled"
    post = get_post(me.browser, started["post"]["id"])
    assert post["state"] == "published" and post["url"] == "https://youtube.com/shorts/mockvid1"
    assert post["error"] is None and post["publishedAt"] is not None
    assert get_project(me.browser, project["id"])["status"] == "posted"


def test_cancelling_a_retry_keeps_post_failed(app):
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    MockYouTube.fail_with = "upload_failed"
    published(me.browser, render_id)
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "post_failed"
    retry = published(me.browser, render_id)
    assert get_project(me.browser, project["id"])["status"] == "posting"
    cancel(me.browser, retry["job"]["id"])
    assert get_project(me.browser, project["id"])["status"] == "post_failed"


def test_cancelling_the_post_of_an_outdated_render_gives_back_the_edited_status(app):
    """The Reel was edited after its render (status `ready`), then the older render was
    published with confirmOutdated and cancelled: the Reel is as it was before, `ready`."""
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    scene = project["scenes"][0]
    me.browser.patch(f"/scenes/{scene['id']}", {"onScreenText": "Edited after the render"})
    assert get_project(me.browser, project["id"])["status"] == "ready"
    started = published(me.browser, render_id, {**BODY, "confirmOutdated": True})
    cancel(me.browser, started["job"]["id"])
    assert get_project(me.browser, project["id"])["status"] == "ready"


def test_a_publish_cancelled_while_the_reel_was_generating_gives_the_reel_back(app):
    """F5 is allowed while scenes are being generated (the design refuses only rendering and
    posting). Cancelling it must give the Reel back its generation: once the voice is made, the
    Reel is `ready`, as it would have been without the publish."""
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    scene = project["scenes"][0]
    res = me.browser.patch(
        f"/scenes/{scene['id']}", {"narration": "A completely new first line for this Reel."}
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    res = me.browser.post(f"/projects/{project['id']}/generate")
    assert res.status_code == 202, res.get_data(as_text=True)
    assert get_project(me.browser, project["id"])["status"] == "generating"
    started = published(me.browser, render_id, {**BODY, "confirmOutdated": True})
    cancel(me.browser, started["job"]["id"])
    run_jobs(app)  # the voice job ends
    after = get_project(me.browser, project["id"])
    assert all(s["voiceStatus"] == "ready" for s in after["scenes"])
    assert after["status"] == "ready", after["status"]


# ---------------------------------------------------------------- F6 and the posts list


def test_f6_and_the_posts_list(app):
    me = creator(app)
    project, render_id = ready_to_publish(app, me.browser)
    MockYouTube.fail_with = "upload_failed"
    first = published(me.browser, render_id)["post"]
    run_jobs(app)
    second = published(me.browser, render_id)["post"]
    run_jobs(app)
    res = me.browser.get(f"/projects/{project['id']}/posts")
    assert res.status_code == 200
    items = res.get_json()["items"]
    assert [p["id"] for p in items] == [second["id"], first["id"]]
    assert all(set(p) == POST_FIELDS for p in items)
    assert [p["state"] for p in items] == ["published", "failed"]
    assert items[1]["error"]["code"] == "upload_failed" and items[0]["error"] is None
    assert get_project(me.browser, project["id"])["latestPost"] == items[0]


def test_f6_and_the_posts_list_belong_to_their_owner(app):
    owner, other = creator(app), creator(app)
    project, render_id = ready_to_publish(app, owner.browser)
    post = published(owner.browser, render_id)["post"]
    assert_error(other.browser.get(f"/posts/{post['id']}"), 404, "not_found")
    assert_error(other.browser.get(f"/projects/{project['id']}/posts"), 404, "not_found")
    assert_error(owner.browser.get("/posts/not-a-uuid"), 404, "not_found")
    assert_error(owner.browser.get("/posts/00000000-0000-0000-0000-000000000000"), 404, "not_found")
    assert_error(Browser(app).get(f"/posts/{post['id']}"), 401, "not_authenticated")
    # A deleted Reel's posts are gone with it.
    run_jobs(app)
    assert owner.browser.delete(f"/projects/{project['id']}").status_code in (200, 204)
    assert_error(owner.browser.get(f"/posts/{post['id']}"), 404, "not_found")
