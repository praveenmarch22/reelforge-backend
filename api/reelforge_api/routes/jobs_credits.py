"""Jobs and credits (API design groups G and H)."""

from __future__ import annotations

import uuid

from flask import Blueprint, jsonify, request
from sqlalchemy import and_, or_, select

from .. import jobs, projects
from ..auth import current_user, require_user
from ..errors import ApiError
from ..extensions import db
from ..models import CreditAccount, CreditLedger, Job
from ..project_schemas import decode_cursor, encode_cursor, read_limit
from ..script_rules import COSTS

bp = Blueprint("jobs_credits", __name__)


def _iso(value) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _get_job(job_id: str, *, lock: bool = False) -> Job:
    try:
        key = uuid.UUID(job_id)
    except ValueError:
        raise ApiError(404, "not_found", projects.NOT_FOUND) from None
    query = select(Job).where(Job.id == key, Job.user_id == current_user().id)
    if lock:
        query = query.with_for_update()
    job = db.session.execute(query).scalar_one_or_none()
    if job is None:
        raise ApiError(404, "not_found", projects.NOT_FOUND)
    return job


@bp.get("/jobs/<job_id>")
@require_user()
def get_job(job_id: str):
    """G1."""
    return jsonify(job=jobs.job_json(_get_job(job_id)))


@bp.get("/projects/<project_id>/jobs")
@require_user()
def project_jobs(project_id: str):
    """G2. Newest first; `active=true` lists only queued and running jobs."""
    project = projects.get_project(current_user().id, project_id)
    limit = read_limit(request.args.get("limit"))
    cursor = decode_cursor(request.args.get("cursor"))
    active = request.args.get("active")
    if active not in (None, "true", "false"):
        raise ApiError(
            400,
            "validation_error",
            "Some details need another look.",
            {"fields": {"active": "Use true or false"}},
        )
    query = select(Job).where(Job.project_id == project.id)
    if active == "true":
        query = query.where(Job.state.in_(jobs.ACTIVE))
    if cursor:
        moment, last_id = cursor
        query = query.where(or_(Job.queued_at < moment, and_(Job.queued_at == moment, Job.id < last_id)))
    rows = list(
        db.session.execute(query.order_by(Job.queued_at.desc(), Job.id.desc()).limit(limit + 1)).scalars()
    )
    page = rows[:limit]
    return jsonify(
        items=jobs.jobs_json(page),
        nextCursor=encode_cursor(page[-1].queued_at, page[-1].id) if len(rows) > limit else None,
    )


@bp.post("/jobs/<job_id>/cancel")
@require_user()
def cancel_job(job_id: str):
    """G3. Stops a queued or running job and refunds its credits."""
    # The project is locked before the job, the order the worker uses (QB4-015).
    job = jobs.lock(_get_job(job_id).id)
    if job is None or job.state not in jobs.ACTIVE:
        raise ApiError(409, "job_finished", "This has already finished.")
    jobs.cancel(job)
    db.session.commit()
    return jsonify(job=jobs.job_json(job))


@bp.get("/credits")
@require_user()
def get_credits():
    """H1. The web app shows costs from here and never hard-codes them."""
    account = db.session.get(CreditAccount, current_user().id)
    return jsonify(
        balance=account.balance,
        monthlyAllowance=account.monthly_allowance,
        resetsAt=_iso(account.resets_at),
        costs=COSTS,
    )


@bp.get("/credits/ledger")
@require_user()
def ledger():
    """H2. Every change to the balance, newest first."""
    user = current_user()
    limit = read_limit(request.args.get("limit"))
    cursor = decode_cursor(request.args.get("cursor"))
    query = (
        select(CreditLedger, Job.project_id)
        .outerjoin(Job, Job.id == CreditLedger.job_id)
        .where(CreditLedger.user_id == user.id)
    )
    if cursor:
        moment, last_id = cursor
        query = query.where(
            or_(
                CreditLedger.created_at < moment,
                and_(CreditLedger.created_at == moment, CreditLedger.id < last_id),
            )
        )
    rows = db.session.execute(
        query.order_by(CreditLedger.created_at.desc(), CreditLedger.id.desc()).limit(limit + 1)
    ).all()
    page = rows[:limit]
    return jsonify(
        items=[
            {
                "id": str(entry.id),
                "amount": entry.amount,
                "kind": entry.kind,
                "note": entry.note,
                "jobId": str(entry.job_id) if entry.job_id else None,
                "projectId": str(project_id) if project_id else None,
                "createdAt": _iso(entry.created_at),
            }
            for entry, project_id in page
        ],
        nextCursor=encode_cursor(page[-1][0].created_at, page[-1][0].id) if len(rows) > limit else None,
    )
