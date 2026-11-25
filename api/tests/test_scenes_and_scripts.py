"""C1-C6 scenes, B8 regenerate and B9 hooks (docs/API_DESIGN.md groups B and C; PRD S5-S8,
S-7, G-12; DB_DESIGN.md 5)."""

from __future__ import annotations

import uuid

import pytest

from reelforge_api.providers.llm import MockLLM
from reelforge_api.script_rules import SCENES_MAX

from .projects_support import (
    JOB_FIELDS,
    SCENE_FIELDS,
    SCRIPT_COST,
    assert_ledger_matches_balance,
    balance,
    creator,
    get_job,
    get_project,
    job_ledger,
    new_asset,
    positions,
    run_jobs,
    scene_ids,
    scripted_project,
    set_balance,
    set_statuses,
    start_project,
    waiting_jobs,
)
from .support import assert_error, assert_invalid


@pytest.fixture
def prompts(monkeypatch) -> list[dict]:
    """Every request the mock language model receives: kind, prompt and context."""
    seen: list[dict] = []
    real = MockLLM.generate_json

    def recording(self, prompt, kind, context):
        seen.append({"kind": kind, "prompt": prompt, "context": context})
        return real(self, prompt, kind, context)

    monkeypatch.setattr(MockLLM, "generate_json", recording)
    return seen


def without_time(project: dict) -> dict:
    """The project without updatedAt, which moves whenever the row is written (also when a
    failed job puts the status back)."""
    return {k: v for k, v in project.items() if k != "updatedAt"}


def add_scene(browser, project_id: str, **body):
    body.setdefault("narration", "A new scene.")
    return browser.post(f"/projects/{project_id}/scenes", body)


def assert_positions_are_1_to_n(project: dict) -> None:
    assert positions(project["scenes"]) == list(range(1, len(project["scenes"]) + 1))


# ---------------------------------------------------------------- C1


def test_adding_a_scene_puts_it_last_by_default_or_where_asked(app, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    n = len(project["scenes"])
    res = add_scene(me.browser, project["id"], narration="Last one.", onScreenText="End", mood="sad")
    assert res.status_code == 201, res.get_data(as_text=True)
    body = res.get_json()
    assert set(body) == {"scene", "estimate"}
    scene = body["scene"]
    assert set(scene) == SCENE_FIELDS
    assert scene["position"] == n + 1
    assert (scene["narration"], scene["onScreenText"], scene["mood"]) == ("Last one.", "End", "sad")
    assert scene["visualSource"] == "stock" and scene["voiceStatus"] == "missing"
    assert scene["visualPrompt"] == "" and scene["stockQuery"] == ""

    first = add_scene(me.browser, project["id"], narration="First now.", position=1).get_json()["scene"]
    assert first["position"] == 1
    far = add_scene(me.browser, project["id"], narration="Far away.", position=99).get_json()["scene"]
    middle = add_scene(me.browser, project["id"], narration="Middle.", position=3).get_json()["scene"]
    after = get_project(me.browser, project["id"])
    assert_positions_are_1_to_n(after)
    order = [s["id"] for s in after["scenes"]]
    assert order[0] == first["id"]
    assert order[2] == middle["id"]
    assert order[-1] == far["id"], "a position past the end means last"
    assert [s for s in order if s in scene_ids(project)] == scene_ids(project), "the others keep their order"
    assert order[-2] == scene["id"]


def test_a_full_ai_project_adds_ai_video_scenes_unless_told_otherwise(app, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser, mode="full_ai")
    assert add_scene(me.browser, project["id"]).get_json()["scene"]["visualSource"] == "ai_video"
    chosen = add_scene(
        me.browser, project["id"], visualSource="ai_image", visualPrompt="a fox", stockQuery="fox"
    )
    assert chosen.get_json()["scene"]["visualSource"] == "ai_image"
    assert chosen.get_json()["scene"]["visualPrompt"] == "a fox"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"narration": None}, "narration"),
        ({"narration": ""}, "narration"),
        ({"narration": "  \n "}, "narration"),
        ({"narration": "n" * 1501}, "narration"),
        ({"narration": 3}, "narration"),
        ({"onScreenText": "o" * 81}, "onScreenText"),
        ({"onScreenText": "two\nlines"}, "onScreenText"),
        ({"visualPrompt": "v" * 1001}, "visualPrompt"),
        ({"stockQuery": "s" * 101}, "stockQuery"),
        ({"mood": "angry"}, "mood"),
        ({"visualSource": "gif"}, "visualSource"),
        ({"position": 0}, "position"),
        ({"position": "2"}, "position"),
        ({"position": 1.5}, "position"),
        ({"on_screen_text": "x"}, "on_screen_text"),
        ({"voiceStatus": "ready"}, "voiceStatus"),
        ({"projectId": str(uuid.uuid4())}, "projectId"),
    ],
)
def test_adding_a_scene_refuses_invalid_bodies(app, outbox, body, field):
    me = creator(app)
    project = scripted_project(app, me.browser)
    assert_invalid(me.browser.post(f"/projects/{project['id']}/scenes", {"narration": "ok", **body}), field)
    assert get_project(me.browser, project["id"]) == project


def test_the_narration_limit_is_inclusive_and_keeps_line_breaks(app, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    res = add_scene(me.browser, project["id"], narration="n" * 1500)
    assert res.status_code == 201
    res = add_scene(me.browser, project["id"], narration="One line.\nAnother line.", onScreenText="o" * 80)
    assert res.get_json()["scene"]["narration"] == "One line.\nAnother line."


def test_a_reel_has_at_most_30_scenes(app, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    for _ in range(SCENES_MAX - len(project["scenes"])):
        assert add_scene(me.browser, project["id"]).status_code == 201
    assert_invalid(add_scene(me.browser, project["id"]))
    full = get_project(me.browser, project["id"])
    assert len(full["scenes"]) == SCENES_MAX
    assert_invalid(me.browser.post(f"/scenes/{full['scenes'][0]['id']}/duplicate"))
    assert len(get_project(me.browser, project["id"])["scenes"]) == SCENES_MAX


def test_adding_a_scene_to_a_ready_project_sends_it_back_to_draft(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    set_statuses(database, project["id"], voice="ready", visual="ready", status="rendered")
    add_scene(me.browser, project["id"])
    assert get_project(me.browser, project["id"])["status"] == "draft", "the new scene has no voice yet"


# ---------------------------------------------------------------- C2


def patch_scene(browser, scene_id: str, body: dict):
    return browser.patch(f"/scenes/{scene_id}", body)


@pytest.mark.parametrize(
    ("change", "voice", "visual"),
    [
        ({"narration": "Brand new words."}, "outdated", "ready"),
        ({"mood": "tense"}, "outdated", "ready"),
        ({"visualPrompt": "a different shot"}, "ready", "outdated"),
        ({"stockQuery": "city night"}, "ready", "outdated"),
        ({"visualSource": "ai_image"}, "ready", "outdated"),
        ({"onScreenText": "New headline"}, "ready", "ready"),
        ({"trimStart": 0.5, "trimEnd": 2.25}, "ready", "ready"),
    ],
)
def test_editing_a_scene_outdates_exactly_what_it_affects(app, database, outbox, change, voice, visual):
    """C2, S-7."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    set_statuses(database, project["id"], voice="ready", visual="ready", status="rendered")
    target, *others = scene_ids(project)
    res = patch_scene(me.browser, target, change)
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert set(body) == {"scene", "estimate"}
    scene = body["scene"]
    for field, value in change.items():
        assert scene[field] == value
    assert (scene["voiceStatus"], scene["visualStatus"]) == (voice, visual)
    after = get_project(me.browser, project["id"])
    for other in after["scenes"][1:]:
        assert (other["voiceStatus"], other["visualStatus"]) == ("ready", "ready"), "other scenes untouched"
    expected = "ready" if (voice, visual) == ("ready", "ready") else "draft"
    assert after["status"] == expected


def test_sending_the_same_value_outdates_nothing(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    set_statuses(database, project["id"], voice="ready", visual="ready")
    scene = project["scenes"][0]
    res = patch_scene(
        me.browser,
        scene["id"],
        {k: scene[k] for k in ("narration", "mood", "visualPrompt", "stockQuery", "visualSource")},
    )
    assert (res.get_json()["scene"]["voiceStatus"], res.get_json()["scene"]["visualStatus"]) == (
        "ready",
        "ready",
    )


def test_missing_parts_stay_missing_and_failed_ones_become_outdated(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    first, second = scene_ids(project)[:2]
    database.run(
        "UPDATE scenes SET voice_status = 'failed', visual_status = 'failed' WHERE id = :s", s=second
    )
    scene = patch_scene(me.browser, first, {"narration": "Changed.", "stockQuery": "new"}).get_json()["scene"]
    assert (scene["voiceStatus"], scene["visualStatus"]) == ("missing", "missing")
    scene = patch_scene(me.browser, second, {"narration": "Changed.", "stockQuery": "new"}).get_json()[
        "scene"
    ]
    assert (scene["voiceStatus"], scene["visualStatus"]) == ("outdated", "outdated")


def test_live_word_count_and_duration_follow_the_narration(app, outbox):
    """PRD S7."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    scene = project["scenes"][0]
    words = " ".join(["word"] * 25)
    res = patch_scene(me.browser, scene["id"], {"narration": words})
    assert res.get_json()["scene"]["estimatedSeconds"] == 10.0
    after = get_project(me.browser, project["id"])
    old = len(scene["narration"].split())
    assert after["wordCount"] == project["wordCount"] - old + 25
    assert after["estimatedSeconds"] == pytest.approx(
        sum(s["estimatedSeconds"] for s in after["scenes"]), abs=0.05
    )


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"narration": None}, "narration"),
        ({"narration": ""}, "narration"),
        ({"narration": "n" * 1501}, "narration"),
        ({"onScreenText": None}, "onScreenText"),
        ({"onScreenText": "o" * 81}, "onScreenText"),
        ({"mood": None}, "mood"),
        ({"mood": "angry"}, "mood"),
        ({"visualSource": None}, "visualSource"),
        ({"visualSource": "gif"}, "visualSource"),
        ({"visualPrompt": None}, "visualPrompt"),
        ({"stockQuery": None}, "stockQuery"),
        ({"trimStart": None}, "trimStart"),
        ({"trimStart": -1}, "trimStart"),
        ({"trimStart": 601}, "trimStart"),
        ({"trimStart": "1"}, "trimStart"),
        ({"trimEnd": 0}, "trimEnd"),
        ({"trimStart": 2, "trimEnd": 2}, "trimEnd"),
        ({"trimStart": 3, "trimEnd": 2}, "trimEnd"),
        ({"words": None}, "words"),
        ({"words": "fixed"}, "words"),
        ({"position": 2}, "position"),
        ({"voiceStatus": "ready"}, "voiceStatus"),
        ({"words_edited": True}, "words_edited"),
    ],
)
def test_editing_a_scene_refuses_invalid_bodies_and_changes_nothing(app, outbox, body, field):
    me = creator(app)
    project = scripted_project(app, me.browser)
    assert_invalid(patch_scene(me.browser, project["scenes"][0]["id"], body), field)
    assert get_project(me.browser, project["id"]) == project


def test_trim_end_can_be_cleared_with_null(app, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    scene_id = project["scenes"][0]["id"]
    assert (
        patch_scene(me.browser, scene_id, {"trimStart": 1, "trimEnd": 4.5}).get_json()["scene"]["trimEnd"]
        == 4.5
    )
    assert_invalid(patch_scene(me.browser, scene_id, {"trimEnd": 0.5}), "trimEnd")
    scene = patch_scene(me.browser, scene_id, {"trimEnd": None}).get_json()["scene"]
    assert scene["trimEnd"] is None and scene["trimStart"] == 1


def test_caption_word_fixes_change_text_only_and_keep_the_times(app, database, outbox):
    """C2 `words`; CA-4 wordsEdited."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    scene_id = project["scenes"][0]["id"]
    database.run(
        "UPDATE scenes SET words = CAST(:w AS jsonb) WHERE id = :s",
        w='[{"text": "Most", "start": 0.05, "end": 0.31}, {"text": "pepole", "start": 0.31, "end": 0.6}]',
        s=scene_id,
    )
    scene = patch_scene(me.browser, scene_id, {"words": [{"text": "Most"}, {"text": "people"}]}).get_json()[
        "scene"
    ]
    assert scene["words"] == [
        {"text": "Most", "start": 0.05, "end": 0.31},
        {"text": "people", "start": 0.31, "end": 0.6},
    ]
    assert scene["wordsEdited"] is True
    assert scene["voiceStatus"] == "missing", "caption fixes outdate nothing"
    for wrong in (
        [{"text": "one"}],
        [{"text": "a"}, {"text": "b"}, {"text": "c"}],
        [{"text": ""}, {"text": "x"}],
        [{"text": "a", "start": 9}, {"text": "b"}],
    ):
        assert_invalid(patch_scene(me.browser, scene_id, {"words": wrong}))


# ---------------------------------------------------------------- C3


def test_deleting_a_scene_renumbers_the_rest(app, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    res = me.browser.delete(f"/scenes/{ids[1]}")
    assert res.status_code == 204 and res.get_data() == b""
    after = get_project(me.browser, project["id"])
    assert scene_ids(after) == [ids[0], *ids[2:]]
    assert_positions_are_1_to_n(after)
    assert_error(me.browser.delete(f"/scenes/{ids[1]}"), 404, "not_found")
    for scene_id in scene_ids(after):
        assert me.browser.delete(f"/scenes/{scene_id}").status_code == 204
    assert get_project(me.browser, project["id"])["scenes"] == []


def test_deleting_a_scene_of_a_ready_project_keeps_it_ready(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    set_statuses(database, project["id"], voice="ready", visual="ready", status="rendered")
    me.browser.delete(f"/scenes/{project['scenes'][0]['id']}")
    assert get_project(me.browser, project["id"])["status"] == "ready"


def test_deleting_a_scene_with_generated_files_releases_them_for_the_clean_up(app, database, outbox):
    """F-2: files no scene uses any more are marked replaced; a file a copy still uses is kept."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    voice = new_asset(database, me.id, project["id"])
    shared = new_asset(database, me.id, project["id"])
    first, second = scene_ids(project)[:2]
    database.run(
        "UPDATE scenes SET voice_asset_id = :a, voice_status = 'ready' WHERE id = :s", a=voice, s=first
    )
    database.run(
        "UPDATE scenes SET visual_asset_id = :a, visual_status = 'ready' WHERE id = :s", a=shared, s=second
    )
    copy = me.browser.post(f"/scenes/{second}/duplicate").get_json()["scene"]

    assert me.browser.delete(f"/scenes/{first}").status_code == 204
    assert database.value("SELECT replaced_at FROM assets WHERE id = :a", a=voice) is not None
    assert me.browser.delete(f"/scenes/{second}").status_code == 204
    assert (
        database.value("SELECT replaced_at FROM assets WHERE id = :a", a=shared) is None
    ), "the copy uses it"
    assert me.browser.delete(f"/scenes/{copy['id']}").status_code == 204
    assert database.value("SELECT replaced_at FROM assets WHERE id = :a", a=shared) is not None


# ---------------------------------------------------------------- C4


def test_duplicating_a_scene_places_the_copy_right_after_it(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    voice = new_asset(database, me.id, project["id"])
    database.run(
        "UPDATE scenes SET voice_asset_id = :a, voice_status = 'ready' WHERE id = :s", a=voice, s=ids[1]
    )
    before = balance(me.browser)
    res = me.browser.post(f"/scenes/{ids[1]}/duplicate")
    assert res.status_code == 201, res.get_data(as_text=True)
    assert set(res.get_json()) == {"scene", "estimate"}
    copy = res.get_json()["scene"]
    assert copy["position"] == 3 and copy["id"] not in ids
    original = get_project(me.browser, project["id"])["scenes"][1]
    assert {k: v for k, v in copy.items() if k not in ("id", "position")} == {
        k: v for k, v in original.items() if k not in ("id", "position")
    }
    assert copy["voiceStatus"] == "ready", "it reuses the original's voice"
    after = get_project(me.browser, project["id"])
    assert scene_ids(after) == [ids[0], ids[1], copy["id"], *ids[2:]]
    assert_positions_are_1_to_n(after)
    assert balance(me.browser) == before

    last = me.browser.post(f"/scenes/{ids[-1]}/duplicate").get_json()["scene"]
    assert last["position"] == len(after["scenes"]) + 1


# ---------------------------------------------------------------- C5


def reorder(browser, project_id: str, ids: list[str]):
    return browser.post(f"/projects/{project_id}/scenes/reorder", {"sceneIds": ids})


def test_reordering_puts_scenes_in_the_new_order(app, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    new_order = list(reversed(ids))
    res = reorder(me.browser, project["id"], new_order)
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert set(body) == {"scenes", "estimate"}
    assert [s["id"] for s in body["scenes"]] == new_order
    assert positions(body["scenes"]) == list(range(1, len(ids) + 1))
    assert scene_ids(get_project(me.browser, project["id"])) == new_order
    rotated = new_order[1:] + new_order[:1]
    assert scene_ids(get_project(me.browser, project["id"])) != rotated
    reorder(me.browser, project["id"], rotated)
    assert scene_ids(get_project(me.browser, project["id"])) == rotated


def test_reordering_a_rendered_project_sends_it_back_to_ready(app, database, outbox):
    """DB design 5: any edit to a rendered or posted project goes back to ready (the timeline,
    and so the render, no longer matches)."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    set_statuses(database, project["id"], voice="ready", visual="ready", status="posted")
    assert reorder(me.browser, project["id"], list(reversed(scene_ids(project)))).status_code == 200
    assert get_project(me.browser, project["id"])["status"] == "ready"


def test_reordering_needs_every_scene_exactly_once(app, outbox):
    me, other = creator(app), creator(app)
    project = scripted_project(app, me.browser)
    theirs = scripted_project(app, other.browser)
    ids = scene_ids(project)
    for wrong in (
        ids[:-1],
        [*ids, ids[0]],
        [*ids[:-1], ids[0]],
        [*ids[:-1], theirs["scenes"][0]["id"]],
        [*ids, str(uuid.uuid4())],
    ):
        assert_invalid(reorder(me.browser, project["id"], wrong), "sceneIds")
    for body in ({"sceneIds": []}, {"sceneIds": None}, {}, {"sceneIds": ["nope"]}, {"scene_ids": ids}):
        assert_invalid(me.browser.post(f"/projects/{project['id']}/scenes/reorder", body))
    assert get_project(me.browser, project["id"]) == project
    assert scene_ids(get_project(other.browser, theirs["id"])) == scene_ids(theirs)


# ---------------------------------------------------------------- scene edits wait for the script


def test_scene_edits_are_refused_while_the_script_is_being_written(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    res = me.browser.post(f"/projects/{project['id']}/script/regenerate", {})
    assert res.status_code == 202
    for res in (
        add_scene(me.browser, project["id"]),
        patch_scene(me.browser, ids[0], {"narration": "Edited while writing."}),
        me.browser.delete(f"/scenes/{ids[0]}"),
        me.browser.post(f"/scenes/{ids[0]}/duplicate"),
        reorder(me.browser, project["id"], list(reversed(ids))),
        me.browser.post(f"/scenes/{ids[0]}/rewrite", {"instructions": "shorter"}),
        me.browser.post(f"/projects/{project['id']}/hooks"),
    ):
        assert_error(res, 409, "already_running")
    assert scene_ids(get_project(me.browser, project["id"])) == ids, "nothing changed"
    assert balance(me.browser) == 100 - 2 * SCRIPT_COST, "nothing extra was charged"


# ---------------------------------------------------------------- C6


def rewrite(browser, scene_id: str, instructions: str = "make it punchier"):
    return browser.post(f"/scenes/{scene_id}/rewrite", {"instructions": instructions})


def test_rewriting_a_scene_costs_a_credit_and_outdates_its_voice(app, database, outbox, prompts):
    me = creator(app)
    project = scripted_project(app, me.browser)
    set_statuses(database, project["id"], voice="ready", visual="ready", status="ready")
    first, second, third = project["scenes"][:3]
    before = balance(me.browser)
    res = rewrite(me.browser, second["id"], "make this punchier")
    assert res.status_code == 202, res.get_data(as_text=True)
    assert set(res.get_json()) == {"job"}
    job = res.get_json()["job"]
    assert set(job) == JOB_FIELDS
    assert (
        job["type"] == "rewrite_scene"
        and job["sceneId"] == second["id"]
        and job["projectId"] == project["id"]
    )
    assert job["creditsReserved"] == SCRIPT_COST
    assert balance(me.browser) == before - SCRIPT_COST
    assert [j["id"] for j in get_project(me.browser, project["id"])["activeJobs"]] == [job["id"]]

    prompts.clear()
    run_jobs(app)
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded"
    scene = get_project(me.browser, project["id"])["scenes"][1]
    assert scene["narration"] != second["narration"]
    assert done["output"] == {"narration": scene["narration"], "onScreenText": scene["onScreenText"]}
    assert scene["voiceStatus"] == "outdated" and scene["visualStatus"] == "ready"
    assert get_project(me.browser, project["id"])["status"] == "draft"
    # The model sees the scene, its neighbours and the instructions.
    prompt = prompts[0]["prompt"]
    for text in (second["narration"], first["narration"], third["narration"], "make this punchier"):
        assert text in prompt
    assert balance(me.browser) == before - SCRIPT_COST
    assert_ledger_matches_balance(database, me.id)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "instructions"),
        ({"instructions": ""}, "instructions"),
        ({"instructions": "  "}, "instructions"),
        ({"instructions": "i" * 201}, "instructions"),
        ({"instructions": None}, "instructions"),
        ({"instructions": "ok", "tone": "funny"}, "tone"),
    ],
)
def test_rewrite_refuses_invalid_bodies(app, database, outbox, body, field):
    me = creator(app)
    project = scripted_project(app, me.browser)
    assert_invalid(me.browser.post(f"/scenes/{project['scenes'][0]['id']}/rewrite", body), field)
    assert balance(me.browser) == 100 - SCRIPT_COST
    assert waiting_jobs() == []


def test_one_rewrite_per_scene_at_a_time(app, database, outbox):
    """G-12: one active job per scene and job type; a duplicate is not charged."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    first, second = scene_ids(project)[:2]
    assert rewrite(me.browser, first).status_code == 202
    before = balance(me.browser)
    assert_error(rewrite(me.browser, first), 409, "already_running")
    assert balance(me.browser) == before
    assert rewrite(me.browser, second).status_code == 202, "another scene is fine"
    run_jobs(app)
    assert rewrite(me.browser, first).status_code == 202, "possible again once finished"
    run_jobs(app)
    assert_ledger_matches_balance(database, me.id)


def test_an_edit_during_a_rewrite_wins_and_the_rewrite_is_refunded(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    scene_id = project["scenes"][0]["id"]
    job = rewrite(me.browser, scene_id).get_json()["job"]
    assert patch_scene(me.browser, scene_id, {"narration": "My own edit."}).status_code == 200
    run_jobs(app)
    job = get_job(me.browser, job["id"])
    assert job["state"] == "failed" and job["creditsRefunded"] is True
    assert get_project(me.browser, project["id"])["scenes"][0]["narration"] == "My own edit."
    assert balance(me.browser) == 100 - SCRIPT_COST
    assert_ledger_matches_balance(database, me.id)


def test_deleting_the_scene_cancels_its_rewrite_and_refunds_it(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    scene_id = project["scenes"][0]["id"]
    job = rewrite(me.browser, scene_id).get_json()["job"]
    assert me.browser.delete(f"/scenes/{scene_id}").status_code == 204
    assert balance(me.browser) == 100 - SCRIPT_COST
    run_jobs(app)
    assert balance(me.browser) == 100 - SCRIPT_COST
    assert len(get_project(me.browser, project["id"])["scenes"]) == len(project["scenes"]) - 1
    assert job_ledger(database, job["id"]) == [("reserve", -SCRIPT_COST), ("refund", SCRIPT_COST)]
    refunds = [
        r
        for r in database.rows(
            "SELECT amount FROM credit_ledger WHERE user_id = :u AND kind = 'refund'", u=me.id
        )
    ]
    assert refunds == [{"amount": SCRIPT_COST}], "refunded exactly once"
    assert_ledger_matches_balance(database, me.id)


def test_a_failed_rewrite_changes_nothing_and_is_refunded(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    scene = project["scenes"][0]
    job = rewrite(me.browser, scene["id"], "shorter [fail]").get_json()["job"]
    run_jobs(app)
    job = get_job(me.browser, job["id"])
    assert job["state"] == "failed" and job["creditsRefunded"] is True
    assert get_project(me.browser, project["id"])["scenes"][0] == scene
    assert balance(me.browser) == 100 - SCRIPT_COST


def test_a_rewrite_needs_a_credit(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    set_balance(database, me.id, 0)
    error = assert_error(rewrite(me.browser, project["scenes"][0]["id"]), 402, "insufficient_credits")
    assert error["details"]["needed"] == SCRIPT_COST and error["details"]["balance"] == 0
    assert waiting_jobs() == []


# ---------------------------------------------------------------- B8


def regenerate(browser, project_id: str, body: dict | None = None):
    return browser.post(f"/projects/{project_id}/script/regenerate", body)


def test_regenerating_replaces_the_scenes_and_keeps_title_mood_and_caption(app, database, outbox, prompts):
    me = creator(app)
    project = scripted_project(app, me.browser)
    me.browser.patch(
        f"/projects/{project['id']}",
        {"title": "My title", "musicMood": "sad", "postCaption": "My caption", "hashtags": ["mine"]},
    )
    before = balance(me.browser)
    res = regenerate(me.browser, project["id"], {"instructions": "make it funnier"})
    assert res.status_code == 202, res.get_data(as_text=True)
    assert set(res.get_json()) == {"job"}
    job = res.get_json()["job"]
    assert job["type"] == "script" and job["creditsReserved"] == SCRIPT_COST
    assert balance(me.browser) == before - SCRIPT_COST
    during = get_project(me.browser, project["id"])
    assert during["status"] == "scripting" and isinstance(during["progress"], int)
    assert scene_ids(during) == scene_ids(project), "the old scenes stay until the new ones arrive"

    prompts.clear()
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "succeeded"
    after = get_project(me.browser, project["id"])
    assert after["status"] == "draft"
    assert after["scenes"] and not set(scene_ids(after)) & set(scene_ids(project))
    assert_positions_are_1_to_n(after)
    assert (after["title"], after["musicMood"], after["postCaption"], after["hashtags"]) == (
        "My title",
        "sad",
        "My caption",
        ["mine"],
    )
    assert "make it funnier" in prompts[0]["prompt"]
    assert project["topic"] in prompts[0]["prompt"]
    assert_ledger_matches_balance(database, me.id)


def test_regenerating_without_a_body_or_instructions_works(app, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    assert regenerate(me.browser, project["id"]).status_code == 202
    run_jobs(app)
    assert regenerate(me.browser, project["id"], {}).status_code == 202
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "draft"


@pytest.mark.parametrize(
    "body", [{"instructions": "i" * 201}, {"instructions": 5}, {"topic": "new"}, {"instructions": None}]
)
def test_regenerate_refuses_invalid_bodies(app, outbox, body):
    me = creator(app)
    project = scripted_project(app, me.browser)
    assert_error(regenerate(me.browser, project["id"], body), 400, "validation_error")
    assert get_project(me.browser, project["id"])["status"] == "draft"


def test_one_script_job_per_project_at_a_time(app, database, outbox):
    """G-12: the second request is 409 and charges nothing."""
    me = creator(app)
    project, _ = start_project(me.browser)
    assert_error(regenerate(me.browser, project["id"]), 409, "already_running")
    assert balance(me.browser) == 100 - SCRIPT_COST
    run_jobs(app)
    assert regenerate(me.browser, project["id"]).status_code == 202
    assert_error(regenerate(me.browser, project["id"]), 409, "already_running")
    assert balance(me.browser) == 100 - 2 * SCRIPT_COST
    assert len(waiting_jobs()) == 1
    run_jobs(app)
    assert_ledger_matches_balance(database, me.id)


@pytest.mark.parametrize("status", ["rendering", "posting"])
def test_regenerating_waits_for_a_render_or_post(app, database, outbox, status):
    me = creator(app)
    project = scripted_project(app, me.browser)
    set_statuses(database, project["id"], status=status)
    assert_error(regenerate(me.browser, project["id"]), 409, "already_running")
    assert balance(me.browser) == 100 - SCRIPT_COST
    assert get_project(me.browser, project["id"])["status"] == status


@pytest.mark.parametrize("status", ["draft", "ready", "rendered", "posted", "needs_attention"])
def test_a_failed_regeneration_restores_the_status_and_leaves_the_scenes(app, database, outbox, status):
    me = creator(app)
    project = scripted_project(app, me.browser)
    set_statuses(database, project["id"], voice="ready", visual="ready", status=status)
    project = get_project(me.browser, project["id"])
    job = regenerate(me.browser, project["id"], {"instructions": "funnier [fail]"}).get_json()["job"]
    run_jobs(app)
    job = get_job(me.browser, job["id"])
    assert job["state"] == "failed" and job["creditsRefunded"] is True
    assert without_time(get_project(me.browser, project["id"])) == without_time(project)
    assert_ledger_matches_balance(database, me.id)


def test_cancelling_a_regeneration_restores_the_status_and_refunds(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    set_statuses(database, project["id"], voice="ready", visual="ready", status="rendered")
    project = get_project(me.browser, project["id"])
    job = regenerate(me.browser, project["id"]).get_json()["job"]
    res = me.browser.post(f"/jobs/{job['id']}/cancel")
    assert res.status_code == 200
    assert res.get_json()["job"]["state"] == "cancelled" and res.get_json()["job"]["creditsRefunded"] is True
    run_jobs(app)
    assert without_time(get_project(me.browser, project["id"])) == without_time(project)
    assert_ledger_matches_balance(database, me.id)


def test_regenerating_cancels_and_refunds_work_running_on_the_old_scenes(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    rewrite_job = rewrite(me.browser, project["scenes"][0]["id"]).get_json()["job"]
    script_job = regenerate(me.browser, project["id"]).get_json()["job"]
    # The script is written first; the rewrite then finds its scene gone.
    from reelforge_api import jobs

    jobs.manual_queue.sort(key=lambda job_id: job_id != script_job["id"])
    run_jobs(app)
    assert get_job(me.browser, script_job["id"])["state"] == "succeeded"
    refunds = database.rows(
        "SELECT amount FROM credit_ledger WHERE user_id = :u AND kind = 'refund'", u=me.id
    )
    assert refunds == [{"amount": SCRIPT_COST}], f"the rewrite {rewrite_job['id']} is refunded once"
    assert balance(me.browser) == 100 - 2 * SCRIPT_COST
    assert_ledger_matches_balance(database, me.id)


def test_regenerating_a_generated_project_releases_the_old_files(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    voice = new_asset(database, me.id, project["id"])
    database.run(
        "UPDATE scenes SET voice_asset_id = :a, voice_status = 'ready' WHERE id = :s",
        a=voice,
        s=project["scenes"][0]["id"],
    )
    job = regenerate(me.browser, project["id"]).get_json()["job"]
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "succeeded"
    assert not set(scene_ids(get_project(me.browser, project["id"]))) & set(scene_ids(project))
    assert database.value("SELECT replaced_at FROM assets WHERE id = :a", a=voice) is not None


def test_a_pasted_script_can_be_rewritten_by_the_model(app, outbox, prompts):
    me = creator(app)
    script = "My own first sentence. My own second sentence."
    project = scripted_project(app, me.browser, script=script)
    regenerate(me.browser, project["id"])
    prompts.clear()
    run_jobs(app)
    assert prompts[0]["kind"] == "script"
    assert script in prompts[0]["prompt"], "the pasted text is the material"
    assert get_project(me.browser, project["id"])["status"] == "draft"


# ---------------------------------------------------------------- B9


def test_hooks_are_three_free_opening_lines(app, database, outbox, prompts):
    me = creator(app)
    project = scripted_project(app, me.browser)
    before = balance(me.browser)
    res = me.browser.post(f"/projects/{project['id']}/hooks")
    assert res.status_code == 202, res.get_data(as_text=True)
    job = res.get_json()["job"]
    assert job["type"] == "hooks" and job["creditsReserved"] == 0 and job["projectId"] == project["id"]
    prompts.clear()
    run_jobs(app)
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded"
    hooks = done["output"]["hooks"]
    assert len(hooks) == 3 and all(isinstance(h, str) and h for h in hooks)
    assert project["scenes"][0]["narration"] in prompts[0]["prompt"]
    assert balance(me.browser) == before
    assert get_project(me.browser, project["id"]) == project, "choosing a hook is an ordinary edit"


def test_hooks_need_a_script(app, database, outbox):
    me = creator(app)
    project, _ = start_project(me.browser, topic="habits [fail]")
    assert_error(me.browser.post(f"/projects/{project['id']}/hooks"), 409, "already_running")
    run_jobs(app)
    assert_error(me.browser.post(f"/projects/{project['id']}/hooks"), 409, "no_script")


# ---------------------------------------------------------------- after the B3 review fixes


def test_write_script_after_a_failed_first_attempt_runs_as_the_first_attempt(
    app, database, outbox, monkeypatch
):
    """B3/B8: a pasted script is prepared again (words kept) and the AI names the project."""
    script = "My own first sentence. My own second sentence.\n\nA new paragraph."
    calls: list[str] = []
    real = MockLLM.generate_json

    def failing_once(self, prompt, kind, context):
        calls.append(kind)
        if len(calls) == 1:
            from reelforge_api.providers.base import ProviderError

            raise ProviderError("down")
        return real(self, prompt, kind, context)

    monkeypatch.setattr(MockLLM, "generate_json", failing_once)
    me = creator(app)
    project, job = start_project(me.browser, script=script)
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "failed"
    assert regenerate(me.browser, project["id"]).status_code == 202
    run_jobs(app)
    after = get_project(me.browser, project["id"])
    assert calls[-1] == "annotate_script", "prepared again, not rewritten"
    assert [w for s in after["scenes"] for w in s["narration"].split()] == script.split()
    assert after["title"] and after["title"] != "Untitled Reel"


def test_every_language_model_job_records_its_prompt_version(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    me.browser.post("/ideas", {"niche": "fitness"})
    me.browser.post(f"/projects/{project['id']}/hooks")
    me.browser.post(f"/scenes/{project['scenes'][0]['id']}/rewrite", {"instructions": "x"})
    regenerate(me.browser, project["id"])
    rows = database.rows("SELECT type, input->>'promptVersion' AS v FROM jobs WHERE user_id = :u", u=me.id)
    assert {r["type"] for r in rows} == {"ideas", "script", "hooks", "rewrite_scene"}
    assert all(r["v"] for r in rows), rows
    run_jobs(app)


def test_every_scene_edit_moves_the_project_to_the_top_of_the_list(app, database, outbox):
    """Lists are sorted by updatedAt; editing a scene is editing the project."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    other = scripted_project(app, me.browser)
    ids = scene_ids(project)

    def top() -> str:
        return me.browser.get("/projects").get_json()["items"][0]["id"]

    for action in (
        lambda: patch_scene(me.browser, ids[0], {"onScreenText": "New"}),
        lambda: add_scene(me.browser, project["id"]),
        lambda: me.browser.post(f"/scenes/{ids[1]}/duplicate"),
        lambda: reorder(me.browser, project["id"], scene_ids(get_project(me.browser, project["id"]))[::-1]),
        lambda: me.browser.delete(f"/scenes/{ids[2]}"),
    ):
        database.run("UPDATE projects SET title = title WHERE id = :p", p=other["id"])
        assert top() == other["id"]
        assert action().status_code in (200, 201, 204)
        assert top() == project["id"]


def test_a_deleted_scenes_cancelled_rewrite_can_still_be_read(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    scene_id = project["scenes"][0]["id"]
    job = rewrite(me.browser, scene_id).get_json()["job"]
    assert me.browser.delete(f"/scenes/{scene_id}").status_code == 204
    found = get_job(me.browser, job["id"])
    assert found["state"] == "cancelled" and found["creditsRefunded"] is True
    assert job_ledger(database, job["id"]) == [("reserve", -SCRIPT_COST), ("refund", SCRIPT_COST)]
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "cancelled"


def test_duplicate_and_reorder_answer_with_the_new_estimate(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    est = me.browser.post(f"/scenes/{project['scenes'][0]['id']}/duplicate").get_json()["estimate"]
    assert est == me.browser.get(f"/projects/{project['id']}/estimate").get_json()
    ids = scene_ids(get_project(me.browser, project["id"]))
    est = reorder(me.browser, project["id"], ids[::-1]).get_json()["estimate"]
    assert est == me.browser.get(f"/projects/{project['id']}/estimate").get_json()
