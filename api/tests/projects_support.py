"""Shared by the project, scene, job and credit tests (milestone B3).

In the test environment jobs are not sent to Celery: `jobs.dispatch` keeps their ids in
`reelforge_api.jobs.manual_queue` and `run_jobs` runs them, so a test decides when a job runs.

Field names below are the contract in docs/API_DESIGN.md (Shared objects), written out on
purpose so that a renamed field in the code fails a test.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from reelforge_api import jobs
from reelforge_api.script_rules import COSTS

from .support import Account, Browser, sign_up

SUMMARY_FIELDS = {
    "id",
    "title",
    "mode",
    "status",
    "targetSeconds",
    "durationSeconds",
    "thumbnailUrl",
    "progress",
    "updatedAt",
}
PROJECT_FIELDS = SUMMARY_FIELDS | {
    "topic",
    "tone",
    "style",
    "character",
    "voiceId",
    "musicMood",
    "music",
    "settings",
    "estimatedSeconds",
    "wordCount",
    "timelineVersion",
    "postCaption",
    "hashtags",
    "scenes",
    "activeJobs",
    "latestRender",
    "latestPost",
    "createdAt",
}
SCENE_FIELDS = {
    "id",
    "position",
    "narration",
    "onScreenText",
    "visualPrompt",
    "stockQuery",
    "mood",
    "visualSource",
    "voiceStatus",
    "visualStatus",
    "voice",
    "visual",
    "words",
    "wordsEdited",
    "trimStart",
    "trimEnd",
    "lastError",
    "estimatedSeconds",
}
JOB_FIELDS = {
    "id",
    "type",
    "projectId",
    "sceneId",
    "state",
    "progress",
    "provider",
    "creditsReserved",
    "creditsRefunded",
    "error",
    "output",
    "queuedAt",
    "finishedAt",
}
STATUSES = (
    "scripting",
    "draft",
    "generating",
    "needs_attention",
    "ready",
    "rendering",
    "render_failed",
    "rendered",
    "posting",
    "post_failed",
    "posted",
)
MOODS = (
    "calm",
    "joyful",
    "excited",
    "funny",
    "inspiring",
    "triumphant",
    "serious",
    "tense",
    "mysterious",
    "sad",
)
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SCRIPT_COST = COSTS["script"]
TOPIC = "3 morning habits that changed my life"


def run_jobs(app) -> int:
    """Runs every job waiting in the manual queue, as the worker would. Returns how many ran."""
    with app.app_context():
        return jobs.run_manual_queue()


def waiting_jobs() -> list[str]:
    return list(jobs.manual_queue)


@dataclass
class Creator:
    account: Account

    @property
    def browser(self) -> Browser:
        return self.account.browser

    @property
    def id(self) -> str:
        return self.account.user["id"]


def creator(app, **kwargs) -> Creator:
    return Creator(sign_up(app, **kwargs))


def start_project(browser: Browser, **body) -> tuple[dict, dict]:
    """B3 with a topic unless the body says otherwise. Returns (project, job); not yet run."""
    if "topic" not in body and "script" not in body:
        body["topic"] = TOPIC
    res = browser.post("/projects", body)
    assert res.status_code == 202, res.get_data(as_text=True)
    data = res.get_json()
    return data["project"], data["job"]


def get_project(browser: Browser, project_id: str) -> dict:
    res = browser.get(f"/projects/{project_id}")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()["project"]


def get_job(browser: Browser, job_id: str) -> dict:
    res = browser.get(f"/jobs/{job_id}")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()["job"]


def scripted_project(app, browser: Browser, **body) -> dict:
    """A project whose script has been written (status draft, scenes present)."""
    project, _ = start_project(browser, **body)
    run_jobs(app)
    project = get_project(browser, project["id"])
    assert project["status"] == "draft", project
    assert project["scenes"], project
    return project


def balance(browser: Browser) -> int:
    res = browser.get("/credits")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()["balance"]


def positions(scenes: list[dict]) -> list[int]:
    return [s["position"] for s in scenes]


def scene_ids(project: dict) -> list[str]:
    return [s["id"] for s in project["scenes"]]


# ---------------------------------------------------------------- straight to the database


def set_balance(database, user_id: str, amount: int) -> None:
    """Sets the balance together with an `adjust` ledger row, as an admin would, so the
    balance stays the sum of the ledger."""
    current = database.value("SELECT balance FROM credit_accounts WHERE user_id = :u", u=user_id)
    if amount != current:
        database.run(
            "INSERT INTO credit_ledger (user_id, amount, kind, note) VALUES (:u, :a, 'adjust', 'test')",
            u=user_id,
            a=amount - current,
        )
    database.run("UPDATE credit_accounts SET balance = :b WHERE user_id = :u", b=amount, u=user_id)


def stored_balance(database, user_id: str) -> int:
    return database.value("SELECT balance FROM credit_accounts WHERE user_id = :u", u=user_id)


def assert_ledger_matches_balance(database, user_id: str) -> None:
    """DB design 3.5: the balance is the cached sum of the ledger."""
    total = database.value("SELECT coalesce(sum(amount), 0) FROM credit_ledger WHERE user_id = :u", u=user_id)
    assert stored_balance(database, user_id) == total, "the balance is not the sum of the ledger"
    assert stored_balance(database, user_id) >= 0


def ledger_rows(database, user_id: str, kind: str | None = None) -> list[dict]:
    query = "SELECT amount, kind, job_id, note FROM credit_ledger WHERE user_id = :u"
    if kind:
        query += " AND kind = :k"
    return database.rows(query + " ORDER BY created_at, id", u=user_id, k=kind)


def job_ledger(database, job_id: str) -> list[tuple[str, int]]:
    return [
        (r["kind"], r["amount"])
        for r in database.rows(
            "SELECT kind, amount FROM credit_ledger WHERE job_id = :j ORDER BY created_at", j=job_id
        )
    ]


def set_statuses(
    database,
    project_id: str,
    *,
    voice: str | None = None,
    visual: str | None = None,
    status: str | None = None,
) -> None:
    """Pretends generation happened (milestone B4 builds it): scene and project statuses."""
    if voice:
        database.run("UPDATE scenes SET voice_status = :s WHERE project_id = :p", s=voice, p=project_id)
    if visual:
        database.run("UPDATE scenes SET visual_status = :s WHERE project_id = :p", s=visual, p=project_id)
    if status:
        database.run("UPDATE projects SET status = :s WHERE id = :p", s=status, p=project_id)


def scene_row(database, scene_id: str) -> dict:
    return database.one("SELECT * FROM scenes WHERE id = :s", s=scene_id)


def project_row(database, project_id: str) -> dict:
    return database.one("SELECT * FROM projects WHERE id = :p", p=project_id)


def new_asset(database, user_id: str, project_id: str, kind: str = "voice") -> str:
    asset_id = str(uuid.uuid4())
    database.run(
        "INSERT INTO assets (id, user_id, project_id, kind, s3_key, content_type, provider, "
        "duration_seconds) "
        "VALUES (:id, :u, :p, :k, :key, 'audio/mpeg', 'elevenlabs', 3.5)",
        id=asset_id,
        u=user_id,
        p=project_id,
        k=kind,
        key=f"test/{asset_id}",
    )
    return asset_id
