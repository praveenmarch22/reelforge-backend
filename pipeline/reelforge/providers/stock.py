"""Stock footage providers. Return None when nothing suitable is found."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..config import Settings
from ..errors import NotConfigured, http
from ..media import download
from ..models import VisualResult


class PexelsStock:
    name = "pexels"

    def __init__(self, s: Settings):
        self.key = s.pexels_api_key

    def find_video(self, query: str, min_duration: float, out_path: Path) -> Optional[VisualResult]:
        if not self.key:
            raise NotConfigured("PEXELS_API_KEY is empty")
        data = http(
            "GET", "https://api.pexels.com/videos/search",
            params={"query": query, "orientation": "portrait", "per_page": 15, "size": "medium"},
            headers={"Authorization": self.key},
        ).json()
        best = None
        for video in data.get("videos", []):
            files = [
                f for f in video.get("video_files", [])
                if f.get("file_type") == "video/mp4" and f.get("height") and f.get("width")
                and f["height"] > f["width"] and 1280 <= f["height"] <= 2160
            ]
            if not files:
                continue
            file = min(files, key=lambda f: abs(f["height"] - 1920))
            duration = float(video.get("duration") or 0)
            # Prefer clips long enough for the scene; among those, the shortest download.
            score = (0 if duration >= min_duration else 1, abs(file["height"] - 1920), duration)
            if best is None or score < best[0]:
                best = (score, file, duration)
        if best is None:
            return None
        _, file, duration = best
        out_path = out_path.with_suffix(".mp4")
        download(file["link"], out_path)
        return VisualResult(kind="video", path=str(out_path), duration=duration or None, provider=self.name)


class MockStock:
    """Offline stand-in: never finds anything, which exercises the AI-image fallback."""

    name = "mock"

    def __init__(self, s: Settings):
        pass

    def find_video(self, query: str, min_duration: float, out_path: Path) -> Optional[VisualResult]:
        return None
