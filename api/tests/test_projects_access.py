"""Who may call the B3 endpoints: no session, CSRF, User vs Verified, ownership (another user's
project, scene or job is 404, never 403), and the shared hourly limit on language-model work
(docs/API_DESIGN.md Conventions, groups B, C, G, H and section 4)."""

from __future__ import annotations

import uuid

import pytest

from .projects_support import (
    TOPIC,
    balance,
    creator,
    get_project,
    run_jobs,
    scripted_project,
    waiting_jobs,
)
from .support import Browser, assert_error

VERIFIED, USER = "verified", "user"

# (method, path, body, level). {p} project, {s} scene, {j} job of the owner.
ENDPOINTS = [
    ("POST", "/ideas", {"niche": "fitness"}, VERIFIED),
    ("GET", "/projects", None, USER),
    ("POST", "/projects", {"topic": TOPIC}, VERIFIED),
    ("GET", "/projects/{p}", None, USER),
    ("PATCH", "/projects/{p}", {"title": "Hijacked"}, USER),
    ("POST", "/projects/{p}/duplicate", None, USER),
    ("POST", "/projects/{p}/script/regenerate", {}, VERIFIED),
    ("POST", "/projects/{p}/hooks", None, VERIFIED),
    ("GET", "/projects/{p}/estimate", None, USER),
    ("POST", "/projects/{p}/scenes", {"narration": "Injected scene."}, USER),
    ("PATCH", "/scenes/{s}", {"narration": "Hijacked."}, USER),
    ("POST", "/scenes/{s}/duplicate", None, USER),
    ("POST", "/projects/{p}/scenes/reorder", {"sceneIds": "{ids}"}, USER),
    ("POST", "/scenes/{s}/rewrite", {"instructions": "shorter"}, VERIFIED),
    ("GET", "/jobs/{j}", None, USER),
    ("GET", "/projects/{p}/jobs", None, USER),
    ("POST", "/jobs/{j}/cancel", None, USER),
    ("GET", "/credits", None, USER),
    ("GET", "/credits/ledger", None, USER),
    ("DELETE", "/scenes/{s}", None, USER),
    ("DELETE", "/projects/{p}", None, USER),
]
OWNED = [e for e in ENDPOINTS if "{" in e[1]]


def fill(path: str, body, project: dict, job_id: str):
    ids = {"p": project["id"], "s": project["scenes"][-1]["id"], "j": job_id}
    if isinstance(body, dict) and body.get("sceneIds") == "{ids}":
        body = {"sceneIds": [s["id"] for s in reversed(project["scenes"])]}
    return path.format(**ids), body


@pytest.fixture
def owned(app, outbox):
    """A creator with a written project and a queued rewrite job."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    job = me.browser.post(f"/scenes/{project['scenes'][0]['id']}/rewrite", {"instructions": "x"}).get_json()[
        "job"
    ]
    return me, get_project(me.browser, project["id"]), job["id"]


@pytest.mark.parametrize(("method", "path", "body", "level"), ENDPOINTS)
def test_every_endpoint_needs_a_session(app, owned, method, path, body, level):
    _, project, job_id = owned
    path, body = fill(path, body, project, job_id)
    assert_error(Browser(app).call(method, path, body), 401, "not_authenticated")


@pytest.mark.parametrize(("method", "path", "body", "level"), [e for e in ENDPOINTS if e[0] != "GET"])
def test_every_change_needs_the_csrf_header(app, owned, method, path, body, level):
    me, project, job_id = owned
    path, body = fill(path, body, project, job_id)
    assert_error(me.browser.call(method, path, body, csrf=None), 403, "csrf_failed")
    assert_error(me.browser.call(method, path, body, csrf="wrong"), 403, "csrf_failed")
    assert get_project(me.browser, project["id"]) == project


@pytest.mark.parametrize(("method", "path", "body", "level"), ENDPOINTS)
def test_an_unverified_creator_can_edit_but_not_start_ai_work(
    app, database, owned, method, path, body, level
):
    """Verified endpoints are 403 email_not_verified; User endpoints work."""
    me, project, job_id = owned
    database.run("UPDATE users SET email_verified_at = NULL WHERE id = :u", u=me.id)
    path, body = fill(path, body, project, job_id)
    before = balance(me.browser)
    queued = len(waiting_jobs())
    res = me.browser.call(method, path, body)
    if level == VERIFIED:
        assert_error(res, 403, "email_not_verified")
        assert balance(me.browser) == before
        assert len(waiting_jobs()) == queued
    else:
        assert res.status_code in (200, 201, 204), (path, res.status_code, res.get_data(as_text=True))


@pytest.mark.parametrize(("method", "path", "body", "level"), OWNED)
def test_another_creators_projects_scenes_and_jobs_are_404(app, database, owned, method, path, body, level):
    """Ownership: never 403, so ids can't be probed; nothing changes and nothing is charged."""
    me, project, job_id = owned
    stranger = creator(app)
    path, body = fill(path, body, project, job_id)
    owner_balance = balance(me.browser)
    ledger = database.value("SELECT count(*) FROM credit_ledger")
    assert_error(stranger.browser.call(method, path, body), 404, "not_found")
    assert get_project(me.browser, project["id"]) == project
    assert balance(me.browser) == owner_balance
    assert balance(stranger.browser) == 100
    assert database.value("SELECT count(*) FROM credit_ledger") == ledger
    assert database.value("SELECT count(*) FROM projects WHERE user_id = :u", u=stranger.id) == 0
    assert database.value("SELECT count(*) FROM jobs WHERE user_id = :u", u=stranger.id) == 0
    assert database.value("SELECT state FROM jobs WHERE id = :j", j=job_id) == "queued"


@pytest.mark.parametrize("bad", ["00000000-0000-0000-0000-000000000000", "abc", "%27%20OR%201%3D1--", "1e3"])
def test_malformed_or_unknown_ids_are_404(app, outbox, bad):
    me = creator(app)
    for method, path, body in (
        ("GET", "/projects/{x}", None),
        ("GET", "/projects/{x}/estimate", None),
        ("POST", "/projects/{x}/scenes", {"narration": "x"}),
        ("PATCH", "/scenes/{x}", {"narration": "x"}),
        ("POST", "/scenes/{x}/rewrite", {"instructions": "x"}),
        ("GET", "/jobs/{x}", None),
        ("POST", "/jobs/{x}/cancel", None),
    ):
        assert_error(me.browser.call(method, path.format(x=bad), body), 404, "not_found")


def test_a_stranger_cannot_reorder_my_scenes_into_their_project(app, outbox):
    me, stranger = creator(app), creator(app)
    mine = scripted_project(app, me.browser)
    theirs = scripted_project(app, stranger.browser)
    res = stranger.browser.post(
        f"/projects/{theirs['id']}/scenes/reorder", {"sceneIds": [s["id"] for s in mine["scenes"]]}
    )
    assert_error(res, 400, "validation_error")
    assert get_project(me.browser, mine["id"]) == mine


def test_a_stranger_cannot_use_my_upload_as_their_music(app, database, outbox):
    me, stranger = creator(app), creator(app)
    mine = scripted_project(app, me.browser)
    theirs = scripted_project(app, stranger.browser)
    asset = str(uuid.uuid4())
    database.run(
        "INSERT INTO assets (id, user_id, project_id, kind, s3_key, content_type, provider) "
        "VALUES (:id, :u, :p, 'music', :k, 'audio/mpeg', 'upload')",
        id=asset,
        u=me.id,
        p=mine["id"],
        k=f"test/{asset}",
    )
    res = stranger.browser.patch(f"/projects/{theirs['id']}", {"music": {"assetId": asset}})
    assert_error(res, 400, "validation_error")
    assert get_project(stranger.browser, theirs["id"])["music"] is None


# ---------------------------------------------------------------- rate limits


def test_language_model_work_shares_one_limit_of_20_per_hour(limited_app, database, outbox):
    """B1 ideas, B3 and B8 scripts, B9 hooks and C6 rewrite: 20 per hour per user combined."""
    me = creator(limited_app, ip="10.0.0.1")
    project = scripted_project(limited_app, me.browser)  # 1
    scene = project["scenes"][0]["id"]
    assert me.browser.post(f"/projects/{project['id']}/hooks").status_code == 202  # 2
    assert me.browser.post(f"/scenes/{scene}/rewrite", {"instructions": "x"}).status_code == 202  # 3
    run_jobs(limited_app)
    assert me.browser.post(f"/projects/{project['id']}/script/regenerate", {}).status_code == 202  # 4
    run_jobs(limited_app)
    scene = get_project(me.browser, project["id"])["scenes"][0]["id"]  # the script replaced the scenes
    for _ in range(16):  # 5..20
        assert me.browser.post("/ideas", {"niche": "fitness"}).status_code == 202
    run_jobs(limited_app)
    before = balance(me.browser)

    for path, body in (
        ("/ideas", {"niche": "fitness"}),
        ("/projects", {"topic": TOPIC}),
        (f"/projects/{project['id']}/script/regenerate", {}),
        (f"/projects/{project['id']}/hooks", None),
        (f"/scenes/{scene}/rewrite", {"instructions": "x"}),
    ):
        res = me.browser.post(path, body)
        error = assert_error(res, 429, "rate_limited")
        seconds = error["details"]["retryAfterSeconds"]
        assert 0 < seconds <= 3600
        assert res.headers["Retry-After"] == str(seconds)
    assert balance(me.browser) == before, "nothing charged"
    assert waiting_jobs() == []
    assert database.value("SELECT count(*) FROM projects WHERE user_id = :u", u=me.id) == 1

    # Editing is not language-model work; another creator has their own limit.
    assert me.browser.patch(f"/projects/{project['id']}", {"title": "Still editable"}).status_code == 200
    other = creator(limited_app, ip="10.0.0.2")
    assert other.browser.post("/ideas", {"niche": "food"}).status_code == 202
    run_jobs(limited_app)
