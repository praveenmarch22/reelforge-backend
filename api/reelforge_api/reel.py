"""Generate Reel (API B11), the project's timeline (B12) and closing a generate run.

Generate starts everything a Reel still needs in one go: every missing, outdated or failed
voice and visual, and a music track if none is chosen. Voices and visuals run side by side; a
visual is fitted to the scene's estimated length until its voice exists, and the renderer
stretches or trims clips to the final length (pipeline strategy 3).

The project is `generating` until its last voice or visual job ends. Then `advance_project`
looks at the scenes: all ready → `ready` (and the timeline is saved), otherwise
`needs_attention` (the failed scenes show why and can be retried).
"""

from __future__ import annotations

from sqlalchemy import select

from . import credits, generation, jobs, music, timeline, visuals, voices
from .auth import current_user
from .errors import ApiError
from .extensions import db
from .models import Asset, Job, MusicTrack, Project, Scene
from .projects import NEEDS_WORK
from .script_rules import estimated_seconds, scene_voice_credits, voice_credits

BUSY = ("queued", "running")
STOPPED_STATUSES = ("scripting", "rendering", "posting")


def _share(total: int, weights: list[float]) -> list[int]:
    """Splits `total` credits over the jobs in proportion to their length, in whole credits
    that add up exactly (largest remainder), so a failed scene refunds its own part."""
    if not weights:
        return []
    whole = sum(weights) or 1.0
    raw = [total * w / whole for w in weights]
    parts = [int(r) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - parts[i], reverse=True)[: total - sum(parts)]:
        parts[i] += 1
    return parts


def plan(project: Project, scene_ids: set[str] | None) -> tuple[list[Scene], list[Scene], list[Scene]]:
    """(scenes needing a voice, scenes needing a visual, upload scenes with no file)."""
    chosen = [s for s in project.scenes if scene_ids is None or str(s.id) in scene_ids]
    need_voice = [s for s in chosen if s.voice_status in NEEDS_WORK]
    need_visual = [s for s in chosen if s.visual_status in NEEDS_WORK and s.visual_source != "upload"]
    stuck = [s for s in chosen if s.visual_status in NEEDS_WORK and s.visual_source == "upload"]
    return need_voice, need_visual, stuck


def generate(project: Project, scene_ids: set[str] | None) -> tuple[list[Job], int]:
    """B11. Creates every job the Reel still needs and reserves their credits all at once (or
    none: CR-1). The project must be locked. Caller commits, then dispatches.

    Order of refusals: 409 (script being written, or rendering / posting), 503 (a needed
    provider is off), 409 already_running (nothing left to start but work is running), 402.
    """
    user = current_user()
    if project.status in STOPPED_STATUSES:
        raise ApiError(409, "already_running", "This Reel is busy. Try again when it's done.")
    need_voice, need_visual, _ = plan(project, scene_ids)
    if need_voice and not generation.voice_available():
        raise ApiError(503, "provider_unavailable", jobs.MESSAGES["provider_unavailable"])
    if any(not visuals.visual_available(s.visual_source) for s in need_visual):
        raise ApiError(503, "provider_unavailable", jobs.MESSAGES["provider_unavailable"])

    if project.music_track_id is None and project.music_asset_id is None:
        track = music.pick_track(project)
        if track is not None:
            project.music_track_id = track.id

    if not need_voice and not need_visual:
        busy = any(s.voice_status in BUSY or s.visual_status in BUSY for s in project.scenes)
        if busy:
            raise ApiError(409, "already_running", "This is already in progress.")
        return [], 0

    account = credits.lock_account(user.id)
    voice = voices.resolve(project.voice_id, user.default_voice_id)
    seconds = [estimated_seconds(s.narration) for s in need_voice]
    if len(need_voice) == 1:
        shares = [scene_voice_credits(need_voice[0].narration)]  # the same as C7 for one scene
    else:
        shares = _share(voice_credits(sum(seconds)), seconds) if need_voice else []
    new_jobs: list[Job] = []
    for scene, cost in zip(need_voice, shares, strict=True):
        new_jobs.append(generation.start_voice_job(project, scene, voice.id, cost=cost, reserve=False))
    for scene in need_visual:
        new_jobs.append(visuals.start_visual_job(project, scene, reserve=False))
    credits.reserve(account, new_jobs)
    project.status = "generating"
    return new_jobs, sum(j.credits_reserved for j in new_jobs)


# ---------------------------------------------------------------- closing a run


def advance_project(project_id, finishing=None) -> None:
    """Called whenever a voice or visual job ends (`finishing`: the job saving its result,
    still marked running until its transaction ends). When the project is `generating` and none
    of its voice or visual jobs is still active, it becomes `ready` (timeline saved) or
    `needs_attention`. The caller holds the project's lock (jobs lock the project first)."""
    project = db.session.get(Project, project_id)
    if project is None or project.status != "generating":
        return
    active = db.session.execute(
        select(Job.id).where(
            Job.project_id == project.id,
            Job.type.in_(("voice", "visual")),
            Job.state.in_(jobs.ACTIVE),
            Job.id != finishing if finishing is not None else True,
        )
    ).first()
    if active is not None:
        return
    scenes = project.scenes
    if scenes and all(s.voice_status == "ready" and s.visual_status == "ready" for s in scenes):
        project.status = "ready"
        project.timeline = build(project, links=False)
    else:
        project.status = "needs_attention" if scenes else "draft"


# ---------------------------------------------------------------- the timeline


def _music(project: Project) -> tuple[MusicTrack | None, Asset | None]:
    track = db.session.get(MusicTrack, project.music_track_id) if project.music_track_id else None
    if track is not None and not track.is_active:
        track = None  # taken out of the library: the preview and renders use the mood's pick
    asset = db.session.get(Asset, project.music_asset_id) if project.music_asset_id else None
    if track is None and asset is None:
        track = music.pick_track(project)  # the preview plays the track Generate would choose
    return track, asset


def build(project: Project, *, links: bool) -> dict:
    track, asset = _music(project)
    return timeline.build(project, track, asset, links=links)


def timeline_json(project: Project) -> dict:
    """B12: the timeline with playable links, plus what the editor needs around it."""
    built = build(project, links=True)
    return {
        "timelineVersion": project.timeline_version,
        "timeline": built,
        "durationSeconds": timeline.seconds_of(built),
        "tooLong": timeline.too_long(built),
        "ready": bool(built["scenes"]) and all(s["ready"] for s in built["scenes"]),
    }
