"""E1 to E3 rendering (milestone B5) with the offline render stand-in (RENDER_SERVICE_URL=mock).

Checked against docs/API_DESIGN.md group E and the Render object, PRD 11.6 (R-1 to R-4) and
CR-1/CR-2, TECHNICAL_DESIGN "Render service". The starter file test_renders.py covers the
basic success, retry and outdated paths; this file covers the refusals and their order,
idempotency, credits, cancel and delete during a render, project statuses, the JSON and the
production settings.
"""

from __future__ import annotations

import threading
import urllib.request

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from pydantic import ValidationError

from reelforge_api import jobs
from reelforge_api.config import Settings
from reelforge_api.renders import MockRenderService

from .projects_support import (
    ISO_UTC,
    assert_ledger_matches_balance,
    balance,
    creator,
    get_job,
    get_project,
    job_ledger,
    run_jobs,
    scripted_project,
    set_balance,
    set_statuses,
)
from .render_support import (
    RENDER_COST,
    RENDER_FIELDS,
    RENDER_STATES,
    VIDEO_FIELDS,
    get_render,
    history,
    in_another_thread,
    listed,
    ready_project,
    render_keys,
    reset_mock,
    second_browser,
    start_render,
    started,
)
from .support import Browser, assert_error, sign_up
from .test_worker_and_config import PRODUCTION
from .voice_support import no_real_voice_services  # noqa: F401 - autouse fixture

# Production settings with real providers named, so the container's *_PROVIDERS=mock
# environment (the offline test stack) can't be the reason a setting is refused.
REAL_PROVIDERS = {
    "voice_providers": "elevenlabs,edge",
    "stock_providers": "pixabay",
    "image_providers": "cloudflare",
}
PRODUCTION_SETTINGS = {**PRODUCTION, **REAL_PROVIDERS}

# Two words of mock voice: a 1.5 s scene, under the 3 s minimum.
SHORT_SCRIPT = "Go now."

# 46 sentences of 5 words: 230 words, 92 s of mock voice (2.5 words a second): over 90 s.
LONG_SCRIPT = " ".join(["Morning light feels calm today."] * 46)


@pytest.fixture(autouse=True)
def fresh_mock():
    reset_mock()
    yield
    reset_mock()


@pytest.fixture
def cancels(monkeypatch) -> list[str]:
    """Every render the worker told the service to stop."""
    calls: list[str] = []
    real = MockRenderService.cancel

    def cancel(self, service_id):
        calls.append(service_id)
        real(self, service_id)

    monkeypatch.setattr(MockRenderService, "cancel", cancel)
    return calls


def while_running(monkeypatch, on_poll, *, polls: int = 2, progress: int = 50) -> None:
    """The stand-in reports `running` for the first `polls` polls, calling on_poll(n) on each
    (from the worker, before it answers), then the real result."""
    real = MockRenderService.status
    count = {"n": 0}

    def status(self, service_id):
        count["n"] += 1
        on_poll(count["n"])
        if count["n"] <= polls:
            return {"state": "running", "progress": progress, "error": None}
        return real(self, service_id)

    monkeypatch.setattr(MockRenderService, "status", status)


def storage(app):
    return app.extensions["storage"]


def exists(app, key: str) -> bool:
    with app.app_context():
        return storage(app).exists(key)


def voice_key(database, scene_id: str) -> str:
    return database.value(
        "SELECT a.s3_key FROM scenes s JOIN assets a ON a.id = s.voice_asset_id WHERE s.id = :s", s=scene_id
    )


# ---------------------------------------------------------------- E1 refusals


def test_a_draft_is_not_ready_and_lists_every_scene_that_is_not(app):
    me = creator(app)
    draft = scripted_project(app, me.browser)
    error = assert_error(start_render(me.browser, draft["id"]), 409, "not_ready")
    assert sorted(error["details"]["scenes"]) == sorted(s["id"] for s in draft["scenes"])
    assert history(me.browser, draft["id"]) == []
    assert get_project(me.browser, draft["id"])["status"] == "draft"


def test_only_the_scenes_that_are_not_ready_are_listed(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    edited = project["scenes"][-1]["id"]
    res = me.browser.patch(f"/scenes/{edited}", {"narration": "A completely new line for the ending."})
    assert res.status_code == 200
    error = assert_error(start_render(me.browser, project["id"]), 409, "not_ready")
    assert error["details"]["scenes"] == [edited]


def test_a_reel_that_is_generating_is_not_ready(app):
    me = creator(app)
    project = scripted_project(app, me.browser)
    assert me.browser.post(f"/projects/{project['id']}/generate").status_code == 202
    assert get_project(me.browser, project["id"])["status"] == "generating"
    error = assert_error(start_render(me.browser, project["id"]), 409, "not_ready")
    assert "scenes" in error["details"]
    run_jobs(app)


def test_a_reel_whose_script_is_being_written_is_not_ready(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    assert me.browser.post(f"/projects/{project['id']}/script/regenerate").status_code == 202
    assert get_project(me.browser, project["id"])["status"] == "scripting"
    assert_error(start_render(me.browser, project["id"]), 409, "not_ready")
    run_jobs(app)


def test_a_reel_over_90_seconds_is_too_long(app):
    me = creator(app)
    project = ready_project(app, me.browser, script=LONG_SCRIPT)
    timeline = me.browser.get(f"/projects/{project['id']}/timeline").get_json()
    assert timeline["tooLong"] is True, timeline["durationSeconds"]
    before = balance(me.browser)
    assert_error(start_render(me.browser, project["id"]), 409, "too_long")
    assert balance(me.browser) == before and history(me.browser, project["id"]) == []


def test_too_long_names_the_length(app):
    """API design error table: `409 too_long` carries `details.seconds`."""
    me = creator(app)
    project = ready_project(app, me.browser, script=LONG_SCRIPT)
    seconds = me.browser.get(f"/projects/{project['id']}/timeline").get_json()["durationSeconds"]
    error = assert_error(start_render(me.browser, project["id"]), 409, "too_long")
    assert (error.get("details") or {}).get("seconds") == seconds


def test_a_reel_under_3_seconds_is_too_short(app):
    """E1 (QA round 1): Instagram refuses Reels under 3 s; nothing is charged."""
    me = creator(app)
    project = ready_project(app, me.browser, script=SHORT_SCRIPT)
    seconds = me.browser.get(f"/projects/{project['id']}/timeline").get_json()["durationSeconds"]
    assert seconds < 3, seconds
    before = balance(me.browser)
    assert_error(start_render(me.browser, project["id"]), 409, "too_short")
    assert balance(me.browser) == before and history(me.browser, project["id"]) == []
    assert get_project(me.browser, project["id"])["status"] == "ready"


def test_a_reel_of_3_seconds_or_more_is_not_too_short(app):
    me = creator(app)
    project = ready_project(app, me.browser, script="Drink a full glass of water first thing every morning.")
    seconds = me.browser.get(f"/projects/{project['id']}/timeline").get_json()["durationSeconds"]
    assert seconds >= 3, seconds
    started(me.browser, project["id"])
    run_jobs(app)


def test_the_same_reel_cannot_render_twice_at_once(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    started(me.browser, project["id"])
    spent = balance(me.browser)
    assert_error(start_render(me.browser, project["id"]), 409, "already_running")
    assert balance(me.browser) == spent and len(history(me.browser, project["id"])) == 1
    run_jobs(app)


def test_a_reel_that_is_posting_cannot_render(app, database):
    me = creator(app)
    project = ready_project(app, me.browser)
    set_statuses(database, project["id"], status="posting")
    assert_error(start_render(me.browser, project["id"]), 409, "already_running")


def test_only_one_of_a_creators_reels_renders_at_a_time(app):
    """R-4. Another creator's render does not block this one."""
    me, stranger = creator(app), creator(app)
    first, second = ready_project(app, me.browser), ready_project(app, me.browser)
    theirs = ready_project(app, stranger.browser)
    started(me.browser, first["id"])
    error = assert_error(start_render(me.browser, second["id"]), 409, "already_running")
    assert "another" in error["message"].lower()
    assert get_project(me.browser, second["id"])["status"] == "ready"
    started(stranger.browser, theirs["id"])
    run_jobs(app)
    # Once the first has finished, the second may render.
    started(me.browser, second["id"])
    run_jobs(app)
    assert get_project(me.browser, second["id"])["status"] == "rendered"


def test_two_renders_sent_at_the_same_moment_start_only_one(app):
    """R-4 under a race: two of a creator's Reels, each asked for from its own browser at once."""
    me = creator(app)
    projects = [ready_project(app, me.browser), ready_project(app, me.browser)]
    browsers = [second_browser(app, me.account), second_browser(app, me.account)]
    before = balance(me.browser)
    gate = threading.Barrier(2)
    answers: list = [None, None]

    def ask(i):
        gate.wait()
        answers[i] = start_render(browsers[i], projects[i]["id"])

    threads = [threading.Thread(target=ask, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    codes = sorted(a.status_code for a in answers)
    assert codes == [202, 409], [a.get_data(as_text=True) for a in answers]
    refused = next(a for a in answers if a.status_code == 409)
    assert refused.get_json()["error"]["code"] == "already_running"
    assert balance(me.browser) == before - RENDER_COST  # one render's credits, not two
    assert sum(len(history(me.browser, p["id"])) for p in projects) == 1
    run_jobs(app)


def test_not_enough_credits_is_402_and_nothing_starts(app, database):
    me = creator(app)
    project = ready_project(app, me.browser)
    set_balance(database, me.id, RENDER_COST - 1)
    error = assert_error(start_render(me.browser, project["id"]), 402, "insufficient_credits")
    assert error["details"]["needed"] == RENDER_COST and error["details"]["balance"] == RENDER_COST - 1
    assert history(me.browser, project["id"]) == []
    assert get_project(me.browser, project["id"])["status"] == "ready"
    assert database.value("SELECT count(*) FROM jobs WHERE type = 'render'") == 0
    set_balance(database, me.id, RENDER_COST)
    started(me.browser, project["id"])
    assert balance(me.browser) == 0
    run_jobs(app)


def test_refusals_come_in_the_documented_order_when_another_reel_renders(app, database):
    """E1: not_ready, then too_long, then too_short, then already_running, then 402."""
    me = creator(app)
    rendering = ready_project(app, me.browser)
    long_one = ready_project(app, me.browser, script=LONG_SCRIPT)
    short_one = ready_project(app, me.browser, script=SHORT_SCRIPT)
    draft = scripted_project(app, me.browser)
    other = ready_project(app, me.browser)
    started(me.browser, rendering["id"])
    set_balance(database, me.id, 0)

    assert_error(start_render(me.browser, draft["id"]), 409, "not_ready")
    assert_error(start_render(me.browser, long_one["id"]), 409, "too_long")
    assert_error(start_render(me.browser, short_one["id"]), 409, "too_short")
    assert_error(start_render(me.browser, other["id"]), 409, "already_running")
    run_jobs(app)
    assert_error(start_render(me.browser, other["id"]), 402, "insufficient_credits")


def test_a_rendering_reel_with_a_scene_no_longer_ready_answers_not_ready_first(app):
    """E1 lists not_ready before already_running ("checked in this order"). A Reel edited
    while it renders can have a scene that is no longer ready."""
    me = creator(app)
    project = ready_project(app, me.browser)
    started(me.browser, project["id"])
    scene = project["scenes"][0]["id"]
    assert me.browser.patch(f"/scenes/{scene}", {"narration": "Brand new words here."}).status_code == 200
    error = assert_error(start_render(me.browser, project["id"]), 409, "not_ready")
    assert error["details"]["scenes"] == [scene]
    run_jobs(app)


def test_render_needs_a_verified_owner(app):
    owner = creator(app)
    project = ready_project(app, owner.browser)
    stranger = creator(app)
    assert_error(start_render(stranger.browser, project["id"]), 404, "not_found")
    assert_error(start_render(owner.browser, "not-a-uuid"), 404, "not_found")
    assert_error(start_render(owner.browser, "00000000-0000-4000-8000-000000000000"), 404, "not_found")
    assert_error(start_render(Browser(app), project["id"]), 401, "not_authenticated")
    unverified = sign_up(app, verified=False)
    assert_error(start_render(unverified.browser, project["id"]), 403, "email_not_verified")
    assert history(owner.browser, project["id"]) == []


def test_a_deleted_project_cannot_render(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    assert me.browser.delete(f"/projects/{project['id']}").status_code == 204
    assert_error(start_render(me.browser, project["id"]), 404, "not_found")


# ---------------------------------------------------------------- idempotency


def test_the_same_idempotency_key_starts_one_render(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    first = start_render(me.browser, project["id"], key="render-1")
    again = start_render(me.browser, project["id"], key="render-1")
    assert first.status_code == again.status_code == 202
    assert again.get_json() == first.get_json()
    assert balance(me.browser) == before - RENDER_COST
    assert len(history(me.browser, project["id"])) == 1
    # A different body with the same key is a conflict, not a second render.
    conflict = start_render(me.browser, project["id"], key="render-1", body={"other": True})
    assert_error(conflict, 409, "idempotency_conflict")
    run_jobs(app)
    # Still the first answer after the render finished.
    assert start_render(me.browser, project["id"], key="render-1").get_json() == first.get_json()
    assert len(history(me.browser, project["id"])) == 1


def test_a_refused_render_can_be_retried_with_its_key(app):
    me = creator(app)
    project = scripted_project(app, me.browser)
    assert_error(start_render(me.browser, project["id"], key="k-ready"), 409, "not_ready")
    assert me.browser.post(f"/projects/{project['id']}/generate").status_code == 202
    run_jobs(app)
    assert start_render(me.browser, project["id"], key="k-ready").status_code == 202
    run_jobs(app)


# ---------------------------------------------------------------- credits


def test_a_render_reserves_its_credits_and_keeps_them_when_it_succeeds(app, database):
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    body = started(me.browser, project["id"])
    assert body["creditsReserved"] == RENDER_COST and body["balance"] == before - RENDER_COST
    assert body["job"]["type"] == "render" and body["job"]["creditsReserved"] == RENDER_COST
    assert body["job"]["projectId"] == project["id"] and body["job"]["sceneId"] is None
    assert body["render"]["jobId"] == body["job"]["id"]
    run_jobs(app)
    job = get_job(me.browser, body["job"]["id"])
    assert job["state"] == "succeeded" and job["creditsRefunded"] is False and job["provider"] == "remotion"
    assert job_ledger(database, body["job"]["id"]) == [("reserve", -RENDER_COST)]
    assert balance(me.browser) == before - RENDER_COST
    assert_ledger_matches_balance(database, me.id)


def test_a_failed_render_refunds_and_says_why(app, database):
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    MockRenderService.fail_next = 2
    body = started(me.browser, project["id"])
    run_jobs(app)
    assert len(MockRenderService.started) == 2  # R-1: tried once more
    job = get_job(me.browser, body["job"]["id"])
    assert job["state"] == "failed" and job["creditsRefunded"] is True
    assert job["error"] == {"code": "render_failed", "message": jobs.MESSAGES["render_failed"]}
    assert job_ledger(database, body["job"]["id"]) == [("reserve", -RENDER_COST), ("refund", RENDER_COST)]
    assert balance(me.browser) == before
    assert_ledger_matches_balance(database, me.id)
    render = get_render(me.browser, body["render"]["id"])
    assert render["state"] == "failed" and render["video"] is None and render["thumbnailUrl"] is None
    assert render["error"] == {"code": "render_failed", "message": jobs.MESSAGES["render_failed"]}
    assert render["finishedAt"] and ISO_UTC.match(render["finishedAt"])
    project = get_project(me.browser, project["id"])
    assert project["status"] == "render_failed" and project["thumbnailUrl"] is None
    assert project["latestRender"]["id"] == render["id"]


def test_each_attempt_is_a_separate_render_at_the_service_and_one_charge(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    MockRenderService.fail_next = 1
    body = started(me.browser, project["id"])
    run_jobs(app)
    ids = [s["id"] for s in MockRenderService.started]
    assert ids == [f"{body['render']['id']}-1", f"{body['render']['id']}-2"]
    assert get_render(me.browser, body["render"]["id"])["state"] == "succeeded"
    assert balance(me.browser) == before - RENDER_COST


def test_no_second_attempt_when_it_could_not_finish_in_time(app, monkeypatch):
    """R-1's retry only starts if the time left is more than the first attempt took (QA rounds
    1 and 2). With a limit of 0 s, no retry can finish."""
    monkeypatch.setattr(app.extensions["settings"], "render_time_limit_seconds", 0)
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    MockRenderService.fail_next = 1
    body = started(me.browser, project["id"])
    run_jobs(app)
    assert [s["id"] for s in MockRenderService.started] == [f"{body['render']['id']}-1"]
    job = get_job(me.browser, body["job"]["id"])
    assert job["state"] == "failed" and job["error"]["code"] == "render_failed" and job["creditsRefunded"]
    assert get_project(me.browser, project["id"])["status"] == "render_failed"
    assert balance(me.browser) == before


def test_the_stuck_job_sweep_waits_longer_than_celery_runs_a_render(app):
    """A running render is never failed by the sweep while Celery may still be running it, and
    Redis never delivers a running render again (its visibility timeout outlasts it)."""
    with app.app_context():
        soft, hard = jobs.task_limits("render")
    assert soft == app.extensions["settings"].render_time_limit_seconds and soft < hard
    redelivery = app.config["CELERY"]["broker_transport_options"]["visibility_timeout"]
    assert redelivery > hard


def test_a_render_after_a_failure_works(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    MockRenderService.fail_next = 2
    started(me.browser, project["id"])
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "render_failed"
    second = started(me.browser, project["id"])
    assert get_project(me.browser, project["id"])["status"] == "rendering"
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "rendered"
    assert [r["id"] for r in history(me.browser, project["id"])][0] == second["render"]["id"]


# ---------------------------------------------------------------- R-2: files checked first


def test_a_missing_file_fails_with_media_missing_without_reaching_the_service(app, database):
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    body = started(me.browser, project["id"])
    with app.app_context():
        storage(app).delete(voice_key(database, project["scenes"][0]["id"]))
    run_jobs(app)
    assert MockRenderService.started == []
    job = get_job(me.browser, body["job"]["id"])
    assert job["state"] == "failed" and job["creditsRefunded"] is True
    assert job["error"] == {"code": "media_missing", "message": jobs.MESSAGES["media_missing"]}
    render = get_render(me.browser, body["render"]["id"])
    assert render["state"] == "failed"
    assert render["error"] == {"code": "media_missing", "message": jobs.MESSAGES["media_missing"]}
    assert get_project(me.browser, project["id"])["status"] == "render_failed"
    assert balance(me.browser) == before


def test_the_service_gets_fresh_download_links_for_every_file(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    started(me.browser, project["id"])
    run_jobs(app)
    sent = MockRenderService.started[0]["timeline"]
    links = [s[part]["src"] for s in sent["scenes"] for part in ("visual", "voice")]
    if sent.get("music"):
        links.append(sent["music"]["src"])
    assert links and all(link.startswith("http") and "Signature" in link for link in links)
    for link in links:
        with urllib.request.urlopen(link, timeout=5) as answer:  # noqa: S310 - the test storage
            assert answer.status == 200 and answer.read()


# ---------------------------------------------------------------- R-3: edits while rendering


def test_the_render_uses_the_timeline_as_it_was_when_it_started(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    scene = project["scenes"][0]
    body = started(me.browser, project["id"])
    assert body["render"]["timelineVersion"] == project["timelineVersion"]
    res = me.browser.patch(f"/scenes/{scene['id']}", {"onScreenText": "Edited during the render"})
    assert res.status_code == 200
    run_jobs(app)
    sent = MockRenderService.started[0]["timeline"]
    assert sent["scenes"][0]["onScreenText"] == scene["onScreenText"]
    render = get_render(me.browser, body["render"]["id"])
    assert render["timelineVersion"] == project["timelineVersion"] and render["outdated"] is True


def test_an_edit_that_outdates_a_voice_during_the_render_leaves_a_draft(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    body = started(me.browser, project["id"])
    scene = project["scenes"][0]["id"]
    assert me.browser.patch(f"/scenes/{scene}", {"narration": "Different words now."}).status_code == 200
    assert get_project(me.browser, project["id"])["status"] == "rendering"
    run_jobs(app)
    assert get_render(me.browser, body["render"]["id"])["state"] == "succeeded"
    after = get_project(me.browser, project["id"])
    assert after["status"] == "draft" and after["latestRender"]["outdated"] is True
    assert after["thumbnailUrl"]  # the finished render's cover is still the Reel's cover


def test_a_render_that_fails_after_an_edit_leaves_the_status_any_edit_gives(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    MockRenderService.fail_next = 2
    body = started(me.browser, project["id"])
    scene = project["scenes"][0]["id"]
    assert me.browser.patch(f"/scenes/{scene}", {"onScreenText": "Changed"}).status_code == 200
    run_jobs(app)
    assert get_render(me.browser, body["render"]["id"])["state"] == "failed"
    assert get_project(me.browser, project["id"])["status"] == "ready"
    assert get_job(me.browser, body["job"]["id"])["creditsRefunded"] is True


def test_a_render_of_the_current_version_is_not_outdated_until_the_next_edit(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    body = started(me.browser, project["id"])
    run_jobs(app)
    assert get_render(me.browser, body["render"]["id"])["outdated"] is False
    scene = project["scenes"][0]["id"]
    assert me.browser.patch(f"/scenes/{scene}", {"onScreenText": "Later"}).status_code == 200
    assert get_render(me.browser, body["render"]["id"])["outdated"] is True
    after = get_project(me.browser, project["id"])
    assert after["status"] == "ready" and after["latestRender"]["outdated"] is True


# ---------------------------------------------------------------- generate and script while rendering


def test_generate_and_a_new_script_are_refused_while_rendering(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    started(me.browser, project["id"])
    spent = balance(me.browser)
    assert_error(me.browser.post(f"/projects/{project['id']}/generate"), 409, "already_running")
    assert_error(me.browser.post(f"/projects/{project['id']}/script/regenerate"), 409, "already_running")
    assert balance(me.browser) == spent
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "rendered"


# ---------------------------------------------------------------- cancel (G3) and delete


def test_cancelling_a_queued_render_refunds_it_and_it_never_reaches_the_service(app, database):
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    body = started(me.browser, project["id"])
    res = me.browser.post(f"/jobs/{body['job']['id']}/cancel")
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["job"]["state"] == "cancelled" and res.get_json()["job"]["creditsRefunded"] is True
    render = get_render(me.browser, body["render"]["id"])
    assert render["state"] == "cancelled" and render["error"] is None and render["finishedAt"]
    assert get_project(me.browser, project["id"])["status"] == "ready"
    assert balance(me.browser) == before
    run_jobs(app)
    assert MockRenderService.started == []
    assert_error(me.browser.post(f"/jobs/{body['job']['id']}/cancel"), 409, "job_finished")
    assert_ledger_matches_balance(database, me.id)


def test_cancelling_a_running_render_stops_it_at_the_service_and_keeps_no_files(app, monkeypatch, cancels):
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    body = started(me.browser, project["id"])
    other = second_browser(app, me.account)
    seen: dict = {}

    def on_poll(n):
        if n == 2:  # the first poll's progress is saved by now
            seen["project"] = in_another_thread(get_project, other, project["id"])
            seen["render"] = in_another_thread(get_render, other, body["render"]["id"])
            seen["cancel"] = in_another_thread(other.post, f"/jobs/{body['job']['id']}/cancel")

    while_running(monkeypatch, on_poll, polls=5)
    run_jobs(app)

    assert seen["project"]["status"] == "rendering"
    assert 0 < seen["project"]["progress"] < 100
    assert seen["render"]["state"] == "running" and 0 < seen["render"]["progress"] < 100
    # The render, its job and the project show the same number.
    assert seen["render"]["progress"] == seen["project"]["progress"]
    assert seen["cancel"].status_code == 200
    assert cancels == [f"{body['render']['id']}-1"]
    render = get_render(me.browser, body["render"]["id"])
    assert render["state"] == "cancelled" and render["video"] is None
    job = get_job(me.browser, body["job"]["id"])
    assert job["state"] == "cancelled" and job["creditsRefunded"] is True
    assert balance(me.browser) == before
    assert get_project(me.browser, project["id"])["status"] == "ready"
    video, cover = render_keys(me.id, project["id"], body["render"]["id"])
    assert not exists(app, video) and not exists(app, cover)  # the stand-in had written them


def test_cancelling_a_new_render_of_a_rendered_reel_keeps_it_rendered(app):
    """The earlier MP4 is still of the current version, so the Reel is still rendered (the
    job keeps `previousStatus` for this)."""
    me = creator(app)
    project = ready_project(app, me.browser)
    started(me.browser, project["id"])
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "rendered"
    again = started(me.browser, project["id"])
    assert me.browser.post(f"/jobs/{again['job']['id']}/cancel").status_code == 200
    assert get_project(me.browser, project["id"])["status"] == "rendered"


def test_cancelling_a_new_render_after_a_failed_one_keeps_render_failed(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    MockRenderService.fail_next = 2
    started(me.browser, project["id"])
    run_jobs(app)
    again = started(me.browser, project["id"])
    assert me.browser.post(f"/jobs/{again['job']['id']}/cancel").status_code == 200
    assert get_project(me.browser, project["id"])["status"] == "render_failed"


def test_cancelling_after_an_edit_gives_the_status_any_edit_gives(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    started(me.browser, project["id"])
    run_jobs(app)
    again = started(me.browser, project["id"])
    scene = project["scenes"][0]["id"]
    assert me.browser.patch(f"/scenes/{scene}", {"narration": "Other words entirely."}).status_code == 200
    assert me.browser.post(f"/jobs/{again['job']['id']}/cancel").status_code == 200
    assert get_project(me.browser, project["id"])["status"] == "draft"


def test_the_project_shows_the_render_progress_while_it_runs(app, monkeypatch):
    me = creator(app)
    project = ready_project(app, me.browser)
    body = started(me.browser, project["id"])
    assert get_project(me.browser, project["id"])["progress"] == 0
    other = second_browser(app, me.account)
    seen: list = []

    def on_poll(n):
        if n >= 2:
            seen.append(
                (
                    in_another_thread(listed, other, project["id"])["progress"],
                    in_another_thread(get_job, other, body["job"]["id"])["progress"],
                )
            )

    while_running(monkeypatch, on_poll, polls=3, progress=50)
    run_jobs(app)
    assert seen and all(0 < listed_p < 100 and listed_p == job_p for listed_p, job_p in seen)
    done = listed(me.browser, project["id"])
    assert done["status"] == "rendered" and done["progress"] is None


def test_deleting_a_project_while_it_renders_stops_and_refunds_the_render(app, monkeypatch, cancels):
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    body = started(me.browser, project["id"])
    other = second_browser(app, me.account)
    deleted: dict = {}

    def on_poll(n):
        if n == 1:
            deleted["res"] = in_another_thread(other.delete, f"/projects/{project['id']}")

    while_running(monkeypatch, on_poll, polls=3)
    run_jobs(app)
    assert deleted["res"].status_code == 204
    assert cancels == [f"{body['render']['id']}-1"]
    assert get_job(me.browser, body["job"]["id"])["state"] == "cancelled"
    assert balance(me.browser) == before
    assert_error(me.browser.get(f"/renders/{body['render']['id']}"), 404, "not_found")
    assert_error(me.browser.get(f"/projects/{project['id']}/renders"), 404, "not_found")
    video, cover = render_keys(me.id, project["id"], body["render"]["id"])
    assert not exists(app, video) and not exists(app, cover)


def test_a_render_that_runs_out_of_time_stops_at_the_service_and_is_refunded(app, monkeypatch, cancels):
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    body = started(me.browser, project["id"])

    def on_poll(n):
        raise SoftTimeLimitExceeded()

    while_running(monkeypatch, on_poll)
    run_jobs(app)
    assert cancels == [f"{body['render']['id']}-1"]
    job = get_job(me.browser, body["job"]["id"])
    assert job["state"] == "failed" and job["error"]["code"] == "timed_out" and job["creditsRefunded"]
    render = get_render(me.browser, body["render"]["id"])
    assert render["state"] == "failed" and render["error"]["code"] == "timed_out"
    assert get_project(me.browser, project["id"])["status"] == "render_failed"
    assert balance(me.browser) == before


# ---------------------------------------------------------------- JSON


def test_the_render_object_has_the_documented_shape(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    title = 'Café "Morning" / habits'
    assert me.browser.patch(f"/projects/{project['id']}", {"title": title}).status_code == 200
    project = get_project(me.browser, project["id"])
    body = started(me.browser, project["id"])
    queued = body["render"]
    assert set(queued) == RENDER_FIELDS
    assert queued["state"] == "queued" and queued["progress"] == 0 and queued["projectId"] == project["id"]
    assert queued["video"] is None and queued["thumbnailUrl"] is None and queued["error"] is None
    assert ISO_UTC.match(queued["createdAt"]) and queued["finishedAt"] is None
    assert queued["timelineVersion"] == project["timelineVersion"] and queued["outdated"] is False
    run_jobs(app)
    done = get_render(me.browser, queued["id"])
    assert set(done) == RENDER_FIELDS and done["state"] in RENDER_STATES
    assert done["state"] == "succeeded" and done["progress"] == 100 and done["error"] is None
    assert set(done["video"]) == VIDEO_FIELDS
    seconds = me.browser.get(f"/projects/{project['id']}/timeline").get_json()["durationSeconds"]
    assert done["video"]["durationSeconds"] == pytest.approx(seconds, abs=0.05)
    assert done["video"]["sizeBytes"] > 0
    assert ISO_UTC.match(done["finishedAt"]) and done["finishedAt"] >= done["createdAt"]
    # The download link saves the file under the Reel's title, made safe for a header.
    download = urllib.request.unquote(done["video"]["downloadUrl"])
    assert 'attachment; filename="Caf-Morning-habits.mp4"' in download
    assert "attachment" not in urllib.request.unquote(done["video"]["url"])
    with urllib.request.urlopen(done["video"]["downloadUrl"], timeout=5) as answer:  # noqa: S310
        assert answer.status == 200 and answer.headers["Content-Disposition"].startswith("attachment")
        assert len(answer.read()) == done["video"]["sizeBytes"]
    with urllib.request.urlopen(done["thumbnailUrl"], timeout=5) as answer:  # noqa: S310
        assert answer.status == 200 and answer.read()


def test_a_title_with_no_safe_letters_downloads_as_reel_mp4(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    res = me.browser.patch(f"/projects/{project['id']}", {"title": "日本語のタイトル"})
    assert res.status_code == 200
    body = started(me.browser, project["id"])
    run_jobs(app)
    download = urllib.request.unquote(get_render(me.browser, body["render"]["id"])["video"]["downloadUrl"])
    assert 'filename="reel.mp4"' in download


def test_project_and_list_json_show_the_latest_render_and_its_cover(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    assert project["latestRender"] is None and project["thumbnailUrl"] is None
    assert listed(me.browser, project["id"])["thumbnailUrl"] is None

    first = started(me.browser, project["id"])
    rendering = get_project(me.browser, project["id"])
    assert rendering["latestRender"]["id"] == first["render"]["id"]
    assert rendering["latestRender"]["state"] == "queued"
    assert listed(me.browser, project["id"])["status"] == "rendering"
    run_jobs(app)

    done = get_project(me.browser, project["id"])
    summary = listed(me.browser, project["id"])
    assert done["status"] == summary["status"] == "rendered"
    assert done["latestRender"]["state"] == "succeeded"
    assert done["thumbnailUrl"] and summary["thumbnailUrl"]
    cover_address = done["latestRender"]["thumbnailUrl"].split("?")[0]
    assert done["thumbnailUrl"].split("?")[0] == summary["thumbnailUrl"].split("?")[0] == cover_address

    # A newer render that fails: it is the latest render, but the cover stays the finished one's.
    MockRenderService.fail_next = 2
    second = started(me.browser, project["id"])
    run_jobs(app)
    after = get_project(me.browser, project["id"])
    assert after["latestRender"]["id"] == second["render"]["id"]
    assert after["latestRender"]["state"] == "failed"
    assert after["thumbnailUrl"].split("?")[0] == cover_address
    assert listed(me.browser, project["id"])["thumbnailUrl"].split("?")[0] == cover_address
    assert [r["id"] for r in history(me.browser, project["id"])] == [
        second["render"]["id"],
        first["render"]["id"],
    ]


def test_a_new_finished_render_replaces_the_cover(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    started(me.browser, project["id"])
    run_jobs(app)
    first_cover = get_project(me.browser, project["id"])["thumbnailUrl"].split("?")[0]
    second = started(me.browser, project["id"])
    run_jobs(app)
    after = get_project(me.browser, project["id"])
    assert after["thumbnailUrl"].split("?")[0] != first_cover
    assert (
        after["thumbnailUrl"].split("?")[0]
        == get_render(me.browser, second["render"]["id"])["thumbnailUrl"].split("?")[0]
    )


def test_a_duplicate_starts_without_renders(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    started(me.browser, project["id"])
    run_jobs(app)
    copy = me.browser.post(f"/projects/{project['id']}/duplicate").get_json()["project"]
    assert copy["latestRender"] is None and copy["thumbnailUrl"] is None
    assert history(me.browser, copy["id"]) == []


def test_history_is_newest_first_and_holds_the_latest_50(app, database):
    me = creator(app)
    project = ready_project(app, me.browser)
    for i in range(55):
        database.run(
            "INSERT INTO renders (project_id, timeline_version, timeline, state, created_at) "
            "VALUES (:p, 1, '{}'::jsonb, 'failed', now() - make_interval(mins => :i))",
            p=project["id"],
            i=i,
        )
    items = history(me.browser, project["id"])
    assert len(items) == 50
    created = [r["createdAt"] for r in items]
    assert created == sorted(created, reverse=True)


# ---------------------------------------------------------------- E2 and E3 ownership


def test_renders_are_only_visible_to_their_owner(app):
    owner, stranger = creator(app), creator(app)
    project = ready_project(app, owner.browser)
    body = started(owner.browser, project["id"])
    run_jobs(app)
    render_id = body["render"]["id"]
    assert_error(stranger.browser.get(f"/renders/{render_id}"), 404, "not_found")
    assert_error(stranger.browser.get(f"/projects/{project['id']}/renders"), 404, "not_found")
    assert_error(owner.browser.get("/renders/not-a-uuid"), 404, "not_found")
    assert_error(owner.browser.get("/renders/00000000-0000-4000-8000-000000000000"), 404, "not_found")
    assert_error(Browser(app).get(f"/renders/{render_id}"), 401, "not_authenticated")
    assert_error(Browser(app).get(f"/projects/{project['id']}/renders"), 401, "not_authenticated")
    # An unverified creator can still read their own renders (E2, E3 are "User").
    assert get_render(owner.browser, render_id)["id"] == render_id


def test_the_renders_of_a_deleted_project_are_gone(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    body = started(me.browser, project["id"])
    run_jobs(app)
    assert me.browser.delete(f"/projects/{project['id']}").status_code == 204
    assert_error(me.browser.get(f"/renders/{body['render']['id']}"), 404, "not_found")
    assert_error(me.browser.get(f"/projects/{project['id']}/renders"), 404, "not_found")


def test_a_cancelled_or_failed_render_is_not_running_for_r4(app):
    """After a cancel or a failure the creator may render another Reel straight away."""
    me = creator(app)
    first, second = ready_project(app, me.browser), ready_project(app, me.browser)
    body = started(me.browser, first["id"])
    assert me.browser.post(f"/jobs/{body['job']['id']}/cancel").status_code == 200
    MockRenderService.fail_next = 2
    started(me.browser, second["id"])
    run_jobs(app)
    started(me.browser, first["id"])
    run_jobs(app)
    assert get_project(me.browser, first["id"])["status"] == "rendered"


# ---------------------------------------------------------------- production settings


@pytest.mark.parametrize(
    "unsafe",
    [
        {"render_service_url": "mock"},
        {"render_service_url": ""},
        {"render_service_token": ""},
        {"render_service_token": "Vd6tKs2wQm9xLp4hVd6tKs2wQm9xLp4"},  # 31 characters
        {"render_service_token": "dev-only-render-token-padded-to-be-long-enough"},
    ],
    ids=["mock", "empty-url", "no-token", "short-token", "dev-token"],
)
def test_production_refuses_an_unsafe_render_service_setting(unsafe):
    with pytest.raises(ValidationError, match=r"RENDER_SERVICE"):
        Settings(**{**PRODUCTION_SETTINGS, **unsafe})


@pytest.mark.parametrize(
    ("forgotten", "variable"),
    [("render_service_url", "RENDER_SERVICE_URL"), ("render_service_token", "RENDER_SERVICE_TOKEN")],
)
def test_production_refuses_a_forgotten_render_service_setting(monkeypatch, forgotten, variable):
    monkeypatch.delenv(variable, raising=False)
    with pytest.raises(ValidationError, match=r"RENDER_SERVICE"):
        Settings(**{k: v for k, v in PRODUCTION_SETTINGS.items() if k != forgotten})


def test_production_accepts_a_real_render_service():
    assert Settings(**PRODUCTION_SETTINGS).render_service_url == PRODUCTION["render_service_url"]


def test_development_may_use_the_offline_stand_in():
    assert Settings(**{**PRODUCTION_SETTINGS, "env": "development", "render_service_url": "mock"})
