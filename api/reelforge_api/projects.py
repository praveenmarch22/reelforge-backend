"""Projects and scenes: finding them, describing them in JSON, and keeping their status right."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, union, update

from . import jobs as jobs_module
from .errors import ApiError
from .extensions import db
from .models import Asset, CreditAccount, Job, Project, Scene
from .script_rules import estimated_seconds, visual_credits, voice_credits, word_count
from .storage import get_storage

NOT_FOUND = "We couldn't find that."
# Statuses a project has once its scenes have been generated at least once.
_GENERATED = ("generating", "needs_attention", "ready", "rendering", "render_failed", "rendered",
              "posting", "post_failed", "posted")  # fmt: skip
NEEDS_WORK = ("missing", "outdated", "failed")


def _iso(value) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        raise ApiError(404, "not_found", NOT_FOUND) from None


# ---------------------------------------------------------------- finding


def get_project(user_id: uuid.UUID, project_id: str, *, lock: bool = False) -> Project:
    """The user's project, or 404 for anything missing, deleted or someone else's."""
    query = select(Project).where(
        Project.id == _uuid(project_id), Project.user_id == user_id, Project.deleted_at.is_(None)
    )
    if lock:
        query = query.with_for_update(of=Project).execution_options(populate_existing=True)
    project = db.session.execute(query).scalar_one_or_none()
    if project is None:
        raise ApiError(404, "not_found", NOT_FOUND)
    return project


def get_scene(user_id: uuid.UUID, scene_id: str, *, lock: bool = False) -> tuple[Scene, Project]:
    """The scene and its project (the project is locked when lock=True), or 404."""
    row = db.session.execute(
        select(Scene.project_id)
        .join(Project, Project.id == Scene.project_id)
        .where(Scene.id == _uuid(scene_id), Project.user_id == user_id, Project.deleted_at.is_(None))
    ).first()
    if row is None:
        raise ApiError(404, "not_found", NOT_FOUND)
    project = get_project(user_id, str(row[0]), lock=lock)
    scene = db.session.get(Scene, _uuid(scene_id))
    if scene is None:  # deleted between the two reads
        raise ApiError(404, "not_found", NOT_FOUND)
    return scene, project


def active_jobs(project_id: uuid.UUID) -> list[Job]:
    return list(
        db.session.execute(
            select(Job)
            .where(Job.project_id == project_id, Job.state.in_(jobs_module.ACTIVE))
            .order_by(Job.queued_at)
        ).scalars()
    )


# ---------------------------------------------------------------- status


def mark_voice_outdated(scene: Scene) -> None:
    if scene.voice_status in ("ready", "failed"):
        scene.voice_status = "outdated"


def mark_visual_outdated(scene: Scene) -> None:
    if scene.visual_status in ("ready", "failed"):
        scene.visual_status = "outdated"


def after_edit(project: Project, *, force: bool = False) -> None:
    """An edit to a generated project sends it back to ready, or to draft if something is
    now missing or outdated (DB design 5). Projects that are still being written or are in
    the middle of rendering or posting keep their status. `force` decides it for a draft too
    (a duplicated project)."""
    # Any change to the project or its scenes moves it to the top of the dashboard (QB3-007).
    project.updated_at = func.now()
    if project.status in ("scripting", "generating", "rendering", "posting"):
        return
    scenes = project.scenes
    # A draft counts as generated once any scene has a file: it can become ready again
    # after a voice that failed is redone (QB4-014).
    generated = project.status in _GENERATED or any(s.voice_asset_id or s.visual_asset_id for s in scenes)
    if not generated and not force:
        return
    # A scene getting a new take still has its earlier, ready file: it counts as ready.
    redoing = {
        job.scene_id
        for job in db.session.execute(
            select(Job).where(
                Job.project_id == project.id, Job.type == "voice", Job.state.in_(jobs_module.ACTIVE)
            )
        ).scalars()
        if job.input.get("previousStatus") == "ready"
    }

    def voice_ok(s: Scene) -> bool:
        return s.voice_status == "ready" or (
            s.voice_status in ("queued", "running") and s.id in redoing and s.voice_asset_id is not None
        )

    complete = bool(scenes) and all(voice_ok(s) and s.visual_status == "ready" for s in scenes)
    project.status = "ready" if complete else "draft"


def renumber(project: Project) -> None:
    """Positions 1..n in the current order. The unique (project, position) constraint is
    deferred for this transaction so positions can be swapped."""
    db.session.execute(db.text("SET CONSTRAINTS scenes_position_unique DEFERRED"))
    for index, scene in enumerate(sorted(project.scenes, key=lambda s: s.position), start=1):
        scene.position = index


def copy_scene(scene: Scene, *, project_id, position: int) -> Scene:
    """A copy that reuses the scene's generated files. Work that was still running is not
    copied: those parts count as missing (or outdated, when an older file exists)."""

    def status(value: str, asset_id) -> str:
        if value in ("queued", "running"):
            return "outdated" if asset_id else "missing"
        return value

    return Scene(
        project_id=project_id,
        position=position,
        **{
            f: getattr(scene, f)
            for f in (
                "narration",
                "on_screen_text",
                "visual_prompt",
                "stock_query",
                "mood",
                "visual_source",
                "voice_asset_id",
                "visual_asset_id",
                "keyframe_asset_id",
                "words",
                "words_edited",
                "trim_start",
                "trim_end",
                "last_error",
            )
        },  # fmt: skip
        voice_status=status(scene.voice_status, scene.voice_asset_id),
        visual_status=status(scene.visual_status, scene.visual_asset_id),
    )


def detach_jobs(scene_ids: list) -> None:
    """Keeps the jobs of scenes about to be deleted, without their scene, so a job being
    polled still answers and the credit history keeps its links (QB3-008). Call after
    their active jobs were cancelled."""
    if scene_ids:
        db.session.execute(update(Job).where(Job.scene_id.in_(scene_ids)).values(scene_id=None))


def release_assets(asset_ids: list) -> None:
    """Marks files that no scene uses any more as replaced, so the daily clean-up removes them
    after 7 days (F-2). A file still used by another scene (a duplicate) is kept."""
    asset_ids = [a for a in asset_ids if a]
    if not asset_ids:
        return
    in_use = set(
        db.session.execute(
            union(
                select(Scene.voice_asset_id).where(Scene.voice_asset_id.in_(asset_ids)),
                select(Scene.visual_asset_id).where(Scene.visual_asset_id.in_(asset_ids)),
                select(Scene.keyframe_asset_id).where(Scene.keyframe_asset_id.in_(asset_ids)),
            )
        ).scalars()
    )
    unused = [a for a in asset_ids if a not in in_use]
    if unused:
        db.session.execute(
            update(Asset)
            .where(Asset.id.in_(unused), Asset.replaced_at.is_(None))
            .values(replaced_at=func.now())
        )


# ---------------------------------------------------------------- JSON


def scene_json(scene: Scene) -> dict[str, Any]:
    def media(asset) -> dict[str, Any] | None:
        # The link is signed here, without a network call, and works for 2 hours.
        if asset is None:
            return None
        return {
            "assetId": str(asset.id),
            "url": get_storage().presigned_get(asset.s3_key),
            "durationSeconds": asset.duration_seconds,
        }

    return {
        "id": str(scene.id),
        "position": scene.position,
        "narration": scene.narration,
        "onScreenText": scene.on_screen_text,
        "visualPrompt": scene.visual_prompt,
        "stockQuery": scene.stock_query,
        "mood": scene.mood,
        "visualSource": scene.visual_source,
        "estimatedSeconds": estimated_seconds(scene.narration),
        "voiceStatus": scene.voice_status,
        "visualStatus": scene.visual_status,
        "voice": voice_media(scene.voice_asset, media),
        "visual": media(scene.visual_asset),
        "words": scene.words,
        "wordsEdited": scene.words_edited,
        "trimStart": scene.trim_start,
        "trimEnd": scene.trim_end,
        "lastError": scene.last_error,
    }


def voice_media(asset, media) -> dict[str, Any] | None:
    """The voice file plus what the editor shows about it: who made it, in which voice, and
    whether the caption timings are only estimated (QB4-008)."""
    out = media(asset)
    if out is not None:
        meta = asset.meta or {}
        out.update(
            provider=asset.provider,
            voiceId=meta.get("voiceId", ""),
            estimatedTimings=bool(meta.get("estimatedTimings")),
        )
    return out


def _progress(
    project: Project, script_progress: int | None, scene_counts: tuple[int, int] | None
) -> int | None:
    if project.status == "scripting":
        return script_progress or 0
    if project.status == "generating":
        done, total = scene_counts or (0, 0)
        return round(100 * done / total) if total else 0
    if project.status in ("rendering", "posting"):
        return 0  # the render and post progress arrive with milestones B5 and B6
    return None


def summary_json(project: Project, script_progress: int | None = None,
                 scene_counts: tuple[int, int] | None = None) -> dict[str, Any]:  # fmt: skip
    return {
        "id": str(project.id),
        "title": project.title,
        "mode": project.mode,
        "status": project.status,
        "targetSeconds": project.target_seconds,
        "durationSeconds": None,  # from the timeline, milestone B4
        "thumbnailUrl": None,  # from the render, milestone B5
        "progress": _progress(project, script_progress, scene_counts),
        "updatedAt": _iso(project.updated_at),
    }


def project_json(project: Project) -> dict[str, Any]:
    active = active_jobs(project.id)
    scenes = project.scenes
    script_job = next((j for j in active if j.type == "script"), None)
    done = sum((s.voice_status == "ready") + (s.visual_status == "ready") for s in scenes)
    music = (
        {"trackId": str(project.music_track_id)}
        if project.music_track_id
        else {"assetId": str(project.music_asset_id)}
        if project.music_asset_id
        else None
    )
    return {
        **summary_json(project, script_job.progress if script_job else None, (done, 2 * len(scenes))),
        "topic": project.topic,
        "tone": project.tone,
        "style": project.style,
        "character": project.character,
        "voiceId": project.voice_id,
        "musicMood": project.music_mood,
        "music": music,
        "settings": project.settings,
        "estimatedSeconds": round(sum(estimated_seconds(s.narration) for s in scenes), 1),
        "wordCount": sum(word_count(s.narration) for s in scenes),
        "timelineVersion": project.timeline_version,
        "postCaption": project.post_caption,
        "hashtags": project.hashtags,
        "scenes": [scene_json(s) for s in scenes],
        "activeJobs": jobs_module.jobs_json(active),
        "latestRender": None,  # milestone B5
        "latestPost": None,  # milestone B6
        "createdAt": _iso(project.created_at),
    }


def estimate(project: Project) -> dict[str, Any]:
    """B10: the credits needed to generate everything missing or outdated."""
    voices = [s for s in project.scenes if s.voice_status in NEEDS_WORK]
    visuals = [s for s in project.scenes if s.visual_status in NEEDS_WORK]
    by_source = {
        source: [s for s in visuals if s.visual_source == source]
        for source in ("stock", "ai_image", "ai_video")
    }
    items = [
        {
            "type": "voice",
            "count": len(voices),
            # One charge for all the voices Generate makes: their total length (CR, 2026-11-25).
            "credits": voice_credits(sum(estimated_seconds(s.narration) for s in voices)) if voices else 0,
        },
        {"type": "visual", "count": len(by_source["stock"]), "credits": 0},
        {
            "type": "ai_image",
            "count": len(by_source["ai_image"]),
            "credits": visual_credits("ai_image") * len(by_source["ai_image"]),
        },  # noqa: E501
        {
            "type": "ai_video",
            "count": len(by_source["ai_video"]),
            "credits": visual_credits("ai_video") * len(by_source["ai_video"]),
        },  # noqa: E501
        {
            "type": "music",
            "count": int(project.music_track_id is None and project.music_asset_id is None),
            "credits": 0,
        },  # noqa: E501
    ]
    items = [item for item in items if item["count"]]
    balance = db.session.execute(
        select(CreditAccount.balance).where(CreditAccount.user_id == project.user_id)
    ).scalar_one()
    return {"credits": sum(item["credits"] for item in items), "balance": balance, "items": items}
