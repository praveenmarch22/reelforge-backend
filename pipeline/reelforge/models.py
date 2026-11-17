"""Data shapes shared by every pipeline step."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

MOODS = [
    "calm", "joyful", "excited", "funny", "inspiring", "triumphant",
    "serious", "tense", "mysterious", "sad",
]
TONES = ["educational", "funny", "motivational", "storytelling", "dramatic"]
VisualSource = Literal["stock", "ai_image", "ai_video", "upload"]
Mode = Literal["faceless", "full_ai"]

WORDS_PER_SECOND = 2.5
MAX_SECONDS = 90


def normalise_mood(value: str) -> str:
    value = (value or "").strip().lower()
    return value if value in MOODS else "calm"


class Scene(BaseModel):
    id: str
    narration: str = Field(min_length=1)
    on_screen_text: str = ""
    visual_prompt: str = Field(min_length=1)
    stock_query: str = ""
    mood: str = "calm"
    visual_source: VisualSource = "stock"
    upload_path: Optional[str] = None

    @field_validator("mood")
    @classmethod
    def _mood(cls, v: str) -> str:
        return normalise_mood(v)


class Script(BaseModel):
    title: str
    topic: str
    tone: str = "educational"
    mode: Mode = "faceless"
    target_seconds: int = Field(default=30, ge=5, le=MAX_SECONDS)
    style: str = "cinematic"
    character: str = ""
    voice: str = ""  # provider-specific voice id; empty = provider default
    music_mood: str = "calm"
    post_caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    scenes: list[Scene] = Field(min_length=1)

    @field_validator("music_mood")
    @classmethod
    def _music_mood(cls, v: str) -> str:
        return normalise_mood(v)

    def word_count(self) -> int:
        return sum(len(s.narration.split()) for s in self.scenes)

    def estimated_seconds(self) -> float:
        return self.word_count() / WORDS_PER_SECOND


class Word(BaseModel):
    text: str
    start: float
    end: float


class VoiceResult(BaseModel):
    path: str
    duration: float
    words: list[Word]
    provider: str


class VisualResult(BaseModel):
    kind: Literal["image", "video"]
    path: str
    duration: Optional[float] = None
    provider: str


class MusicResult(BaseModel):
    path: str
    provider: str
    license: str = ""
