"""Settings loaded from environment variables (and pipeline/.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PIPELINE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = PIPELINE_DIR.parent
load_dotenv(PIPELINE_DIR / ".env")


def _list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class Settings:
    llm_providers: list[str] = field(default_factory=lambda: _list("LLM_PROVIDERS", "gemini,groq"))
    voice_providers: list[str] = field(default_factory=lambda: _list("VOICE_PROVIDERS", "elevenlabs,edge"))
    stock_providers: list[str] = field(default_factory=lambda: _list("STOCK_PROVIDERS", "pexels"))
    image_providers: list[str] = field(default_factory=lambda: _list("IMAGE_PROVIDERS", "cloudflare"))
    video_providers: list[str] = field(default_factory=lambda: _list("VIDEO_PROVIDERS", ""))
    music_providers: list[str] = field(default_factory=lambda: _list("MUSIC_PROVIDERS", "library,mock"))

    gemini_api_key: str = field(default_factory=lambda: _str("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _str("GEMINI_MODEL", "gemini-2.5-flash"))
    groq_api_key: str = field(default_factory=lambda: _str("GROQ_API_KEY"))
    groq_model: str = field(default_factory=lambda: _str("GROQ_MODEL", "llama-3.3-70b-versatile"))

    elevenlabs_api_key: str = field(default_factory=lambda: _str("ELEVENLABS_API_KEY"))
    elevenlabs_voice_id: str = field(default_factory=lambda: _str("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"))
    elevenlabs_model: str = field(default_factory=lambda: _str("ELEVENLABS_MODEL", "eleven_multilingual_v2"))
    edge_voice: str = field(default_factory=lambda: _str("EDGE_VOICE", "en-US-AriaNeural"))

    pexels_api_key: str = field(default_factory=lambda: _str("PEXELS_API_KEY"))

    cloudflare_account_id: str = field(default_factory=lambda: _str("CLOUDFLARE_ACCOUNT_ID"))
    cloudflare_api_token: str = field(default_factory=lambda: _str("CLOUDFLARE_API_TOKEN"))
    cloudflare_image_model: str = field(
        default_factory=lambda: _str("CLOUDFLARE_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
    )

    hf_token: str = field(default_factory=lambda: _str("HF_TOKEN"))
    hf_video_space: str = field(default_factory=lambda: _str("HF_VIDEO_SPACE"))
    hf_video_api_name: str = field(default_factory=lambda: _str("HF_VIDEO_API_NAME", "/predict"))
    hf_video_args: str = field(default_factory=lambda: _str("HF_VIDEO_ARGS", '{"prompt": "{prompt}", "image": "{image}"}'))

    concurrency: int = field(default_factory=lambda: int(_str("PROVIDER_CONCURRENCY", "2") or 2))
    seed: int = field(default_factory=lambda: int(_str("SEED", "42") or 42))

    renderer_dir: Path = field(default_factory=lambda: Path(_str("RENDERER_DIR") or REPO_DIR / "renderer"))

    @property
    def public_dir(self) -> Path:
        return self.renderer_dir / "public"

    @property
    def projects_dir(self) -> Path:
        return self.public_dir / "projects"

    def use_mocks_everywhere(self) -> None:
        """Switch every step to offline placeholder providers (for testing)."""
        self.llm_providers = ["mock"]
        self.voice_providers = ["mock"]
        self.stock_providers = ["mock"]
        self.image_providers = ["mock"]
        self.video_providers = []
        self.music_providers = ["mock"]
