"""Builds the timeline JSON that the Remotion renderer plays."""

from __future__ import annotations

import math
from pathlib import Path

from .models import MAX_SECONDS, MusicResult, Script, VisualResult, VoiceResult

FPS = 30
WIDTH, HEIGHT = 1080, 1920
SCENE_PADDING = 0.3   # seconds of breathing room after each scene's narration
MIN_SCENE = 1.5       # seconds
MOTIONS = ["zoom-in", "pan-left", "zoom-out", "pan-right"]


class TimelineTooLong(Exception):
    pass


def _public_path(path: str, public_dir: Path) -> str:
    """Paths in the timeline are relative to renderer/public (or full URLs)."""
    if path.startswith(("http://", "https://")):
        return path
    return Path(path).resolve().relative_to(public_dir.resolve()).as_posix()


def build_timeline(
    project_id: str,
    script: Script,
    voices: dict[str, VoiceResult],
    visuals: dict[str, VisualResult],
    music: MusicResult | None,
    public_dir: Path,
    *,
    caption_style: str = "bold-pop",
    show_captions: bool = True,
) -> dict:
    scenes, frame = [], 0
    for i, scene in enumerate(script.scenes):
        voice, visual = voices[scene.id], visuals[scene.id]
        seconds = max(voice.duration + SCENE_PADDING, MIN_SCENE)
        frames = math.ceil(seconds * FPS)
        scenes.append({
            "id": scene.id,
            "startFrame": frame,
            "durationInFrames": frames,
            "mood": scene.mood,
            "onScreenText": scene.on_screen_text,
            "visual": {
                "kind": visual.kind,
                "src": _public_path(visual.path, public_dir),
                "durationSeconds": visual.duration,
                "motion": MOTIONS[i % len(MOTIONS)],
            },
            "voice": {"src": _public_path(voice.path, public_dir), "durationSeconds": round(voice.duration, 3)},
            "words": [w.model_dump() for w in voice.words],
        })
        frame += frames

    total_seconds = frame / FPS
    if total_seconds > MAX_SECONDS:
        raise TimelineTooLong(
            f"The Reel would be {total_seconds:.1f}s, over the {MAX_SECONDS}s limit. Shorten the script."
        )

    return {
        "version": 1,
        "projectId": project_id,
        "fps": FPS, "width": WIDTH, "height": HEIGHT,
        "durationInFrames": frame,
        "captionStyle": caption_style,
        "showCaptions": show_captions,
        "voiceVolume": 1.0,
        "music": None if music is None else {
            "src": _public_path(music.path, public_dir),
            "volume": 0.25, "duckedVolume": 0.08,
            "fadeInSeconds": 1.0, "fadeOutSeconds": 1.5,
        },
        "scenes": scenes,
    }
