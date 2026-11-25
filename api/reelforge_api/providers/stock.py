"""Stock video providers (technical design 9.0). Pixabay for now; Pexels goes in front of it
once Pexels issues API keys again (decided 2026-11-25). Built on the prototype in `pipeline/`.

Pixabay's terms, which the callers follow: search results are kept for 24 hours, clips are
downloaded to our own storage (never linked to), and stock search shows where results come
from. Thumbnails and previews may be shown straight from Pixabay while searching.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass

import requests

from ..config import Settings
from .base import USER_AGENT, NotConfigured, ProviderError, http, json_of

PER_PAGE = 20
MAX_CLIP_BYTES = 150 * 1024 * 1024
# The vertical frame is 1080 pixels wide and 1920 high. A landscape clip is cropped to fill
# it, so its height matters most: the smallest rendition at least this high is downloaded.
TARGET_HEIGHT = 1080


@dataclass
class StockClip:
    provider_id: str  # "pixabay:12345"
    duration: float
    width: int
    height: int
    author: str
    page_url: str
    thumbnail_url: str  # for search results only
    preview_url: str  # a small rendition, for search results only
    download_url: str
    download_size: int
    download_width: int
    download_height: int
    tags: str = ""

    def json(self) -> dict:
        """The shape API C9 returns for one result."""
        return {
            "providerId": self.provider_id,
            "thumbnailUrl": self.thumbnail_url,
            "previewUrl": self.preview_url,
            "durationSeconds": self.duration,
            "width": self.width,
            "height": self.height,
            "author": self.author,
            "source": self.provider_id.split(":", 1)[0],
            "pageUrl": self.page_url,
        }


def _pick_rendition(videos: dict) -> dict | None:
    renditions = [
        v for v in videos.values()
        if isinstance(v, dict) and v.get("url") and v.get("height") and (v.get("size") or 0) <= MAX_CLIP_BYTES
    ]  # fmt: skip
    if not renditions:
        return None
    tall_enough = [v for v in renditions if v["height"] >= TARGET_HEIGHT]
    if tall_enough:
        return min(tall_enough, key=lambda v: (v["height"], v.get("size") or 0))
    return max(renditions, key=lambda v: v["height"])


class PixabayStock:
    name = "pixabay"
    URL = "https://pixabay.com/api/videos/"

    def __init__(self, s: Settings):
        self.key = s.pixabay_api_key

    def _clip(self, hit: dict) -> StockClip | None:
        videos = hit.get("videos") or {}
        best = _pick_rendition(videos)
        if best is None or not hit.get("id"):
            return None
        small = videos.get("tiny") or videos.get("small") or best
        biggest = max(
            (v for v in videos.values() if isinstance(v, dict) and v.get("height")), key=lambda v: v["height"]
        )
        return StockClip(
            provider_id=f"pixabay:{hit['id']}",
            duration=float(hit.get("duration") or 0),
            width=int(biggest.get("width") or 0),
            height=int(biggest.get("height") or 0),
            author=str(hit.get("user") or ""),
            page_url=str(hit.get("pageURL") or ""),
            thumbnail_url=str(small.get("thumbnail") or best.get("thumbnail") or ""),
            preview_url=str(small.get("url") or ""),
            download_url=best["url"],
            download_size=int(best.get("size") or 0),
            download_width=int(best.get("width") or 0),
            download_height=int(best["height"]),
            tags=str(hit.get("tags") or ""),
        )

    def _get(self, params: dict) -> dict:
        if not self.key:
            raise NotConfigured("PIXABAY_API_KEY is not set")
        return json_of(
            http("GET", self.URL, params={"key": self.key, "safesearch": "true", **params}, timeout=15)
        )

    def search(self, query: str, page: int = 1) -> tuple[list[StockClip], bool]:
        """One page of results, and whether there is a next page."""
        data = self._get({"q": query[:100], "page": page, "per_page": PER_PAGE})
        hits = data.get("hits")
        if not isinstance(hits, list):
            raise ProviderError("Pixabay sent no results list", retryable=True)
        clips = [c for c in (self._clip(h) for h in hits if isinstance(h, dict)) if c]
        total = int(data.get("totalHits") or 0)
        return clips, page * PER_PAGE < min(total, 500)

    def get(self, clip_id: str) -> StockClip | None:
        data = self._get({"id": clip_id})
        hits = data.get("hits") or []
        return self._clip(hits[0]) if hits and isinstance(hits[0], dict) else None


class MockStock:
    """Offline stand-in. A query containing "[no-stock]" finds nothing (to exercise the
    AI-image fallback) and "[stock-fail]" fails the call."""

    name = "mock"

    def __init__(self, s: Settings):
        pass

    def _clip(self, n: int, query: str) -> StockClip:
        return StockClip(
            provider_id=f"mock:{n}", duration=float(4 + n * 3), width=1920, height=1080,
            author=f"mock author {n}", page_url=f"https://example.com/clip/{n}",
            thumbnail_url=f"https://example.com/clip/{n}.jpg", preview_url=f"https://example.com/clip/{n}.mp4",
            download_url=f"mock://{n}", download_size=2048, download_width=1920, download_height=1080,
            tags=query,
        )  # fmt: skip

    def search(self, query: str, page: int = 1) -> tuple[list[StockClip], bool]:
        if "[stock-fail]" in query:
            raise ProviderError("mock stock failure")
        if "[no-stock]" in query:
            return [], False
        start = (page - 1) * 5 + 1
        return [self._clip(n, query) for n in range(start, start + 5)], page < 3

    def get(self, clip_id: str) -> StockClip | None:
        return self._clip(int(clip_id), "") if clip_id.isdigit() and 0 < int(clip_id) <= 15 else None


def download(clip: StockClip):
    """The clip's file in a temporary file (in memory up to 16 MB, then on disk), checked to
    be an MP4 and no larger than the limit. The caller closes it."""
    out = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    if clip.download_url.startswith("mock://"):
        out.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2036)  # an MP4 header; enough for tests
        out.seek(0)
        return out
    try:
        with requests.get(
            clip.download_url,
            stream=True,
            timeout=(10, 30),
            allow_redirects=False,  # a redirect could point anywhere (QB4S2-005)
            headers={"User-Agent": USER_AGENT},
        ) as resp:
            if resp.status_code != 200:
                raise ProviderError(
                    f"stock download failed {resp.status_code}", retryable=resp.status_code >= 500
                )
            size = 0
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                size += len(chunk)
                if size > MAX_CLIP_BYTES:
                    raise ProviderError("the stock clip is too large")
                out.write(chunk)
    except requests.RequestException as e:
        out.close()
        raise ProviderError(f"stock download failed: {type(e).__name__}", retryable=True) from None
    except ProviderError:
        out.close()
        raise
    out.seek(0)
    head = out.read(12)
    out.seek(0)
    if head[4:8] != b"ftyp":
        out.close()
        raise ProviderError("the stock clip is not an MP4 file")
    return out


PROVIDERS = {"pixabay": PixabayStock, "mock": MockStock}
