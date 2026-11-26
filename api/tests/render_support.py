"""Shared by the rendering tests (milestone B5): a ready Reel, starting a render, and the
Render object's contract (docs/API_DESIGN.md, Shared objects: Render).

Field names are written out on purpose, so a renamed field in the code fails a test.
"""

from __future__ import annotations

import threading

from reelforge_api.renders import MockRenderService
from reelforge_api.script_rules import COSTS

from .projects_support import get_project, run_jobs, scripted_project
from .support import Browser, log_in

RENDER_FIELDS = {
    "id",
    "projectId",
    "jobId",
    "state",
    "progress",
    "timelineVersion",
    "outdated",
    "video",
    "thumbnailUrl",
    "error",
    "createdAt",
    "finishedAt",
}
VIDEO_FIELDS = {"url", "downloadUrl", "durationSeconds", "sizeBytes"}
RENDER_STATES = ("queued", "running", "succeeded", "failed", "cancelled")
RENDER_COST = COSTS["render"]


def ready_project(app, browser: Browser, **body) -> dict:
    """A scripted project after Generate has made every voice and visual (status ready)."""
    project = scripted_project(app, browser, **body)
    res = browser.post(f"/projects/{project['id']}/generate")
    assert res.status_code == 202, res.get_data(as_text=True)
    run_jobs(app)
    project = get_project(browser, project["id"])
    assert project["status"] == "ready", project["status"]
    return project


def start_render(browser: Browser, project_id: str, *, key: str | None = None, body=None):
    headers = {"Idempotency-Key": key} if key is not None else None
    return browser.post(f"/projects/{project_id}/render", body, headers=headers)


def started(browser: Browser, project_id: str) -> dict:
    """E1 that must succeed; returns the 202 body."""
    res = start_render(browser, project_id)
    assert res.status_code == 202, res.get_data(as_text=True)
    return res.get_json()


def get_render(browser: Browser, render_id: str) -> dict:
    res = browser.get(f"/renders/{render_id}")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()["render"]


def history(browser: Browser, project_id: str) -> list[dict]:
    res = browser.get(f"/projects/{project_id}/renders")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()["items"]


def listed(browser: Browser, project_id: str) -> dict:
    """The project's summary in B2."""
    res = browser.get("/projects")
    assert res.status_code == 200, res.get_data(as_text=True)
    return next(p for p in res.get_json()["items"] if p["id"] == project_id)


def render_keys(user_id: str, project_id: str, render_id: str, attempt: int = 1) -> tuple[str, str]:
    """Where the worker asks for one attempt's MP4 and cover: each attempt has its own files."""
    folder = f"users/{user_id}/projects/{project_id}/renders/{render_id}-{attempt}"
    return f"{folder}.mp4", f"{folder}.jpeg"


def in_another_thread(fn, *args, **kwargs):
    """Runs `fn` in its own thread (its own app context and database session), as a second
    request arriving while the worker is busy would, and returns its result."""
    box: dict = {}

    def target():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 - re-raised in the caller's thread
            box["error"] = e

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout=60)
    assert not thread.is_alive(), "the request did not finish"
    if "error" in box:
        raise box["error"]
    return box["value"]


def second_browser(app, account) -> Browser:
    return log_in(app, account)


def reset_mock() -> None:
    MockRenderService.fail_next = 0
    MockRenderService.started.clear()
    MockRenderService._states.clear()
