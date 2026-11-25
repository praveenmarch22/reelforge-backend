"""Ideas and projects (API design group B: B1 to B10).

B11 (generate) and B12 (timeline) belong to the generation milestone (B4).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, make_response, request
from sqlalchemy import and_, case, func, or_, select

from .. import credits, jobs, projects, ratelimit
from ..auth import current_user, require_user
from ..errors import ApiError
from ..extensions import db
from ..job_types import llm_available
from ..models import Asset, Job, MusicTrack, Project, Scene
from ..project_schemas import (
    CreateProjectBody,
    IdeasBody,
    InstructionsBody,
    UpdateProjectBody,
    decode_cursor,
    encode_cursor,
    present,
    read_limit,
)
from ..prompts import PROMPT_VERSION
from ..schemas import read_body
from ..script_rules import DEFAULT_SETTINGS, JOB_COSTS, PROJECT_STATUSES, TITLE_MAX
from ..sessions import now

bp = Blueprint("projects", __name__)

HOUR = 3600
SCRIPT_IN_PROGRESS = "The script is still being written. Try again when it's ready."


def _bad(field: str, message: str) -> ApiError:
    return ApiError(400, "validation_error", "Some details need another look.", {"fields": {field: message}})


def start_llm_job(job_type: str, *, project=None, scene=None, input: dict) -> jobs.Job:
    """Creates a language-model job and reserves its credits. Caller commits, then dispatches.

    Checked in this order: a model is available (503), the same work is not already running
    (409), there are enough credits (402), the hourly limit (429).
    """
    user = current_user()
    if not llm_available():
        raise ApiError(503, "provider_unavailable", jobs.MESSAGES["provider_unavailable"])
    account = credits.lock_account(user.id)
    job = jobs.new_job(
        user.id,
        job_type,
        project_id=project.id if project else None,
        scene_id=scene.id if scene else None,
        input={**input, "promptVersion": PROMPT_VERSION},
        cost=JOB_COSTS[job_type],
    )
    credits.reserve(account, [job])
    # Ideas, scripts, rewrites and hooks share one limit (API design 4). Counted last, so a
    # request refused for another reason (409, 402) doesn't use up one of the 20; a 429 here
    # rolls the job and the reservation back with the rest of the request.
    ratelimit.hit("llm", str(user.id), limit=20, seconds=HOUR)
    return job


def script_input(project: Project, **extra) -> dict:
    source = {"script": project.source_script} if project.source_script and extra.get("first") else {
        # A regenerated script is always written fresh; for a pasted script its text is the topic.
        "topic": project.topic or (project.source_script or "")[:3000]
    }  # fmt: skip
    return {
        **source,
        "tone": project.tone,
        "mode": project.mode,
        "targetSeconds": project.target_seconds,
        "style": project.style,
        "character": project.character,
        **extra,
    }


def refuse_while_scripting(project: Project) -> None:
    if project.status == "scripting":
        raise ApiError(409, "already_running", SCRIPT_IN_PROGRESS)


# ---------------------------------------------------------------- B1


@bp.post("/ideas")
@require_user(verified=True)
def ideas():
    """B1. Free; the ideas arrive on the job's output."""
    body = read_body(IdeasBody)
    job = start_llm_job("ideas", input={"niche": body.niche, "keywords": body.keywords, "count": body.count})
    db.session.commit()
    answer = jsonify(job=jobs.job_json(job, False))
    jobs.dispatch([job])
    return answer, 202


# ---------------------------------------------------------------- B2, B3


@bp.get("/projects")
@require_user()
def list_projects():
    """B2. Newest first; `counts` ignores the filters (the dashboard tiles use it)."""
    user = current_user()
    limit = read_limit(request.args.get("limit"))
    cursor = decode_cursor(request.args.get("cursor"))
    mine = and_(Project.user_id == user.id, Project.deleted_at.is_(None))
    query = select(Project).where(mine)

    statuses = [s.strip() for s in (request.args.get("status") or "").split(",") if s.strip()]
    if any(s not in PROJECT_STATUSES for s in statuses):
        raise _bad("status", "Unknown status")
    if statuses:
        query = query.where(Project.status.in_(statuses))
    search = (request.args.get("q") or "").strip()
    if len(search) > 100:
        raise _bad("q", "Use 100 characters or fewer")
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(Project.title.ilike(f"%{escaped}%", escape="\\"))
    if cursor:
        moment, last_id = cursor
        query = query.where(
            or_(Project.updated_at < moment, and_(Project.updated_at == moment, Project.id < last_id))
        )
    rows = list(
        db.session.execute(
            query.order_by(Project.updated_at.desc(), Project.id.desc()).limit(limit + 1)
        ).scalars()
    )
    page, more = rows[:limit], len(rows) > limit

    ids = [p.id for p in page]
    script_progress = dict(
        db.session.execute(
            select(Job.project_id, Job.progress).where(
                Job.project_id.in_(ids), Job.type == "script", Job.state.in_(jobs.ACTIVE)
            )
        ).all()
    )
    scene_counts = {
        row.project_id: (int(row.done or 0), 2 * row.total)
        for row in db.session.execute(
            select(
                Scene.project_id,
                func.count().label("total"),
                func.sum(
                    case((Scene.voice_status == "ready", 1), else_=0)
                    + case((Scene.visual_status == "ready", 1), else_=0)
                ).label("done"),
            )
            .where(Scene.project_id.in_(ids))
            .group_by(Scene.project_id)
        )
    }
    total, posted = db.session.execute(
        select(func.count(), func.count().filter(Project.status == "posted")).where(mine)
    ).one()
    return jsonify(
        items=[projects.summary_json(p, script_progress.get(p.id), scene_counts.get(p.id)) for p in page],
        nextCursor=encode_cursor(page[-1].updated_at, page[-1].id) if more else None,
        counts={"total": total, "posted": posted},
    )


@bp.post("/projects")
@require_user(verified=True)
def create_project():
    """B3. Creates the project and starts writing (or preparing) its script. 1 credit."""
    body = read_body(CreateProjectBody)
    if body.topic is None and body.script is None:
        raise _bad("topic", "Enter a topic or paste a script")
    if body.topic is not None and body.script is not None:
        raise _bad("script", "Send a topic or a script, not both")
    user = current_user()
    project = Project(
        user_id=user.id,
        topic=body.topic or "",
        source_script=body.script,
        tone=body.tone,
        mode=body.mode or user.default_mode,
        target_seconds=body.target_seconds,
        style=body.style,
        character=body.character,
        voice_id=user.default_voice_id if body.voice_id is None else body.voice_id,
        settings={**DEFAULT_SETTINGS, "captionStyle": user.default_caption_style},
        status="scripting",
    )
    db.session.add(project)
    db.session.flush()
    job = start_llm_job(
        "script", project=project, input=script_input(project, first=True, previousStatus="draft")
    )
    db.session.commit()
    db.session.refresh(project)
    answer = jsonify(project=projects.project_json(project), job=jobs.job_json(job, False))
    jobs.dispatch([job])
    return answer, 202


# ---------------------------------------------------------------- B4 to B7


@bp.get("/projects/<project_id>")
@require_user()
def get_project(project_id: str):
    """B4."""
    return jsonify(project=projects.project_json(projects.get_project(current_user().id, project_id)))


@bp.patch("/projects/<project_id>")
@require_user()
def update_project(project_id: str):
    """B5. Changing the voice outdates every voice; changing the style or character outdates
    AI visuals. Other changes only affect the timeline."""
    body = read_body(UpdateProjectBody)
    sent = present(body)
    project = projects.get_project(current_user().id, project_id, lock=True)

    # Only `music` may be null (no music chosen); every other field always has a value.
    for field, value in sent.items():
        if value is None and field != "music":
            raise _bad(_camel(field), "Required")
    if body.settings is not None:
        nulls = [
            k for k, v in body.settings.model_dump(exclude_unset=True, by_alias=True).items() if v is None
        ]
        if nulls:
            raise _bad(f"settings.{nulls[0]}", "Required")
    for field in ("title", "tone", "music_mood", "post_caption", "hashtags"):
        if field in sent:
            setattr(project, field, sent[field])
    if sent.get("voice_id") is not None and sent["voice_id"] != project.voice_id:
        # A voice still being made in the old voice is stopped and refunded (QB4-001).
        jobs.cancel_active(project_id=project.id, job_type="voice")
        project.voice_id = sent["voice_id"]
        for scene in project.scenes:
            projects.mark_voice_outdated(scene)
    for field in ("style", "character"):
        if sent.get(field) is not None and sent[field] != getattr(project, field):
            # AI visuals being made in the old style are stopped and refunded.
            # So are stock scenes showing a fallback AI image, whose new take may be one too
            # (QB4S2-010).
            ai_scenes = [
                s.id
                for s in project.scenes
                if s.visual_source in ("ai_image", "ai_video")
                or (s.visual_asset is not None and (s.visual_asset.meta or {}).get("stockFallback"))
            ]
            if ai_scenes:
                jobs.cancel_active(scene_ids=ai_scenes, job_type="visual")
            setattr(project, field, sent[field])
            # Stock scenes showing an AI image (no clip fitted) are in the old style too (QB4S2-006).
            for scene in project.scenes:
                if scene.visual_asset is not None and (scene.visual_asset.meta or {}).get("stockFallback"):
                    projects.mark_visual_outdated(scene)
            for scene in project.scenes:
                if scene.visual_source in ("ai_image", "ai_video"):
                    projects.mark_visual_outdated(scene)
    if "music" in sent:
        _choose_music(project, sent["music"])
    if sent.get("settings") is not None:
        changes = body.settings.model_dump(exclude_unset=True, by_alias=True)
        project.settings = {**project.settings, **{k: v for k, v in changes.items() if v is not None}}
    projects.after_edit(project)
    db.session.commit()
    return jsonify(project=projects.project_json(project), estimate=projects.estimate(project))


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def _choose_music(project: Project, choice) -> None:
    if choice is None:
        project.music_track_id = project.music_asset_id = None
        return
    if choice.track_id is not None:
        track = db.session.get(MusicTrack, choice.track_id)
        if track is None or not track.is_active:
            raise _bad("music", "That track isn't available")
        project.music_track_id, project.music_asset_id = track.id, None
        return
    asset = db.session.get(Asset, choice.asset_id)
    if (
        asset is None
        or asset.user_id != project.user_id
        or asset.status != "ready"
        or asset.kind
        not in (
            "music",
            "upload",
        )
    ):
        raise _bad("music", "That file isn't available")
    project.music_asset_id, project.music_track_id = asset.id, None


@bp.delete("/projects/<project_id>")
@require_user()
def delete_project(project_id: str):
    """B6. Active jobs are cancelled and refunded; the project is purged after 7 days."""
    project = projects.get_project(current_user().id, project_id, lock=True)
    jobs.cancel_active(project_id=project.id)
    project.deleted_at = now()
    db.session.commit()
    return make_response("", 204)


@bp.post("/projects/<project_id>/duplicate")
@require_user()
def duplicate_project(project_id: str):
    """B7. Copies the script and settings and reuses the generated files (no credits)."""
    source = projects.get_project(current_user().id, project_id)
    refuse_while_scripting(source)  # its scenes are about to be replaced
    copy = Project(
        user_id=source.user_id,
        title=f"{source.title} (copy)"[:TITLE_MAX],
        **{
            f: getattr(source, f)
            for f in (
                "topic",
                "source_script",
                "tone",
                "mode",
                "target_seconds",
                "style",
                "character",
                "voice_id",
                "music_mood",
                "music_track_id",
                "music_asset_id",
                "settings",
                "post_caption",
                "hashtags",
            )
        },  # fmt: skip
        status="draft",
    )
    db.session.add(copy)
    db.session.flush()
    for scene in source.scenes:
        db.session.add(projects.copy_scene(scene, project_id=copy.id, position=scene.position))
    db.session.flush()
    db.session.refresh(copy)
    projects.after_edit(copy, force=True)
    db.session.commit()
    return jsonify(project=projects.project_json(copy)), 201


# ---------------------------------------------------------------- B8 to B10


@bp.post("/projects/<project_id>/script/regenerate")
@require_user(verified=True)
def regenerate_script(project_id: str):
    """B8. Writes a new script and replaces every scene. 1 credit."""
    body = read_body(InstructionsBody, allow_empty=True)
    project = projects.get_project(current_user().id, project_id, lock=True)
    if project.status in ("rendering", "posting"):
        raise ApiError(409, "already_running", "Wait until the render or post has finished.")
    # Until a script has been written once, this is the first attempt again (a retry after
    # a failure): a pasted script is prepared again and the AI names the project.
    written = db.session.execute(
        select(Job.id)
        .where(Job.project_id == project.id, Job.type == "script", Job.state == "succeeded")
        .limit(1)
    ).first()
    extra = {"previousStatus": project.status} if written else {"first": True, "previousStatus": "draft"}
    job = start_llm_job(
        "script", project=project, input=script_input(project, instructions=body.instructions, **extra)
    )
    project.status = "scripting"
    db.session.commit()
    answer = jsonify(job=jobs.job_json(job, False))
    jobs.dispatch([job])
    return answer, 202


@bp.post("/projects/<project_id>/hooks")
@require_user(verified=True)
def hooks(project_id: str):
    """B9. Three alternative opening lines. Free."""
    project = projects.get_project(current_user().id, project_id)
    refuse_while_scripting(project)
    if not project.scenes:
        raise ApiError(409, "no_script", "Write the script first.")
    job = start_llm_job(
        "hooks",
        project=project,
        input={
            "topic": project.topic or project.title,
            "tone": project.tone,
            "firstScene": project.scenes[0].narration,
        },
    )
    db.session.commit()
    answer = jsonify(job=jobs.job_json(job, False))
    jobs.dispatch([job])
    return answer, 202


@bp.get("/projects/<project_id>/estimate")
@require_user()
def estimate(project_id: str):
    """B10."""
    return jsonify(projects.estimate(projects.get_project(current_user().id, project_id)))
