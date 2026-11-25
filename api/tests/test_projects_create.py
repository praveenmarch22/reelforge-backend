"""B1 ideas and B3 create project: the main flow, validation, credits and failures
(docs/API_DESIGN.md B1, B3; PRD 5.2, S-1, S-4, S-6, CR-1, CR-2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from reelforge_api.script_rules import SCENES_MAX

from .projects_support import (
    ISO_UTC,
    JOB_FIELDS,
    MOODS,
    PROJECT_FIELDS,
    SCENE_FIELDS,
    SCRIPT_COST,
    TOPIC,
    assert_ledger_matches_balance,
    balance,
    creator,
    get_job,
    get_project,
    job_ledger,
    ledger_rows,
    run_jobs,
    scripted_project,
    set_balance,
    start_project,
    waiting_jobs,
)
from .support import assert_error, assert_invalid


def count(database, table: str, user_id: str) -> int:
    return database.value(f"SELECT count(*) FROM {table} WHERE user_id = :u", u=user_id)  # noqa: S608


# ---------------------------------------------------------------- B3: the main flow


def test_creating_a_project_reserves_a_credit_and_writes_the_script(app, database, outbox):
    me = creator(app)
    res = me.browser.post("/projects", {"topic": TOPIC, "tone": "funny", "targetSeconds": 60})
    assert res.status_code == 202, res.get_data(as_text=True)
    body = res.get_json()
    assert set(body) == {"project", "job"}
    project, job = body["project"], body["job"]

    assert set(project) == PROJECT_FIELDS
    assert project["status"] == "scripting"
    assert project["topic"] == TOPIC
    assert project["tone"] == "funny" and project["targetSeconds"] == 60
    assert project["mode"] == "faceless"  # from the profile
    assert project["scenes"] == []
    assert project["progress"] == 0
    assert [j["id"] for j in project["activeJobs"]] == [job["id"]]
    assert set(job) == JOB_FIELDS
    assert job["type"] == "script" and job["state"] == "queued"
    assert job["projectId"] == project["id"] and job["sceneId"] is None
    assert job["creditsReserved"] == SCRIPT_COST and job["creditsRefunded"] is False
    assert ISO_UTC.match(job["queuedAt"]) and job["finishedAt"] is None
    assert ISO_UTC.match(project["createdAt"]) and ISO_UTC.match(project["updatedAt"])
    created = datetime.strptime(project["createdAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert abs(datetime.now(UTC) - created) < timedelta(minutes=1), "times are UTC"

    # Reserved at once, in the ledger, with the job.
    assert balance(me.browser) == 100 - SCRIPT_COST
    assert job_ledger(database, job["id"]) == [("reserve", -SCRIPT_COST)]
    assert waiting_jobs() == [job["id"]]

    assert run_jobs(app) == 1
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["progress"] == 100
    assert done["provider"] == "mock" and done["error"] is None
    assert ISO_UTC.match(done["finishedAt"])

    project = get_project(me.browser, project["id"])
    assert project["status"] == "draft"
    assert project["activeJobs"] == [] and project["progress"] is None
    assert project["title"], "the first script names the project"
    assert project["postCaption"] and project["hashtags"]
    assert project["musicMood"] in MOODS
    assert len(project["scenes"]) >= 1
    for position, scene in enumerate(project["scenes"], start=1):
        assert set(scene) == SCENE_FIELDS
        assert scene["position"] == position
        assert scene["narration"]
        assert scene["mood"] in MOODS
        assert scene["visualSource"] == "stock"  # faceless
        assert scene["voiceStatus"] == "missing" and scene["visualStatus"] == "missing"
        assert scene["voice"] is None and scene["visual"] is None
        assert scene["words"] == [] and scene["wordsEdited"] is False
        assert scene["trimStart"] == 0 and scene["trimEnd"] is None and scene["lastError"] is None
        assert scene["estimatedSeconds"] == round(len(scene["narration"].split()) / 2.5, 1)
    words = sum(len(s["narration"].split()) for s in project["scenes"])
    assert project["wordCount"] == words
    assert project["estimatedSeconds"] == pytest.approx(sum(s["estimatedSeconds"] for s in project["scenes"]))
    # Success: the reservation becomes the charge, nothing is refunded.
    assert balance(me.browser) == 100 - SCRIPT_COST
    assert job_ledger(database, job["id"]) == [("reserve", -SCRIPT_COST)]
    assert_ledger_matches_balance(database, me.id)


def test_defaults_come_from_the_profile_and_the_settings(app, outbox):
    me = creator(app)
    res = me.browser.patch(
        "/me", {"defaults": {"voiceId": "Xb7hH8MSUJpSbSDYk0k2", "captionStyle": "neon", "mode": "full_ai"}}
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    project, _ = start_project(me.browser)
    assert project["voiceId"] == "Xb7hH8MSUJpSbSDYk0k2"
    assert project["mode"] == "full_ai"
    assert project["settings"]["captionStyle"] == "neon"
    assert project["tone"] == "educational" and project["targetSeconds"] == 30
    run_jobs(app)
    project = get_project(me.browser, project["id"])
    assert {s["visualSource"] for s in project["scenes"]} == {"ai_video"}, "Full AI scenes use AI video"

    chosen, _ = start_project(me.browser, voiceId="", mode="faceless")
    assert chosen["voiceId"] == "" and chosen["mode"] == "faceless", "what the body says wins"


@pytest.mark.parametrize("seconds", [15, 30, 60, 90])
@pytest.mark.parametrize("tone", ["educational", "funny", "motivational", "storytelling", "dramatic"])
def test_every_documented_tone_and_length_is_accepted(app, outbox, tone, seconds):
    me = creator(app)
    project, _ = start_project(me.browser, tone=tone, targetSeconds=seconds, mode="full_ai")
    assert (project["tone"], project["targetSeconds"], project["mode"]) == (tone, seconds, "full_ai")


# ---------------------------------------------------------------- B3: validation


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "topic"),
        ({"topic": TOPIC, "script": "My own words."}, "script"),
        ({"topic": None}, "topic"),
        ({"topic": None, "script": None}, "topic"),
        ({"topic": ""}, "topic"),
        ({"topic": "   "}, "topic"),
        ({"topic": "x" * 301}, "topic"),
        ({"topic": "two\nlines"}, "topic"),
        ({"topic": 42}, "topic"),
        ({"script": ""}, "script"),
        ({"script": " \n\n "}, "script"),
        ({"script": "x" * 5001}, "script"),
        ({"script": "nul \x00 here"}, "script"),
        ({"topic": TOPIC, "tone": "angry"}, "tone"),
        ({"topic": TOPIC, "tone": None}, "tone"),
        ({"topic": TOPIC, "mode": "faces"}, "mode"),
        ({"topic": TOPIC, "targetSeconds": 45}, "targetSeconds"),
        ({"topic": TOPIC, "targetSeconds": "30"}, "targetSeconds"),
        ({"topic": TOPIC, "targetSeconds": True}, "targetSeconds"),
        ({"topic": TOPIC, "style": "s" * 101}, "style"),
        ({"topic": TOPIC, "character": "c" * 301}, "character"),
        ({"topic": TOPIC, "voiceId": 7}, "voiceId"),
        ({"topic": TOPIC, "target_seconds": 30}, "target_seconds"),
        ({"topic": TOPIC, "credits": 0}, "credits"),
        ({"topic": TOPIC, "status": "posted"}, "status"),
        ({"topic": TOPIC, "userId": "00000000-0000-0000-0000-000000000000"}, "userId"),
    ],
)
def test_create_refuses_invalid_bodies_and_creates_nothing(app, database, outbox, body, field):
    """Exactly one of topic and script; enums, limits and strict types; camelCase only;
    unknown fields refused. Nothing is created or charged."""
    me = creator(app)
    assert_invalid(me.browser.post("/projects", body), field)
    assert count(database, "projects", me.id) == 0
    assert count(database, "jobs", me.id) == 0
    assert balance(me.browser) == 100
    assert waiting_jobs() == []


def test_a_body_that_is_not_json_is_refused(app, database, outbox):
    me = creator(app)
    res = me.browser.post("/projects", data="topic=habits", content_type="application/x-www-form-urlencoded")
    assert_error(res, 415, "unsupported_media_type")
    res = me.browser.post("/projects", data="[1, 2]", content_type="application/json")
    assert_error(res, 400, "validation_error")
    assert count(database, "projects", me.id) == 0


def test_limits_are_inclusive(app, outbox):
    me = creator(app)
    project, _ = start_project(me.browser, topic="t" * 300, style="s" * 100, character="c" * 300)
    assert project["topic"] == "t" * 300
    project, _ = start_project(me.browser, script="word " * 999 + "last.")  # 5,000 characters
    assert project["status"] == "scripting"


def test_text_is_trimmed(app, outbox):
    me = creator(app)
    project, _ = start_project(me.browser, topic="   Cold showers   ", style="  noir  ")
    assert project["topic"] == "Cold showers" and project["style"] == "noir"


# ---------------------------------------------------------------- B3: credits


def test_without_enough_credits_it_is_402_and_nothing_is_created(app, database, outbox):
    me = creator(app)
    set_balance(database, me.id, 0)
    res = me.browser.post("/projects", {"topic": TOPIC})
    error = assert_error(res, 402, "insufficient_credits")
    assert error["details"]["needed"] == SCRIPT_COST
    assert error["details"]["balance"] == 0
    assert count(database, "projects", me.id) == 0, "no project without its script job"
    assert count(database, "jobs", me.id) == 0
    assert ledger_rows(database, me.id, "reserve") == []
    assert waiting_jobs() == []
    assert_ledger_matches_balance(database, me.id)


def test_the_last_credit_can_be_spent(app, database, outbox):
    me = creator(app)
    set_balance(database, me.id, SCRIPT_COST)
    start_project(me.browser)
    assert balance(me.browser) == 0
    assert_error(me.browser.post("/projects", {"topic": TOPIC}), 402, "insufficient_credits")
    assert_ledger_matches_balance(database, me.id)


@pytest.mark.parametrize(
    ("marker", "code"),
    [("[fail]", "provider_failed"), ("[invalid]", "invalid_output"), ("[refuse]", "content_refused")],
)
def test_when_writing_fails_the_credit_is_refunded_once_and_the_project_stays_a_draft(
    app, database, outbox, marker, code
):
    """B3: the job fails with its error, the credit comes back, the project is a draft with
    no scenes. S-1: unusable output is not charged. S-4: a refusal is a neutral message."""
    me = creator(app)
    project, job = start_project(me.browser, topic=f"habits {marker}")
    run_jobs(app)
    job = get_job(me.browser, job["id"])
    assert job["state"] == "failed"
    assert job["error"]["code"] in (code, "provider_failed" if code == "invalid_output" else code)
    assert job["error"]["message"] and "mock" not in job["error"]["message"].lower()
    assert job["creditsRefunded"] is True
    assert job["output"] is None
    project = get_project(me.browser, project["id"])
    assert project["status"] == "draft" and project["scenes"] == [] and project["activeJobs"] == []
    assert balance(me.browser) == 100
    assert job_ledger(database, job["id"]) == [("reserve", -SCRIPT_COST), ("refund", SCRIPT_COST)]
    assert_ledger_matches_balance(database, me.id)

    # Write script (B8) tries again.
    res = me.browser.post(f"/projects/{project['id']}/script/regenerate", {})
    assert res.status_code == 202


def test_a_refusal_is_reported_as_content_refused(app, outbox):
    me = creator(app)
    _, job = start_project(me.browser, topic="something [refuse]")
    run_jobs(app)
    assert get_job(me.browser, job["id"])["error"]["code"] == "content_refused"


def test_unusable_output_is_asked_again_before_failing(app, outbox, monkeypatch):
    """S-1: validate the answer, retry up to 2 times, then fail."""
    from reelforge_api.providers.llm import MockLLM

    calls = []
    real = MockLLM.generate_json

    def counted(self, prompt, kind, context):
        calls.append(kind)
        return real(self, prompt, kind, context)

    monkeypatch.setattr(MockLLM, "generate_json", counted)
    me = creator(app)
    _, job = start_project(me.browser, topic="habits [invalid]")
    run_jobs(app)
    assert calls == ["script"] * 3
    assert get_job(me.browser, job["id"])["state"] == "failed"


def test_a_job_that_crashes_is_failed_and_refunded_without_details(app, database, outbox, monkeypatch):
    from reelforge_api.providers.llm import MockLLM

    def crash(self, prompt, kind, context):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(MockLLM, "generate_json", crash)
    me = creator(app)
    project, job = start_project(me.browser)
    run_jobs(app)
    job = get_job(me.browser, job["id"])
    assert job["state"] == "failed" and job["creditsRefunded"] is True
    assert "secret internal detail" not in str(job["error"])
    assert get_project(me.browser, project["id"])["status"] == "draft"
    assert_ledger_matches_balance(database, me.id)


# ---------------------------------------------------------------- B3: no language model


def switch_off(database, *providers: str) -> None:
    for provider in providers:
        database.run(
            "INSERT INTO provider_switches (provider, enabled, reason) VALUES (:p, false, 'test') "
            "ON CONFLICT (provider) DO UPDATE SET enabled = false",
            p=provider,
        )


def test_with_every_language_model_switched_off_it_is_503_and_nothing_is_charged(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    scene_id = project["scenes"][0]["id"]
    switch_off(database, "mock")
    before = balance(me.browser)
    for path, body in (
        ("/projects", {"topic": TOPIC}),
        ("/ideas", {"niche": "fitness"}),
        (f"/projects/{project['id']}/script/regenerate", {}),
        (f"/projects/{project['id']}/hooks", None),
        (f"/scenes/{scene_id}/rewrite", {"instructions": "shorter"}),
    ):
        assert_error(me.browser.post(path, body), 503, "provider_unavailable")
    assert balance(me.browser) == before
    assert count(database, "projects", me.id) == 1
    assert count(database, "jobs", me.id) == 1
    assert get_project(me.browser, project["id"])["status"] == "draft", "regenerate changed nothing"
    assert waiting_jobs() == []


def test_switching_a_model_off_after_the_job_was_queued_fails_it_and_refunds(app, database, outbox):
    me = creator(app)
    project, job = start_project(me.browser)
    switch_off(database, "mock")
    run_jobs(app)
    job = get_job(me.browser, job["id"])
    assert job["state"] == "failed" and job["error"]["code"] == "provider_unavailable"
    assert job["creditsRefunded"] is True and balance(me.browser) == 100
    assert get_project(me.browser, project["id"])["status"] == "draft"


# ---------------------------------------------------------------- B3: a pasted script


def words_of(project: dict) -> list[str]:
    return [w for s in project["scenes"] for w in s["narration"].split()]


def pasted(app, browser, script: str) -> dict:
    return scripted_project(app, browser, script=script)


def test_a_pasted_script_keeps_every_word_and_is_split_at_sentence_ends(app, outbox):
    sentences = [f"Sentence number {n} has exactly seven words." for n in range(1, 13)]
    script = " ".join(sentences)
    me = creator(app)
    project = pasted(app, me.browser, script)
    assert words_of(project) == script.split()
    assert project["topic"] == ""
    assert project["title"], "the model adds the title"
    assert len(project["scenes"]) > 1, "84 words are more than one scene"
    for scene in project["scenes"]:
        assert scene["narration"].endswith("words."), "scenes end at a sentence end"
        assert len(scene["narration"].split()) <= 30
        assert scene["onScreenText"] is not None and scene["mood"] in MOODS
        assert scene["visualPrompt"], "the model describes the visuals"


def test_a_paragraph_break_always_starts_a_new_scene(app, outbox):
    script = "Short one. Still short.\n\nNew paragraph here!\r\n\r\nAnd a third? Yes.\n \nFourth."
    me = creator(app)
    project = pasted(app, me.browser, script)
    assert [s["narration"] for s in project["scenes"]] == [
        "Short one. Still short.",
        "New paragraph here!",
        "And a third? Yes.",
        "Fourth.",
    ]


def test_single_line_breaks_and_odd_spacing_do_not_change_words(app, outbox):
    script = "Line one continues\non the next line.   Then   another sentence…  Done!"
    me = creator(app)
    project = pasted(app, me.browser, script)
    assert words_of(project) == script.split()


def test_a_very_long_sentence_is_cut_between_words_within_the_narration_limit(app, outbox):
    script = " ".join(f"word{n}" for n in range(638))  # one sentence, just under 5,000 characters
    assert len(script) <= 5000
    me = creator(app)
    project = pasted(app, me.browser, script)
    assert words_of(project) == script.split()
    assert len(project["scenes"]) > 1
    assert all(1 <= len(s["narration"]) <= 1500 for s in project["scenes"])


def test_a_5000_character_script_with_many_paragraphs_stays_within_30_scenes(app, outbox):
    paragraphs = [f"Paragraph {n} says one short thing." for n in range(1, 200)]
    script = "\n\n".join(paragraphs)[:5000].rsplit("\n\n", 1)[0]
    me = creator(app)
    project = pasted(app, me.browser, script)
    assert 1 <= len(project["scenes"]) <= SCENES_MAX
    assert words_of(project) == script.split(), "merged, never dropped or reordered"
    assert all(len(s["narration"]) <= 1500 for s in project["scenes"])


def test_unicode_and_punctuation_in_a_pasted_script_survive(app, outbox):
    script = "Café crème? Oui! « Ça va » — 100% sure… Emoji 💪 stays. Hindi: नमस्ते दुनिया."
    me = creator(app)
    project = pasted(app, me.browser, script)
    assert words_of(project) == script.split()


def test_the_model_cannot_change_a_pasted_script(app, outbox, monkeypatch):
    """The model only describes the scenes: even if it answers with other narration, the
    creator's words are kept."""
    from reelforge_api.providers.llm import MockLLM

    real = MockLLM._annotate_script

    def rewriting(self, c):
        answer = real(self, c)
        for scene in answer["scenes"]:
            scene["narration"] = "Totally different words from the model."
        return answer

    monkeypatch.setattr(MockLLM, "_annotate_script", rewriting)
    me = creator(app)
    script = "My exact words. Do not touch them."
    project = pasted(app, me.browser, script)
    assert words_of(project) == script.split()


# ---------------------------------------------------------------- B1: ideas


def test_ideas_are_free_and_arrive_on_the_job(app, database, outbox):
    me = creator(app)
    res = me.browser.post("/ideas", {"niche": "fitness", "keywords": "morning, habits", "count": 3})
    assert res.status_code == 202, res.get_data(as_text=True)
    body = res.get_json()
    assert set(body) == {"job"}
    job = body["job"]
    assert set(job) == JOB_FIELDS
    assert job["type"] == "ideas" and job["projectId"] is None and job["sceneId"] is None
    assert job["creditsReserved"] == 0 and job["state"] == "queued"
    run_jobs(app)
    job = get_job(me.browser, job["id"])
    assert job["state"] == "succeeded"
    ideas = job["output"]["ideas"]
    assert len(ideas) == 3
    for idea in ideas:
        assert set(idea) == {"title", "hook", "angle"}
        assert idea["title"] and idea["hook"]
    assert balance(me.browser) == 100
    assert ledger_rows(database, me.id, "reserve") == []


def test_ideas_default_to_five_and_work_with_no_credits(app, database, outbox):
    me = creator(app)
    set_balance(database, me.id, 0)
    res = me.browser.post("/ideas", {"niche": "cooking"})
    assert res.status_code == 202
    run_jobs(app)
    assert len(get_job(me.browser, res.get_json()["job"]["id"])["output"]["ideas"]) == 5


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "niche"),
        ({"niche": ""}, "niche"),
        ({"niche": "  "}, "niche"),
        ({"niche": None}, "niche"),
        ({"niche": "n" * 121}, "niche"),
        ({"niche": "fitness", "count": 0}, "count"),
        ({"niche": "fitness", "count": 11}, "count"),
        ({"niche": "fitness", "count": "5"}, "count"),
        ({"niche": "fitness", "count": 2.0}, "count"),
        ({"niche": "fitness", "keywords": "k" * 201}, "keywords"),
        ({"niche": "fitness", "keywords": None}, "keywords"),
        ({"niche": "fitness", "topic": "x"}, "topic"),
    ],
)
def test_ideas_refuse_invalid_bodies(app, database, outbox, body, field):
    me = creator(app)
    assert_invalid(me.browser.post("/ideas", body), field)
    assert count(database, "jobs", me.id) == 0


def test_ideas_accept_the_count_limits(app, outbox):
    me = creator(app)
    for n in (1, 10):
        res = me.browser.post("/ideas", {"niche": "fitness", "count": n})
        assert res.status_code == 202
    run_jobs(app)


def test_a_refused_niche_fails_the_ideas_job(app, outbox):
    me = creator(app)
    res = me.browser.post("/ideas", {"niche": "fitness [refuse]"})
    run_jobs(app)
    job = get_job(me.browser, res.get_json()["job"]["id"])
    assert job["state"] == "failed" and job["error"]["code"] == "content_refused"
    assert job["output"] is None
