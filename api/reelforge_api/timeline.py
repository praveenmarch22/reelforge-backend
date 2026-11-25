"""The timeline: the one contract between the editor's preview, the renderer and the database
(pipeline strategy, "The timeline JSON"). Built from the prototype's `pipeline/reelforge/timeline.py`.

It is built from the scenes as they are now, so the preview shows every edit at once. A scene
whose voice or visual isn't ready has an empty `src` and the player shows its mood placeholder.
With `links=False` the files are named by their storage keys (what is saved with a render);
with `links=True` they are presigned links the browser can play (API B12).
"""

from __future__ import annotations

import math

from .generation import voice_input_hash
from .models import Asset, MusicTrack, Project
from .script_rules import DEFAULT_SETTINGS, MAX_SECONDS, estimated_seconds
from .storage import get_storage

FPS = 30
WIDTH, HEIGHT = 1080, 1920
SCENE_PADDING = 0.3  # seconds of breathing room after each scene's narration
MIN_SCENE = 1.5
MOTIONS = ("zoom-in", "pan-left", "zoom-out", "pan-right")
FADE_IN, FADE_OUT = 1.0, 1.5


def _src(asset: Asset | MusicTrack | None, links: bool) -> str:
    if asset is None:
        return ""
    return get_storage().presigned_get(asset.s3_key) if links else asset.s3_key


def usable_voice(scene) -> Asset | None:
    """The voice, if it says the scene's narration in its mood. It stays while a new take is
    made or after one failed, but not once the narration or mood changed: it would say the old
    words (QB4S3-003). A voice saves a hash of what it was made from."""
    voice = scene.voice_asset
    if voice is None or scene.voice_status == "outdated":
        return None
    made_for = voice_input_hash(scene.narration, scene.mood, (voice.meta or {}).get("voiceId", ""))
    return voice if voice.input_hash in (None, made_for) else None


def scene_length(scene) -> float:
    """Seconds on screen: the voice plus a short pause, or the estimate until there is one."""
    voice = usable_voice(scene)
    spoken = float(voice.duration_seconds) if voice is not None and voice.duration_seconds else None
    return max(
        (spoken if spoken is not None else estimated_seconds(scene.narration)) + SCENE_PADDING, MIN_SCENE
    )


def build(
    project: Project, music_track: MusicTrack | None, music_asset: Asset | None, *, links: bool
) -> dict:
    settings = {**DEFAULT_SETTINGS, **(project.settings or {})}
    scenes, frame = [], 0
    for index, scene in enumerate(sorted(project.scenes, key=lambda s: s.position)):
        frames = math.ceil(scene_length(scene) * FPS)
        visual = scene.visual_asset
        is_video = visual is not None and (
            visual.kind == "video_clip" or visual.content_type.startswith("video/")
        )
        voice = usable_voice(scene)
        scenes.append(
            {
                "id": str(scene.id),
                "startFrame": frame,
                "durationInFrames": frames,
                "mood": scene.mood,
                "onScreenText": scene.on_screen_text,
                "visual": {
                    "kind": "video" if is_video else "image",
                    "src": _src(visual, links),
                    "durationSeconds": visual.duration_seconds if visual is not None else None,
                    "motion": MOTIONS[index % len(MOTIONS)],
                    "trimStart": float(scene.trim_start or 0),
                    "trimEnd": float(scene.trim_end) if scene.trim_end is not None else None,
                },
                "voice": {
                    "src": _src(voice, links),
                    "durationSeconds": voice.duration_seconds if voice is not None else None,
                },
                "words": scene.words if voice is not None else [],
                "ready": scene.voice_status == "ready" and scene.visual_status == "ready",
            }
        )
        frame += frames

    music = music_asset or music_track
    volume = float(settings["musicVolume"])
    return {
        "version": 1,
        "projectId": str(project.id),
        "fps": FPS,
        "width": WIDTH,
        "height": HEIGHT,
        "durationInFrames": frame,
        "captionStyle": settings["captionStyle"],
        "showCaptions": bool(settings["showCaptions"]),
        "voiceVolume": float(settings["voiceVolume"]),
        "transition": settings["transition"],
        "music": None
        if music is None
        else {
            "src": _src(music, links),
            "volume": volume,
            # With lowering switched off, the music stays at its own volume under the voice.
            "duckedVolume": float(settings["duckedVolume"]) if settings["ducking"] else volume,
            "fadeInSeconds": FADE_IN,
            "fadeOutSeconds": FADE_OUT,
            # What actually plays, so the editor names it and the post credits it (QB4S3-006).
            "trackId": str(music.id) if music_asset is None else None,
            "assetId": str(music.id) if music_asset is not None else None,
            "title": getattr(music, "title", "") if music_asset is None else "",
            "license": getattr(music, "license", "") if music_asset is None else "",
        },
        "scenes": scenes,
    }


def seconds_of(timeline: dict) -> float:
    return round(timeline["durationInFrames"] / timeline["fps"], 2)


def too_long(timeline: dict) -> bool:
    return seconds_of(timeline) > MAX_SECONDS
