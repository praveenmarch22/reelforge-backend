"""Voiceover providers (technical design 9.0): ElevenLabs first, the free Edge voices as the
backup, and an offline mock for development and tests. Built on the prototype in `pipeline/`.

Each provider turns one scene's narration into audio (kept in memory, never on disk) plus
the time each word is spoken, which drives the captions.
"""

from __future__ import annotations

import asyncio
import base64
import io
import math
import struct
import wave
from dataclasses import dataclass, field

from celery.exceptions import SoftTimeLimitExceeded

from ..config import Settings
from ..voices import Voice
from .base import NotConfigured, ProviderError, http, json_of

# How each mood changes ElevenLabs' delivery (PRD V3): lower stability is more expressive.
ELEVEN_MOOD_SETTINGS = {
    "calm": (0.65, 0.10), "serious": (0.60, 0.15), "sad": (0.55, 0.30), "mysterious": (0.50, 0.35),
    "tense": (0.40, 0.45), "inspiring": (0.40, 0.50), "joyful": (0.35, 0.50), "funny": (0.30, 0.60),
    "excited": (0.30, 0.65), "triumphant": (0.30, 0.65),
}  # fmt: skip
# The v3 models read emotion tags written into the text instead.
ELEVEN_V3_TAGS = {
    "excited": "[excited]", "joyful": "[happy]", "funny": "[playful]", "sad": "[sad]",
    "tense": "[nervous]", "mysterious": "[whispers]", "calm": "[calm]",
}  # fmt: skip
# Edge has no emotion control, so the mood changes speed and pitch a little.
EDGE_MOOD = {
    "excited": ("+12%", "+4Hz"), "triumphant": ("+8%", "+3Hz"), "joyful": ("+6%", "+3Hz"),
    "funny": ("+8%", "+5Hz"), "inspiring": ("+0%", "+1Hz"), "calm": ("-6%", "-1Hz"),
    "serious": ("-4%", "-2Hz"), "mysterious": ("-10%", "-3Hz"), "tense": ("+4%", "-1Hz"),
    "sad": ("-12%", "-4Hz"),
}  # fmt: skip

ELEVEN_TIMEOUT = 40
EDGE_TIMEOUT = 30
WORDS_PER_SECOND = 2.5


@dataclass
class VoiceResult:
    audio: bytes
    content_type: str
    extension: str
    duration: float
    words: list[dict] = field(default_factory=list)
    # True when the provider gave no word timings and they were spread evenly instead
    # (captions roughly in sync). Transcribing the audio fixes this in milestone B7 (G-15).
    estimated_timings: bool = False
    meta: dict = field(default_factory=dict)


def mp3_duration(data: bytes) -> float:
    from mutagen.mp3 import MP3, HeaderNotFoundError

    try:
        return round(float(MP3(io.BytesIO(data)).info.length), 3)
    except HeaderNotFoundError:
        raise ProviderError("the voice provider sent audio that can't be read", retryable=True) from None


def estimate_words(text: str, duration: float) -> list[dict]:
    """Even word timings across the audio, weighted by word length."""
    words = text.split()
    if not words:
        return []
    weights = [len(w) + 1 for w in words]
    total, t, out = sum(weights), 0.0, []
    for word, weight in zip(words, weights, strict=True):
        span = duration * weight / total
        out.append({"text": word, "start": round(t, 3), "end": round(t + span, 3)})
        t += span
    return out


def words_from_alignment(alignment: dict) -> list[dict]:
    """ElevenLabs gives a time per character; captions need a time per word."""
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words: list[dict] = []
    buf, start, end = "", 0.0, 0.0
    for ch, st, en in zip(chars, starts, ends, strict=False):
        if ch.isspace():
            if buf:
                words.append({"text": buf, "start": round(start, 3), "end": round(end, 3)})
                buf = ""
            continue
        if not buf:
            start = st
        buf += ch
        end = en
    if buf:
        words.append({"text": buf, "start": round(start, 3), "end": round(end, 3)})
    # Emotion tags such as [excited] are spoken as tone, never shown in the captions.
    return [w for w in words if not (w["text"].startswith("[") and w["text"].endswith("]"))]


def match_narration(words: list[dict], narration: str) -> list[dict]:
    """Captions show the narration exactly as written. Providers differ in what they send
    back (ElevenLabs keeps punctuation, Edge drops it), so when the word counts agree each
    word's text is taken from the narration and only the timings from the provider."""
    written = narration.split()
    if len(written) != len(words):
        return words
    return [{**w, "text": text} for w, text in zip(words, written, strict=True)]


def _sane(words: list[dict], duration: float) -> bool:
    return bool(words) and all(0 <= w["start"] <= w["end"] <= duration + 0.5 for w in words)


class ElevenLabsVoice:
    name = "elevenlabs"

    def __init__(self, s: Settings):
        self.key, self.model = s.elevenlabs_api_key, s.elevenlabs_model

    def synthesize(self, text: str, voice: Voice, mood: str) -> VoiceResult:
        if not self.key:
            raise NotConfigured("ELEVENLABS_API_KEY is not set")
        body: dict = {"text": text, "model_id": self.model}
        if self.model.startswith("eleven_v3"):
            if tag := ELEVEN_V3_TAGS.get(mood):
                body["text"] = f"{tag} {text}"
        else:
            stability, style = ELEVEN_MOOD_SETTINGS.get(mood, (0.5, 0.3))
            body["voice_settings"] = {"stability": stability, "similarity_boost": 0.75, "style": style}
        data = json_of(
            http(
                "POST",
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice.id}/with-timestamps",
                params={"output_format": "mp3_44100_128"},
                json=body,
                headers={"xi-api-key": self.key},
                timeout=ELEVEN_TIMEOUT,
            )
        )
        try:
            audio = base64.b64decode(data["audio_base64"], validate=True)
        except (KeyError, TypeError, ValueError):
            raise ProviderError("ElevenLabs sent no usable audio", retryable=True) from None
        duration = mp3_duration(audio)
        words = words_from_alignment(data.get("alignment") or {})
        estimated = not _sane(words, duration)
        if estimated:
            words = estimate_words(text, duration)
        return VoiceResult(
            audio, "audio/mpeg", "mp3", duration, words, estimated,
            {"voiceId": voice.id, "model": self.model, "characters": len(body["text"])},
        )  # fmt: skip


class EdgeVoice:
    """Microsoft Edge's free voices through the edge-tts package. No key needed."""

    name = "edge"

    def __init__(self, s: Settings):
        pass

    def synthesize(self, text: str, voice: Voice, mood: str) -> VoiceResult:
        import edge_tts

        rate, pitch = EDGE_MOOD.get(mood, ("+0%", "+0Hz"))

        async def stream() -> tuple[bytes, list[dict]]:
            comm = edge_tts.Communicate(text, voice.edge, rate=rate, pitch=pitch, boundary="WordBoundary")
            audio, words = bytearray(), []
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 10_000_000  # 100-nanosecond units
                    end = start + chunk["duration"] / 10_000_000
                    words.append({"text": chunk["text"], "start": round(start, 3), "end": round(end, 3)})
            return bytes(audio), words

        try:
            audio, words = asyncio.run(asyncio.wait_for(stream(), EDGE_TIMEOUT))
        except SoftTimeLimitExceeded:
            raise  # the job's own time limit: stop now, never retry (QB4-002)
        except TimeoutError:
            raise ProviderError("Edge TTS timed out", retryable=True) from None
        except Exception as e:  # noqa: BLE001 - edge-tts raises several types for network trouble
            raise ProviderError(f"Edge TTS failed: {type(e).__name__}", retryable=True) from None
        if not audio:
            raise ProviderError("Edge TTS sent no audio", retryable=True)
        duration = mp3_duration(audio)
        estimated = not _sane(words, duration)
        if estimated:
            words = estimate_words(text, duration)
        return VoiceResult(
            audio,
            "audio/mpeg",
            "mp3",
            duration,
            words,
            estimated,
            {"voiceId": voice.id, "edgeVoice": voice.edge},
        )


class MockVoice:
    """Offline stand-in: a quiet tone while each word is "spoken", at 2.5 words a second.

    For tests, the narration can ask it to misbehave: "[voice-fail]" fails the call and
    "[no-timings]" leaves out the word timings.
    """

    name = "mock"
    RATE = 8000

    def __init__(self, s: Settings):
        pass

    def synthesize(self, text: str, voice: Voice, mood: str) -> VoiceResult:  # noqa: ARG002
        if "[voice-fail]" in text:
            raise ProviderError("mock voice failure")
        duration = round(max(1.0, len(text.split()) / WORDS_PER_SECOND), 3)
        words = estimate_words(text, duration)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.RATE)
            tone = [
                struct.pack("<h", int(1500 * math.sin(2 * math.pi * 330 * n / self.RATE)))
                for n in range(self.RATE // 330 * 10)
            ]
            frames, cursor = bytearray(), 0
            for w in words:  # silence until the word starts, then a tone for most of it
                start, stop = int(w["start"] * self.RATE), int(w["end"] * self.RATE * 0.97)
                frames += b"\0\0" * max(0, start - cursor)
                frames += b"".join(tone[n % len(tone)] for n in range(max(0, stop - start)))
                cursor = max(cursor, stop)
            frames += b"\0\0" * max(0, int(duration * self.RATE) - cursor)
            wf.writeframes(bytes(frames))
        estimated = "[no-timings]" in text
        return VoiceResult(
            buf.getvalue(), "audio/wav", "wav", duration, words, estimated, {"voiceId": voice.id}
        )


PROVIDERS = {"elevenlabs": ElevenLabsVoice, "edge": EdgeVoice, "mock": MockVoice}
