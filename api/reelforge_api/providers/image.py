"""AI image providers (technical design 9.0): FLUX on Cloudflare Workers AI, and an offline
mock. Built on the prototype in `pipeline/`. An image is stored as a still; the renderer gives
it a slow zoom or pan so it feels like video (PRD VI6)."""

from __future__ import annotations

import base64
import binascii
import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from ..config import Settings
from .base import NotConfigured, ProviderError, http, json_of

TIMEOUT = 60
PROMPT_MAX = 2000

MOOD_VISUAL_CUES = {
    "calm": "soft natural light, gentle slow camera drift, peaceful expression",
    "joyful": "warm golden light, bright colours, smiling, lively movement",
    "excited": "vivid colours, dynamic fast camera push-in, energetic expression",
    "funny": "bright playful lighting, exaggerated expression, comedic timing",
    "inspiring": "sunrise light, wide hopeful framing, slow rising camera",
    "triumphant": "heroic low angle, dramatic backlight, confident expression",
    "serious": "neutral muted tones, steady camera, focused expression",
    "tense": "low-key dramatic lighting, slow push-in, tense worried expression",
    "mysterious": "fog, deep shadows, cool blue tones, slow reveal",
    "sad": "overcast cool light, desaturated colours, slow pull-back, melancholic expression",
}
MOOD_COLOURS = {
    "calm": ((40, 90, 120), (120, 170, 190)), "joyful": ((230, 150, 40), (250, 210, 120)),
    "excited": ((200, 30, 90), (250, 140, 40)), "funny": ((90, 180, 60), (240, 220, 70)),
    "inspiring": ((240, 120, 60), (120, 160, 230)), "triumphant": ((150, 90, 20), (240, 200, 80)),
    "serious": ((40, 45, 55), (110, 115, 125)), "tense": ((60, 10, 20), (150, 40, 40)),
    "mysterious": ((10, 20, 60), (60, 70, 130)), "sad": ((50, 60, 80), (120, 130, 150)),
}  # fmt: skip


def image_prompt(scene_prompt: str, mood: str, style: str, character: str) -> str:
    """The whole prompt: the scene, the mood's look, and the project's style anchor, the same
    for every scene so they look alike (pipeline strategy 4)."""
    anchor = f"{style or 'cinematic'} style"
    if character:
        anchor += f", featuring {character}"
    parts = [scene_prompt, MOOD_VISUAL_CUES.get(mood, ""), anchor,
             "vertical 9:16 composition, high detail, no text, no watermark"]  # fmt: skip
    return ", ".join(p.strip() for p in parts if p and p.strip())[:PROMPT_MAX]


@dataclass
class ImageResult:
    data: bytes
    content_type: str
    extension: str
    width: int
    height: int
    meta: dict


def checked_image(data: bytes) -> tuple[str, str, int, int]:
    """(content type, extension, width, height) of a JPEG or PNG; anything else is refused."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            fmt, (width, height) = img.format, img.size
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ProviderError("the image provider sent something that isn't an image", retryable=True) from None
    if fmt == "JPEG":
        return "image/jpeg", "jpg", width, height
    if fmt == "PNG":
        return "image/png", "png", width, height
    raise ProviderError(f"the image provider sent an unexpected format ({fmt})", retryable=True)


class CloudflareImage:
    """FLUX.1 schnell on Cloudflare Workers AI (daily free allowance)."""

    name = "cloudflare"

    def __init__(self, s: Settings):
        self.account, self.token, self.model = (
            s.cloudflare_account_id,
            s.cloudflare_api_token,
            s.cloudflare_image_model,
        )

    def generate(self, prompt: str, seed: int) -> ImageResult:
        if not (self.account and self.token):
            raise NotConfigured("CLOUDFLARE_ACCOUNT_ID or CLOUDFLARE_API_TOKEN is not set")
        data = json_of(
            http(
                "POST",
                f"https://api.cloudflare.com/client/v4/accounts/{self.account}/ai/run/{self.model}",
                # FLUX schnell on Workers AI takes no seed; every call gives a new image anyway.
                json={"prompt": prompt[:PROMPT_MAX], "steps": 4},
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=TIMEOUT,
            )
        )
        encoded = (data.get("result") or {}).get("image") if isinstance(data.get("result"), dict) else None
        if not encoded:
            raise ProviderError("Cloudflare sent no image", retryable=True)
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ProviderError("Cloudflare sent an image that can't be read", retryable=True) from None
        content_type, ext, width, height = checked_image(raw)
        return ImageResult(raw, content_type, ext, width, height, {"model": self.model})


class MockImage:
    """Offline stand-in: a gradient in the mood's colours. A prompt containing "[image-fail]"
    fails the call and "[image-refuse]" is refused by the content rules."""

    name = "mock"

    def __init__(self, s: Settings):
        pass

    def generate(self, prompt: str, seed: int) -> ImageResult:
        if "[image-refuse]" in prompt:
            raise ProviderError("mock content refusal", refused=True)
        if "[image-fail]" in prompt:
            raise ProviderError("mock image failure")
        mood = next((m for m in MOOD_COLOURS if MOOD_VISUAL_CUES[m] in prompt), "calm")
        top, bottom = MOOD_COLOURS[mood]
        width, height = 288, 512
        img = Image.new("RGB", (width, height))
        for y in range(height):
            t = y / (height - 1)
            colour = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True))
            img.paste(colour, (0, y, width, y + 1))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return ImageResult(
            buf.getvalue(), "image/jpeg", "jpg", width, height, {"model": "mock", "seed": seed}
        )


PROVIDERS = {"cloudflare": CloudflareImage, "mock": MockImage}
