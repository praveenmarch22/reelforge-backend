"""The voices creators can choose from (API D3, PRD V2).

A short, curated list of ElevenLabs' ready-made voices that suit Reels. Each one has a
similar free Edge voice, used when ElevenLabs fails or the month's free characters run out,
so a Reel always gets a voice of the same kind.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from flask import current_app

from .providers.base import ProviderError, http, json_of

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Voice:
    id: str  # the ElevenLabs voice id
    name: str
    gender: str
    accent: str
    style: str
    edge: str  # the Edge TTS voice used as the backup


CATALOGUE: tuple[Voice, ...] = (
    Voice("TX3LPaxmHKxFdv7VOQHJ", "Liam", "Male", "American", "Energetic", "en-US-BrianNeural"),
    Voice("FGY2WhTYpPnrIDTdsKH5", "Laura", "Female", "American", "Upbeat", "en-US-EmmaNeural"),
    Voice("EXAVITQu4vr4xnSDxMaL", "Sarah", "Female", "American", "Confident", "en-US-AvaNeural"),
    Voice("JBFqnCBsd6RMkjVDRZzb", "George", "Male", "British", "Storyteller", "en-GB-RyanNeural"),
    Voice("Xb7hH8MSUJpSbSDYk0k2", "Alice", "Female", "British", "Clear", "en-GB-SoniaNeural"),
    Voice("nPczCjzI2devNBz1zQrb", "Brian", "Male", "American", "Deep", "en-US-ChristopherNeural"),
    Voice("cgSgspJ2msm6clMCkdW9", "Jessica", "Female", "American", "Playful", "en-US-JennyNeural"),
    Voice("IKne3meq5aSn9XLyUdCD", "Charlie", "Male", "Australian", "Energetic", "en-AU-WilliamNeural"),
)
BY_ID = {voice.id: voice for voice in CATALOGUE}
DEFAULT = CATALOGUE[0]

PREVIEWS_KEY = "voices:previews"
PREVIEWS_SECONDS = 24 * 3600
FAILED_PREVIEWS_SECONDS = 300


def is_known(voice_id: str) -> bool:
    return voice_id in BY_ID


def resolve(*choices: str) -> Voice:
    """The first known voice among the choices (scene request, project, account default),
    or the default voice. A voice that left the catalogue falls back instead of failing."""
    for choice in choices:
        if choice in BY_ID:
            return BY_ID[choice]
    return DEFAULT


def _previews() -> dict[str, str]:
    """ElevenLabs' sample clip for each voice, asked for once a day and kept in Redis.
    Without a key, or if ElevenLabs can't be reached, the list is served without samples."""
    redis = current_app.extensions["redis"]
    try:
        cached = redis.get(PREVIEWS_KEY)
        if cached is not None:
            return json.loads(cached)
    except Exception:  # noqa: BLE001 - the list still works without Redis
        log.warning("could not read cached voice previews", exc_info=True)

    key = current_app.extensions["settings"].elevenlabs_api_key
    if not key:
        return {}
    try:
        data = json_of(
            http("GET", "https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}, timeout=10)
        )
        if not isinstance(data.get("voices"), list):
            raise TypeError("ElevenLabs sent no voice list")  # counts as a failed lookup (BUG-041)
        previews = {
            v["voice_id"]: v["preview_url"]
            for v in data.get("voices", [])
            if isinstance(v, dict)
            and v.get("voice_id") in BY_ID
            and str(v.get("preview_url", "")).startswith("https://")
        }
    except (ProviderError, KeyError, TypeError):
        log.warning("could not fetch voice previews", exc_info=True)
        # Remembered briefly, so a list request doesn't wait on ElevenLabs again and again
        # while it is down (QB4-006).
        try:
            redis.set(PREVIEWS_KEY, "{}", ex=FAILED_PREVIEWS_SECONDS)
        except Exception:  # noqa: BLE001
            log.warning("could not cache the failed preview lookup", exc_info=True)
        return {}
    try:
        redis.set(PREVIEWS_KEY, json.dumps(previews), ex=PREVIEWS_SECONDS)
    except Exception:  # noqa: BLE001
        log.warning("could not cache voice previews", exc_info=True)
    return previews


def catalogue_json() -> list[dict]:
    previews = _previews()
    return [
        {
            "id": v.id,
            "name": v.name,
            "provider": "elevenlabs",
            "gender": v.gender,
            "accent": v.accent,
            "style": v.style,
            "previewUrl": previews.get(v.id, ""),
        }
        for v in CATALOGUE
    ]
