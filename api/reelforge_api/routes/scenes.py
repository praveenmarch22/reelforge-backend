"""Scenes (API design group C: C1 to C10).

C7 (a scene's voice) is milestone B4 slice 1; C8 to C10 (visuals) are slice 2.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, make_response, request
from sqlalchemy import select

from .. import generation, jobs, projects, ratelimit, visuals
from ..auth import current_user, require_user
from ..errors import ApiError
from ..extensions import db
from ..idempotency import idempotent
from ..models import Scene
from ..project_schemas import (
    CreateSceneBody,
    ReorderBody,
    RewriteBody,
    SelectVisualBody,
    UpdateSceneBody,
    VisualRegenerateBody,
    VoiceRegenerateBody,
    present,
)
from ..schemas import read_body
from ..script_rules import SCENES_MAX, default_visual_source
from .projects import refuse_while_scripting, start_llm_job

bp = Blueprint("scenes", __name__)


def _bad(field: str, message: str) -> ApiError:
    return ApiError(400, "validation_error", "Some details need another look.", {"fields": {field: message}})


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def _make_room(project, position: int) -> None:
    """Moves scenes at `position` and after one place down, so a scene can go there."""
    projects.renumber(project)  # also defers the position constraint for this transaction
    for scene in project.scenes:
        if scene.position >= position:
            scene.position += 1


def _check_room(project) -> None:
    if len(project.scenes) >= SCENES_MAX:
        raise _bad("scenes", f"A Reel can have up to {SCENES_MAX} scenes")


@bp.post("/projects/<project_id>/scenes")
@require_user()
def add_scene(project_id: str):
    """C1. Added at `position` (default: last)."""
    body = read_body(CreateSceneBody)
    project = projects.get_project(current_user().id, project_id, lock=True)
    refuse_while_scripting(project)
    _check_room(project)
    last = len(project.scenes) + 1
    position = min(body.position or last, last)
    _make_room(project, position)
    fields = {k: v for k, v in present(body).items() if k != "position" and v is not None}
    scene = Scene(
        project_id=project.id,
        position=position,
        visual_source=fields.pop("visual_source", default_visual_source(project.mode)),
        **fields,
    )
    db.session.add(scene)
    db.session.flush()
    db.session.expire(project, ["scenes"])
    projects.after_edit(project)
    db.session.commit()
    return jsonify(scene=projects.scene_json(scene), estimate=projects.estimate(project)), 201


@bp.patch("/scenes/<scene_id>")
@require_user()
def update_scene(scene_id: str):
    """C2. Narration or mood outdates the voice; the prompt, stock query or visual source
    outdates the visual; on-screen text, caption words and trims only change the timeline."""
    body = read_body(UpdateSceneBody)
    sent = present(body)
    scene, project = projects.get_scene(current_user().id, scene_id, lock=True)
    refuse_while_scripting(project)
    for field in sent:
        if sent[field] is None and field != "trim_end":
            raise _bad(_camel(field), "Required")
    voice_busy = scene.voice_status in ("queued", "running")
    if "words" in sent and voice_busy:
        # The new voice would replace the fixed words as soon as it is ready (QB4-004).
        raise ApiError(409, "already_running", generation.VOICE_BUSY)
    if voice_busy and any(f in sent and sent[f] != getattr(scene, f) for f in ("narration", "mood")):
        # The voice being made is for the old text: stop it now and refund it (QB4-005).
        jobs.cancel_active(scene_ids=[scene.id], job_type="voice")
    if scene.visual_status in ("queued", "running") and any(
        f in sent and sent[f] != getattr(scene, f) for f in visuals.VISUAL_FIELDS
    ):
        # Likewise a visual being made for the old prompt, query or source.
        jobs.cancel_active(scene_ids=[scene.id], job_type="visual")

    for field, outdates in (
        ("narration", projects.mark_voice_outdated),
        ("mood", projects.mark_voice_outdated),
        ("visual_prompt", projects.mark_visual_outdated),
        ("stock_query", projects.mark_visual_outdated),
        ("visual_source", projects.mark_visual_outdated),
    ):
        if field in sent and sent[field] != getattr(scene, field):
            setattr(scene, field, sent[field])
            outdates(scene)
    if "on_screen_text" in sent:
        scene.on_screen_text = sent["on_screen_text"]

    if "words" in sent:
        # Caption fixes change the text of each word only; the timings stay (CA-4).
        if len(sent["words"]) != len(scene.words):
            raise _bad("words", f"Send exactly {len(scene.words)} words, one for each word in the captions")
        scene.words = [{**old, "text": new.text} for old, new in zip(scene.words, sent["words"], strict=True)]
        scene.words_edited = True

    start = sent.get("trim_start", scene.trim_start)
    end = sent["trim_end"] if "trim_end" in sent else scene.trim_end
    if end is not None and end <= start:
        raise _bad("trimEnd", "The end must be after the start")
    scene.trim_start, scene.trim_end = start, end

    projects.after_edit(project)
    db.session.commit()
    return jsonify(scene=projects.scene_json(scene), estimate=projects.estimate(project))


@bp.delete("/scenes/<scene_id>")
@require_user()
def delete_scene(scene_id: str):
    """C3. Work running on the scene is cancelled and refunded; the rest are renumbered."""
    scene, project = projects.get_scene(current_user().id, scene_id, lock=True)
    refuse_while_scripting(project)
    jobs.cancel_active(scene_ids=[scene.id])
    projects.detach_jobs([scene.id])
    asset_ids = [scene.voice_asset_id, scene.visual_asset_id, scene.keyframe_asset_id]
    db.session.delete(scene)
    db.session.flush()
    db.session.expire(project, ["scenes"])
    projects.release_assets(asset_ids)
    projects.renumber(project)
    projects.after_edit(project)
    db.session.commit()
    return make_response("", 204)


@bp.post("/scenes/<scene_id>/duplicate")
@require_user()
def duplicate_scene(scene_id: str):
    """C4. The copy goes right after the original and reuses its files."""
    scene, project = projects.get_scene(current_user().id, scene_id, lock=True)
    refuse_while_scripting(project)
    _check_room(project)
    _make_room(project, scene.position + 1)
    copy = projects.copy_scene(scene, project_id=project.id, position=scene.position + 1)
    db.session.add(copy)
    db.session.flush()
    db.session.expire(project, ["scenes"])
    projects.after_edit(project)
    db.session.commit()
    return jsonify(scene=projects.scene_json(copy), estimate=projects.estimate(project)), 201


@bp.post("/projects/<project_id>/scenes/reorder")
@require_user()
def reorder_scenes(project_id: str):
    """C5. `sceneIds` lists every scene of the project once, in the new order."""
    body = read_body(ReorderBody)
    project = projects.get_project(current_user().id, project_id, lock=True)
    refuse_while_scripting(project)
    by_id = {scene.id: scene for scene in project.scenes}
    if len(body.scene_ids) != len(set(body.scene_ids)) or set(body.scene_ids) != set(by_id):
        raise _bad("sceneIds", "List every scene of this project exactly once")
    projects.renumber(project)
    for position, scene_id in enumerate(body.scene_ids, start=1):
        by_id[scene_id].position = position
    db.session.flush()
    db.session.expire(project, ["scenes"])
    projects.after_edit(project)
    db.session.commit()
    return jsonify(
        scenes=[projects.scene_json(s) for s in project.scenes], estimate=projects.estimate(project)
    )


@bp.post("/scenes/<scene_id>/rewrite")
@require_user(verified=True)
def rewrite_scene(scene_id: str):
    """C6. The AI rewrites this scene's text; its voice becomes outdated. 1 credit."""
    body = read_body(RewriteBody)
    scene, project = projects.get_scene(current_user().id, scene_id, lock=True)
    refuse_while_scripting(project)
    neighbours = {
        s.position: s.narration
        for s in db.session.execute(
            select(Scene).where(
                Scene.project_id == project.id, Scene.position.in_([scene.position - 1, scene.position + 1])
            )
        ).scalars()
    }
    job = start_llm_job(
        "rewrite_scene",
        project=project,
        scene=scene,
        input={
            "topic": project.topic or project.title,
            "tone": project.tone,
            "before": neighbours.get(scene.position - 1, ""),
            "narration": scene.narration,
            "onScreenText": scene.on_screen_text,
            "after": neighbours.get(scene.position + 1, ""),
            "instructions": body.instructions,
        },
    )
    db.session.commit()
    answer = jsonify(job=jobs.job_json(job, False))
    jobs.dispatch([job])
    return answer, 202


@bp.post("/scenes/<scene_id>/voice/regenerate")
@require_user(verified=True)
@idempotent
def regenerate_voice(scene_id: str):
    """C7. A new voiceover for this scene. 2 credits per started 30 seconds of narration."""
    body = read_body(VoiceRegenerateBody, allow_empty=True)
    scene, project = projects.get_scene(current_user().id, scene_id, lock=True)
    refuse_while_scripting(project)
    if scene.words_edited and not body.confirm_replace_words:
        raise ApiError(409, "words_edited", generation.WORDS_EDITED)
    job = generation.start_voice_job(project, scene, body.voice_id or "")
    db.session.commit()
    answer = jsonify(job=jobs.job_json(job, False))
    jobs.dispatch([job])
    return answer, 202


def _bad_query(field: str, message: str) -> ApiError:
    return ApiError(400, "validation_error", "Some details need another look.", {"fields": {field: message}})


@bp.post("/scenes/<scene_id>/visual/regenerate")
@require_user(verified=True)
@idempotent
def regenerate_visual(scene_id: str):
    """C8. A new visual for this scene: the next stock clip, or a new AI image. Changes to
    the source, prompt or stock query sent here are saved first. 0 credits for stock, 1 for an
    AI image (and, until B7, for an AI video scene, which gets an AI image with motion)."""
    body = read_body(VisualRegenerateBody, allow_empty=True)
    scene, project = projects.get_scene(current_user().id, scene_id, lock=True)
    refuse_while_scripting(project)
    sent = present(body)
    for field, value in sent.items():
        if value is None:
            raise _bad(_camel(field), "Required")
    if scene.visual_status in ("queued", "running"):
        raise ApiError(409, "already_running", "This scene's visual is already being made.")
    changed = [f for f in visuals.VISUAL_FIELDS if f in sent and sent[f] != getattr(scene, f)]
    if changed:
        # The current visual no longer matches: if this take is cancelled, the scene must not
        # show the old visual as ready (BUG-043).
        projects.mark_visual_outdated(scene)
        for field in changed:
            setattr(scene, field, sent[field])
    if scene.visual_source == "upload":
        raise _bad("visualSource", "Uploads are chosen with a file; pick stock or an AI source to regenerate")
    job = visuals.start_visual_job(project, scene)
    # 60 an hour (QB4S2-004), counted last so a refused request doesn't use one up.
    ratelimit.hit("visual", str(current_user().id), limit=60, seconds=3600)
    projects.after_edit(project)
    db.session.commit()
    answer = jsonify(job=jobs.job_json(job, False))
    jobs.dispatch([job])
    return answer, 202


@bp.get("/scenes/<scene_id>/stock-search")
@require_user(verified=True)
def stock_search(scene_id: str):
    """C9. Stock clips for this scene, 20 a page. 60 searches an hour."""
    scene, _ = projects.get_scene(current_user().id, scene_id)
    query = " ".join((request.args.get("q") or scene.stock_query or scene.visual_prompt or "").split())
    if not query:
        raise _bad_query("q", "Type what to search for")
    if len(query) > 100:
        raise _bad_query("q", "Use 100 characters or fewer")
    page_text = request.args.get("page", "1")
    if not page_text.isdigit() or not 1 <= int(page_text) <= 25:
        raise _bad_query("page", "Use a page number from 1 to 25")
    if not visuals.stock_available():
        raise ApiError(503, "provider_unavailable", jobs.MESSAGES["provider_unavailable"])
    ratelimit.hit("stock_search", str(current_user().id), limit=60, seconds=3600)
    db.session.rollback()  # nothing to save; no row stays locked while the provider answers
    page = int(page_text)
    try:
        clips, more = visuals.stock_search(query, page)
    except jobs.ProviderError:
        raise ApiError(503, "provider_unavailable", jobs.MESSAGES["provider_unavailable"]) from None
    return jsonify(items=[c.json() for c in clips], nextPage=page + 1 if more else None, query=query)


@bp.post("/scenes/<scene_id>/visual/select")
@require_user()
def select_visual(scene_id: str):
    """C10. A stock clip from C9 (downloaded in the background, 202) or a finished upload
    (used at once, 200). 0 credits."""
    body = read_body(SelectVisualBody)
    scene, project = projects.get_scene(current_user().id, scene_id, lock=True)
    refuse_while_scripting(project)
    if body.asset_id is not None:
        visuals.use_upload(project, scene, body.asset_id)
        db.session.commit()
        return jsonify(scene=projects.scene_json(scene), estimate=projects.estimate(project))
    if not visuals.is_stock_id(body.stock):
        raise _bad("stock", "Choose a clip from the stock search")
    jobs.cancel_active(scene_ids=[scene.id], job_type="visual")
    if scene.visual_source != "stock":
        projects.mark_visual_outdated(scene)  # an AI visual being swapped for stock (BUG-043)
        scene.visual_source = "stock"
    job = visuals.start_visual_job(project, scene, stock_id=body.stock)
    projects.after_edit(project)
    db.session.commit()
    answer = jsonify(job=jobs.job_json(job, False))
    jobs.dispatch([job])
    return answer, 202
