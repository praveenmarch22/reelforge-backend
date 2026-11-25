"""Generation jobs (milestone B4). Slice 1: one scene's voiceover.

A voice job reads the scene's narration and mood, asks the voice providers in order
(ElevenLabs, then Edge), stores the audio in S3 and links it to the scene with its word
timings. The scene's `voiceStatus` follows the job: queued, running, then ready or failed.
"""

from __future__ import annotations

import hashlib
import logging

from flask import current_app
from sqlalchemy import select, update

from . import credits, jobs, voices
from .auth import current_user
from .errors import ApiError
from .extensions import db
from .models import Asset, Job, Project, ProviderSwitch, Scene
from .projects import after_edit, release_assets
from .providers.base import AllProvidersFailed, with_fallback
from .providers.voice import PROVIDERS as VOICE_PROVIDERS
from .providers.voice import match_narration
from .script_rules import scene_voice_credits
from .storage import get_storage

log = logging.getLogger(__name__)

VOICE_CHANGED = "The scene was edited while its voice was being made, so that voice was not used."
VOICE_BUSY = "This scene's voice is being made. Fix caption words when it's ready."
WORDS_EDITED = (
    "You fixed caption words in this scene by hand. A new voice replaces them. Confirm to continue."
)


# ---------------------------------------------------------------- providers


def enabled_voice_names() -> list[str]:
    """Configured voice providers that the admin has not switched off (G-3)."""
    names = current_app.extensions["settings"].voice_provider_names
    off = set(
        db.session.execute(
            select(ProviderSwitch.provider).where(
                ProviderSwitch.provider.in_(names), ProviderSwitch.enabled.is_(False)
            )
        ).scalars()
    )
    return [n for n in names if n in VOICE_PROVIDERS and n not in off]


def voice_available() -> bool:
    settings = current_app.extensions["settings"]
    keys = {"elevenlabs": settings.elevenlabs_api_key, "edge": "free", "mock": "yes"}
    return any(keys.get(name) for name in enabled_voice_names())


def voice_input_hash(narration: str, mood: str, voice_id: str) -> str:
    """What a voiceover depends on; slice 3's Generate reuses a file with the same hash."""
    return hashlib.sha256(f"voice\n{voice_id}\n{mood}\n{narration}".encode()).hexdigest()


# ---------------------------------------------------------------- starting


def start_voice_job(
    project: Project, scene: Scene, voice_id: str = "", *, cost: int | None = None, reserve: bool = True
) -> Job:
    """Creates a voice job for one scene and reserves its credits (C7). The project must be
    locked. Caller commits, then dispatches.

    Checked in this order: a voice provider is available (503), the scene's voice is not
    already being made (409), there are enough credits (402).
    """
    user = current_user()
    if not voice_available():
        raise ApiError(503, "provider_unavailable", jobs.MESSAGES["provider_unavailable"])
    voice = voices.resolve(voice_id, project.voice_id, user.default_voice_id)
    # Generate (B11) passes the scene's share of the whole Reel's voice price and reserves the
    # batch itself, all or nothing.
    account = credits.lock_account(user.id) if reserve else None
    job = jobs.new_job(
        user.id,
        "voice",
        project_id=project.id,
        scene_id=scene.id,
        input={
            "narration": scene.narration,
            "mood": scene.mood,
            "voiceId": voice.id,
            "previousStatus": scene.voice_status,
        },
        cost=scene_voice_credits(scene.narration) if cost is None else cost,
    )
    if account is not None:
        credits.reserve(account, [job])
    # A ready project stays ready while a scene gets a new take (its old voice still plays);
    # it goes back to draft only if the new voice fails (on_stop).
    scene.voice_status = "queued"
    return job


# ---------------------------------------------------------------- the job type


def _lock_scene(scene_id) -> Scene | None:
    if scene_id is None:
        return None
    return db.session.execute(
        select(Scene).where(Scene.id == scene_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()


class VoiceJob:
    @staticmethod
    def on_start(job: Job) -> None:
        db.session.execute(
            update(Scene)
            .where(Scene.id == job.scene_id, Scene.voice_status == "queued")
            .values(voice_status="running")
        )

    @staticmethod
    def work(job: Job):
        i = job.input
        settings = current_app.extensions["settings"]
        voice = voices.resolve(i["voiceId"])
        providers = [VOICE_PROVIDERS[name](settings) for name in enabled_voice_names()]
        if not providers:
            raise AllProvidersFailed("voice: no voice provider is switched on", none_available=True)
        delay = 0 if settings.env == "test" else 2

        def pause(seconds: float) -> None:
            if delay:
                import time

                time.sleep(seconds)

        result, provider = with_fallback(
            providers, lambda p: p.synthesize(i["narration"], voice, i["mood"]), label="voice", sleep=pause
        )
        result.words = match_narration(result.words, i["narration"])
        # Stored before the job is saved, so no database lock is held during the upload. If the
        # job is then dropped (cancelled, or the scene changed), `discard` removes the file.
        key = f"users/{job.user_id}/projects/{job.project_id}/voice/{job.id}.{result.extension}"
        get_storage().put_bytes(key, result.audio, result.content_type)
        return {"key": key, "result": result, "provider": provider}, provider

    @staticmethod
    def apply(job: Job, made: dict) -> dict:
        i, result = job.input, made["result"]
        scene = _lock_scene(job.scene_id)
        if scene is None:
            raise jobs.JobFailed("scene_changed", VOICE_CHANGED)
        if scene.narration != i["narration"] or scene.mood != i["mood"]:
            raise jobs.JobFailed("scene_changed", VOICE_CHANGED)
        asset = Asset(
            user_id=job.user_id,
            project_id=job.project_id,
            kind="voice",
            s3_key=made["key"],
            content_type=result.content_type,
            size_bytes=len(result.audio),
            duration_seconds=result.duration,
            provider=made["provider"],
            input_hash=voice_input_hash(i["narration"], i["mood"], i["voiceId"]),
            meta={**result.meta, "estimatedTimings": result.estimated_timings},
        )
        db.session.add(asset)
        db.session.flush()
        old = scene.voice_asset_id
        scene.voice_asset_id = asset.id
        scene.words = result.words
        scene.words_edited = False
        scene.voice_status = "ready"
        scene.last_error = None
        db.session.flush()
        release_assets([old])
        db.session.expire(scene, ["voice_asset"])
        project = db.session.get(Project, job.project_id)
        if project is not None:
            after_edit(project)
            _advance(project.id, finishing=job.id)
        return {
            "voice": {"assetId": str(asset.id), "durationSeconds": result.duration},
            "estimatedTimings": result.estimated_timings,
        }

    @staticmethod
    def discard(job: Job, made: dict) -> None:  # noqa: ARG004
        try:
            get_storage().delete(made["key"])
        except Exception:  # noqa: BLE001 - left for the clean-up of unused files (B8)
            log.warning("could not delete an unused voice file", exc_info=True)

    @staticmethod
    def on_stop(job: Job) -> None:
        """A failed voice shows as failed with the reason; a cancelled or outdated one goes
        back to what the scene needs next. Then the project's status is worked out again."""
        scene = _lock_scene(job.scene_id)
        if scene is None or scene.voice_status not in ("queued", "running"):
            return
        changed = scene.narration != job.input["narration"] or scene.mood != job.input["mood"]
        if job.error_code == "scene_changed":
            scene.voice_status = "outdated" if scene.voice_asset_id else "missing"
        elif job.state == "failed":
            scene.voice_status, scene.last_error = "failed", job.error_message
        else:
            previous = job.input.get("previousStatus", "missing")
            if previous in ("queued", "running") or (previous == "ready" and changed):
                previous = "outdated" if scene.voice_asset_id else "missing"
            scene.voice_status = previous
        project = db.session.get(Project, scene.project_id)
        if project is not None:
            after_edit(project)
            _advance(project.id)


def _advance(project_id, finishing=None) -> None:
    from .reel import advance_project

    advance_project(project_id, finishing)
