"""`flask music-seed` and the starter music list (B4 slice 3; README "Music library").

Every download is stubbed: no test reaches incompetech.com."""

from __future__ import annotations

import contextlib
import json

import pytest

from reelforge_api import music
from reelforge_api.script_rules import MOODS

from .visual_support import MP4_HEAD
from .voice_support import (
    mp3_bytes,
    mp3_seconds,
    no_real_voice_services,  # noqa: F401 - autouse fixture
)

BASE = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"


class FakeDownload:
    def __init__(self, status: int = 200, chunks: list[bytes] | None = None):
        self.status_code, self.chunks = status, chunks if chunks is not None else [mp3_bytes()]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=1):
        yield from self.chunks


class Calls(list):
    answers: dict


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A small starter list in place of the real one."""

    def write(tracks, *, base_url=BASE):
        spec = {
            "license": 'CC BY 4.0: "{title}" by Kevin MacLeod (incompetech.com)',
            "base_url": base_url,
            "source_page": "https://incompetech.com/music/royalty-free/music.html",
            "tracks": tracks,
        }
        path = tmp_path / "music_library.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        monkeypatch.setattr(music, "LIBRARY", path)

    return write


@pytest.fixture
def downloads_list(monkeypatch) -> Calls:
    calls = Calls()
    calls.answers = {}

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return calls.answers.get(url, FakeDownload())

    monkeypatch.setattr("requests.get", fake_get)
    return calls


def seed(app, **kwargs) -> int:
    with app.app_context():
        return music.seed(echo=lambda _: None, **kwargs)


def rows(database) -> list[dict]:
    return database.rows(
        "SELECT title, s3_key, duration_seconds, moods, license, source_url, is_active "
        "FROM music_tracks ORDER BY title"
    )


def forget_files(app, *titles) -> None:
    storage = app.extensions["storage"]
    for title in titles:
        with contextlib.suppress(Exception):
            storage.delete(music.track_key(title))


def test_the_starter_list_is_well_formed():
    spec = json.loads(music.LIBRARY.read_text(encoding="utf-8"))
    assert spec["base_url"].startswith("https://incompetech.com/")
    assert len(spec["tracks"]) == 15
    titles = [t["title"] for t in spec["tracks"]]
    assert len(set(titles)) == 15 and len({music.track_key(t) for t in titles}) == 15
    for track in spec["tracks"]:
        assert track["moods"] and set(track["moods"]) <= set(MOODS), track
    assert set(MOODS) <= {m for t in spec["tracks"] for m in t["moods"]}, "every mood has a track"
    assert "{title}" in spec["license"]


def test_seeding_downloads_stores_and_adds_rows(app, database, outbox, library, downloads_list):
    library(
        [{"title": "Seed Test One", "moods": ["calm"]}, {"title": "Seed Test Two", "moods": ["sad", "tense"]}]
    )
    forget_files(app, "Seed Test One", "Seed Test Two")
    assert seed(app) == 2
    assert [c["url"] for c in downloads_list] == [
        BASE + "Seed%20Test%20One.mp3",
        BASE + "Seed%20Test%20Two.mp3",
    ]
    for call in downloads_list:
        assert call["stream"] is True and call["allow_redirects"] is False
        assert call["headers"] == {"User-Agent": "ReelForge/1.0"}
    found = rows(database)
    assert [r["title"] for r in found] == ["Seed Test One", "Seed Test Two"]
    assert found[0]["s3_key"] == "library/music/seed-test-one.mp3"
    assert found[1]["moods"] == ["sad", "tense"] and found[0]["is_active"] is True
    assert float(found[0]["duration_seconds"]) == pytest.approx(mp3_seconds(), abs=0.01)
    assert found[0]["license"] == 'CC BY 4.0: "Seed Test One" by Kevin MacLeod (incompetech.com)'
    assert app.extensions["storage"].exists("library/music/seed-test-one.mp3")


def test_seeding_again_downloads_nothing_and_updates_rows(app, database, outbox, library, downloads_list):
    library([{"title": "Seed Again", "moods": ["calm"]}])
    forget_files(app, "Seed Again")
    assert seed(app) == 1
    database.run("UPDATE music_tracks SET is_active = false, moods = '{joyful}'")
    library([{"title": "Seed Again", "moods": ["calm", "inspiring"]}])
    assert seed(app) == 0, "already stored"
    assert len(downloads_list) == 1
    (row,) = rows(database)
    assert row["moods"] == ["calm", "inspiring"] and row["is_active"] is True
    assert seed(app, force=True) == 1 and len(downloads_list) == 2
    assert len(rows(database)) == 1, "one row per file"


def test_a_row_whose_file_is_missing_is_downloaded_again(app, database, outbox, library, downloads_list):
    library([{"title": "Seed Lost", "moods": ["calm"]}])
    seed(app)
    forget_files(app, "Seed Lost")
    assert seed(app) == 1


def test_the_cli_command_runs_the_seed(app, database, outbox, library, downloads_list):
    library([{"title": "Seed Cli", "moods": ["calm"]}])
    forget_files(app, "Seed Cli")
    result = app.test_cli_runner().invoke(args=["music-seed"])
    assert result.exit_code == 0, result.output
    assert "1 file(s) downloaded" in result.output
    result = app.test_cli_runner().invoke(args=["music-seed", "--force"])
    assert result.exit_code == 0 and "1 file(s) downloaded" in result.output


@pytest.mark.parametrize(
    "base_url",
    [
        "http://incompetech.com/music/",
        "https://evil.example/music/",
        "https://incompetech.com.evil.example/",
        "https://incompetech.com@evil.example/",
        "https://www.incompetech.com/music/",
    ],
)
def test_only_https_incompetech_addresses_are_downloaded(
    app, database, outbox, library, downloads_list, base_url
):
    library([{"title": "Seed Host", "moods": ["calm"]}], base_url=base_url)
    forget_files(app, "Seed Host")
    with pytest.raises(ValueError, match="not an allowed music address"):
        seed(app)
    assert downloads_list == [] and rows(database) == []


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (FakeDownload(404), "404"),
        (FakeDownload(302), "302"),
        (FakeDownload(chunks=[MP4_HEAD + b"\x00" * 500]), "not an MP3"),
        (FakeDownload(chunks=[b"<html>gone</html>"]), "not an MP3"),
    ],
)
def test_a_bad_download_stores_nothing(app, database, outbox, library, downloads_list, answer, message):
    library([{"title": "Seed Bad", "moods": ["calm"]}])
    forget_files(app, "Seed Bad")
    downloads_list.answers[BASE + "Seed%20Bad.mp3"] = answer
    with pytest.raises(ValueError, match=message):
        seed(app)
    assert rows(database) == []
    assert not app.extensions["storage"].exists("library/music/seed-bad.mp3")


def test_a_track_over_30_mb_is_refused(app, database, outbox, library, downloads_list, monkeypatch):
    assert music.MAX_TRACK_BYTES == 30 * 1024 * 1024
    monkeypatch.setattr(music, "MAX_TRACK_BYTES", 1000)
    library([{"title": "Seed Big", "moods": ["calm"]}])
    forget_files(app, "Seed Big")
    downloads_list.answers[BASE + "Seed%20Big.mp3"] = FakeDownload(chunks=[b"\xff" * 600, b"\xff" * 600])
    with pytest.raises(ValueError, match="30 MB"):
        seed(app)
    assert rows(database) == []


def test_an_unknown_mood_stops_the_seed(app, database, outbox, library, downloads_list):
    library([{"title": "Seed Mood", "moods": ["calm", "euphoric"]}])
    with pytest.raises(ValueError, match="euphoric"):
        seed(app)
    assert downloads_list == [] and rows(database) == []
