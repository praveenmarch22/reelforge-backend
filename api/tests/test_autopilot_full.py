"""The daily autopilot in full (docs/AUTOPILOT.md: API group L, edge cases AP-1 to AP-7).

`test_autopilot.py` holds the starter tests; this file covers every endpoint's rules, the topic
suggestions, the daily start, each step of a run and its failures, and migration 0003.

Everything runs with the offline mocks. A run is driven the way the scheduler and the workers
drive it: `tick` is one `autopilot.advance` round, `run_jobs` is the workers.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from reelforge_api import autopilot, jobs
from reelforge_api.providers.base import ProviderError
from reelforge_api.providers.llm import MockLLM
from reelforge_api.providers.publish import MockYouTube
from reelforge_api.renders import MockRenderService
from reelforge_api.script_rules import COSTS, TONES

from .projects_support import balance, creator, get_job, get_project, run_jobs, set_balance
from .publish_support import BODY as PUBLISH_BODY
from .render_support import ready_project, started
from .render_support import reset_mock as reset_render_mock
from .support import Browser, assert_error, assert_invalid
from .test_publishing import connect
from .voice_support import no_real_voice_services  # noqa: F401 - autouse fixture

TOPIC = {"category": "football", "subject": "Pelé", "angle": "Three World Cups by the age of 29"}
SENNA = {"category": "f1", "subject": "Ayrton Senna", "angle": "The opening lap at Donington in 1993"}
SACHIN = {"category": "cricket", "subject": "Sachin Tendulkar", "angle": "A hundred international hundreds"}
SETTINGS_FIELDS = {
    "enabled", "mode", "runHourUtc", "rotation", "tone", "targetSeconds", "visibility", "lastRunOn",
}  # fmt: skip
TOPIC_FIELDS = {
    "id", "category", "subject", "angle", "status", "projectId", "error", "runOn", "video", "url", "title",
}  # fmt: skip
STATUSES = ("queued", "running", "awaiting_approval", "publishing", "published", "skipped", "failed")
# 2026-12-01 is a Tuesday: Formula 1 in the default rotation.
TUESDAY = datetime(2026, 12, 1, 17, 30, tzinfo=UTC)
WEDNESDAY = datetime(2026, 12, 2, 17, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fresh(database):
    """Every test starts with empty tables (a run left over by another test would be advanced
    too) and with the mocks as new."""
    MockYouTube.fail_with, MockYouTube.no_channel, MockYouTube.processing = None, False, "processed"
    MockYouTube.uploads.clear()
    reset_render_mock()
    jobs.manual_queue.clear()
    yield
    MockYouTube.fail_with = None
    reset_render_mock()
    jobs.manual_queue.clear()


# ---------------------------------------------------------------- helpers


def state(browser: Browser) -> dict:
    res = browser.get("/autopilot")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()


def add(browser: Browser, topic: dict = TOPIC, **over) -> dict:
    """L4 that must succeed; returns the new topic as the queue shows it."""
    body = {**topic, **over}
    res = browser.post("/autopilot/topics", body)
    assert res.status_code == 201, res.get_data(as_text=True)
    return next(t for t in res.get_json()["queue"] if t["subject"] == body["subject"].strip())


def run_now(browser: Browser) -> dict:
    """L8 that must succeed; returns the run."""
    res = browser.post("/autopilot/run")
    assert res.status_code == 202, res.get_data(as_text=True)
    return next(r for r in res.get_json()["runs"] if r["status"] == "running")


def tick(app) -> int:
    """One round of the `autopilot.advance` task."""
    with app.app_context():
        return autopilot.advance_all()


def finish(app, rounds: int = 14) -> None:
    """The scheduler and the workers, until nothing moves any more."""
    for _ in range(rounds):
        tick(app)
        if not run_jobs(app) and not tick(app):
            return


def drive_to(app, browser: Browser, project_id: str, wanted: str) -> dict:
    """Runs the scheduler and the workers until the run's project has the status `wanted`."""
    for _ in range(14):
        project = get_project(browser, project_id)
        if project["status"] == wanted:
            return project
        if not run_jobs(app):
            tick(app)
    raise AssertionError(f"the project never became {wanted}: {get_project(browser, project_id)['status']}")


def topic_of(browser: Browser, topic_id: str) -> dict:
    found = state(browser)
    return next(t for t in found["queue"] + found["runs"] if t["id"] == topic_id)


def insert_topic(database, user_id: str, status: str = "queued", **over) -> str:
    values = {
        "position": 1, "category": "football", "subject": "Seeded", "angle": "A story", "run_on": None,
        "error": None, "project_id": None, **over,
    }  # fmt: skip
    return str(
        database.value(
            "INSERT INTO autopilot_topics (user_id, position, category, subject, angle, status, run_on, "
            "error, project_id) VALUES (:u, :position, :category, :subject, :angle, :s, :run_on, :error, "
            ":project_id) RETURNING id",
            u=user_id,
            s=status,
            **values,
        )
    )


def waiting_run(app, browser: Browser, topic: dict = TOPIC) -> dict:
    """A finished Reel waiting for Publish or Skip (approve mode)."""
    add(browser, topic)
    started_run = run_now(browser)
    finish(app)
    waiting = topic_of(browser, started_run["id"])
    assert waiting["status"] == "awaiting_approval", waiting
    return waiting


def start_due(app, when: datetime) -> int:
    with app.app_context():
        return autopilot.start_due(when)


def enabled_creator(app, hour: int = 17, **settings):
    me = creator(app)
    connect(me.browser)
    res = me.browser.patch("/autopilot", {"enabled": True, "runHourUtc": hour, **settings})
    assert res.status_code == 200, res.get_data(as_text=True)
    return me


# ---------------------------------------------------------------- who may call what

ENDPOINTS = [
    ("GET", "/autopilot", None),
    ("PATCH", "/autopilot", {"mode": "auto"}),
    ("POST", "/autopilot/topics/suggest", None),
    ("POST", "/autopilot/topics", TOPIC),
    ("DELETE", "/autopilot/topics/{id}", None),
    ("POST", "/autopilot/topics/{id}/approve", None),
    ("POST", "/autopilot/topics/{id}/skip", None),
    ("POST", "/autopilot/run", None),
]
VERIFIED_ONLY = {
    ("PATCH", "/autopilot"),
    ("POST", "/autopilot/topics/suggest"),
    ("POST", "/autopilot/topics"),
    ("POST", "/autopilot/topics/{id}/approve"),
    ("POST", "/autopilot/run"),
}


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS, ids=[f"{m} {p}" for m, p, _ in ENDPOINTS])
def test_every_endpoint_needs_a_signed_in_user(app, database, method, path, body):
    owner = creator(app)
    topic_id = insert_topic(database, owner.id, "awaiting_approval")
    res = Browser(app).call(method, path.format(id=topic_id), body)
    assert_error(res, 401, "not_authenticated")
    assert (
        database.value("SELECT status FROM autopilot_topics WHERE id = :t", t=topic_id) == "awaiting_approval"
    )


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS, ids=[f"{m} {p}" for m, p, _ in ENDPOINTS])
def test_changes_that_spend_or_publish_need_a_verified_email(app, database, method, path, body):
    me = creator(app, verified=False)
    topic_id = insert_topic(database, me.id, "awaiting_approval")
    res = me.browser.call(method, path.format(id=topic_id), body)
    if (method, path) in VERIFIED_ONLY:
        assert_error(res, 403, "email_not_verified")
        row = database.one("SELECT status FROM autopilot_topics WHERE id = :t", t=topic_id)
        assert row["status"] == "awaiting_approval"
        assert database.value("SELECT count(*) FROM autopilot_topics") == 1
        assert database.value("SELECT count(*) FROM jobs") == 0
    else:
        # Reading, removing and skipping publish nothing and cost nothing.
        assert res.status_code in (200, 204), res.get_data(as_text=True)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [e for e in ENDPOINTS if e[0] != "GET"],
    ids=[f"{m} {p}" for m, p, _ in ENDPOINTS if m != "GET"],
)
def test_changes_need_the_csrf_header(app, database, method, path, body):
    me = creator(app)
    topic_id = insert_topic(database, me.id, "awaiting_approval")
    assert_error(me.browser.call(method, path.format(id=topic_id), body, csrf=None), 403, "csrf_failed")
    assert (
        database.value("SELECT status FROM autopilot_topics WHERE id = :t", t=topic_id) == "awaiting_approval"
    )


@pytest.mark.parametrize("action", ["delete", "approve", "skip"])
def test_another_creators_topic_is_not_found(app, database, action):
    owner, other = creator(app), creator(app)
    connect(other.browser)
    waiting = insert_topic(database, owner.id, "awaiting_approval")
    queued = insert_topic(database, owner.id, "queued", position=2, subject="Mine")
    for topic_id in (waiting, queued, "not-a-uuid", "00000000-0000-4000-8000-000000000000"):
        if action == "delete":
            res = other.browser.delete(f"/autopilot/topics/{topic_id}")
        else:
            res = other.browser.post(f"/autopilot/topics/{topic_id}/{action}")
        assert_error(res, 404, "not_found")
    assert database.value("SELECT count(*) FROM autopilot_topics WHERE user_id = :u", u=owner.id) == 2
    assert (
        database.value("SELECT status FROM autopilot_topics WHERE id = :t", t=waiting) == "awaiting_approval"
    )
    assert MockYouTube.uploads == []


def test_each_creator_sees_only_their_own_settings_queue_and_runs(app, database):
    me, other = creator(app), creator(app)
    add(me.browser)
    insert_topic(database, me.id, "failed", position=2, subject="Old run", error="Something")
    me.browser.patch("/autopilot", {"runHourUtc": 6})
    theirs = state(other.browser)
    assert theirs["queue"] == [] and theirs["runs"] == []
    assert theirs["settings"]["runHourUtc"] == 17


# ---------------------------------------------------------------- L1


def test_l1_the_defaults_and_the_shape(app):
    me = creator(app)
    found = state(me.browser)
    assert set(found) == {"settings", "queue", "runs"}
    assert found["settings"] == {
        "enabled": False,
        "mode": "approve",
        "runHourUtc": 17,
        # Football Monday, Wednesday, Friday and Sunday; F1 Tuesday and Saturday; cricket Thursday.
        "rotation": {
            "0": "football", "1": "f1", "2": "football", "3": "cricket", "4": "football", "5": "f1",
            "6": "football",
        },
        "tone": "storytelling",
        "targetSeconds": 30,
        "visibility": "public",
        "lastRunOn": None,
    }  # fmt: skip
    assert set(found["settings"]) == SETTINGS_FIELDS
    topic = add(me.browser)
    assert set(topic) == TOPIC_FIELDS
    assert topic == {
        **TOPIC, "id": topic["id"], "status": "queued", "projectId": None, "error": None, "runOn": None,
        "video": None, "url": None, "title": None,
    }  # fmt: skip


def test_l1_the_queue_is_in_the_order_added_and_runs_are_newest_first_at_most_30(app, database):
    me = creator(app)
    for topic in (TOPIC, SENNA, SACHIN):
        add(me.browser, topic)
    for day in range(1, 29):
        insert_topic(
            database, me.id, "skipped", subject=f"Run {day:02d}", run_on=f"2026-11-{day:02d}", position=day
        )
    for day in range(1, 8):
        insert_topic(
            database, me.id, "failed", subject=f"Dec {day:02d}", run_on=f"2026-12-{day:02d}", position=day
        )
    found = state(me.browser)
    assert [t["subject"] for t in found["queue"]] == ["Pelé", "Ayrton Senna", "Sachin Tendulkar"]
    dates = [r["runOn"] for r in found["runs"]]
    assert len(dates) == 30
    assert dates == sorted(dates, reverse=True) and dates[0] == "2026-12-07" and dates[-1] == "2026-11-06"
    assert all(set(r) == TOPIC_FIELDS for r in found["runs"])


# ---------------------------------------------------------------- L2

BAD_SETTINGS = [
    ({"runHourUtc": -1}, "runHourUtc"),
    ({"runHourUtc": 24}, "runHourUtc"),
    ({"runHourUtc": "9"}, "runHourUtc"),
    ({"runHourUtc": 9.5}, "runHourUtc"),
    ({"mode": "manual"}, "mode"),
    ({"mode": True}, "mode"),
    ({"enabled": "yes"}, "enabled"),
    ({"enabled": 1}, "enabled"),
    ({"tone": "angry"}, "tone"),
    ({"targetSeconds": 45}, "targetSeconds"),
    ({"targetSeconds": "30"}, "targetSeconds"),
    ({"visibility": "friends"}, "visibility"),
    ({"rotation": {"7": "football"}}, "rotation"),
    ({"rotation": {"-1": "football"}}, "rotation"),
    ({"rotation": {"monday": "football"}}, "rotation"),
    ({"rotation": {"0": "tennis"}}, "rotation"),
    ({"rotation": {"0": "Football"}}, "rotation"),
    ({"rotation": {"0": None}}, "rotation"),
    ({"rotation": ["football"]}, "rotation"),
    ({"rotation": "football"}, "rotation"),
    ({"lastRunOn": "2026-01-01"}, "lastRunOn"),
    ({"somethingElse": 1}, "somethingElse"),
]


@pytest.mark.parametrize(("body", "field"), BAD_SETTINGS, ids=[str(b) for b, _ in BAD_SETTINGS])
def test_l2_refuses_bad_settings_and_saves_nothing(app, body, field):
    me = creator(app)
    before = state(me.browser)["settings"]
    named = assert_invalid(
        me.browser.patch("/autopilot", {"mode": "auto", **body} if "mode" not in body else body)
    )
    assert any(name == field or name.startswith(field) for name in named), named
    assert state(me.browser)["settings"] == before


def test_l2_refuses_a_body_that_is_not_an_object(app):
    me = creator(app)
    assert_error(me.browser.patch("/autopilot", ["auto"]), 400, "validation_error")
    assert_error(me.browser.patch("/autopilot"), 400, "validation_error")


def test_l2_saves_each_setting_and_leaves_the_others(app):
    me = creator(app)
    for hour in (0, 23):
        assert (
            me.browser.patch("/autopilot", {"runHourUtc": hour}).get_json()["settings"]["runHourUtc"] == hour
        )
    for mode in ("auto", "approve"):
        assert me.browser.patch("/autopilot", {"mode": mode}).get_json()["settings"]["mode"] == mode
    for tone in TONES:
        assert me.browser.patch("/autopilot", {"tone": tone}).get_json()["settings"]["tone"] == tone
    for seconds in (15, 30, 60, 90):
        got = me.browser.patch("/autopilot", {"targetSeconds": seconds}).get_json()["settings"]
        assert got["targetSeconds"] == seconds
    for visibility in ("private", "unlisted", "public"):
        got = me.browser.patch("/autopilot", {"visibility": visibility}).get_json()["settings"]
        assert got["visibility"] == visibility
    assert state(me.browser)["settings"] == {
        **state(me.browser)["settings"],
        "enabled": False, "mode": "approve", "runHourUtc": 23, "tone": TONES[-1], "targetSeconds": 90,
        "visibility": "public",
    }  # fmt: skip
    # The answer is the whole state, as L1 gives it.
    res = me.browser.patch("/autopilot", {})
    assert res.status_code == 200 and res.get_json() == state(me.browser)


def test_l2_a_rotation_change_keeps_the_other_days(app):
    me = creator(app)
    got = me.browser.patch("/autopilot", {"rotation": {"0": "cricket"}}).get_json()["settings"]["rotation"]
    assert got == {
        "0": "cricket", "1": "f1", "2": "football", "3": "cricket", "4": "football", "5": "f1",
        "6": "football",
    }  # fmt: skip
    got = me.browser.patch("/autopilot", {"rotation": {"6": "f1", "1": "football"}}).get_json()
    assert got["settings"]["rotation"] == {
        "0": "cricket", "1": "football", "2": "football", "3": "cricket", "4": "football", "5": "f1",
        "6": "f1",
    }  # fmt: skip
    assert (
        me.browser.patch("/autopilot", {"rotation": {}}).get_json()["settings"]["rotation"]["0"] == "cricket"
    )


def test_l2_turning_it_on_needs_a_connected_channel(app, database):
    me = creator(app)
    # Not connected: refused, and nothing else in the request is saved.
    error = assert_error(
        me.browser.patch("/autopilot", {"enabled": True, "runHourUtc": 3}), 409, "not_connected"
    )
    assert "YouTube" in error["message"]
    assert state(me.browser)["settings"]["enabled"] is False
    assert state(me.browser)["settings"]["runHourUtc"] == 17
    # Everything else can be set up before connecting, and "off" is always allowed.
    assert me.browser.patch("/autopilot", {"enabled": False, "runHourUtc": 3}).status_code == 200

    connect(me.browser)
    database.run("UPDATE youtube_accounts SET status = 'reconnect_required' WHERE user_id = :u", u=me.id)
    assert_error(me.browser.patch("/autopilot", {"enabled": True}), 409, "not_connected")
    database.run("UPDATE youtube_accounts SET status = 'connected' WHERE user_id = :u", u=me.id)
    assert me.browser.patch("/autopilot", {"enabled": True}).get_json()["settings"]["enabled"] is True

    # Once on, other settings and "off" don't need the channel.
    assert me.browser.delete("/connections/youtube").status_code == 204
    assert me.browser.patch("/autopilot", {"mode": "auto"}).get_json()["settings"]["enabled"] is True
    assert me.browser.patch("/autopilot", {"enabled": False}).get_json()["settings"]["enabled"] is False


# ---------------------------------------------------------------- L4, L5: the queue by hand

BAD_TOPICS = [
    ({"category": "tennis"}, "category"),
    ({"category": "Football"}, "category"),
    ({"category": ""}, "category"),
    ({"category": None}, "category"),
    ({"subject": ""}, "subject"),
    ({"subject": "   "}, "subject"),
    ({"subject": "x" * 81}, "subject"),
    ({"subject": "Pelé\nthe king"}, "subject"),
    ({"subject": 10}, "subject"),
    ({"angle": ""}, "angle"),
    ({"angle": " \t "}, "angle"),
    ({"angle": "x" * 161}, "angle"),
    ({"angle": "one\ntwo"}, "angle"),
    ({"angle": None}, "angle"),
    ({"status": "published"}, "status"),
    ({"position": 1}, "position"),
]


@pytest.mark.parametrize(
    ("change", "field"), BAD_TOPICS, ids=[f"{f}={str(c[f])[:12]!r}" for c, f in BAD_TOPICS]
)
def test_l4_refuses_a_bad_topic(app, change, field):
    me = creator(app)
    assert_invalid(me.browser.post("/autopilot/topics", {**TOPIC, **change}), field)
    assert state(me.browser)["queue"] == []


@pytest.mark.parametrize("missing", ["category", "subject", "angle"])
def test_l4_needs_every_field(app, missing):
    me = creator(app)
    body = {k: v for k, v in TOPIC.items() if k != missing}
    assert_invalid(me.browser.post("/autopilot/topics", body), missing)


def test_l4_accepts_the_longest_text_and_trims_it(app):
    me = creator(app)
    long = add(me.browser, subject="s" * 80, angle="a" * 160)
    assert len(long["subject"]) == 80 and len(long["angle"]) == 160
    trimmed = add(me.browser, SENNA, subject="  Ayrton Senna ", angle=" Donington 1993  ")
    assert (trimmed["subject"], trimmed["angle"]) == ("Ayrton Senna", "Donington 1993")
    for category in ("football", "f1", "cricket"):
        assert add(me.browser, category=category, subject=f"Legend of {category}")["category"] == category
    assert [t["subject"] for t in state(me.browser)["queue"]][:2] == ["s" * 80, "Ayrton Senna"]


def test_l4_the_same_legend_and_story_is_not_queued_twice(app):
    me, other = creator(app), creator(app)
    add(me.browser)
    for repeat in (
        TOPIC,
        {**TOPIC, "subject": "PELÉ", "angle": TOPIC["angle"].upper()},
        {**TOPIC, "category": "f1"},
    ):
        error = assert_error(me.browser.post("/autopilot/topics", repeat), 409, "not_added")
        assert "already in the queue" in error["message"]
    assert len(state(me.browser)["queue"]) == 1
    # Another story about the same legend, the same story about another one, and another
    # creator's queue are all fine.
    add(me.browser, angle="The thousandth goal")
    add(me.browser, subject="Garrincha")
    add(other.browser)
    assert len(state(me.browser)["queue"]) == 3


def test_l4_the_queue_holds_60_topics(app, database):
    me = creator(app)
    for n in range(1, 60):
        insert_topic(database, me.id, subject=f"Legend {n}", position=n)
    # Finished runs don't count towards the limit.
    for n in range(5):
        insert_topic(database, me.id, "published", subject=f"Done {n}", position=n)
    last = add(me.browser)
    assert len(state(me.browser)["queue"]) == 60 and state(me.browser)["queue"][-1]["id"] == last["id"]
    error = assert_error(me.browser.post("/autopilot/topics", SENNA), 409, "not_added")
    assert "full" in error["message"]
    assert len(state(me.browser)["queue"]) == 60
    # One removed, one more fits.
    assert me.browser.delete(f"/autopilot/topics/{last['id']}").status_code == 204
    add(me.browser, SENNA)


@pytest.mark.parametrize("status", STATUSES)
def test_l5_removes_a_topic_from_the_queue_or_a_run_from_the_list(app, database, status):
    me = creator(app)
    keep = insert_topic(database, me.id, "queued", subject="Kept", position=9)
    topic_id = insert_topic(database, me.id, status)
    res = me.browser.delete(f"/autopilot/topics/{topic_id}")
    assert res.status_code == 204 and res.get_data() == b""
    assert_error(me.browser.delete(f"/autopilot/topics/{topic_id}"), 404, "not_found")
    found = state(me.browser)
    assert [t["id"] for t in found["queue"]] == [keep] and found["runs"] == []


def test_l5_on_a_run_under_way_is_the_stop(app, database):
    """The autopilot takes the Reel no further; the Reel stays in the library as it is."""
    me = creator(app)
    connect(me.browser)
    me.browser.patch("/autopilot", {"mode": "auto"})
    add(me.browser)
    add(me.browser, subject="Garrincha")
    run = run_now(me.browser)
    drive_to(app, me.browser, run["projectId"], "ready")
    assert me.browser.delete(f"/autopilot/topics/{run['id']}").status_code == 204
    assert state(me.browser)["runs"] == []
    for _ in range(3):
        assert tick(app) == 0
        run_jobs(app)
    # Not rendered, not published, and still the creator's to finish by hand.
    assert get_project(me.browser, run["projectId"])["status"] == "ready"
    assert MockRenderService.started == [] and MockYouTube.uploads == []
    # The autopilot is free for the next topic at once.
    assert run_now(me.browser)["subject"] == "Garrincha"


# ---------------------------------------------------------------- L3: suggestions


def suggest(app, browser: Browser) -> dict:
    """L3, then the worker. Returns the finished job."""
    res = browser.post("/autopilot/topics/suggest")
    assert res.status_code == 202, res.get_data(as_text=True)
    job = res.get_json()["job"]
    assert job["type"] == "autopilot_topics" and job["state"] == "queued" and job["creditsReserved"] == 0
    run_jobs(app)
    return get_job(browser, job["id"])


def job_input(database, job_id: str) -> dict:
    return database.value("SELECT input FROM jobs WHERE id = :j", j=job_id)


def test_l3_suggests_two_weeks_in_the_default_rotation_for_free(app, database):
    me = creator(app)
    before = balance(me.browser)
    done = suggest(app, me.browser)
    assert done["state"] == "succeeded" and done["output"] == {"added": 14}
    # Two weeks of the default week: 4 football, 2 F1 and 1 cricket days, twice.
    assert job_input(database, done["id"])["mix"] == {"football": 8, "f1": 4, "cricket": 2}
    queue = state(me.browser)["queue"]
    counts = {c: sum(t["category"] == c for t in queue) for c in ("football", "f1", "cricket")}
    assert counts == {"football": 8, "f1": 4, "cricket": 2}
    assert all(t["status"] == "queued" and t["subject"] and t["angle"] for t in queue)
    assert balance(me.browser) == before
    assert database.value("SELECT count(*) FROM credit_ledger WHERE job_id = :j", j=done["id"]) == 0


def test_l3_the_mix_follows_the_creators_rotation(app, database):
    me = creator(app)
    me.browser.patch("/autopilot", {"rotation": {str(day): "cricket" for day in range(7)}})
    done = suggest(app, me.browser)
    assert job_input(database, done["id"])["mix"] == {"cricket": 14}
    assert {t["category"] for t in state(me.browser)["queue"]} == {"cricket"}

    other = creator(app)
    other.browser.patch(
        "/autopilot", {"rotation": {"0": "f1", "2": "f1", "3": "f1"}}
    )  # 1 football day less...
    done = suggest(app, other.browser)
    assert job_input(database, done["id"])["mix"] == {"f1": 10, "football": 4}


def test_l3_tells_the_model_what_is_already_planned_or_made(app, database):
    me, other = creator(app), creator(app)
    add(me.browser)
    insert_topic(database, me.id, "published", subject="Diego Maradona", angle="The 1986 World Cup")
    insert_topic(database, me.id, "skipped", subject="Jim Clark", angle="Indianapolis 1965", category="f1")
    add(other.browser, SACHIN)
    done = suggest(app, me.browser)
    avoid = job_input(database, done["id"])["avoid"]
    assert sorted(avoid) == [
        "Diego Maradona: The 1986 World Cup",
        "Jim Clark: Indianapolis 1965",
        "Pelé: Three World Cups by the age of 29",
    ]
    with app.app_context():
        from reelforge_api.prompts import autopilot_topics_prompt

        prompt = autopilot_topics_prompt({"football": 8, "cricket": 2}, avoid)
    assert "8 about football legends" in prompt and "2 about cricket legends" in prompt
    assert all(line in prompt for line in avoid)
    # With nothing planned the prompt says so.
    assert "none" in autopilot_topics_prompt({"f1": 1}, [])


def test_l3_suggesting_again_adds_nothing_that_is_still_queued(app):
    me = creator(app)
    assert suggest(app, me.browser)["output"] == {"added": 14}
    assert suggest(app, me.browser)["output"] == {"added": 0}
    assert len(state(me.browser)["queue"]) == 14


def test_l3_never_fills_the_queue_past_60(app, database):
    me = creator(app)
    for n in range(55):
        insert_topic(database, me.id, subject=f"Legend {n}", position=n + 1)
    assert suggest(app, me.browser)["output"] == {"added": 5}
    queue = state(me.browser)["queue"]
    assert len(queue) == 60
    # The new ones go to the end.
    assert [t["subject"] for t in queue[:55]] == [f"Legend {n}" for n in range(55)]
    assert suggest(app, me.browser)["output"] == {"added": 0}


def answer_with(monkeypatch, answer) -> None:
    monkeypatch.setattr(MockLLM, "_autopilot_topics", lambda self, context: answer)


@pytest.mark.parametrize(
    "answer",
    [
        {"unexpected": True},
        {"topics": []},
        {"topics": "Pelé"},
        {"topics": None},
        {"topics": [{"category": "tennis", "subject": "Someone", "angle": "Something"}]},
        {"topics": [{"category": "football", "subject": "", "angle": "Something"}]},
        {"topics": [{"category": "football", "subject": "Someone"}]},
        {"topics": ["Pelé", 3, None]},
    ],
    ids=["no topics key", "empty", "text", "null", "unknown sport", "no legend", "no story", "not objects"],
)
def test_l3_an_answer_with_nothing_usable_fails_the_job_and_adds_nothing(app, monkeypatch, answer):
    me = creator(app)
    add(me.browser)
    answer_with(monkeypatch, answer)
    done = suggest(app, me.browser)
    assert done["state"] == "failed" and done["error"]["code"] == "provider_failed"
    assert done["error"]["message"]
    assert [t["subject"] for t in state(me.browser)["queue"]] == ["Pelé"]


def test_l3_a_refusal_fails_the_job(app, monkeypatch):
    me = creator(app)
    answer_with(monkeypatch, {"refused": True})
    done = suggest(app, me.browser)
    assert done["state"] == "failed" and done["error"]["code"] == "content_refused"
    assert state(me.browser)["queue"] == []


def test_l3_keeps_the_usable_topics_of_a_mixed_answer(app, monkeypatch):
    me = creator(app)
    add(me.browser)  # Pelé is queued already
    answer_with(
        monkeypatch,
        {
            "topics": [
                {"category": " Football ", "subject": "  Johan   Cruyff ", "angle": "The  turn in 1974 "},
                {"category": "F1", "subject": "S" * 200, "angle": "A" * 400},
                {"category": "tennis", "subject": "Roger Federer", "angle": "Wimbledon"},
                {"category": "cricket", "subject": "", "angle": "No legend"},
                {"category": "cricket", "subject": "No story", "angle": None},
                "not a topic",
                TOPIC,  # queued already
                {"category": "football", "subject": "johan cruyff", "angle": "the turn in 1974"},  # a repeat
                {
                    "category": "cricket",
                    "subject": "Shane Warne",
                    "angle": "The ball of the century",
                    "extra": 1,
                },
            ]
        },
    )
    done = suggest(app, me.browser)
    queue = state(me.browser)["queue"]
    assert done["state"] == "succeeded", done
    assert [(t["category"], t["subject"], t["angle"]) for t in queue] == [
        ("football", "Pelé", TOPIC["angle"]),
        ("football", "Johan Cruyff", "The turn in 1974"),
        ("f1", "S" * 80, "A" * 160),
        ("cricket", "Shane Warne", "The ball of the century"),
    ]
    assert done["output"] == {"added": 3}


def test_l3_a_sport_outside_the_creators_rotation_is_left_out(app, monkeypatch):
    me = creator(app)
    me.browser.patch("/autopilot", {"rotation": {str(day): "f1" for day in range(7)}})
    answer_with(monkeypatch, {"topics": [SENNA, TOPIC, SACHIN]})
    assert suggest(app, me.browser)["output"] == {"added": 1}
    assert [t["subject"] for t in state(me.browser)["queue"]] == ["Ayrton Senna"]


def test_l3_takes_at_most_30_topics_from_one_answer(app, monkeypatch):
    me = creator(app)
    answer_with(
        monkeypatch,
        {
            "topics": [
                {"category": "football", "subject": f"Legend {n}", "angle": "A story"} for n in range(45)
            ]
        },
    )
    assert suggest(app, me.browser)["output"] == {"added": 30}


def test_l3_is_refused_when_no_language_model_is_switched_on(app, database):
    me = creator(app)
    database.run("INSERT INTO provider_switches (provider, enabled, reason) VALUES ('mock', false, 'test')")
    assert_error(me.browser.post("/autopilot/topics/suggest"), 503, "provider_unavailable")
    assert database.value("SELECT count(*) FROM jobs") == 0


# ---------------------------------------------------------------- topic_text


def topic_text(app, **fields) -> str:
    from reelforge_api.models import AutopilotTopic

    with app.app_context():
        return autopilot.topic_text(AutopilotTopic(**{**TOPIC, **fields}))


def test_topic_text_names_the_legend_the_sport_and_the_story(app):
    assert topic_text(app) == "Pelé (football legend): Three World Cups by the age of 29"
    assert topic_text(app, **SENNA) == "Ayrton Senna (Formula 1 legend): The opening lap at Donington in 1993"
    assert topic_text(app, **SACHIN).startswith("Sachin Tendulkar (cricket legend): ")


@pytest.mark.parametrize(
    ("subject", "angle"),
    [("P", "x"), ("s" * 60, "a" * 140), ("s" * 80, "a" * 160)],
    ids=["shortest", "the prompt's limits", "the form's limits"],
)
@pytest.mark.parametrize("category", ["football", "f1", "cricket"])
def test_topic_text_is_at_most_300_characters_and_keeps_the_whole_story(app, category, subject, angle):
    made = topic_text(app, category=category, subject=subject, angle=angle)
    assert len(made) <= 300, len(made)
    assert made.startswith(f"{subject} (") and made.endswith(f" legend): {angle}")


def test_topic_text_never_passes_300_characters_whatever_the_row_holds(app):
    assert len(topic_text(app, subject="s" * 200, angle="a" * 400)) == 300


def test_the_script_is_told_the_whole_story_of_a_topic_the_queue_accepted(app):
    me = creator(app)
    add(me.browser, subject="s" * 80, angle="a" * 160)
    run = run_now(me.browser)
    topic = get_project(me.browser, run["projectId"])["topic"]
    assert topic == "s" * 80 + " (football legend): " + "a" * 160


# ---------------------------------------------------------------- the rules for real people


def script_jobs(database, project_id: str) -> list[dict]:
    rows = database.rows(
        "SELECT input FROM jobs WHERE project_id = :p AND type = 'script' ORDER BY queued_at", p=project_id
    )
    return [r["input"] for r in rows]


def test_the_rules_for_real_people_are_instructions_to_the_script_model_not_part_of_the_topic(app, database):
    from reelforge_api import prompts

    me = creator(app)
    add(me.browser)
    run = run_now(me.browser)
    [sent] = script_jobs(database, run["projectId"])
    assert sent["rules"] == list(autopilot.SCRIPT_RULES)
    assert sent["topic"] == "Pelé (football legend): Three World Cups by the age of 29"
    rules = " ".join(autopilot.SCRIPT_RULES)
    for phrase in (
        "widely documented facts",
        "No invented quotes",
        "no private life",
        "never name or describe a real person",
    ):
        assert phrase in rules, phrase

    prompt = prompts.script_prompt(
        sent["topic"], "storytelling", "faceless", 30, "cinematic", "", "", sent["rules"]
    )
    head, _, own_rules = prompt.partition("\nRules:\n")
    # The topic is quoted material; the rules stand with the prompt's own rules, after it.
    assert sent["topic"] in head and not any(rule in head for rule in autopilot.SCRIPT_RULES)
    for rule in autopilot.SCRIPT_RULES:
        assert f"- {rule}\n" in own_rules
    # A Reel made by hand gets none.
    by_hand = prompts.script_prompt("Pelé", "funny", "faceless", 30, "", "")
    assert not any(rule in by_hand for rule in autopilot.SCRIPT_RULES)


def test_a_script_written_again_for_an_autopilot_reel_keeps_the_rules(app, database):
    me = creator(app)
    waiting = waiting_run(app, me.browser)
    res = me.browser.post(f"/projects/{waiting['projectId']}/script/regenerate", {"instructions": "Shorter"})
    assert res.status_code == 202, res.get_data(as_text=True)
    first, again = script_jobs(database, waiting["projectId"])
    assert again["rules"] == first["rules"] == list(autopilot.SCRIPT_RULES)
    # A Reel the creator made by hand has no such rules.
    by_hand = me.browser.post("/projects", {"topic": "Pelé"}).get_json()["project"]
    assert "rules" not in script_jobs(database, by_hand["id"])[0]


def image_prompt_for(app, project_id, prompt: str) -> tuple[str, str]:
    with app.app_context():
        return autopilot.image_prompt_for(project_id, prompt)


def test_an_autopilot_image_prompt_loses_the_legends_name_and_gains_the_no_face_rule(app):
    me = creator(app)
    add(me.browser, SENNA)
    run = run_now(me.browser)
    cleaned, rule = image_prompt_for(
        app, run["projectId"], "Ayrton Senna in the rain, SENNA's helmet, ayrton waving"
    )
    assert "senna" not in cleaned.lower() and "ayrton" not in cleaned.lower()
    assert cleaned.count("the athlete") >= 3 and cleaned.endswith("the athlete waving")
    assert rule == autopilot.IMAGE_RULE
    for phrase in ("no recognisable real person", "no face"):
        assert phrase in autopilot.IMAGE_RULE
    # A Reel made by hand is left alone.
    by_hand = me.browser.post("/projects", {"topic": "Ayrton Senna"}).get_json()["project"]
    assert image_prompt_for(app, by_hand["id"], "Ayrton Senna in the rain") == (
        "Ayrton Senna in the rain",
        "",
    )
    assert image_prompt_for(app, None, "Ayrton Senna") == ("Ayrton Senna", "")


def test_the_image_provider_is_given_the_rule_first_and_never_the_name(app, monkeypatch):
    from reelforge_api.providers.image import MockImage

    prompts_sent: list[str] = []
    real = MockImage.generate

    def record(self, prompt, seed):
        prompts_sent.append(prompt)
        return real(self, prompt, seed)

    monkeypatch.setattr(MockImage, "generate", record)
    me = creator(app)
    add(me.browser)
    run = run_now(me.browser)
    project = drive_to(app, me.browser, run["projectId"], "ready")
    scene = project["scenes"][0]
    res = me.browser.patch(f"/scenes/{scene['id']}", {"visualPrompt": "Pelé lifting the trophy in 1970"})
    assert res.status_code == 200, res.get_data(as_text=True)
    res = me.browser.post(f"/scenes/{scene['id']}/visual/regenerate", {"visualSource": "ai_image"})
    assert res.status_code == 202, res.get_data(as_text=True)
    run_jobs(app)
    assert prompts_sent, "no image was asked for"
    sent = prompts_sent[-1]
    assert sent.startswith(autopilot.IMAGE_RULE) and "the athlete lifting the trophy in 1970" in sent
    assert "pelé" not in sent.lower()


def test_removing_the_legends_name_leaves_other_words_whole(app):
    """Only the name goes. Words that merely contain a part of it (Don in London and in
    abandoned) must reach the image model as they were written."""
    me = creator(app)
    add(me.browser, SACHIN, subject="Don Bradman")
    run = run_now(me.browser)
    cleaned, _ = image_prompt_for(
        app, run["projectId"], "Don Bradman's bat, a London crowd, an abandoned pavilion"
    )
    assert "Bradman" not in cleaned
    assert "a London crowd, an abandoned pavilion" in cleaned, cleaned


# ---------------------------------------------------------------- L8 and one run at a time


def test_l8_an_empty_queue_starts_nothing(app, database):
    me = creator(app)
    error = assert_error(me.browser.post("/autopilot/run"), 409, "queue_empty")
    assert "queue is empty" in error["message"]
    insert_topic(database, me.id, "failed")
    assert_error(me.browser.post("/autopilot/run"), 409, "queue_empty")
    assert database.value("SELECT count(*) FROM projects") == 0


def test_l8_starts_the_project_as_the_settings_say(app):
    me = creator(app)
    me.browser.patch("/autopilot", {"tone": "dramatic", "targetSeconds": 60})
    add(me.browser)
    before = balance(me.browser)
    res = me.browser.post("/autopilot/run")
    assert res.status_code == 202, res.get_data(as_text=True)
    body = res.get_json()
    assert body["queue"] == [] and len(body["runs"]) == 1
    run = body["runs"][0]
    assert run["status"] == "running" and run["error"] is None
    assert run["runOn"] == datetime.now(UTC).date().isoformat() or run["runOn"]
    project = get_project(me.browser, run["projectId"])
    assert project["status"] == "scripting"
    assert (project["tone"], project["targetSeconds"], project["mode"]) == ("dramatic", 60, "faceless")
    assert project["topic"] == f"Pelé (football legend): {TOPIC['angle']}"
    assert [j["type"] for j in project["activeJobs"]] == ["script"]
    # It costs what a script costs by hand.
    assert balance(me.browser) == before - COSTS["script"]


def test_l8_works_with_the_autopilot_off_and_no_channel(app):
    """L8 is for trying it out: neither the switch nor a channel is needed to make the Reel."""
    me = creator(app)
    add(me.browser)
    assert state(me.browser)["settings"]["enabled"] is False
    assert run_now(me.browser)["status"] == "running"


def test_one_run_at_a_time_for_each_creator(app, database):
    me, other = creator(app), creator(app)
    for who in (me, other):
        add(who.browser)
        add(who.browser, SENNA)
        add(who.browser, SACHIN)
    first = run_now(me.browser)
    error = assert_error(me.browser.post("/autopilot/run"), 409, "not_started")
    assert "already being made" in error["message"]
    # Through every step of the run, a second one is refused.
    project_id = first["projectId"]
    for status in ("draft", "generating", "ready", "rendering", "rendered"):
        drive_to(app, me.browser, project_id, status)
        assert_error(me.browser.post("/autopilot/run"), 409, "not_started")
    assert database.value("SELECT count(*) FROM projects WHERE user_id = :u", u=me.id) == 1
    assert len(state(me.browser)["queue"]) == 2
    # Another creator's run is their own.
    assert run_now(other.browser)["status"] == "running"
    # AP-5: once the first Reel waits for approval, the next can be made; several can wait.
    finish(app)
    assert topic_of(me.browser, first["id"])["status"] == "awaiting_approval"
    second = run_now(me.browser)
    assert second["id"] != first["id"]
    finish(app)
    waiting = [r for r in state(me.browser)["runs"] if r["status"] == "awaiting_approval"]
    assert len(waiting) == 2


def test_the_queue_is_taken_in_order_when_no_topic_fits_the_day(app):
    me = creator(app)
    me.browser.patch("/autopilot", {"rotation": {str(day): "cricket" for day in range(7)}})
    add(me.browser)
    add(me.browser, SENNA)
    assert run_now(me.browser)["subject"] == "Pelé"


def test_run_now_takes_the_days_sport_first(app):
    me = creator(app)
    me.browser.patch("/autopilot", {"rotation": {str(day): "cricket" for day in range(7)}})
    add(me.browser)
    add(me.browser, SENNA)
    add(me.browser, SACHIN)
    add(me.browser, SACHIN, subject="Shane Warne")
    assert run_now(me.browser)["subject"] == "Sachin Tendulkar"
    assert [t["subject"] for t in state(me.browser)["queue"]] == ["Pelé", "Ayrton Senna", "Shane Warne"]


# ---------------------------------------------------------------- the daily start


def test_start_due_waits_for_the_hour_then_runs_once_a_day(app):
    me = enabled_creator(app, hour=17)
    for topic in (TOPIC, SENNA, SACHIN):
        add(me.browser, topic)
    assert start_due(app, TUESDAY.replace(hour=0, minute=2)) == 0
    assert start_due(app, TUESDAY.replace(hour=16, minute=59)) == 0
    assert state(me.browser)["runs"] == [] and state(me.browser)["settings"]["lastRunOn"] is None
    assert start_due(app, TUESDAY.replace(hour=17, minute=2)) == 1  # the hour itself
    found = state(me.browser)
    [run] = found["runs"]
    assert (run["subject"], run["status"], run["runOn"]) == ("Ayrton Senna", "running", "2026-12-01")
    assert found["settings"]["lastRunOn"] == "2026-12-01"
    assert get_project(me.browser, run["projectId"])["status"] == "scripting"
    for hour in (17, 18, 23):
        assert start_due(app, TUESDAY.replace(hour=hour, minute=32)) == 0
    finish(app)
    # The next day, before the hour and then at it.
    assert start_due(app, WEDNESDAY.replace(hour=9)) == 0
    assert start_due(app, WEDNESDAY) == 1
    runs = state(me.browser)["runs"]
    assert [(r["subject"], r["runOn"]) for r in runs] == [
        ("Pelé", "2026-12-02"),
        ("Ayrton Senna", "2026-12-01"),
    ]


def test_start_due_starts_later_the_same_day_if_the_hour_was_missed(app):
    """AP-6."""
    me = enabled_creator(app, hour=5)
    add(me.browser)
    assert start_due(app, TUESDAY.replace(hour=23, minute=2)) == 1
    assert state(me.browser)["runs"][0]["runOn"] == "2026-12-01"


@pytest.mark.parametrize("hour", [0, 23])
def test_start_due_at_the_ends_of_the_day(app, hour):
    me = enabled_creator(app, hour=hour)
    add(me.browser)
    if hour:
        assert start_due(app, TUESDAY.replace(hour=hour - 1, minute=59)) == 0
    assert start_due(app, TUESDAY.replace(hour=hour, minute=2)) == 1
    assert state(me.browser)["runs"][0]["status"] == "running"


def test_start_due_follows_the_rotation_then_the_queue_order(app):
    me = enabled_creator(app, hour=0)
    for topic in (TOPIC, SACHIN, SENNA):
        add(me.browser, topic)
    add(me.browser, SENNA, subject="Niki Lauda", angle="Back in the car six weeks after the Nürburgring")
    # Tuesday is F1: the first F1 topic, not the first topic.
    assert start_due(app, TUESDAY) == 1
    assert state(me.browser)["runs"][0]["subject"] == "Ayrton Senna"
    finish(app)
    # Wednesday is football.
    assert start_due(app, WEDNESDAY) == 1
    assert state(me.browser)["runs"][0]["subject"] == "Pelé"
    finish(app)
    # Friday is football and none is left: the first in the queue.
    assert start_due(app, datetime(2026, 12, 4, 12, tzinfo=UTC)) == 1
    assert state(me.browser)["runs"][0]["subject"] == "Sachin Tendulkar"
    assert [t["subject"] for t in state(me.browser)["queue"]] == ["Niki Lauda"]


def test_start_due_uses_the_creators_own_rotation(app):
    me = enabled_creator(app, hour=0, rotation={"1": "cricket"})
    for topic in (TOPIC, SENNA, SACHIN):
        add(me.browser, topic)
    assert start_due(app, TUESDAY) == 1
    assert state(me.browser)["runs"][0]["subject"] == "Sachin Tendulkar"


def test_start_due_skips_an_autopilot_that_is_off(app):
    me = creator(app)
    connect(me.browser)
    add(me.browser)
    assert start_due(app, TUESDAY) == 0
    me.browser.patch("/autopilot", {"enabled": True})
    me.browser.patch("/autopilot", {"enabled": False})
    assert start_due(app, TUESDAY) == 0
    found = state(me.browser)
    assert found["runs"] == [] and len(found["queue"]) == 1 and found["settings"]["lastRunOn"] is None


@pytest.mark.parametrize("column", ["disabled_at", "deleted_at"])
def test_start_due_skips_disabled_and_deleted_accounts(app, database, column):
    me = enabled_creator(app, hour=0)
    add(me.browser)
    database.run(f"UPDATE users SET {column} = now() WHERE id = :u", u=me.id)  # noqa: S608 - a fixed name
    assert start_due(app, TUESDAY) == 0
    assert database.value("SELECT status FROM autopilot_topics WHERE user_id = :u", u=me.id) == "queued"
    assert database.value("SELECT count(*) FROM projects WHERE user_id = :u", u=me.id) == 0
    assert database.value("SELECT count(*) FROM jobs WHERE user_id = :u", u=me.id) == 0
    # Back again (an admin enables the account): the day's Reel starts at the next check.
    database.run(f"UPDATE users SET {column} = NULL WHERE id = :u", u=me.id)  # noqa: S608
    assert start_due(app, TUESDAY) == 1


def test_start_due_with_an_empty_queue_marks_the_day(app):
    """AP-1: the day is skipped."""
    me = enabled_creator(app, hour=9)
    assert start_due(app, TUESDAY) == 0
    found = state(me.browser)
    assert found["settings"]["lastRunOn"] == "2026-12-01" and found["runs"] == []
    assert found["settings"]["enabled"] is True
    # A topic added later that day waits for tomorrow.
    add(me.browser)
    assert start_due(app, TUESDAY.replace(hour=20)) == 0
    assert len(state(me.browser)["queue"]) == 1
    assert start_due(app, WEDNESDAY) == 1


def test_start_due_still_runs_while_yesterdays_reel_waits_for_approval(app):
    """AP-5."""
    me = enabled_creator(app, hour=0)
    add(me.browser, SENNA)
    add(me.browser)
    assert start_due(app, TUESDAY) == 1
    finish(app)
    assert state(me.browser)["runs"][0]["status"] == "awaiting_approval"
    assert start_due(app, WEDNESDAY) == 1
    finish(app)
    assert [r["status"] for r in state(me.browser)["runs"]] == ["awaiting_approval", "awaiting_approval"]


def test_start_due_does_not_start_a_second_run_while_one_is_under_way(app, database):
    me = enabled_creator(app, hour=0)
    add(me.browser, SENNA)
    add(me.browser)
    assert start_due(app, TUESDAY) == 1
    assert start_due(app, WEDNESDAY) == 0  # yesterday's is still being made
    assert database.value("SELECT count(*) FROM projects WHERE user_id = :u", u=me.id) == 1
    assert [t["subject"] for t in state(me.browser)["queue"]] == ["Pelé"]


def test_start_due_starts_every_creator_who_is_due(app):
    early, late, off = enabled_creator(app, hour=6), enabled_creator(app, hour=20), creator(app)
    for who in (early, late, off):
        add(who.browser)
    assert start_due(app, TUESDAY.replace(hour=12)) == 1
    assert state(early.browser)["runs"][0]["status"] == "running"
    assert state(late.browser)["runs"] == [] and state(off.browser)["runs"] == []
    assert start_due(app, TUESDAY.replace(hour=20)) == 1
    assert state(late.browser)["runs"][0]["status"] == "running"


def break_the_start_for(monkeypatch, subject: str):
    real = autopilot.topic_text

    def explode(topic):
        if topic.subject == subject:
            raise RuntimeError("a fault nobody planned for")
        return real(topic)

    monkeypatch.setattr(autopilot, "topic_text", explode)
    return lambda: monkeypatch.setattr(autopilot, "topic_text", real)


def test_start_due_one_creators_failure_does_not_stop_the_others(app, database, monkeypatch):
    broken, fine, also_fine = (enabled_creator(app, hour=0) for _ in range(3))
    add(fine.browser)
    add(broken.browser, subject="Boom")
    add(also_fine.browser, SENNA)
    break_the_start_for(monkeypatch, "Boom")
    assert start_due(app, TUESDAY) == 2
    assert state(fine.browser)["runs"][0]["status"] == "running"
    assert state(also_fine.browser)["runs"][0]["status"] == "running"
    # Nothing half-made is left for the one that broke.
    left = state(broken.browser)
    assert left["runs"] == [] and [t["status"] for t in left["queue"]] == ["queued"]
    assert database.value("SELECT count(*) FROM projects WHERE user_id = :u", u=broken.id) == 0
    assert database.value("SELECT count(*) FROM jobs WHERE user_id = :u", u=broken.id) == 0
    assert balance(broken.browser) == 100


def test_start_due_tries_again_at_the_next_check_after_an_unexpected_fault(app, monkeypatch):
    """A fault that is nobody's refusal (the database or Redis hiccups at 17:02) leaves no
    failed topic and no reason on the screen, so the day must not count as run."""
    me = enabled_creator(app, hour=0)
    add(me.browser, subject="Boom")
    mend = break_the_start_for(monkeypatch, "Boom")
    assert start_due(app, TUESDAY) == 0
    mend()
    assert state(me.browser)["runs"] == []
    assert start_due(app, TUESDAY.replace(hour=18)) == 1, "the day was marked as run although nothing started"
    assert state(me.browser)["runs"][0]["status"] == "running"


def test_a_run_started_by_hand_does_not_use_up_the_days_scheduled_run(app):
    me = enabled_creator(app, hour=17)
    add(me.browser)
    add(me.browser, subject="Garrincha")
    add(me.browser, subject="Zico")
    run_now(me.browser)
    assert state(me.browser)["settings"]["lastRunOn"] is None
    # Refused because one is being made: that doesn't use the day up either.
    assert_error(me.browser.post("/autopilot/run"), 409, "not_started")
    assert state(me.browser)["settings"]["lastRunOn"] is None
    finish(app)
    assert start_due(app, WEDNESDAY) == 1
    assert state(me.browser)["settings"]["lastRunOn"] == "2026-12-02"
    assert len(state(me.browser)["runs"]) == 2


def test_two_starts_at_the_same_moment_make_one_reel(app, database, monkeypatch):
    """One run at a time: a second start arriving while the first is between choosing the topic
    and creating its project (two tabs, or the hourly task and the button) must find the topic
    taken."""
    from reelforge_api.extensions import db
    from reelforge_api.models import User

    from .render_support import in_another_thread

    me = creator(app)
    add(me.browser)
    real = autopilot.settings_for
    calls = {"locked": 0, "second": None}

    def second_start():
        with app.app_context():
            started_topic = autopilot.start_next(db.session.get(User, me.id))
            return None if started_topic is None else started_topic.status

    def settings_for(user_id, *, lock: bool = False):
        if lock:
            calls["locked"] += 1
            if calls["locked"] == 2:  # the first start has chosen its topic and let go of the lock
                calls["second"] = in_another_thread(second_start)
        return real(user_id, lock=lock)

    monkeypatch.setattr(autopilot, "settings_for", settings_for)
    res = me.browser.post("/autopilot/run")
    monkeypatch.setattr(autopilot, "settings_for", real)
    made = database.value("SELECT count(*) FROM projects WHERE user_id = :u", u=me.id)
    scripts = database.value("SELECT count(*) FROM jobs WHERE user_id = :u AND type = 'script'", u=me.id)
    assert (made, scripts) == (
        1,
        1,
    ), f"{made} Reels were started for one topic ({res.status_code}, {calls['second']})"
    assert balance(me.browser) == 100 - COSTS["script"]


def test_the_scheduled_tasks_run_the_daily_start_and_the_advance(app, monkeypatch):
    from celery.schedules import crontab

    from reelforge_api.tasks.jobs import autopilot_advance, autopilot_start

    schedule = app.extensions["celery"].conf.beat_schedule
    start = next(e for e in schedule.values() if e["task"] == "autopilot.start")
    advance = next(e for e in schedule.values() if e["task"] == "autopilot.advance")
    assert start["schedule"] == crontab(minute=2)  # hourly
    assert advance["schedule"] == crontab(minute="*/2")
    assert start["options"]["queue"] == advance["options"]["queue"] == "maintenance"

    me = enabled_creator(app, hour=0)
    add(me.browser)
    with app.app_context():
        assert autopilot_start() == 1
        assert autopilot_advance() == 1
    assert state(me.browser)["runs"][0]["status"] == "running"


# ---------------------------------------------------------------- a run, step by step


def test_advance_moves_one_step_for_each_project_status(app, database):
    me = creator(app)
    connect(me.browser)
    me.browser.patch("/autopilot", {"mode": "auto"})
    add(me.browser)
    run = run_now(me.browser)
    project_id = run["projectId"]

    def status() -> str:
        return get_project(me.browser, project_id)["status"]

    def job_types() -> list[str]:
        rows = database.rows(
            "SELECT type FROM jobs WHERE project_id = :p ORDER BY queued_at, type", p=project_id
        )
        return sorted({r["type"] for r in rows})

    def nothing_moves(expected: str) -> None:
        """While the project is busy the autopilot waits: no status change, no new job."""
        waiting, count = list(jobs.manual_queue), database.value("SELECT count(*) FROM jobs")
        for _ in range(2):
            assert tick(app) == 1
        assert status() == expected and topic_of(me.browser, run["id"])["status"] in ("running", "publishing")
        assert list(jobs.manual_queue) == waiting and database.value("SELECT count(*) FROM jobs") == count

    nothing_moves("scripting")
    run_jobs(app)
    assert status() == "draft"
    tick(app)  # draft: Generate
    assert status() == "generating" and job_types() == ["script", "visual", "voice"]
    nothing_moves("generating")
    run_jobs(app)
    assert status() == "ready"
    tick(app)  # ready: Render
    assert status() == "rendering" and "render" in job_types()
    nothing_moves("rendering")
    run_jobs(app)
    assert status() == "rendered"
    tick(app)  # rendered: the post's text
    assert status() == "rendered" and "caption" in job_types()
    assert topic_of(me.browser, run["id"])["status"] == "running"
    tick(app)  # the caption job hasn't run yet: wait for it, don't start another
    assert (
        database.value("SELECT count(*) FROM jobs WHERE project_id = :p AND type = 'caption'", p=project_id)
        == 1
    )
    run_jobs(app)
    assert get_project(me.browser, project_id)["postTitle"]
    tick(app)  # automatic: publish
    assert status() == "posting" and topic_of(me.browser, run["id"])["status"] == "publishing"
    nothing_moves("posting")
    run_jobs(app)
    assert status() == "posted"
    tick(app)
    done = topic_of(me.browser, run["id"])
    assert done["status"] == "published" and done["error"] is None
    assert done["url"].startswith("https://youtube.com/shorts/") and done["title"]
    assert tick(app) == 0  # nothing is under way any more
    # One of each: nothing was made twice.
    counts = database.rows(
        "SELECT type, count(*) AS n FROM jobs WHERE project_id = :p GROUP BY type", p=project_id
    )
    assert {r["type"]: r["n"] for r in counts if r["type"] in ("script", "render", "caption", "publish")} == {
        "script": 1, "render": 1, "caption": 1, "publish": 1,
    }  # fmt: skip
    assert len(MockYouTube.uploads) == 1


def test_a_run_costs_what_the_same_reel_costs_by_hand(app):
    by_hand, by_autopilot = creator(app), creator(app)
    add(by_autopilot.browser)
    run = run_now(by_autopilot.browser)
    finish(app)
    assert topic_of(by_autopilot.browser, run["id"])["status"] == "awaiting_approval"
    made = get_project(by_autopilot.browser, run["projectId"])

    res = by_hand.browser.post(
        "/projects", {"topic": made["topic"], "tone": made["tone"], "targetSeconds": made["targetSeconds"]}
    )
    assert res.status_code == 202, res.get_data(as_text=True)
    project_id = res.get_json()["project"]["id"]
    run_jobs(app)
    assert by_hand.browser.post(f"/projects/{project_id}/generate").status_code == 202
    run_jobs(app)
    started(by_hand.browser, project_id)
    run_jobs(app)
    assert balance(by_autopilot.browser) == balance(by_hand.browser) < 100


def test_approve_mode_waits_and_shows_the_video_and_the_written_title(app):
    me = creator(app)
    connect(me.browser)
    waiting = waiting_run(app, me.browser)
    project = get_project(me.browser, waiting["projectId"])
    assert project["status"] == "rendered"
    assert waiting["title"] == project["postTitle"] != ""
    assert waiting["video"]["url"] and waiting["video"]["downloadUrl"]
    assert waiting["url"] is None and waiting["error"] is None
    assert MockYouTube.uploads == []
    assert tick(app) == 0  # a waiting Reel is left alone
    assert topic_of(me.browser, waiting["id"])["status"] == "awaiting_approval"


def test_the_mode_is_read_when_the_reel_is_finished(app):
    """Switching to automatic while a Reel is being made publishes that Reel too."""
    me = creator(app)
    connect(me.browser)
    add(me.browser)
    run = run_now(me.browser)
    drive_to(app, me.browser, run["projectId"], "ready")
    me.browser.patch("/autopilot", {"mode": "auto", "visibility": "private"})
    finish(app)
    assert topic_of(me.browser, run["id"])["status"] == "published"
    assert MockYouTube.uploads[0]["metadata"]["status"]["privacyStatus"] == "private"


# ---------------------------------------------------------------- AP-4: a step fails


def test_a_refused_script_fails_the_topic_with_the_jobs_reason(app, database):
    me = creator(app)
    add(me.browser, subject="[refuse] Pelé")
    run = run_now(me.browser)
    run_jobs(app)
    project = get_project(me.browser, run["projectId"])
    assert project["status"] == "draft" and project["scenes"] == []
    tick(app)
    failed = topic_of(me.browser, run["id"])
    assert failed["status"] == "failed"
    assert failed["error"] == jobs.MESSAGES["content_refused"]
    assert failed["projectId"] == run["projectId"]  # the project stays, to fix by hand
    # The script's credit came back, nothing else was started and nothing is retried.
    assert balance(me.browser) == 100
    assert tick(app) == 0 and jobs.manual_queue == []
    assert database.value("SELECT count(*) FROM jobs WHERE user_id = :u", u=me.id) == 1


def test_a_script_the_model_could_not_write_fails_the_topic(app):
    me = creator(app)
    add(me.browser, angle="[fail] the story")
    run = run_now(me.browser)
    finish(app)
    failed = topic_of(me.browser, run["id"])
    assert failed["status"] == "failed" and failed["error"] == jobs.MESSAGES["provider_failed"]


def test_a_scene_that_could_not_be_made_fails_the_topic(app):
    me = creator(app)
    add(me.browser, subject="[voice-fail] Pelé")
    run = run_now(me.browser)
    finish(app)
    failed = topic_of(me.browser, run["id"])
    assert failed["status"] == "failed"
    assert failed["error"] == "Some scenes couldn't be made. Open the Reel to retry them."
    assert get_project(me.browser, run["projectId"])["status"] == "needs_attention"
    assert MockRenderService.started == []
    # Nothing is retried, and the next topic can start.
    assert tick(app) == 0
    add(me.browser, SENNA)
    assert run_now(me.browser)["subject"] == "Ayrton Senna"


def test_a_failed_render_fails_the_topic_and_gives_the_credits_back(app):
    me = creator(app)
    add(me.browser)
    run = run_now(me.browser)
    drive_to(app, me.browser, run["projectId"], "ready")
    before = balance(me.browser)
    MockRenderService.fail_next = 2  # both attempts (R-1)
    finish(app)
    failed = topic_of(me.browser, run["id"])
    assert failed["status"] == "failed"
    assert failed["error"] == "The video couldn't be rendered. Open the Reel to try again."
    assert get_project(me.browser, run["projectId"])["status"] == "render_failed"
    assert balance(me.browser) == before
    assert failed["video"] is None and tick(app) == 0


def test_a_failed_caption_job_still_proceeds_with_the_reels_title(app, monkeypatch):
    me = creator(app)
    connect(me.browser)

    def no_caption(self, context):
        raise ProviderError("mock failure", retryable=False)

    monkeypatch.setattr(MockLLM, "_caption", no_caption)
    waiting = waiting_run(app, me.browser)
    project = get_project(me.browser, waiting["projectId"])
    assert project["postTitle"] == "" and project["latestRender"]["state"] == "succeeded"
    assert waiting["title"] == project["title"] and waiting["error"] is None
    # Not asked again on every round.
    assert tick(app) == 0
    assert me.browser.post(f"/autopilot/topics/{waiting['id']}/approve").status_code == 202
    finish(app)
    assert topic_of(me.browser, waiting["id"])["status"] == "published"
    assert MockYouTube.uploads[0]["metadata"]["snippet"]["title"] == project["title"][:100]


def test_a_failed_caption_job_in_automatic_mode_still_publishes(app, monkeypatch):
    me = creator(app)
    connect(me.browser)
    me.browser.patch("/autopilot", {"mode": "auto"})
    monkeypatch.setattr(
        MockLLM,
        "_caption",
        lambda self, context: (_ for _ in ()).throw(ProviderError("down", retryable=False)),
    )
    add(me.browser)
    run = run_now(me.browser)
    finish(app)
    assert topic_of(me.browser, run["id"])["status"] == "published"
    title = get_project(me.browser, run["projectId"])["title"]
    assert MockYouTube.uploads[0]["metadata"]["snippet"]["title"] == title[:100]


# ---------------------------------------------------------------- AP-2: credits


def test_no_credits_at_the_daily_start_fails_the_topic_and_the_autopilot_stays_on(app, database):
    me = enabled_creator(app, hour=0)
    add(me.browser)
    add(me.browser, SENNA)
    set_balance(database, me.id, 0)
    assert start_due(app, WEDNESDAY) == 0
    found = state(me.browser)
    [failed] = found["runs"]
    assert failed["subject"] == "Pelé" and failed["status"] == "failed"
    assert failed["error"] == "This needs 1 credits; you have 0."
    assert found["settings"]["enabled"] is True and found["settings"]["lastRunOn"] == "2026-12-02"
    assert database.value("SELECT count(*) FROM projects WHERE user_id = :u", u=me.id) == 0
    assert database.value("SELECT count(*) FROM jobs WHERE user_id = :u", u=me.id) == 0
    # Not repeated every hour: the next topic waits for tomorrow.
    assert start_due(app, WEDNESDAY.replace(hour=20)) == 0
    assert [t["subject"] for t in state(me.browser)["queue"]] == ["Ayrton Senna"]
    # Tomorrow, with credits again, it carries on.
    set_balance(database, me.id, 50)
    assert start_due(app, datetime(2026, 12, 3, 9, tzinfo=UTC)) == 1


def test_no_credits_for_generate_fails_the_topic(app, database):
    me = creator(app)
    add(me.browser)
    set_balance(database, me.id, COSTS["script"])
    run = run_now(me.browser)
    run_jobs(app)
    tick(app)
    failed = topic_of(me.browser, run["id"])
    assert failed["status"] == "failed"
    assert failed["error"].startswith("This needs ") and failed["error"].endswith("credits; you have 0.")
    project = get_project(me.browser, run["projectId"])
    assert project["status"] == "draft" and project["activeJobs"] == []
    assert balance(me.browser) == 0 and jobs.manual_queue == []
    assert state(me.browser)["settings"] == {**state(me.browser)["settings"], "enabled": False}


def test_no_credits_for_the_render_fails_the_topic(app, database):
    me = enabled_creator(app, hour=0)
    add(me.browser)
    run = run_now(me.browser)
    drive_to(app, me.browser, run["projectId"], "ready")
    set_balance(database, me.id, COSTS["render"] - 1)
    tick(app)
    failed = topic_of(me.browser, run["id"])
    assert failed["status"] == "failed"
    assert failed["error"] == f"This needs {COSTS['render']} credits; you have {COSTS['render'] - 1}."
    assert get_project(me.browser, run["projectId"])["status"] == "ready"
    assert MockRenderService.started == [] and balance(me.browser) == COSTS["render"] - 1
    assert state(me.browser)["settings"]["enabled"] is True  # AP-2: autopilot stays on


def test_the_post_text_and_publishing_need_no_credits(app, database):
    me = creator(app)
    connect(me.browser)
    add(me.browser)
    run = run_now(me.browser)
    drive_to(app, me.browser, run["projectId"], "rendered")
    set_balance(database, me.id, 0)
    finish(app)
    assert topic_of(me.browser, run["id"])["status"] == "awaiting_approval"
    assert me.browser.post(f"/autopilot/topics/{run['id']}/approve").status_code == 202
    finish(app)
    assert topic_of(me.browser, run["id"])["status"] == "published" and balance(me.browser) == 0


def test_l8_says_why_when_the_start_fails_even_with_earlier_runs_in_the_list(app, database):
    """The refusal carries the reason of the topic that just failed, not a guess from an
    older run."""
    me = creator(app)
    insert_topic(database, me.id, "skipped", subject="Yesterday", run_on="2026-11-20")
    add(me.browser)
    set_balance(database, me.id, 0)
    res = me.browser.post("/autopilot/run")
    error = assert_error(res, 409, "not_started")
    assert state(me.browser)["runs"] and any(r["status"] == "failed" for r in state(me.browser)["runs"])
    assert "credit" in error["message"].lower(), error["message"]


def test_a_topic_that_failed_to_start_today_is_listed_first(app, database):
    me = creator(app)
    insert_topic(database, me.id, "published", subject="Last week", run_on="2026-11-20")
    add(me.browser)
    set_balance(database, me.id, 0)
    assert me.browser.post("/autopilot/run").status_code == 409
    runs = state(me.browser)["runs"]
    assert [r["subject"] for r in runs] == ["Pelé", "Last week"], [(r["subject"], r["runOn"]) for r in runs]
    assert runs[0]["runOn"] is not None


# ---------------------------------------------------------------- AP-3: YouTube can't take it now


def test_reconnect_required_makes_the_reel_wait_with_the_reason(app, database):
    me = creator(app)
    connect(me.browser)
    me.browser.patch("/autopilot", {"mode": "auto"})
    add(me.browser)
    run = run_now(me.browser)
    drive_to(app, me.browser, run["projectId"], "rendered")
    database.run("UPDATE youtube_accounts SET status = 'reconnect_required' WHERE user_id = :u", u=me.id)
    finish(app)
    waiting = topic_of(me.browser, run["id"])
    assert waiting["status"] == "awaiting_approval"
    assert waiting["error"] == "Your YouTube connection has expired. Connect YouTube again, then try again."
    assert get_project(me.browser, run["projectId"])["status"] == "rendered" and MockYouTube.uploads == []
    assert tick(app) == 0  # it waits for the creator, it isn't tried every 2 minutes
    # Publish is refused the same way until the channel is connected again.
    assert_error(me.browser.post(f"/autopilot/topics/{run['id']}/approve"), 409, "reconnect_required")
    assert topic_of(me.browser, run["id"])["status"] == "awaiting_approval"
    connect(me.browser)
    assert me.browser.post(f"/autopilot/topics/{run['id']}/approve").status_code == 202
    finish(app)
    done = topic_of(me.browser, run["id"])
    assert done["status"] == "published" and done["error"] is None


def test_not_connected_makes_the_reel_wait_and_publish_is_refused_until_connected(app):
    me = creator(app)
    me.browser.patch("/autopilot", {"mode": "auto"})
    add(me.browser)
    run = run_now(me.browser)
    finish(app)
    waiting = topic_of(me.browser, run["id"])
    assert (
        waiting["status"] == "awaiting_approval" and waiting["error"] == "Connect your YouTube channel first."
    )
    assert waiting["video"]["url"]
    assert_error(me.browser.post(f"/autopilot/topics/{run['id']}/approve"), 409, "not_connected")
    assert topic_of(me.browser, run["id"])["status"] == "awaiting_approval"
    connect(me.browser)
    assert me.browser.post(f"/autopilot/topics/{run['id']}/approve").status_code == 202


def test_youtube_switched_off_makes_the_reel_wait_with_the_reason(app, database):
    me = creator(app)
    connect(me.browser)
    me.browser.patch("/autopilot", {"mode": "auto"})
    add(me.browser)
    run = run_now(me.browser)
    drive_to(app, me.browser, run["projectId"], "rendered")
    database.run(
        "INSERT INTO provider_switches (provider, enabled, reason) VALUES ('youtube', false, 'quota')"
    )
    finish(app)
    waiting = topic_of(me.browser, run["id"])
    assert waiting["status"] == "awaiting_approval"
    assert waiting["error"] == "Publishing to YouTube is paused for now. Try again later."
    assert MockYouTube.uploads == []
    assert_error(me.browser.post(f"/autopilot/topics/{run['id']}/approve"), 503, "provider_unavailable")
    database.run("DELETE FROM provider_switches")
    assert me.browser.post(f"/autopilot/topics/{run['id']}/approve").status_code == 202
    finish(app)
    assert topic_of(me.browser, run["id"])["status"] == "published"


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("quota_exceeded", "YouTube's daily upload limit is reached. Try again tomorrow."),
        ("upload_failed", "The upload to YouTube didn't go through. Please try again."),
        ("reconnect_required", "Your YouTube connection has expired. Connect YouTube again, then try again."),
    ],
)
def test_a_publish_that_fails_makes_the_reel_wait_with_the_reason(app, code, reason):
    me = creator(app)
    connect(me.browser)
    me.browser.patch("/autopilot", {"mode": "auto"})
    add(me.browser)
    run = run_now(me.browser)
    drive_to(app, me.browser, run["projectId"], "rendered")
    MockYouTube.fail_with = code
    finish(app)
    waiting = topic_of(me.browser, run["id"])
    assert waiting["status"] == "awaiting_approval" and waiting["error"] == reason
    assert get_project(me.browser, run["projectId"])["status"] == "post_failed"
    assert waiting["video"]["url"] and waiting["url"] is None
    assert tick(app) == 0  # not retried by itself
    # The creator tries again with Publish.
    if code == "reconnect_required":
        connect(me.browser)
    assert me.browser.post(f"/autopilot/topics/{run['id']}/approve").status_code == 202
    finish(app)
    done = topic_of(me.browser, run["id"])
    assert done["status"] == "published" and done["error"] is None and done["url"]


def test_a_publish_that_fails_after_approval_waits_again(app):
    me = creator(app)
    connect(me.browser)
    waiting = waiting_run(app, me.browser)
    MockYouTube.fail_with = "quota_exceeded"
    assert me.browser.post(f"/autopilot/topics/{waiting['id']}/approve").status_code == 202
    finish(app)
    again = topic_of(me.browser, waiting["id"])
    assert again["status"] == "awaiting_approval" and "daily upload limit" in again["error"]


def test_the_post_text_that_cannot_be_asked_for_does_not_fail_a_finished_reel(app, database):
    """No language model can be asked (switched off, the hourly limit): the Reel's own title is
    used and the run goes on."""
    me = creator(app)
    connect(me.browser)
    add(me.browser)
    run = run_now(me.browser)
    project = drive_to(app, me.browser, run["projectId"], "rendered")
    database.run("INSERT INTO provider_switches (provider, enabled, reason) VALUES ('mock', false, 'test')")
    finish(app)
    waiting = topic_of(me.browser, run["id"])
    assert waiting["status"] == "awaiting_approval" and waiting["error"] is None
    assert waiting["title"] == project["title"][:100]
    count = "SELECT count(*) FROM jobs WHERE project_id = :p AND type = 'caption'"
    assert database.value(count, p=project["id"]) == 0


def age_the_run(database, topic_id: str, interval: str) -> None:
    """Moves the run's last change back in time (the trigger would set it to now)."""
    database.run("ALTER TABLE autopilot_topics DISABLE TRIGGER autopilot_topics_updated_at")
    try:
        database.run(
            f"UPDATE autopilot_topics SET updated_at = now() - interval '{interval}' WHERE id = :t",  # noqa: S608
            t=topic_id,
        )
    finally:
        database.run("ALTER TABLE autopilot_topics ENABLE TRIGGER autopilot_topics_updated_at")


def test_a_run_that_has_not_finished_after_four_hours_is_failed(app, database):
    me = creator(app)
    add(me.browser)
    add(me.browser, subject="Garrincha")
    run = run_now(me.browser)  # its script job is never run: a run that cannot move
    age_the_run(database, run["id"], "3 hours 55 minutes")
    tick(app)
    assert topic_of(me.browser, run["id"])["status"] == "running"
    age_the_run(database, run["id"], "4 hours 1 minute")
    tick(app)
    failed = topic_of(me.browser, run["id"])
    assert failed["status"] == "failed"
    assert failed["error"] == "This run took too long and was stopped. Open the Reel to carry on by hand."
    assert failed["projectId"] == run["projectId"]
    # The days after it are not blocked.
    assert tick(app) == 0
    jobs.manual_queue.clear()
    assert run_now(me.browser)["subject"] == "Garrincha"


def test_a_publish_stuck_for_four_hours_is_failed_too(app, database):
    me = creator(app)
    connect(me.browser)
    waiting = waiting_run(app, me.browser)
    assert me.browser.post(f"/autopilot/topics/{waiting['id']}/approve").status_code == 202
    age_the_run(database, waiting["id"], "5 hours")
    tick(app)
    assert topic_of(me.browser, waiting["id"])["status"] == "failed"


# ---------------------------------------------------------------- the Reel or its owner goes away


@pytest.mark.parametrize("at_status", ["scripting", "draft", "generating", "ready", "rendering", "rendered"])
def test_a_project_deleted_mid_run_fails_the_topic(app, database, at_status):
    me = creator(app)
    add(me.browser)
    add(me.browser, subject="Garrincha")
    run = run_now(me.browser)
    drive_to(app, me.browser, run["projectId"], at_status)
    before = balance(me.browser)
    assert me.browser.delete(f"/projects/{run['projectId']}").status_code == 204
    finish(app)
    failed = topic_of(me.browser, run["id"])
    assert failed["status"] == "failed" and failed["error"] == "The Reel was deleted."
    assert balance(me.browser) >= before  # whatever was still running was refunded
    assert MockYouTube.uploads == []
    assert database.value("SELECT count(*) FROM jobs WHERE state IN ('queued', 'running')") == 0
    # The autopilot is free for the next topic.
    assert run_now(me.browser)["subject"] == "Garrincha"


@pytest.mark.parametrize("column", ["disabled_at", "deleted_at"])
def test_an_account_disabled_or_deleted_mid_run_fails_the_topic(app, database, column):
    me = creator(app)
    add(me.browser)
    run = run_now(me.browser)
    drive_to(app, me.browser, run["projectId"], "ready")
    database.run(f"UPDATE users SET {column} = now() WHERE id = :u", u=me.id)  # noqa: S608 - a fixed name
    finish(app)
    row = database.one("SELECT status, error FROM autopilot_topics WHERE id = :t", t=run["id"])
    assert row == {"status": "failed", "error": "The account or the Reel is gone."}
    assert MockRenderService.started == []


def test_a_purged_project_fails_the_topic(app, database):
    me = creator(app)
    add(me.browser)
    run = run_now(me.browser)
    drive_to(app, me.browser, run["projectId"], "draft")
    database.run("UPDATE autopilot_topics SET project_id = NULL WHERE id = :t", t=run["id"])
    tick(app)
    failed = topic_of(me.browser, run["id"])
    assert failed["status"] == "failed" and failed["error"] == "The account or the Reel is gone."


# ---------------------------------------------------------------- AP-7: the creator steps in


def test_the_creator_generating_and_rendering_by_hand_is_carried_on_from(app, database):
    me = creator(app)
    connect(me.browser)
    add(me.browser)
    run = run_now(me.browser)
    project = drive_to(app, me.browser, run["projectId"], "draft")
    # The creator fixes a line, then presses Generate and Render themselves.
    scene = project["scenes"][0]
    res = me.browser.patch(f"/scenes/{scene['id']}", {"narration": "Pelé won his first World Cup at 17."})
    assert res.status_code == 200, res.get_data(as_text=True)
    assert me.browser.post(f"/projects/{project['id']}/generate").status_code == 202
    tick(app)  # generating by hand: the autopilot waits
    run_jobs(app)
    started(me.browser, project["id"])
    tick(app)  # rendering by hand: the autopilot waits
    finish(app)
    waiting = topic_of(me.browser, run["id"])
    assert waiting["status"] == "awaiting_approval" and waiting["error"] is None
    made = get_project(me.browser, project["id"])
    assert made["scenes"][0]["narration"] == "Pelé won his first World Cup at 17."
    assert database.value("SELECT count(*) FROM renders WHERE project_id = :p", p=project["id"]) == 1
    assert database.value(
        "SELECT count(*) FROM jobs WHERE project_id = :p AND type = 'voice'", p=project["id"]
    ) == len(made["scenes"])


def test_an_edit_after_the_render_is_rendered_again_before_it_waits(app, database):
    me = creator(app)
    connect(me.browser)
    add(me.browser)
    run = run_now(me.browser)
    project = drive_to(app, me.browser, run["projectId"], "rendered")
    res = me.browser.patch(f"/scenes/{project['scenes'][0]['id']}", {"onScreenText": "1958"})
    assert res.status_code == 200, res.get_data(as_text=True)
    assert get_project(me.browser, project["id"])["status"] == "ready"
    finish(app)
    waiting = topic_of(me.browser, run["id"])
    assert waiting["status"] == "awaiting_approval"
    made = get_project(me.browser, project["id"])
    assert made["status"] == "rendered" and made["latestRender"]["outdated"] is False
    assert database.value("SELECT count(*) FROM renders WHERE project_id = :p", p=project["id"]) == 2


def test_the_creator_publishing_by_hand_finishes_the_run(app):
    me = creator(app)
    connect(me.browser)
    add(me.browser)
    run = run_now(me.browser)
    project = drive_to(app, me.browser, run["projectId"], "rendered")
    res = me.browser.post(f"/renders/{project['latestRender']['id']}/publish", PUBLISH_BODY)
    assert res.status_code == 202, res.get_data(as_text=True)
    tick(app)  # posting by hand: the autopilot waits
    assert topic_of(me.browser, run["id"])["status"] == "running"
    finish(app)
    done = topic_of(me.browser, run["id"])
    assert done["status"] == "published" and done["url"].startswith("https://youtube.com/shorts/")
    assert done["title"] == PUBLISH_BODY["title"]
    assert len(MockYouTube.uploads) == 1


def test_a_reel_waiting_on_an_upload_does_not_keep_the_run_going_for_ever(app, database):
    """AP-7 with AP-4: the creator switches a finished scene to "my own upload" mid-run and adds
    no file. Nothing can be generated and nothing can be rendered, so the run must end (failed,
    with the reason) and free the autopilot, not stay "being made" for good."""
    me = creator(app)
    add(me.browser)
    add(me.browser, SENNA)
    run = run_now(me.browser)
    project = drive_to(app, me.browser, run["projectId"], "ready")
    res = me.browser.patch(f"/scenes/{project['scenes'][0]['id']}", {"visualSource": "upload"})
    assert res.status_code == 200, res.get_data(as_text=True)
    assert get_project(me.browser, project["id"])["status"] == "draft"
    version = get_project(me.browser, project["id"])["timelineVersion"]
    for _ in range(5):
        tick(app)
        run_jobs(app)
    after = topic_of(me.browser, run["id"])
    assert after["status"] == "failed", "the run is stuck: it never ends and blocks every later run"
    assert after["error"] == "Some scenes need you. Open the Reel to finish them."
    # And the idle rounds didn't count as edits of the Reel.
    assert get_project(me.browser, project["id"])["timelineVersion"] == version


def test_another_reel_of_the_creator_rendering_makes_the_run_wait_not_fail(app):
    me = creator(app)
    connect(me.browser)
    add(me.browser)
    run = run_now(me.browser)
    drive_to(app, me.browser, run["projectId"], "ready")
    mine = ready_project(app, me.browser)
    started(me.browser, mine["id"])  # the creator's own render is in the queue (R-4: one at a time)
    for _ in range(3):
        tick(app)
        still = topic_of(me.browser, run["id"])
        assert still["status"] == "running" and still["error"] is None
        assert get_project(me.browser, run["projectId"])["status"] == "ready"
    run_jobs(app)  # the creator's render ends
    finish(app)
    assert topic_of(me.browser, run["id"])["status"] == "awaiting_approval"
    assert get_project(me.browser, mine["id"])["status"] == "rendered"


def test_advance_all_one_runs_failure_does_not_stop_the_others(app, monkeypatch):
    creators = [creator(app) for _ in range(3)]
    runs = []
    for who in creators:
        add(who.browser)
        runs.append(run_now(who.browser))
    for who, run in zip(creators, runs, strict=True):
        drive_to(app, who.browser, run["projectId"], "ready")
    broken = runs[1]["projectId"]
    real = autopilot.renders.start_render

    def explode(project):
        if str(project.id) == broken:
            raise RuntimeError("a fault nobody planned for")
        return real(project)

    monkeypatch.setattr(autopilot.renders, "start_render", explode)
    assert tick(app) == 3
    statuses = [
        get_project(who.browser, run["projectId"])["status"] for who, run in zip(creators, runs, strict=True)
    ]
    assert statuses == ["rendering", "ready", "rendering"]
    # The one that broke is still under way, costs nothing, and goes on once the fault is gone.
    assert topic_of(creators[1].browser, runs[1]["id"])["status"] == "running"
    assert balance(creators[1].browser) == balance(creators[0].browser) + COSTS["render"]
    monkeypatch.setattr(autopilot.renders, "start_render", real)
    finish(app)
    for who, run in zip(creators, runs, strict=True):
        assert topic_of(who.browser, run["id"])["status"] == "awaiting_approval"


def test_advance_ignores_topics_that_are_not_under_way(app, database):
    me = creator(app)
    ids = [
        insert_topic(database, me.id, status, position=n)
        for n, status in enumerate(STATUSES)
        if status not in ("running", "publishing")
    ]
    assert tick(app) == 0
    with app.app_context():
        for topic_id in [*ids, "00000000-0000-4000-8000-000000000000"]:
            autopilot.advance(topic_id)
    rows = database.rows("SELECT status, error FROM autopilot_topics ORDER BY position")
    assert [r["status"] for r in rows] == ["queued", "awaiting_approval", "published", "skipped", "failed"]
    assert all(r["error"] is None for r in rows)


# ---------------------------------------------------------------- L6: approve


@pytest.mark.parametrize("status", [s for s in STATUSES if s != "awaiting_approval"])
def test_l6_only_a_waiting_reel_can_be_published(app, database, status):
    me = creator(app)
    connect(me.browser)
    topic_id = insert_topic(database, me.id, status)
    error = assert_error(me.browser.post(f"/autopilot/topics/{topic_id}/approve"), 409, "not_waiting")
    assert error["message"] == "This Reel isn't waiting to be published."
    assert database.value("SELECT status FROM autopilot_topics WHERE id = :t", t=topic_id) == status
    assert MockYouTube.uploads == [] and jobs.manual_queue == []


def test_l6_twice_publishes_once(app):
    me = creator(app)
    connect(me.browser)
    waiting = waiting_run(app, me.browser)
    res = me.browser.post(f"/autopilot/topics/{waiting['id']}/approve")
    assert res.status_code == 202
    [run] = res.get_json()["runs"]
    assert run["status"] == "publishing" and run["error"] is None
    assert get_project(me.browser, waiting["projectId"])["status"] == "posting"
    assert_error(me.browser.post(f"/autopilot/topics/{waiting['id']}/approve"), 409, "not_waiting")
    finish(app)
    assert len(MockYouTube.uploads) == 1
    assert_error(me.browser.post(f"/autopilot/topics/{waiting['id']}/approve"), 409, "not_waiting")
    assert_error(me.browser.post(f"/autopilot/topics/{waiting['id']}/skip"), 409, "not_waiting")


@pytest.mark.parametrize("visibility", ["private", "unlisted", "public"])
def test_l6_publishes_the_saved_title_caption_and_hashtags_with_the_chosen_visibility(app, visibility):
    me = creator(app)
    connect(me.browser)
    waiting = waiting_run(app, me.browser)
    # The creator rewrites the post's text on the Reel before publishing, and picks who sees it.
    res = me.browser.patch(
        f"/projects/{waiting['projectId']}",
        {
            "postTitle": "Pelé: three World Cups",
            "postCaption": "The only one.\nEver.",
            "hashtags": ["pele", "football"],
        },
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    assert topic_of(me.browser, waiting["id"])["title"] == "Pelé: three World Cups"
    me.browser.patch("/autopilot", {"visibility": visibility})
    assert me.browser.post(f"/autopilot/topics/{waiting['id']}/approve").status_code == 202
    finish(app)
    [upload] = MockYouTube.uploads
    assert upload["metadata"]["snippet"]["title"] == "Pelé: three World Cups"
    assert upload["metadata"]["snippet"]["description"].startswith("The only one.\nEver.\n\n#pele #football")
    assert upload["metadata"]["snippet"]["tags"] == ["pele", "football"]
    assert upload["metadata"]["status"]["privacyStatus"] == visibility
    done = topic_of(me.browser, waiting["id"])
    assert done["status"] == "published" and done["title"] == "Pelé: three World Cups"
    post = get_project(me.browser, waiting["projectId"])["latestPost"]
    assert post["visibility"] == visibility and post["url"] == done["url"]


def test_l6_strips_angle_brackets_youtube_refuses(app, database):
    """A script's own caption and hashtags may hold < or > (only the caption job removes them)."""
    me = creator(app)
    connect(me.browser)
    waiting = waiting_run(app, me.browser)
    database.run(
        "UPDATE projects SET post_title = :t, post_caption = :c, hashtags = :h WHERE id = :p",
        t="Pelé <the king> of 1970",
        c="Goals > words. <3 football",
        h=["pele", "<b>", "goat>", "football"],
        p=waiting["projectId"],
    )
    assert me.browser.post(f"/autopilot/topics/{waiting['id']}/approve").status_code == 202
    finish(app)
    snippet = MockYouTube.uploads[0]["metadata"]["snippet"]
    assert snippet["title"] == "Pelé the king of 1970"
    assert snippet["description"].startswith("Goals  words. 3 football\n\n#pele #football")
    assert snippet["tags"] == ["pele", "football"]
    assert "<" not in str(snippet) and ">" not in str(snippet)
    assert topic_of(me.browser, waiting["id"])["status"] == "published"


def test_l6_does_not_publish_a_video_made_before_the_creators_last_edit(app, database):
    """The waiting card says "Watch it and check the facts" and links to "Edit the Reel". After
    a fact is corrected in the script, Publish must not put the old video on the channel: the
    Reel is made again and waits once more."""
    me = creator(app)
    connect(me.browser)
    waiting = waiting_run(app, me.browser)
    project = get_project(me.browser, waiting["projectId"])
    old_video = waiting["video"]["url"].split("?")[0]
    res = me.browser.patch(
        f"/scenes/{project['scenes'][0]['id']}",
        {"narration": "Corrected: Pelé won three World Cups, not four."},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    edited = get_project(me.browser, waiting["projectId"])
    assert edited["latestRender"]["outdated"] is True and edited["status"] == "draft"

    error = assert_error(
        me.browser.post(f"/autopilot/topics/{waiting['id']}/approve"), 409, "render_outdated"
    )
    assert "edited" in error["message"]
    assert MockYouTube.uploads == [] and jobs.manual_queue == []
    again = topic_of(me.browser, waiting["id"])
    assert again["status"] == "running" and again["video"] is None
    finish(app)
    assert MockYouTube.uploads == [], "it must wait for the creator again, not publish by itself"
    ready = topic_of(me.browser, waiting["id"])
    assert ready["status"] == "awaiting_approval" and ready["video"]["url"].split("?")[0] != old_video
    made = get_project(me.browser, waiting["projectId"])
    assert made["status"] == "rendered" and made["latestRender"]["outdated"] is False
    assert database.value("SELECT count(*) FROM renders WHERE project_id = :p", p=made["id"]) == 2
    assert me.browser.post(f"/autopilot/topics/{waiting['id']}/approve").status_code == 202
    finish(app)
    assert len(MockYouTube.uploads) == 1 and topic_of(me.browser, waiting["id"])["status"] == "published"


def test_l6_after_an_edit_that_keeps_every_scene_ready_renders_again_too(app):
    me = creator(app)
    connect(me.browser)
    waiting = waiting_run(app, me.browser)
    project = get_project(me.browser, waiting["projectId"])
    assert (
        me.browser.patch(f"/scenes/{project['scenes'][0]['id']}", {"onScreenText": "1958"}).status_code == 200
    )
    assert get_project(me.browser, project["id"])["status"] == "ready"
    assert_error(me.browser.post(f"/autopilot/topics/{waiting['id']}/approve"), 409, "render_outdated")
    finish(app)
    assert MockYouTube.uploads == []
    assert topic_of(me.browser, waiting["id"])["status"] == "awaiting_approval"
    assert get_project(me.browser, project["id"])["latestRender"]["outdated"] is False


def test_l6_changing_only_the_post_text_is_not_an_edit_of_the_video(app):
    me = creator(app)
    connect(me.browser)
    waiting = waiting_run(app, me.browser)
    assert (
        me.browser.patch(f"/projects/{waiting['projectId']}", {"postTitle": "A better title"}).status_code
        == 200
    )
    assert me.browser.post(f"/autopilot/topics/{waiting['id']}/approve").status_code == 202


def test_l6_of_a_reel_the_creator_already_published_by_hand_marks_it_published(app):
    me = creator(app)
    connect(me.browser)
    waiting = waiting_run(app, me.browser)
    render_id = get_project(me.browser, waiting["projectId"])["latestRender"]["id"]
    assert me.browser.post(f"/renders/{render_id}/publish", PUBLISH_BODY).status_code == 202
    run_jobs(app)
    assert get_project(me.browser, waiting["projectId"])["status"] == "posted"
    res = me.browser.post(f"/autopilot/topics/{waiting['id']}/approve")
    assert res.status_code == 200, res.get_data(as_text=True)
    [done] = res.get_json()["runs"]
    assert done["status"] == "published" and done["url"].startswith("https://youtube.com/shorts/")
    assert len(MockYouTube.uploads) == 1  # no second copy


@pytest.mark.parametrize(
    "hashtags", [["two words"], ["with#hash"], ["x" * 101]], ids=["a space", "a # inside", "too long"]
)
def test_l6_post_text_youtube_would_refuse_is_a_clear_refusal_not_a_crash(app, database, hashtags):
    """A script's own hashtags are not held to the publish rules; the creator is told and can
    fix them on the Reel."""
    me = creator(app)
    connect(me.browser)
    waiting = waiting_run(app, me.browser)
    database.run("UPDATE projects SET hashtags = :h WHERE id = :p", h=hashtags, p=waiting["projectId"])
    error = assert_error(
        me.browser.post(f"/autopilot/topics/{waiting['id']}/approve"), 409, "post_text_invalid"
    )
    assert error["message"]
    assert topic_of(me.browser, waiting["id"])["status"] == "awaiting_approval" and MockYouTube.uploads == []
    assert me.browser.patch(f"/projects/{waiting['projectId']}", {"hashtags": ["pele"]}).status_code == 200
    assert me.browser.post(f"/autopilot/topics/{waiting['id']}/approve").status_code == 202


def test_automatic_mode_with_post_text_youtube_would_refuse_waits_with_the_reason(app, database):
    me = creator(app)
    connect(me.browser)
    me.browser.patch("/autopilot", {"mode": "auto"})
    add(me.browser)
    run = run_now(me.browser)
    drive_to(app, me.browser, run["projectId"], "rendered")
    tick(app)
    run_jobs(app)  # the caption job
    database.run("UPDATE projects SET hashtags = :h WHERE id = :p", h=["with#hash"], p=run["projectId"])
    finish(app)
    waiting = topic_of(me.browser, run["id"])
    assert waiting["status"] == "awaiting_approval", waiting
    assert waiting["error"] == "The title or description can't be published as it is."
    assert MockYouTube.uploads == [] and tick(app) == 0


def test_a_title_of_nothing_but_angle_brackets_falls_back_to_the_legends_name(app, database):
    me = creator(app)
    connect(me.browser)
    waiting = waiting_run(app, me.browser)
    database.run("UPDATE projects SET post_title = '<<>>' WHERE id = :p", p=waiting["projectId"])
    assert me.browser.post(f"/autopilot/topics/{waiting['id']}/approve").status_code == 202
    finish(app)
    assert MockYouTube.uploads[0]["metadata"]["snippet"]["title"] == "Pelé"


def test_l6_of_a_deleted_reel_is_not_found(app):
    me = creator(app)
    connect(me.browser)
    waiting = waiting_run(app, me.browser)
    assert me.browser.delete(f"/projects/{waiting['projectId']}").status_code == 204
    assert_error(me.browser.post(f"/autopilot/topics/{waiting['id']}/approve"), 404, "not_found")
    assert MockYouTube.uploads == []
    # It can still be skipped or removed from the list.
    assert me.browser.post(f"/autopilot/topics/{waiting['id']}/skip").status_code == 200


# ---------------------------------------------------------------- L7: skip


@pytest.mark.parametrize("status", [s for s in STATUSES if s != "awaiting_approval"])
def test_l7_only_a_waiting_reel_can_be_skipped(app, database, status):
    me = creator(app)
    topic_id = insert_topic(database, me.id, status)
    assert_error(me.browser.post(f"/autopilot/topics/{topic_id}/skip"), 409, "not_waiting")
    assert database.value("SELECT status FROM autopilot_topics WHERE id = :t", t=topic_id) == status


def test_l7_skip_keeps_the_reel_clears_the_reason_and_publishes_nothing(app):
    me = creator(app)
    me.browser.patch("/autopilot", {"mode": "auto"})
    add(me.browser)
    run = run_now(me.browser)
    finish(app)
    assert topic_of(me.browser, run["id"])["error"]  # not connected (AP-3)
    res = me.browser.post(f"/autopilot/topics/{run['id']}/skip")
    assert res.status_code == 200 and res.get_json() == state(me.browser)
    skipped = topic_of(me.browser, run["id"])
    assert skipped["status"] == "skipped" and skipped["error"] is None
    assert skipped["title"] and skipped["url"] is None
    project = get_project(me.browser, run["projectId"])
    assert project["status"] == "rendered" and project["latestRender"]["state"] == "succeeded"
    assert tick(app) == 0 and MockYouTube.uploads == []
    assert me.browser.delete(f"/autopilot/topics/{run['id']}").status_code == 204
    assert get_project(me.browser, run["projectId"])["status"] == "rendered"  # the Reel stays in the library


# ---------------------------------------------------------------- the database (migration 0003)


def test_the_tables_refuse_values_the_api_never_writes(app, database):
    me = creator(app)
    state(me.browser)  # creates the settings row
    for column, value in (
        ("mode", "'manual'"),
        ("run_hour_utc", "24"),
        ("target_seconds", "45"),
        ("visibility", "'x'"),
    ):
        with pytest.raises(IntegrityError):
            database.run(f"UPDATE autopilot_settings SET {column} = {value} WHERE user_id = :u", u=me.id)  # noqa: S608
    for over in (
        {"status": "done"},
        {"subject": ""},
        {"subject": "x" * 81},
        {"angle": ""},
        {"angle": "x" * 161},
    ):
        with pytest.raises(IntegrityError):
            insert_topic(database, me.id, over.pop("status", "queued"), **over)


def test_an_accounts_autopilot_goes_with_the_account_and_a_purged_reel_leaves_the_run(app, database):
    me = creator(app)
    connect(me.browser)
    waiting = waiting_run(app, me.browser)
    add(me.browser, SENNA)
    database.run("DELETE FROM projects WHERE id = :p", p=waiting["projectId"])
    row = database.one("SELECT status, project_id FROM autopilot_topics WHERE id = :t", t=waiting["id"])
    assert row == {"status": "awaiting_approval", "project_id": None}
    listed = topic_of(me.browser, waiting["id"])
    assert listed["projectId"] is None and listed["video"] is None
    assert_error(me.browser.post(f"/autopilot/topics/{waiting['id']}/approve"), 409, "not_waiting")
    database.run("DELETE FROM users WHERE id = :u", u=me.id)
    assert database.value("SELECT count(*) FROM autopilot_topics") == 0
    assert database.value("SELECT count(*) FROM autopilot_settings") == 0


def test_migration_0003_can_be_undone_and_reapplied(app, database):
    from flask_migrate import downgrade, upgrade

    from reelforge_api.extensions import db

    me = creator(app)
    add(me.browser)
    suggested = suggest(app, me.browser)
    queued = me.browser.post("/autopilot/topics/suggest").get_json()["job"]  # one still waiting
    jobs.manual_queue.clear()
    run = run_now(me.browser)
    run_jobs(app)

    def exists(table: str) -> bool:
        return database.value("SELECT to_regclass(:t) IS NOT NULL", t=f"public.{table}")

    def new_job(job_type: str) -> int:
        return database.run(
            "INSERT INTO jobs (user_id, type, credits_reserved, input) VALUES (:u, :t, 0, '{}'::jsonb)",
            u=me.id,
            t=job_type,
        )

    with app.app_context():
        try:
            downgrade(revision="0002")
            db.session.rollback()
            assert not exists("autopilot_topics") and not exists("autopilot_settings")
            assert database.value("SELECT version_num FROM alembic_version") == "0002"
            # The suggestion jobs go (the older constraint has no such type); every other job stays.
            ids = {str(r["id"]) for r in database.rows("SELECT id FROM jobs")}
            assert suggested["id"] not in ids and queued["id"] not in ids
            assert database.value("SELECT count(*) FROM jobs WHERE type = 'script'") == 1
            assert database.value("SELECT status FROM projects WHERE id = :p", p=run["projectId"]) == "draft"
            with pytest.raises(IntegrityError, match="jobs_type_check"):
                new_job("autopilot_topics")
            assert new_job("ideas") == 1
        finally:
            db.session.rollback()
            upgrade()
            db.session.rollback()
    assert exists("autopilot_topics") and exists("autopilot_settings")
    assert database.value("SELECT version_num FROM alembic_version") == "0003"
    assert new_job("autopilot_topics") == 1
    for known in (
        "ideas",
        "script",
        "rewrite_scene",
        "hooks",
        "caption",
        "voice",
        "visual",
        "render",
        "publish",
    ):
        assert new_job(known) == 1
    with pytest.raises(IntegrityError, match="jobs_type_check"):
        new_job("something_else")
    # The account and its Reel are untouched; the autopilot starts from its defaults again.
    assert get_project(me.browser, run["projectId"])["status"] == "draft"
    found = state(me.browser)
    assert found["queue"] == [] and found["runs"] == [] and found["settings"]["enabled"] is False
