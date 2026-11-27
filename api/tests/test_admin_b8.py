"""Milestone B8: the admin API (docs/API_DESIGN.md group I, I1 to I14, with the "As built (B8)"
notes), the clean-up of replaced files, the purge deleting files first, and the beat schedule.

Offline: the mock providers of the test settings; the two tests about real provider names
(elevenlabs, edge) stub every network call the way test_voice_providers.py does.
"""

# ruff: noqa: F811 - fixtures imported from other test modules are named again as arguments
from __future__ import annotations

import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta

import pytest
from celery.schedules import crontab

from reelforge_api import cleanup
from reelforge_api.storage import Storage
from reelforge_api.tasks.accounts import purge_deleted

from .projects_support import (
    JOB_FIELDS,
    assert_ledger_matches_balance,
    balance,
    creator,
    get_job,
    get_project,
    ledger_rows,
    new_asset,
    run_jobs,
    scripted_project,
    stored_balance,
    waiting_jobs,
)
from .publish_support import connect, fresh_youtube, publish, rendered_project  # noqa: F401 - fixture
from .render_support import ready_project
from .support import PASSWORD, Browser, assert_error, assert_invalid, log_in
from .test_voice_providers import (  # noqa: F401 - fixtures
    edge,
    edge_words,
    eleven_app,
    http,
    response,
    voice_scene,
)
from .voice_support import (  # noqa: F401 - no_real_voice_services is an autouse fixture
    download,
    no_real_voice_services,
    regenerate,
    scene_of,
    started,
)

USER_ROW_FIELDS = {
    "id", "email", "name", "isAdmin", "isTester", "disabled", "emailVerified", "balance",
    "monthlyAllowance", "projectCount", "lastLoginAt", "createdAt",
}  # fmt: skip
PROVIDER_NAMES = ["gemini", "groq", "elevenlabs", "edge", "pixabay", "cloudflare", "youtube"]
PROVIDER_FIELDS = {"name", "purpose", "enabled", "reason", "callsToday", "errorRateToday"}
TRACK_FIELDS = {
    "id", "title", "durationSeconds", "moods", "license", "sourceUrl", "previewUrl", "isActive",
    "createdAt",
}  # fmt: skip
TRACK = {
    "title": "Night Drive", "moods": ["calm"], "license": "CC BY 4.0: test", "filename": "night.mp3",
    "contentType": "audio/mpeg", "sizeBytes": 1000, "durationSeconds": 60,
}  # fmt: skip
NOBODY = "00000000-0000-4000-8000-000000000000"

# Every admin endpoint (I1 to I14), with a body that would be accepted from an admin.
ENDPOINTS = [
    ("GET", "/admin/users", None),
    ("GET", f"/admin/users/{NOBODY}", None),
    ("PATCH", f"/admin/users/{NOBODY}", {"isTester": True}),
    ("POST", f"/admin/users/{NOBODY}/credits", {"amount": 5, "note": "x"}),
    ("GET", "/admin/jobs", None),
    ("POST", f"/admin/jobs/{NOBODY}/retry", None),
    ("GET", "/admin/usage", None),
    ("GET", "/admin/providers", None),
    ("PATCH", "/admin/providers/gemini", {"enabled": False, "reason": "not allowed"}),
    ("GET", "/admin/music", None),
    ("POST", "/admin/music", TRACK),
    ("PATCH", f"/admin/music/{NOBODY}", {"title": "x"}),
    ("DELETE", f"/admin/music/{NOBODY}", None),
    ("GET", "/admin/stats", None),
]


def admin_of(app, database, **kwargs):
    who = creator(app, **kwargs)
    database.run("UPDATE users SET is_admin = true WHERE id = :id", id=who.id)
    return who


def keys_under(app, prefix: str) -> list[str]:
    storage = app.extensions["storage"]
    found = storage.client.list_objects_v2(Bucket=storage.bucket, Prefix=prefix)
    return sorted(item["Key"] for item in found.get("Contents", []))


def put_file(app, key: str, data: bytes = b"data") -> None:
    app.extensions["storage"].put_bytes(key, data, "application/octet-stream")


def file_exists(app, key: str) -> bool:
    return app.extensions["storage"].exists(key)


def asset_with_file(app, database, user_id: str, project_id: str, kind: str = "voice") -> tuple[str, str]:
    """An asset row whose file really is in storage. Returns (asset id, key)."""
    asset_id = new_asset(database, user_id, project_id, kind)
    key = f"test/{asset_id}"
    put_file(app, key)
    return asset_id, key


def replaced(database, asset_id: str, ago: str) -> None:
    database.run(
        f"UPDATE assets SET replaced_at = now() - interval '{ago}' WHERE id = :a",  # noqa: S608 - fixed text
        a=asset_id,
    )


def failed_voice_job(app, database, me) -> tuple[dict, str, dict]:
    """A voice job that failed because no voice provider was on. Returns (project, scene id,
    the failed job); the provider is on again afterwards."""
    project = scripted_project(app, me.browser)
    scene_id = project["scenes"][0]["id"]
    job = started(me.browser, scene_id)
    database.run("INSERT INTO provider_switches (provider, enabled, reason) VALUES ('mock', false, 'test')")
    run_jobs(app)
    database.run("DELETE FROM provider_switches WHERE provider = 'mock'")
    job = get_job(me.browser, job["id"])
    assert job["state"] == "failed", job
    return project, scene_id, job


def failed_ideas_job(app, me) -> dict:
    res = me.browser.post("/ideas", {"niche": "fitness [fail]"})
    assert res.status_code == 202, res.get_data(as_text=True)
    run_jobs(app)
    job = get_job(me.browser, res.get_json()["job"]["id"])
    assert job["state"] == "failed", job
    return job


def retry(admin, job_id: str):
    return admin.browser.post(f"/admin/jobs/{job_id}/retry")


# ---------------------------------------------------------------- admins only


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS, ids=[f"{m} {p}" for m, p, _ in ENDPOINTS])
def test_every_admin_endpoint_refuses_signed_out_callers_and_non_admins(app, database, method, path, body):
    assert_error(Browser(app).call(method, path, body), 401, "not_authenticated")
    me = creator(app)
    assert_error(me.browser.call(method, path, body), 403, "forbidden")


def test_a_refused_non_admin_changes_nothing(app, database):
    me, other = creator(app), creator(app)
    me.browser.patch("/admin/providers/gemini", {"enabled": False, "reason": "mine now"})
    me.browser.post(f"/admin/users/{me.id}/credits", {"amount": 500, "note": "free credits"})
    me.browser.patch(f"/admin/users/{me.id}", {"isAdmin": True})
    me.browser.patch(f"/admin/users/{other.id}", {"disabled": True})
    me.browser.post("/admin/music", TRACK)
    assert database.value("SELECT count(*) FROM provider_switches") == 0
    assert stored_balance(database, me.id) == 100
    assert database.value("SELECT is_admin FROM users WHERE id = :id", id=me.id) is False
    assert database.value("SELECT disabled_at IS NULL FROM users WHERE id = :id", id=other.id) is True
    assert database.value("SELECT count(*) FROM music_tracks") == 0


def test_an_admin_role_removed_takes_effect_on_the_next_request(app, database):
    boss, second = admin_of(app, database), admin_of(app, database)
    assert second.browser.get("/admin/stats").status_code == 200
    res = boss.browser.patch(f"/admin/users/{second.id}", {"isAdmin": False})
    assert res.status_code == 200 and res.get_json()["user"]["isAdmin"] is False
    assert_error(second.browser.get("/admin/stats"), 403, "forbidden")


def test_admin_changes_need_the_csrf_header(app, database):
    admin, other = admin_of(app, database), creator(app)
    res = admin.browser.post(f"/admin/users/{other.id}/credits", {"amount": 5, "note": "x"}, csrf=None)
    assert_error(res, 403, "csrf_failed")
    assert stored_balance(database, other.id) == 100


# ---------------------------------------------------------------- I1 users list


def test_users_list_is_newest_first_with_the_documented_fields(app, database):
    admin = admin_of(app, database)
    other = creator(app, name="Second Person")
    scripted_project(app, other.browser)
    items = admin.browser.get("/admin/users").get_json()["items"]
    assert [u["id"] for u in items] == [other.id, admin.id]
    assert set(items[0]) == USER_ROW_FIELDS
    assert items[0]["email"] == other.account.email and items[0]["name"] == "Second Person"
    assert items[0]["projectCount"] == 1 and items[0]["balance"] == balance(other.browser)
    assert items[0]["monthlyAllowance"] == 100 and items[0]["emailVerified"] is True
    assert items[0]["isAdmin"] is False and items[1]["isAdmin"] is True
    assert items[0]["disabled"] is False and items[0]["isTester"] is False


def test_users_list_pages_with_cursors_without_repeats_or_gaps(app, database):
    admin = admin_of(app, database)
    everyone = [admin.id] + [creator(app).id for _ in range(4)]
    seen, cursor, pages = [], None, 0
    while True:
        path = "/admin/users?limit=2" + (f"&cursor={cursor}" if cursor else "")
        body = admin.browser.get(path).get_json()
        assert 1 <= len(body["items"]) <= 2
        seen += [u["id"] for u in body["items"]]
        pages += 1
        cursor = body["nextCursor"]
        if cursor is None:
            break
        assert pages < 10, "the cursor never ends"
    assert seen == list(reversed(everyone)) and pages == 3


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=abc", "limit=-1"])
def test_users_list_refuses_a_bad_limit(app, database, query):
    admin = admin_of(app, database)
    assert_invalid(admin.browser.get(f"/admin/users?{query}"), "limit")


def test_admin_lists_refuse_a_bad_cursor(app, database):
    admin = admin_of(app, database)
    for path in ("/admin/users", "/admin/jobs"):
        assert_invalid(admin.browser.get(f"{path}?cursor=not-a-cursor"), "cursor")


def test_users_search_matches_email_or_name_in_any_case(app, database):
    admin = admin_of(app, database, name="The Admin")
    asha = creator(app, name="Asha Rao", email="asha.rao@example.com")
    zed = creator(app, name="Zed Person", email="someone.else@example.com")

    def found(q: str) -> list[str]:
        return [u["id"] for u in admin.browser.get("/admin/users", query_string={"q": q}).get_json()["items"]]

    assert found("ASHA.RAO@") == [asha.id]
    assert found("zed per") == [zed.id]
    assert found("  zed  ") == [zed.id]
    assert found("nobody-by-this-name") == []
    assert found("") == [zed.id, asha.id, admin.id]


def test_users_search_finds_an_address_with_an_underscore(app, database):
    """An underscore is an ordinary character in an email address."""
    admin = admin_of(app, database)
    target = creator(app, email="first_last@example.com")
    creator(app, email="firstxlast@example.com")
    items = admin.browser.get("/admin/users", query_string={"q": "first_last"}).get_json()["items"]
    assert [u["id"] for u in items] == [target.id]


def test_users_search_treats_a_percent_sign_as_text(app, database):
    admin = admin_of(app, database)
    creator(app)
    items = admin.browser.get("/admin/users", query_string={"q": "%"}).get_json()["items"]
    assert items == [], "a search for % listed every user"


def test_deleted_accounts_are_not_listed_or_opened(app, database):
    admin, gone = admin_of(app, database), creator(app, name="Gone Person")
    assert gone.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    assert [u["id"] for u in admin.browser.get("/admin/users").get_json()["items"]] == [admin.id]
    assert admin.browser.get("/admin/users?q=gone").get_json()["items"] == []
    assert_error(admin.browser.get(f"/admin/users/{gone.id}"), 404, "not_found")
    assert_error(admin.browser.patch(f"/admin/users/{gone.id}", {"isTester": True}), 404, "not_found")
    assert_error(
        admin.browser.post(f"/admin/users/{gone.id}/credits", {"amount": 5, "note": "x"}), 404, "not_found"
    )


# ---------------------------------------------------------------- I2 one user


def test_user_detail_has_credits_recent_jobs_and_the_youtube_connection(app, database, fresh_youtube):
    admin, other = admin_of(app, database), creator(app)
    project = scripted_project(app, other.browser)
    connect(other.browser)
    other.browser.patch("/me", {"niche": "Fitness, Finance"})
    detail = admin.browser.get(f"/admin/users/{other.id}").get_json()["user"]
    assert set(detail) == USER_ROW_FIELDS | {"niche", "resetsAt", "youtube", "recentJobs"}
    assert detail["niche"] == "Fitness, Finance" and detail["projectCount"] == 1
    assert detail["resetsAt"] == other.browser.get("/credits").get_json()["resetsAt"]
    assert detail["youtube"]["status"] == "connected" and detail["youtube"]["name"]
    assert [j["type"] for j in detail["recentJobs"]] == ["script"]
    assert set(detail["recentJobs"][0]) == JOB_FIELDS
    assert detail["recentJobs"][0]["projectId"] == project["id"]


def test_user_detail_lists_at_most_20_jobs_newest_first(app, database):
    admin, other = admin_of(app, database), creator(app)
    for n in range(22):
        database.run(
            "INSERT INTO jobs (user_id, type, state, queued_at) "
            "VALUES (:u, 'ideas', 'succeeded', now() - make_interval(mins => :n))",
            u=other.id,
            n=n,
        )
    jobs = admin.browser.get(f"/admin/users/{other.id}").get_json()["user"]["recentJobs"]
    assert len(jobs) == 20
    assert [j["queuedAt"] for j in jobs] == sorted((j["queuedAt"] for j in jobs), reverse=True)


@pytest.mark.parametrize("user_id", [NOBODY, "not-a-uuid", "123"])
def test_an_unknown_user_is_404_everywhere(app, database, user_id):
    admin = admin_of(app, database)
    assert_error(admin.browser.get(f"/admin/users/{user_id}"), 404, "not_found")
    assert_error(admin.browser.patch(f"/admin/users/{user_id}", {"isTester": True}), 404, "not_found")
    assert_error(
        admin.browser.post(f"/admin/users/{user_id}/credits", {"amount": 5, "note": "x"}), 404, "not_found"
    )


# ---------------------------------------------------------------- I3 update a user


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"monthlyAllowance": -1}, "monthlyAllowance"),
        ({"monthlyAllowance": 100_001}, "monthlyAllowance"),
        ({"monthlyAllowance": "500"}, "monthlyAllowance"),
        ({"monthlyAllowance": 10.5}, "monthlyAllowance"),
        ({"isTester": "yes"}, "isTester"),
        ({"disabled": 1}, "disabled"),
        ({"isAdmin": "true"}, "isAdmin"),
        ({"balance": 5000}, "balance"),
    ],
)
def test_update_user_validates_its_body(app, database, body, field):
    admin, other = admin_of(app, database), creator(app)
    before = admin.browser.get(f"/admin/users/{other.id}").get_json()["user"]
    assert_invalid(admin.browser.patch(f"/admin/users/{other.id}", body), field)
    assert admin.browser.get(f"/admin/users/{other.id}").get_json()["user"] == before


def test_update_user_sets_tester_allowance_and_admin(app, database):
    admin, other = admin_of(app, database), creator(app)
    res = admin.browser.patch(
        f"/admin/users/{other.id}", {"isTester": True, "monthlyAllowance": 500, "isAdmin": True}
    )
    assert res.status_code == 200
    user = res.get_json()["user"]
    assert set(user) == USER_ROW_FIELDS
    assert (user["isTester"], user["monthlyAllowance"], user["isAdmin"]) == (True, 500, True)
    assert user["balance"] == 100, "the allowance applies from the next reset; the balance is unchanged"
    assert other.browser.get("/credits").get_json()["monthlyAllowance"] == 500
    assert other.browser.get("/me").get_json()["user"]["isAdmin"] is True
    assert other.browser.get("/admin/stats").status_code == 200
    assert admin.browser.patch(f"/admin/users/{other.id}", {"monthlyAllowance": 0}).get_json()["user"][
        "monthlyAllowance"
    ] == 0  # fmt: skip


def test_making_someone_a_tester_gives_the_tester_allowance_and_back(app, database):
    """QB8-010: the toggle sets the monthly allowance to the tester or the normal default."""
    settings = app.extensions["settings"]
    assert settings.monthly_credits_tester > settings.monthly_credits
    admin, other = admin_of(app, database), creator(app)
    on = admin.browser.patch(f"/admin/users/{other.id}", {"isTester": True}).get_json()["user"]
    assert (on["isTester"], on["monthlyAllowance"]) == (True, settings.monthly_credits_tester)
    assert on["balance"] == 100, "the larger allowance arrives with the next reset"
    off = admin.browser.patch(f"/admin/users/{other.id}", {"isTester": False}).get_json()["user"]
    assert (off["isTester"], off["monthlyAllowance"]) == (False, settings.monthly_credits)


def test_an_allowance_given_with_the_tester_toggle_wins_and_an_unchanged_toggle_keeps_it(app, database):
    admin, other = admin_of(app, database), creator(app)
    both = admin.browser.patch(f"/admin/users/{other.id}", {"isTester": True, "monthlyAllowance": 750})
    assert both.get_json()["user"]["monthlyAllowance"] == 750
    # Sending the same value again is not a toggle: the hand-set allowance stays.
    again = admin.browser.patch(f"/admin/users/{other.id}", {"isTester": True})
    assert again.get_json()["user"]["monthlyAllowance"] == 750
    # Other changes leave it alone too.
    admin.browser.patch(f"/admin/users/{other.id}", {"isAdmin": True})
    assert (
        database.value("SELECT monthly_allowance FROM credit_accounts WHERE user_id = :u", u=other.id) == 750
    )


def test_an_admin_cannot_disable_or_demote_themselves(app, database):
    admin = admin_of(app, database)
    for body in ({"disabled": True}, {"isAdmin": False}, {"isTester": True, "isAdmin": False}):
        assert_error(admin.browser.patch(f"/admin/users/{admin.id}", body), 409, "own_account")
    row = database.one("SELECT is_admin, is_tester, disabled_at FROM users WHERE id = :id", id=admin.id)
    assert row == {"is_admin": True, "is_tester": False, "disabled_at": None}
    assert admin.browser.get("/admin/stats").status_code == 200
    # Everything else about their own account is theirs to change.
    res = admin.browser.patch(
        f"/admin/users/{admin.id}",
        {"isTester": True, "monthlyAllowance": 500, "disabled": False, "isAdmin": True},
    )
    assert res.status_code == 200 and res.get_json()["user"]["isTester"] is True


def test_disabling_a_user_ends_every_session_and_refuses_new_ones(app, database):
    admin, other = admin_of(app, database), creator(app)
    phone = log_in(app, other.account)
    res = admin.browser.patch(f"/admin/users/{other.id}", {"disabled": True})
    assert res.status_code == 200 and res.get_json()["user"]["disabled"] is True
    for browser in (other.browser, phone):
        assert browser.get("/me").status_code in (401, 403)
        assert browser.post("/auth/refresh").status_code in (401, 403)
        assert browser.post("/projects", {"topic": "anything at all"}).status_code in (401, 403)
    assert database.value(
        "SELECT count(*) FROM refresh_tokens WHERE user_id = :u AND revoked_at IS NULL", u=other.id
    ) == 0  # fmt: skip
    login = Browser(app).post("/auth/login", {"email": other.account.email, "password": PASSWORD})
    assert login.status_code in (401, 403)
    assert admin.browser.get("/admin/users?q=" + other.account.email).get_json()["items"][0]["disabled"]


def test_enabling_a_user_again_lets_them_log_in_but_does_not_revive_old_sessions(app, database):
    admin, other = admin_of(app, database), creator(app)
    assert admin.browser.patch(f"/admin/users/{other.id}", {"disabled": True}).status_code == 200
    res = admin.browser.patch(f"/admin/users/{other.id}", {"disabled": False})
    assert res.status_code == 200 and res.get_json()["user"]["disabled"] is False
    assert_error(other.browser.get("/me"), 401, "not_authenticated")
    fresh = log_in(app, other.account)
    assert fresh.get("/me").status_code == 200


def test_disabling_twice_keeps_the_first_date(app, database):
    admin, other = admin_of(app, database), creator(app)
    admin.browser.patch(f"/admin/users/{other.id}", {"disabled": True})
    database.run("UPDATE users SET disabled_at = now() - interval '3 days' WHERE id = :u", u=other.id)
    first = database.value("SELECT disabled_at FROM users WHERE id = :u", u=other.id)
    assert admin.browser.patch(f"/admin/users/{other.id}", {"disabled": True}).status_code == 200
    assert database.value("SELECT disabled_at FROM users WHERE id = :u", u=other.id) == first


# ---------------------------------------------------------------- I4 adjust credits


def test_adjusting_credits_up_and_down_keeps_the_ledger_and_the_balance_together(app, database):
    admin, other = admin_of(app, database), creator(app)
    up = admin.browser.post(f"/admin/users/{other.id}/credits", {"amount": 25, "note": "  Thanks  "})
    assert up.status_code == 201 and up.get_json() == {"balance": 125}
    down = admin.browser.post(f"/admin/users/{other.id}/credits", {"amount": -125, "note": "Clearing"})
    assert down.status_code == 201 and down.get_json() == {"balance": 0}, "exactly zero is allowed"
    assert [(r["amount"], r["kind"], r["note"]) for r in ledger_rows(database, other.id, "adjust")] == [
        (25, "adjust", "Thanks"),
        (-125, "adjust", "Clearing"),
    ]
    created_by = database.rows(
        "SELECT created_by::text FROM credit_ledger WHERE user_id = :u AND kind = 'adjust'", u=other.id
    )
    assert {r["created_by"] for r in created_by} == {admin.id}
    assert_ledger_matches_balance(database, other.id)
    assert balance(other.browser) == 0
    # The creator sees it in their own history (H2).
    history = other.browser.get("/credits/ledger").get_json()["items"]
    assert [(i["amount"], i["kind"], i["note"]) for i in history[:2]] == [
        (-125, "adjust", "Clearing"),
        (25, "adjust", "Thanks"),
    ]


def test_an_adjustment_below_zero_is_refused_and_changes_nothing(app, database):
    admin, other = admin_of(app, database), creator(app)
    error = assert_error(
        admin.browser.post(f"/admin/users/{other.id}/credits", {"amount": -101, "note": "Too much"}),
        409,
        "balance_negative",
    )
    assert "100" in error["message"]
    assert stored_balance(database, other.id) == 100
    assert ledger_rows(database, other.id, "adjust") == []


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"amount": 0, "note": "Nothing"}, "amount"),
        ({"amount": 5}, "note"),
        ({"amount": 5, "note": ""}, "note"),
        ({"amount": 5, "note": "   "}, "note"),
        ({"amount": 5, "note": "x" * 201}, "note"),
        ({"amount": 5, "note": "two\nlines"}, "note"),
        ({"note": "No amount"}, "amount"),
        ({"amount": "5", "note": "Text amount"}, "amount"),
        ({"amount": 1.5, "note": "Half"}, "amount"),
        ({"amount": 100_001, "note": "Too big"}, "amount"),
        ({"amount": -100_001, "note": "Too small"}, "amount"),
        ({"amount": True, "note": "A boolean"}, "amount"),
    ],
)
def test_adjust_credits_validates_its_body(app, database, body, field):
    admin, other = admin_of(app, database), creator(app)
    assert_invalid(admin.browser.post(f"/admin/users/{other.id}/credits", body), field)
    assert stored_balance(database, other.id) == 100
    assert ledger_rows(database, other.id, "adjust") == []


def test_a_200_character_note_is_accepted(app, database):
    admin, other = admin_of(app, database), creator(app)
    res = admin.browser.post(f"/admin/users/{other.id}/credits", {"amount": 1, "note": "n" * 200})
    assert res.status_code == 201


# ---------------------------------------------------------------- I5 jobs list


def jobs_found(admin, **query) -> list[dict]:
    res = admin.browser.get("/admin/jobs", query_string=query)
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()["items"]


def test_jobs_list_filters_by_state_type_and_provider(app, database):
    admin, other = admin_of(app, database), creator(app)
    scripted_project(app, other.browser)  # script, succeeded, provider mock
    failed = failed_ideas_job(app, other)  # ideas, failed
    queued = other.browser.post("/ideas", {"niche": "gardening"}).get_json()["job"]  # ideas, queued

    everything = jobs_found(admin)
    assert len(everything) == 3
    assert set(everything[0]) == JOB_FIELDS | {"userEmail", "attempts"}
    assert {j["userEmail"] for j in everything} == {other.account.email}
    assert [j["id"] for j in jobs_found(admin, state="failed")] == [failed["id"]]
    assert [j["id"] for j in jobs_found(admin, state="queued")] == [queued["id"]]
    assert {j["type"] for j in jobs_found(admin, type="ideas")} == {"ideas"}
    assert len(jobs_found(admin, type="ideas")) == 2
    assert [j["type"] for j in jobs_found(admin, type="script")] == ["script"]
    by_provider = jobs_found(admin, provider="mock")
    assert "script" in {j["type"] for j in by_provider} and {j["provider"] for j in by_provider} == {"mock"}
    assert [j["type"] for j in jobs_found(admin, provider="mock", state="succeeded")] == ["script"]
    assert jobs_found(admin, state="failed", type="script") == []
    assert [j["id"] for j in jobs_found(admin, state="failed", type="ideas")] == [failed["id"]]
    listed = jobs_found(admin, state="failed")[0]
    assert listed["error"]["code"] and listed["error"]["message"] and listed["attempts"] == 1
    assert listed["creditsRefunded"] is (listed["creditsReserved"] > 0)


def test_jobs_list_answers_unknown_filter_values_with_nothing_or_400(app, database):
    admin = admin_of(app, database)
    scripted_project(app, admin.browser)
    for query in ({"state": "exploded"}, {"type": "teleport"}, {"provider": "nobody"}):
        res = admin.browser.get("/admin/jobs", query_string=query)
        assert res.status_code in (200, 400), res.get_data(as_text=True)
        if res.status_code == 200:
            assert res.get_json()["items"] == []


def test_jobs_list_is_newest_first_and_pages_with_cursors(app, database):
    admin, other = admin_of(app, database), creator(app)
    ids = []
    for n in range(5):
        job_id = str(uuid.uuid4())
        ids.append(job_id)
        database.run(
            "INSERT INTO jobs (id, user_id, type, state, queued_at) "
            "VALUES (:id, :u, 'ideas', 'failed', now() - make_interval(mins => :n))",
            id=job_id,
            u=other.id,
            n=n // 2,  # some share a moment: the id breaks the tie
        )
    seen, cursor = [], None
    for _ in range(10):
        query = {"limit": 2, "state": "failed", **({"cursor": cursor} if cursor else {})}
        body = admin.browser.get("/admin/jobs", query_string=query).get_json()
        seen += [j["id"] for j in body["items"]]
        cursor = body["nextCursor"]
        if cursor is None:
            break
    assert sorted(seen) == sorted(ids) and len(seen) == 5, "a job was repeated or skipped between pages"
    moments = [j["queuedAt"] for j in jobs_found(admin, state="failed")]
    assert moments == sorted(moments, reverse=True)
    assert_invalid(admin.browser.get("/admin/jobs?limit=500"), "limit")


# ---------------------------------------------------------------- I6 retry


def test_retrying_a_failed_voice_job_starts_a_free_new_job_that_is_dispatched(app, database):
    admin, other = admin_of(app, database), creator(app)
    project, scene_id, old = failed_voice_job(app, database, other)
    assert scene_of(other.browser, project["id"], scene_id)["voiceStatus"] == "failed"
    before = balance(other.browser)
    ledger_before = len(ledger_rows(database, other.id))

    res = retry(admin, old["id"])
    assert res.status_code == 202, res.get_data(as_text=True)
    new = res.get_json()["job"]
    assert set(new) == JOB_FIELDS
    assert new["id"] != old["id"] and new["type"] == "voice" and new["state"] == "queued"
    assert new["sceneId"] == scene_id and new["projectId"] == project["id"]
    assert new["creditsReserved"] == 0 and new["creditsRefunded"] is False
    assert waiting_jobs() == [new["id"]], "the new job was not handed to the worker"
    assert scene_of(other.browser, project["id"], scene_id)["voiceStatus"] == "queued"
    row = database.one("SELECT user_id::text, input FROM jobs WHERE id = :j", j=new["id"])
    old_input = database.value("SELECT input FROM jobs WHERE id = :j", j=old["id"])
    assert row["user_id"] == other.id
    assert row["input"] == {**old_input, "previousStatus": "failed"}
    after = get_job(other.browser, old["id"])
    assert after["state"] == "failed" and after["error"] == old["error"], "the old job stays failed"
    assert after["output"]["retriedAs"] == new["id"]

    run_jobs(app)
    assert get_job(other.browser, new["id"])["state"] == "succeeded"
    scene = scene_of(other.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == "ready" and scene["voice"] is not None
    assert balance(other.browser) == before and len(ledger_rows(database, other.id)) == ledger_before
    assert_ledger_matches_balance(database, other.id)


def test_a_failed_ideas_job_is_not_retried_from_the_admin_page(app, database):
    """QB8-005: only a scene's voice or visual; the creator asks for ideas again with one click."""
    admin, other = admin_of(app, database), creator(app)
    old = failed_ideas_job(app, other)
    assert_error(retry(admin, old["id"]), 409, "not_retryable")
    assert waiting_jobs() == []
    assert database.value("SELECT count(*) FROM jobs WHERE user_id = :u", u=other.id) == 1


@pytest.mark.parametrize("state", ["queued", "running", "succeeded", "cancelled"])
def test_only_failed_jobs_can_be_retried(app, database, state):
    admin, other = admin_of(app, database), creator(app)
    job_id = str(uuid.uuid4())
    database.run(
        "INSERT INTO jobs (id, user_id, type, state) VALUES (:id, :u, 'ideas', :s)",
        id=job_id,
        u=other.id,
        s=state,
    )
    assert_error(retry(admin, job_id), 409, "not_failed")
    assert database.value("SELECT count(*) FROM jobs WHERE user_id = :u", u=other.id) == 1
    assert waiting_jobs() == []


@pytest.mark.parametrize(
    "job_type",
    ["script", "rewrite_scene", "render", "publish", "transcribe", "music", "ai_video", "keyframe",
     "ideas", "hooks", "caption"],
)  # fmt: skip
def test_other_job_types_are_retried_by_the_creator(app, database, job_type):
    admin, other = admin_of(app, database), creator(app)
    project = scripted_project(app, other.browser)
    job_id = str(uuid.uuid4())
    database.run(
        "INSERT INTO jobs (id, user_id, project_id, type, state, error_code, error_message) "
        "VALUES (:id, :u, :p, :t, 'failed', 'provider_failed', 'It failed')",
        id=job_id,
        u=other.id,
        p=project["id"],
        t=job_type,
    )
    assert_error(retry(admin, job_id), 409, "not_retryable")
    assert database.value("SELECT count(*) FROM jobs WHERE type = :t AND state = 'queued'", t=job_type) == 0
    assert waiting_jobs() == []


@pytest.mark.parametrize("job_id", [NOBODY, "not-a-uuid"])
def test_retrying_an_unknown_job_is_404(app, database, job_id):
    admin = admin_of(app, database)
    assert_error(retry(admin, job_id), 404, "not_found")


def test_a_job_of_a_deleted_reel_is_not_retried(app, database):
    admin, other = admin_of(app, database), creator(app)
    project, scene_id, old = failed_voice_job(app, database, other)
    assert other.browser.delete(f"/projects/{project['id']}").status_code in (200, 204)
    assert_error(retry(admin, old["id"]), 409, "not_retryable")
    assert waiting_jobs() == []
    assert database.value("SELECT count(*) FROM jobs WHERE state = 'queued'") == 0


def test_a_job_of_a_deleted_scene_is_not_retried(app, database):
    admin, other = admin_of(app, database), creator(app)
    project, scene_id, old = failed_voice_job(app, database, other)
    assert other.browser.delete(f"/scenes/{scene_id}").status_code in (200, 204)
    assert_error(retry(admin, old["id"]), 409, "not_retryable")
    assert waiting_jobs() == []
    assert database.value("SELECT count(*) FROM jobs WHERE state = 'queued'") == 0


def test_a_job_is_retried_once_and_the_new_job_can_be_retried_if_it_fails_too(app, database):
    admin, other = admin_of(app, database), creator(app)
    project, scene_id, old = failed_voice_job(app, database, other)
    first = retry(admin, old["id"])
    assert first.status_code == 202
    assert_error(retry(admin, old["id"]), 409, "not_retryable")
    assert len(waiting_jobs()) == 1
    # The retry fails as well: the scene is failed again, and it is the new job that is retried.
    database.run("INSERT INTO provider_switches (provider, enabled, reason) VALUES ('mock', false, 'test')")
    run_jobs(app)
    database.run("DELETE FROM provider_switches WHERE provider = 'mock'")
    new_id = first.get_json()["job"]["id"]
    assert get_job(other.browser, new_id)["state"] == "failed"
    assert scene_of(other.browser, project["id"], scene_id)["voiceStatus"] == "failed"
    assert_error(retry(admin, old["id"]), 409, "not_retryable")
    assert retry(admin, new_id).status_code == 202
    run_jobs(app)
    assert scene_of(other.browser, project["id"], scene_id)["voiceStatus"] == "ready"
    assert_ledger_matches_balance(database, other.id)


@pytest.mark.parametrize("status", ["missing", "outdated", "ready", "queued", "running"])
def test_a_retry_needs_the_scenes_voice_to_still_be_failed(app, database, status):
    admin, other = admin_of(app, database), creator(app)
    _, scene_id, old = failed_voice_job(app, database, other)
    database.run("UPDATE scenes SET voice_status = :s WHERE id = :id", s=status, id=scene_id)
    assert_error(retry(admin, old["id"]), 409, "not_retryable")
    assert waiting_jobs() == []
    assert database.value("SELECT voice_status FROM scenes WHERE id = :id", id=scene_id) == status
    assert not (database.value("SELECT output FROM jobs WHERE id = :j", j=old["id"]) or {}).get("retriedAs")


def test_a_voice_whose_narration_was_edited_since_is_not_retried(app, database):
    admin, other = admin_of(app, database), creator(app)
    project, scene_id, old = failed_voice_job(app, database, other)
    res = other.browser.patch(f"/scenes/{scene_id}", {"narration": "A different line now."})
    assert res.status_code == 200
    assert_error(retry(admin, old["id"]), 409, "not_retryable")
    # Even if the scene still shows as failed, the old words are not spoken.
    database.run("UPDATE scenes SET voice_status = 'failed' WHERE id = :id", id=scene_id)
    assert_error(retry(admin, old["id"]), 409, "not_retryable")
    assert waiting_jobs() == []


def test_a_voice_whose_caption_words_were_fixed_by_hand_is_not_retried(app, database):
    admin, other = admin_of(app, database), creator(app)
    _, scene_id, old = failed_voice_job(app, database, other)
    database.run("UPDATE scenes SET words_edited = true WHERE id = :id", id=scene_id)
    assert_error(retry(admin, old["id"]), 409, "not_retryable")
    assert waiting_jobs() == []


@pytest.mark.parametrize("status", ["scripting", "rendering", "posting"])
def test_a_job_of_a_busy_reel_is_not_retried(app, database, status):
    admin, other = admin_of(app, database), creator(app)
    project, scene_id, old = failed_voice_job(app, database, other)
    database.run("UPDATE projects SET status = :s WHERE id = :p", s=status, p=project["id"])
    assert_error(retry(admin, old["id"]), 409, "not_retryable")
    assert waiting_jobs() == []
    assert database.value("SELECT voice_status FROM scenes WHERE id = :id", id=scene_id) == "failed"


def test_a_job_of_a_disabled_account_is_not_retried_until_it_is_enabled_again(app, database):
    admin, other = admin_of(app, database), creator(app)
    _, _, old = failed_voice_job(app, database, other)
    assert admin.browser.patch(f"/admin/users/{other.id}", {"disabled": True}).status_code == 200
    assert_error(retry(admin, old["id"]), 409, "not_retryable")
    assert waiting_jobs() == []
    assert admin.browser.patch(f"/admin/users/{other.id}", {"disabled": False}).status_code == 200
    assert retry(admin, old["id"]).status_code == 202


def test_retrying_a_failed_visual_job_sets_the_scene_to_queued(app, database):
    admin, other = admin_of(app, database), creator(app)
    project = scripted_project(app, other.browser)
    scene_id = project["scenes"][0]["id"]
    res = other.browser.post(f"/scenes/{scene_id}/visual/regenerate", {"visualSource": "ai_image"})
    assert res.status_code == 202, res.get_data(as_text=True)
    old = res.get_json()["job"]
    database.run("INSERT INTO provider_switches (provider, enabled, reason) VALUES ('mock', false, 'test')")
    run_jobs(app)
    database.run("DELETE FROM provider_switches WHERE provider = 'mock'")
    assert get_job(other.browser, old["id"])["state"] == "failed"
    before = balance(other.browser)

    res = retry(admin, old["id"])
    assert res.status_code == 202, res.get_data(as_text=True)
    new = res.get_json()["job"]
    assert new["type"] == "visual" and new["creditsReserved"] == 0
    assert scene_of(other.browser, project["id"], scene_id)["visualStatus"] == "queued"
    run_jobs(app)
    assert get_job(other.browser, new["id"])["state"] == "succeeded"
    assert scene_of(other.browser, project["id"], scene_id)["visualStatus"] == "ready"
    assert balance(other.browser) == before


def test_retrying_an_old_failure_does_not_spoil_a_voice_the_creator_has_since_remade(app, database):
    """The failed job is from before an edit. Since then the creator changed the narration and
    made the voice again, so the scene is fine. The admin's retry of the old job must leave it so."""
    admin, other = admin_of(app, database), creator(app)
    project, scene_id, old = failed_voice_job(app, database, other)
    assert other.browser.patch(f"/scenes/{scene_id}", {"narration": "A brand new line."}).status_code == 200
    started(other.browser, scene_id)
    run_jobs(app)
    scene = scene_of(other.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == "ready"
    voice_before = scene["voice"]["assetId"]

    res = retry(admin, old["id"])
    if res.status_code == 202:
        run_jobs(app)
    scene = scene_of(other.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == "ready", f"the remade voice now shows as {scene['voiceStatus']}"
    assert scene["voice"]["assetId"] == voice_before, "the voice was replaced by one of the old narration"


def test_a_job_of_a_deleted_account_is_not_retried(app, database):
    admin, other = admin_of(app, database), creator(app)
    _, scene_id, old = failed_voice_job(app, database, other)
    assert other.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    assert_error(retry(admin, old["id"]), 409, "not_retryable")
    assert waiting_jobs() == []
    assert database.value("SELECT count(*) FROM jobs WHERE state = 'queued'") == 0


# ---------------------------------------------------------------- I7 usage


def add_usage(database, day: date, provider: str, job_type: str, calls=1, failures=0, credits=0) -> None:
    database.run(
        "INSERT INTO usage_daily (day, provider, job_type, calls, failures, credits_charged, "
        "cost_estimate_usd) VALUES (:d, :p, :t, :c, :f, :cr, 0.125)",
        d=day,
        p=provider,
        t=job_type,
        c=calls,
        f=failures,
        cr=credits,
    )


def db_today(database) -> date:
    return database.value("SELECT current_date")


def test_usage_defaults_to_the_last_30_days_newest_first(app, database):
    admin = admin_of(app, database)
    today = db_today(database)
    add_usage(database, today, "gemini", "script", calls=7, failures=2, credits=5)
    add_usage(database, today, "elevenlabs", "voice", calls=3)
    add_usage(database, today - timedelta(days=29), "gemini", "ideas")
    add_usage(database, today - timedelta(days=30), "gemini", "hooks")
    body = admin.browser.get("/admin/usage").get_json()
    assert set(body) == {"items", "from", "to"}
    assert (body["from"], body["to"]) == ((today - timedelta(days=29)).isoformat(), today.isoformat())
    assert [(i["day"], i["provider"], i["jobType"]) for i in body["items"]] == [
        (today.isoformat(), "elevenlabs", "voice"),
        (today.isoformat(), "gemini", "script"),
        ((today - timedelta(days=29)).isoformat(), "gemini", "ideas"),
    ]
    assert body["items"][1] == {
        "day": today.isoformat(), "provider": "gemini", "jobType": "script", "calls": 7,
        "failures": 2, "creditsCharged": 5, "costEstimateUsd": 0.125,
    }  # fmt: skip


def test_usage_takes_a_date_range_with_both_ends_included(app, database):
    admin = admin_of(app, database)
    for day in ("2026-03-01", "2026-03-02", "2026-03-03", "2026-03-04"):
        add_usage(database, date.fromisoformat(day), "groq", "script")
    body = admin.browser.get("/admin/usage?from=2026-03-02&to=2026-03-03").get_json()
    assert [i["day"] for i in body["items"]] == ["2026-03-03", "2026-03-02"]
    assert (body["from"], body["to"]) == ("2026-03-02", "2026-03-03")
    one_day = admin.browser.get("/admin/usage?from=2026-03-04&to=2026-03-04").get_json()
    assert [i["day"] for i in one_day["items"]] == ["2026-03-04"]
    # Only `to`: the 30 days up to it.
    only_to = admin.browser.get("/admin/usage?to=2026-03-02").get_json()
    assert (only_to["from"], only_to["to"]) == ("2026-02-01", "2026-03-02")
    # A year at most.
    assert admin.browser.get("/admin/usage?from=2025-03-01&to=2026-03-01").status_code == 200


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ("from=yesterday", "from"),
        ("from=2026-13-01", "from"),
        ("from=2026-03-05&to=2026-03-01", "from"),
        ("from=2024-01-01&to=2026-03-01", "from"),
        ("to=03/01/2026", "to"),
        ("to=2026-02-30", "to"),
    ],
)
def test_usage_refuses_a_bad_date_range(app, database, query, field):
    admin = admin_of(app, database)
    assert_invalid(admin.browser.get(f"/admin/usage?{query}"), field)


def test_a_finished_job_is_counted_in_usage_and_in_the_providers_list(app, database):
    admin = admin_of(app, database)
    scripted_project(app, admin.browser)
    rows = admin.browser.get("/admin/usage").get_json()["items"]
    assert [(r["provider"], r["jobType"], r["calls"], r["failures"]) for r in rows] == [
        ("mock", "script", 1, 0)
    ]
    assert rows[0]["creditsCharged"] == 1


# ---------------------------------------------------------------- I8, I9 providers


def test_providers_list_has_every_provider_with_todays_calls_and_error_rate(app, database):
    admin = admin_of(app, database)
    today = db_today(database)
    add_usage(database, today, "elevenlabs", "voice", calls=6, failures=1)
    add_usage(database, today, "elevenlabs", "transcribe", calls=2, failures=1)
    add_usage(database, today - timedelta(days=1), "gemini", "script", calls=9, failures=9)
    items = admin.browser.get("/admin/providers").get_json()["items"]
    assert [p["name"] for p in items] == PROVIDER_NAMES
    assert all(set(p) == PROVIDER_FIELDS and p["purpose"] for p in items)
    by_name = {p["name"]: p for p in items}
    assert by_name["elevenlabs"]["callsToday"] == 8 and by_name["elevenlabs"]["errorRateToday"] == 0.25
    # Yesterday's failures are not today's.
    assert by_name["gemini"]["callsToday"] == 0 and by_name["gemini"]["errorRateToday"] is None
    assert all(p["enabled"] is True and p["reason"] == "" for p in items)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"enabled": False}, "reason"),
        ({"enabled": False, "reason": ""}, "reason"),
        ({"enabled": False, "reason": "   "}, "reason"),
        ({"enabled": False, "reason": "r" * 201}, "reason"),
        ({"reason": "No switch"}, "enabled"),
        ({"enabled": "off", "reason": "Text"}, "enabled"),
        ({"enabled": 0, "reason": "A number"}, "enabled"),
        ({}, "enabled"),
    ],
)
def test_switching_a_provider_validates_its_body(app, database, body, field):
    admin = admin_of(app, database)
    assert_invalid(admin.browser.patch("/admin/providers/pixabay", body), field)
    assert database.value("SELECT count(*) FROM provider_switches") == 0


def test_switching_a_provider_off_and_on_records_who_and_why(app, database):
    admin = admin_of(app, database)
    res = admin.browser.patch(
        "/admin/providers/cloudflare", {"enabled": False, "reason": " Free quota used up "}
    )
    assert res.status_code == 200
    provider = res.get_json()["provider"]
    assert set(provider) == PROVIDER_FIELDS
    assert (provider["name"], provider["enabled"], provider["reason"]) == (
        "cloudflare", False, "Free quota used up",
    )  # fmt: skip
    row = database.one("SELECT enabled, reason, updated_by::text FROM provider_switches")
    assert row == {"enabled": False, "reason": "Free quota used up", "updated_by": admin.id}
    # Off twice is fine and keeps one row.
    assert admin.browser.patch(
        "/admin/providers/cloudflare", {"enabled": False, "reason": "Still off"}
    ).get_json()["provider"]["reason"] == "Still off"  # fmt: skip
    assert database.value("SELECT count(*) FROM provider_switches") == 1
    on = admin.browser.patch("/admin/providers/cloudflare", {"enabled": True})
    assert on.status_code == 200 and on.get_json()["provider"]["enabled"] is True
    assert on.get_json()["provider"]["reason"] == "", "the reason for being off outlived the switch"
    others = [
        p for p in admin.browser.get("/admin/providers").get_json()["items"] if p["name"] != "cloudflare"
    ]
    assert all(p["enabled"] for p in others)


@pytest.mark.parametrize("name", ["nope", "mock", "GEMINI", "openai"])
def test_switching_an_unknown_provider_is_404(app, database, name):
    admin = admin_of(app, database)
    assert_error(admin.browser.patch(f"/admin/providers/{name}", {"enabled": True}), 404, "not_found")
    assert database.value("SELECT count(*) FROM provider_switches") == 0


def test_a_provider_switched_off_by_the_admin_is_skipped_by_voice_jobs(
    eleven_app, database, outbox, http, edge
):
    admin = admin_of(eleven_app, database)
    res = admin.browser.patch("/admin/providers/elevenlabs", {"enabled": False, "reason": "Quota used up"})
    assert res.status_code == 200
    narration = "Straight to the backup."
    edge.words = edge_words(narration)
    me = creator(eleven_app)
    _, scene_id = voice_scene(eleven_app, me, narration)
    job = started(me.browser, scene_id)
    run_jobs(eleven_app)
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["provider"] == "edge"
    assert http.calls == [], "the switched-off provider was still called"


def test_a_job_every_provider_failed_is_counted_against_each_of_them(
    eleven_app,
    database,
    outbox,
    http,
    edge,
):
    """QB8-004: the error rate means something, and failed jobs can be found by provider."""
    http.tts = [response(503, text="down")]
    edge.error = OSError("no network")
    admin, me = admin_of(eleven_app, database), creator(eleven_app)
    _, scene_id = voice_scene(eleven_app, me, "Nobody answers today.")
    job = started(me.browser, scene_id)
    run_jobs(eleven_app)
    failed = get_job(me.browser, job["id"])
    assert failed["state"] == "failed" and failed["provider"] == "edge", "the last provider tried"

    usage = admin.browser.get("/admin/usage").get_json()["items"]
    voice = {
        r["provider"]: (r["calls"], r["failures"], r["creditsCharged"])
        for r in usage
        if r["jobType"] == "voice"
    }
    assert voice == {"elevenlabs": (1, 1, 0), "edge": (1, 1, 0)}
    providers = {p["name"]: p for p in admin.browser.get("/admin/providers").get_json()["items"]}
    for name in ("elevenlabs", "edge"):
        assert (providers[name]["callsToday"], providers[name]["errorRateToday"]) == (1, 1.0)
    assert providers["gemini"]["errorRateToday"] is None
    found = admin.browser.get("/admin/jobs?state=failed&provider=edge").get_json()["items"]
    assert [j["id"] for j in found] == [job["id"]]
    assert admin.browser.get("/admin/jobs?state=failed&provider=pixabay").get_json()["items"] == []


def test_with_every_voice_provider_switched_off_a_voice_is_refused_free_of_charge(
    eleven_app,
    database,
    outbox,
    http,
    edge,
):
    admin = admin_of(eleven_app, database)
    for name in ("elevenlabs", "edge"):
        admin.browser.patch(f"/admin/providers/{name}", {"enabled": False, "reason": "Both down"})
    me = creator(eleven_app)
    _, scene_id = voice_scene(eleven_app, me, "Nobody is home.")
    before = balance(me.browser)
    assert_error(regenerate(me.browser, scene_id), 503, "provider_unavailable")
    assert balance(me.browser) == before and http.calls == [] and edge.calls == []
    # Switched on again, work is accepted again.
    assert admin.browser.patch("/admin/providers/edge", {"enabled": True}).status_code == 200
    assert regenerate(me.browser, scene_id).status_code == 202


def test_youtube_switched_off_pauses_publishing_until_it_is_switched_on_again(
    app,
    database,
    fresh_youtube,
):
    """I9 / AD2 / QB8-003: with `youtube` off, F5 answers 503 provider_unavailable."""
    from reelforge_api.providers.publish import MockYouTube

    admin, me = admin_of(app, database), creator(app)
    project, render_id = rendered_project(app, me.browser)
    connect(me.browser)
    before = balance(me.browser)
    res = admin.browser.patch("/admin/providers/youtube", {"enabled": False, "reason": "API quota used up"})
    assert res.status_code == 200 and res.get_json()["provider"]["enabled"] is False

    assert_error(publish(me.browser, render_id), 503, "provider_unavailable")
    assert run_jobs(app) == 0 and MockYouTube.uploads == []
    assert database.value("SELECT count(*) FROM posts") == 0
    assert database.value("SELECT count(*) FROM jobs WHERE type = 'publish'") == 0
    assert get_project(me.browser, project["id"])["status"] == "rendered"
    assert balance(me.browser) == before

    assert admin.browser.patch("/admin/providers/youtube", {"enabled": True}).status_code == 200
    assert publish(me.browser, render_id).status_code == 202
    run_jobs(app)
    assert len(MockYouTube.uploads) == 1
    assert get_project(me.browser, project["id"])["status"] == "posted"


def test_a_publish_already_waiting_when_youtube_is_switched_off_does_not_upload(
    app,
    database,
    fresh_youtube,
):
    """The switch is for a used-up quota: work already queued must not spend it either."""
    from reelforge_api.providers.publish import MockYouTube

    admin, me = admin_of(app, database), creator(app)
    _, render_id = rendered_project(app, me.browser)
    connect(me.browser)
    assert publish(me.browser, render_id).status_code == 202
    admin.browser.patch("/admin/providers/youtube", {"enabled": False, "reason": "API quota used up"})
    run_jobs(app)
    assert MockYouTube.uploads == [], "the video was uploaded although youtube is switched off"


# ---------------------------------------------------------------- I10 to I13 music


def add_track(admin, **changes) -> dict:
    res = admin.browser.post("/admin/music", {**TRACK, **changes})
    assert res.status_code == 201, res.get_data(as_text=True)
    return res.get_json()


def track_key(database, track_id: str) -> str:
    return database.value("SELECT s3_key FROM music_tracks WHERE id = :id", id=track_id)


def put_to(url: str, data: bytes, content_type: str) -> int:
    request = urllib.request.Request(url, data=data, method="PUT", headers={"Content-Type": content_type})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=10) as res:  # noqa: S310 - a link we just made
            return res.status
    except urllib.error.HTTPError as e:
        return e.code


def test_a_new_track_is_inactive_and_its_upload_link_takes_the_file(app, database):
    admin = admin_of(app, database)
    added = add_track(admin, moods=[" Calm ", "JOYFUL", "calm"], sourceUrl="https://example.com/night")
    track = added["track"]
    assert set(added) == {"track", "uploadUrl"} and set(track) == TRACK_FIELDS
    assert track["isActive"] is False and track["title"] == "Night Drive"
    assert track["moods"] == ["calm", "joyful"] and track["durationSeconds"] == 60
    assert track["license"] == "CC BY 4.0: test" and track["sourceUrl"] == "https://example.com/night"
    key = track_key(database, track["id"])
    assert key == f"library/music/{track['id']}.mp3"
    # Not offered to creators yet.
    assert admin.browser.get("/music").get_json()["items"] == []

    # The link takes the declared content type only.
    assert put_to(added["uploadUrl"], b"<html>", "text/html") == 403
    assert not file_exists(app, key)
    assert put_to(added["uploadUrl"], b"ID3 the song", "audio/mpeg") == 200
    assert file_exists(app, key)

    res = admin.browser.patch(f"/admin/music/{track['id']}", {"isActive": True})
    assert res.status_code == 200 and res.get_json()["track"]["isActive"] is True
    offered = admin.browser.get("/music").get_json()["items"]
    assert [t["id"] for t in offered] == [track["id"]]
    assert download(offered[0]["previewUrl"]) == b"ID3 the song"


@pytest.mark.parametrize(
    ("content_type", "extension"),
    [
        ("audio/mpeg", "mp3"),
        ("audio/mp4", "m4a"),
        ("audio/ogg", "ogg"),
        ("audio/wav", "wav"),
        ("audio/x-wav", "wav"),
    ],
)
def test_the_stored_file_name_follows_the_content_type(app, database, content_type, extension):
    admin = admin_of(app, database)
    track = add_track(admin, contentType=content_type)["track"]
    assert track_key(database, track["id"]).endswith(f"{track['id']}.{extension}")


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"title": ""}, "title"),
        ({"title": "   "}, "title"),
        ({"title": "t" * 121}, "title"),
        ({"moods": []}, "moods"),
        ({"moods": ["cheerful"]}, "moods"),
        ({"moods": ["calm", "nope"]}, "moods"),
        ({"moods": "calm"}, "moods"),
        ({"license": ""}, "license"),
        ({"license": "l" * 301}, "license"),
        ({"sourceUrl": "s" * 301}, "sourceUrl"),
        ({"filename": ""}, "filename"),
        ({"contentType": "video/mp4"}, "contentType"),
        ({"contentType": "application/x-msdownload"}, "contentType"),
        ({"sizeBytes": 0}, "sizeBytes"),
        ({"sizeBytes": 30 * 1024 * 1024 + 1}, "sizeBytes"),
        ({"sizeBytes": "1000"}, "sizeBytes"),
        ({"durationSeconds": 0}, "durationSeconds"),
        ({"durationSeconds": -3}, "durationSeconds"),
        ({"durationSeconds": 3601}, "durationSeconds"),
        ({"isActive": True}, "isActive"),
    ],
)
def test_add_music_validates_its_body(app, database, changes, field):
    admin = admin_of(app, database)
    assert_invalid(admin.browser.post("/admin/music", {**TRACK, **changes}), field)
    assert database.value("SELECT count(*) FROM music_tracks") == 0


@pytest.mark.parametrize(
    "missing", ["title", "moods", "license", "filename", "contentType", "sizeBytes", "durationSeconds"]
)
def test_add_music_needs_every_required_field(app, database, missing):
    admin = admin_of(app, database)
    body = {k: v for k, v in TRACK.items() if k != missing}
    assert_invalid(admin.browser.post("/admin/music", body), missing)
    assert database.value("SELECT count(*) FROM music_tracks") == 0


def test_the_largest_allowed_file_and_length_are_accepted(app, database):
    admin = admin_of(app, database)
    add_track(admin, sizeBytes=30 * 1024 * 1024, durationSeconds=3600)


def test_music_list_includes_inactive_tracks_in_title_order(app, database):
    admin = admin_of(app, database)
    b = add_track(admin, title="Bravo")["track"]
    a = add_track(admin, title="Alpha")["track"]
    put_file(app, track_key(database, a["id"]))
    assert admin.browser.patch(f"/admin/music/{a['id']}", {"isActive": True}).status_code == 200
    items = admin.browser.get("/admin/music").get_json()["items"]
    assert [(t["id"], t["isActive"]) for t in items] == [(a["id"], True), (b["id"], False)]
    assert all(set(t) == TRACK_FIELDS for t in items)
    assert [t["id"] for t in admin.browser.get("/music").get_json()["items"]] == [a["id"]]


def test_a_track_cannot_be_switched_on_before_its_file_is_uploaded(app, database):
    admin = admin_of(app, database)
    track = add_track(admin)["track"]
    assert_error(admin.browser.patch(f"/admin/music/{track['id']}", {"isActive": True}), 409, "file_missing")
    assert database.value("SELECT is_active FROM music_tracks WHERE id = :id", id=track["id"]) is False
    # Other fields can be edited meanwhile, and switching off never needs the file.
    assert admin.browser.patch(f"/admin/music/{track['id']}", {"title": "Renamed"}).status_code == 200
    assert admin.browser.patch(f"/admin/music/{track['id']}", {"isActive": False}).status_code == 200
    # Refused together with other edits: nothing of that request is kept.
    assert_error(
        admin.browser.patch(f"/admin/music/{track['id']}", {"title": "Half saved", "isActive": True}),
        409,
        "file_missing",
    )
    assert database.value("SELECT title FROM music_tracks WHERE id = :id", id=track["id"]) == "Renamed"


def test_a_track_with_an_empty_or_oversized_file_cannot_be_switched_on(app, database, monkeypatch):
    """QB8-009: what was uploaded is checked when the track is switched on."""
    admin = admin_of(app, database)
    track = add_track(admin)["track"]
    key = track_key(database, track["id"])
    switch_on = {"isActive": True}
    put_file(app, key, b"")
    assert_error(admin.browser.patch(f"/admin/music/{track['id']}", switch_on), 409, "file_too_large")
    put_file(app, key, b"ID3 a song")
    limit = 30 * 1024 * 1024
    monkeypatch.setattr(Storage, "size", lambda self, key: limit + 1)
    assert_error(admin.browser.patch(f"/admin/music/{track['id']}", switch_on), 409, "file_too_large")
    assert database.value("SELECT is_active FROM music_tracks WHERE id = :id", id=track["id"]) is False
    assert admin.browser.get("/music").get_json()["items"] == []
    monkeypatch.setattr(Storage, "size", lambda self, key: limit)
    assert admin.browser.patch(f"/admin/music/{track['id']}", switch_on).status_code == 200


def test_update_music_edits_title_moods_licence_and_active(app, database):
    admin = admin_of(app, database)
    track = add_track(admin)["track"]
    put_file(app, track_key(database, track["id"]))
    res = admin.browser.patch(
        f"/admin/music/{track['id']}",
        {"title": " Dawn ", "moods": ["Sad", "tense"], "license": "CC0", "isActive": True},
    )
    assert res.status_code == 200
    updated = res.get_json()["track"]
    assert (updated["title"], updated["moods"], updated["license"], updated["isActive"]) == (
        "Dawn", ["sad", "tense"], "CC0", True,
    )  # fmt: skip
    off = admin.browser.patch(f"/admin/music/{track['id']}", {"isActive": False})
    assert off.get_json()["track"]["isActive"] is False
    assert admin.browser.get("/music").get_json()["items"] == []


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"title": ""}, "title"),
        ({"title": "t" * 121}, "title"),
        ({"moods": []}, "moods"),
        ({"moods": ["loud"]}, "moods"),
        ({"license": "  "}, "license"),
        ({"isActive": "yes"}, "isActive"),
        ({"durationSeconds": 10}, "durationSeconds"),
    ],
)
def test_update_music_validates_its_body(app, database, body, field):
    admin = admin_of(app, database)
    track = add_track(admin)["track"]
    assert_invalid(admin.browser.patch(f"/admin/music/{track['id']}", body), field)
    row = database.one("SELECT title, moods, license FROM music_tracks WHERE id = :id", id=track["id"])
    assert (row["title"], list(row["moods"]), row["license"]) == ("Night Drive", ["calm"], "CC BY 4.0: test")


@pytest.mark.parametrize("track_id", [NOBODY, "not-a-uuid"])
def test_an_unknown_track_is_404(app, database, track_id):
    admin = admin_of(app, database)
    assert_error(admin.browser.patch(f"/admin/music/{track_id}", {"title": "x"}), 404, "not_found")
    assert_error(admin.browser.delete(f"/admin/music/{track_id}"), 404, "not_found")


def test_deleting_an_unused_track_removes_the_row_and_the_file(app, database):
    admin = admin_of(app, database)
    track = add_track(admin)["track"]
    key = track_key(database, track["id"])
    put_file(app, key)
    res = admin.browser.delete(f"/admin/music/{track['id']}")
    assert res.status_code == 200 and res.get_json() == {"deleted": True, "track": None}
    assert database.value("SELECT count(*) FROM music_tracks") == 0
    assert not file_exists(app, key)
    assert_error(admin.browser.delete(f"/admin/music/{track['id']}"), 404, "not_found")


def test_deleting_a_track_a_reel_uses_only_deactivates_it(app, database):
    admin, other = admin_of(app, database), creator(app)
    track = add_track(admin)["track"]
    key = track_key(database, track["id"])
    put_file(app, key)
    assert admin.browser.patch(f"/admin/music/{track['id']}", {"isActive": True}).status_code == 200
    project = scripted_project(app, other.browser)
    database.run("UPDATE projects SET music_track_id = :t WHERE id = :p", t=track["id"], p=project["id"])

    res = admin.browser.delete(f"/admin/music/{track['id']}")
    assert res.status_code == 200
    body = res.get_json()
    assert body["deleted"] is False and body["track"]["id"] == track["id"]
    assert body["track"]["isActive"] is False and set(body["track"]) == TRACK_FIELDS
    assert file_exists(app, key), "the file of a track still in use was deleted"
    assert (
        database.value("SELECT music_track_id::text FROM projects WHERE id = :p", p=project["id"])
        == track["id"]
    )
    assert admin.browser.get("/music").get_json()["items"] == []
    assert [t["id"] for t in admin.browser.get("/admin/music").get_json()["items"]] == [track["id"]]
    # Once no Reel uses it, it can be deleted for real.
    database.run("UPDATE projects SET music_track_id = NULL WHERE id = :p", p=project["id"])
    assert admin.browser.delete(f"/admin/music/{track['id']}").get_json()["deleted"] is True
    assert not file_exists(app, key)


# ---------------------------------------------------------------- I14 stats


def test_stats_count_users_reels_todays_failed_jobs_and_credits_held(app, database):
    admin, other = admin_of(app, database), creator(app)
    assert set(admin.browser.get("/admin/stats").get_json()) == {
        "users", "projects", "failedJobsToday", "creditsInCirculation",
    }  # fmt: skip
    project = scripted_project(app, other.browser)  # costs 1
    scripted_project(app, other.browser)
    failed_ideas_job(app, other)
    database.run(
        "INSERT INTO jobs (user_id, type, state, finished_at) "
        "VALUES (:u, 'ideas', 'failed', now() - interval '2 days')",
        u=other.id,
    )
    assert other.browser.delete(f"/projects/{project['id']}").status_code in (200, 204)
    assert admin.browser.get("/admin/stats").get_json() == {
        "users": 2,
        "projects": 1,
        "failedJobsToday": 1,
        "creditsInCirculation": stored_balance(database, admin.id) + stored_balance(database, other.id),
    }


def test_stats_do_not_count_the_reels_of_a_deleted_account(app, database):
    admin, gone = admin_of(app, database), creator(app)
    scripted_project(app, gone.browser)
    assert gone.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    stats = admin.browser.get("/admin/stats").get_json()
    assert stats["users"] == 1
    assert stats["projects"] == 0, "the Reels of a deleted account are counted"


def test_stats_do_not_count_the_credits_of_a_deleted_account(app, database):
    admin, gone = admin_of(app, database), creator(app)
    assert gone.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    stats = admin.browser.get("/admin/stats").get_json()
    assert stats["users"] == 1
    assert stats["creditsInCirculation"] == 100, "the credits of a deleted account are counted as held"


# ---------------------------------------------------------------- clean-up of replaced files


def clean(app) -> int:
    with app.app_context():
        return cleanup.clean_replaced()


def test_clean_up_only_takes_assets_replaced_more_than_7_days_ago(app, database):
    me = creator(app)
    project = scripted_project(app, me.browser)
    old, old_key = asset_with_file(app, database, me.id, project["id"])
    recent, recent_key = asset_with_file(app, database, me.id, project["id"])
    current, current_key = asset_with_file(app, database, me.id, project["id"])
    replaced(database, old, "7 days 1 minute")
    replaced(database, recent, "6 days 23 hours")
    database.run("UPDATE assets SET created_at = now() - interval '90 days' WHERE id = :a", a=current)

    assert clean(app) == 1
    assert database.value("SELECT count(*) FROM assets WHERE id = :a", a=old) == 0
    assert not file_exists(app, old_key), "the row went but the file stayed"
    assert database.value("SELECT replaced_at IS NOT NULL FROM assets WHERE id = :a", a=recent) is True
    assert database.value("SELECT replaced_at IS NULL FROM assets WHERE id = :a", a=current) is True
    assert file_exists(app, recent_key) and file_exists(app, current_key)
    assert clean(app) == 0


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("scenes", "voice_asset_id"),
        ("scenes", "visual_asset_id"),
        ("scenes", "keyframe_asset_id"),
        ("projects", "thumbnail_asset_id"),
        ("projects", "music_asset_id"),
    ],
)
def test_clean_up_keeps_and_unflags_an_asset_still_in_use(app, database, table, column):
    me = creator(app)
    project = scripted_project(app, me.browser)
    used, used_key = asset_with_file(app, database, me.id, project["id"])
    unused, unused_key = asset_with_file(app, database, me.id, project["id"])
    replaced(database, used, "30 days")
    replaced(database, unused, "30 days")
    where = "id = :p" if table == "projects" else "id = (SELECT id FROM scenes WHERE project_id = :p LIMIT 1)"
    database.run(f"UPDATE {table} SET {column} = :a WHERE {where}", a=used, p=project["id"])  # noqa: S608

    assert clean(app) == 1
    assert database.value("SELECT replaced_at IS NULL FROM assets WHERE id = :a", a=used) is True
    assert file_exists(app, used_key) and not file_exists(app, unused_key)
    assert clean(app) == 0, "the kept asset was looked at again"


def test_clean_up_keeps_a_renders_video_and_cover(app, database):
    me = creator(app)
    project, render_id = rendered_project(app, me.browser)
    render = database.one(
        "SELECT asset_id::text, thumbnail_asset_id::text FROM renders WHERE id = :r", r=render_id
    )
    assert render["asset_id"] and render["thumbnail_asset_id"]
    extra, extra_key = asset_with_file(app, database, me.id, project["id"])
    for asset_id in (render["asset_id"], render["thumbnail_asset_id"], extra):
        replaced(database, asset_id, "8 days")
    keys = [
        r["s3_key"]
        for r in database.rows(
            "SELECT s3_key FROM assets WHERE id IN (:a, :b)",
            a=render["asset_id"],
            b=render["thumbnail_asset_id"],
        )
    ]

    assert clean(app) == 1
    assert all(file_exists(app, key) for key in keys) and not file_exists(app, extra_key)
    assert database.value(
        "SELECT count(*) FROM assets WHERE id IN (:a, :b) AND replaced_at IS NULL",
        a=render["asset_id"],
        b=render["thumbnail_asset_id"],
    ) == 2  # fmt: skip
    # The render still plays.
    res = me.browser.get(f"/renders/{render_id}")
    assert res.status_code == 200 and res.get_json()["render"]["video"]["url"]


def test_clean_up_works_through_more_than_one_batch(app, database, monkeypatch):
    monkeypatch.setattr(cleanup, "BATCH", 3)
    me = creator(app)
    project = scripted_project(app, me.browser)
    scenes = [s["id"] for s in project["scenes"]][:2]
    used = []
    for scene_id in scenes:
        asset_id, key = asset_with_file(app, database, me.id, project["id"])
        database.run("UPDATE scenes SET voice_asset_id = :a WHERE id = :s", a=asset_id, s=scene_id)
        used.append((asset_id, key))
    unused = [asset_with_file(app, database, me.id, project["id"]) for _ in range(8)]
    database.run(
        "UPDATE assets SET replaced_at = now() - interval '9 days' WHERE project_id = :p", p=project["id"]
    )

    assert clean(app) == 8
    assert not any(file_exists(app, key) for _, key in unused)
    assert all(file_exists(app, key) for _, key in used)
    assert database.value(
        "SELECT count(*) FROM assets WHERE project_id = :p AND replaced_at IS NULL", p=project["id"]
    ) == len(used)  # fmt: skip
    assert database.value("SELECT count(*) FROM assets WHERE replaced_at IS NOT NULL") == 0


def test_clean_up_keeps_the_row_of_a_file_it_could_not_delete(app, database, monkeypatch):
    """cleanup._delete_files: a failed delete is "tried again on the next run". That needs the
    asset row, which is the only list of the file."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    asset_id, key = asset_with_file(app, database, me.id, project["id"])
    replaced(database, asset_id, "8 days")
    real = Storage.delete

    def storage_down(self, key):
        raise ConnectionError("storage is unreachable")

    monkeypatch.setattr(Storage, "delete", storage_down)
    assert clean(app) == 0
    monkeypatch.setattr(Storage, "delete", real)
    assert file_exists(app, key)
    assert (
        database.value("SELECT count(*) FROM assets WHERE id = :a", a=asset_id) == 1
    ), "the file is still in storage but nothing lists it any more, so no later run can delete it"
    assert clean(app) == 1 and not file_exists(app, key)


def test_clean_up_removes_a_replaced_visuals_thumbnail_too(app, database):
    """A visual is two files: the picture or clip and its small still (meta.thumbnailKey)."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    scene_id = project["scenes"][0]["id"]
    for _ in range(2):  # the second take replaces the first
        res = me.browser.post(f"/scenes/{scene_id}/visual/regenerate", {"visualSource": "ai_image"})
        assert res.status_code == 202, res.get_data(as_text=True)
        run_jobs(app)
    prefix = f"users/{me.id}/projects/{project['id']}/visual/"
    assert (
        database.value(
            "SELECT count(*) FROM assets WHERE project_id = :p AND replaced_at IS NOT NULL", p=project["id"]
        )
        == 1
    )
    current = database.one(
        "SELECT s3_key, meta FROM assets WHERE project_id = :p AND kind <> 'voice' AND replaced_at IS NULL",
        p=project["id"],
    )
    expected = sorted(k for k in (current["s3_key"], current["meta"].get("thumbnailKey")) if k)
    assert len(keys_under(app, prefix)) > len(expected), "the first take's files should still be there"
    database.run(
        "UPDATE assets SET replaced_at = replaced_at - interval '8 days' WHERE replaced_at IS NOT NULL"
    )

    assert clean(app) == 1
    assert keys_under(app, prefix) == expected, "files of the replaced take were left in storage"


# ---------------------------------------------------------------- the purge deletes files first


def purge(app) -> int:
    with app.app_context():
        return purge_deleted.run()


def test_the_purge_deletes_the_files_of_accounts_deleted_over_7_days_ago(app, database):
    old, recent, active = creator(app), creator(app), creator(app)
    files = {}
    for who in (old, recent, active):
        project = scripted_project(app, who.browser)
        files[who.id] = asset_with_file(app, database, who.id, project["id"])[1]
    for who in (old, recent):
        assert who.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    database.run("UPDATE users SET deleted_at = now() - interval '7 days 1 minute' WHERE id = :u", u=old.id)
    database.run(
        "UPDATE users SET deleted_at = now() - interval '6 days 23 hours' WHERE id = :u", u=recent.id
    )

    assert purge(app) == 1
    assert not file_exists(app, files[old.id])
    assert file_exists(app, files[recent.id]) and file_exists(app, files[active.id])
    assert database.value("SELECT count(*) FROM assets WHERE user_id = :u", u=old.id) == 0
    assert database.value("SELECT count(*) FROM assets") == 2


def test_the_purge_deletes_the_files_of_reels_deleted_over_7_days_ago(app, database):
    me = creator(app)
    old = scripted_project(app, me.browser)
    recent = scripted_project(app, me.browser)
    kept = scripted_project(app, me.browser)
    keys = {p["id"]: asset_with_file(app, database, me.id, p["id"])[1] for p in (old, recent, kept)}
    for project in (old, recent):
        assert me.browser.delete(f"/projects/{project['id']}").status_code in (200, 204)
    database.run("UPDATE projects SET deleted_at = now() - interval '8 days' WHERE id = :p", p=old["id"])
    database.run("UPDATE projects SET deleted_at = now() - interval '6 days' WHERE id = :p", p=recent["id"])

    purge(app)
    assert not file_exists(app, keys[old["id"]])
    assert file_exists(app, keys[recent["id"]]) and file_exists(app, keys[kept["id"]])
    assert database.value("SELECT count(*) FROM projects WHERE id = :p", p=old["id"]) == 0
    assert database.value("SELECT count(*) FROM assets WHERE project_id = :p", p=old["id"]) == 0
    assert database.value("SELECT count(*) FROM projects WHERE user_id = :u", u=me.id) == 2


def test_the_purge_deletes_files_while_the_rows_still_list_them(app, database, monkeypatch):
    """Files first, then rows: once the rows are gone nothing knows where the files are."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    _, key = asset_with_file(app, database, me.id, project["id"])
    assert me.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    database.run("UPDATE users SET deleted_at = now() - interval '8 days' WHERE id = :u", u=me.id)
    real = Storage.delete
    seen = []

    def watching(self, deleted_key):
        seen.append((deleted_key, database.value("SELECT count(*) FROM users WHERE id = :u", u=me.id)))
        return real(self, deleted_key)

    monkeypatch.setattr(Storage, "delete", watching)
    assert purge(app) == 1
    assert (key, 1) in seen, "the file was not deleted while its account's rows still existed"
    assert database.value("SELECT count(*) FROM users WHERE id = :u", u=me.id) == 0


def test_the_purge_leaves_nothing_of_a_finished_reel_in_storage(app, database):
    """A whole Reel: voices, visuals with their small stills, the render and its cover."""
    me, other = creator(app), creator(app)
    project, _ = rendered_project(app, me.browser)
    res = me.browser.post(
        f"/scenes/{project['scenes'][0]['id']}/visual/regenerate", {"visualSource": "ai_image"}
    )
    assert res.status_code == 202, res.get_data(as_text=True)
    run_jobs(app)
    assert any(
        k.endswith("-thumb.jpg") for k in keys_under(app, f"users/{me.id}/")
    ), "no small still was made"
    theirs = ready_project(app, other.browser)
    mine = keys_under(app, f"users/{me.id}/")
    assert len(mine) >= 3, mine
    their_files = keys_under(app, f"users/{other.id}/")
    assert me.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    database.run("UPDATE users SET deleted_at = now() - interval '8 days' WHERE id = :u", u=me.id)

    assert purge(app) == 1
    assert keys_under(app, f"users/{me.id}/") == [], "files of a purged account were left in storage"
    assert keys_under(app, f"users/{other.id}/") == their_files
    assert get_project(other.browser, theirs["id"])["status"] == "ready"


def test_the_purge_keeps_the_rows_when_storage_is_down(app, database, monkeypatch):
    """With storage unreachable the files can't be deleted. Purging the rows anyway would leave
    the files with nothing listing them; the next daily run must be able to finish the job."""
    me = creator(app)
    project = scripted_project(app, me.browser)
    asset_id, key = asset_with_file(app, database, me.id, project["id"])
    assert me.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    database.run("UPDATE users SET deleted_at = now() - interval '8 days' WHERE id = :u", u=me.id)
    real = Storage.delete

    def storage_down(self, key):
        raise ConnectionError("storage is unreachable")

    monkeypatch.setattr(Storage, "delete", storage_down)
    purge(app)
    monkeypatch.setattr(Storage, "delete", real)
    assert file_exists(app, key)
    assert (
        database.value("SELECT count(*) FROM assets WHERE id = :a", a=asset_id) == 1
    ), "the account's rows were purged although its files are still in storage"
    assert purge(app) == 1 and not file_exists(app, key)


def test_the_purge_removes_what_it_could_and_leaves_the_rest_for_the_next_run(app, database, monkeypatch):
    """QB8-002: only accounts whose files were all deleted lose their rows."""
    stuck, fine = creator(app), creator(app)
    keys = {}
    for who in (stuck, fine):
        project = scripted_project(app, who.browser)
        keys[who.id] = [asset_with_file(app, database, who.id, project["id"])[1] for _ in range(2)]
        assert who.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    database.run("UPDATE users SET deleted_at = now() - interval '8 days'")
    real = Storage.delete
    unlucky = keys[stuck.id][1]

    def one_fails(self, key):
        if key == unlucky:
            raise ConnectionError("storage hiccup")
        return real(self, key)

    monkeypatch.setattr(Storage, "delete", one_fails)
    assert purge(app) == 1
    assert database.value("SELECT count(*) FROM users WHERE id = :u", u=fine.id) == 0
    assert not any(file_exists(app, k) for k in keys[fine.id])
    assert database.value("SELECT count(*) FROM users WHERE id = :u", u=stuck.id) == 1
    assert database.value("SELECT count(*) FROM assets WHERE s3_key = :k", k=unlucky) == 1
    assert file_exists(app, unlucky)
    monkeypatch.setattr(Storage, "delete", real)
    assert purge(app) == 1
    assert not file_exists(app, unlucky) and database.value("SELECT count(*) FROM users") == 0


def test_the_purge_keeps_files_a_duplicate_of_the_deleted_reel_still_uses(app, database):
    """QB8-001: a duplicated Reel shares its source's files; purging the source hands them over."""
    me = creator(app)
    source = ready_project(app, me.browser)
    res = me.browser.post(f"/projects/{source['id']}/duplicate")
    assert res.status_code in (200, 201), res.get_data(as_text=True)
    copy = get_project(me.browser, res.get_json()["project"]["id"])
    shared = [s[part]["assetId"] for s in copy["scenes"] for part in ("voice", "visual")]
    owned = {
        str(r["id"]) for r in database.rows("SELECT id FROM assets WHERE project_id = :p", p=source["id"])
    }
    assert shared and set(shared) <= owned, "this test needs the copy to share the source's files"
    _, own_key = asset_with_file(app, database, me.id, source["id"])
    assert me.browser.delete(f"/projects/{source['id']}").status_code in (200, 204)
    database.run("UPDATE projects SET deleted_at = now() - interval '8 days' WHERE id = :p", p=source["id"])

    purge(app)
    assert database.value("SELECT count(*) FROM projects WHERE id = :p", p=source["id"]) == 0
    assert not file_exists(app, own_key), "a file only the deleted Reel used was kept"
    after = get_project(me.browser, copy["id"])
    assert after["status"] == copy["status"]
    for scene in after["scenes"]:
        assert scene["voiceStatus"] == "ready" and scene["visualStatus"] == "ready"
        assert download(scene["voice"]["url"]), "the copy's voice file is gone"
        assert download(scene["visual"]["url"]), "the copy's visual file is gone"
    owners = database.rows(
        "SELECT DISTINCT project_id::text FROM assets WHERE id::text = ANY(:ids)", ids=shared
    )
    assert [r["project_id"] for r in owners] == [copy["id"]]


def test_the_purge_removes_the_reels_of_a_deleted_account_with_their_files(app, database):
    me = creator(app)
    live = scripted_project(app, me.browser)
    binned = scripted_project(app, me.browser)
    keys = [asset_with_file(app, database, me.id, p["id"])[1] for p in (live, binned)]
    assert me.browser.delete(f"/projects/{binned['id']}").status_code in (200, 204)
    assert me.browser.delete("/me", {"password": PASSWORD}).status_code == 204
    database.run("UPDATE users SET deleted_at = now() - interval '8 days' WHERE id = :u", u=me.id)
    assert purge(app) == 1
    assert database.value("SELECT count(*) FROM projects") == 0
    assert database.value("SELECT count(*) FROM assets") == 0
    assert not any(file_exists(app, k) for k in keys)


def test_the_purge_with_nothing_to_do_touches_no_file(app, database, monkeypatch):
    me = creator(app)
    project = scripted_project(app, me.browser)
    _, key = asset_with_file(app, database, me.id, project["id"])
    calls = []
    monkeypatch.setattr(Storage, "delete", lambda self, key: calls.append(key))
    assert purge(app) == 0
    assert calls == [] and file_exists(app, key)


# ---------------------------------------------------------------- the schedule


def test_the_beat_schedule_runs_the_file_clean_up_daily_on_the_maintenance_queue(app):
    celery = app.extensions["celery"]
    entries = [e for e in celery.conf.beat_schedule.values() if e["task"] == "files.clean_replaced"]
    assert len(entries) == 1
    schedule = entries[0]["schedule"]
    assert isinstance(schedule, crontab)
    assert len(schedule.hour) == 1 and len(schedule.minute) == 1, "not once a day"
    assert len(schedule.day_of_week) == 7 and len(schedule.day_of_month) == 31
    assert entries[0]["options"] == {"queue": "maintenance"}
    assert "files.clean_replaced" in celery.tasks
    # Every scheduled task exists, and the purge is still scheduled.
    for entry in celery.conf.beat_schedule.values():
        assert entry["task"] in celery.tasks, entry["task"]
    assert "accounts.purge_deleted" in {e["task"] for e in celery.conf.beat_schedule.values()}


def test_the_scheduled_task_runs_the_clean_up(app, database):
    me = creator(app)
    project = scripted_project(app, me.browser)
    asset_id, key = asset_with_file(app, database, me.id, project["id"])
    replaced(database, asset_id, "8 days")
    with app.app_context():
        assert app.extensions["celery"].tasks["files.clean_replaced"].run() == 1
    assert not file_exists(app, key)
