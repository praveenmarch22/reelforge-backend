"""B2 list, B4 get, B5 update, B6 delete, B7 duplicate, B10 estimate and the purge of deleted
projects (docs/API_DESIGN.md group B; DB_DESIGN.md 5, 8; PRD S-7, G-14)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from reelforge_api.script_rules import estimated_seconds, visual_credits, voice_credits
from reelforge_api.tasks.accounts import purge_deleted

from .projects_support import (
    SCRIPT_COST,
    STATUSES,
    SUMMARY_FIELDS,
    TOPIC,
    assert_ledger_matches_balance,
    balance,
    creator,
    get_job,
    get_project,
    job_ledger,
    new_asset,
    project_row,
    run_jobs,
    scene_ids,
    scene_row,
    scripted_project,
    set_statuses,
    start_project,
    unsigned,
)
from .support import assert_error, assert_invalid

# ---------------------------------------------------------------- B2: the list


def insert_project(
    database,
    user_id: str,
    title: str,
    *,
    status: str = "draft",
    minutes_ago: float = 0,
    deleted: bool = False,
    at: datetime | None = None,
) -> str:
    """A project row as it would be at `at` (an UPDATE would reset updated_at to now)."""
    project_id = str(uuid.uuid4())
    moment = at or datetime.now(UTC) - timedelta(minutes=minutes_ago)
    database.run(
        "INSERT INTO projects (id, user_id, title, status, created_at, updated_at, deleted_at) "
        "VALUES (:id, :u, :t, :s, :m, :m, :d)",
        id=project_id,
        u=user_id,
        t=title,
        s=status,
        m=moment,
        d=moment if deleted else None,
    )
    return project_id


def listed(browser, query: str = "") -> dict:
    res = browser.get(f"/projects{query}")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()


def test_the_list_is_newest_first_and_pages_through_everything_once(app, database, outbox):
    me = creator(app)
    made = [insert_project(database, me.id, f"Reel {n}", minutes_ago=50 - n) for n in range(45)]
    # Five with exactly the same time: the order must still be stable across pages.
    same = datetime.now(UTC) - timedelta(hours=2)
    tied = [insert_project(database, me.id, f"Tied {n}", at=same) for n in range(5)]

    seen: list[str] = []
    cursor, pages = None, 0
    while True:
        page = listed(me.browser, f"?limit=7{'&cursor=' + cursor if cursor else ''}")
        assert set(page) == {"items", "nextCursor", "counts"}
        assert len(page["items"]) <= 7
        seen += [item["id"] for item in page["items"]]
        pages += 1
        cursor = page["nextCursor"]
        if cursor is None:
            break
        assert pages < 20
    assert len(seen) == len(set(seen)) == 50
    assert seen[:45] == list(reversed(made)), "newest first"
    assert set(seen[45:]) == set(tied)


def test_the_list_defaults_to_20_and_allows_up_to_100(app, database, outbox):
    me = creator(app)
    for n in range(101):
        insert_project(database, me.id, f"Reel {n}", minutes_ago=n)
    assert len(listed(me.browser)["items"]) == 20
    page = listed(me.browser, "?limit=100")
    assert len(page["items"]) == 100 and page["nextCursor"]
    assert len(listed(me.browser, f"?limit=100&cursor={page['nextCursor']}")["items"]) == 1


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ("?limit=0", "limit"),
        ("?limit=101", "limit"),
        ("?limit=abc", "limit"),
        ("?limit=-1", "limit"),
        ("?cursor=not-a-cursor", "cursor"),
        ("?cursor=%E2%9C%93", "cursor"),
        ("?status=done", "status"),
        ("?status=draft,nope", "status"),
        ("?q=" + "q" * 101, "q"),
    ],
)
def test_bad_list_parameters_are_400(app, outbox, query, field):
    me = creator(app)
    assert_invalid(me.browser.get(f"/projects{query}"), field)


def test_status_filters_and_counts_that_ignore_them(app, database, outbox):
    me = creator(app)
    for status in STATUSES:
        insert_project(database, me.id, f"A {status}", status=status)
    insert_project(database, me.id, "Another posted", status="posted")
    insert_project(database, me.id, "Gone posted", status="posted", deleted=True)
    other = creator(app)
    insert_project(database, other.id, "Not mine", status="posted")

    everything = listed(me.browser)
    assert everything["counts"] == {"total": 12, "posted": 2}
    assert {item["title"] for item in everything["items"]} == {f"A {s}" for s in STATUSES} | {
        "Another posted"
    }

    one = listed(me.browser, "?status=posted")
    assert {item["title"] for item in one["items"]} == {"A posted", "Another posted"}
    assert one["counts"] == {"total": 12, "posted": 2}, "counts ignore the filters"

    failed = listed(me.browser, "?status=render_failed,post_failed&q=A")
    assert {item["status"] for item in failed["items"]} == {"render_failed", "post_failed"}
    assert failed["counts"] == {"total": 12, "posted": 2}

    for item in everything["items"]:
        assert set(item) == SUMMARY_FIELDS
        assert item["durationSeconds"] is None and item["thumbnailUrl"] is None
        if item["status"] in ("scripting", "generating", "rendering", "posting"):
            assert isinstance(item["progress"], int) and 0 <= item["progress"] <= 100
        else:
            assert item["progress"] is None, item


def test_title_search_is_case_insensitive_and_treats_wildcards_as_text(app, database, outbox):
    me = creator(app)
    for title in (
        "100% Protein",
        "1000 Protein bars",
        "snake_case tips",
        "snakeXcase tips",
        "Back\\slash",
        "Morning HABITS",
    ):
        insert_project(database, me.id, title)
    titles = lambda q: {i["title"] for i in listed(me.browser, f"?q={q}")["items"]}  # noqa: E731
    assert titles("habits") == {"Morning HABITS"}
    assert titles("100%25") == {"100% Protein"}, "% is a character, not a wildcard"
    assert titles("snake_") == {"snake_case tips"}, "_ is a character, not a wildcard"
    assert titles("%25") == {"100% Protein"}
    assert titles("_") == {"snake_case tips"}
    assert titles("k%5Cs") == {"Back\\slash"}
    assert titles("nothing-like-this") == set()
    assert listed(me.browser, "?q=%25")["counts"]["total"] == 6


def test_the_list_never_shows_other_users_or_deleted_projects(app, database, outbox):
    me, other = creator(app), creator(app)
    insert_project(database, other.id, "Theirs")
    insert_project(database, me.id, "Deleted", deleted=True)
    page = listed(me.browser)
    assert page["items"] == [] and page["counts"] == {"total": 0, "posted": 0}
    assert page["nextCursor"] is None


def test_a_project_being_written_shows_its_progress_in_the_list(app, outbox):
    me = creator(app)
    project, _ = start_project(me.browser)
    item = listed(me.browser)["items"][0]
    assert item["id"] == project["id"] and item["status"] == "scripting" and item["progress"] == 0
    run_jobs(app)
    item = listed(me.browser)["items"][0]
    assert item["status"] == "draft" and item["progress"] is None


def test_generating_progress_counts_ready_voices_and_visuals(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    n = len(project["scenes"])
    set_statuses(database, project["id"], voice="ready", status="generating")
    assert get_project(me.browser, project["id"])["progress"] == 50
    assert listed(me.browser)["items"][0]["progress"] == 50
    database.run("UPDATE scenes SET visual_status = 'ready' WHERE id = :s", s=project["scenes"][0]["id"])
    assert get_project(me.browser, project["id"])["progress"] == round(100 * (n + 1) / (2 * n))


# ---------------------------------------------------------------- B4


def test_a_deleted_or_missing_project_is_404(app, database, outbox):
    me = creator(app)
    gone = insert_project(database, me.id, "Gone", deleted=True)
    for project_id in (gone, str(uuid.uuid4()), "not-a-uuid", "1"):
        assert_error(me.browser.get(f"/projects/{project_id}"), 404, "not_found")


# ---------------------------------------------------------------- B5


def patch(browser, project_id: str, body: dict):
    return browser.patch(f"/projects/{project_id}", body)


def test_patch_changes_only_what_is_sent_and_answers_with_the_estimate(app, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    res = patch(
        me.browser,
        project["id"],
        {
            "title": "  My Reel  ",
            "tone": "dramatic",
            "musicMood": "tense",
            "postCaption": "Line one\nLine two",
            "hashtags": ["#Fitness", "habits", "fitness", " ", "#morning"],
            "settings": {"musicVolume": 0.5, "transition": "fade"},
        },
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert set(body) == {"project", "estimate"}
    changed = body["project"]
    assert changed["title"] == "My Reel"
    assert changed["tone"] == "dramatic" and changed["musicMood"] == "tense"
    assert changed["postCaption"] == "Line one\nLine two"
    assert changed["hashtags"] == ["Fitness", "habits", "morning"], "# removed, repeats dropped"
    assert changed["settings"] == {**project["settings"], "musicVolume": 0.5, "transition": "fade"}
    for unchanged in ("topic", "style", "character", "voiceId", "music", "scenes", "mode", "targetSeconds"):
        assert changed[unchanged] == project[unchanged], unchanged
    assert set(body["estimate"]) == {"credits", "balance", "items"}
    assert get_project(me.browser, project["id"]) == changed

    # An empty body changes nothing.
    res = patch(me.browser, project["id"], {})
    assert res.status_code == 200
    assert res.get_json()["project"]["title"] == "My Reel"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"title": ""}, "title"),
        ({"title": "t" * 121}, "title"),
        ({"title": "a\nb"}, "title"),
        ({"tone": "sad"}, "tone"),
        ({"musicMood": "happy"}, "musicMood"),
        ({"style": "s" * 101}, "style"),
        ({"character": "c" * 301}, "character"),
        ({"voiceId": "v" * 101}, "voiceId"),
        ({"postCaption": "c" * 2201}, "postCaption"),
        ({"hashtags": [f"tag{n}" for n in range(31)]}, "hashtags"),
        ({"hashtags": ["two words"]}, "hashtags"),
        ({"hashtags": ["a#b"]}, "hashtags"),
        ({"hashtags": ["h" * 101]}, "hashtags"),
        ({"hashtags": "fitness"}, "hashtags"),
        ({"settings": {"voiceVolume": 1.5}}, "settings.voiceVolume"),
        ({"settings": {"musicVolume": -0.1}}, "settings.musicVolume"),
        ({"settings": {"captionStyle": "comic"}}, "settings.captionStyle"),
        ({"settings": {"transition": "wipe"}}, "settings.transition"),
        ({"settings": {"showCaptions": "yes"}}, "settings.showCaptions"),
        ({"settings": {"font": "serif"}}, "settings.font"),
        ({"settings": {"caption_style": "neon"}}, "settings.caption_style"),
        ({"music_mood": "calm"}, "music_mood"),
        ({"status": "posted"}, "status"),
        ({"mode": "full_ai"}, "mode"),
        ({"targetSeconds": 60}, "targetSeconds"),
        ({"topic": "new topic"}, "topic"),
        ({"timelineVersion": 9}, "timelineVersion"),
    ],
)
def test_patch_refuses_invalid_and_unknown_fields_and_changes_nothing(app, outbox, body, field):
    me = creator(app)
    project = scripted_project(app, me.browser)
    assert_invalid(patch(me.browser, project["id"], {"tone": "funny", **body}), field)
    assert get_project(me.browser, project["id"]) == project


def test_settings_accept_their_limits(app, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    settings = {
        "voiceVolume": 0,
        "musicVolume": 1,
        "duckedVolume": 0.0,
        "ducking": False,
        "showCaptions": False,
        "captionStyle": "clean",
        "transition": "slide",
    }
    res = patch(
        me.browser,
        project["id"],
        {"settings": settings, "postCaption": "c" * 2200, "hashtags": [f"t{n}" for n in range(30)]},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["project"]["settings"] == settings
    assert len(res.get_json()["project"]["hashtags"]) == 30


@pytest.mark.parametrize(
    "field",
    ["title", "tone", "style", "character", "voiceId", "musicMood", "postCaption", "hashtags", "settings"],
)
def test_null_is_refused_for_fields_that_always_have_a_value(app, outbox, field):
    """Only `music` can be null (no music chosen). The scene endpoint (C2) and this one's own
    `title` answer null with 400 Required; every other field must do the same rather than
    silently ignoring it."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    assert_invalid(patch(me.browser, project["id"], {field: None}), field)
    assert get_project(me.browser, project["id"]) == project


def test_music_can_be_a_library_track_an_own_upload_or_nothing(app, database, outbox):
    me, other = creator(app), creator(app)
    project = scripted_project(app, me.browser)
    track = str(uuid.uuid4())
    inactive = str(uuid.uuid4())
    for track_id, active in ((track, True), (inactive, False)):
        database.run(
            "INSERT INTO music_tracks (id, title, s3_key, duration_seconds, moods, license, is_active) "
            "VALUES (:id, 'Song', :key, 60, '{calm}', 'CC0', :a)",
            id=track_id,
            key=f"library/music/{track_id}.mp3",
            a=active,
        )
    mine = new_asset(database, me.id, project["id"], kind="music")
    theirs = new_asset(database, other.id, None, kind="music")
    voice = new_asset(database, me.id, project["id"], kind="voice")

    res = patch(me.browser, project["id"], {"music": {"trackId": track}})
    assert res.status_code == 200 and res.get_json()["project"]["music"] == {"trackId": track}
    res = patch(me.browser, project["id"], {"music": {"assetId": mine}})
    assert res.status_code == 200 and res.get_json()["project"]["music"] == {"assetId": mine}
    for refused in (
        {"trackId": inactive},
        {"trackId": str(uuid.uuid4())},
        {"assetId": theirs},
        {"assetId": voice},
        {"assetId": str(uuid.uuid4())},
        {"trackId": track, "assetId": mine},
        {"trackId": "not-a-uuid"},
        {},
    ):
        res = patch(me.browser, project["id"], {"music": refused})
        assert_invalid(res)
        assert any(k.startswith("music") for k in res.get_json()["error"]["details"]["fields"]), refused
    assert get_project(me.browser, project["id"])["music"] == {"assetId": mine}
    res = patch(me.browser, project["id"], {"music": None})
    assert res.status_code == 200 and res.get_json()["project"]["music"] is None


def test_a_new_voice_outdates_generated_voices_only(app, database, outbox):
    """B5, S-7: changing voiceId marks voices outdated; nothing missing becomes outdated."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    first, second, *rest = scene_ids(project)
    set_statuses(database, project["id"], voice="ready", visual="ready", status="ready")
    database.run("UPDATE scenes SET voice_status = 'missing' WHERE id = :s", s=first)
    database.run("UPDATE scenes SET voice_status = 'failed' WHERE id = :s", s=second)

    res = patch(me.browser, project["id"], {"voiceId": project["voiceId"]})
    assert res.status_code == 200
    assert [s["voiceStatus"] for s in res.get_json()["project"]["scenes"]][2:] == ["ready"] * len(rest)

    res = patch(me.browser, project["id"], {"voiceId": "IKne3meq5aSn9XLyUdCD"})
    changed = res.get_json()["project"]
    assert changed["voiceId"] == "IKne3meq5aSn9XLyUdCD"
    by_id = {s["id"]: s for s in changed["scenes"]}
    assert by_id[first]["voiceStatus"] == "missing"
    assert by_id[second]["voiceStatus"] == "outdated"
    assert all(by_id[s]["voiceStatus"] == "outdated" for s in rest)
    assert all(s["visualStatus"] == "ready" for s in changed["scenes"]), "visuals are untouched"
    assert changed["status"] == "draft", "something is outdated: back to draft"


def test_a_new_style_or_character_outdates_ai_visuals_but_not_stock(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    set_statuses(database, project["id"], voice="ready", visual="ready", status="rendered")
    database.run("UPDATE scenes SET visual_source = 'ai_image' WHERE id = :s", s=ids[0])
    database.run("UPDATE scenes SET visual_source = 'ai_video' WHERE id = :s", s=ids[1])
    database.run("UPDATE scenes SET visual_source = 'upload' WHERE id = :s", s=ids[2])

    res = patch(me.browser, project["id"], {"style": project["style"], "settings": {"ducking": False}})
    assert res.get_json()["project"]["status"] == "ready", "an edit to a rendered project: back to ready"
    assert {s["visualStatus"] for s in res.get_json()["project"]["scenes"]} == {"ready"}

    for change in ({"style": "watercolour"}, {"character": "a red fox"}):
        set_statuses(database, project["id"], visual="ready", status="ready")
        res = patch(me.browser, project["id"], change)
        by_id = {s["id"]: s for s in res.get_json()["project"]["scenes"]}
        assert by_id[ids[0]]["visualStatus"] == "outdated"
        assert by_id[ids[1]]["visualStatus"] == "outdated"
        assert all(by_id[s]["visualStatus"] == "ready" for s in ids[2:]), "stock and uploads stay"
        assert all(s["voiceStatus"] == "ready" for s in by_id.values())
        assert res.get_json()["project"]["status"] == "draft"


@pytest.mark.parametrize(
    "status", ["rendered", "posted", "ready", "post_failed", "render_failed", "needs_attention"]
)
def test_editing_a_generated_project_sends_it_back_to_ready(app, database, outbox, status):
    me = creator(app)
    project = scripted_project(app, me.browser)
    set_statuses(database, project["id"], voice="ready", visual="ready", status=status)
    res = patch(me.browser, project["id"], {"postCaption": "New caption"})
    assert res.get_json()["project"]["status"] == "ready"


@pytest.mark.parametrize("status", ["generating", "rendering", "posting"])
def test_editing_keeps_a_status_that_is_still_in_progress(app, database, outbox, status):
    me = creator(app)
    project = scripted_project(app, me.browser)
    set_statuses(database, project["id"], voice="ready", visual="ready", status=status)
    res = patch(me.browser, project["id"], {"voiceId": "nPczCjzI2devNBz1zQrb"})
    assert res.status_code == 200
    assert res.get_json()["project"]["status"] == status


def test_a_draft_stays_a_draft_when_edited(app, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    res = patch(me.browser, project["id"], {"title": "Still a draft"})
    assert res.get_json()["project"]["status"] == "draft"


# ---------------------------------------------------------------- B6


def test_deleting_a_project_while_its_script_is_written_cancels_and_refunds(app, database, outbox):
    """B6, G-14."""
    me = creator(app)
    project, job = start_project(me.browser)
    assert balance(me.browser) == 100 - SCRIPT_COST
    res = me.browser.delete(f"/projects/{project['id']}")
    assert res.status_code == 204 and res.get_data() == b""
    assert balance(me.browser) == 100
    job = get_job(me.browser, job["id"])
    assert job["state"] == "cancelled" and job["creditsRefunded"] is True
    assert job_ledger(database, job["id"]) == [("reserve", -SCRIPT_COST), ("refund", SCRIPT_COST)]

    # The worker later finds the job cancelled and writes nothing.
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "cancelled"
    assert database.value("SELECT count(*) FROM scenes WHERE project_id = :p", p=project["id"]) == 0
    assert balance(me.browser) == 100
    assert_ledger_matches_balance(database, me.id)

    assert_error(me.browser.get(f"/projects/{project['id']}"), 404, "not_found")
    assert_error(me.browser.delete(f"/projects/{project['id']}"), 404, "not_found")
    assert_error(me.browser.patch(f"/projects/{project['id']}", {"title": "x"}), 404, "not_found")
    assert listed(me.browser)["counts"]["total"] == 0
    assert project_row(database, project["id"])["deleted_at"] is not None, "soft delete"


def test_deleting_a_project_makes_its_scenes_unreachable(app, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    scene = project["scenes"][0]["id"]
    assert me.browser.delete(f"/projects/{project['id']}").status_code == 204
    assert_error(me.browser.patch(f"/scenes/{scene}", {"narration": "x"}), 404, "not_found")
    assert_error(me.browser.delete(f"/scenes/{scene}"), 404, "not_found")
    assert_error(me.browser.post(f"/scenes/{scene}/duplicate"), 404, "not_found")
    assert_error(me.browser.post(f"/scenes/{scene}/rewrite", {"instructions": "x"}), 404, "not_found")
    assert_error(me.browser.get(f"/projects/{project['id']}/jobs"), 404, "not_found")
    assert_error(me.browser.get(f"/projects/{project['id']}/estimate"), 404, "not_found")


def test_deleting_cancels_a_running_scene_rewrite_and_refunds_it(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    res = me.browser.post(f"/scenes/{project['scenes'][0]['id']}/rewrite", {"instructions": "punchier"})
    job = res.get_json()["job"]
    assert me.browser.delete(f"/projects/{project['id']}").status_code == 204
    assert get_job(me.browser, job["id"])["state"] == "cancelled"
    assert balance(me.browser) == 100 - SCRIPT_COST, "only the script that was written is charged"
    run_jobs(app)
    assert_ledger_matches_balance(database, me.id)


# ---------------------------------------------------------------- purge


def test_projects_deleted_more_than_seven_days_ago_are_purged(app, database, outbox):
    me = creator(app)
    old = scripted_project(app, me.browser)
    recent = scripted_project(app, me.browser)
    kept = scripted_project(app, me.browser)
    for project, days in ((old, 8), (recent, 6)):
        assert me.browser.delete(f"/projects/{project['id']}").status_code == 204
        database.run(
            "UPDATE projects SET deleted_at = now() - make_interval(days => :d) WHERE id = :p",
            d=days,
            p=project["id"],
        )
    ledger_before = database.value("SELECT count(*) FROM credit_ledger WHERE user_id = :u", u=me.id)

    with app.app_context():
        purge_deleted.run()

    remaining = {r["id"] for r in database.rows("SELECT id::text FROM projects WHERE user_id = :u", u=me.id)}
    assert remaining == {recent["id"], kept["id"]}
    for table in ("scenes", "jobs"):
        assert database.value(f"SELECT count(*) FROM {table} WHERE project_id = :p", p=old["id"]) == 0  # noqa: S608
    assert database.value("SELECT count(*) FROM scenes WHERE project_id = :p", p=recent["id"]) > 0
    # The credit ledger is kept forever (DB design 8).
    assert database.value("SELECT count(*) FROM credit_ledger WHERE user_id = :u", u=me.id) == ledger_before
    assert_ledger_matches_balance(database, me.id)
    assert get_project(me.browser, kept["id"])["status"] == "draft"


# ---------------------------------------------------------------- B7


def test_duplicating_copies_script_and_settings_and_reuses_files_for_free(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    patch(me.browser, project["id"], {"settings": {"transition": "fade"}, "hashtags": ["one"]})
    set_statuses(database, project["id"], voice="ready", visual="ready", status="rendered")
    voice = new_asset(database, me.id, project["id"])
    first = project["scenes"][0]["id"]
    database.run("UPDATE scenes SET voice_asset_id = :a WHERE id = :s", a=voice, s=first)
    source = get_project(me.browser, project["id"])
    before = balance(me.browser)

    res = me.browser.post(f"/projects/{project['id']}/duplicate")
    assert res.status_code == 201, res.get_data(as_text=True)
    assert set(res.get_json()) == {"project"}
    copy = res.get_json()["project"]
    assert copy["id"] != source["id"]
    assert copy["title"] == f"{source['title']} (copy)"
    for field in (
        "topic",
        "tone",
        "mode",
        "targetSeconds",
        "style",
        "character",
        "voiceId",
        "musicMood",
        "music",
        "settings",
        "postCaption",
        "hashtags",
        "wordCount",
        "estimatedSeconds",
    ):
        assert copy[field] == source[field], field
    assert copy["status"] == "ready", "everything was generated: the copy is ready to render"
    assert copy["activeJobs"] == [] and copy["latestRender"] is None
    strip = lambda s: {k: v for k, v in unsigned(s).items() if k != "id"}  # noqa: E731
    assert [strip(s) for s in copy["scenes"]] == [strip(s) for s in source["scenes"]]
    assert not set(scene_ids(copy)) & set(scene_ids(source))
    assert scene_row(database, copy["scenes"][0]["id"])["voice_asset_id"] == uuid.UUID(voice)
    assert balance(me.browser) == before
    again = get_project(me.browser, project["id"])
    assert {**again, "scenes": [unsigned(s) for s in again["scenes"]]} == {
        **source,
        "scenes": [unsigned(s) for s in source["scenes"]],
    }, "the original is untouched"


def test_a_copy_of_work_in_progress_needs_that_work_again(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    voice = new_asset(database, me.id, project["id"])
    first, second = scene_ids(project)[:2]
    set_statuses(database, project["id"], voice="ready", visual="ready", status="generating")
    database.run(
        "UPDATE scenes SET voice_status = 'running', voice_asset_id = :a WHERE id = :s", a=voice, s=first
    )
    database.run("UPDATE scenes SET visual_status = 'queued' WHERE id = :s", s=second)
    copy = me.browser.post(f"/projects/{project['id']}/duplicate").get_json()["project"]
    assert copy["scenes"][0]["voiceStatus"] == "outdated", "an older file exists"
    assert copy["scenes"][1]["visualStatus"] == "missing"
    assert copy["status"] == "draft"


def test_a_long_title_still_fits_when_copied(app, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    patch(me.browser, project["id"], {"title": "t" * 120})
    copy = me.browser.post(f"/projects/{project['id']}/duplicate").get_json()["project"]
    assert 1 <= len(copy["title"]) <= 120


# ---------------------------------------------------------------- B10


def items_by_type(estimate: dict) -> dict[str, dict]:
    return {item["type"]: item for item in estimate["items"]}


def item(estimate: dict, kind: str) -> tuple[int, int]:
    found = items_by_type(estimate).get(kind, {"count": 0, "credits": 0})
    return found["count"], found["credits"]


def test_the_estimate_counts_what_is_missing_or_outdated(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    assert len(ids) >= 4
    # Scene 1: 80 words = 32 s of speech: 2 credits per started 30 s = 4.
    long = " ".join(["word"] * 79) + " end."
    me.browser.patch(f"/scenes/{ids[0]}", {"narration": long})
    database.run("UPDATE scenes SET visual_source = 'ai_image' WHERE id = :s", s=ids[1])
    database.run("UPDATE scenes SET visual_source = 'ai_video' WHERE id = :s", s=ids[2])
    database.run("UPDATE scenes SET voice_status = 'ready', visual_status = 'ready' WHERE id = :s", s=ids[3])
    for scene_id in ids[4:]:
        database.run(
            "UPDATE scenes SET voice_status = 'ready', visual_status = 'ready' WHERE id = :s", s=scene_id
        )

    res = me.browser.get(f"/projects/{project['id']}/estimate")
    assert res.status_code == 200, res.get_data(as_text=True)
    estimate = res.get_json()
    assert set(estimate) == {"credits", "balance", "items"}
    for entry in estimate["items"]:
        assert set(entry) == {"type", "count", "credits"}
    # The voices needed are charged together: 2 credits per started 30 s of all of them
    # (product decision 2026-11-25), not rounded up scene by scene.
    narrations = {s["id"]: s["narration"] for s in get_project(me.browser, project["id"])["scenes"]}
    needed = sum(estimated_seconds(narrations[s]) for s in ids[:3])
    assert 30 < needed <= 60, "scene 1 alone is 32 s; the mock's other scenes are short"
    voices_cost = voice_credits(needed)
    assert voices_cost == 4, "per scene it would have been 4 + 2 + 2"
    assert item(estimate, "voice") == (3, voices_cost)
    assert item(estimate, "ai_image") == (1, 1)
    assert item(estimate, "ai_video") == (1, visual_credits("ai_video")), "an AI image until B7: 1 credit"
    assert item(estimate, "visual") == (1, 0), "stock footage is free"
    assert item(estimate, "music") == (1, 0)
    assert (
        estimate["credits"]
        == sum(entry["credits"] for entry in estimate["items"])
        == voices_cost + 1 + visual_credits("ai_video")
    )
    assert estimate["balance"] == balance(me.browser)

    # Outdated and failed parts count; ready, queued and running ones don't.
    database.run("UPDATE scenes SET voice_status = 'outdated' WHERE id = :s", s=ids[3])
    database.run(
        "UPDATE scenes SET voice_status = 'running', visual_status = 'queued' WHERE id = :s", s=ids[0]
    )
    estimate = me.browser.get(f"/projects/{project['id']}/estimate").get_json()
    again = sum(estimated_seconds(narrations[s]) for s in ids[1:4])
    assert item(estimate, "voice") == (3, voice_credits(again)), "scenes 2, 3 (missing) and 4 (outdated)"


def test_the_estimate_of_a_project_with_nothing_left_to_do_is_zero(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    set_statuses(database, project["id"], voice="ready", visual="ready")
    track = str(uuid.uuid4())
    database.run(
        "INSERT INTO music_tracks (id, title, s3_key, duration_seconds, license) "
        "VALUES (:id, 'S', :k, 30, 'CC0')",
        id=track,
        k=f"library/{track}",
    )
    database.run("UPDATE projects SET music_track_id = :t WHERE id = :p", t=track, p=project["id"])
    estimate = me.browser.get(f"/projects/{project['id']}/estimate").get_json()
    assert estimate["credits"] == 0
    assert all(entry["count"] == 0 for entry in estimate["items"])


def test_the_estimate_of_a_project_without_scenes_needs_only_music(app, outbox):
    me = creator(app)
    project, _ = start_project(me.browser, topic=TOPIC)
    estimate = me.browser.get(f"/projects/{project['id']}/estimate").get_json()
    assert estimate["credits"] == 0
    assert item(estimate, "voice") == (0, 0)
