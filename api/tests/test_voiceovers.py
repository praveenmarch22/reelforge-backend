"""C7 a scene's voiceover and the voice job (milestone B4 slice 1): docs/API_DESIGN.md C7, D3,
Scene and Job objects, Idempotency; PRD V2, V3, CA-4, G-12, G-15; TECHNICAL_DESIGN.md "Voice".

Runs with the offline mock voice (test settings). Narration containing "[voice-fail]" makes
it fail; "[no-timings]" makes it report estimated word timings.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from reelforge_api import jobs
from reelforge_api.generation import voice_input_hash
from reelforge_api.providers.voice import MockVoice
from reelforge_api.script_rules import COSTS
from reelforge_api.tasks.jobs import fail_stuck_jobs

from .conftest import build_app
from .projects_support import (
    ISO_UTC,
    JOB_FIELDS,
    SCENE_FIELDS,
    SCRIPT_COST,
    assert_ledger_matches_balance,
    balance,
    creator,
    get_job,
    get_project,
    job_ledger,
    run_jobs,
    scene_ids,
    scripted_project,
    set_balance,
    unsigned,
    waiting_jobs,
)
from .support import Browser, assert_error, assert_invalid, log_in
from .voice_support import (  # noqa: F401 - no_real_voice_services is an autouse fixture
    DEFAULT_VOICE,
    VOICE_FIELDS,
    VOICE_IDS,
    WORD_FIELDS,
    download,
    no_real_voice_services,
    regenerate,
    scene_of,
    started,
    stored_voice_files,
    words_of,
)

# Every scene in these tests speaks for 15 s or less unless it says otherwise: 1 credit (C7).
VOICE_COST = COSTS["voiceSceneShort"]
AFTER_SCRIPT = 100 - SCRIPT_COST


def voice_project(app, me, narration: str | None = None) -> tuple[dict, str]:
    """A scripted project and its first scene, optionally with this narration."""
    project = scripted_project(app, me.browser)
    scene = project["scenes"][0]["id"]
    if narration is not None:
        res = me.browser.patch(f"/scenes/{scene}", {"narration": narration})
        assert res.status_code == 200, res.get_data(as_text=True)
    return project, scene


def asset_rows(database, project_id: str) -> list[dict]:
    return database.rows(
        "SELECT id::text, kind, s3_key, content_type, size_bytes, duration_seconds, provider, input_hash, "
        "meta, replaced_at FROM assets WHERE project_id = :p ORDER BY created_at",
        p=project_id,
    )


def during_work(monkeypatch, action) -> list:
    """Runs `action()` while the voice is being made (the worker holds no lock then)."""
    results: list = []
    real = MockVoice.synthesize

    def slow(self, text, voice, mood):
        results.append(action())
        return real(self, text, voice, mood)

    monkeypatch.setattr(MockVoice, "synthesize", slow)
    return results


def on_another_thread(fn):
    """Sends a request on its own thread and connection, as the web app would while the
    worker is busy."""
    out: list = []
    thread = threading.Thread(target=lambda: out.append(fn()))
    thread.start()
    thread.join(timeout=30)
    assert out, "the request did not finish"
    return out[0]


# ---------------------------------------------------------------- C7: the main flow


def test_a_new_voice_goes_queued_running_ready_with_its_file_words_and_link(
    app, database, outbox, monkeypatch
):
    me = creator(app)
    narration = "Wake up early and drink a full glass of water first."
    project, scene_id = voice_project(app, me, narration)

    res = regenerate(me.browser, scene_id)
    assert res.status_code == 202, res.get_data(as_text=True)
    assert set(res.get_json()) == {"job"}
    job = res.get_json()["job"]
    assert set(job) == JOB_FIELDS
    assert job["type"] == "voice" and job["state"] == "queued"
    assert job["sceneId"] == scene_id and job["projectId"] == project["id"]
    assert job["creditsReserved"] == VOICE_COST and job["creditsRefunded"] is False
    assert job["error"] is None and job["output"] is None and ISO_UTC.match(job["queuedAt"])

    queued = get_project(me.browser, project["id"])
    assert queued["scenes"][0]["voiceStatus"] == "queued"
    assert [j["id"] for j in queued["activeJobs"]] == [job["id"]]
    assert balance(me.browser) == AFTER_SCRIPT - VOICE_COST

    seen = during_work(
        monkeypatch, lambda: database.value("SELECT voice_status FROM scenes WHERE id = :s", s=scene_id)
    )
    assert run_jobs(app) == 1
    assert seen == ["running"], "the scene shows running while its voice is being made"

    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["progress"] == 100 and done["provider"] == "mock"
    assert ISO_UTC.match(done["finishedAt"]) and done["creditsRefunded"] is False
    scene = scene_of(me.browser, project["id"], scene_id)
    assert set(scene) == SCENE_FIELDS
    assert scene["voiceStatus"] == "ready" and scene["lastError"] is None and scene["wordsEdited"] is False
    assert set(scene["voice"]) == VOICE_FIELDS
    assert scene["voice"]["provider"] == "mock" and scene["voice"]["voiceId"] == DEFAULT_VOICE.id
    assert scene["voice"]["estimatedTimings"] is False
    assert done["output"] == {
        "voice": {"assetId": scene["voice"]["assetId"], "durationSeconds": scene["voice"]["durationSeconds"]},
        "estimatedTimings": False,
    }
    # The mock speaks 2.5 words a second.
    assert scene["voice"]["durationSeconds"] == pytest.approx(len(narration.split()) / 2.5, abs=0.01)

    words = scene["words"]
    assert [w["text"] for w in words] == narration.split()
    for w in words:
        assert set(w) == WORD_FIELDS
        assert 0 <= w["start"] <= w["end"] <= scene["voice"]["durationSeconds"] + 0.01
    assert all(a["end"] <= b["start"] + 0.001 for a, b in zip(words, words[1:], strict=False)), "in order"

    audio = download(scene["voice"]["url"])
    assert audio[:4] == b"RIFF", "the link serves the stored audio"

    (asset,) = asset_rows(database, project["id"])
    assert asset["id"] == scene["voice"]["assetId"]
    assert asset["kind"] == "voice" and asset["provider"] == "mock" and asset["content_type"] == "audio/wav"
    assert asset["s3_key"] == f"users/{me.id}/projects/{project['id']}/voice/{job['id']}.wav"
    assert asset["size_bytes"] == len(audio)
    assert asset["input_hash"] == voice_input_hash(narration, scene["mood"], DEFAULT_VOICE.id)
    assert asset["meta"]["voiceId"] == DEFAULT_VOICE.id and asset["meta"]["estimatedTimings"] is False

    assert balance(me.browser) == AFTER_SCRIPT - VOICE_COST, "a voice that worked is not refunded"
    assert job_ledger(database, job["id"]) == [("reserve", -VOICE_COST)]
    assert database.value("SELECT note FROM credit_ledger WHERE job_id = :j", j=job["id"]) == "Voiceover"
    assert get_project(me.browser, project["id"])["activeJobs"] == []
    assert_ledger_matches_balance(database, me.id)


def test_another_take_replaces_the_voice_and_releases_the_old_file(app, database, outbox):
    """C7 always makes a new take, even if nothing changed; the old file is left for the clean-up."""
    me = creator(app)
    project, scene_id = voice_project(app, me)
    started(me.browser, scene_id)
    run_jobs(app)
    first = scene_of(me.browser, project["id"], scene_id)["voice"]["assetId"]

    started(me.browser, scene_id)
    assert scene_of(me.browser, project["id"], scene_id)["voice"]["assetId"] == first, "kept until replaced"
    run_jobs(app)
    second = scene_of(me.browser, project["id"], scene_id)
    assert second["voiceStatus"] == "ready" and second["voice"]["assetId"] != first
    replaced = {a["id"]: a["replaced_at"] for a in asset_rows(database, project["id"])}
    assert replaced[first] is not None and replaced[second["voice"]["assetId"]] is None
    assert balance(me.browser) == AFTER_SCRIPT - 2 * VOICE_COST


@pytest.mark.parametrize(
    ("words", "credits"),
    [(1, 1), (37, 1), (38, 2), (74, 2), (75, 2), (76, 4), (150, 4), (151, 6)],
)
def test_a_scene_voice_costs_1_credit_up_to_15_seconds_else_2_per_started_30(
    app, database, outbox, words, credits
):
    """Product decision 2026-11-25. 2.5 words a second: 37 words are 14.8 s, 38 are 15.2 s,
    75 are exactly 30 s."""
    me = creator(app)
    _, scene_id = voice_project(app, me, words_of(words))
    job = started(me.browser, scene_id)
    assert job["creditsReserved"] == credits
    assert balance(me.browser) == AFTER_SCRIPT - credits
    assert job_ledger(database, job["id"]) == [("reserve", -credits)]
    run_jobs(app)


def test_the_credit_costs_list_the_short_scene_voice_price(app, outbox):
    """GET /credits lists every cost the web app shows; C7 reads from the same table."""
    me = creator(app)
    costs = me.browser.get("/credits").get_json()["costs"]
    assert costs["voiceSceneShort"] == 1 and costs["voicePer30s"] == 2
    assert costs == COSTS


def test_a_failed_voice_is_refunded_and_the_scene_shows_why(app, database, outbox):
    me = creator(app)
    project, scene_id = voice_project(app, me, "This one will not work [voice-fail]")
    job = started(me.browser, scene_id)
    run_jobs(app)

    failed = get_job(me.browser, job["id"])
    assert failed["state"] == "failed" and failed["creditsRefunded"] is True
    assert failed["error"]["code"] == "provider_failed" and failed["error"]["message"]
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == "failed" and scene["lastError"] == failed["error"]["message"]
    assert scene["voice"] is None and scene["words"] == []
    assert balance(me.browser) == AFTER_SCRIPT
    assert job_ledger(database, job["id"]) == [("reserve", -VOICE_COST), ("refund", VOICE_COST)]
    assert asset_rows(database, project["id"]) == []
    assert stored_voice_files(app, me.id, project["id"]) == []

    # Fixing the text and trying again works and clears the error.
    me.browser.patch(f"/scenes/{scene_id}", {"narration": "This one will work."})
    started(me.browser, scene_id)
    run_jobs(app)
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == "ready" and scene["lastError"] is None
    assert_ledger_matches_balance(database, me.id)


@pytest.mark.parametrize("before", ["missing", "ready"])
def test_cancelling_a_queued_voice_refunds_it_and_puts_the_status_back(app, database, outbox, before):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    if before == "ready":
        started(me.browser, scene_id)
        run_jobs(app)
    earlier = scene_of(me.browser, project["id"], scene_id)
    assert earlier["voiceStatus"] == before
    paid = balance(me.browser)

    job = started(me.browser, scene_id)
    res = me.browser.post(f"/jobs/{job['id']}/cancel")
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["job"]["state"] == "cancelled" and res.get_json()["job"]["creditsRefunded"] is True
    assert unsigned(scene_of(me.browser, project["id"], scene_id)) == unsigned(earlier)
    assert balance(me.browser) == paid

    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "cancelled"
    assert unsigned(scene_of(me.browser, project["id"], scene_id)) == unsigned(
        earlier
    ), "the worker skips a cancelled job"
    assert len(asset_rows(database, project["id"])) == (1 if before == "ready" else 0)
    assert_ledger_matches_balance(database, me.id)


def test_a_voice_cancelled_while_it_is_being_made_is_dropped_and_its_file_deleted(
    app, database, outbox, monkeypatch
):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    job = started(me.browser, scene_id)
    statuses = during_work(
        monkeypatch,
        lambda: on_another_thread(lambda: me.browser.post(f"/jobs/{job['id']}/cancel").status_code),
    )
    run_jobs(app)
    assert statuses == [200]
    final = get_job(me.browser, job["id"])
    assert final["state"] == "cancelled" and final["creditsRefunded"] is True and final["output"] is None
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == "missing" and scene["voice"] is None
    assert asset_rows(database, project["id"]) == []
    assert stored_voice_files(app, me.id, project["id"]) == [], "the made file is deleted"
    assert balance(me.browser) == AFTER_SCRIPT
    assert_ledger_matches_balance(database, me.id)


def test_estimated_word_timings_are_reported_on_the_job_and_the_file(app, database, outbox):
    """G-15 (until Whisper arrives in B7): audio without usable timings gets even timings,
    flagged estimatedTimings."""
    me = creator(app)
    narration = "Captions will be roughly in sync [no-timings]"
    project, scene_id = voice_project(app, me, narration)
    job = started(me.browser, scene_id)
    run_jobs(app)
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["output"]["estimatedTimings"] is True
    (asset,) = asset_rows(database, project["id"])
    assert asset["meta"]["estimatedTimings"] is True
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["voice"]["estimatedTimings"] is True, "the editor can warn that captions are approximate"
    assert [w["text"] for w in scene["words"]] == narration.split()
    assert scene["words"][-1]["end"] == pytest.approx(scene["voice"]["durationSeconds"], abs=0.01)


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "cancelled"])
def test_a_ready_project_stays_ready_during_a_new_take_and_after_unless_it_fails(
    app, database, outbox, outcome
):
    """Fix log QB4-003 / BUG-038: the old voice still plays while a new take is made, so the
    project stays ready; it goes back to draft only if the take fails (DB design 5)."""
    me = creator(app)
    project, scene_id = voice_project(app, me)
    for sid in scene_ids(project):
        started(me.browser, sid)
    run_jobs(app)
    database.run("UPDATE scenes SET visual_status = 'ready' WHERE project_id = :p", p=project["id"])
    database.run("UPDATE projects SET status = 'ready' WHERE id = :p", p=project["id"])

    job = started(me.browser, scene_id)
    assert get_project(me.browser, project["id"])["status"] == "ready", "while the new take is queued"
    if outcome == "failed":
        switch_off(database, "mock")  # the job then fails with provider_unavailable
    elif outcome == "cancelled":
        assert me.browser.post(f"/jobs/{job['id']}/cancel").status_code == 200
    run_jobs(app)
    after = get_project(me.browser, project["id"])
    voice_status = scene_of(me.browser, project["id"], scene_id)["voiceStatus"]
    if outcome == "failed":
        assert voice_status == "failed"
        assert after["status"] == "draft", f"a scene's voice failed but the project is {after['status']}"
    else:
        assert voice_status == "ready" and after["status"] == "ready"


def make_ready(app, database, me) -> tuple[dict, list[str]]:
    """A project whose scenes all have a voice and a visual, status ready."""
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    for sid in ids:
        started(me.browser, sid)
    run_jobs(app)
    database.run("UPDATE scenes SET visual_status = 'ready' WHERE project_id = :p", p=project["id"])
    database.run("UPDATE projects SET status = 'ready' WHERE id = :p", p=project["id"])
    return project, ids


def test_editing_another_scene_during_a_new_take_keeps_the_project_ready(app, database, outbox):
    """QA's QB4-014 repro: ready project, C7 on scene 1, edit scene 2's on-screen text, the
    take succeeds: the project is ready all along (the scene's earlier voice still plays)."""
    me = creator(app)
    project, ids = make_ready(app, database, me)
    started(me.browser, ids[0])
    res = me.browser.patch(f"/scenes/{ids[1]}", {"onScreenText": "New title"})
    assert res.status_code == 200
    assert get_project(me.browser, project["id"])["status"] == "ready"
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "ready"


def test_a_failed_take_then_a_successful_retry_brings_the_project_back_to_ready(app, database, outbox):
    me = creator(app)
    project, ids = make_ready(app, database, me)
    started(me.browser, ids[0])
    switch_off(database, "mock")
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "draft"
    database.run("UPDATE provider_switches SET enabled = true WHERE provider = 'mock'")
    started(me.browser, ids[0])
    run_jobs(app)
    assert scene_of(me.browser, project["id"], ids[0])["voiceStatus"] == "ready"
    assert get_project(me.browser, project["id"])["status"] == "ready"


def test_a_draft_that_gets_its_last_voice_becomes_ready(app, database, outbox):
    """A draft counts as generated once any scene has a file."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    database.run("UPDATE scenes SET visual_status = 'ready' WHERE project_id = :p", p=project["id"])
    for sid in ids[:-1]:
        started(me.browser, sid)
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "draft", "one voice still missing"
    started(me.browser, ids[-1])
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "ready"


def test_a_scene_getting_its_first_voice_does_not_count_as_ready(app, database, outbox):
    """Only a scene whose earlier voice was ready counts as ready while it is redone."""
    me = creator(app)
    project, ids = make_ready(app, database, me)
    copy = me.browser.post(f"/scenes/{ids[0]}/duplicate").get_json()["scene"]
    database.run(
        "UPDATE scenes SET voice_asset_id = NULL, voice_status = 'missing' WHERE id = :s", s=copy["id"]
    )
    me.browser.patch(f"/scenes/{ids[1]}", {"onScreenText": "x"})
    assert get_project(me.browser, project["id"])["status"] == "draft"
    started(me.browser, copy["id"])
    me.browser.patch(f"/scenes/{ids[1]}", {"onScreenText": "y"})
    assert get_project(me.browser, project["id"])["status"] == "draft", "the copy has no voice yet"
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "ready"


def test_a_stuck_voice_job_fails_and_is_refunded_after_8_minutes(app, database, outbox):
    """Technical design 7: a voice job may run 7 minutes (Celery hard limit) and counts as
    stuck one minute after that."""
    me = creator(app)
    project, scene_id = voice_project(app, me)
    job = started(me.browser, scene_id)
    jobs.manual_queue.clear()
    database.run("UPDATE scenes SET voice_status = 'running' WHERE id = :s", s=scene_id)

    def running_for(minutes: float) -> None:
        moment = datetime.now(UTC) - timedelta(minutes=minutes)
        database.run(
            "UPDATE jobs SET state = 'running', started_at = :m WHERE id = :j", m=moment, j=job["id"]
        )

    running_for(7.5)
    with app.app_context():
        assert fail_stuck_jobs.run() == 0, "still inside its limit and grace minute"
    assert get_job(me.browser, job["id"])["state"] == "running"

    running_for(8.5)
    with app.app_context():
        assert fail_stuck_jobs.run() == 1
    failed = get_job(me.browser, job["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "timed_out"
    assert failed["creditsRefunded"] is True
    assert scene_of(me.browser, project["id"], scene_id)["voiceStatus"] == "failed"
    assert balance(me.browser) == AFTER_SCRIPT


def test_voice_jobs_are_sent_to_the_voice_queue_with_their_own_time_limits(app, outbox, monkeypatch):
    """Technical design 7: soft 6 minutes, hard 7 (enough for three ElevenLabs tries and three
    Edge tries with waits). Nothing reaches the worker: apply_async is replaced."""
    celery_app = build_app(job_dispatch="celery")
    sent: list[dict] = []

    class Sent:
        id = "task-1"

    def record(task, args=None, kwargs=None, **options):
        sent.append({"name": task.name, "args": tuple(args or ()), **options})
        return Sent()

    monkeypatch.setattr("celery.app.task.Task.apply_async", record)
    me = creator(celery_app)
    res = me.browser.post("/projects", {"topic": "habits"})
    assert res.status_code == 202, res.get_data(as_text=True)
    with celery_app.app_context():
        jobs.run(res.get_json()["job"]["id"])  # the script, run here instead of on the worker
    scene_id = get_project(me.browser, res.get_json()["project"]["id"])["scenes"][0]["id"]
    job = started(me.browser, scene_id)
    (voice,) = [s for s in sent if s["args"] == (job["id"],)]
    assert voice["queue"] == "voice"
    assert (voice["soft_time_limit"], voice["time_limit"]) == (360, 420)


# ---------------------------------------------------------------- CA-4: hand-fixed caption words


def test_a_scene_with_hand_fixed_words_needs_confirmation(app, database, outbox):
    me = creator(app)
    project, scene_id = voice_project(app, me, "Drink water every morning")
    started(me.browser, scene_id)
    run_jobs(app)
    words = scene_of(me.browser, project["id"], scene_id)["words"]
    fixed = [{"text": w["text"]} for w in words]
    fixed[0]["text"] = "DRINK"
    res = me.browser.patch(f"/scenes/{scene_id}", {"words": fixed})
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["scene"]["wordsEdited"] is True
    paid = balance(me.browser)

    for body in (None, {}, {"confirmReplaceWords": False}, {"voiceId": VOICE_IDS[1]}):
        error = assert_error(regenerate(me.browser, scene_id, body), 409, "words_edited")
        assert "caption" in error["message"].lower()
    assert balance(me.browser) == paid and waiting_jobs() == []
    assert scene_of(me.browser, project["id"], scene_id)["words"][0]["text"] == "DRINK"

    job = started(me.browser, scene_id, {"confirmReplaceWords": True})
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "succeeded"
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["wordsEdited"] is False
    assert [w["text"] for w in scene["words"]] == ["Drink", "water", "every", "morning"]


def test_confirming_is_harmless_when_no_words_were_fixed(app, outbox):
    me = creator(app)
    _, scene_id = voice_project(app, me)
    started(me.browser, scene_id, {"confirmReplaceWords": True})
    run_jobs(app)


# ---------------------------------------------------------------- which voice is used


def voice_used(database, project_id: str) -> str:
    rows = asset_rows(database, project_id)
    return rows[-1]["meta"]["voiceId"]


def set_default_voice(browser, voice_id: str) -> None:
    res = browser.patch("/me", {"defaults": {"voiceId": voice_id}})
    assert res.status_code == 200, res.get_data(as_text=True)


def test_the_voice_comes_from_the_request_then_the_project_then_the_account_then_the_default(
    app, database, outbox
):
    account_voice, project_voice, scene_voice = VOICE_IDS[3], VOICE_IDS[4], VOICE_IDS[5]
    me = creator(app)
    set_default_voice(me.browser, account_voice)
    project, scene_id = voice_project(app, me)
    assert project["voiceId"] == account_voice

    def make(body=None) -> str:
        started(me.browser, scene_id, body)
        run_jobs(app)
        return voice_used(database, project["id"])

    assert make() == account_voice, "the project took the account default when it was created"
    me.browser.patch(f"/projects/{project['id']}", {"voiceId": project_voice})
    assert make() == project_voice
    assert make({"voiceId": scene_voice}) == scene_voice
    assert get_project(me.browser, project["id"])["voiceId"] == project_voice, "the project keeps its voice"

    res = me.browser.patch(f"/projects/{project['id']}", {"voiceId": ""})
    assert res.status_code == 200 and res.get_json()["project"]["voiceId"] == ""
    assert make() == account_voice
    set_default_voice(me.browser, "")
    assert make() == DEFAULT_VOICE.id
    assert make({"voiceId": ""}) == DEFAULT_VOICE.id


def test_a_stored_voice_that_left_the_catalogue_falls_back_instead_of_failing(app, database, outbox):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    database.run("UPDATE projects SET voice_id = 'retired-voice' WHERE id = :p", p=project["id"])
    database.run("UPDATE users SET default_voice_id = :v WHERE id = :u", v=VOICE_IDS[2], u=me.id)
    job = started(me.browser, scene_id)
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "succeeded"
    assert voice_used(database, project["id"]) == VOICE_IDS[2]

    database.run("UPDATE users SET default_voice_id = 'also-retired' WHERE id = :u", u=me.id)
    started(me.browser, scene_id)
    run_jobs(app)
    assert voice_used(database, project["id"]) == DEFAULT_VOICE.id


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"voiceId": "voice-7"}, "voiceId"),
        ({"voiceId": DEFAULT_VOICE.id.lower()}, "voiceId"),
        ({"voiceId": "x" * 101}, "voiceId"),
        ({"voiceId": 7}, "voiceId"),
        ({"confirmReplaceWords": "yes"}, "confirmReplaceWords"),
        ({"voice_id": DEFAULT_VOICE.id}, "voice_id"),
        ({"narration": "new text"}, "narration"),
    ],
)
def test_invalid_bodies_are_refused_and_charge_nothing(app, outbox, body, field):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    assert_invalid(regenerate(me.browser, scene_id, body), field)
    assert balance(me.browser) == AFTER_SCRIPT and waiting_jobs() == []
    assert scene_of(me.browser, project["id"], scene_id)["voiceStatus"] == "missing"


def test_every_catalogue_voice_is_accepted(app, database, outbox):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    for voice_id in VOICE_IDS:
        started(me.browser, scene_id, {"voiceId": voice_id})
        run_jobs(app)
        assert voice_used(database, project["id"]) == voice_id


# ---------------------------------------------------------------- who may, and when


def test_an_unverified_creator_cannot_make_a_voice(app, database, outbox):
    me = creator(app)
    _, scene_id = voice_project(app, me)
    database.run("UPDATE users SET email_verified_at = NULL WHERE id = :u", u=me.id)
    assert_error(regenerate(me.browser, scene_id), 403, "email_not_verified")
    assert balance(me.browser) == AFTER_SCRIPT and waiting_jobs() == []


def test_signed_out_is_401(app, outbox):
    me = creator(app)
    _, scene_id = voice_project(app, me)
    assert_error(Browser(app).post(f"/scenes/{scene_id}/voice/regenerate", {}), 401, "not_authenticated")


def test_someone_elses_or_an_unknown_scene_is_404(app, database, outbox):
    owner, other = creator(app), creator(app)
    project, scene_id = voice_project(app, owner)
    for target in (scene_id, str(uuid.uuid4()), "not-a-uuid"):
        assert_error(regenerate(other.browser, target), 404, "not_found")
    assert balance(other.browser) == 100 and waiting_jobs() == []
    assert scene_of(owner.browser, project["id"], scene_id)["voiceStatus"] == "missing"

    assert owner.browser.delete(f"/projects/{project['id']}").status_code == 204
    assert_error(regenerate(owner.browser, scene_id), 404, "not_found")


def test_no_voice_while_the_script_is_being_written(app, database, outbox):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    assert me.browser.post(f"/projects/{project['id']}/script/regenerate", {}).status_code == 202
    assert_error(regenerate(me.browser, scene_id), 409, "already_running")
    assert balance(me.browser) == AFTER_SCRIPT - SCRIPT_COST
    run_jobs(app)


def test_one_voice_per_scene_at_a_time(app, database, outbox):
    """G-12: clicking twice starts one job; other scenes are not blocked."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    first, second = scene_ids(project)[:2]
    job = started(me.browser, first)
    assert_error(regenerate(me.browser, first), 409, "already_running")
    assert_error(regenerate(me.browser, first, {"voiceId": VOICE_IDS[1]}), 409, "already_running")
    assert balance(me.browser) == AFTER_SCRIPT - VOICE_COST
    assert waiting_jobs() == [job["id"]]
    started(me.browser, second)
    run_jobs(app)
    started(me.browser, first)  # free again once the first finished
    run_jobs(app)
    assert_ledger_matches_balance(database, me.id)


def test_not_enough_credits_is_402_and_changes_nothing(app, database, outbox):
    me = creator(app)
    project, scene_id = voice_project(app, me, words_of(80))  # 4 credits
    set_balance(database, me.id, 3)
    error = assert_error(regenerate(me.browser, scene_id), 402, "insufficient_credits")
    assert error["details"]["needed"] == 4 and error["details"]["balance"] == 3
    assert scene_of(me.browser, project["id"], scene_id)["voiceStatus"] == "missing"
    assert (
        waiting_jobs() == []
        and database.value("SELECT count(*) FROM jobs WHERE type = 'voice' AND user_id = :u", u=me.id) == 0
    )
    set_balance(database, me.id, 4)
    started(me.browser, scene_id)
    assert balance(me.browser) == 0
    run_jobs(app)


def switch_off(database, *providers: str) -> None:
    for name in providers:
        database.run(
            "INSERT INTO provider_switches (provider, enabled, reason) VALUES (:p, false, 'test') "
            "ON CONFLICT (provider) DO UPDATE SET enabled = false",
            p=name,
        )


def test_with_every_voice_provider_switched_off_it_is_503_and_free(app, database, outbox):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    switch_off(database, "mock")
    assert_error(regenerate(me.browser, scene_id), 503, "provider_unavailable")
    assert balance(me.browser) == AFTER_SCRIPT and waiting_jobs() == []
    assert scene_of(me.browser, project["id"], scene_id)["voiceStatus"] == "missing"
    database.run("UPDATE provider_switches SET enabled = true WHERE provider = 'mock'")
    started(me.browser, scene_id)
    run_jobs(app)


def test_errors_come_in_the_documented_order_503_then_409_then_402(app, database, outbox):
    me = creator(app)
    _, scene_id = voice_project(app, me)
    started(me.browser, scene_id)  # the scene's voice is now being made
    set_balance(database, me.id, 0)
    switch_off(database, "mock")
    assert_error(regenerate(me.browser, scene_id), 503, "provider_unavailable")
    database.run("UPDATE provider_switches SET enabled = true WHERE provider = 'mock'")
    assert_error(regenerate(me.browser, scene_id), 409, "already_running")
    jobs.manual_queue.clear()
    database.run("UPDATE jobs SET state = 'failed' WHERE user_id = :u AND type = 'voice'", u=me.id)
    assert_error(regenerate(me.browser, scene_id), 402, "insufficient_credits")


def test_a_provider_switched_off_after_the_click_fails_the_job_and_refunds_it(app, database, outbox):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    job = started(me.browser, scene_id)
    switch_off(database, "mock")
    run_jobs(app)
    failed = get_job(me.browser, job["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "provider_unavailable"
    assert failed["creditsRefunded"] is True and balance(me.browser) == AFTER_SCRIPT
    assert scene_of(me.browser, project["id"], scene_id)["voiceStatus"] == "failed"


@pytest.fixture(scope="module")
def elevenlabs_only_without_key(app):
    return build_app(voice_providers="elevenlabs", elevenlabs_api_key="")


def test_elevenlabs_alone_without_a_key_is_503(app, elevenlabs_only_without_key, outbox):
    me = creator(elevenlabs_only_without_key)
    project = scripted_project(elevenlabs_only_without_key, me.browser)
    scene_id = project["scenes"][0]["id"]
    assert_error(regenerate(me.browser, scene_id), 503, "provider_unavailable")
    assert balance(me.browser) == AFTER_SCRIPT


# ---------------------------------------------------------------- Idempotency-Key


def test_the_same_key_and_body_returns_the_first_answer_without_new_work(app, database, outbox):
    me = creator(app)
    _, scene_id = voice_project(app, me)
    first = regenerate(me.browser, scene_id, {"voiceId": VOICE_IDS[2]}, key="click-1")
    assert first.status_code == 202
    again = regenerate(me.browser, scene_id, {"voiceId": VOICE_IDS[2]}, key="click-1")
    assert again.status_code == 202
    assert again.get_json() == first.get_json()
    assert database.value("SELECT count(*) FROM jobs WHERE type = 'voice' AND user_id = :u", u=me.id) == 1
    assert balance(me.browser) == AFTER_SCRIPT - VOICE_COST
    run_jobs(app)
    # Still the first answer after the job finished (keys last 24 hours).
    assert (
        regenerate(me.browser, scene_id, {"voiceId": VOICE_IDS[2]}, key="click-1").get_json()
        == first.get_json()
    )
    assert waiting_jobs() == []


def test_the_same_key_with_another_body_is_a_conflict(app, database, outbox):
    me = creator(app)
    _, scene_id = voice_project(app, me)
    assert regenerate(me.browser, scene_id, {}, key="click-2").status_code == 202
    assert_error(
        regenerate(me.browser, scene_id, {"voiceId": VOICE_IDS[1]}, key="click-2"),
        409,
        "idempotency_conflict",
    )
    assert balance(me.browser) == AFTER_SCRIPT - VOICE_COST
    run_jobs(app)


def test_a_refused_request_can_be_retried_with_its_key(app, database, outbox):
    """Only successful answers are kept."""
    me = creator(app)
    project, scene_id = voice_project(app, me)
    set_balance(database, me.id, 0)
    assert_error(regenerate(me.browser, scene_id, {}, key="retry-me"), 402, "insufficient_credits")
    set_balance(database, me.id, 10)
    assert regenerate(me.browser, scene_id, {}, key="retry-me").status_code == 202
    run_jobs(app)
    assert scene_of(me.browser, project["id"], scene_id)["voiceStatus"] == "ready"


@pytest.mark.parametrize("key", ["", "has space", "x" * 101, "ключ", "a/b", "semi;colon"])
def test_an_invalid_key_is_400(app, outbox, key):
    me = creator(app)
    _, scene_id = voice_project(app, me)
    assert_invalid(regenerate(me.browser, scene_id, {}, key=key), "Idempotency-Key")
    assert balance(me.browser) == AFTER_SCRIPT and waiting_jobs() == []


@pytest.mark.parametrize("key", ["a", "x" * 100, "0c4f-77ab_12:34.56", str(uuid.uuid4())])
def test_valid_keys_are_accepted(app, outbox, key):
    me = creator(app)
    _, scene_id = voice_project(app, me)
    assert regenerate(me.browser, scene_id, {}, key=key).status_code == 202
    run_jobs(app)


def test_keys_belong_to_one_creator(app, outbox):
    one, two = creator(app), creator(app)
    _, first = voice_project(app, one)
    _, second = voice_project(app, two)
    a = regenerate(one.browser, first, {}, key="shared-key")
    b = regenerate(two.browser, second, {}, key="shared-key")
    assert a.status_code == b.status_code == 202
    assert a.get_json()["job"]["id"] != b.get_json()["job"]["id"]
    run_jobs(app)


def test_the_same_key_sent_while_the_first_is_still_handled_is_already_running(app, outbox, monkeypatch):
    from reelforge_api import generation

    me = creator(app)
    _, scene_id = voice_project(app, me)
    other = log_in(app, me.account)
    real = generation.start_voice_job
    seen: list = []

    def slow_start(project, scene, voice_id=""):
        res = on_another_thread(lambda: regenerate(other, scene_id, {}, key="double-click"))
        seen.append((res.status_code, (res.get_json() or {}).get("error", {}).get("code")))
        return real(project, scene, voice_id)

    monkeypatch.setattr(generation, "start_voice_job", slow_start)
    assert regenerate(me.browser, scene_id, {}, key="double-click").status_code == 202
    assert seen == [(409, "already_running")]
    assert balance(me.browser) == AFTER_SCRIPT - VOICE_COST
    run_jobs(app)


# ---------------------------------------------------------------- edits while the voice is being made


def ready_voice(app, me, project, scene_id) -> str:
    started(me.browser, scene_id)
    run_jobs(app)
    return scene_of(me.browser, project["id"], scene_id)["voice"]["assetId"]


@pytest.mark.parametrize(
    ("change", "had_voice", "after"),
    [
        ({"narration": "Something else entirely."}, False, "missing"),
        ({"narration": "Something else entirely."}, True, "outdated"),
        ({"mood": "sad"}, True, "outdated"),
        ({"mood": "sad"}, False, "missing"),
    ],
)
def test_editing_narration_or_mood_while_queued_cancels_and_refunds_the_voice_at_once(
    app, database, outbox, change, had_voice, after
):
    """C2 (QB4-005 fix): the voice being made is for the old text, so the edit stops it and
    refunds it. The scene is `outdated`, or `missing` if it never had a voice."""
    me = creator(app)
    project, scene_id = voice_project(app, me)
    database.run("UPDATE scenes SET mood = 'calm' WHERE id = :s", s=scene_id)
    old = ready_voice(app, me, project, scene_id) if had_voice else None
    paid = balance(me.browser)
    job = started(me.browser, scene_id)

    res = me.browser.patch(f"/scenes/{scene_id}", change)
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["scene"]["voiceStatus"] == after
    cancelled = get_job(me.browser, job["id"])
    assert cancelled["state"] == "cancelled" and cancelled["creditsRefunded"] is True
    assert balance(me.browser) == paid
    assert get_project(me.browser, project["id"])["activeJobs"] == []
    assert job_ledger(database, job["id"]) == [("reserve", -VOICE_COST), ("refund", VOICE_COST)]

    run_jobs(app)  # the worker finds it cancelled and does nothing
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == after
    assert (scene["voice"] or {}).get("assetId") == old, "an earlier voice is kept"
    kept = [a["s3_key"] for a in asset_rows(database, project["id"])]
    assert stored_voice_files(app, me.id, project["id"]) == kept
    # The scene can get a voice for the new text straight away.
    started(me.browser, scene_id)
    run_jobs(app)
    assert scene_of(me.browser, project["id"], scene_id)["voiceStatus"] == "ready"
    assert_ledger_matches_balance(database, me.id)


@pytest.mark.parametrize("had_voice", [False, True])
def test_editing_narration_while_the_voice_is_being_made_cancels_it_and_deletes_its_file(
    app, database, outbox, monkeypatch, had_voice
):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    old = ready_voice(app, me, project, scene_id) if had_voice else None
    paid = balance(me.browser)
    job = started(me.browser, scene_id)
    edits = during_work(
        monkeypatch,
        lambda: on_another_thread(
            lambda: me.browser.patch(f"/scenes/{scene_id}", {"narration": "Changed meanwhile."}).status_code
        ),
    )
    run_jobs(app)
    assert edits == [200]
    final = get_job(me.browser, job["id"])
    assert final["state"] == "cancelled" and final["creditsRefunded"] is True and final["output"] is None
    assert balance(me.browser) == paid
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == ("outdated" if had_voice else "missing")
    assert (scene["voice"] or {}).get("assetId") == old
    kept = [a["s3_key"] for a in asset_rows(database, project["id"])]
    assert len(kept) == int(had_voice)
    assert stored_voice_files(app, me.id, project["id"]) == kept, "the dropped voice's file is deleted"
    assert_ledger_matches_balance(database, me.id)


@pytest.mark.parametrize("had_voice", [False, True])
def test_a_rewrite_that_changes_the_narration_cancels_the_scenes_voice(app, database, outbox, had_voice):
    """Fix log round 3: a C6 rewrite landing while the scene's voice is queued cancels and
    refunds it, like a C2 edit. The scene is `outdated`, or `missing` if it never had a voice."""
    me = creator(app)
    project, scene_id = voice_project(app, me)
    old = ready_voice(app, me, project, scene_id) if had_voice else None
    voice = started(me.browser, scene_id)
    paid = balance(me.browser)
    rewrite = me.browser.post(f"/scenes/{scene_id}/rewrite", {"instructions": "shorter"}).get_json()["job"]
    jobs.manual_queue.sort(key=lambda job_id: job_id != rewrite["id"])  # the rewrite lands first
    run_jobs(app)
    assert get_job(me.browser, rewrite["id"])["state"] == "succeeded"
    final = get_job(me.browser, voice["id"])
    assert final["state"] == "cancelled" and final["creditsRefunded"] is True
    assert balance(me.browser) == paid - COSTS["rewrite"] + VOICE_COST
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == ("outdated" if had_voice else "missing")
    assert (scene["voice"] or {}).get("assetId") == old
    kept = [a["s3_key"] for a in asset_rows(database, project["id"])]
    assert len(kept) == int(had_voice)
    assert stored_voice_files(app, me.id, project["id"]) == kept
    assert_ledger_matches_balance(database, me.id)


@pytest.mark.parametrize("had_voice", [False, True])
def test_a_narration_changed_behind_the_apis_back_is_caught_when_the_voice_is_saved(
    app, database, outbox, monkeypatch, had_voice
):
    """The apply-time `scene_changed` safety net, now reachable only by a direct change (API
    design, Job object: `outdated`, or `missing` if the scene never had a voice)."""
    me = creator(app)
    project, scene_id = voice_project(app, me)
    old = ready_voice(app, me, project, scene_id) if had_voice else None
    paid = balance(me.browser)
    job = started(me.browser, scene_id)
    during_work(
        monkeypatch,
        lambda: database.run("UPDATE scenes SET narration = 'Changed directly.' WHERE id = :s", s=scene_id),
    )
    run_jobs(app)
    final = get_job(me.browser, job["id"])
    assert final["state"] == "failed" and final["error"]["code"] == "scene_changed"
    assert final["creditsRefunded"] is True and balance(me.browser) == paid
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == ("outdated" if had_voice else "missing")
    assert (scene["voice"] or {}).get("assetId") == old
    kept = [a["s3_key"] for a in asset_rows(database, project["id"])]
    assert len(kept) == int(had_voice)
    assert stored_voice_files(app, me.id, project["id"]) == kept, "the dropped voice's file is deleted"


def test_a_lock_error_when_saving_fails_the_voice_and_deletes_its_file(app, database, outbox, monkeypatch):
    """Fix log round 3 (QB4-015): the worker's second lock is inside the error handling, so a
    deadlock victim fails the job (refunded) instead of leaving it running."""
    me = creator(app)
    project, scene_id = voice_project(app, me)
    job = started(me.browser, scene_id)
    real = jobs._lock
    calls = {"n": 0}

    def second_lock_fails(job_id):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("deadlock detected")
        return real(job_id)

    monkeypatch.setattr(jobs, "_lock", second_lock_fails)
    run_jobs(app)
    final = get_job(me.browser, job["id"])
    assert final["state"] == "failed" and final["error"]["code"] == "internal_error"
    assert final["creditsRefunded"] is True and balance(me.browser) == AFTER_SCRIPT
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == "failed"
    assert asset_rows(database, project["id"]) == []
    assert stored_voice_files(app, me.id, project["id"]) == []


def test_deleting_the_account_while_a_voice_is_being_made_drops_it(app, database, outbox, monkeypatch):
    """A12 with a voice job running: the job is cancelled with the account, and the worker
    then drops the made file."""
    from .support import PASSWORD

    me = creator(app)
    project, scene_id = voice_project(app, me)
    job = started(me.browser, scene_id)
    answers = during_work(
        monkeypatch,
        lambda: on_another_thread(lambda: me.browser.delete("/me", {"password": PASSWORD}).status_code),
    )
    run_jobs(app)
    assert answers == [204]
    state = database.rows("SELECT state FROM jobs WHERE id = :j", j=job["id"])
    assert state in ([], [{"state": "cancelled"}])
    assert database.rows("SELECT id FROM assets WHERE project_id = :p", p=project["id"]) == []
    assert stored_voice_files(app, me.id, project["id"]) == []


@pytest.mark.parametrize("state", ["queued", "running"])
def test_caption_fixes_are_refused_while_the_voice_is_being_made(app, database, outbox, monkeypatch, state):
    """QB4-004 fix: the new voice would replace the fixed words, so C2 `words` is 409."""
    me = creator(app)
    project, scene_id = voice_project(app, me, "Drink water every morning")
    ready_voice(app, me, project, scene_id)
    words = [{"text": w["text"].upper()} for w in scene_of(me.browser, project["id"], scene_id)["words"]]
    started(me.browser, scene_id)

    def fix():
        return me.browser.patch(f"/scenes/{scene_id}", {"words": words})

    if state == "queued":
        answers = [fix()]
    else:
        answers = during_work(monkeypatch, lambda: on_another_thread(fix))
    run_jobs(app)
    assert_error(answers[0], 409, "already_running")
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == "ready" and scene["wordsEdited"] is False
    assert [w["text"] for w in scene["words"]] == ["Drink", "water", "every", "morning"]
    assert fix().status_code == 200, "allowed again once the voice is ready"


def test_edits_that_do_not_change_the_voice_let_it_finish(app, database, outbox, monkeypatch):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    job = started(me.browser, scene_id)
    during_work(
        monkeypatch,
        lambda: on_another_thread(
            lambda: me.browser.patch(
                f"/scenes/{scene_id}", {"onScreenText": "New title", "visualPrompt": "a cat"}
            )
        ),
    )
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "succeeded"
    assert scene_of(me.browser, project["id"], scene_id)["voiceStatus"] == "ready"


def test_sending_the_same_narration_and_mood_does_not_stop_the_voice(app, database, outbox):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    scene = scene_of(me.browser, project["id"], scene_id)
    job = started(me.browser, scene_id)
    res = me.browser.patch(f"/scenes/{scene_id}", {"narration": scene["narration"], "mood": scene["mood"]})
    assert res.status_code == 200
    assert get_job(me.browser, job["id"])["state"] == "queued"
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "succeeded"


@pytest.mark.parametrize("when", ["queued", "running"])
@pytest.mark.parametrize("had_voice", [False, True])
def test_changing_the_project_voice_mid_job_cancels_the_voice_and_refunds_it(
    app, database, outbox, monkeypatch, when, had_voice
):
    """BUG-036 fix. B5: changing voiceId marks all voices outdated; a voice still being made in
    the old voice is stopped and refunded."""
    me = creator(app)
    project, scene_id = voice_project(app, me)
    old = ready_voice(app, me, project, scene_id) if had_voice else None
    paid = balance(me.browser)
    job = started(me.browser, scene_id)

    def change():
        return me.browser.patch(f"/projects/{project['id']}", {"voiceId": VOICE_IDS[6]}).status_code

    if when == "queued":
        assert change() == 200
    else:
        # Later takes call `change` again; choosing the same voice again changes nothing.
        changes = during_work(monkeypatch, lambda: on_another_thread(change))
    run_jobs(app)
    if when == "running":
        assert changes == [200]
    final = get_job(me.browser, job["id"])
    assert final["state"] == "cancelled" and final["creditsRefunded"] is True
    assert balance(me.browser) == paid
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == ("outdated" if had_voice else "missing")
    assert (scene["voice"] or {}).get("assetId") == old
    assert len(asset_rows(database, project["id"])) == int(had_voice)
    assert stored_voice_files(app, me.id, project["id"]) == [
        a["s3_key"] for a in asset_rows(database, project["id"])
    ]
    assert get_project(me.browser, project["id"])["voiceId"] == VOICE_IDS[6]

    started(me.browser, scene_id)
    run_jobs(app)
    assert voice_used(database, project["id"]) == VOICE_IDS[6]


def test_changing_the_project_voice_stops_only_voice_jobs(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    first, second = scene_ids(project)[:2]
    voice = started(me.browser, first)
    rewrite = me.browser.post(f"/scenes/{second}/rewrite", {"instructions": "shorter"}).get_json()["job"]
    assert me.browser.patch(f"/projects/{project['id']}", {"voiceId": VOICE_IDS[6]}).status_code == 200
    assert get_job(me.browser, voice["id"])["state"] == "cancelled"
    assert get_job(me.browser, rewrite["id"])["state"] == "queued"
    run_jobs(app)
    assert get_job(me.browser, rewrite["id"])["state"] == "succeeded"


def test_choosing_the_same_project_voice_again_does_not_stop_the_voice(app, database, outbox):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    job = started(me.browser, scene_id)
    assert me.browser.patch(f"/projects/{project['id']}", {"voiceId": project["voiceId"]}).status_code == 200
    assert get_job(me.browser, job["id"])["state"] == "queued"
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "succeeded"


@pytest.mark.parametrize(
    "edit",
    [
        lambda me, project, scene_id: me.browser.patch(f"/scenes/{scene_id}", {"narration": "New words."}),
        lambda me, project, scene_id: me.browser.patch(f"/scenes/{scene_id}", {"mood": "tense"}),
        lambda me, project, scene_id: me.browser.patch(
            f"/projects/{project['id']}", {"voiceId": VOICE_IDS[7]}
        ),
    ],
    ids=["narration", "mood", "project voice"],
)
def test_a_stale_voice_never_comes_back_as_ready_after_an_edit(app, database, outbox, edit):
    """BUG-037 fix: the edit itself stops the job and leaves the scene `outdated`; a later
    cancel finds the job finished."""
    me = creator(app)
    project, scene_id = voice_project(app, me)
    database.run("UPDATE scenes SET mood = 'calm' WHERE id = :s", s=scene_id)
    ready_voice(app, me, project, scene_id)

    job = started(me.browser, scene_id)
    assert edit(me, project, scene_id).status_code == 200
    assert scene_of(me.browser, project["id"], scene_id)["voiceStatus"] == "outdated"
    assert_error(me.browser.post(f"/jobs/{job['id']}/cancel"), 409, "job_finished")
    run_jobs(app)
    assert scene_of(me.browser, project["id"], scene_id)["voiceStatus"] == "outdated"


def test_deleting_the_scene_mid_job_cancels_refunds_and_deletes_the_file(app, database, outbox, monkeypatch):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    job = started(me.browser, scene_id)
    deletes = during_work(
        monkeypatch, lambda: on_another_thread(lambda: me.browser.delete(f"/scenes/{scene_id}").status_code)
    )
    run_jobs(app)
    assert deletes == [204]
    final = get_job(me.browser, job["id"])
    assert final["state"] == "cancelled" and final["creditsRefunded"] is True
    assert final["sceneId"] is None, "jobs of a deleted scene are kept without their scene"
    assert asset_rows(database, project["id"]) == []
    assert stored_voice_files(app, me.id, project["id"]) == []
    assert balance(me.browser) == AFTER_SCRIPT
    assert_ledger_matches_balance(database, me.id)


def test_deleting_the_project_mid_job_cancels_refunds_and_deletes_the_file(
    app, database, outbox, monkeypatch
):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    job = started(me.browser, scene_id)
    deletes = during_work(
        monkeypatch,
        lambda: on_another_thread(lambda: me.browser.delete(f"/projects/{project['id']}").status_code),
    )
    run_jobs(app)
    assert deletes == [204]
    assert get_job(me.browser, job["id"])["state"] == "cancelled"
    assert asset_rows(database, project["id"]) == []
    assert stored_voice_files(app, me.id, project["id"]) == []
    assert balance(me.browser) == AFTER_SCRIPT


def test_deleting_a_scene_with_a_queued_voice_cancels_it(app, database, outbox):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    job = started(me.browser, scene_id)
    assert me.browser.delete(f"/scenes/{scene_id}").status_code == 204
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "cancelled"
    assert balance(me.browser) == AFTER_SCRIPT
    assert get_project(me.browser, project["id"])["activeJobs"] == []


# ---------------------------------------------------------------- regressions: shared files


def test_a_duplicated_scene_shares_the_voice_until_one_of_them_gets_a_new_one(app, database, outbox):
    """F-2 and C4: a copy reuses the file; replacing one scene's voice keeps the file the copy
    still uses, and the file is released once no scene uses it."""
    me = creator(app)
    project, scene_id = voice_project(app, me)
    started(me.browser, scene_id)
    run_jobs(app)
    original = scene_of(me.browser, project["id"], scene_id)
    copy = me.browser.post(f"/scenes/{scene_id}/duplicate").get_json()["scene"]
    assert copy["voice"]["assetId"] == original["voice"]["assetId"] and copy["voiceStatus"] == "ready"
    assert download(copy["voice"]["url"]) == download(original["voice"]["url"])
    shared = original["voice"]["assetId"]

    def replaced(asset_id):
        return database.value("SELECT replaced_at FROM assets WHERE id = :a", a=asset_id)

    started(me.browser, scene_id)
    run_jobs(app)
    assert replaced(shared) is None, "the copy still uses it"
    assert scene_of(me.browser, project["id"], copy["id"])["voice"]["assetId"] == shared

    started(me.browser, copy["id"])
    run_jobs(app)
    assert replaced(shared) is not None, "no scene uses it any more"


def test_duplicating_a_scene_whose_voice_is_being_made_does_not_copy_the_work(app, database, outbox):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    started(me.browser, scene_id)
    copy = me.browser.post(f"/scenes/{scene_id}/duplicate").get_json()["scene"]
    assert copy["voiceStatus"] == "missing"
    run_jobs(app)
    assert scene_of(me.browser, project["id"], scene_id)["voiceStatus"] == "ready"
    after = scene_of(me.browser, project["id"], copy["id"])
    assert after["voiceStatus"] == "missing" and after["voice"] is None


def test_regenerating_the_script_cancels_running_voices(app, database, outbox):
    me = creator(app)
    project, scene_id = voice_project(app, me)
    job = started(me.browser, scene_id)
    script = me.browser.post(f"/projects/{project['id']}/script/regenerate", {}).get_json()["job"]
    # The new script is written first; the voice then finds its scene replaced.
    jobs.manual_queue.sort(key=lambda job_id: job_id != script["id"])
    run_jobs(app)
    assert get_job(me.browser, script["id"])["state"] == "succeeded"
    final = get_job(me.browser, job["id"])
    assert final["state"] == "cancelled" and final["creditsRefunded"] is True and final["sceneId"] is None
    assert balance(me.browser) == AFTER_SCRIPT - SCRIPT_COST
    assert asset_rows(database, project["id"]) == []
    assert stored_voice_files(app, me.id, project["id"]) == []


def test_a_voice_on_a_ready_project_keeps_it_ready(app, database, outbox):
    """after_edit: a project whose scenes all have voice and visual stays ready when one of
    them gets a new take."""
    me = creator(app)
    project, scene_id = voice_project(app, me)
    for sid in scene_ids(project):
        started(me.browser, sid)
    run_jobs(app)
    database.run("UPDATE scenes SET visual_status = 'ready' WHERE project_id = :p", p=project["id"])
    database.run("UPDATE projects SET status = 'ready' WHERE id = :p", p=project["id"])
    started(me.browser, scene_id)
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "ready"
