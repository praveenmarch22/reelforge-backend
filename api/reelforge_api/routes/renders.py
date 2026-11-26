"""Rendering (API design group E: E1 to E3; milestone B5)."""

from __future__ import annotations

from flask import Blueprint, jsonify
from sqlalchemy import select

from .. import jobs, projects, renders
from ..auth import current_user, require_user
from ..extensions import db
from ..idempotency import idempotent
from ..models import CreditAccount, Render

bp = Blueprint("renders", __name__)
HISTORY_MAX = 50


@bp.post("/projects/<project_id>/render")
@require_user(verified=True)
@idempotent
def start_render(project_id: str):
    """E1. Renders the Reel as it is now into an MP4. 2 credits, refunded if it fails."""
    project = projects.get_project(current_user().id, project_id, lock=True)
    render, job = renders.start_render(project)
    db.session.commit()
    answer = jsonify(
        render=renders.render_json(render, project),
        job=jobs.job_json(job, refunded=False),
        creditsReserved=job.credits_reserved,
        balance=db.session.get(CreditAccount, project.user_id).balance,
    )
    jobs.dispatch([job])
    return answer, 202


@bp.get("/renders/<render_id>")
@require_user()
def get_render(render_id: str):
    """E2. Polled while the render is queued or running."""
    render, project = renders.get_render(current_user().id, render_id)
    return jsonify(render=renders.render_json(render, project))


@bp.get("/projects/<project_id>/renders")
@require_user()
def list_renders(project_id: str):
    """E3. The Reel's renders, newest first."""
    project = projects.get_project(current_user().id, project_id)
    items = db.session.execute(
        select(Render)
        .where(Render.project_id == project.id)
        .order_by(Render.created_at.desc())
        .limit(HISTORY_MAX)
    ).scalars()
    return jsonify(items=[renders.render_json(r, project) for r in items])
