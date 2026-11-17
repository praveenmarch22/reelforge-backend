"""Text-to-speech providers. Each writes an audio file and returns word timings."""

from __future__ import annotations

import asyncio
import base64
import math
import struct
import wave
from pathlib import Path

from ..config import Settings
from ..errors import NotConfigured, ProviderError, http
from ..media import audio_duration, estimate_words
from ..models import WORDS_PER_SECOND, VoiceResult, Word

# How each mood changes ElevenLabs delivery: lower stability = more expressive.
ELEVEN_MOOD_SETTINGS = {
    "calm": (0.65, 0.10), "serious": (0.60, 0.15), "sad": (0.55, 0.30), "mysterious": (0.50, 0.35),
    "tense": (0.40, 0.45), "inspiring": (0.40, 0.50), "joyful": (0.35, 0.50), "funny": (0.30, 0.60),
    "excited": (0.30, 0.65), "triumphant": (0.30, 0.65),
}
# eleven_v3 understands emotion tags written in the text.
ELEVEN_V3_TAGS = {
    "excited": "[excited]", "joyful": "[happy]", "funny": "[playful]", "sad": "[sad]",
    "tense": "[nervous]", "mysterious": "[whispers]", "calm": "[calm]",
}
# Edge TTS has no emotion control in its API, so mood adjusts speed and pitch.
EDGE_MOOD = {
    "excited": ("+12%", "+4Hz"), "triumphant": ("+8%", "+3Hz"), "joyful": ("+6%", "+3Hz"),
    "funny": ("+8%", "+5Hz"), "inspiring": ("+0%", "+1Hz"), "calm": ("-6%", "-1Hz"),
    "serious": ("-4%", "-2Hz"), "mysterious": ("-10%", "-3Hz"), "tense": ("+4%", "-1Hz"),
    "sad": ("-12%", "-4Hz"),
}


def _words_from_alignment(alignment: dict) -> list[Word]:
    """Turn ElevenLabs per-character timings into per-word timings."""
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words: list[Word] = []
    buf, w_start, w_end = "", 0.0, 0.0
    for ch, st, en in zip(chars, starts, ends):
        if ch.isspace():
            if buf:
                words.append(Word(text=buf, start=w_start, end=w_end))
                buf = ""
            continue
        if not buf:
            w_start = st
        buf += ch
        w_end = en
    if buf:
        words.append(Word(text=buf, start=w_start, end=w_end))
    # Drop emotion tags like [excited] from captions.
    return [w for w in words if not (w.text.startswith("[") and w.text.endswith("]"))]


class ElevenLabsVoice:
    name = "elevenlabs"

    def __init__(self, s: Settings):
        self.key, self.voice_id, self.model = s.elevenlabs_api_key, s.elevenlabs_voice_id, s.elevenlabs_model

    def synthesize(self, text: str, voice: str, mood: str, out_path: Path) -> VoiceResult:
        if not self.key:
            raise NotConfigured("ELEVENLABS_API_KEY is empty")
        voice_id = voice if voice and not voice.startswith("en-") else self.voice_id
        body: dict = {"text": text, "model_id": self.model}
        if self.model.startswith("eleven_v3"):
            tag = ELEVEN_V3_TAGS.get(mood)
            if tag:
                body["text"] = f"{tag} {text}"
        else:
            stability, style = ELEVEN_MOOD_SETTINGS.get(mood, (0.5, 0.3))
            body["voice_settings"] = {"stability": stability, "similarity_boost": 0.75, "style": style}
        resp = http(
            "POST",
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
            params={"output_format": "mp3_44100_128"},
            json=body, headers={"xi-api-key": self.key}, timeout=120,
        )
        data = resp.json()
        out_path = out_path.with_suffix(".mp3")
        out_path.write_bytes(base64.b64decode(data["audio_base64"]))
        duration = audio_duration(out_path)
        words = _words_from_alignment(data.get("alignment") or {})
        if not words:  # timings missing: spread words evenly (Whisper is the product's fix)
            words = estimate_words(text, duration)
        return VoiceResult(path=str(out_path), duration=duration, words=words, provider=self.name)


class EdgeVoice:
    """Free Microsoft Edge voices through the edge-tts package. No key needed."""

    name = "edge"

    def __init__(self, s: Settings):
        self.default_voice = s.edge_voice

    def synthesize(self, text: str, voice: str, mood: str, out_path: Path) -> VoiceResult:
        try:
            import edge_tts
        except ImportError as e:
            raise NotConfigured("edge-tts is not installed") from e
        voice_name = voice if voice.startswith("en-") else self.default_voice
        rate, pitch = EDGE_MOOD.get(mood, ("+0%", "+0Hz"))
        out_path = out_path.with_suffix(".mp3")

        async def run() -> list[Word]:
            try:
                comm = edge_tts.Communicate(text, voice_name, rate=rate, pitch=pitch, boundary="WordBoundary")
            except TypeError:  # older edge-tts without the boundary option
                comm = edge_tts.Communicate(text, voice_name, rate=rate, pitch=pitch)
            words: list[Word] = []
            with open(out_path, "wb") as f:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        start = chunk["offset"] / 10_000_000  # 100-nanosecond units
                        end = start + chunk["duration"] / 10_000_000
                        words.append(Word(text=chunk["text"], start=start, end=end))
            return words

        try:
            words = asyncio.run(run())
        except Exception as e:  # edge-tts raises several types for network problems
            raise ProviderError(f"edge-tts failed: {e}", retryable=True) from e
        duration = audio_duration(out_path)
        if not words:
            words = estimate_words(text, duration)
        return VoiceResult(path=str(out_path), duration=duration, words=words, provider=self.name)


class MockVoice:
    """Offline stand-in: a quiet tone per word at a natural speaking pace."""

    name = "mock"
    RATE = 22050

    def __init__(self, s: Settings):
        pass

    def synthesize(self, text: str, voice: str, mood: str, out_path: Path) -> VoiceResult:
        out_path = out_path.with_suffix(".wav")
        duration = max(1.0, len(text.split()) / WORDS_PER_SECOND)
        words = estimate_words(text, duration)
        total = int(duration * self.RATE)
        samples = bytearray()
        spans = [(int(w.start * self.RATE), int(w.end * self.RATE)) for w in words]
        span_i = 0
        for n in range(total):
            while span_i < len(spans) and n >= spans[span_i][1]:
                span_i += 1
            speaking = span_i < len(spans) and spans[span_i][0] <= n
            value = int(1800 * math.sin(2 * math.pi * 330 * n / self.RATE)) if speaking else 0
            samples += struct.pack("<h", value)
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.RATE)
            wf.writeframes(bytes(samples))
        return VoiceResult(path=str(out_path), duration=duration, words=words, provider=self.name)
