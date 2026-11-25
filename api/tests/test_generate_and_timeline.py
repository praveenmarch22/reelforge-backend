"""B11 Generate, B12 timeline, D4 music and the music library (milestone B4 slice 3):
docs/API_DESIGN.md B11, B12, D4; TECHNICAL_DESIGN.md 7 "As built"; PRD CR-1, G-5;
pipeline strategy "The timeline JSON". Runs with the offline mocks."""

from __future__ import annotations

import math
import uuid

import pytest

from reelforge_api.reel import _share
from reelforge_api.script_rules import MOODS, estimated_seconds, voice_credits

from .projects_support import (
    ISO_UTC,
    JOB_FIELDS,
    SCRIPT_COST,
    assert_ledger_matches_balance,
    balance,
    creator,
    get_job,
    get_project,
    job_ledger,
    new_asset,
    run_jobs,
    scene_ids,
    scripted_project,
    set_balance,
    waiting_jobs,
)
from .support import Browser, assert_error, assert_invalid
from .visual_support import scene_of, select_visual, upload_asset, visual_started
from .voice_support import (  # noqa: F401 - no_real_voice_services is an autouse fixture
    NETWORK_ATTEMPTS,
    download,
    no_real_voice_services,
    started,
)

AFTER_SCRIPT = 100 - SCRIPT_COST
TRACK_FIELDS = {"id", "title", "durationSeconds", "moods", "license", "sourceUrl", "previewUrl"}


def generate(browser, project_id: str, body: dict | None = None, *, key: str | None = None):
    headers = {"Idempotency-Key": key} if key is not None else None
    return browser.post(f"/projects/{project_id}/generate", body, headers=headers)


def generated(browser, project_id: str, body: dict | None = None) -> dict:
    res = generate(browser, project_id, body)
    assert res.status_code == 202, res.get_data(as_text=True)
    return res.get_json()


def timeline(browser, project_id: str) -> dict:
    res = browser.get(f"/projects/{project_id}/timeline")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()


def add_track(database, title: str, moods: list[str], *, active: bool = True, seconds: float = 120.0) -> str:
    track_id = str(uuid.uuid4())
    database.run(
        "INSERT INTO music_tracks "
        "(id, title, s3_key, duration_seconds, moods, license, source_url, is_active) "
        "VALUES (:id, :t, :k, :d, :m, :l, 'https://incompetech.com/', :a)",
        id=track_id, t=title, k=f"library/music/test-{track_id}.mp3", d=seconds, m=moods,
        l=f'CC BY 4.0: "{title}"', a=active,
    )  # fmt: skip
    return track_id


def set_narrations(browser, project: dict, narrations: list[str]) -> None:
    for scene, narration in zip(project["scenes"], narrations, strict=False):
        assert browser.patch(f"/scenes/{scene['id']}", {"narration": narration}).status_code == 200


def words(n: int) -> str:
    return " ".join(["word"] * n)


def project_status(browser, project_id: str) -> str:
    return get_project(browser, project_id)["status"]


# ---------------------------------------------------------------- B11: the main flow


def test_generate_starts_every_voice_and_visual_then_the_reel_is_ready(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    track = add_track(database, "Calm One", [project["musicMood"]])
    add_track(database, "Other Mood", [m for m in MOODS if m != project["musicMood"]][:1])
    ids = scene_ids(project)

    res = generate(me.browser, project["id"])
    assert res.status_code == 202, res.get_data(as_text=True)
    body = res.get_json()
    assert set(body) == {"jobs", "creditsReserved", "balance"}
    for job in body["jobs"]:
        assert set(job) == JOB_FIELDS and job["state"] == "queued" and ISO_UTC.match(job["queuedAt"])
    voice_jobs = [j for j in body["jobs"] if j["type"] == "voice"]
    visual_jobs = [j for j in body["jobs"] if j["type"] == "visual"]
    assert sorted(j["sceneId"] for j in voice_jobs) == sorted(ids)
    assert sorted(j["sceneId"] for j in visual_jobs) == sorted(ids)
    voice_price = voice_credits(sum(estimated_seconds(s["narration"]) for s in project["scenes"]))
    assert sum(j["creditsReserved"] for j in voice_jobs) == voice_price
    assert all(j["creditsReserved"] == 0 for j in visual_jobs), "stock is free"
    assert body["creditsReserved"] == voice_price
    assert body["balance"] == balance(me.browser) == AFTER_SCRIPT - voice_price

    during = get_project(me.browser, project["id"])
    assert during["status"] == "generating" and isinstance(during["progress"], int)
    assert during["music"] == {"trackId": track}, "a track tagged with the project's music mood"
    assert all(s["voiceStatus"] == "queued" and s["visualStatus"] == "queued" for s in during["scenes"])

    run_jobs(app)
    after = get_project(me.browser, project["id"])
    assert after["status"] == "ready" and after["progress"] is None
    assert all(s["voiceStatus"] == s["visualStatus"] == "ready" for s in after["scenes"])
    built = timeline(me.browser, project["id"])
    assert built["ready"] is True and after["durationSeconds"] == built["durationSeconds"]
    saved = database.value("SELECT timeline FROM projects WHERE id = :p", p=project["id"])
    assert saved is not None and saved["durationInFrames"] == built["timeline"]["durationInFrames"]
    srcs = [s["voice"]["src"] for s in saved["scenes"]] + [s["visual"]["src"] for s in saved["scenes"]]
    assert all(src.startswith("users/") for src in srcs), "the saved timeline names storage keys, not links"
    assert saved["music"]["src"].startswith("library/music/")
    assert_ledger_matches_balance(database, me.id)


def test_without_a_library_generate_still_works_with_no_music(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    generated(me.browser, project["id"])
    run_jobs(app)
    found = get_project(me.browser, project["id"])
    assert found["status"] == "ready" and found["music"] is None
    assert timeline(me.browser, project["id"])["timeline"]["music"] is None


def test_a_chosen_music_track_or_upload_is_kept(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    add_track(database, "A", [project["musicMood"]])
    chosen = add_track(database, "B", ["sad"])
    assert me.browser.patch(f"/projects/{project['id']}", {"music": {"trackId": chosen}}).status_code == 200
    generated(me.browser, project["id"])
    assert get_project(me.browser, project["id"])["music"] == {"trackId": chosen}
    run_jobs(app)


def test_without_a_track_for_the_mood_any_active_one_is_picked_and_kept(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    others = [m for m in MOODS if m != project["musicMood"]]
    tracks = {add_track(database, f"T{n}", [others[n]]) for n in range(3)}
    add_track(database, "Retired", [project["musicMood"]], active=False)
    first = timeline(me.browser, project["id"])["timeline"]["music"]["src"]
    assert (
        timeline(me.browser, project["id"])["timeline"]["music"]["src"].split("?")[0] == first.split("?")[0]
    )
    generated(me.browser, project["id"])
    assert get_project(me.browser, project["id"])["music"]["trackId"] in tracks
    run_jobs(app)


# ---------------------------------------------------------------- B11: the plan


def test_the_plan_takes_missing_outdated_and_failed_parts_and_skips_upload_scenes(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    assert len(ids) >= 4
    started(me.browser, ids[0])  # voice ready
    visual_started(me.browser, ids[0])  # visual ready
    started(me.browser, ids[1])
    run_jobs(app)
    me.browser.patch(f"/scenes/{ids[1]}", {"narration": "Changed words."})  # voice outdated
    database.run(
        "UPDATE scenes SET voice_status = 'failed', visual_status = 'failed' WHERE id = :s", s=ids[2]
    )
    asset = upload_asset(database, me.id, project["id"])
    assert select_visual(me.browser, ids[3], {"assetId": asset}).status_code == 200
    database.run(
        "UPDATE scenes SET visual_asset_id = NULL, visual_status = 'missing' WHERE id = :s", s=ids[3]
    )

    body = generated(me.browser, project["id"])
    planned = {(j["type"], j["sceneId"]) for j in body["jobs"]}
    assert ("voice", ids[0]) not in planned and ("visual", ids[0]) not in planned, "ready parts are kept"
    assert ("voice", ids[1]) in planned and ("visual", ids[1]) in planned
    assert ("voice", ids[2]) in planned and ("visual", ids[2]) in planned
    assert ("voice", ids[3]) in planned and ("visual", ids[3]) not in planned, "upload scenes wait for a file"
    run_jobs(app)
    after = get_project(me.browser, project["id"])
    assert after["status"] == "needs_attention", "the upload scene still has no visual"
    assert scene_of(me.browser, project["id"], ids[3])["visualStatus"] == "missing"


def test_scene_ids_limit_generate_to_those_scenes(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    body = generated(me.browser, project["id"], {"sceneIds": ids[:2]})
    assert {j["sceneId"] for j in body["jobs"]} == set(ids[:2]) and len(body["jobs"]) == 4
    run_jobs(app)
    assert project_status(me.browser, project["id"]) == "needs_attention", "the other scenes still need work"


@pytest.mark.parametrize(
    "body",
    [{"sceneIds": []}, {"sceneIds": ["not-a-uuid"]}, {"sceneIds": "all"}, {"scene_ids": []}, {"extra": 1}],
)
def test_bad_bodies_are_400_and_start_nothing(app, outbox, body):
    me = creator(app)
    project = scripted_project(app, me.browser)
    assert_error(generate(me.browser, project["id"], body), 400, "validation_error")
    assert waiting_jobs() == [] and balance(me.browser) == AFTER_SCRIPT


def test_scene_ids_of_another_project_are_400(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    other = scripted_project(app, me.browser)
    assert_invalid(generate(me.browser, project["id"], {"sceneIds": scene_ids(other)[:1]}), "sceneIds")
    assert_invalid(generate(me.browser, project["id"], {"sceneIds": [str(uuid.uuid4())]}), "sceneIds")
    assert waiting_jobs() == []


# ---------------------------------------------------------------- B11: the price


@pytest.mark.parametrize(
    ("total", "weights", "expected"),
    [
        (4, [4.0, 8.0, 20.0], [1, 1, 2]),
        (2, [4.0, 4.0, 4.0], None),
        (6, [30.0, 30.0, 30.0], [2, 2, 2]),
        (2, [0.0, 0.0], None),
        (0, [5.0], [0]),
        (2, [], []),
    ],
)
def test_the_voice_price_is_split_in_whole_credits_that_add_up(total, weights, expected):
    parts = _share(total, weights)
    assert len(parts) == len(weights)
    if weights:
        assert sum(parts) == total and all(p >= 0 for p in parts)
    if expected is not None:
        assert parts == expected


def test_voices_are_charged_together_on_the_reels_length(app, database, outbox):
    """Product decision 2026-11-25: 2 per started 30 s of all the voices together."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    n = len(project["scenes"])
    set_narrations(me.browser, project, [words(10)] * n)  # 4 s each
    body = generated(me.browser, project["id"])
    voice_jobs = [j for j in body["jobs"] if j["type"] == "voice"]
    total = voice_credits(4.0 * n)
    assert sum(j["creditsReserved"] for j in voice_jobs) == total
    assert total <= sum(2 for _ in range(n)), "never more than charging scene by scene"
    for job in voice_jobs:
        assert job_ledger(database, job["id"]) == (
            [("reserve", -job["creditsReserved"])] if job["creditsReserved"] else []
        )
    run_jobs(app)


def test_a_failed_scene_refunds_its_own_share_and_generate_retries_just_it(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    set_narrations(
        me.browser, project, [words(40), words(40) + " [voice-fail]"] + [words(10)] * (len(ids) - 2)
    )
    body = generated(me.browser, project["id"])
    failing = next(j for j in body["jobs"] if j["type"] == "voice" and j["sceneId"] == ids[1])
    assert failing["creditsReserved"] > 0
    run_jobs(app)
    failed = get_job(me.browser, failing["id"])
    assert failed["state"] == "failed" and failed["creditsRefunded"] is True
    assert balance(me.browser) == AFTER_SCRIPT - body["creditsReserved"] + failing["creditsReserved"]
    after = get_project(me.browser, project["id"])
    assert after["status"] == "needs_attention"
    assert scene_of(me.browser, project["id"], ids[1])["voiceStatus"] == "failed"

    me.browser.patch(f"/scenes/{ids[1]}", {"narration": words(40)})
    retry = generated(me.browser, project["id"])
    assert [(j["type"], j["sceneId"]) for j in retry["jobs"]] == [("voice", ids[1])]
    assert retry["creditsReserved"] == voice_credits(estimated_seconds(words(40)))
    run_jobs(app)
    assert project_status(me.browser, project["id"]) == "ready"
    assert_ledger_matches_balance(database, me.id)


def test_not_enough_credits_starts_nothing(app, database, outbox):
    """CR-1: all of it is reserved at once, or nothing starts."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    for scene in project["scenes"]:
        me.browser.patch(f"/scenes/{scene['id']}", {"visualSource": "ai_image"})
    needed = voice_credits(sum(estimated_seconds(s["narration"]) for s in project["scenes"])) + len(
        project["scenes"]
    )
    set_balance(database, me.id, needed - 1)
    before = get_project(me.browser, project["id"])
    error = assert_error(generate(me.browser, project["id"]), 402, "insufficient_credits")
    assert error["details"]["needed"] == needed and error["details"]["balance"] == needed - 1
    after = get_project(me.browser, project["id"])
    assert after["status"] == before["status"] and after["music"] is None
    assert [(s["voiceStatus"], s["visualStatus"]) for s in after["scenes"]] == [
        (s["voiceStatus"], s["visualStatus"]) for s in before["scenes"]
    ]
    assert waiting_jobs() == []
    assert (
        database.value("SELECT count(*) FROM jobs WHERE user_id = :u AND type IN ('voice','visual')", u=me.id)
        == 0
    )
    set_balance(database, me.id, needed)
    generated(me.browser, project["id"])
    assert balance(me.browser) == 0
    run_jobs(app)


# ---------------------------------------------------------------- B11: refusals and their order


@pytest.mark.parametrize("status", ["rendering", "posting"])
def test_a_rendering_or_posting_reel_is_409_before_anything_else(app, database, outbox, status):
    me = creator(app)
    project = scripted_project(app, me.browser)
    database.run("UPDATE projects SET status = :s WHERE id = :p", s=status, p=project["id"])
    database.run("INSERT INTO provider_switches (provider, enabled) VALUES ('mock', false)")
    set_balance(database, me.id, 0)
    assert_error(generate(me.browser, project["id"]), 409, "already_running")


def test_while_the_script_is_being_written_it_is_409(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    assert me.browser.post(f"/projects/{project['id']}/script/regenerate", {}).status_code == 202
    assert_error(generate(me.browser, project["id"]), 409, "already_running")
    run_jobs(app)


def test_a_needed_provider_switched_off_is_503_before_402(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    set_balance(database, me.id, 0)
    database.run("INSERT INTO provider_switches (provider, enabled) VALUES ('mock', false)")
    assert_error(generate(me.browser, project["id"]), 503, "provider_unavailable")
    assert project_status(me.browser, project["id"]) == "draft" and waiting_jobs() == []


def test_a_provider_that_is_not_needed_does_not_block(app, database, outbox):
    """Only visuals left to make: the voice providers being off doesn't matter."""
    from .conftest import build_app

    me = creator(app)
    project = scripted_project(app, me.browser)
    for sid in scene_ids(project):
        started(me.browser, sid)
    run_jobs(app)
    no_voice = build_app(voice_providers="elevenlabs", elevenlabs_api_key="")
    browser = Browser(no_voice).copy_session_from(me.browser)
    res = browser.post(f"/projects/{project['id']}/generate", {})
    assert res.status_code == 202, res.get_data(as_text=True)
    assert {j["type"] for j in res.get_json()["jobs"]} == {"visual"}
    run_jobs(app)


def test_when_everything_left_is_in_progress_it_is_409(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    generated(me.browser, project["id"])
    set_balance(database, me.id, 0)
    assert_error(generate(me.browser, project["id"]), 409, "already_running")
    run_jobs(app)


def test_nothing_left_to_make_is_200_and_a_complete_reel_is_ready(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    for sid in scene_ids(project):
        started(me.browser, sid)
        visual_started(me.browser, sid)
    run_jobs(app)
    database.run("UPDATE projects SET status = 'draft' WHERE id = :p", p=project["id"])
    res = generate(me.browser, project["id"])
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json() == {"jobs": [], "creditsReserved": 0, "balance": balance(me.browser)}
    assert project_status(me.browser, project["id"]) == "ready"


def test_an_empty_generate_closes_a_run_left_in_generating(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    database.run("UPDATE scenes SET voice_status = 'failed' WHERE project_id = :p", p=project["id"])
    generated(me.browser, project["id"], {"sceneIds": scene_ids(project)[:1]})
    run_jobs(app)
    database.run("UPDATE projects SET status = 'generating' WHERE id = :p", p=project["id"])
    database.run(
        "UPDATE scenes SET voice_status = 'ready', visual_status = 'ready' WHERE project_id = :p",
        p=project["id"],
    )
    assert generate(me.browser, project["id"]).status_code == 200
    assert project_status(me.browser, project["id"]) == "ready"


def test_who_may_generate(app, database, outbox):
    owner, other = creator(app), creator(app)
    project = scripted_project(app, owner.browser)
    assert_error(generate(other.browser, project["id"]), 404, "not_found")
    assert_error(Browser(app).post(f"/projects/{project['id']}/generate", {}), 401, "not_authenticated")
    database.run("UPDATE users SET email_verified_at = NULL WHERE id = :u", u=owner.id)
    assert_error(generate(owner.browser, project["id"]), 403, "email_not_verified")
    assert waiting_jobs() == []


def test_the_same_idempotency_key_returns_the_first_answer(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    first = generate(me.browser, project["id"], {}, key="gen-1")
    again = generate(me.browser, project["id"], {}, key="gen-1")
    assert first.status_code == again.status_code == 202 and again.get_json() == first.get_json()
    assert len(waiting_jobs()) == len(first.get_json()["jobs"])
    assert_error(
        generate(me.browser, project["id"], {"sceneIds": scene_ids(project)[:1]}, key="gen-1"),
        409,
        "idempotency_conflict",
    )
    run_jobs(app)


# ---------------------------------------------------------------- B11: things happening during a run


def test_an_edit_during_the_run_still_closes_it(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    body = generated(me.browser, project["id"])
    assert me.browser.patch(f"/scenes/{ids[0]}", {"narration": "Edited during the run."}).status_code == 200
    voice = next(j for j in body["jobs"] if j["type"] == "voice" and j["sceneId"] == ids[0])
    assert get_job(me.browser, voice["id"])["state"] == "cancelled"
    assert project_status(me.browser, project["id"]) == "generating"
    run_jobs(app)
    assert project_status(me.browser, project["id"]) == "needs_attention"
    retry = generated(me.browser, project["id"])
    assert [(j["type"], j["sceneId"]) for j in retry["jobs"]] == [("voice", ids[0])]
    run_jobs(app)
    assert project_status(me.browser, project["id"]) == "ready"


def test_cancelling_one_job_still_closes_the_run(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    body = generated(me.browser, project["id"])
    job = body["jobs"][0]
    assert me.browser.post(f"/jobs/{job['id']}/cancel").status_code == 200
    run_jobs(app)
    assert project_status(me.browser, project["id"]) == "needs_attention"


def test_cancelling_every_job_closes_the_run(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    body = generated(me.browser, project["id"])
    for job in body["jobs"]:
        assert me.browser.post(f"/jobs/{job['id']}/cancel").status_code == 200
    assert project_status(me.browser, project["id"]) == "needs_attention"
    assert balance(me.browser) == AFTER_SCRIPT


def test_deleting_a_scene_during_the_run(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    generated(me.browser, project["id"])
    assert me.browser.delete(f"/scenes/{ids[0]}").status_code == 204
    run_jobs(app)
    found = get_project(me.browser, project["id"])
    assert found["status"] == "ready" and found["activeJobs"] == []
    assert timeline(me.browser, project["id"])["ready"] is True
    assert found["durationSeconds"] == timeline(me.browser, project["id"])["durationSeconds"]
    assert_ledger_matches_balance(database, me.id)


def test_deleting_the_project_during_the_run_refunds_everything(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    generated(me.browser, project["id"])
    assert me.browser.delete(f"/projects/{project['id']}").status_code == 204
    run_jobs(app)
    assert balance(me.browser) == AFTER_SCRIPT
    assert (
        database.value(
            "SELECT count(*) FROM jobs WHERE project_id = :p AND state IN ('queued','running')",
            p=project["id"],
        )
        == 0
    )


def test_a_single_scene_reel_closes_when_its_last_job_ends(app, database, outbox):
    """The job saving its result is still `running` until its transaction ends; the run must
    close anyway (the `finishing` job is not counted as active)."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    for sid in scene_ids(project)[1:]:
        assert me.browser.delete(f"/scenes/{sid}").status_code == 204
    generated(me.browser, project["id"])
    run_jobs(app)
    assert project_status(me.browser, project["id"]) == "ready"


def test_a_ready_project_that_is_edited_and_generated_again_is_ready_again(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    generated(me.browser, project["id"])
    run_jobs(app)
    me.browser.patch(f"/scenes/{scene_ids(project)[0]}", {"stockQuery": "forest"})
    assert project_status(me.browser, project["id"]) == "draft"
    body = generated(me.browser, project["id"])
    assert [(j["type"]) for j in body["jobs"]] == ["visual"]
    run_jobs(app)
    assert project_status(me.browser, project["id"]) == "ready"


# ---------------------------------------------------------------- B12 the timeline


def test_the_timeline_of_a_fresh_draft_shows_placeholders(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    res = me.browser.get(f"/projects/{project['id']}/timeline")
    assert res.status_code == 200
    body = res.get_json()
    assert set(body) == {"timelineVersion", "timeline", "durationSeconds", "tooLong", "ready"}
    assert body["ready"] is False and body["tooLong"] is False
    assert body["timelineVersion"] == get_project(me.browser, project["id"])["timelineVersion"]
    t = body["timeline"]
    assert {"version", "projectId", "fps", "width", "height", "durationInFrames", "captionStyle",
            "showCaptions", "voiceVolume", "transition", "music", "scenes"} <= set(t)  # fmt: skip
    assert (t["fps"], t["width"], t["height"]) == (30, 1080, 1920) and t["projectId"] == project["id"]
    frame = 0
    for index, (scene, item) in enumerate(zip(project["scenes"], t["scenes"], strict=True)):
        assert item["id"] == scene["id"] and item["startFrame"] == frame
        expected = max(estimated_seconds(scene["narration"]) + 0.3, 1.5)
        assert item["durationInFrames"] == math.ceil(expected * 30)
        assert item["visual"]["src"] == "" and item["voice"]["src"] == "" and item["words"] == []
        assert item["ready"] is False and item["mood"] == scene["mood"]
        assert item["visual"]["motion"] == ("zoom-in", "pan-left", "zoom-out", "pan-right")[index % 4]
        frame += item["durationInFrames"]
    assert t["durationInFrames"] == frame and body["durationSeconds"] == round(frame / 30, 2)


def test_the_timeline_uses_the_voice_length_and_plays_the_files(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    sid = scene_ids(project)[0]
    started(me.browser, sid)
    visual_started(me.browser, sid)
    run_jobs(app)
    scene = scene_of(me.browser, project["id"], sid)
    item = timeline(me.browser, project["id"])["timeline"]["scenes"][0]
    assert item["durationInFrames"] == math.ceil(max(scene["voice"]["durationSeconds"] + 0.3, 1.5) * 30)
    assert item["voice"]["durationSeconds"] == scene["voice"]["durationSeconds"]
    assert item["words"] == scene["words"] and item["ready"] is True
    assert item["visual"]["kind"] == "video"
    assert download(item["voice"]["src"])[:4] == b"RIFF" and download(item["visual"]["src"])[4:8] == b"ftyp"


def test_a_short_voice_still_gets_a_second_and_a_half(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    sid = scene_ids(project)[0]
    me.browser.patch(f"/scenes/{sid}", {"narration": "Go."})
    started(me.browser, sid)
    run_jobs(app)
    database.run(
        "UPDATE assets SET duration_seconds = 0.4 "
        "WHERE id = (SELECT voice_asset_id FROM scenes WHERE id = :s)",
        s=sid,
    )
    assert timeline(me.browser, project["id"])["timeline"]["scenes"][0]["durationInFrames"] == 45


def test_an_outdated_voice_is_left_out_but_a_failed_retake_keeps_the_old_one(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    first, second = scene_ids(project)[:2]
    for sid in (first, second):
        started(me.browser, sid)
    run_jobs(app)
    me.browser.patch(f"/scenes/{first}", {"narration": words(20)})
    database.run("UPDATE scenes SET voice_status = 'failed' WHERE id = :s", s=second)
    items = timeline(me.browser, project["id"])["timeline"]["scenes"]
    assert items[0]["voice"]["src"] == "" and items[0]["words"] == []
    assert items[0]["durationInFrames"] == math.ceil((estimated_seconds(words(20)) + 0.3) * 30)
    assert (
        items[1]["voice"]["src"] != "" and items[1]["words"]
    ), "the old voice still plays after a failed take"
    assert items[1]["ready"] is False


def test_trims_settings_and_on_screen_text_reach_the_timeline(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    sid = scene_ids(project)[0]
    before = timeline(me.browser, project["id"])["timelineVersion"]
    me.browser.patch(f"/scenes/{sid}", {"trimStart": 1.5, "trimEnd": 4.0, "onScreenText": "Big text"})
    me.browser.patch(
        f"/projects/{project['id']}",
        {"settings": {"captionStyle": "neon", "showCaptions": False, "voiceVolume": 0.8, "transition": "fade",
                      "musicVolume": 0.4, "duckedVolume": 0.1, "ducking": False}},
    )  # fmt: skip
    add_track(database, "Any", [project["musicMood"]])
    body = timeline(me.browser, project["id"])
    t = body["timeline"]
    assert body["timelineVersion"] == before + 2
    assert t["scenes"][0]["visual"]["trimStart"] == 1.5 and t["scenes"][0]["visual"]["trimEnd"] == 4.0
    assert t["scenes"][0]["onScreenText"] == "Big text"
    assert (t["captionStyle"], t["showCaptions"], t["voiceVolume"], t["transition"]) == (
        "neon",
        False,
        0.8,
        "fade",
    )
    assert t["music"]["volume"] == 0.4 and t["music"]["duckedVolume"] == 0.4, "ducking off: same volume"
    assert (t["music"]["fadeInSeconds"], t["music"]["fadeOutSeconds"]) == (1.0, 1.5)
    me.browser.patch(f"/projects/{project['id']}", {"settings": {"ducking": True}})
    assert timeline(me.browser, project["id"])["timeline"]["music"]["duckedVolume"] == 0.1


def test_the_timeline_music_is_the_upload_then_the_track_then_the_pick(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    picked = add_track(database, "Picked", [project["musicMood"]])
    chosen = add_track(database, "Chosen", ["sad"])
    upload = new_asset(database, me.id, project["id"], kind="music")

    def src():
        music = timeline(me.browser, project["id"])["timeline"]["music"]
        return music["src"].split("?")[0] if music else None

    assert "test-" + picked in src(), "the track Generate would choose"
    me.browser.patch(f"/projects/{project['id']}", {"music": {"trackId": chosen}})
    assert "test-" + chosen in src()
    me.browser.patch(f"/projects/{project['id']}", {"music": {"assetId": upload}})
    assert src().endswith(f"test/{upload}")


def test_a_reel_over_90_seconds_is_too_long(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    me.browser.patch(f"/scenes/{scene_ids(project)[0]}", {"narration": words(230)})  # 92 s
    body = timeline(me.browser, project["id"])
    assert body["tooLong"] is True and body["durationSeconds"] > 90


def test_the_timeline_needs_the_owner(app, outbox):
    owner, other = creator(app), creator(app)
    project = scripted_project(app, owner.browser)
    assert_error(other.browser.get(f"/projects/{project['id']}/timeline"), 404, "not_found")
    assert_error(Browser(app).get(f"/projects/{project['id']}/timeline"), 401, "not_authenticated")


def test_every_edit_bumps_the_timeline_version(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)

    def version():
        return get_project(me.browser, project["id"])["timelineVersion"]

    edits = [
        lambda: me.browser.patch(f"/scenes/{ids[0]}", {"onScreenText": "x"}),
        lambda: me.browser.patch(f"/projects/{project['id']}", {"settings": {"transition": "fade"}}),
        lambda: me.browser.post(
            f"/projects/{project['id']}/scenes/reorder", {"sceneIds": list(reversed(ids))}
        ),
        lambda: me.browser.post(f"/projects/{project['id']}/scenes", {"narration": "New scene."}),
        lambda: me.browser.post(f"/scenes/{ids[0]}/duplicate"),
        lambda: me.browser.delete(f"/scenes/{ids[1]}"),
    ]
    for edit in edits:
        before = version()
        assert edit().status_code in (200, 201, 204)
        assert version() == before + 1
    before = version()
    started(me.browser, ids[0])
    run_jobs(app)
    assert version() > before, "a new voice changes the timeline too"
    before = version()
    me.browser.get(f"/projects/{project['id']}/timeline")
    assert version() == before, "reading changes nothing"


# ---------------------------------------------------------------- D4 music


def test_the_music_list_is_sorted_by_title_with_links(app, database, outbox):
    me = creator(app)
    b = add_track(database, "Bravo", ["calm", "sad"])
    a = add_track(database, "Alpha", ["joyful"])
    add_track(database, "Charlie", ["calm"], active=False)
    res = me.browser.get("/music")
    assert res.status_code == 200 and set(res.get_json()) == {"items"}
    items = res.get_json()["items"]
    assert [i["id"] for i in items] == [a, b], "by title; inactive tracks are hidden"
    for item in items:
        assert set(item) == TRACK_FIELDS and item["previewUrl"].startswith("http")
    assert items[1]["moods"] == ["calm", "sad"] and items[1]["durationSeconds"] == 120.0
    assert [i["id"] for i in me.browser.get("/music?mood=calm").get_json()["items"]] == [b]
    assert me.browser.get("/music?mood=funny").get_json()["items"] == []
    assert len(me.browser.get("/music?mood=").get_json()["items"]) == 2, "empty means all"


@pytest.mark.parametrize("mood", ["CALM", "happy", "calm,sad", "x" * 200])
def test_an_unknown_mood_is_400(app, outbox, mood):
    me = creator(app)
    assert_invalid(me.browser.get(f"/music?mood={mood}"), "mood")


def test_the_music_list_needs_a_login_but_not_a_verified_email(app, outbox):
    assert_error(Browser(app).get("/music"), 401, "not_authenticated")
    me = creator(app, verified=False)
    assert me.browser.get("/music").status_code == 200


def test_edit_answers_carry_the_new_timeline_version(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    res = me.browser.patch(f"/projects/{project['id']}", {"title": "New title"})
    assert res.status_code == 200
    assert (
        res.get_json()["project"]["timelineVersion"]
        == get_project(me.browser, project["id"])["timelineVersion"]
    )
    assert res.get_json()["project"]["timelineVersion"] == project["timelineVersion"] + 1


def test_the_listed_duration_follows_the_reel_after_edits(app, database, outbox):
    """API design, Project summary: `durationSeconds` is the Reel's length (null until it
    exists). After a generated Reel is edited, the dashboard must not show the old length."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    generated(me.browser, project["id"])
    run_jobs(app)
    assert me.browser.delete(f"/scenes/{scene_ids(project)[0]}").status_code == 204
    now = timeline(me.browser, project["id"])["durationSeconds"]
    listed = next(p for p in me.browser.get("/projects").get_json()["items"] if p["id"] == project["id"])
    assert listed["durationSeconds"] == now


def test_a_reel_made_ready_scene_by_scene_has_a_listed_duration(app, database, outbox):
    """A project can become ready without Generate (C7 and C8 on every scene)."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    for sid in scene_ids(project):
        started(me.browser, sid)
        visual_started(me.browser, sid)
    run_jobs(app)
    found = get_project(me.browser, project["id"])
    assert found["status"] == "ready"
    assert found["durationSeconds"] == timeline(me.browser, project["id"])["durationSeconds"]


# ---------------------------------------------------------------- round 2


def run_only(app, job_ids: list[str]) -> None:
    """Runs these queued jobs now and leaves the rest waiting, as a busy worker might."""
    from reelforge_api import jobs as jobs_module

    with app.app_context():
        for job_id in job_ids:
            jobs_module.manual_queue.remove(job_id)
            jobs_module.run(job_id)


@pytest.mark.parametrize("ending", ["fails", "is cancelled"])
def test_a_script_rewrite_during_a_run_that_does_not_happen_still_closes_the_run(
    app, database, outbox, ending
):
    """QB4S3-001: the voices and visuals finish while the project is `scripting`; when the
    script job then fails or is cancelled, the run must close, not stay `generating`."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    body = generated(me.browser, project["id"])
    instructions = "shorter [fail]" if ending == "fails" else "shorter"
    script = me.browser.post(f"/projects/{project['id']}/script/regenerate", {"instructions": instructions})
    assert script.status_code == 202, script.get_data(as_text=True)
    script_job = script.get_json()["job"]
    assert project_status(me.browser, project["id"]) == "scripting"
    run_only(app, [j["id"] for j in body["jobs"]])
    assert project_status(me.browser, project["id"]) == "scripting"
    if ending == "fails":
        run_jobs(app)
        assert get_job(me.browser, script_job["id"])["state"] == "failed"
    else:
        assert me.browser.post(f"/jobs/{script_job['id']}/cancel").status_code == 200
    assert project_status(me.browser, project["id"]) == "ready"
    listed = get_project(me.browser, project["id"])["durationSeconds"]
    assert listed == timeline(me.browser, project["id"])["durationSeconds"]


def test_a_script_rewrite_that_fails_with_jobs_left_keeps_the_run_until_they_end(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    generated(me.browser, project["id"])
    res = me.browser.post(f"/projects/{project['id']}/script/regenerate", {"instructions": "x [fail]"})
    run_only(app, [res.get_json()["job"]["id"]])
    assert project_status(me.browser, project["id"]) == "generating", "its jobs are still waiting"
    run_jobs(app)
    assert project_status(me.browser, project["id"]) == "ready"


def saved_timeline(database, project_id: str):
    return database.value("SELECT timeline FROM projects WHERE id = :p", p=project_id)


def test_the_saved_timeline_follows_the_status(app, database, outbox):
    """`projects.save_timeline`: rebuilt whenever the project is ready, cleared otherwise."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    generated(me.browser, project["id"])
    run_jobs(app)
    assert saved_timeline(database, project["id"]) is not None

    me.browser.patch(f"/scenes/{ids[0]}", {"onScreenText": "Changed text"})  # still ready
    assert project_status(me.browser, project["id"]) == "ready"
    assert saved_timeline(database, project["id"])["scenes"][0]["onScreenText"] == "Changed text"

    me.browser.patch(f"/scenes/{ids[0]}", {"stockQuery": "forest"})  # now draft
    assert project_status(me.browser, project["id"]) == "draft"
    assert saved_timeline(database, project["id"]) is None
    assert get_project(me.browser, project["id"])["durationSeconds"] is None

    visual_started(me.browser, ids[0])
    run_jobs(app)
    assert project_status(me.browser, project["id"]) == "ready"
    assert saved_timeline(database, project["id"]) is not None
    listed = get_project(me.browser, project["id"])["durationSeconds"]
    assert listed == timeline(me.browser, project["id"])["durationSeconds"]


def first_item(browser, project_id: str) -> dict:
    return timeline(browser, project_id)["timeline"]["scenes"][0]


def assert_old_voice_left_out(item: dict) -> None:
    assert item["voice"]["src"] == "" and item["words"] == []
    assert item["durationInFrames"] == math.ceil((estimated_seconds(words(20)) + 0.3) * 30)


def test_after_a_narration_edit_the_old_voice_is_not_played_while_a_take_is_queued(app, database, outbox):
    """QB4S3-003: a voice is used only if made from the current narration and mood."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    sid = scene_ids(project)[0]
    started(me.browser, sid)
    run_jobs(app)
    me.browser.patch(f"/scenes/{sid}", {"narration": words(20)})
    started(me.browser, sid)
    assert_old_voice_left_out(first_item(me.browser, project["id"]))
    run_jobs(app)
    assert first_item(me.browser, project["id"])["voice"]["src"] != ""


def test_after_a_narration_edit_the_old_voice_is_not_played_while_a_take_runs(
    app, database, outbox, monkeypatch
):
    from reelforge_api.providers.voice import MockVoice

    me = creator(app)
    project = scripted_project(app, me.browser)
    sid = scene_ids(project)[0]
    started(me.browser, sid)
    run_jobs(app)
    me.browser.patch(f"/scenes/{sid}", {"narration": words(20)})
    seen: list = []
    real = MockVoice.synthesize

    def slow(self, *args):
        seen.append(first_item(me.browser, project["id"]))
        return real(self, *args)

    monkeypatch.setattr(MockVoice, "synthesize", slow)
    started(me.browser, sid)
    run_jobs(app)
    assert_old_voice_left_out(seen[0])


def test_after_a_narration_edit_and_a_failed_take_the_old_voice_is_not_played(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    sid = scene_ids(project)[0]
    started(me.browser, sid)
    run_jobs(app)
    me.browser.patch(f"/scenes/{sid}", {"narration": words(20)})
    job = started(me.browser, sid)
    database.run("INSERT INTO provider_switches (provider, enabled) VALUES ('mock', false)")
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "failed"
    assert scene_of(me.browser, project["id"], sid)["voiceStatus"] == "failed"
    assert_old_voice_left_out(first_item(me.browser, project["id"]))


def test_after_a_mood_change_the_old_voice_is_not_played(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    sid = scene_ids(project)[0]
    database.run("UPDATE scenes SET mood = 'calm' WHERE id = :s", s=sid)
    started(me.browser, sid)
    run_jobs(app)
    me.browser.patch(f"/scenes/{sid}", {"mood": "sad"})
    started(me.browser, sid)
    item = first_item(me.browser, project["id"])
    assert item["voice"]["src"] == "" and item["words"] == []
    run_jobs(app)
    assert first_item(me.browser, project["id"])["voice"]["src"] != ""


def test_a_new_take_of_an_unchanged_scene_keeps_playing_the_old_voice(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    sid = scene_ids(project)[0]
    started(me.browser, sid)
    run_jobs(app)
    started(me.browser, sid)
    item = first_item(me.browser, project["id"])
    assert item["voice"]["src"] != "" and item["words"]
    run_jobs(app)


@pytest.mark.parametrize(("n_words", "cost"), [(10, 1), (37, 1), (38, 2), (80, 4)])
def test_a_generate_that_makes_one_voice_costs_what_c7_would(app, database, outbox, n_words, cost):
    """QB4S3-004: `scene_voice_credits` (1 up to 15 s, else 2 per started 30 s)."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    for sid in ids:
        started(me.browser, sid)
        visual_started(me.browser, sid)
    run_jobs(app)
    me.browser.patch(f"/scenes/{ids[0]}", {"narration": words(n_words)})
    body = generated(me.browser, project["id"])
    assert [(j["type"], j["creditsReserved"]) for j in body["jobs"]] == [("voice", cost)]
    assert body["creditsReserved"] == cost
    run_jobs(app)


def test_an_empty_generate_on_a_ready_reel_changes_nothing(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    generated(me.browser, project["id"])
    run_jobs(app)
    before = get_project(me.browser, project["id"])
    res = generate(me.browser, project["id"])
    assert res.status_code == 200 and res.get_json()["jobs"] == []
    after = get_project(me.browser, project["id"])
    assert after["timelineVersion"] == before["timelineVersion"]
    assert after["status"] == "ready" and after["updatedAt"] == before["updatedAt"]


def test_an_empty_generate_leaves_needs_attention_alone_when_only_an_upload_is_missing(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    asset = upload_asset(database, me.id, project["id"])
    assert select_visual(me.browser, ids[0], {"assetId": asset}).status_code == 200
    database.run(
        "UPDATE scenes SET visual_asset_id = NULL, visual_status = 'missing' WHERE id = :s", s=ids[0]
    )
    generated(me.browser, project["id"])
    run_jobs(app)
    assert project_status(me.browser, project["id"]) == "needs_attention"
    version = get_project(me.browser, project["id"])["timelineVersion"]
    res = generate(me.browser, project["id"])
    assert res.status_code == 200 and res.get_json()["jobs"] == []
    found = get_project(me.browser, project["id"])
    assert found["status"] == "needs_attention" and found["timelineVersion"] == version


def test_an_empty_generate_turns_a_complete_draft_ready_with_its_timeline(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    for sid in scene_ids(project):
        started(me.browser, sid)
        visual_started(me.browser, sid)
    run_jobs(app)
    database.run("UPDATE projects SET status = 'draft', timeline = NULL WHERE id = :p", p=project["id"])
    assert generate(me.browser, project["id"]).status_code == 200
    found = get_project(me.browser, project["id"])
    assert found["status"] == "ready" and found["durationSeconds"] is not None


def test_a_deactivated_track_is_replaced_by_the_moods_pick(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    picked = add_track(database, "Mood Pick", [project["musicMood"]])
    chosen = add_track(database, "Chosen", ["sad"])
    me.browser.patch(f"/projects/{project['id']}", {"music": {"trackId": chosen}})
    database.run("UPDATE music_tracks SET is_active = false WHERE id = :t", t=chosen)
    src = timeline(me.browser, project["id"])["timeline"]["music"]["src"].split("?")[0]
    assert src.endswith(f"test-{picked}.mp3")
