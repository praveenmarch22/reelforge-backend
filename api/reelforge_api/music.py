"""The music library (B4 slice 3): seeding it, listing it (API D4) and choosing a track.

Tracks live in storage under `library/music/` and in the `music_tracks` table. The starter list
is `music_library.json`; `flask music-seed` downloads each file and adds or updates its row, so
the same command fills a new environment.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from mutagen.mp3 import MP3, HeaderNotFoundError
from sqlalchemy import func, select

from .extensions import db
from .models import MusicTrack, Project
from .providers.base import USER_AGENT
from .script_rules import MOODS
from .storage import get_storage

LIBRARY = Path(__file__).with_name("music_library.json")
MAX_TRACK_BYTES = 30 * 1024 * 1024
ALLOWED_HOSTS = ("incompetech.com",)


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def track_key(title: str) -> str:
    return f"library/music/{_slug(title)}.mp3"


def _download(url: str) -> tempfile.SpooledTemporaryFile:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"not an allowed music address: {url}")
    out = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    with requests.get(url, stream=True, timeout=(10, 60), allow_redirects=False,
                      headers={"User-Agent": USER_AGENT}) as resp:  # fmt: skip
        if resp.status_code != 200:
            raise ValueError(f"download failed with {resp.status_code}")
        size = 0
        for chunk in resp.iter_content(1024 * 1024):
            size += len(chunk)
            if size > MAX_TRACK_BYTES:
                raise ValueError("the track is larger than 30 MB")
            out.write(chunk)
    out.seek(0)
    return out


def _duration(f) -> float:
    try:
        return round(float(MP3(f).info.length), 3)
    except HeaderNotFoundError:
        raise ValueError("the file is not an MP3") from None
    finally:
        f.seek(0)


def seed(*, force: bool = False, echo=print) -> int:
    """Downloads every track in the starter list that isn't stored yet, and adds or updates
    its row (title, moods, license, source). Returns how many files were downloaded."""
    spec = json.loads(LIBRARY.read_text(encoding="utf-8"))
    storage = get_storage()
    downloaded = 0
    for item in spec["tracks"]:
        title, moods = item["title"], item["moods"]
        unknown = set(moods) - set(MOODS)
        if unknown:
            raise ValueError(f"{title}: unknown moods {sorted(unknown)}")
        key = track_key(title)
        row = db.session.execute(select(MusicTrack).where(MusicTrack.s3_key == key)).scalar_one_or_none()
        url = spec["base_url"] + quote(title) + ".mp3"
        if force or row is None or not storage.exists(key):
            with _download(url) as f:
                duration = _duration(f)
                storage.put_fileobj(key, f, "audio/mpeg")
            downloaded += 1
            echo(f"stored {key} ({duration:.0f} s)")
        else:
            duration = row.duration_seconds
        if row is None:
            row = MusicTrack(s3_key=key)
            db.session.add(row)
        row.title, row.moods, row.duration_seconds = title, moods, duration
        row.license = spec["license"].format(title=title)
        row.source_url = spec["source_page"]
        row.is_active = True
        row.updated_at = func.now()
        db.session.commit()
    return downloaded


# ---------------------------------------------------------------- listing and choosing


def track_json(track: MusicTrack) -> dict:
    """API D4. `previewUrl` is a link to the stored file, valid for 2 hours."""
    return {
        "id": str(track.id),
        "title": track.title,
        "durationSeconds": track.duration_seconds,
        "moods": list(track.moods),
        "license": track.license,
        "sourceUrl": track.source_url,
        "previewUrl": get_storage().presigned_get(track.s3_key),
    }


def list_tracks(mood: str | None = None) -> list[MusicTrack]:
    query = select(MusicTrack).where(MusicTrack.is_active.is_(True)).order_by(MusicTrack.title)
    if mood:
        query = query.where(MusicTrack.moods.any(mood))
    return list(db.session.execute(query).scalars())


def pick_track(project: Project) -> MusicTrack | None:
    """The default track for a project: one tagged with its music mood, else any active one.
    Chosen by the project's id, so the same project keeps the same track until changed."""
    for mood in (project.music_mood, None):
        tracks = list_tracks(mood)
        if tracks:
            return tracks[project.id.int % len(tracks)]
    return None
