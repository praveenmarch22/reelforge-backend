"""Small media helpers: durations, word-time estimates, downloads, placeholder images."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Optional

from .errors import ProviderError, http
from .models import Word


def audio_duration(path: Path) -> float:
    from mutagen import File as MutagenFile

    info = MutagenFile(str(path))
    if info is None or not getattr(info, "info", None):
        raise ProviderError(f"could not read audio length of {path.name}")
    return float(info.info.length)


def media_duration(path: Path) -> Optional[float]:
    """Length of an audio or video file, or None if it can't be read."""
    try:
        return audio_duration(path)
    except Exception:
        return None


def estimate_words(text: str, duration: float) -> list[Word]:
    """Spread words across the duration, weighted by word length."""
    tokens = text.split()
    if not tokens:
        return []
    weights = [len(t) + 2 for t in tokens]
    total = sum(weights)
    words, t = [], 0.0
    for token, w in zip(tokens, weights):
        span = duration * w / total
        words.append(Word(text=token, start=round(t, 3), end=round(t + span * 0.92, 3)))
        t += span
    return words


def download(url: str, out_path: Path, headers: Optional[dict] = None) -> Path:
    resp = http("GET", url, stream=True, timeout=180, headers=headers or {})
    with open(out_path, "wb") as f:
        for chunk in resp.iter_content(1 << 16):
            f.write(chunk)
    return out_path


MOOD_COLOURS = {
    "calm": ((40, 90, 140), (10, 30, 60)), "joyful": ((250, 180, 60), (220, 90, 60)),
    "excited": ((255, 80, 120), (120, 40, 200)), "funny": ((255, 210, 0), (255, 120, 0)),
    "inspiring": ((60, 160, 255), (140, 60, 220)), "triumphant": ((255, 190, 40), (180, 60, 20)),
    "serious": ((70, 70, 80), (20, 20, 30)), "tense": ((140, 20, 30), (20, 10, 20)),
    "mysterious": ((60, 30, 110), (10, 10, 40)), "sad": ((70, 90, 120), (30, 35, 50)),
}


def placeholder_image(text: str, mood: str, out_path: Path, size=(1080, 1920)) -> Path:
    """A gradient image with the prompt written on it, for offline testing."""
    from PIL import Image, ImageDraw, ImageFont

    top, bottom = MOOD_COLOURS.get(mood, MOOD_COLOURS["calm"])
    w, h = size
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        k = y / (h - 1)
        draw.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(top, bottom)))
    try:
        font = ImageFont.truetype("arial.ttf", 56)
    except OSError:
        font = ImageFont.load_default()
    lines = textwrap.wrap(f"[placeholder] {text}", width=28)[:8]
    y = h * 0.36  # below the on-screen headline, above the captions
    for line in lines:
        draw.text((80, y), line, fill=(255, 255, 255), font=font)
        y += 72
    img.save(out_path, "JPEG", quality=88)
    return out_path
