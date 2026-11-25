"""Shared by the voiceover tests (milestone B4 slice 1).

No test may reach a real voice service: the repo's .env holds real keys with a small free
quota. `no_real_voice_services` blocks every way out (requests, edge-tts) unless a test puts
its own stub in place.
"""

from __future__ import annotations

import io
import urllib.request

import pytest

from reelforge_api import voices

from .projects_support import get_project

# The voice picked when nothing else says otherwise: the first in D3 (API design D3).
DEFAULT_VOICE = voices.CATALOGUE[0]
VOICE_IDS = [v.id for v in voices.CATALOGUE]
MEDIA_FIELDS = {"assetId", "url", "durationSeconds"}
# The Scene's `voice` also says who made it, in which voice, and whether timings are estimated.
VOICE_FIELDS = MEDIA_FIELDS | {"provider", "voiceId", "estimatedTimings"}
WORD_FIELDS = {"text", "start", "end"}


class RealServiceCalled(AssertionError):
    pass


@pytest.fixture(autouse=True)
def no_real_voice_services(monkeypatch):
    """Fails the test instead of calling ElevenLabs, Edge TTS or anything else on the network
    through requests. Tests that exercise those providers replace these stubs with their own."""

    def refuse_http(method, url, *args, **kwargs):
        raise RealServiceCalled(f"a test tried to call {method} {url}")

    class RefuseEdge:
        def __init__(self, *args, **kwargs):
            raise RealServiceCalled("a test tried to use the real Edge TTS")

    monkeypatch.setattr("requests.request", refuse_http)
    monkeypatch.setattr("reelforge_api.providers.base.requests.request", refuse_http)
    monkeypatch.setattr(
        "requests.Session.request", lambda self, method, url, *a, **k: refuse_http(method, url)
    )
    monkeypatch.setattr("edge_tts.Communicate", RefuseEdge)


def regenerate(browser, scene_id: str, body: dict | None = None, *, key: str | None = None, **kwargs):
    headers = {"Idempotency-Key": key} if key is not None else None
    return browser.post(f"/scenes/{scene_id}/voice/regenerate", body, headers=headers, **kwargs)


def started(browser, scene_id: str, body: dict | None = None, **kwargs) -> dict:
    res = regenerate(browser, scene_id, body, **kwargs)
    assert res.status_code == 202, res.get_data(as_text=True)
    return res.get_json()["job"]


def scene_of(browser, project_id: str, scene_id: str) -> dict:
    return next(s for s in get_project(browser, project_id)["scenes"] if s["id"] == scene_id)


def words_of(n: int) -> str:
    """Narration of exactly n words."""
    return " ".join(f"word{i}" for i in range(1, n + 1))


def stored_voice_files(app, user_id: str, project_id: str) -> list[str]:
    """The voice files kept in storage for this project."""
    storage = app.extensions["storage"]
    found = storage.client.list_objects_v2(
        Bucket=storage.bucket, Prefix=f"users/{user_id}/projects/{project_id}/voice/"
    )
    return [item["Key"] for item in found.get("Contents", [])]


def download(url: str) -> bytes:
    """What a browser gets from a presigned link (the test settings sign for the internal address)."""
    with urllib.request.urlopen(url, timeout=10) as res:  # noqa: S310 - a link we just made
        return res.read()


def mp3_bytes(frames: int = 100) -> bytes:
    """A small, valid MPEG-1 Layer III stream (128 kb/s, 44.1 kHz): 1152 samples per frame,
    so 100 frames last about 2.6 seconds."""
    frame = bytes([0xFF, 0xFB, 0x90, 0x64]) + bytes(413)
    return frame * frames


def mp3_seconds(frames: int = 100) -> float:
    from mutagen.mp3 import MP3

    return round(float(MP3(io.BytesIO(mp3_bytes(frames))).info.length), 3)
