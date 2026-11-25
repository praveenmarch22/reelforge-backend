"""C8 a scene's new visual, C9 stock search and C10 choosing a visual (milestone B4 slice 2):
docs/API_DESIGN.md C8-C10, Scene and Job objects; PRD VI1-VI6, G-5, G-6; pipeline strategy 3-4;
TECHNICAL_DESIGN.md "Stock (Pixabay, later Pexels)".

Runs with the offline mocks (test settings): a stock query containing "[no-stock]" finds
nothing and "[stock-fail]" fails; an image prompt containing "[image-fail]" fails and
"[image-refuse]" is refused.
"""

from __future__ import annotations

import io
import json
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from reelforge_api import jobs
from reelforge_api.providers.stock import MockStock
from reelforge_api.script_rules import COSTS, visual_credits
from reelforge_api.tasks.jobs import fail_stuck_jobs

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
    run_jobs,
    scene_ids,
    scripted_project,
    set_balance,
    unsigned,
    waiting_jobs,
)
from .support import Browser, assert_error, assert_invalid
from .visual_support import (
    STOCK_RESULT_FIELDS,
    VISUAL_FIELDS,
    regenerate_visual,
    scene_of,
    select_visual,
    stock_search,
    stored_visual_files,
    upload_asset,
    visual_assets,
    visual_project,
    visual_started,
)
from .voice_support import (  # noqa: F401 - no_real_voice_services is an autouse fixture
    NETWORK_ATTEMPTS,
    download,
    no_real_voice_services,
    started,
)

AFTER_SCRIPT = 100 - SCRIPT_COST
IMAGE_COST = COSTS["aiImage"]


def during_work(monkeypatch, target: str, action) -> list:
    """Runs `action()` while the provider works ("stock" search or "image" generation)."""
    from reelforge_api.providers.image import MockImage

    cls, method = (MockStock, "search") if target == "stock" else (MockImage, "generate")
    real = getattr(cls, method)
    results: list = []

    def slow(self, *args, **kwargs):
        results.append(action())
        return real(self, *args, **kwargs)

    monkeypatch.setattr(cls, method, slow)
    return results


def on_another_thread(fn):
    out: list = []
    thread = threading.Thread(target=lambda: out.append(fn()))
    thread.start()
    thread.join(timeout=30)
    assert out, "the request did not finish"
    return out[0]


def searched(monkeypatch) -> list[tuple[str, int]]:
    """Every query the mock stock provider receives."""
    calls: list[tuple[str, int]] = []
    real = MockStock.search

    def recording(self, query, page=1):
        calls.append((query, page))
        return real(self, query, page)

    monkeypatch.setattr(MockStock, "search", recording)
    return calls


def switch_off(database, *providers: str) -> None:
    for name in providers:
        database.run(
            "INSERT INTO provider_switches (provider, enabled, reason) VALUES (:p, false, 'test') "
            "ON CONFLICT (provider) DO UPDATE SET enabled = false",
            p=name,
        )


def make_ready(app, database, me) -> tuple[dict, list[str]]:
    """A project whose scenes all have a voice and a stock visual, status ready."""
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    for sid in ids:
        started(me.browser, sid)
        visual_started(me.browser, sid)
    run_jobs(app)
    found = get_project(me.browser, project["id"])
    assert found["status"] == "ready", [(s["voiceStatus"], s["visualStatus"]) for s in found["scenes"]]
    return found, ids


# ---------------------------------------------------------------- C8 stock: the main flow


def test_a_stock_visual_goes_queued_running_ready_with_its_clip_and_credits(
    app, database, outbox, monkeypatch
):
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="ocean waves")
    assert scene_of(me.browser, project["id"], scene_id)["visualSource"] == "stock", "faceless default"

    res = regenerate_visual(me.browser, scene_id)
    assert res.status_code == 202, res.get_data(as_text=True)
    assert set(res.get_json()) == {"job"}
    job = res.get_json()["job"]
    assert set(job) == JOB_FIELDS
    assert job["type"] == "visual" and job["state"] == "queued" and job["sceneId"] == scene_id
    assert job["creditsReserved"] == 0 and ISO_UTC.match(job["queuedAt"])
    assert scene_of(me.browser, project["id"], scene_id)["visualStatus"] == "queued"

    seen = during_work(
        monkeypatch, "stock",
        lambda: database.value("SELECT visual_status FROM scenes WHERE id = :s", s=scene_id),
    )  # fmt: skip
    run_jobs(app)
    assert seen == ["running"]

    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["provider"] == "mock" and done["creditsRefunded"] is False
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["visualStatus"] == "ready" and scene["lastError"] is None
    visual = scene["visual"]
    assert set(visual) == VISUAL_FIELDS
    assert visual["kind"] == "video" and visual["provider"] == "mock" and visual["stockFallback"] is False
    assert visual["author"].startswith("mock author") and visual["sourceUrl"].startswith("https://")
    assert (visual["width"], visual["height"]) == (1920, 1080)
    assert done["output"] == {
        "visual": {
            "assetId": visual["assetId"],
            "kind": "video",
            "durationSeconds": visual["durationSeconds"],
        },
        "stockFallback": False,
    }
    assert download(visual["url"])[4:8] == b"ftyp", "the link serves the stored clip"

    (asset,) = visual_assets(database, project["id"])
    assert asset["kind"] == "video_clip" and asset["content_type"] == "video/mp4"
    assert asset["s3_key"] == f"users/{me.id}/projects/{project['id']}/visual/{job['id']}.mp4"
    assert asset["meta"]["providerId"].startswith("mock:")
    assert asset["meta"]["author"] == visual["author"] and asset["meta"]["pageUrl"] == visual["sourceUrl"]
    assert balance(me.browser) == AFTER_SCRIPT and job_ledger(database, job["id"]) == []


def test_the_offline_mock_stock_makes_no_network_call(app, database, outbox):
    """The mocks stand in offline (TECHNICAL_DESIGN 9.0: every provider has an offline mock).
    Storing a mock clip must not fetch its thumbnail from the internet."""
    me = creator(app)
    _, scene_id = visual_project(app, me, stockQuery="ocean waves")
    visual_started(me.browser, scene_id)
    run_jobs(app)
    assert NETWORK_ATTEMPTS == [], f"the mock reached out to {NETWORK_ATTEMPTS}"


@pytest.mark.parametrize(
    ("words", "voice_seconds", "expected"),
    [
        (10, None, "mock:1"),  # 4 s: the first clip long enough (7 s)
        (30, None, "mock:3"),  # 12 s: 13 s is the first long enough
        (60, None, "mock:5"),  # 24 s: nothing is long enough, the longest (19 s)
        (10, 15.0, "mock:4"),  # the voice (15 s) sets the length when it is made
    ],
)
def test_the_clip_is_long_enough_for_the_scene(app, database, outbox, words, voice_seconds, expected):
    """Pipeline strategy 3; mock clips last 7, 10, 13, 16 and 19 s."""
    me = creator(app)
    project, scene_id = visual_project(app, me, narration=" ".join(["word"] * words), stockQuery="ocean")
    if voice_seconds:
        started(me.browser, scene_id)
        run_jobs(app)
        database.run(
            "UPDATE assets SET duration_seconds = :d "
            "WHERE id = (SELECT voice_asset_id FROM scenes WHERE id = :s)",
            d=voice_seconds, s=scene_id,
        )  # fmt: skip
    visual_started(me.browser, scene_id)
    run_jobs(app)
    assert visual_assets(database, project["id"])[-1]["meta"]["providerId"] == expected


def test_regenerating_never_gives_the_current_clip_again(app, database, outbox):
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="ocean")
    picked = []
    for _ in range(3):
        visual_started(me.browser, scene_id)
        run_jobs(app)
        picked.append(visual_assets(database, project["id"])[-1]["meta"]["providerId"])
    assert picked[0] != picked[1] and picked[1] != picked[2]
    rows = visual_assets(database, project["id"])
    assert [r["replaced_at"] is not None for r in rows] == [True, True, False], "replaced files are released"


def test_the_stock_query_falls_back_to_the_visual_prompt_then_the_narration(
    app, database, outbox, monkeypatch
):
    me = creator(app)
    _, scene_id = visual_project(app, me, stockQuery="", visualPrompt="city lights", narration="Night falls.")
    queries = searched(monkeypatch)
    visual_started(me.browser, scene_id)
    run_jobs(app)
    assert queries[0][0] == "city lights"
    me.browser.patch(f"/scenes/{scene_id}", {"visualPrompt": ""})
    visual_started(me.browser, scene_id)
    run_jobs(app)
    assert queries[-1][0] == "Night falls."


def test_a_query_with_no_results_is_retried_with_simpler_keywords(app, database, outbox, monkeypatch):
    """G-6: retry with simpler keywords before the AI image."""
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="ocean waves [no-stock]")
    queries = searched(monkeypatch)
    job = visual_started(me.browser, scene_id)
    run_jobs(app)
    assert [q for q, _ in queries] == ["ocean waves [no-stock]", "ocean waves"]
    done = get_job(me.browser, job["id"])
    assert done["output"]["stockFallback"] is False and done["output"]["visual"]["kind"] == "video"


def test_nothing_in_stock_falls_back_to_a_free_ai_image(app, database, outbox, monkeypatch):
    """G-6, PRD VI2: nothing fits → an AI image at no extra charge, flagged stockFallback."""
    me = creator(app)
    project, scene_id = visual_project(
        app, me, stockQuery="[no-stock] ocean waves", visualPrompt="a calm sea"
    )
    queries = searched(monkeypatch)
    job = visual_started(me.browser, scene_id)
    assert job["creditsReserved"] == 0
    run_jobs(app)
    assert [q for q, _ in queries] == ["[no-stock] ocean waves", "[no-stock] ocean"]
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["output"]["stockFallback"] is True
    visual = scene_of(me.browser, project["id"], scene_id)["visual"]
    assert visual["kind"] == "image" and visual["stockFallback"] is True and visual["durationSeconds"] is None
    (asset,) = visual_assets(database, project["id"])
    assert asset["kind"] == "image" and asset["content_type"] == "image/jpeg"
    assert asset["s3_key"].endswith(f"/visual/{job['id']}.jpg")
    # A separate small still for lists (fix log QB4S2-009).
    assert asset["meta"]["thumbnailKey"] == asset["s3_key"].replace(".jpg", "-thumb.jpg")
    assert (
        unsigned({"visual": visual})["visual"]["thumbnailUrl"]
        != unsigned({"visual": visual})["visual"]["url"]
    )
    with Image.open(io.BytesIO(download(visual["thumbnailUrl"]))) as thumb:
        assert thumb.format == "JPEG" and max(thumb.size) <= 640 and thumb.size[0] <= 360
    assert "a calm sea" in asset["meta"]["prompt"] and asset["meta"]["motion"] == "zoom"
    assert download(visual["url"])[:3] == b"\xff\xd8\xff"
    assert balance(me.browser) == AFTER_SCRIPT


def test_a_failing_stock_search_goes_to_the_free_ai_image(app, database, outbox):
    """Fix log QB4S2-002: a stock provider that is down sends the job to the AI image."""
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="ocean [stock-fail]")
    job = visual_started(me.browser, scene_id)
    run_jobs(app)
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["output"]["stockFallback"] is True
    assert done["creditsReserved"] == 0
    assert scene_of(me.browser, project["id"], scene_id)["visual"]["kind"] == "image"


@pytest.fixture(scope="module")
def no_stock_app(app):
    """Pixabay configured without a key: no stock provider is available, the image mock is."""
    from .conftest import build_app

    return build_app(stock_providers="pixabay", pixabay_api_key="")


def test_without_a_stock_provider_a_stock_scene_gets_a_free_ai_image(no_stock_app, outbox):
    me = creator(no_stock_app)
    project, scene_id = visual_project(no_stock_app, me, stockQuery="ocean")
    job = visual_started(me.browser, scene_id)
    assert job["creditsReserved"] == 0
    run_jobs(no_stock_app)
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["output"]["stockFallback"] is True
    assert_error(stock_search(me.browser, scene_id), 503, "provider_unavailable")
    assert NETWORK_ATTEMPTS == []


# ---------------------------------------------------------------- C8 AI images


@pytest.mark.parametrize("source", ["ai_image", "ai_video"])
def test_an_ai_visual_costs_one_credit_and_uses_the_projects_style(app, database, outbox, source):
    """Until AI video arrives (B7), an ai_video scene gets an AI image for 1 credit."""
    assert visual_credits(source) == IMAGE_COST == 1
    me = creator(app)
    project, scene_id = visual_project(app, me, visualPrompt="a runner at dawn", mood="excited")
    me.browser.patch(f"/projects/{project['id']}", {"style": "watercolour", "character": "a red fox"})
    job = visual_started(me.browser, scene_id, {"visualSource": source})
    assert job["creditsReserved"] == 1 and balance(me.browser) == AFTER_SCRIPT - 1
    run_jobs(app)
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["output"]["stockFallback"] is False
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["visualSource"] == source and scene["visualStatus"] == "ready"
    visual = scene["visual"]
    assert visual["kind"] == "image" and visual["provider"] == "mock"
    assert (visual["width"], visual["height"]) == (288, 512)
    (asset,) = visual_assets(database, project["id"])
    prompt = asset["meta"]["prompt"]
    for part in ("a runner at dawn", "watercolour style", "featuring a red fox", "vertical 9:16"):
        assert part in prompt, (part, prompt)
    assert "dynamic fast camera push-in" in prompt, "the mood's look"
    assert job_ledger(database, job["id"]) == [("reserve", -1)]


def test_a_new_ai_image_is_a_new_take(app, database, outbox):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    visual_started(me.browser, scene_id, {"visualSource": "ai_image"})
    run_jobs(app)
    visual_started(me.browser, scene_id)
    run_jobs(app)
    first, second = visual_assets(database, project["id"])
    assert first["id"] != second["id"] and first["replaced_at"] is not None
    assert first["meta"]["seed"] != second["meta"]["seed"]
    assert balance(me.browser) == AFTER_SCRIPT - 2


@pytest.mark.parametrize(
    ("marker", "code"), [("[image-fail]", "provider_failed"), ("[image-refuse]", "content_refused")]
)
def test_a_failed_ai_image_is_refunded(app, database, outbox, marker, code):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    job = visual_started(
        me.browser, scene_id, {"visualSource": "ai_image", "visualPrompt": f"a cat {marker}"}
    )
    run_jobs(app)
    failed = get_job(me.browser, job["id"])
    assert (
        failed["state"] == "failed" and failed["error"]["code"] == code and failed["creditsRefunded"] is True
    )
    assert balance(me.browser) == AFTER_SCRIPT
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["visualStatus"] == "failed" and scene["lastError"] == failed["error"]["message"]
    assert (
        visual_assets(database, project["id"]) == [] and stored_visual_files(app, me.id, project["id"]) == []
    )
    assert_ledger_matches_balance(database, me.id)


# ---------------------------------------------------------------- C8 body, refusals and order


def test_fields_sent_with_the_request_are_saved_first(app, database, outbox, monkeypatch):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    queries = searched(monkeypatch)
    visual_started(me.browser, scene_id, {"stockQuery": "mountain lake", "visualPrompt": "a quiet lake"})
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["stockQuery"] == "mountain lake" and scene["visualPrompt"] == "a quiet lake"
    run_jobs(app)
    assert queries[0][0] == "mountain lake"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"visualSource": "upload"}, "visualSource"),
        ({"visualSource": "video"}, "visualSource"),
        ({"visualPrompt": None}, "visualPrompt"),
        ({"stockQuery": "x" * 101}, "stockQuery"),
        ({"visualPrompt": "x" * 1001}, "visualPrompt"),
        ({"stock_query": "x"}, "stock_query"),
        ({"narration": "x"}, "narration"),
    ],
)
def test_invalid_bodies_are_refused_and_change_nothing(app, outbox, body, field):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    before = scene_of(me.browser, project["id"], scene_id)
    assert_invalid(regenerate_visual(me.browser, scene_id, body), field)
    assert unsigned(scene_of(me.browser, project["id"], scene_id)) == unsigned(before)
    assert waiting_jobs() == []


def test_an_upload_scene_is_regenerated_only_by_choosing_another_source(app, database, outbox):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    asset = upload_asset(database, me.id, project["id"])
    assert select_visual(me.browser, scene_id, {"assetId": asset}).status_code == 200
    assert_invalid(regenerate_visual(me.browser, scene_id), "visualSource")
    assert_invalid(regenerate_visual(me.browser, scene_id, {"stockQuery": "new"}), "visualSource")
    assert scene_of(me.browser, project["id"], scene_id)["stockQuery"] != "new", "nothing saved"
    visual_started(me.browser, scene_id, {"visualSource": "stock"})
    run_jobs(app)


def test_who_may_and_when(app, database, outbox):
    owner, other = creator(app), creator(app)
    project, scene_id = visual_project(app, owner)
    for target in (scene_id, str(uuid.uuid4()), "nope"):
        assert_error(regenerate_visual(other.browser, target), 404, "not_found")
    assert_error(Browser(app).post(f"/scenes/{scene_id}/visual/regenerate", {}), 401, "not_authenticated")

    job = visual_started(owner.browser, scene_id)
    assert_error(regenerate_visual(owner.browser, scene_id), 409, "already_running")
    run_jobs(app)
    assert owner.browser.post(f"/projects/{project['id']}/script/regenerate", {}).status_code == 202
    assert_error(regenerate_visual(owner.browser, scene_id), 409, "already_running")
    run_jobs(app)
    assert get_job(owner.browser, job["id"])["state"] == "succeeded"

    database.run("UPDATE users SET email_verified_at = NULL WHERE id = :u", u=owner.id)
    new_scene = get_project(owner.browser, project["id"])["scenes"][0]["id"]
    assert_error(regenerate_visual(owner.browser, new_scene), 403, "email_not_verified")


def test_an_ai_image_needs_a_credit(app, database, outbox):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    set_balance(database, me.id, 0)
    error = assert_error(
        regenerate_visual(me.browser, scene_id, {"visualSource": "ai_image"}), 402, "insufficient_credits"
    )
    assert error["details"]["needed"] == 1
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["visualSource"] == "stock", "nothing was saved"
    assert scene["visualStatus"] == "missing" and waiting_jobs() == []
    visual_started(me.browser, scene_id)  # stock is free
    run_jobs(app)


def test_with_every_provider_switched_off_it_is_503(app, database, outbox):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    switch_off(database, "mock")  # the stock and image mocks share the name
    assert_error(regenerate_visual(me.browser, scene_id), 503, "provider_unavailable")
    assert_error(
        regenerate_visual(me.browser, scene_id, {"visualSource": "ai_image"}), 503, "provider_unavailable"
    )
    assert balance(me.browser) == AFTER_SCRIPT and waiting_jobs() == []
    assert scene_of(me.browser, project["id"], scene_id)["visualSource"] == "stock"


def test_the_same_idempotency_key_returns_the_first_answer(app, database, outbox):
    me = creator(app)
    _, scene_id = visual_project(app, me)
    first = regenerate_visual(me.browser, scene_id, {"visualSource": "ai_image"}, key="v-1")
    again = regenerate_visual(me.browser, scene_id, {"visualSource": "ai_image"}, key="v-1")
    assert first.status_code == again.status_code == 202 and again.get_json() == first.get_json()
    assert balance(me.browser) == AFTER_SCRIPT - 1
    assert_error(
        regenerate_visual(me.browser, scene_id, {"visualSource": "stock"}, key="v-1"),
        409,
        "idempotency_conflict",
    )
    run_jobs(app)


def test_a_stuck_visual_job_fails_after_8_minutes(app, database, outbox):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    job = visual_started(me.browser, scene_id, {"visualSource": "ai_image"})
    jobs.manual_queue.clear()
    database.run("UPDATE scenes SET visual_status = 'running' WHERE id = :s", s=scene_id)

    def running_for(minutes: float) -> None:
        moment = datetime.now(UTC) - timedelta(minutes=minutes)
        database.run(
            "UPDATE jobs SET state = 'running', started_at = :m WHERE id = :j", m=moment, j=job["id"]
        )

    running_for(7.5)
    with app.app_context():
        assert fail_stuck_jobs.run() == 0
    running_for(8.5)
    with app.app_context():
        assert fail_stuck_jobs.run() == 1
    failed = get_job(me.browser, job["id"])
    assert failed["error"]["code"] == "timed_out" and failed["creditsRefunded"] is True
    assert scene_of(me.browser, project["id"], scene_id)["visualStatus"] == "failed"


def test_visual_jobs_go_to_the_visuals_queue_with_their_time_limits(app, outbox, monkeypatch):
    from .conftest import build_app

    celery_app = build_app(job_dispatch="celery")
    sent: list[dict] = []

    class Sent:
        id = "task-1"

    def record(task, args=None, kwargs=None, **options):
        sent.append({"args": tuple(args or ()), **options})
        return Sent()

    monkeypatch.setattr("celery.app.task.Task.apply_async", record)
    me = creator(celery_app)
    res = me.browser.post("/projects", {"topic": "habits"})
    with celery_app.app_context():
        jobs.run(res.get_json()["job"]["id"])
    scene_id = get_project(me.browser, res.get_json()["project"]["id"])["scenes"][0]["id"]
    job = visual_started(me.browser, scene_id)
    (visual,) = [s for s in sent if s["args"] == (job["id"],)]
    assert (visual["queue"], visual["soft_time_limit"], visual["time_limit"]) == ("visuals", 360, 420)


# ---------------------------------------------------------------- C9 stock search


def test_stock_search_answers_with_results_and_the_next_page(app, outbox):
    me = creator(app)
    _, scene_id = visual_project(app, me, stockQuery="ocean waves")
    res = stock_search(me.browser, scene_id)
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert set(body) == {"items", "nextPage", "query"}
    assert body["query"] == "ocean waves" and body["nextPage"] == 2
    assert body["items"]
    for item in body["items"]:
        assert set(item) == STOCK_RESULT_FIELDS
        assert item["providerId"].startswith("mock:") and item["source"] == "mock"
    last = stock_search(me.browser, scene_id, q="ocean", page=3).get_json()
    assert last["nextPage"] is None and last["query"] == "ocean"
    nothing = stock_search(me.browser, scene_id, q="[no-stock]").get_json()
    assert nothing["items"] == [] and nothing["nextPage"] is None


def test_the_query_defaults_to_the_stock_query_then_the_visual_prompt(app, database, outbox):
    me = creator(app)
    _, scene_id = visual_project(app, me, stockQuery="", visualPrompt="snowy forest")
    assert stock_search(me.browser, scene_id).get_json()["query"] == "snowy forest"
    database.run("UPDATE scenes SET visual_prompt = '' WHERE id = :s", s=scene_id)
    assert_invalid(stock_search(me.browser, scene_id), "q")


def test_results_are_kept_for_a_day_and_shared_by_the_same_query(app, outbox, monkeypatch):
    me = creator(app)
    _, scene_id = visual_project(app, me)
    queries = searched(monkeypatch)
    first = stock_search(me.browser, scene_id, q="Ocean%20%20Waves").get_json()
    again = stock_search(me.browser, scene_id, q="ocean%20waves").get_json()
    assert again["items"] == first["items"] and len(queries) == 1
    stock_search(me.browser, scene_id, q="ocean%20waves", page=2)
    assert len(queries) == 2, "another page is another search"
    redis = app.extensions["redis"]
    keys = [k for k in redis.scan_iter("stock:*")]
    assert keys and all(24 * 3600 - 60 <= redis.ttl(k) <= 24 * 3600 for k in keys)


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ({"q": "x" * 101}, "q"),
        ({"page": "0"}, "page"),
        ({"page": "26"}, "page"),
        ({"page": "two"}, "page"),
        ({"page": "-1"}, "page"),
        ({"q": "%20%20"}, "q"),
    ],
)
def test_stock_search_refuses_bad_queries(app, database, outbox, query, field):
    me = creator(app)
    _, scene_id = visual_project(app, me, stockQuery="ocean")
    res = stock_search(me.browser, scene_id, **query)
    assert_invalid(res, field)


def test_stock_search_needs_a_verified_owner(app, database, outbox):
    owner, other = creator(app), creator(app)
    _, scene_id = visual_project(app, owner)
    assert_error(stock_search(other.browser, scene_id), 404, "not_found")
    assert_error(Browser(app).get(f"/scenes/{scene_id}/stock-search"), 401, "not_authenticated")
    database.run("UPDATE users SET email_verified_at = NULL WHERE id = :u", u=owner.id)
    assert_error(stock_search(owner.browser, scene_id), 403, "email_not_verified")


def test_stock_search_is_503_when_no_stock_provider_answers(app, database, outbox):
    me = creator(app)
    _, scene_id = visual_project(app, me)
    assert_error(stock_search(me.browser, scene_id, q="ocean%20[stock-fail]"), 503, "provider_unavailable")
    switch_off(database, "mock")
    assert_error(stock_search(me.browser, scene_id, q="ocean"), 503, "provider_unavailable")


def test_stock_search_is_limited_to_60_an_hour(limited_app, outbox):
    me = creator(limited_app)
    _, scene_id = visual_project(limited_app, me)
    for n in range(60):
        assert stock_search(me.browser, scene_id, q="ocean").status_code == 200, n
    res = stock_search(me.browser, scene_id, q="ocean")
    error = assert_error(res, 429, "rate_limited")
    assert int(res.headers["Retry-After"]) == error["details"]["retryAfterSeconds"] > 0
    other = creator(limited_app)
    _, theirs = visual_project(limited_app, other)
    assert stock_search(other.browser, theirs, q="ocean").status_code == 200, "per creator"


# ---------------------------------------------------------------- C10 choosing a stock clip


def test_choosing_a_stock_clip_downloads_it_for_free(app, database, outbox):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    me.browser.patch(f"/scenes/{scene_id}", {"visualSource": "ai_image"})
    res = select_visual(me.browser, scene_id, {"stock": "mock:4"})
    assert res.status_code == 202, res.get_data(as_text=True)
    job = res.get_json()["job"]
    assert job["type"] == "visual" and job["creditsReserved"] == 0
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["visualSource"] == "stock" and scene["visualStatus"] == "queued"
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "succeeded"
    assert visual_assets(database, project["id"])[-1]["meta"]["providerId"] == "mock:4"
    assert scene_of(me.browser, project["id"], scene_id)["visual"]["kind"] == "video"
    assert balance(me.browser) == AFTER_SCRIPT


def test_a_clip_that_is_gone_fails_as_stock_unavailable(app, database, outbox):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    job = select_visual(me.browser, scene_id, {"stock": "mock:99"}).get_json()["job"]
    run_jobs(app)
    failed = get_job(me.browser, job["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "stock_unavailable"
    assert failed["error"]["message"] == jobs.MESSAGES["stock_unavailable"]
    assert scene_of(me.browser, project["id"], scene_id)["visualStatus"] == "failed"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"stock": "pixabay:abc"}, "stock"),
        ({"stock": "mock"}, "stock"),
        ({"stock": "mock:"}, "stock"),
        ({"stock": "unsplash:12"}, "stock"),
        ({"stock": "mock:" + "1" * 21}, "stock"),
        ({"stock": ""}, "stock"),
        ({}, None),
        ({"stock": "mock:1", "assetId": str(uuid.uuid4())}, None),
    ],
)
def test_choosing_refuses_bad_bodies(app, outbox, body, field):
    me = creator(app)
    _, scene_id = visual_project(app, me)
    res = select_visual(me.browser, scene_id, body)
    assert_error(res, 400, "validation_error")
    if field:
        assert_invalid(res, field)
    assert waiting_jobs() == []


def test_choosing_while_a_visual_is_being_made_replaces_it(app, database, outbox):
    """Any visual being made for the scene is cancelled and refunded first."""
    me = creator(app)
    project, scene_id = visual_project(app, me)
    first = visual_started(me.browser, scene_id, {"visualSource": "ai_image"})
    second = select_visual(me.browser, scene_id, {"stock": "mock:2"}).get_json()["job"]
    cancelled = get_job(me.browser, first["id"])
    assert cancelled["state"] == "cancelled" and cancelled["creditsRefunded"] is True
    assert balance(me.browser) == AFTER_SCRIPT
    run_jobs(app)
    assert get_job(me.browser, second["id"])["state"] == "succeeded"
    assert visual_assets(database, project["id"])[-1]["meta"]["providerId"] == "mock:2"


def test_choosing_needs_a_login_and_the_owner(app, database, outbox):
    owner, other = creator(app), creator(app)
    project, scene_id = visual_project(app, owner)
    assert_error(select_visual(other.browser, scene_id, {"stock": "mock:1"}), 404, "not_found")
    assert_error(
        Browser(app).post(f"/scenes/{scene_id}/visual/select", {"stock": "mock:1"}), 401, "not_authenticated"
    )
    database.run("UPDATE users SET email_verified_at = NULL WHERE id = :u", u=owner.id)
    assert select_visual(owner.browser, scene_id, {"stock": "mock:1"}).status_code == 202, "User level"
    run_jobs(app)


# ---------------------------------------------------------------- C10 choosing an upload


@pytest.mark.parametrize(("content_type", "kind"), [("video/mp4", "video"), ("image/png", "image")])
def test_choosing_an_upload_uses_it_at_once(app, database, outbox, content_type, kind):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    visual_started(me.browser, scene_id)
    run_jobs(app)
    old = scene_of(me.browser, project["id"], scene_id)["visual"]["assetId"]
    asset = upload_asset(database, me.id, project["id"], content_type=content_type)
    res = select_visual(me.browser, scene_id, {"assetId": asset})
    assert res.status_code == 200, res.get_data(as_text=True)
    assert set(res.get_json()) == {"scene", "estimate"}
    scene = res.get_json()["scene"]
    assert scene["visualSource"] == "upload" and scene["visualStatus"] == "ready"
    assert scene["visual"]["assetId"] == asset and scene["visual"]["kind"] == kind
    assert scene["visual"]["provider"] == "upload"
    if kind == "image":
        assert scene["visual"]["thumbnailUrl"].split("?")[0] == scene["visual"]["url"].split("?")[0]
    assert waiting_jobs() == [] and balance(me.browser) == AFTER_SCRIPT
    assert database.value("SELECT replaced_at FROM assets WHERE id = :a", a=old) is not None


def test_choosing_an_upload_cancels_a_visual_being_made(app, database, outbox):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    job = visual_started(me.browser, scene_id, {"visualSource": "ai_image"})
    asset = upload_asset(database, me.id, project["id"])
    assert select_visual(me.browser, scene_id, {"assetId": asset}).status_code == 200
    assert get_job(me.browser, job["id"])["state"] == "cancelled"
    assert balance(me.browser) == AFTER_SCRIPT
    run_jobs(app)
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["visualStatus"] == "ready" and scene["visual"]["assetId"] == asset


@pytest.mark.parametrize(
    "bad",
    ["unknown", "not-a-uuid", "other user", "other project", "no project", "voice kind", "pending",
     "rejected", "audio", "replaced"],
)  # fmt: skip
def test_choosing_refuses_anything_but_a_finished_image_or_video_upload_of_this_project(
    app, database, outbox, bad
):
    me, other = creator(app), creator(app)
    project, scene_id = visual_project(app, me)
    second = scripted_project(app, me.browser)
    asset = {
        "unknown": lambda: str(uuid.uuid4()),
        "not-a-uuid": lambda: "not-a-uuid",
        "other user": lambda: upload_asset(database, other.id, None),
        "other project": lambda: upload_asset(database, me.id, second["id"]),
        "no project": lambda: upload_asset(database, me.id, None),
        "voice kind": lambda: upload_asset(
            database, me.id, project["id"], kind="voice", content_type="audio/mpeg"
        ),
        "pending": lambda: upload_asset(database, me.id, project["id"], status="pending_upload"),
        "rejected": lambda: upload_asset(database, me.id, project["id"], status="rejected"),
        "audio": lambda: upload_asset(database, me.id, project["id"], content_type="audio/mpeg"),
        "replaced": lambda: upload_asset(database, me.id, project["id"], replaced=True),
    }[bad]()
    before = unsigned(scene_of(me.browser, project["id"], scene_id))
    assert_invalid(select_visual(me.browser, scene_id, {"assetId": asset}), "assetId")
    assert unsigned(scene_of(me.browser, project["id"], scene_id)) == before


# ---------------------------------------------------------------- edits while the visual is being made


@pytest.mark.parametrize(
    "change",
    [{"visualPrompt": "something new"}, {"stockQuery": "forest"}, {"visualSource": "ai_image"}],
)
@pytest.mark.parametrize("had_visual", [False, True])
def test_editing_the_visual_fields_cancels_the_visual_being_made(app, database, outbox, change, had_visual):
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="ocean", visualPrompt="sea")
    old = None
    if had_visual:
        visual_started(me.browser, scene_id)
        run_jobs(app)
        old = scene_of(me.browser, project["id"], scene_id)["visual"]["assetId"]
    job = visual_started(me.browser, scene_id)
    res = me.browser.patch(f"/scenes/{scene_id}", change)
    assert res.status_code == 200
    assert res.get_json()["scene"]["visualStatus"] == ("outdated" if had_visual else "missing")
    assert get_job(me.browser, job["id"])["state"] == "cancelled"
    run_jobs(app)
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["visualStatus"] == ("outdated" if had_visual else "missing")
    assert (scene["visual"] or {}).get("assetId") == old
    assert len(visual_assets(database, project["id"])) == int(had_visual)


def test_an_edit_while_the_clip_is_being_found_drops_it_and_deletes_its_files(
    app, database, outbox, monkeypatch
):
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="ocean")
    job = visual_started(me.browser, scene_id)

    def edit():
        return me.browser.patch(f"/scenes/{scene_id}", {"stockQuery": "forest"}).status_code

    answers = during_work(monkeypatch, "stock", lambda: on_another_thread(edit))
    run_jobs(app)
    assert answers == [200]
    assert get_job(me.browser, job["id"])["state"] == "cancelled"
    assert visual_assets(database, project["id"]) == []
    assert stored_visual_files(app, me.id, project["id"]) == [], "the downloaded clip is deleted"


def test_edits_that_do_not_touch_the_visual_let_it_finish(app, database, outbox):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    job = visual_started(me.browser, scene_id)
    me.browser.patch(f"/scenes/{scene_id}", {"narration": "New words here.", "onScreenText": "Hi"})
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "succeeded"


def test_a_project_style_change_cancels_ai_visuals_only(app, database, outbox):
    """B5: style or character outdates AI visuals; AI visuals being made are cancelled."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    ai_scene, stock_scene = scene_ids(project)[:2]
    ai_job = visual_started(me.browser, ai_scene, {"visualSource": "ai_image"})
    stock_job = visual_started(me.browser, stock_scene)
    assert me.browser.patch(f"/projects/{project['id']}", {"style": "anime"}).status_code == 200
    assert get_job(me.browser, ai_job["id"])["state"] == "cancelled"
    assert get_job(me.browser, stock_job["id"])["state"] == "queued"
    assert balance(me.browser) == AFTER_SCRIPT
    run_jobs(app)
    assert get_job(me.browser, stock_job["id"])["state"] == "succeeded"
    assert scene_of(me.browser, project["id"], ai_scene)["visualStatus"] == "missing"


def test_a_character_change_while_the_image_is_made_drops_it(app, database, outbox, monkeypatch):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    job = visual_started(me.browser, scene_id, {"visualSource": "ai_image"})

    def edit():
        return me.browser.patch(f"/projects/{project['id']}", {"character": "a robot"})

    during_work(monkeypatch, "image", lambda: on_another_thread(edit))
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "cancelled"
    assert stored_visual_files(app, me.id, project["id"]) == []


def test_a_change_behind_the_apis_back_is_caught_when_the_visual_is_saved(app, database, outbox, monkeypatch):
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="ocean")
    job = visual_started(me.browser, scene_id)
    during_work(
        monkeypatch, "stock",
        lambda: database.run("UPDATE scenes SET stock_query = 'changed' WHERE id = :s", s=scene_id),
    )  # fmt: skip
    run_jobs(app)
    final = get_job(me.browser, job["id"])
    assert final["state"] == "failed" and final["error"]["code"] == "scene_changed"
    assert scene_of(me.browser, project["id"], scene_id)["visualStatus"] == "missing"
    assert stored_visual_files(app, me.id, project["id"]) == []


def test_deleting_the_scene_mid_job_deletes_the_downloaded_clip(app, database, outbox, monkeypatch):
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="ocean")
    job = visual_started(me.browser, scene_id)
    answers = during_work(
        monkeypatch,
        "stock",
        lambda: on_another_thread(lambda: me.browser.delete(f"/scenes/{scene_id}").status_code),
    )
    run_jobs(app)
    assert answers == [204]
    final = get_job(me.browser, job["id"])
    assert final["state"] == "cancelled" and final["sceneId"] is None
    assert stored_visual_files(app, me.id, project["id"]) == []


@pytest.mark.parametrize("via", ["C8 with a new prompt", "C10 stock on an AI scene"])
def test_cancelling_a_take_started_with_new_fields_does_not_bring_back_the_old_visual_as_ready(
    app, database, outbox, via
):
    """C8 saves the sent fields "as with C2", and C10 switches the source to stock. C2 would
    have made the earlier visual `outdated`; cancelling the new take must not show the old
    visual as `ready` for the new prompt or source (API design C2, C8; DB design 5)."""
    me = creator(app)
    project, scene_id = visual_project(app, me, visualPrompt="a cat")
    visual_started(me.browser, scene_id, {"visualSource": "ai_image"})
    run_jobs(app)
    if via.startswith("C8"):
        job = visual_started(me.browser, scene_id, {"visualPrompt": "a dog"})
    else:
        job = select_visual(me.browser, scene_id, {"stock": "mock:1"}).get_json()["job"]
    assert me.browser.post(f"/jobs/{job['id']}/cancel").status_code == 200
    assert scene_of(me.browser, project["id"], scene_id)["visualStatus"] == "outdated"


# ---------------------------------------------------------------- the project's status


def test_a_ready_project_stays_ready_during_a_new_visual_and_after_it(app, database, outbox):
    me = creator(app)
    project, ids = make_ready(app, database, me)
    visual_started(me.browser, ids[0])
    assert get_project(me.browser, project["id"])["status"] == "ready"
    me.browser.patch(f"/scenes/{ids[1]}", {"onScreenText": "x"})
    assert get_project(me.browser, project["id"])["status"] == "ready"
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "ready"


def test_a_failed_visual_sends_the_project_to_draft_and_a_retry_brings_it_back(app, database, outbox):
    me = creator(app)
    project, ids = make_ready(app, database, me)
    visual_started(me.browser, ids[0], {"visualSource": "ai_image", "visualPrompt": "x [image-fail]"})
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "draft"
    visual_started(me.browser, ids[0], {"visualPrompt": "x"})
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "ready"


def test_a_draft_gets_ready_when_its_last_visual_arrives(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = scene_ids(project)
    for sid in ids:
        started(me.browser, sid)
    for sid in ids[:-1]:
        visual_started(me.browser, sid)
    run_jobs(app)
    assert get_project(me.browser, project["id"])["status"] == "draft"
    asset = upload_asset(database, me.id, project["id"])
    assert select_visual(me.browser, ids[-1], {"assetId": asset}).status_code == 200
    assert get_project(me.browser, project["id"])["status"] == "ready"


# ---------------------------------------------------------------- regressions


def test_a_duplicated_scene_shares_the_visual_until_one_gets_a_new_one(app, database, outbox):
    me = creator(app)
    project, scene_id = visual_project(app, me)
    visual_started(me.browser, scene_id)
    run_jobs(app)
    shared = scene_of(me.browser, project["id"], scene_id)["visual"]["assetId"]
    copy = me.browser.post(f"/scenes/{scene_id}/duplicate").get_json()["scene"]
    assert copy["visual"]["assetId"] == shared
    visual_started(me.browser, scene_id)
    run_jobs(app)
    assert database.value("SELECT replaced_at FROM assets WHERE id = :a", a=shared) is None
    visual_started(me.browser, copy["id"])
    run_jobs(app)
    assert database.value("SELECT replaced_at FROM assets WHERE id = :a", a=shared) is not None


def test_the_estimate_counts_ai_video_scenes_as_ai_images_until_b7(app, database, outbox):
    me = creator(app)
    project, scene_id = visual_project(app, me, visualSource="ai_video")
    estimate = me.browser.get(f"/projects/{project['id']}/estimate").get_json()
    item = next(i for i in estimate["items"] if i["type"] == "ai_video")
    assert item == {"type": "ai_video", "count": 1, "credits": 1}


# ---------------------------------------------------------------- round 2: new behaviour


def test_regenerating_moves_on_through_clips_the_scene_has_not_had(app, database, outbox):
    """Fix log QB4S2-001: the last 30 clips a scene has had (`meta.seen`) are skipped."""
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="ocean")
    picked = []
    for _ in range(5):  # the mock offers 5 clips on its first page
        visual_started(me.browser, scene_id)
        run_jobs(app)
        picked.append(visual_assets(database, project["id"])[-1]["meta"]["providerId"])
    assert len(set(picked)) == 5, picked
    assert visual_assets(database, project["id"])[-1]["meta"]["seen"] == picked[:-1]


def test_seen_clips_are_capped_at_30(app, database, outbox):
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="ocean")
    visual_started(me.browser, scene_id)
    run_jobs(app)
    old = [f"mock:{n}" for n in range(100, 135)]  # 35 older clips
    database.run(
        "UPDATE assets SET meta = jsonb_set(meta, '{seen}', CAST(:s AS jsonb)) WHERE project_id = :p",
        s=json.dumps(old), p=project["id"],
    )  # fmt: skip
    current = visual_assets(database, project["id"])[-1]["meta"]["providerId"]
    visual_started(me.browser, scene_id)
    run_jobs(app)
    seen = visual_assets(database, project["id"])[-1]["meta"]["seen"]
    assert len(seen) == 30 and seen[-1] == current and seen[0] == "mock:106"


@pytest.mark.parametrize(("used", "allowed"), [(19, True), (20, False)])
def test_free_fallback_images_are_limited_to_20_a_day(app, database, outbox, used, allowed):
    """Fix log QB4S2-004: then the job fails with no_stock_match (nothing was charged)."""
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="[no-stock]")
    app.extensions["redis"].set(f"fallback:{me.id}:{datetime.now(UTC):%Y-%m-%d}", used)
    job = visual_started(me.browser, scene_id)
    run_jobs(app)
    final = get_job(me.browser, job["id"])
    if allowed:
        assert final["state"] == "succeeded" and final["output"]["stockFallback"] is True
    else:
        assert final["state"] == "failed" and final["error"]["code"] == "no_stock_match"
        assert final["error"]["message"] == jobs.MESSAGES["no_stock_match"]
        scene = scene_of(me.browser, project["id"], scene_id)
        assert scene["visualStatus"] == "failed" and scene["lastError"] == final["error"]["message"]
        assert stored_visual_files(app, me.id, project["id"]) == []
    assert balance(me.browser) == AFTER_SCRIPT


def test_the_free_fallback_count_is_per_creator_and_per_day(app, database, outbox):
    me, other = creator(app), creator(app)
    redis = app.extensions["redis"]
    redis.set(f"fallback:{me.id}:{datetime.now(UTC):%Y-%m-%d}", 20)
    redis.set(f"fallback:{other.id}:{(datetime.now(UTC) - timedelta(days=1)):%Y-%m-%d}", 20)
    _, scene_id = visual_project(app, other, stockQuery="[no-stock]")
    job = visual_started(other.browser, scene_id)
    run_jobs(app)
    assert get_job(other.browser, job["id"])["state"] == "succeeded"
    ttl = redis.ttl(f"fallback:{other.id}:{datetime.now(UTC):%Y-%m-%d}")
    assert 0 < ttl <= 2 * 24 * 3600


def test_c8_is_limited_to_60_an_hour_counting_only_started_work(limited_app, database, outbox):
    from reelforge_api.ratelimit import _key

    me = creator(limited_app)
    project, scene_id = visual_project(limited_app, me)
    redis = limited_app.extensions["redis"]
    redis.set(_key("visual", me.id), 59, ex=3600)
    visual_started(me.browser, scene_id)  # the 60th
    assert_error(regenerate_visual(me.browser, scene_id), 409, "already_running")  # refused: not counted
    run_jobs(limited_app)
    res = regenerate_visual(me.browser, scene_id, {"stockQuery": "new words"})
    error = assert_error(res, 429, "rate_limited")
    assert int(res.headers["Retry-After"]) == error["details"]["retryAfterSeconds"] > 0
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["stockQuery"] != "new words" and scene["visualStatus"] == "ready", "nothing was saved"
    assert waiting_jobs() == []


def test_a_new_prompt_outdates_the_visual_before_the_take_so_the_project_is_not_ready_meanwhile(
    app, database, outbox
):
    """BUG-043 fix: the project doesn't count the old visual as ready for the new prompt."""
    me = creator(app)
    project, ids = make_ready(app, database, me)
    visual_started(me.browser, ids[0])  # same fields: the old visual still counts
    assert get_project(me.browser, project["id"])["status"] == "ready"
    run_jobs(app)
    job = visual_started(me.browser, ids[0], {"stockQuery": "something else"})
    assert get_project(me.browser, project["id"])["status"] == "draft"
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "succeeded"
    assert get_project(me.browser, project["id"])["status"] == "ready"


def test_a_style_change_outdates_fallback_images_but_not_stock_clips(app, database, outbox):
    """Fix log QB4S2-006: a stock scene showing an AI image is in the old style too."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    clip_scene, image_scene = scene_ids(project)[:2]
    me.browser.patch(f"/scenes/{image_scene}", {"stockQuery": "[no-stock]"})
    visual_started(me.browser, clip_scene)
    visual_started(me.browser, image_scene)
    run_jobs(app)
    assert scene_of(me.browser, project["id"], image_scene)["visual"]["stockFallback"] is True
    assert me.browser.patch(f"/projects/{project['id']}", {"character": "a robot"}).status_code == 200
    assert scene_of(me.browser, project["id"], image_scene)["visualStatus"] == "outdated"
    assert scene_of(me.browser, project["id"], clip_scene)["visualStatus"] == "ready"


def test_a_fallback_image_made_in_the_old_style_is_dropped(app, database, outbox, monkeypatch):
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="[no-stock]")
    job = visual_started(me.browser, scene_id)

    def restyle():
        return me.browser.patch(f"/projects/{project['id']}", {"style": "anime"}).status_code

    answers = during_work(monkeypatch, "image", lambda: on_another_thread(restyle))
    run_jobs(app)
    assert answers == [200]
    final = get_job(me.browser, job["id"])
    assert final["state"] == "failed" and final["error"]["code"] == "scene_changed"
    assert scene_of(me.browser, project["id"], scene_id)["visualStatus"] == "missing"
    assert stored_visual_files(app, me.id, project["id"]) == []


# ---------------------------------------------------------------- round 3


def test_a_style_change_cancels_a_new_take_on_a_scene_showing_a_fallback_image(app, database, outbox):
    """Fix log QB4S2-010: the scene ends `outdated`, never `ready` with the old-style image."""
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="[no-stock]")
    visual_started(me.browser, scene_id)
    run_jobs(app)
    assert scene_of(me.browser, project["id"], scene_id)["visual"]["stockFallback"] is True
    job = visual_started(me.browser, scene_id)
    assert me.browser.patch(f"/projects/{project['id']}", {"style": "anime"}).status_code == 200
    cancelled = get_job(me.browser, job["id"])
    assert cancelled["state"] == "cancelled"
    assert scene_of(me.browser, project["id"], scene_id)["visualStatus"] == "outdated"
    run_jobs(app)
    assert scene_of(me.browser, project["id"], scene_id)["visualStatus"] == "outdated"


def seen_clips(database, project_id: str, ids: list[str]) -> None:
    """Pretends the scene has had these clips, and forgets cached searches so every page the
    next take looks at reaches the provider."""
    redis = database._app.extensions["redis"]
    for key in redis.scan_iter("stock:*"):
        redis.delete(key)
    database.run(
        "UPDATE assets SET meta = jsonb_set(meta, '{seen}', CAST(:s AS jsonb)) WHERE project_id = :p",
        s=json.dumps(ids), p=project_id,
    )  # fmt: skip


def test_regenerate_looks_at_later_result_pages_before_the_ai_image(app, database, outbox, monkeypatch):
    """Fix log QB4S2-011: pages 1 to STOCK_PAGES of each query (the mock: 5 clips a page)."""
    from reelforge_api.visuals import STOCK_PAGES

    assert STOCK_PAGES == 3
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="ocean")
    visual_started(me.browser, scene_id)
    run_jobs(app)
    seen_clips(database, project["id"], [f"mock:{n}" for n in range(1, 6)])  # all of page 1
    queries = searched(monkeypatch)
    visual_started(me.browser, scene_id)
    run_jobs(app)
    assert visual_assets(database, project["id"])[-1]["meta"]["providerId"] == "mock:6"
    assert queries == [("ocean", 1), ("ocean", 2)]


def test_after_three_pages_of_seen_clips_the_scene_gets_the_ai_image(app, database, outbox, monkeypatch):
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="ocean")
    visual_started(me.browser, scene_id)
    run_jobs(app)
    seen_clips(database, project["id"], [f"mock:{n}" for n in range(1, 16)])
    queries = searched(monkeypatch)
    job = visual_started(me.browser, scene_id)
    run_jobs(app)
    assert queries == [("ocean", 1), ("ocean", 2), ("ocean", 3)], "no page 4; one word has no simpler query"
    assert get_job(me.browser, job["id"])["output"]["stockFallback"] is True


def fallback_count(app, user_id: str) -> int:
    return int(app.extensions["redis"].get(f"fallback:{user_id}:{datetime.now(UTC):%Y-%m-%d}") or 0)


def test_only_fallback_images_actually_made_are_counted(app, database, outbox):
    """Fix log QB4S2-012: checked before, counted after the image is made."""
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery="[no-stock]", visualPrompt="sea [image-fail]")
    job = visual_started(me.browser, scene_id)
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "failed"
    assert fallback_count(app, me.id) == 0, "a failed image is not counted"
    me.browser.patch(f"/scenes/{scene_id}", {"visualPrompt": "sea"})
    visual_started(me.browser, scene_id)
    run_jobs(app)
    assert fallback_count(app, me.id) == 1
    clip_scene = scene_ids(get_project(me.browser, project["id"]))[1]
    visual_started(me.browser, clip_scene)
    run_jobs(app)
    assert fallback_count(app, me.id) == 1, "a stock clip is not counted"


@pytest.mark.parametrize(
    ("query", "code"), [("ocean [stock-fail]", "provider_unavailable"), ("[no-stock]", "no_stock_match")]
)
def test_with_the_days_free_images_used_the_error_says_why(app, database, outbox, query, code):
    """Stock down → provider_unavailable; nothing fits → no_stock_match."""
    me = creator(app)
    project, scene_id = visual_project(app, me, stockQuery=query)
    app.extensions["redis"].set(f"fallback:{me.id}:{datetime.now(UTC):%Y-%m-%d}", 20)
    job = visual_started(me.browser, scene_id)
    run_jobs(app)
    failed = get_job(me.browser, job["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == code
    assert fallback_count(app, me.id) == 20
    assert stored_visual_files(app, me.id, project["id"]) == []
