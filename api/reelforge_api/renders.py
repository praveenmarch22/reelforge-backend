"""Rendering the final MP4 (milestone B5; API E1 to E3, PRD 5.7 and 11.6).

E1 saves the timeline exactly as it is (storage keys, the version it was made from) with a new
render and starts a `render` job. The job hands that timeline to the render service with links
the service can download from, and upload links made for it it uploads the MP4 and its cover image to,
then polls the service for progress. A failed attempt is tried once more (R-1). Edits made
while it renders don't change it; the finished render is then `outdated` (R-3).

The project is `rendering` while its render runs, then `rendered`, or `render_failed` with the
credits refunded. If the Reel was edited in the meantime it goes back to `ready` or `draft`, the
status any edit gives it.
"""

from __future__ import annotations

import copy
import logging
import time
import uuid
from typing import Any

import requests
from celery.exceptions import SoftTimeLimitExceeded
from flask import current_app
from sqlalchemy import select, update

from . import credits, jobs, reel, timeline
from .auth import current_user
from .errors import ApiError
from .extensions import db
from .models import Asset, Job, Project, Render
from .projects import NOT_FOUND, settle_status
from .script_rules import COSTS
from .sessions import now
from .storage import get_storage

log = logging.getLogger(__name__)

ATTEMPTS = 2  # R-1: a failed render is tried once more before the job fails
POLL_SECONDS = 2
UNREACHABLE_POLLS = 15  # about 30 s without an answer from the service counts as a failed attempt
START_TRIES = 12  # a service that is restarting (503 while it bundles) is asked again for about 1 min
SETTLE_POLLS = 30  # after stopping an attempt, wait up to about 1 min for its upload to end (QB5-005)
LINK_SECONDS = 3 * 3600  # longer than the job's time limit
MIN_SECONDS = 3  # Instagram refuses Reels shorter than 3 s (QB5 Q3)


# ---------------------------------------------------------------- the render service


class HttpRenderService:
    """The Node service in `renderer/server.mjs` (API design group K)."""

    def __init__(self, settings):
        self.url = settings.render_service_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {settings.render_service_token}"}

    def _call(self, method: str, path: str, **kwargs) -> requests.Response:
        return requests.request(method, self.url + path, headers=self.headers, timeout=10, **kwargs)

    def start(self, service_id: str, timeline: dict, video: dict, cover: dict) -> None:
        answer = self._call(
            "POST",
            "/renders",
            json={
                "renderId": service_id,
                "timeline": timeline,
                "videoUploadUrl": video["url"],
                "thumbnailUploadUrl": cover["url"],
            },
        )
        answer.raise_for_status()

    def status(self, service_id: str) -> dict:
        answer = self._call("GET", f"/renders/{service_id}")
        if answer.status_code == 404:
            return {"state": "lost", "error": "the render service restarted"}
        answer.raise_for_status()
        return answer.json()

    def cancel(self, service_id: str) -> None:
        try:
            self._call("DELETE", f"/renders/{service_id}")
        except requests.RequestException:
            log.warning("could not stop a render at the render service", exc_info=True)


class MockRenderService:
    """Offline stand-in for tests and development: stores a tiny file in place of the MP4.
    `fail_next` makes that many attempts fail; `started` lists what was asked."""

    fail_next = 0
    started: list[dict] = []
    _states: dict[str, dict] = {}

    def __init__(self, settings):  # noqa: ARG002
        pass

    def start(self, service_id: str, timeline: dict, video: dict, cover: dict) -> None:
        MockRenderService.started.append({"id": service_id, "timeline": timeline})
        if MockRenderService.fail_next > 0:
            MockRenderService.fail_next -= 1
            self._states[service_id] = {"state": "failed", "progress": 40, "error": "mock failure"}
            return
        storage = get_storage()
        storage.put_bytes(video["key"], b"\x00\x00\x00\x18ftypmp42mock-render", "video/mp4")
        storage.put_bytes(cover["key"], b"\xff\xd8\xff\xe0mock-cover", "image/jpeg")
        seconds = round(timeline["durationInFrames"] / timeline["fps"], 2)
        self._states[service_id] = {"state": "succeeded", "progress": 100, "durationSeconds": seconds}

    def status(self, service_id: str) -> dict:
        return self._states.get(service_id, {"state": "lost"})

    def cancel(self, service_id: str) -> None:
        self._states.pop(service_id, None)


def render_service():
    settings = current_app.extensions["settings"]
    kind = MockRenderService if settings.render_service_url == "mock" else HttpRenderService
    return kind(settings)


# ---------------------------------------------------------------- starting (E1)


def _keys(snapshot: dict) -> list[str]:
    """Every stored file the timeline plays."""
    keys = [s[part]["src"] for s in snapshot["scenes"] for part in ("visual", "voice")]
    if snapshot.get("music"):
        keys.append(snapshot["music"]["src"])
    return [k for k in keys if k]


def start_render(project: Project) -> tuple[Render, Job]:
    """E1. The project must be locked. Caller commits, then dispatches.

    Refused in this order: 409 not_ready (with the scenes that aren't), 409 too_long and
    too_short (with the length), 409 already_running (this Reel is rendering or posting, or
    another of the user's is rendering: R-4), 402.
    """
    user = current_user()
    snapshot = reel.build(project, links=False)
    waiting = [s["id"] for s in snapshot["scenes"] if not s["ready"]]
    if project.status in ("scripting", "generating") or not snapshot["scenes"] or waiting:
        raise ApiError(
            409, "not_ready", "Some scenes aren't ready yet. Generate them first.", {"scenes": waiting}
        )
    seconds = timeline.seconds_of(snapshot)
    if timeline.too_long(snapshot):
        raise ApiError(
            409,
            "too_long",
            "This Reel is longer than 90 seconds. Shorten it to render.",
            {"seconds": seconds},
        )
    if seconds < MIN_SECONDS:
        raise ApiError(
            409,
            "too_short",
            "Instagram needs Reels of at least 3 seconds. Add to the script.",
            {"seconds": seconds},
        )
    if project.status in ("rendering", "posting"):
        raise ApiError(409, "already_running", "This Reel is already rendering.")

    account = credits.lock_account(user.id)  # also makes one user's render requests wait in turn
    rendering = db.session.execute(
        select(Job.id).where(Job.user_id == user.id, Job.type == "render", Job.state.in_(jobs.ACTIVE))
    ).first()
    if rendering is not None:
        raise ApiError(
            409, "already_running", "Another of your Reels is rendering. Try again when it's done."
        )
    render = Render(
        id=uuid.uuid4(),
        project_id=project.id,
        timeline_version=project.timeline_version,
        timeline=snapshot,
    )
    db.session.add(render)
    db.session.flush()
    job = jobs.new_job(
        user.id,
        "render",
        project_id=project.id,
        input={"renderId": str(render.id), "previousStatus": project.status},
        cost=COSTS["render"],
    )
    credits.reserve(account, [job])
    render.job_id = job.id
    project.status = "rendering"
    return render, job


# ---------------------------------------------------------------- the job type


def _with_links(snapshot: dict) -> dict:
    """The saved timeline with each storage key replaced by a link the render service can
    download from (at storage's internal address)."""
    storage = get_storage()
    built = copy.deepcopy(snapshot)
    for scene in built["scenes"]:
        for part in ("visual", "voice"):
            if scene[part]["src"]:
                scene[part]["src"] = storage.internal_get(scene[part]["src"], LINK_SECONDS)
    if built.get("music") and built["music"]["src"]:
        built["music"]["src"] = storage.internal_get(built["music"]["src"], LINK_SECONDS)
    return built


def _lock_render(render_id) -> Render | None:
    return db.session.execute(
        select(Render)
        .where(Render.id == render_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _report(job_id, render_id, progress: int) -> None:
    """Saves the progress (the service's 0 to 100 for this attempt) for the app to show, without
    locks. The job and the render show the same number; a retry starts again from 10 (QB5-009)."""
    overall = min(10 + round(0.88 * progress), 98)
    db.session.execute(update(Job).where(Job.id == job_id, Job.state == "running").values(progress=overall))
    db.session.execute(
        update(Render).where(Render.id == render_id, Render.state == "running").values(progress=overall)
    )
    db.session.commit()


def _cancelled(job_id) -> bool:
    state = db.session.execute(select(Job.state).where(Job.id == job_id)).scalar_one_or_none()
    db.session.commit()
    return state != "running"


def _attempt(service, service_id: str, job: Job, render_id, attempt: int, built: dict, video: dict,
             cover: dict) -> dict | None:  # fmt: skip
    """One try at the render service. The finished status, or None if this try failed.
    Returns {"state": "cancelled"} when the job was cancelled meanwhile."""
    settings = current_app.extensions["settings"]
    pause = 0 if settings.env == "test" else POLL_SECONDS
    # A service that is restarting answers 503 or refuses connections for a minute or so: it
    # is asked again rather than using up an attempt at once (QB5-004).
    for start_try in range(1, START_TRIES + 1):
        try:
            service.start(service_id, built, video, cover)
            break
        except requests.RequestException as e:
            answer = getattr(e, "response", None)
            # Only "not now" is asked again (refused, timed out, 429, 5xx); a 400 or 401 won't
            # change by asking again (QB5-014).
            permanent = answer is not None and 400 <= answer.status_code < 500 and answer.status_code != 429
            if permanent or start_try == START_TRIES:
                log.warning(
                    "the render service did not take a render",
                    extra={
                        "job_id": str(job.id),
                        "status": answer.status_code if answer is not None else None,
                    },
                )
                service.cancel(service_id)  # in case it was taken after all and the answer was lost
                return None
            time.sleep(pause * 2.5)
            if _cancelled(job.id):
                _stop(service, service_id, pause)
                return {"state": "cancelled"}
    unreachable = 0
    try:
        while True:
            time.sleep(pause)
            if _cancelled(job.id):
                _stop(service, service_id, pause)
                return {"state": "cancelled"}
            try:
                status = service.status(service_id)
                unreachable = 0
            except (requests.RequestException, ValueError):
                unreachable += 1
                if unreachable >= UNREACHABLE_POLLS:
                    log.warning("the render service stopped answering", extra={"job_id": str(job.id)})
                    service.cancel(service_id)  # if it comes back, this attempt must not go on (QB5-003)
                    return None
                continue
            if status["state"] == "succeeded":
                return status
            if status["state"] in ("failed", "lost", "cancelled"):
                log.warning(
                    "a render attempt failed",
                    extra={"job_id": str(job.id), "attempt": attempt, "error": status.get("error")},
                )
                return None
            if status["state"] == "running":  # waiting behind another render shows no progress
                _report(job.id, render_id, int(status.get("progress") or 0))
    except SoftTimeLimitExceeded:
        service.cancel(service_id)  # out of time: the job fails as timed_out
        raise


def _stop(service, service_id: str, pause: float) -> None:
    """Stops an attempt and waits (about 1 min at most) until the service has finished with it,
    so an upload already under way is over before its files are deleted (QB5-005)."""
    service.cancel(service_id)
    for _ in range(SETTLE_POLLS):
        try:
            if service.status(service_id).get("state") not in ("queued", "running", "cancelling"):
                return
        except (requests.RequestException, ValueError):
            return
        time.sleep(pause)


class RenderJob:
    @staticmethod
    def on_start(job: Job) -> None:
        db.session.execute(
            update(Render)
            .where(Render.id == uuid.UUID(job.input["renderId"]), Render.state == "queued")
            .values(state="running")
        )

    @staticmethod
    def work(job: Job):
        render = db.session.get(Render, uuid.UUID(job.input["renderId"]))
        if render is None:
            raise jobs.JobFailed("internal_error")
        render_id, snapshot = render.id, render.timeline
        db.session.commit()  # no transaction stays open while the video is made

        storage = get_storage()
        # R-2: every file is checked before the render starts, and each link is made fresh.
        if not all(storage.exists(key) for key in _keys(snapshot)):
            raise jobs.JobFailed("media_missing")
        built = _with_links(snapshot)
        folder = f"users/{job.user_id}/projects/{job.project_id}/renders/{render_id}"
        made: dict = {"keys": []}

        service = render_service()
        try:
            return RenderJob._attempts(job, service, storage, built, snapshot, folder, render_id, made)
        except SoftTimeLimitExceeded:
            RenderJob.discard(job, made)  # out of time: nothing it made is kept (QB5-016)
            raise

    @staticmethod
    def _attempts(job, service, storage, built, snapshot, folder, render_id, made):
        soft_limit = jobs.task_limits("render")[0]
        started = time.monotonic()
        for attempt in range(1, ATTEMPTS + 1):
            # R-1's second try only starts if it can finish: the first one's duration (plus a
            # tenth) must still fit in the time left (QB5-001, QB5-017). A render that failed
            # late fails now as render_failed rather than later as timed_out.
            used = time.monotonic() - started
            if attempt > 1 and used * 2.1 > soft_limit:
                break
            # Each attempt uploads to its own files, so a late upload from an earlier one can
            # never replace the result (QB5-003).
            video = {"key": f"{folder}-{attempt}.mp4"}
            cover = {"key": f"{folder}-{attempt}.jpeg"}
            video["url"] = storage.internal_put(video["key"], "video/mp4", LINK_SECONDS)
            cover["url"] = storage.internal_put(cover["key"], "image/jpeg", LINK_SECONDS)
            made["keys"] += [video["key"], cover["key"]]
            status = _attempt(service, f"{render_id}-{attempt}", job, render_id, attempt, built, video, cover)
            if status is None:
                continue
            if status["state"] == "cancelled":
                return made, "remotion"  # the job is no longer running: run() discards the files
            made["videoKey"], made["coverKey"] = video["key"], cover["key"]
            made["videoBytes"] = storage.size(video["key"])
            made["coverBytes"] = storage.size(cover["key"])
            made["durationSeconds"] = status.get("durationSeconds") or timeline.seconds_of(snapshot)
            # The service may render smaller than the timeline (RENDER_SCALE on a developer's machine).
            made["width"] = status.get("width") or timeline.WIDTH
            made["height"] = status.get("height") or timeline.HEIGHT
            return made, "remotion"
        RenderJob.discard(job, made)  # what failed attempts may have uploaded
        raise jobs.JobFailed("render_failed")

    @staticmethod
    def apply(job: Job, made: dict) -> dict:
        render = _lock_render(uuid.UUID(job.input["renderId"]))
        if render is None:
            raise jobs.JobFailed("internal_error")
        common = {"user_id": job.user_id, "project_id": job.project_id, "provider": "remotion"}
        video = Asset(
            **common,
            kind="render",
            s3_key=made["videoKey"],
            content_type="video/mp4",
            size_bytes=made["videoBytes"],
            duration_seconds=made["durationSeconds"],
            width=made["width"],
            height=made["height"],
        )
        cover = Asset(
            **common,
            kind="thumbnail",
            s3_key=made["coverKey"],
            content_type="image/jpeg",
            size_bytes=made["coverBytes"],
            width=made["width"],
            height=made["height"],
        )
        db.session.add_all([video, cover])
        db.session.flush()
        # Files of an earlier attempt that failed (its upload may have ended late) are removed.
        RenderJob.discard(
            job, {"keys": [k for k in made["keys"] if k not in (made["videoKey"], made["coverKey"])]}
        )
        render.asset_id, render.thumbnail_asset_id = video.id, cover.id
        render.state, render.progress, render.finished_at = "succeeded", 100, now()
        project = db.session.get(Project, job.project_id)
        if project is not None:
            project.thumbnail_asset_id = cover.id
            if project.status == "rendering":
                if project.timeline_version == render.timeline_version:
                    project.status = "rendered"
                else:
                    settle_status(project)  # edited while rendering: the render is outdated (R-3)
        return {"renderId": str(render.id), "videoAssetId": str(video.id)}

    @staticmethod
    def discard(job: Job | None, made: dict) -> None:  # noqa: ARG004
        storage = get_storage()
        for key in made.get("keys", []):
            try:
                storage.delete(key)
            except Exception:  # noqa: BLE001
                log.warning("could not delete an unused render file", exc_info=True)

    @staticmethod
    def on_stop(job: Job) -> None:
        render = _lock_render(uuid.UUID(job.input["renderId"]))
        if render is not None and render.state in jobs.ACTIVE:
            render.state = "failed" if job.state == "failed" else "cancelled"
            render.error_message = job.error_message if job.state == "failed" else None
            render.finished_at = now()
        project = db.session.get(Project, job.project_id)
        if project is None or project.status != "rendering":
            return
        unchanged = render is not None and project.timeline_version == render.timeline_version
        previous = job.input.get("previousStatus")
        if job.state == "failed" and unchanged:
            project.status = "render_failed"
        elif unchanged and previous in ("ready", "rendered", "render_failed", "post_failed", "posted"):
            project.status = previous  # cancelled: the Reel is as it was before (QB5-007)
        else:
            settle_status(project)


# ---------------------------------------------------------------- JSON


def render_json(render: Render, project: Project) -> dict[str, Any]:
    storage = get_storage()
    video = render.asset
    error = None
    if render.state == "failed":
        job = db.session.get(Job, render.job_id) if render.job_id else None
        code = job.error_code if job is not None and job.error_code else "render_failed"
        error = {"code": code, "message": render.error_message or jobs.MESSAGES["render_failed"]}
    return {
        "id": str(render.id),
        "projectId": str(render.project_id),
        "jobId": str(render.job_id) if render.job_id else None,
        "state": render.state,
        "progress": render.progress,
        "timelineVersion": render.timeline_version,
        "outdated": render.timeline_version < project.timeline_version,
        "video": None
        if video is None
        else {
            "url": storage.presigned_get(video.s3_key),
            "downloadUrl": storage.presigned_get(video.s3_key, download_name=_file_name(project)),
            "durationSeconds": video.duration_seconds,
            "sizeBytes": video.size_bytes,
        },
        "thumbnailUrl": storage.presigned_get(render.thumbnail_asset.s3_key)
        if render.thumbnail_asset
        else None,
        "error": error,
        "createdAt": _iso(render.created_at),
        "finishedAt": _iso(render.finished_at),
    }


def _file_name(project: Project) -> str:
    safe = "".join(ch if ch.isascii() and (ch.isalnum() or ch in " -_") else " " for ch in project.title)
    return ("-".join(safe.split())[:60] or "reel") + ".mp4"


def _iso(value) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def latest_render(project: Project) -> Render | None:
    return db.session.execute(
        select(Render).where(Render.project_id == project.id).order_by(Render.created_at.desc()).limit(1)
    ).scalar_one_or_none()


def get_render(user_id, render_id: str) -> tuple[Render, Project]:
    """The user's render, or 404 for anything missing, of a deleted project or someone else's."""
    try:
        wanted = uuid.UUID(render_id)
    except (ValueError, TypeError):
        raise ApiError(404, "not_found", NOT_FOUND) from None
    found = db.session.execute(
        select(Render, Project)
        .join(Project, Project.id == Render.project_id)
        .where(Render.id == wanted, Project.user_id == user_id, Project.deleted_at.is_(None))
    ).first()
    if found is None:
        raise ApiError(404, "not_found", NOT_FOUND)
    return found[0], found[1]
