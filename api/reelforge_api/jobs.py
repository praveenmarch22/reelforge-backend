"""Background jobs: creating, starting, running, failing and cancelling them (technical design 7).

The life of a job:
  1. A request creates it (state queued) and reserves its credits in the same transaction.
  2. After the commit it is handed to the worker (or, in tests, kept for a test to run).
  3. The worker marks it running and does the slow work (a provider call) without holding
     any database lock.
  4. It then locks the job again. If the job was cancelled in the meantime the result is
     thrown away; otherwise the result is saved and the job succeeds, in one transaction.
  5. Any failure marks it failed with a plain-language message and refunds its credits.

What each job type does is in `job_types.py`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from flask import current_app
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from . import credits
from .errors import ApiError
from .extensions import db
from .models import Job, Project
from .providers.base import AllProvidersFailed, ProviderError
from .sessions import now

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
QUEUES = {
    "ideas": "llm", "script": "llm", "rewrite_scene": "llm", "hooks": "llm", "caption": "llm",
    "voice": "voice", "visual": "visuals", "render": "render", "publish": "publish",
}  # fmt: skip
# A job running longer than this is treated as stuck and failed (task design rule 3).
TIME_LIMITS = {
    "llm": timedelta(minutes=5), "voice": timedelta(minutes=7), "visuals": timedelta(minutes=7),
    "publish": timedelta(minutes=22),
}  # fmt: skip
# Celery's (soft, hard) limits in seconds per queue. A voice job may try ElevenLabs three
# times and then Edge three times, with waits for rate limits, before it gives up.
# Renders have their own worker that takes one at a time, so a render's clock starts when the
# service starts drawing it. A 90 s Reel takes about 30 minutes on a developer's machine at
# 720x1280 (QB5-001). The limits stay under Redis's 1-hour redelivery timeout.
TASK_LIMITS = {
    "llm": (240, 300),
    "voice": (360, 420),
    "visuals": (360, 420),
    "publish": (1200, 1260),
}  # render: task_limits()


def task_limits(queue: str) -> tuple[int, int]:
    """Celery's (soft, hard) limits for a queue; a render's come from the settings."""
    if queue == "render":
        soft = current_app.extensions["settings"].render_time_limit_seconds
        return soft, soft + 60
    return TASK_LIMITS.get(queue, TASK_LIMITS["llm"])


# A job still queued after this long means no worker is taking it. Renders wait in turn for the
# one render worker, so theirs is longer (QB5-002).
QUEUED_LIMIT = timedelta(minutes=30)
QUEUED_LIMITS = {"render": timedelta(hours=3)}

# Filled when job dispatch is "manual" (tests): job ids waiting to be run by run_manual_queue().
manual_queue: list[str] = []

MESSAGES = {
    "content_refused": "This was refused by the AI's content rules. Try a different wording.",
    "provider_unavailable": "The AI service is not available right now. Please try again later.",
    "provider_failed": "The AI service didn't answer properly. Please try again.",
    "scene_changed": "The scene was edited while it was being rewritten, so the rewrite was not applied.",
    "timed_out": "This took too long and was stopped. Please try again.",
    "stock_unavailable": "That clip is no longer available. Pick another one.",
    "no_stock_match": "No stock clip fits this scene. Try other search words, or switch it to an AI image.",
    "media_missing": "A file this Reel uses is missing. Generate the scenes again, then render.",
    "render_failed": "The video couldn't be made. Please try again.",
    "not_connected": "Connect your YouTube channel first.",
    "reconnect_required": "Your YouTube connection has expired. Connect YouTube again, then try again.",
    "quota_exceeded": "YouTube's daily upload limit is reached. Try again tomorrow.",
    "rejected": "YouTube couldn't process this video. Render it again and try once more.",
    "upload_failed": "The upload to YouTube didn't go through. Please try again.",
    "invalid_metadata": (
        "YouTube didn't accept the title, description or hashtags. Change them and try again."
    ),
    "queue_unavailable": "We couldn't start this. Please try again in a moment.",
    "internal_error": "Something went wrong on our side. Please try again.",
}


class JobFailed(Exception):
    """Raised by a job type's work or apply step to fail the job with a documented code."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(code)
        self.code = code
        self.message = message or MESSAGES[code]


# ---------------------------------------------------------------- JSON


def _iso(value) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def job_json(job: Job, refunded: bool | None = None) -> dict[str, Any]:
    if refunded is None:
        refunded = job.id in credits.was_refunded([job.id])
    return {
        "id": str(job.id),
        "type": job.type,
        "projectId": str(job.project_id) if job.project_id else None,
        "sceneId": str(job.scene_id) if job.scene_id else None,
        "state": job.state,
        "progress": job.progress,
        "provider": job.provider,
        "creditsReserved": job.credits_reserved,
        "creditsRefunded": refunded,
        "error": {"code": job.error_code, "message": job.error_message} if job.error_code else None,
        "output": job.output,
        "queuedAt": _iso(job.queued_at),
        "finishedAt": _iso(job.finished_at),
    }


def jobs_json(jobs: list[Job]) -> list[dict[str, Any]]:
    refunded = credits.was_refunded([job.id for job in jobs])
    return [job_json(job, job.id in refunded) for job in jobs]


# ---------------------------------------------------------------- creating and starting


def new_job(user_id, job_type: str, *, project_id=None, scene_id=None, input: dict | None = None,
            cost: int = 0) -> Job:  # fmt: skip
    """Adds a queued job (not yet committed). Raises 409 already_running if the same work is
    already active (one active job per scene and type, and one script job per project: G-12)."""
    job = Job(
        user_id=user_id,
        project_id=project_id,
        scene_id=scene_id,
        type=job_type,
        credits_reserved=cost,
        input=input or {},
    )
    try:
        with db.session.begin_nested():
            db.session.add(job)
            db.session.flush()
    except IntegrityError as e:  # the partial unique indexes on jobs
        if "jobs_one_active" in str(e.orig):
            raise ApiError(409, "already_running", "This is already in progress.") from None
        raise
    return job


def dispatch(jobs: list[Job]) -> None:
    """Hands committed jobs to the worker. Call after the commit that created them."""
    settings = current_app.extensions["settings"]
    for job in jobs:
        if settings.jobs_dispatch == "manual":
            manual_queue.append(str(job.id))
            continue
        from .tasks.jobs import run_job

        try:
            queue = QUEUES.get(job.type, "default")
            soft, hard = task_limits(queue)
            result = run_job.apply_async(
                args=[str(job.id)], queue=queue, soft_time_limit=soft, time_limit=hard
            )
        except Exception:  # noqa: BLE001 - the queue (Redis) is unreachable
            log.exception("could not queue job", extra={"job_id": str(job.id), "type": job.type})
            fail(job.id, JobFailed("queue_unavailable"))
            continue
        db.session.execute(
            text("UPDATE jobs SET celery_task_id = :task WHERE id = :id AND celery_task_id IS NULL"),
            {"task": result.id, "id": job.id},
        )
        db.session.commit()


def run_manual_queue() -> int:
    """Tests: runs every job waiting in the manual queue, in order. Returns how many ran."""
    count = 0
    while manual_queue:
        run(manual_queue.pop(0))
        count += 1
    return count


# ---------------------------------------------------------------- running


def _lock(job_id) -> Job | None:
    """Locks the job's project first, then the job: the same order web requests use (they
    lock the project, then its scenes and jobs), so the two can never deadlock (QB4-009)."""
    project_id = db.session.execute(select(Job.project_id).where(Job.id == job_id)).scalar_one_or_none()
    if project_id is not None:
        db.session.execute(
            select(Project.id).where(Project.id == project_id).with_for_update()
        ).scalar_one_or_none()
    # populate_existing: a job already in memory must be re-read after the lock, or a cancel
    # made by another request in the meantime would go unseen (QB3-002).
    return db.session.execute(
        select(Job).where(Job.id == job_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()


def run(job_id: str) -> None:
    """The worker's entry point for one job."""
    from .job_types import TYPES

    job_id = uuid.UUID(str(job_id))
    job = _lock(job_id)
    if job is None or job.state != "queued":
        db.session.rollback()  # cancelled, or already taken by another delivery of the task
        return
    job.state, job.started_at, job.progress = "running", now(), 10
    job.attempts += 1
    kind = TYPES[job.type]
    if hasattr(kind, "on_start"):
        kind.on_start(job)
    db.session.commit()

    try:
        result, provider = kind.work(job)
    except JobFailed as e:
        fail(job_id, e)
        return
    except SoftTimeLimitExceeded:
        fail(job_id, JobFailed("timed_out"))
        return
    except ProviderError as e:
        fail(job_id, _provider_failure(e), provider_error=True)
        return
    except Exception:  # noqa: BLE001
        log.exception("job failed unexpectedly", extra={"job_id": str(job_id), "type": job.type})
        db.session.rollback()
        fail(job_id, JobFailed("internal_error"))
        return

    try:
        job = _lock(job_id)
    except Exception:  # noqa: BLE001 - for example a deadlock victim: never leave it running
        log.exception("could not lock a finished job", extra={"job_id": str(job_id)})
        db.session.rollback()
        _discard(kind, None, result)
        fail(job_id, JobFailed("internal_error"))
        return
    if job is None or job.state != "running":
        db.session.rollback()  # cancelled while the provider was working: the result is dropped
        _discard(kind, job, result)
        return
    try:
        output = kind.apply(job, result)
    except JobFailed as e:
        db.session.rollback()
        _discard(kind, job, result)
        fail(job_id, e)
        return
    except Exception:  # noqa: BLE001 - never leave a job running because saving failed
        log.exception("saving a job's result failed", extra={"job_id": str(job_id), "type": job.type})
        db.session.rollback()
        _discard(kind, job, result)
        fail(job_id, JobFailed("internal_error"))
        return
    job.state, job.progress, job.finished_at = "succeeded", 100, now()
    job.provider, job.output = provider, output
    db.session.commit()
    record_usage(job.type, provider, failed=False, credits_charged=job.credits_reserved)


def lock(job_id) -> Job | None:
    """Locks a job, and its project first (see _lock). For requests that change a job."""
    return _lock(job_id)


def _discard(kind, job: Job | None, result) -> None:
    """Removes what a job made but didn't keep (for example a stored file)."""
    if hasattr(kind, "discard"):
        kind.discard(job, result)


def _provider_failure(e: ProviderError) -> JobFailed:
    if e.refused:
        return JobFailed("content_refused")
    if isinstance(e, AllProvidersFailed) and e.none_available:
        return JobFailed("provider_unavailable")
    return JobFailed("provider_failed")


def fail(job_id, error: JobFailed, *, provider_error: bool = False) -> None:
    """Marks a job failed (if it is still active), refunds it and undoes its effect on the
    project. Safe to call for a job that already finished: then it does nothing."""

    db.session.rollback()
    job = _lock(job_id)
    if job is None or job.state not in ACTIVE:
        db.session.rollback()
        return
    job.state, job.finished_at = "failed", now()
    job.error_code, job.error_message = error.code, error.message
    credits.refund(job)
    _on_stop(job)
    db.session.commit()
    log.info("job failed", extra={"job_id": str(job.id), "type": job.type, "code": error.code})
    if provider_error:
        record_usage(job.type, None, failed=True, credits_charged=0)


def _on_stop(job: Job) -> None:
    """The job type's clean-up, if it has one (job types of later milestones add theirs)."""
    from .job_types import TYPES

    kind = TYPES.get(job.type)
    if kind is not None:
        kind.on_stop(job)


def cancel(job: Job) -> None:
    """G3, and when a project or scene is deleted. The job must be locked. Caller commits."""

    if job.state not in ACTIVE:
        return
    job.state, job.finished_at = "cancelled", now()
    credits.refund(job)
    _on_stop(job)
    if job.celery_task_id:
        try:
            # Not terminated: a running task finds the job cancelled and drops its result.
            current_app.extensions["celery"].control.revoke(job.celery_task_id)
        except Exception:  # noqa: BLE001
            log.warning("could not revoke task", extra={"job_id": str(job.id)}, exc_info=True)


def cancel_active(*, project_id=None, scene_ids=None, user_id=None, job_type=None) -> None:
    """Cancels and refunds every active job of a project or of some scenes (optionally of
    one type). Caller commits."""
    query = (
        select(Job).where(Job.state.in_(ACTIVE)).with_for_update().execution_options(populate_existing=True)
    )
    if project_id is not None:
        query = query.where(Job.project_id == project_id)
    if scene_ids is not None:
        query = query.where(Job.scene_id.in_(list(scene_ids)))
    if user_id is not None:
        query = query.where(Job.user_id == user_id)
    if job_type is not None:
        query = query.where(Job.type == job_type)
    for job in db.session.execute(query).scalars():
        cancel(job)


def fail_stuck() -> int:
    """Scheduled every 5 minutes: jobs past their time limit fail and are refunded (G-11)."""
    stuck: list[uuid.UUID] = []
    for job in db.session.execute(select(Job).where(Job.state.in_(ACTIVE))).scalars():
        if job.state == "running" and job.started_at:
            queue = QUEUES.get(job.type, "default")
            if queue == "render":
                limit = timedelta(seconds=task_limits("render")[1] + 60)
            else:
                limit = TIME_LIMITS.get(queue, timedelta(minutes=15))
            if now() - job.started_at > limit + timedelta(minutes=1):
                stuck.append(job.id)
        elif job.state == "queued" and now() - job.queued_at > QUEUED_LIMITS.get(job.type, QUEUED_LIMIT):
            stuck.append(job.id)
    db.session.rollback()
    for job_id in stuck:
        fail(job_id, JobFailed("timed_out"))
    if stuck:
        log.warning("stuck jobs failed", extra={"count": len(stuck)})
    return len(stuck)


def record_usage(job_type: str, provider: str | None, *, failed: bool, credits_charged: int) -> None:
    """Counts provider calls per day for the admin usage page (AD1). Never fails a job."""
    try:
        db.session.execute(
            text(
                "INSERT INTO usage_daily (day, provider, job_type, calls, failures, credits_charged) "
                "VALUES (current_date, :provider, :type, 1, :failures, :credits) "
                "ON CONFLICT (day, provider, job_type) DO UPDATE SET calls = usage_daily.calls + 1, "
                "failures = usage_daily.failures + EXCLUDED.failures, "
                "credits_charged = usage_daily.credits_charged + EXCLUDED.credits_charged"
            ),
            {
                "provider": provider or "none",
                "type": job_type,
                "failures": int(failed),
                "credits": credits_charged,
            },
        )
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()
        log.warning("could not record usage", exc_info=True)
