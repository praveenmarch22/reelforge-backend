"""The daily autopilot (docs/AUTOPILOT.md): the queue, a full run with the offline mocks, the
approval step, and the failure cases."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from reelforge_api import autopilot
from reelforge_api.providers.publish import MockYouTube

from .projects_support import creator, get_project, run_jobs
from .support import assert_error
from .test_publishing import connect
from .voice_support import no_real_voice_services  # noqa: F401 - autouse fixture


@pytest.fixture(autouse=True)
def fresh_mock():
    MockYouTube.fail_with, MockYouTube.no_channel, MockYouTube.processing = None, False, "processed"
    MockYouTube.uploads.clear()
    yield


def advance(app, rounds: int = 12) -> None:
    """What the scheduler and the workers do, until nothing moves any more."""
    for _ in range(rounds):
        with app.app_context():
            autopilot.advance_all()
        if not run_jobs(app):
            with app.app_context():
                if not autopilot.advance_all():
                    return


def state(browser) -> dict:
    res = browser.get("/autopilot")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()


TOPIC = {"category": "football", "subject": "Pelé", "angle": "Three World Cups by the age of 29"}


def test_defaults_and_settings(app):
    me = creator(app)
    first = state(me.browser)
    assert first["settings"]["enabled"] is False and first["settings"]["mode"] == "approve"
    assert first["settings"]["rotation"]["3"] == "cricket" and first["queue"] == []
    # Turning it on needs a channel.
    assert_error(me.browser.patch("/autopilot", {"enabled": True}), 409, "not_connected")
    connect(me.browser)
    res = me.browser.patch("/autopilot", {"enabled": True, "runHourUtc": 9, "rotation": {"3": "f1"}})
    assert res.status_code == 200, res.get_data(as_text=True)
    settings = res.get_json()["settings"]
    assert settings["enabled"] is True and settings["runHourUtc"] == 9 and settings["rotation"]["3"] == "f1"
    assert me.browser.patch("/autopilot", {"runHourUtc": 24}).status_code == 400


def test_the_queue_add_suggest_and_remove(app):
    me = creator(app)
    assert me.browser.post("/autopilot/topics", TOPIC).status_code == 201
    assert_error(me.browser.post("/autopilot/topics", TOPIC), 409, "not_added")  # no repeats
    res = me.browser.post("/autopilot/topics/suggest")
    assert res.status_code == 202, res.get_data(as_text=True)
    run_jobs(app)
    queue = state(me.browser)["queue"]
    assert len(queue) == 15  # the one by hand and two weeks suggested
    assert {t["category"] for t in queue} == {"football", "f1", "cricket"}
    assert me.browser.delete(f"/autopilot/topics/{queue[0]['id']}").status_code == 204
    assert len(state(me.browser)["queue"]) == 14
    # Someone else's topic can't be touched.
    other = creator(app)
    assert other.browser.delete(f"/autopilot/topics/{queue[1]['id']}").status_code == 404


def test_a_full_run_waits_for_approval_then_publishes(app):
    me = creator(app)
    connect(me.browser)
    me.browser.post("/autopilot/topics", TOPIC)
    res = me.browser.post("/autopilot/run")
    assert res.status_code == 202, res.get_data(as_text=True)
    advance(app)

    [run] = state(me.browser)["runs"]
    assert run["status"] == "awaiting_approval", run
    assert run["video"]["url"] and run["title"]
    project = get_project(me.browser, run["projectId"])
    assert project["status"] == "rendered" and "Pelé" in project["topic"]
    # The rules for real people travel with the script job as instructions (QAP-001).
    with app.app_context():
        from reelforge_api.extensions import db
        from reelforge_api.models import Job

        script = db.session.query(Job).filter_by(project_id=run["projectId"], type="script").one()
        assert any("real person" in rule for rule in script.input["rules"])
    assert MockYouTube.uploads == []  # nothing is published before the approval

    assert me.browser.post(f"/autopilot/topics/{run['id']}/approve").status_code == 202
    advance(app)
    [done] = state(me.browser)["runs"]
    assert done["status"] == "published" and done["url"].startswith("https://youtube.com/shorts/")
    assert len(MockYouTube.uploads) == 1


def test_automatic_mode_publishes_without_asking(app):
    me = creator(app)
    connect(me.browser)
    me.browser.patch("/autopilot", {"mode": "auto", "visibility": "unlisted"})
    me.browser.post("/autopilot/topics", TOPIC)
    assert me.browser.post("/autopilot/run").status_code == 202
    advance(app)
    [run] = state(me.browser)["runs"]
    assert run["status"] == "published", run
    assert MockYouTube.uploads[0]["metadata"]["status"]["privacyStatus"] == "unlisted"


def test_skip_keeps_the_reel_and_publishes_nothing(app):
    me = creator(app)
    connect(me.browser)
    me.browser.post("/autopilot/topics", TOPIC)
    me.browser.post("/autopilot/run")
    advance(app)
    [run] = state(me.browser)["runs"]
    assert me.browser.post(f"/autopilot/topics/{run['id']}/skip").status_code == 200
    assert state(me.browser)["runs"][0]["status"] == "skipped" and MockYouTube.uploads == []
    assert get_project(me.browser, run["projectId"])["status"] == "rendered"
    assert_error(me.browser.post(f"/autopilot/topics/{run['id']}/approve"), 409, "not_waiting")


def test_one_run_at_a_time_and_an_empty_queue(app):
    me = creator(app)
    assert_error(me.browser.post("/autopilot/run"), 409, "queue_empty")
    me.browser.post("/autopilot/topics", TOPIC)
    me.browser.post(
        "/autopilot/topics", {**TOPIC, "subject": "Diego Maradona", "angle": "The 1986 World Cup"}
    )
    assert me.browser.post("/autopilot/run").status_code == 202
    assert_error(me.browser.post("/autopilot/run"), 409, "not_started")


def test_the_daily_start_runs_once_a_day_at_the_hour_and_follows_the_rotation(app, database):
    me = creator(app)
    connect(me.browser)
    me.browser.patch("/autopilot", {"enabled": True, "runHourUtc": 17})
    me.browser.post("/autopilot/topics", TOPIC)
    me.browser.post(
        "/autopilot/topics", {"category": "f1", "subject": "Ayrton Senna", "angle": "Donington 1993"}
    )
    tuesday = datetime(2026, 12, 1, 16, 30, tzinfo=UTC)  # a Tuesday: F1 day
    with app.app_context():
        assert autopilot.start_due(tuesday) == 0  # before the hour
        assert autopilot.start_due(tuesday.replace(hour=18)) == 1  # the hour has passed (AP-6)
        assert autopilot.start_due(tuesday.replace(hour=19)) == 0  # once a day
    [run] = state(me.browser)["runs"]
    assert run["subject"] == "Ayrton Senna" and run["status"] == "running"


def test_without_credits_the_topic_fails_with_the_reason(app, database):
    me = creator(app)
    database.run("UPDATE credit_accounts SET balance = 0 WHERE user_id = :u", u=me.id)
    me.browser.post("/autopilot/topics", TOPIC)
    res = me.browser.post("/autopilot/run")
    assert res.status_code == 409 and "credit" in res.get_json()["error"]["message"].lower()
    assert state(me.browser)["runs"][0]["status"] == "failed"


def test_a_reel_made_while_youtube_is_not_connected_waits_with_the_reason(app):
    me = creator(app)
    me.browser.patch("/autopilot", {"mode": "auto"})
    me.browser.post("/autopilot/topics", TOPIC)
    me.browser.post("/autopilot/run")
    advance(app)
    [run] = state(me.browser)["runs"]
    assert run["status"] == "awaiting_approval" and "YouTube" in run["error"]
