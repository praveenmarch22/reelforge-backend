"""Provider registry: turns the names in .env into provider objects, in fallback order."""

from __future__ import annotations

from ..config import Settings
from . import image, llm, music, stock, video, voice

_REGISTRY = {
    "llm": {"gemini": llm.GeminiLLM, "groq": llm.GroqLLM, "mock": llm.MockLLM},
    "voice": {"elevenlabs": voice.ElevenLabsVoice, "edge": voice.EdgeVoice, "mock": voice.MockVoice},
    "stock": {"pexels": stock.PexelsStock, "mock": stock.MockStock},
    "image": {"cloudflare": image.CloudflareImage, "mock": image.MockImage},
    "video": {"hf_space": video.HuggingFaceSpaceVideo},
    "music": {"library": music.LibraryMusic, "mock": music.MockMusic},
}


def build(kind: str, names: list[str], settings: Settings) -> list:
    table = _REGISTRY[kind]
    unknown = [n for n in names if n not in table]
    if unknown:
        raise ValueError(f"Unknown {kind} provider(s): {unknown}. Choose from {sorted(table)}")
    return [table[n](settings) for n in names]
