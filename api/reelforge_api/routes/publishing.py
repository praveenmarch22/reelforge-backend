"""Publishing (API design group F, platform-neutral; milestone B6, YouTube first):
connections F1 to F3, the AI post text F4, publishing F5 and posts F6."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, redirect, request

from .. import connections, jobs, projects, publishing, renders
from ..auth import current_user, require_user
from ..errors import ApiError
from ..extensions import db
from ..idempotency import idempotent
from ..models import Project
from ..project_schemas import PublishBody
from ..schemas import read_body
from ..sessions import signed_in_user_id
from .projects import refuse_while_scripting, start_llm_job

bp = Blueprint("publishing", __name__)


@bp.get("/connections/<platform>/connect")
@require_user(verified=True)
def connect(platform: str):
    """F1. Google's sign-in address; the web app sends the browser there."""
    return jsonify(
        authorizeUrl=connections.connect_url(current_user(), platform, request.args.get("returnTo"))
    )


@bp.get("/connections/<platform>/callback")
def callback(platform: str):
    """F2. Where Google sends the browser back. Always redirects to the web app."""
    signed_in = signed_in_user_id()
    target = connections.callback(platform, request.args, str(signed_in) if signed_in else None)
    # Absolute, to the web app's own address: in production the API has another host (QB6-006).
    web = current_app.extensions["settings"].web_origin.rstrip("/")
    answer = redirect(web + target, code=302)
    answer.headers["Cache-Control"] = "no-store"
    answer.headers["Referrer-Policy"] = "no-referrer"  # the code must not leak to the next page
    return answer


@bp.delete("/connections/<platform>")
@require_user()
def disconnect(platform: str):
    """F3. Removes the connection; posts are kept."""
    connections.disconnect(current_user(), platform)
    return "", 204


@bp.post("/projects/<project_id>/caption/generate")
@require_user(verified=True)
def generate_caption(project_id: str):
    """F4. A title, caption and hashtags written by the language model. Free."""
    project = projects.get_project(current_user().id, project_id)
    refuse_while_scripting(project)
    if not project.scenes:
        raise ApiError(409, "no_script", "Write the script first.")
    script = " ".join(s.narration for s in project.scenes)[:4000]
    job = start_llm_job(
        "caption",
        project=project,
        input={"title": project.title, "topic": project.topic or project.title, "script": script},
    )
    db.session.commit()
    answer = jsonify(job=jobs.job_json(job, False))
    jobs.dispatch([job])
    return answer, 202


@bp.post("/renders/<render_id>/publish")
@require_user(verified=True)
@idempotent
def publish(render_id: str):
    """F5. Publishes a finished render. Free."""
    body = read_body(PublishBody)
    render, _ = renders.get_render(current_user().id, render_id)
    project = projects.get_project(current_user().id, str(render.project_id), lock=True)
    post, job = publishing.start_publish(render, project, body)
    db.session.commit()
    answer = jsonify(post=publishing.post_json(post), job=jobs.job_json(job, False))
    jobs.dispatch([job])
    return answer, 202


@bp.get("/posts/<post_id>")
@require_user()
def get_post(post_id: str):
    """F6. Polled while publishing."""
    return jsonify(post=publishing.post_json(publishing.get_post(current_user().id, post_id)))


@bp.get("/projects/<project_id>/posts")
@require_user()
def list_posts(project_id: str):
    """The Reel's posts, newest first."""
    from sqlalchemy import select

    from ..models import Post

    project: Project = projects.get_project(current_user().id, project_id)
    items = db.session.execute(
        select(Post).where(Post.project_id == project.id).order_by(Post.created_at.desc()).limit(50)
    ).scalars()
    return jsonify(items=[publishing.post_json(p) for p in items])
