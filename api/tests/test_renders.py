"""E1 to E3 rendering (milestone B5): docs/API_DESIGN.md group E, PRD 11.6 R-1 to R-4.
Runs with the offline render stand-in (RENDER_SERVICE_URL=mock)."""

from __future__ import annotations

import pytest

from reelforge_api.renders import MockRenderService

from .projects_support import balance, creator, get_job, get_project, run_jobs, scripted_project
from .support import assert_error
from .voice_support import no_real_voice_services  # noqa: F401 - autouse fixture


@pytest.fixture(autouse=True)
def fresh_mock():
    MockRenderService.fail_next = 0
    MockRenderService.started.clear()
    yield
    MockRenderService.fail_next = 0


def ready_project(app, browser) -> dict:
    project = scripted_project(app, browser)
    assert browser.post(f"/projects/{project['id']}/generate").status_code == 202
    run_jobs(app)
    project = get_project(browser, project["id"])
    assert project["status"] == "ready", project["status"]
    return project


def render(browser, project_id: str):
    return browser.post(f"/projects/{project_id}/render")


def test_a_render_makes_the_mp4_and_cover_and_the_reel_is_rendered(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)

    res = render(me.browser, project["id"])
    assert res.status_code == 202, res.get_data(as_text=True)
    body = res.get_json()
    assert body["creditsReserved"] == 2 and body["balance"] == before - 2
    assert body["render"]["state"] == "queued" and body["render"]["video"] is None
    assert get_project(me.browser, project["id"])["status"] == "rendering"

    run_jobs(app)
    done = me.browser.get(f"/renders/{body['render']['id']}").get_json()["render"]
    assert done["state"] == "succeeded" and done["progress"] == 100 and done["outdated"] is False
    assert done["video"]["sizeBytes"] > 0 and done["video"]["downloadUrl"] and done["thumbnailUrl"]
    after = get_project(me.browser, project["id"])
    assert after["status"] == "rendered" and after["latestRender"]["id"] == done["id"]
    assert after["thumbnailUrl"]
    assert balance(me.browser) == before - 2
    sent = MockRenderService.started[0]["timeline"]
    assert "onMediaError" not in sent
    assert all(s["visual"]["src"].startswith("http") for s in sent["scenes"])
    history = me.browser.get(f"/projects/{project['id']}/renders").get_json()["items"]
    assert [r["id"] for r in history] == [done["id"]]


def test_a_failed_attempt_is_retried_once_then_fails_with_a_refund(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)

    MockRenderService.fail_next = 1
    first = render(me.browser, project["id"]).get_json()
    run_jobs(app)
    assert me.browser.get(f"/renders/{first['render']['id']}").get_json()["render"]["state"] == "succeeded"
    assert len(MockRenderService.started) == 2

    # Edit so a new render is allowed, then fail both attempts.
    scene = project["scenes"][0]
    assert me.browser.patch(f"/scenes/{scene['id']}", {"onScreenText": "New words"}).status_code == 200
    spent = balance(me.browser)
    MockRenderService.fail_next = 2
    second = render(me.browser, project["id"]).get_json()
    run_jobs(app)
    failed = me.browser.get(f"/renders/{second['render']['id']}").get_json()["render"]
    assert failed["state"] == "failed" and failed["error"]["code"] == "render_failed"
    assert get_job(me.browser, second["job"]["id"])["creditsRefunded"] is True
    assert balance(me.browser) == spent and spent == before - 2
    assert get_project(me.browser, project["id"])["status"] == "render_failed"


def test_an_edit_during_the_render_makes_it_outdated(app):
    me = creator(app)
    project = ready_project(app, me.browser)
    started = render(me.browser, project["id"]).get_json()
    scene = project["scenes"][0]
    assert me.browser.patch(f"/scenes/{scene['id']}", {"onScreenText": "Changed"}).status_code == 200
    assert get_project(me.browser, project["id"])["status"] == "rendering"
    run_jobs(app)
    done = me.browser.get(f"/renders/{started['render']['id']}").get_json()["render"]
    assert done["state"] == "succeeded" and done["outdated"] is True
    assert get_project(me.browser, project["id"])["status"] == "ready"


def test_refusals(app):
    me = creator(app)
    draft = scripted_project(app, me.browser)
    res = assert_error(render(me.browser, draft["id"]), 409, "not_ready")
    assert sorted(res["details"]["scenes"]) == sorted(s["id"] for s in draft["scenes"])

    project = ready_project(app, me.browser)
    assert render(me.browser, project["id"]).status_code == 202
    assert_error(render(me.browser, project["id"]), 409, "already_running")

    other = creator(app)
    assert other.browser.get(f"/projects/{project['id']}/renders").status_code == 404
    run_jobs(app)
