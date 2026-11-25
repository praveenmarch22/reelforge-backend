"""G1-G3 jobs, H1-H2 credits, the job engine (cancel, stuck jobs, usage) and the credit rules
(docs/API_DESIGN.md groups G and H; TECHNICAL_DESIGN.md 7, 8; DB_DESIGN.md 3.5, 3.6, 6.1, 6.2;
PRD 5.10, CR-1..CR-4, G-12)."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from reelforge_api import credits, jobs
from reelforge_api.extensions import db
from reelforge_api.providers.llm import MockLLM
from reelforge_api.script_rules import COSTS
from reelforge_api.tasks.jobs import fail_stuck_jobs, reset_monthly_credits

from .projects_support import (
    ISO_UTC,
    JOB_FIELDS,
    SCRIPT_COST,
    TOPIC,
    assert_ledger_matches_balance,
    balance,
    creator,
    get_job,
    get_project,
    job_ledger,
    run_jobs,
    scripted_project,
    set_balance,
    start_project,
    stored_balance,
    waiting_jobs,
)
from .support import Browser, assert_error, assert_invalid, first_of_next_month, log_in

# ---------------------------------------------------------------- G1, G3


def test_a_job_can_be_read_by_its_owner(app, outbox):
    me = creator(app)
    _, job = start_project(me.browser)
    found = get_job(me.browser, job["id"])
    assert set(found) == JOB_FIELDS
    assert found == job


def test_cancelling_a_queued_job_refunds_it_and_the_worker_then_skips_it(app, database, outbox):
    me = creator(app)
    project, job = start_project(me.browser)
    res = me.browser.post(f"/jobs/{job['id']}/cancel")
    assert res.status_code == 200, res.get_data(as_text=True)
    assert set(res.get_json()) == {"job"}
    cancelled = res.get_json()["job"]
    assert cancelled["state"] == "cancelled" and cancelled["creditsRefunded"] is True
    assert ISO_UTC.match(cancelled["finishedAt"])
    assert balance(me.browser) == 100
    assert get_project(me.browser, project["id"])["status"] == "draft"

    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "cancelled"
    assert get_project(me.browser, project["id"])["scenes"] == [], "a cancelled script writes nothing"
    assert job_ledger(database, job["id"]) == [("reserve", -SCRIPT_COST), ("refund", SCRIPT_COST)]

    # Finished jobs can't be cancelled; nothing is refunded twice.
    assert_error(me.browser.post(f"/jobs/{job['id']}/cancel"), 409, "job_finished")
    assert balance(me.browser) == 100
    assert_ledger_matches_balance(database, me.id)


@pytest.mark.parametrize("marker", ["", "[fail]"])
def test_a_finished_job_cannot_be_cancelled(app, database, outbox, marker):
    me = creator(app)
    _, job = start_project(me.browser, topic=f"{TOPIC} {marker}")
    run_jobs(app)
    before = balance(me.browser)
    assert_error(me.browser.post(f"/jobs/{job['id']}/cancel"), 409, "job_finished")
    assert balance(me.browser) == before
    assert_ledger_matches_balance(database, me.id)


def cancel_from_another_browser(app, browser: Browser, job_id: str) -> int:
    """Cancels through the API on another thread (its own connection), as the web app would
    while the worker is busy."""
    result: list[int] = []
    thread = threading.Thread(
        target=lambda: result.append(browser.post(f"/jobs/{job_id}/cancel").status_code)
    )
    thread.start()
    thread.join(timeout=30)
    assert result, "the cancel request did not finish"
    return result[0]


def test_a_job_cancelled_while_the_model_is_working_drops_its_result(app, database, outbox, monkeypatch):
    """Technical design 7 rule 4: tasks check for cancellation before saving output. The
    creator is refunded, so they must not also get the script."""
    me = creator(app)
    project, job = start_project(me.browser)
    real = MockLLM.generate_json
    statuses: list[int] = []

    def slow_model(self, prompt, kind, context):
        statuses.append(cancel_from_another_browser(app, me.browser, job["id"]))
        return real(self, prompt, kind, context)

    monkeypatch.setattr(MockLLM, "generate_json", slow_model)
    run_jobs(app)
    assert statuses == [200]
    final = get_job(me.browser, job["id"])
    assert final["state"] == "cancelled", final
    assert final["creditsRefunded"] is True and final["output"] is None
    project = get_project(me.browser, project["id"])
    assert project["scenes"] == [] and project["status"] == "draft"
    assert balance(me.browser) == 100
    assert_ledger_matches_balance(database, me.id)


def test_a_rewrite_cancelled_while_running_leaves_the_scene_alone(app, database, outbox, monkeypatch):
    me = creator(app)
    project = scripted_project(app, me.browser)
    scene = project["scenes"][0]
    job = me.browser.post(f"/scenes/{scene['id']}/rewrite", {"instructions": "shorter"}).get_json()["job"]
    real = MockLLM.generate_json

    def slow_model(self, prompt, kind, context):
        cancel_from_another_browser(app, me.browser, job["id"])
        return real(self, prompt, kind, context)

    monkeypatch.setattr(MockLLM, "generate_json", slow_model)
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "cancelled"
    assert get_project(me.browser, project["id"])["scenes"][0] == scene
    assert_ledger_matches_balance(database, me.id)


def test_cancelling_an_ideas_job_works_and_refunds_nothing(app, database, outbox):
    me = creator(app)
    job = me.browser.post("/ideas", {"niche": "fitness"}).get_json()["job"]
    res = me.browser.post(f"/jobs/{job['id']}/cancel")
    assert res.status_code == 200
    assert res.get_json()["job"]["state"] == "cancelled"
    assert res.get_json()["job"]["creditsRefunded"] is False, "nothing was reserved"
    assert job_ledger(database, job["id"]) == []


@pytest.mark.parametrize("job_id", ["not-a-uuid", str(uuid.uuid4()), "1"])
def test_unknown_jobs_are_404(app, outbox, job_id):
    me = creator(app)
    assert_error(me.browser.get(f"/jobs/{job_id}"), 404, "not_found")
    assert_error(me.browser.post(f"/jobs/{job_id}/cancel"), 404, "not_found")


# ---------------------------------------------------------------- G2


def test_project_jobs_are_listed_newest_first_with_an_active_filter(app, database, outbox):
    me = creator(app)
    project = scripted_project(app, me.browser)
    ids = [s["id"] for s in project["scenes"]]
    rewrites = [
        me.browser.post(f"/scenes/{s}/rewrite", {"instructions": "x"}).get_json()["job"] for s in ids[:3]
    ]
    first_script = database.value(
        "SELECT id::text FROM jobs WHERE project_id = :p AND type = 'script'", p=project["id"]
    )
    res = me.browser.get(f"/projects/{project['id']}/jobs")
    assert res.status_code == 200
    body = res.get_json()
    assert set(body) == {"items", "nextCursor"}
    assert {j["id"] for j in body["items"]} == {first_script, *[r["id"] for r in rewrites]}
    assert body["items"][-1]["id"] == first_script, "newest first"
    for item in body["items"]:
        assert set(item) == JOB_FIELDS
    active = me.browser.get(f"/projects/{project['id']}/jobs?active=true").get_json()["items"]
    assert {j["id"] for j in active} == {r["id"] for r in rewrites}
    assert len(me.browser.get(f"/projects/{project['id']}/jobs?active=false").get_json()["items"]) == 4

    seen, cursor = [], None
    while True:
        page = me.browser.get(
            f"/projects/{project['id']}/jobs?limit=1{'&cursor=' + cursor if cursor else ''}"
        )
        assert page.status_code == 200
        seen += [j["id"] for j in page.get_json()["items"]]
        cursor = page.get_json()["nextCursor"]
        if not cursor:
            break
    assert sorted(seen) == sorted(j["id"] for j in body["items"])
    run_jobs(app)


@pytest.mark.parametrize(
    ("query", "field"),
    [("?active=yes", "active"), ("?limit=0", "limit"), ("?limit=101", "limit"), ("?cursor=@@", "cursor")],
)
def test_project_jobs_refuse_bad_parameters(app, outbox, query, field):
    me = creator(app)
    project, _ = start_project(me.browser)
    assert_invalid(me.browser.get(f"/projects/{project['id']}/jobs{query}"), field)


# ---------------------------------------------------------------- H1, H2


def test_credits_show_balance_allowance_reset_date_and_costs(app, outbox):
    me = creator(app)
    res = me.browser.get("/credits")
    assert res.status_code == 200
    body = res.get_json()
    assert set(body) == {"balance", "monthlyAllowance", "resetsAt", "costs"}
    assert (body["balance"], body["monthlyAllowance"], body["resetsAt"]) == (100, 100, first_of_next_month())
    documented = {
        "script": 1,
        "voicePer30s": 2,
        "aiImage": 1,
        "aiVideoClip": 10,
        "render": 2,
        "transcribe": 1,
    }
    assert body["costs"].items() >= documented.items(), "the PRD's starting costs"
    assert body["costs"] == COSTS, "the same table the API charges from"
    assert all(isinstance(v, int) and v >= 0 for v in body["costs"].values())


def test_the_ledger_lists_every_movement_newest_first(app, database, outbox):
    me = creator(app)
    project, job = start_project(me.browser)
    me.browser.post(f"/jobs/{job['id']}/cancel")
    res = me.browser.get("/credits/ledger")
    assert res.status_code == 200
    body = res.get_json()
    assert set(body) == {"items", "nextCursor"} and body["nextCursor"] is None
    items = body["items"]
    for entry in items:
        assert set(entry) == {"id", "amount", "kind", "note", "jobId", "projectId", "createdAt"}
        assert ISO_UTC.match(entry["createdAt"])
    assert [(e["kind"], e["amount"]) for e in items] == [("refund", 1), ("reserve", -1), ("grant", 100)]
    assert items[0]["jobId"] == job["id"] and items[0]["projectId"] == project["id"]
    assert items[2]["jobId"] is None and items[2]["projectId"] is None
    assert items[2]["note"]

    page = me.browser.get("/credits/ledger?limit=2").get_json()
    assert len(page["items"]) == 2 and page["nextCursor"]
    rest = me.browser.get(f"/credits/ledger?limit=2&cursor={page['nextCursor']}").get_json()
    assert [e["id"] for e in page["items"] + rest["items"]] == [e["id"] for e in items]
    assert rest["nextCursor"] is None


def test_the_ledger_shows_only_my_own_movements(app, outbox):
    me, other = creator(app), creator(app)
    start_project(other.browser)
    assert [e["kind"] for e in me.browser.get("/credits/ledger").get_json()["items"]] == ["grant"]


# ---------------------------------------------------------------- CR-3: two requests at once


def wait_for(condition, timeout: float = 15.0, what: str = "condition") -> None:
    """Polls until `condition()` is true (no fixed sleeps)."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.02)


def test_two_reservations_at_the_same_moment_cannot_overspend(app, database, outbox):
    """CR-3: with 1 credit left, two scripts requested at once: one starts, the other is 402,
    and the balance never goes below zero.

    A third connection holds the credit account's row lock while both requests arrive, so
    both are certainly inside their transactions at the same time; then it lets go."""
    me = creator(app)
    second = log_in(app, me.account)
    set_balance(database, me.id, SCRIPT_COST)
    results: dict[str, int] = {}

    with app.app_context():
        holder = db.engine.connect()
        holder_tx = holder.begin()
        holder.execute(
            text("SELECT balance FROM credit_accounts WHERE user_id = :u FOR UPDATE"), {"u": me.id}
        )

        def request(name: str, browser: Browser) -> None:
            results[name] = browser.post("/projects", {"topic": f"{TOPIC} {name}"}).status_code

        threads = [
            threading.Thread(target=request, args=("one", me.browser)),
            threading.Thread(target=request, args=("two", second)),
        ]
        for thread in threads:
            thread.start()

        def both_waiting() -> bool:
            # Inside a transaction the activity view is a snapshot unless cleared.
            holder.execute(text("SELECT pg_stat_clear_snapshot()"))
            return (
                holder.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                        "AND query ILIKE '%credit_accounts%' AND datname = current_database()"
                    )
                ).scalar_one()
                >= 2
            )

        try:
            wait_for(both_waiting, what="both requests to wait for the credit account")
        finally:
            holder_tx.commit()
            holder.close()
        for thread in threads:
            thread.join(timeout=30)

    assert sorted(results.values()) == [202, 402], results
    assert stored_balance(database, me.id) == 0
    assert database.value("SELECT count(*) FROM jobs WHERE user_id = :u", u=me.id) == 1
    assert database.value("SELECT count(*) FROM projects WHERE user_id = :u", u=me.id) == 1
    assert_ledger_matches_balance(database, me.id)
    jobs.manual_queue.clear()


def test_many_reservations_in_parallel_spend_exactly_the_balance(app, database, outbox):
    """CR-3 without a held lock: eight browsers ask at once with 3 credits."""
    me = creator(app)
    browsers = [log_in(app, me.account) for _ in range(8)]
    set_balance(database, me.id, 3)
    barrier = threading.Barrier(len(browsers))
    results: list[int] = []

    def request(browser: Browser) -> None:
        barrier.wait(timeout=30)
        results.append(browser.post("/projects", {"topic": TOPIC}).status_code)

    threads = [threading.Thread(target=request, args=(b,)) for b in browsers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert sorted(results) == [202] * 3 + [402] * 5, results
    assert stored_balance(database, me.id) == 0
    assert_ledger_matches_balance(database, me.id)
    jobs.manual_queue.clear()


# ---------------------------------------------------------------- refunds


def test_a_refund_happens_at_most_once(app, database, outbox):
    """DB design 6.2: safe to run twice."""
    me = creator(app)
    _, job = start_project(me.browser)
    with app.app_context():
        found = db.session.get(jobs.Job, uuid.UUID(job["id"]))
        assert credits.refund(found) == SCRIPT_COST
        assert credits.refund(found) == 0
        db.session.commit()
        jobs.fail(found.id, jobs.JobFailed("internal_error"))
        jobs.fail(found.id, jobs.JobFailed("internal_error"))
    assert balance(me.browser) == 100
    assert job_ledger(database, job["id"]) == [("reserve", -SCRIPT_COST), ("refund", SCRIPT_COST)]
    assert get_job(me.browser, job["id"])["state"] == "failed"
    assert_error(me.browser.post(f"/jobs/{job['id']}/cancel"), 409, "job_finished")
    assert_ledger_matches_balance(database, me.id)
    jobs.manual_queue.clear()


def test_a_job_is_reserved_once_per_job_in_the_ledger(app, database, outbox):
    me = creator(app)
    _, job = start_project(me.browser)
    with pytest.raises(Exception, match="credit_ledger_once_per_job"):
        database.run(
            "INSERT INTO credit_ledger (user_id, amount, kind, job_id) VALUES (:u, -1, 'reserve', :j)",
            u=me.id,
            j=job["id"],
        )
    with pytest.raises(Exception, match="credit_accounts"):
        database.run("UPDATE credit_accounts SET balance = -1 WHERE user_id = :u", u=me.id)
    jobs.manual_queue.clear()


# ---------------------------------------------------------------- stuck jobs


def age_job(database, job_id: str, *, state: str, minutes: int) -> None:
    moment = datetime.now(UTC) - timedelta(minutes=minutes)
    database.run(
        "UPDATE jobs SET state = :s, queued_at = :m, started_at = CASE WHEN :s = 'running' THEN :m END "
        "WHERE id = :j",
        s=state,
        m=moment,
        j=job_id,
    )


def test_stuck_jobs_are_failed_and_refunded(app, database, outbox):
    """Technical design 7: jobs running past their time limit fail and are refunded."""
    me = creator(app)
    projects_and_jobs = [start_project(me.browser) for _ in range(4)]
    (stuck_project, stuck), (fresh_project, fresh), (_, old_queue), (_, new_queue) = projects_and_jobs
    database.run("UPDATE projects SET status = 'scripting'")
    age_job(database, stuck["id"], state="running", minutes=30)
    age_job(database, fresh["id"], state="running", minutes=1)
    age_job(database, old_queue["id"], state="queued", minutes=45)
    age_job(database, new_queue["id"], state="queued", minutes=2)

    with app.app_context():
        assert fail_stuck_jobs.run() == 2

    for job, state in ((stuck, "failed"), (fresh, "running"), (old_queue, "failed"), (new_queue, "queued")):
        found = get_job(me.browser, job["id"])
        assert found["state"] == state, (job["id"], found)
        if state == "failed":
            assert found["error"]["code"] and found["error"]["message"]
            assert found["creditsRefunded"] is True
    assert get_project(me.browser, stuck_project["id"])["status"] == "draft"
    assert get_project(me.browser, fresh_project["id"])["status"] == "scripting"
    assert balance(me.browser) == 100 - 2 * SCRIPT_COST
    with app.app_context():
        assert fail_stuck_jobs.run() == 0, "nothing is failed or refunded twice"
    assert_ledger_matches_balance(database, me.id)
    jobs.manual_queue.clear()


# ---------------------------------------------------------------- CR-4: monthly reset


def test_the_monthly_reset_grants_the_allowance_and_unused_credits_do_not_carry_over(app, database, outbox):
    me, later = creator(app), creator(app)
    set_balance(database, me.id, 37)
    due = datetime(2026, 11, 1, tzinfo=UTC)
    database.run("UPDATE credit_accounts SET resets_at = :d WHERE user_id = :u", d=due, u=me.id)

    with app.app_context():
        assert reset_monthly_credits.run() == 1

    account = database.one("SELECT balance, resets_at FROM credit_accounts WHERE user_id = :u", u=me.id)
    assert account["balance"] == 100
    assert account["resets_at"] == datetime(2026, 12, 1, tzinfo=UTC), "a month after the old date"
    assert balance(later.browser) == 100
    assert_ledger_matches_balance(database, me.id)
    kinds = [
        (r["kind"], r["amount"])
        for r in database.rows(
            "SELECT kind, amount FROM credit_ledger WHERE user_id = :u "
            "ORDER BY created_at DESC, kind LIMIT 2",
            u=me.id,
        )
    ]
    assert sorted(kinds) == [("adjust", -37), ("grant", 100)]

    with app.app_context():
        assert reset_monthly_credits.run() == 0, "not twice in the same month"
    assert stored_balance(database, me.id) == 100


def test_a_reset_that_runs_late_keeps_the_monthly_cycle(app, database, outbox):
    me = creator(app)
    database.run(
        "UPDATE credit_accounts SET resets_at = now() - interval '40 days' WHERE user_id = :u",
        u=me.id,
    )
    with app.app_context():
        reset_monthly_credits.run()
    resets_at = database.value("SELECT resets_at FROM credit_accounts WHERE user_id = :u", u=me.id)
    assert resets_at > datetime.now(UTC), "the next reset is in the future"
    assert resets_at.day == 1 and resets_at.hour == 0 and resets_at.minute == 0
    assert resets_at - datetime.now(UTC) <= timedelta(days=31)


def test_testers_get_their_own_allowance(app, database, outbox):
    me = creator(app)
    database.run(
        "UPDATE credit_accounts SET monthly_allowance = 500, "
        "resets_at = now() - interval '1 hour' WHERE user_id = :u",
        u=me.id,
    )
    with app.app_context():
        reset_monthly_credits.run()
    assert balance(me.browser) == 500
    assert_ledger_matches_balance(database, me.id)


def test_credits_reserved_by_a_running_job_survive_the_reset_and_are_refunded_once(app, database, outbox):
    """The reset sets the balance to the allowance; a job still running keeps its reservation
    and, if it then fails, is refunded exactly once on top."""
    me = creator(app)
    project, job = start_project(me.browser, topic=f"{TOPIC} [fail]")
    database.run(
        "UPDATE credit_accounts SET resets_at = now() - interval '1 hour' WHERE user_id = :u", u=me.id
    )
    with app.app_context():
        reset_monthly_credits.run()
    assert balance(me.browser) == 100
    run_jobs(app)
    assert get_job(me.browser, job["id"])["state"] == "failed"
    assert balance(me.browser) == 100 + SCRIPT_COST
    assert job_ledger(database, job["id"]) == [("reserve", -SCRIPT_COST), ("refund", SCRIPT_COST)]
    assert_ledger_matches_balance(database, me.id)
    assert get_project(me.browser, project["id"])["status"] == "draft"


# ---------------------------------------------------------------- usage counters


def test_finished_jobs_are_counted_for_the_admin_usage_page(app, database, outbox):
    me = creator(app)
    start_project(me.browser)
    start_project(me.browser, topic="x [fail]")
    me.browser.post("/ideas", {"niche": "food"})
    run_jobs(app)
    rows = {
        (r["provider"], r["job_type"]): r
        for r in database.rows("SELECT * FROM usage_daily WHERE day = current_date")
    }
    assert (
        rows[("mock", "script")]["calls"] == 1 and rows[("mock", "script")]["credits_charged"] == SCRIPT_COST
    )
    assert rows[("mock", "ideas")]["calls"] == 1
    failed = sum(r["failures"] for r in rows.values())
    assert failed == 1
    assert waiting_jobs() == []
