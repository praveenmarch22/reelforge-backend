"""Product rules for scripts, scenes and credits, in one place (PRD 5.2, 5.10)."""

from __future__ import annotations

import math

MOODS = (
    "calm", "joyful", "excited", "funny", "inspiring", "triumphant", "serious", "tense", "mysterious", "sad",
)  # fmt: skip
TONES = ("educational", "funny", "motivational", "storytelling", "dramatic")
MODES = ("faceless", "full_ai")
TARGET_SECONDS = (15, 30, 60, 90)
VISUAL_SOURCES = ("stock", "ai_image", "ai_video", "upload")
PROJECT_STATUSES = (
    "scripting", "draft", "generating", "needs_attention", "ready", "rendering",
    "render_failed", "rendered", "posting", "post_failed", "posted",
)  # fmt: skip
CAPTION_STYLES = ("bold-pop", "clean", "neon")
TRANSITIONS = ("cut", "fade", "slide")

WORDS_PER_SECOND = 2.5
MAX_SECONDS = 90
TOO_SHORT_SECONDS = 5

# Limits on what the creator (or the model) can put in.
TITLE_MAX = 120
TOPIC_MAX = 300
SCRIPT_MAX = 5000
NARRATION_MAX = 1500
ON_SCREEN_TEXT_MAX = 80
PROMPT_MAX = 1000
STOCK_QUERY_MAX = 100
STYLE_MAX = 100
CHARACTER_MAX = 300
INSTRUCTIONS_MAX = 200
POST_CAPTION_MAX = 2200
HASHTAGS_MAX = 30
HASHTAG_MAX = 100
SCENES_MAX = 30
IDEAS_MAX = 10

DEFAULT_SETTINGS = {
    "captionStyle": "bold-pop",
    "showCaptions": True,
    "voiceVolume": 1.0,
    "musicVolume": 0.25,
    "duckedVolume": 0.08,
    "ducking": True,
    "transition": "cut",
}

# Credit costs (PRD 5.10). The web app reads them from GET /credits, never hard-codes them.
COSTS = {
    "script": 1,
    "voicePer30s": 2,
    # Redoing one scene's voice: 1 credit for a scene up to 15 seconds, else voicePer30s.
    "voiceSceneShort": 1,
    "aiImage": 1,
    "aiVideoClip": 10,
    "render": 2,
    "transcribe": 1,
    # Free or same-priced actions, listed so the web app never has to guess.
    "ideas": 0,
    "hooks": 0,
    "rewrite": 1,
}
JOB_COSTS = {
    "ideas": COSTS["ideas"],
    "script": COSTS["script"],
    "rewrite_scene": COSTS["rewrite"],
    "hooks": COSTS["hooks"],
}


def normalise_mood(value: object) -> str:
    value = str(value or "").strip().lower()
    return value if value in MOODS else "calm"


def word_count(text: str) -> int:
    return len(text.split())


def estimated_seconds(narration: str) -> float:
    """Spoken length estimate: 2.5 words per second (PRD S4, S7)."""
    return round(word_count(narration) / WORDS_PER_SECOND, 1)


def voice_credits(seconds: float) -> int:
    """2 credits per started 30 seconds of voiceover. Generate charges the whole Reel's
    narration at once, so short scenes are not each rounded up (decided 2026-11-25)."""
    return COSTS["voicePer30s"] * max(1, math.ceil(seconds / 30))


def scene_voice_credits(narration: str) -> int:
    """C7, redoing one scene's voice: 1 credit up to 15 seconds, else 2 per started 30."""
    seconds = estimated_seconds(narration)
    return COSTS["voiceSceneShort"] if seconds <= 15 else voice_credits(seconds)


def visual_credits(source: str) -> int:
    return {"ai_image": COSTS["aiImage"], "ai_video": COSTS["aiVideoClip"]}.get(source, 0)


def default_visual_source(mode: str) -> str:
    return "ai_video" if mode == "full_ai" else "stock"
