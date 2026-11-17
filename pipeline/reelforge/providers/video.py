"""AI video providers (image-to-video)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..config import Settings
from ..errors import NotConfigured, ProviderError
from ..media import media_duration
from ..models import VisualResult


def _find_video_path(result) -> str | None:
    """Gradio Spaces return a path, a dict, or a tuple; find the first video file in it."""
    if isinstance(result, str):
        return result if result.lower().endswith((".mp4", ".webm", ".mov")) else None
    if isinstance(result, dict):
        for key in ("video", "path", "name", "value"):
            if key in result:
                found = _find_video_path(result[key])
                if found:
                    return found
        return None
    if isinstance(result, (list, tuple)):
        for item in result:
            found = _find_video_path(item)
            if found:
                return found
    return None


class HuggingFaceSpaceVideo:
    """Calls any Hugging Face Space that turns an image and a prompt into a clip.

    Free Spaces are queued and change often, so the Space name, endpoint and
    argument names are all set in .env (see .env.example).
    """

    name = "hf_space"

    def __init__(self, s: Settings):
        self.space, self.api_name, self.token = s.hf_video_space, s.hf_video_api_name, s.hf_token
        self.args_template = s.hf_video_args

    def generate(self, prompt: str, image_path: Path, duration: float, seed: int, out_path: Path) -> VisualResult:
        if not self.space:
            raise NotConfigured("HF_VIDEO_SPACE is empty")
        try:
            from gradio_client import Client, handle_file
        except ImportError as e:
            raise NotConfigured("gradio_client is not installed (pip install gradio_client)") from e

        try:
            template = json.loads(self.args_template)
        except json.JSONDecodeError as e:
            raise NotConfigured(f"HF_VIDEO_ARGS is not valid JSON: {e}") from e

        def fill(value):
            if value == "{image}":
                return handle_file(str(image_path))
            if value == "{seed}":
                return seed
            if value == "{duration}":
                return duration
            if isinstance(value, str):
                return value.replace("{prompt}", prompt)
            return value

        kwargs = {k: fill(v) for k, v in template.items()}
        try:
            client = Client(self.space, hf_token=self.token or None)
            result = client.predict(**kwargs, api_name=self.api_name)
        except Exception as e:  # queue full, Space sleeping, quota, timeouts
            raise ProviderError(f"Hugging Face Space failed: {e}", retryable=True) from e

        src = _find_video_path(result)
        if not src:
            raise ProviderError(f"Space returned no video: {str(result)[:300]}")
        out_path = out_path.with_suffix(Path(src).suffix or ".mp4")
        shutil.copyfile(src, out_path)
        return VisualResult(kind="video", path=str(out_path), duration=media_duration(out_path), provider=self.name)
