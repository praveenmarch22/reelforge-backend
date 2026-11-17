"""AI image providers."""

from __future__ import annotations

import base64
from pathlib import Path

from ..config import Settings
from ..errors import NotConfigured, ProviderError, http
from ..media import placeholder_image
from ..models import VisualResult


class CloudflareImage:
    """FLUX on Cloudflare Workers AI (daily free allowance)."""

    name = "cloudflare"

    def __init__(self, s: Settings):
        self.account, self.token, self.model = s.cloudflare_account_id, s.cloudflare_api_token, s.cloudflare_image_model

    def generate(self, prompt: str, mood: str, seed: int, out_path: Path) -> VisualResult:
        if not (self.account and self.token):
            raise NotConfigured("CLOUDFLARE_ACCOUNT_ID or CLOUDFLARE_API_TOKEN is empty")
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account}/ai/run/{self.model}"
        data = http(
            "POST", url, json={"prompt": prompt[:2000], "steps": 4},
            headers={"Authorization": f"Bearer {self.token}"}, timeout=120,
        ).json()
        image_b64 = (data.get("result") or {}).get("image")
        if not image_b64:
            raise ProviderError(f"Cloudflare returned no image: {str(data)[:300]}")
        out_path = out_path.with_suffix(".jpg")
        out_path.write_bytes(base64.b64decode(image_b64))
        return VisualResult(kind="image", path=str(out_path), provider=self.name)


class MockImage:
    name = "mock"

    def __init__(self, s: Settings):
        pass

    def generate(self, prompt: str, mood: str, seed: int, out_path: Path) -> VisualResult:
        out_path = out_path.with_suffix(".jpg")
        placeholder_image(prompt, mood, out_path)
        return VisualResult(kind="image", path=str(out_path), provider=self.name)
