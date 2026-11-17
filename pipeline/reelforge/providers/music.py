"""Background music providers."""

from __future__ import annotations

import json
import math
import shutil
import struct
import wave
from pathlib import Path
from typing import Optional

from ..config import Settings
from ..errors import NotConfigured
from ..models import MusicResult


class LibraryMusic:
    """Tracks you add yourself, listed in renderer/public/music/library.json:

    [{"file": "upbeat-1.mp3", "moods": ["excited", "joyful"], "license": "Pixabay License"}]
    """

    name = "library"

    def __init__(self, s: Settings):
        self.dir = s.public_dir / "music"

    def pick(self, mood: str, out_path: Path) -> Optional[MusicResult]:
        index = self.dir / "library.json"
        if not index.exists():
            raise NotConfigured(f"no music library at {index}")
        tracks = json.loads(index.read_text(encoding="utf-8"))
        if not tracks:
            raise NotConfigured("music library is empty")
        match = next((t for t in tracks if mood in t.get("moods", [])), tracks[0])
        src = self.dir / match["file"]
        out_path = out_path.with_suffix(src.suffix)
        shutil.copyfile(src, out_path)
        return MusicResult(path=str(out_path), provider=self.name, license=match.get("license", ""))


class MockMusic:
    """Offline stand-in: a soft 20-second synth pad that loops."""

    name = "mock"
    RATE = 22050
    CHORDS = {"sad": (220.0, 261.6, 329.6), "tense": (196.0, 233.1, 293.7)}

    def __init__(self, s: Settings):
        pass

    def pick(self, mood: str, out_path: Path) -> Optional[MusicResult]:
        out_path = out_path.with_suffix(".wav")
        freqs = self.CHORDS.get(mood, (261.6, 329.6, 392.0))
        seconds = 20
        frames = bytearray()
        for n in range(seconds * self.RATE):
            t = n / self.RATE
            swell = 0.6 + 0.4 * math.sin(2 * math.pi * t / 5)
            v = sum(math.sin(2 * math.pi * f * t) for f in freqs) / len(freqs)
            frames += struct.pack("<h", int(9000 * swell * v))
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.RATE)
            wf.writeframes(bytes(frames))
        return MusicResult(path=str(out_path), provider=self.name, license="generated")
