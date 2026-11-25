"""Request bodies for ideas, projects, scenes and jobs (API design groups B, C, G)."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field, model_validator

from .errors import ApiError
from .schemas import Body, _clean_text, _storable, _voice_id
from .script_rules import (
    CHARACTER_MAX,
    HASHTAG_MAX,
    HASHTAGS_MAX,
    IDEAS_MAX,
    INSTRUCTIONS_MAX,
    NARRATION_MAX,
    ON_SCREEN_TEXT_MAX,
    POST_CAPTION_MAX,
    PROMPT_MAX,
    SCRIPT_MAX,
    STOCK_QUERY_MAX,
    STYLE_MAX,
    TITLE_MAX,
    TOPIC_MAX,
)


def _line(limit: int, *, required: bool = False):
    """One line of text: trimmed, no line breaks or broken characters, at most `limit` characters."""

    def check(value: str) -> str:
        value = _clean_text(value)
        if required and not value:
            raise ValueError("Required")
        if len(value) > limit:
            raise ValueError(f"Use {limit} characters or fewer")
        return value

    return AfterValidator(check)


def _text(limit: int, *, required: bool = False):
    """Text that may have line breaks (a pasted script, a caption)."""

    def check(value: str) -> str:
        _storable(value)
        value = value.replace("\r\n", "\n").strip()
        if any(ch < " " and ch not in "\n\t" for ch in value):
            raise ValueError("Remove special characters")
        if required and not value:
            raise ValueError("Required")
        if len(value) > limit:
            raise ValueError(f"Use {limit} characters or fewer")
        return value

    return AfterValidator(check)


def _hashtags(value: list[str]) -> list[str]:
    tags: list[str] = []
    for raw in value:
        tag = _clean_text(raw).lstrip("#")
        if not tag:
            continue
        if any(ch.isspace() for ch in tag) or "#" in tag:
            raise ValueError("A hashtag is one word, without spaces")
        if len(tag) > HASHTAG_MAX:
            raise ValueError(f"A hashtag can have up to {HASHTAG_MAX} characters")
        if tag.casefold() in {t.casefold() for t in tags}:
            continue
        tags.append(tag)
    if len(tags) > HASHTAGS_MAX:
        raise ValueError(f"Use up to {HASHTAGS_MAX} hashtags")
    return tags


Mood = Literal[
    "calm", "joyful", "excited", "funny", "inspiring", "triumphant", "serious", "tense", "mysterious", "sad"
]
Tone = Literal["educational", "funny", "motivational", "storytelling", "dramatic"]
Mode = Literal["faceless", "full_ai"]
VisualSource = Literal["stock", "ai_image", "ai_video", "upload"]
Seconds = Annotated[float, Field(ge=0, le=600, allow_inf_nan=False)]
# Ids arrive as JSON strings; the bodies are strict otherwise.
Id = Annotated[uuid.UUID, Field(strict=False)]


class IdeasBody(Body):
    niche: Annotated[str, _line(120, required=True)]
    keywords: Annotated[str, _line(200)] = ""
    count: Annotated[int, Field(ge=1, le=IDEAS_MAX)] = 5


class CreateProjectBody(Body):
    topic: Annotated[str, _line(TOPIC_MAX, required=True)] | None = None
    script: Annotated[str, _text(SCRIPT_MAX, required=True)] | None = None
    tone: Tone = "educational"
    mode: Mode | None = None
    target_seconds: Literal[15, 30, 60, 90] = 30
    style: Annotated[str, _line(STYLE_MAX)] = "cinematic"
    character: Annotated[str, _line(CHARACTER_MAX)] = ""
    voice_id: Annotated[str, AfterValidator(_voice_id)] | None = None


class SettingsBody(Body):
    caption_style: Literal["bold-pop", "clean", "neon"] | None = None
    show_captions: bool | None = None
    voice_volume: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = None
    music_volume: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = None
    ducked_volume: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = None
    ducking: bool | None = None
    transition: Literal["cut", "fade", "slide"] | None = None


class MusicChoice(Body):
    """{"trackId": …} for a library track, or {"assetId": …} for uploaded music."""

    track_id: Id | None = None
    asset_id: Id | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> MusicChoice:
        if (self.track_id is None) == (self.asset_id is None):
            raise ValueError("Choose either a track or an uploaded file")
        return self


class UpdateProjectBody(Body):
    title: Annotated[str, _line(TITLE_MAX, required=True)] | None = None
    tone: Tone | None = None
    style: Annotated[str, _line(STYLE_MAX)] | None = None
    character: Annotated[str, _line(CHARACTER_MAX)] | None = None
    voice_id: Annotated[str, AfterValidator(_voice_id)] | None = None
    music_mood: Mood | None = None
    # Absent = unchanged; null = no music chosen (the library picks one by mood, B4).
    music: MusicChoice | None = Field(default=None)
    settings: SettingsBody | None = None
    post_caption: Annotated[str, _text(POST_CAPTION_MAX)] | None = None
    hashtags: Annotated[list[str], Field(max_length=100), AfterValidator(_hashtags)] | None = None


class InstructionsBody(Body):
    instructions: Annotated[str, _line(INSTRUCTIONS_MAX)] = ""


class RewriteBody(Body):
    instructions: Annotated[str, _line(INSTRUCTIONS_MAX, required=True)]


class VoiceRegenerateBody(Body):
    """C7. `voiceId` applies to this scene's new voice only; `confirmReplaceWords` is needed
    when the creator fixed caption words by hand (CA-4)."""

    voice_id: Annotated[str, AfterValidator(_voice_id)] | None = None
    confirm_replace_words: bool = False


class SceneFields(Body):
    on_screen_text: Annotated[str, _line(ON_SCREEN_TEXT_MAX)] | None = None
    visual_prompt: Annotated[str, _line(PROMPT_MAX)] | None = None
    stock_query: Annotated[str, _line(STOCK_QUERY_MAX)] | None = None
    mood: Mood | None = None
    visual_source: VisualSource | None = None


class VisualRegenerateBody(Body):
    """C8. Changes sent here are saved to the scene first, as with C2."""

    visual_source: Literal["stock", "ai_image", "ai_video"] | None = None
    visual_prompt: Annotated[str, _line(PROMPT_MAX)] | None = None
    stock_query: Annotated[str, _line(STOCK_QUERY_MAX)] | None = None


class SelectVisualBody(Body):
    """C10: a stock clip from C9 (`"pixabay:12345"`) or a finished upload, exactly one."""

    stock: Annotated[str, _line(60, required=True)] | None = None
    asset_id: Annotated[str, _line(60, required=True)] | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.stock is None) == (self.asset_id is None):
            raise ValueError("Choose either a stock clip or an uploaded file")
        return self


class CreateSceneBody(SceneFields):
    narration: Annotated[str, _text(NARRATION_MAX, required=True)]
    position: Annotated[int, Field(ge=1)] | None = None


class WordEdit(Body):
    text: Annotated[str, _line(100, required=True)]


class UpdateSceneBody(SceneFields):
    narration: Annotated[str, _text(NARRATION_MAX, required=True)] | None = None
    words: Annotated[list[WordEdit], Field(max_length=2000)] | None = None
    trim_start: Seconds | None = None
    trim_end: Seconds | None = None


class GenerateBody(Body):
    """B11. `sceneIds` limits Generate to these scenes; without it, the whole Reel."""

    scene_ids: Annotated[list[Id], Field(min_length=1, max_length=200)] | None = None


class ReorderBody(Body):
    scene_ids: Annotated[list[Id], Field(min_length=1, max_length=200)]


# ---------------------------------------------------------------- list cursors


def encode_cursor(moment: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps([moment.isoformat(), str(row_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        moment, row_id = json.loads(raw)
        return datetime.fromisoformat(moment), uuid.UUID(row_id)
    except (ValueError, TypeError):
        raise ApiError(
            400, "validation_error", "That list position is not valid.", {"fields": {"cursor": "Not valid"}}
        ) from None


def read_limit(value: str | None, default: int = 20) -> int:
    if value is None:
        return default
    if not value.isdigit() or not 1 <= int(value) <= 100:
        raise ApiError(
            400, "validation_error", "Some details need another look.", {"fields": {"limit": "Use 1 to 100"}}
        )
    return int(value)


def present(body: Body) -> dict[str, Any]:
    """The fields the client actually sent (an explicit null counts as sent)."""
    return {name: getattr(body, name) for name in body.model_fields_set}
