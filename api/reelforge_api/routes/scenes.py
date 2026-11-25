"""Scenes (API design group C: C1 to C6).

C7 to C10 (voice and visual regeneration, stock search, choosing a visual) belong to the
generation milestone (B4).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, make_response
from sqlalchemy import select

from .. import jobs, projects
from ..auth import current_user, require_user
from ..errors import ApiError
from ..extensions import db
from ..models import Scene
from ..project_schemas import CreateSceneBody, ReorderBody, RewriteBody, UpdateSceneBody, present
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
