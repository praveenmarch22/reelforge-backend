"""The real voice code paths with every network call stubbed (no real service is called and no
real key is used): ElevenLabs request shape, mood settings and v3 emotion tags, word timings
from character alignment, the fallback to the paired Edge voice, estimated timings (G-15),
D3 GET /voices with its daily preview cache, and the VOICE_PROVIDERS settings.
Technical design "Voice (ElevenLabs, fallback Edge TTS)"; API design C7, D3; PRD V1-V3."""

from __future__ import annotations

import base64
import json

import pytest
import requests
from pydantic import ValidationError

from reelforge_api import voices
from reelforge_api.config import Settings
from reelforge_api.providers.base import USER_AGENT, ProviderError
from reelforge_api.providers.voice import (
    EDGE_MOOD,
    ELEVEN_MOOD_SETTINGS,
    estimate_words,
    match_narration,
    mp3_duration,
    words_from_alignment,
)

from .conftest import build_app
from .projects_support import (
    assert_ledger_matches_balance,
    balance,
    creator,
    get_job,
    run_jobs,
    scripted_project,
)
from .support import Browser, assert_error
from .test_worker_and_config import PRODUCTION
from .voice_support import (  # noqa: F401 - no_real_voice_services is an autouse fixture
    DEFAULT_VOICE,
    VOICE_IDS,
    mp3_bytes,
    mp3_seconds,
    no_real_voice_services,
    regenerate,
    scene_of,
    started,
)

ELEVEN_KEY = "eleven-test-key-0123456789"
TTS_PATH = "api.elevenlabs.io/v1/text-to-speech/"
VOICES_URL = "https://api.elevenlabs.io/v1/voices"


@pytest.fixture(scope="module")
def eleven_app(app):
    return build_app(voice_providers="elevenlabs,edge", elevenlabs_api_key=ELEVEN_KEY)


@pytest.fixture(scope="module")
def v3_app(app):
    return build_app(
        voice_providers="elevenlabs,edge", elevenlabs_api_key=ELEVEN_KEY, elevenlabs_model="eleven_v3"
    )


@pytest.fixture(scope="module")
def keyless_app(app):
    return build_app(voice_providers="elevenlabs,edge", elevenlabs_api_key="")


def response(status: int = 200, body=None, *, text: str | None = None) -> requests.Response:
    res = requests.Response()
    res.status_code = status
    res._content = (text if text is not None else json.dumps(body or {})).encode()
    return res


def alignment_for(text: str, seconds: float) -> dict:
    """ElevenLabs' character timings, spread evenly over the audio."""
    step = seconds / max(1, len(text))
    return {
        "characters": list(text),
        "character_start_times_seconds": [round(i * step, 3) for i in range(len(text))],
        "character_end_times_seconds": [round((i + 1) * step, 3) for i in range(len(text))],
    }


def eleven_ok(text: str | None = None, *, frames: int = 100, alignment: dict | None = None) -> callable:
    """An answer built from the request (so the alignment matches the text sent)."""

    def answer(call) -> requests.Response:
        sent = text if text is not None else call["json"]["text"]
        seconds = mp3_seconds(frames)
        return response(
            200,
            {
                "audio_base64": base64.b64encode(mp3_bytes(frames)).decode(),
                "alignment": alignment if alignment is not None else alignment_for(sent, seconds),
            },
        )

    return answer


class FakeHTTP:
    """Stands in for requests.request: records each call and answers from a script per URL."""

    def __init__(self):
        self.calls: list[dict] = []
        self.tts: list = []
        self.voices: list = []

    def __call__(self, method, url, **kwargs):
        call = {"method": method, "url": url, **kwargs}
        self.calls.append(call)
        queue = self.tts if TTS_PATH in url else self.voices if url == VOICES_URL else None
        assert queue, f"unexpected call to {url}"
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer(call) if callable(answer) else answer

    def tts_calls(self) -> list[dict]:
        return [c for c in self.calls if TTS_PATH in c["url"]]


@pytest.fixture
def http(monkeypatch) -> FakeHTTP:
    fake = FakeHTTP()
    monkeypatch.setattr("reelforge_api.providers.base.requests.request", fake)
    return fake


class FakeEdge:
    """Stands in for edge_tts.Communicate. `words` are (text, start, end) in seconds."""

    calls: list[dict] = []
    audio: bytes = b""
    words: list[tuple[str, float, float]] = []
    error: Exception | None = None

    def __init__(self, text, voice, *, rate="+0%", pitch="+0Hz", boundary="SentenceBoundary", **kwargs):
        FakeEdge.calls.append(
            {"text": text, "voice": voice, "rate": rate, "pitch": pitch, "boundary": boundary}
        )

    async def stream(self):
        if FakeEdge.error is not None:
            raise FakeEdge.error
        yield {"type": "audio", "data": FakeEdge.audio[: len(FakeEdge.audio) // 2]}
        for text, start, end in FakeEdge.words:
            yield {
                "type": "WordBoundary",
                "offset": int(start * 10_000_000),
                "duration": int((end - start) * 10_000_000),
                "text": text,
            }
        yield {"type": "audio", "data": FakeEdge.audio[len(FakeEdge.audio) // 2 :]}


@pytest.fixture
def edge(monkeypatch) -> type[FakeEdge]:
    FakeEdge.calls, FakeEdge.audio, FakeEdge.words, FakeEdge.error = [], mp3_bytes(), [], None
    monkeypatch.setattr("edge_tts.Communicate", FakeEdge)
    return FakeEdge


def edge_words(text: str) -> list[tuple[str, float, float]]:
    return [(w, round(0.3 * i, 3), round(0.3 * i + 0.25, 3)) for i, w in enumerate(text.split())]


def voice_scene(app, me, narration: str, mood: str = "calm") -> tuple[dict, str]:
    project = scripted_project(app, me.browser)
    scene_id = project["scenes"][0]["id"]
    res = me.browser.patch(f"/scenes/{scene_id}", {"narration": narration, "mood": mood})
    assert res.status_code == 200, res.get_data(as_text=True)
    return project, scene_id


def voice_asset(database, project_id: str) -> dict:
    return database.one(
        "SELECT s3_key, content_type, provider, duration_seconds, meta FROM assets WHERE project_id = :p",
        p=project_id,
    )


# ---------------------------------------------------------------- ElevenLabs


@pytest.mark.parametrize("mood", ["excited", "calm", "sad"])
def test_elevenlabs_is_asked_first_with_the_voice_mood_settings_and_word_timings(
    eleven_app, database, outbox, http, edge, mood
):
    http.tts = [eleven_ok()]
    me = creator(eleven_app)
    narration = "Drink water before coffee, every single morning."
    project, scene_id = voice_scene(eleven_app, me, narration, mood)
    job = started(me.browser, scene_id, {"voiceId": VOICE_IDS[3]})
    run_jobs(eleven_app)

    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["provider"] == "elevenlabs"
    assert done["output"]["estimatedTimings"] is False
    (call,) = http.tts_calls()
    assert call["method"] == "POST"
    assert call["url"] == f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_IDS[3]}/with-timestamps"
    assert call["headers"] == {"User-Agent": USER_AGENT, "xi-api-key": ELEVEN_KEY}
    assert call["params"] == {"output_format": "mp3_44100_128"}
    assert call["timeout"] and call["timeout"] <= 60
    stability, style = ELEVEN_MOOD_SETTINGS[mood]
    assert call["json"] == {
        "text": narration,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": stability, "similarity_boost": 0.75, "style": style},
    }
    assert edge.calls == [], "Edge is only the backup"

    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == "ready"
    assert scene["voice"]["durationSeconds"] == pytest.approx(mp3_seconds(), abs=0.01)
    assert [w["text"] for w in scene["words"]] == narration.split()
    first = scene["words"][0]
    assert first["start"] == 0 and first["end"] < scene["words"][1]["start"]
    asset = voice_asset(database, project["id"])
    assert asset["provider"] == "elevenlabs" and asset["content_type"] == "audio/mpeg"
    assert asset["s3_key"].endswith(f"/voice/{job['id']}.mp3")
    assert asset["meta"] == {
        "voiceId": VOICE_IDS[3],
        "model": "eleven_multilingual_v2",
        "characters": len(narration),
        "estimatedTimings": False,
    }


def test_the_v3_model_gets_an_emotion_tag_that_never_reaches_the_captions(
    v3_app, database, outbox, http, edge
):
    http.tts = [eleven_ok()]
    me = creator(v3_app)
    narration = "We did it, we actually did it!"
    project, scene_id = voice_scene(v3_app, me, narration, "excited")
    started(me.browser, scene_id)
    run_jobs(v3_app)
    (call,) = http.tts_calls()
    assert call["json"] == {"text": f"[excited] {narration}", "model_id": "eleven_v3"}
    scene = scene_of(me.browser, project["id"], scene_id)
    assert [w["text"] for w in scene["words"]] == narration.split()
    assert voice_asset(database, project["id"])["meta"]["model"] == "eleven_v3"


def test_the_v3_model_sends_moods_without_a_tag_as_they_are(v3_app, outbox, http, edge):
    http.tts = [eleven_ok()]
    me = creator(v3_app)
    _, scene_id = voice_scene(v3_app, me, "Read the fine print.", "serious")
    started(me.browser, scene_id)
    run_jobs(v3_app)
    assert http.tts_calls()[0]["json"]["text"] == "Read the fine print."


def test_without_character_timings_the_words_are_estimated(eleven_app, database, outbox, http, edge):
    """G-15 until Whisper (B7): even timings over the real audio length, flagged."""
    http.tts = [eleven_ok(alignment={})]
    me = creator(eleven_app)
    narration = "Timings went missing somewhere."
    project, scene_id = voice_scene(eleven_app, me, narration)
    job = started(me.browser, scene_id)
    run_jobs(eleven_app)
    assert get_job(me.browser, job["id"])["output"]["estimatedTimings"] is True
    assert voice_asset(database, project["id"])["meta"]["estimatedTimings"] is True
    scene = scene_of(me.browser, project["id"], scene_id)
    assert [w["text"] for w in scene["words"]] == narration.split()
    assert scene["words"][-1]["end"] == pytest.approx(mp3_seconds(), abs=0.01)


def test_timings_past_the_end_of_the_audio_are_not_trusted(eleven_app, database, outbox, http, edge):
    narration = "These timings are nonsense."
    bad = alignment_for(narration, 60.0)  # the audio is about 2.6 s long
    http.tts = [eleven_ok(alignment=bad)]
    me = creator(eleven_app)
    project, scene_id = voice_scene(eleven_app, me, narration)
    job = started(me.browser, scene_id)
    run_jobs(eleven_app)
    assert get_job(me.browser, job["id"])["output"]["estimatedTimings"] is True
    words = scene_of(me.browser, project["id"], scene_id)["words"]
    assert words[-1]["end"] <= mp3_seconds() + 0.01


# ---------------------------------------------------------------- fallback to Edge


def test_when_the_free_elevenlabs_characters_run_out_the_paired_edge_voice_is_used(
    eleven_app, database, outbox, http, edge
):
    http.tts = [response(401, {"detail": {"status": "quota_exceeded", "message": "quota"}})]
    narration = "Edge saves the day again."
    edge.words = edge_words(narration)
    me = creator(eleven_app)
    project, scene_id = voice_scene(eleven_app, me, narration, "excited")
    chosen = voices.BY_ID[VOICE_IDS[4]]
    job = started(me.browser, scene_id, {"voiceId": chosen.id})
    run_jobs(eleven_app)

    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["provider"] == "edge", done
    assert len(http.tts_calls()) == 1, "a 401 is not retried"
    rate, pitch = EDGE_MOOD["excited"]
    assert edge.calls == [
        {"text": narration, "voice": chosen.edge, "rate": rate, "pitch": pitch, "boundary": "WordBoundary"}
    ]
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == "ready"
    assert [(w["text"], w["start"], w["end"]) for w in scene["words"]] == edge.words
    asset = voice_asset(database, project["id"])
    assert asset["provider"] == "edge" and asset["content_type"] == "audio/mpeg"
    assert asset["meta"]["edgeVoice"] == chosen.edge and asset["meta"]["voiceId"] == chosen.id
    assert asset["meta"]["estimatedTimings"] is False
    assert done["creditsRefunded"] is False, "the creator still gets a voice"


def test_captions_keep_the_narrations_punctuation_with_edge(keyless_app, database, outbox, http, edge):
    """QB4-007 fix: Edge sends words without punctuation; when the counts match, the captions
    take each word as written in the narration and only the timings from Edge."""
    narration = "Wait... what? It's 5 a.m., go!"
    edge.words = [(w, round(0.3 * i, 3), round(0.3 * i + 0.25, 3)) for i, w in enumerate(
        ["Wait", "what", "Its", "5", "am", "go"])]  # fmt: skip
    me = creator(keyless_app)
    project, scene_id = voice_scene(keyless_app, me, narration)
    started(me.browser, scene_id)
    run_jobs(keyless_app)
    words = scene_of(me.browser, project["id"], scene_id)["words"]
    assert [w["text"] for w in words] == narration.split()
    assert [(w["start"], w["end"]) for w in words] == [(s, e) for _, s, e in edge.words]


def test_captions_keep_the_providers_words_when_the_counts_differ(keyless_app, database, outbox, http, edge):
    narration = "Meet me at 5pm sharp."
    edge.words = [(w, round(0.3 * i, 3), round(0.3 * i + 0.25, 3)) for i, w in enumerate(
        ["Meet", "me", "at", "5", "PM", "sharp"])]  # fmt: skip
    me = creator(keyless_app)
    project, scene_id = voice_scene(keyless_app, me, narration)
    started(me.browser, scene_id)
    run_jobs(keyless_app)
    words = scene_of(me.browser, project["id"], scene_id)["words"]
    assert [w["text"] for w in words] == ["Meet", "me", "at", "5", "PM", "sharp"]


def test_match_narration_as_a_unit():
    timed = [{"text": "hello", "start": 0.0, "end": 0.4}, {"text": "world", "start": 0.5, "end": 0.9}]
    assert match_narration(timed, "Hello, world!") == [
        {"text": "Hello,", "start": 0.0, "end": 0.4},
        {"text": "world!", "start": 0.5, "end": 0.9},
    ]
    assert match_narration(timed, "Hello there, world") == timed
    assert match_narration([], "") == []


@pytest.mark.parametrize(
    "failure",
    [
        lambda: response(500, text="upstream broke"),
        lambda: response(429, text="slow down"),
        lambda: requests.ConnectionError("unreachable"),
        lambda: response(200, {"audio_base64": "!!not base64!!", "alignment": {}}),
        lambda: response(200, {"audio_base64": base64.b64encode(b"not an mp3 at all" * 50).decode()}),
        lambda: response(200, text="<html>bad gateway</html>"),
    ],
)
def test_retryable_elevenlabs_failures_are_tried_three_times_then_edge(
    eleven_app, outbox, http, edge, failure
):
    http.tts = [failure()]
    narration = "Try again then switch."
    edge.words = edge_words(narration)
    me = creator(eleven_app)
    _, scene_id = voice_scene(eleven_app, me, narration)
    job = started(me.browser, scene_id)
    run_jobs(eleven_app)
    assert get_job(me.browser, job["id"])["provider"] == "edge"
    assert len(http.tts_calls()) == 3
    assert len(edge.calls) == 1


def test_edge_without_word_boundaries_gives_estimated_timings(keyless_app, database, outbox, http, edge):
    """Also: without an ElevenLabs key, Edge is used straight away and nothing is sent to ElevenLabs."""
    narration = "No boundaries came back."
    me = creator(keyless_app)
    project, scene_id = voice_scene(keyless_app, me, narration)
    job = started(me.browser, scene_id)
    run_jobs(keyless_app)
    done = get_job(me.browser, job["id"])
    assert done["provider"] == "edge" and done["output"]["estimatedTimings"] is True
    assert http.calls == []
    assert [w["text"] for w in scene_of(me.browser, project["id"], scene_id)["words"]] == narration.split()


def test_an_admin_switched_off_elevenlabs_is_skipped(eleven_app, database, outbox, http, edge):
    database.run("INSERT INTO provider_switches (provider, enabled) VALUES ('elevenlabs', false)")
    narration = "Straight to the backup."
    edge.words = edge_words(narration)
    me = creator(eleven_app)
    _, scene_id = voice_scene(eleven_app, me, narration)
    job = started(me.browser, scene_id)
    run_jobs(eleven_app)
    assert get_job(me.browser, job["id"])["provider"] == "edge"
    assert http.calls == []


def test_when_both_fail_the_job_fails_and_is_refunded(eleven_app, database, outbox, http, edge):
    http.tts = [response(503, text="down")]
    edge.error = OSError("no network")
    me = creator(eleven_app)
    project, scene_id = voice_scene(eleven_app, me, "Nobody answers today.")
    job = started(me.browser, scene_id)
    run_jobs(eleven_app)
    failed = get_job(me.browser, job["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "provider_failed"
    assert failed["creditsRefunded"] is True
    assert ELEVEN_KEY not in json.dumps(failed)
    scene = scene_of(me.browser, project["id"], scene_id)
    assert scene["voiceStatus"] == "failed" and scene["lastError"] == failed["error"]["message"]
    assert len(edge.calls) == 3, "Edge network errors are retried too"
    assert_ledger_matches_balance(database, me.id)


def test_edge_sending_no_audio_is_a_failure(keyless_app, outbox, http, edge):
    edge.audio = b""
    me = creator(keyless_app)
    _, scene_id = voice_scene(keyless_app, me, "Silence is not a voice.")
    job = started(me.browser, scene_id)
    run_jobs(keyless_app)
    assert get_job(me.browser, job["id"])["state"] == "failed"
    assert balance(me.browser) == 99


def test_a_refusal_from_elevenlabs_is_not_sent_to_edge(eleven_app, outbox, http, edge):
    http.tts = [response(400, text='{"detail": "blocked by safety moderation"}')]
    me = creator(eleven_app)
    _, scene_id = voice_scene(eleven_app, me, "Something it will not say.")
    job = started(me.browser, scene_id)
    run_jobs(eleven_app)
    failed = get_job(me.browser, job["id"])
    assert failed["error"]["code"] == "content_refused" and failed["creditsRefunded"] is True
    assert edge.calls == []


# ---------------------------------------------------------------- helpers, as units


def test_words_from_alignment_joins_characters_and_drops_emotion_tags():
    text = "[excited]  Hello,   world!\nGo now "
    words = words_from_alignment(alignment_for(text, len(text) / 10))
    assert [w["text"] for w in words] == ["Hello,", "world!", "Go", "now"]
    assert all(w["start"] < w["end"] for w in words)
    assert words[0]["start"] == pytest.approx(text.index("H") / 10, abs=0.001)
    assert words[-1]["end"] == pytest.approx((text.index("now") + 3) / 10, abs=0.001)
    assert words_from_alignment({}) == []


def test_estimated_words_fill_the_audio_in_order():
    words = estimate_words("a bb ccc dddd", 5.0)
    assert [w["text"] for w in words] == ["a", "bb", "ccc", "dddd"]
    assert words[0]["start"] == 0 and words[-1]["end"] == pytest.approx(5.0, abs=0.01)
    spans = [w["end"] - w["start"] for w in words]
    assert spans == sorted(spans), "longer words take longer"
    assert estimate_words("   ", 3.0) == []


def test_unreadable_audio_is_a_retryable_provider_error():
    with pytest.raises(ProviderError) as raised:
        mp3_duration(b"\x00" * 100)
    assert raised.value.retryable
    assert mp3_duration(mp3_bytes()) == pytest.approx(mp3_seconds(), abs=0.001)


# ---------------------------------------------------------------- D3 GET /voices

VOICE_ITEM_FIELDS = {"id", "name", "provider", "gender", "accent", "style", "previewUrl"}


def preview_answer() -> requests.Response:
    return response(
        200,
        {
            "voices": [
                *[
                    {"voice_id": v, "name": "x", "preview_url": f"https://storage.example/{v}.mp3"}
                    for v in VOICE_IDS[:-1]
                ],
                {"voice_id": VOICE_IDS[-1], "preview_url": "http://insecure.example/x.mp3"},
                {"voice_id": "someone-else", "preview_url": "https://storage.example/other.mp3"},
                "not a voice",
            ]
        },
    )


def test_the_voice_list_is_the_curated_eight_with_the_default_first(app, outbox):
    me = creator(app)
    res = me.browser.get("/voices")
    assert res.status_code == 200
    assert set(res.get_json()) == {"items"}
    items = res.get_json()["items"]
    assert len(items) == 8 and items[0]["id"] == DEFAULT_VOICE.id
    assert [i["id"] for i in items] == VOICE_IDS
    for item in items:
        assert set(item) == VOICE_ITEM_FIELDS
        assert item["provider"] == "elevenlabs"
        assert all(isinstance(item[f], str) and item[f] for f in ("id", "name", "gender", "accent", "style"))
        assert item["previewUrl"] == "", "no key: no samples, and no call to ElevenLabs"


def test_every_listed_voice_can_be_chosen_everywhere_a_voice_is_asked_for(app, outbox):
    me = creator(app)
    for item in me.browser.get("/voices").get_json()["items"]:
        res = me.browser.patch("/me", {"defaults": {"voiceId": item["id"]}})
        assert res.status_code == 200 and res.get_json()["user"]["defaults"]["voiceId"] == item["id"]
    res = me.browser.post("/projects", {"topic": "habits", "voiceId": VOICE_IDS[5]})
    assert res.status_code == 202 and res.get_json()["project"]["voiceId"] == VOICE_IDS[5]
    run_jobs(app)
    for bad in ("voice-7", "x" * 101):
        assert me.browser.patch("/me", {"defaults": {"voiceId": bad}}).status_code == 400
        assert me.browser.post("/projects", {"topic": "habits", "voiceId": bad}).status_code == 400


def test_the_voice_list_needs_a_login_but_not_a_verified_email(app, database, outbox):
    assert_error(Browser(app).get("/voices"), 401, "not_authenticated")
    me = creator(app, verified=False)
    assert me.browser.get("/voices").status_code == 200


def test_preview_clips_are_fetched_once_and_kept_for_a_day(eleven_app, outbox, http):
    http.voices = [preview_answer()]
    me = creator(eleven_app)
    first = me.browser.get("/voices").get_json()["items"]
    by_id = {i["id"]: i["previewUrl"] for i in first}
    assert by_id == {
        **{v: f"https://storage.example/{v}.mp3" for v in VOICE_IDS[:-1]},
        VOICE_IDS[-1]: "",  # only https links are passed on
    }
    (call,) = http.calls
    assert call["method"] == "GET" and call["url"] == VOICES_URL
    assert call["headers"] == {"User-Agent": USER_AGENT, "xi-api-key": ELEVEN_KEY}

    again = creator(eleven_app).browser.get("/voices").get_json()["items"]
    assert again == first and len(http.calls) == 1, "served from the cache"
    ttl = eleven_app.extensions["redis"].ttl(voices.PREVIEWS_KEY)
    assert 24 * 3600 - 60 <= ttl <= 24 * 3600


@pytest.mark.parametrize(
    "failure",
    [
        lambda: response(500, text="down"),
        lambda: response(401, text="bad key"),
        lambda: requests.Timeout("slow"),
        lambda: response(200, text="<html>"),
    ],
)
def test_the_voice_list_still_works_when_elevenlabs_cannot_be_reached(eleven_app, outbox, http, failure):
    http.voices = [failure()]
    me = creator(eleven_app)
    res = me.browser.get("/voices")
    assert res.status_code == 200, res.get_data(as_text=True)
    items = res.get_json()["items"]
    assert len(items) == 8 and all(i["previewUrl"] == "" for i in items)
    # QB4-006 fix: the failure is remembered for 5 minutes, so the next lists don't wait on
    # ElevenLabs again.
    assert creator(eleven_app).browser.get("/voices").get_json()["items"] == items
    assert len(http.calls) == 1
    ttl = eleven_app.extensions["redis"].ttl(voices.PREVIEWS_KEY)
    assert 0 < ttl <= voices.FAILED_PREVIEWS_SECONDS == 300


@pytest.mark.parametrize("body", [{"voices": "nope"}, {"detail": "unexpected"}])
def test_an_answer_without_a_voice_list_counts_as_a_failed_lookup(eleven_app, outbox, http, body):
    """A 200 whose body has no usable voice list is not "ElevenLabs' samples"; it must be
    remembered like a failure (5 minutes), not kept as an empty list for a day."""
    http.voices = [response(200, body)]
    items = creator(eleven_app).browser.get("/voices").get_json()["items"]
    assert all(i["previewUrl"] == "" for i in items)
    ttl = eleven_app.extensions["redis"].ttl(voices.PREVIEWS_KEY)
    assert 0 < ttl <= voices.FAILED_PREVIEWS_SECONDS, f"remembered for {ttl} s"


def test_samples_are_asked_for_again_once_the_remembered_failure_expires(eleven_app, outbox, http):
    http.voices = [response(500, text="down"), preview_answer()]
    me = creator(eleven_app)
    assert all(i["previewUrl"] == "" for i in me.browser.get("/voices").get_json()["items"])
    eleven_app.extensions["redis"].delete(voices.PREVIEWS_KEY)  # five minutes pass
    items = me.browser.get("/voices").get_json()["items"]
    assert items[0]["previewUrl"] == f"https://storage.example/{VOICE_IDS[0]}.mp3"
    assert len(http.calls) == 2
    assert eleven_app.extensions["redis"].ttl(voices.PREVIEWS_KEY) > 300, "a success is kept for a day"


# ---------------------------------------------------------------- settings


@pytest.mark.parametrize("env", ["development", "test", "production"])
@pytest.mark.parametrize("providers", ["openai", "elevenlabs,polly", "", " , "])
def test_no_environment_accepts_an_unknown_voice_provider(env, providers):
    with pytest.raises(ValidationError, match="VOICE_PROVIDERS"):
        Settings(**{**PRODUCTION, "env": env, "voice_providers": providers})


@pytest.mark.parametrize("providers", ["mock", "elevenlabs,mock", " edge , mock "])
def test_production_refuses_the_mock_voice(providers):
    with pytest.raises(ValidationError, match=r"\bVOICE_PROVIDERS\b"):
        Settings(**{**PRODUCTION, "voice_providers": providers})


@pytest.mark.parametrize("providers", ["elevenlabs,edge", "edge", " edge , elevenlabs "])
def test_production_accepts_the_real_voice_providers_in_any_order(providers):
    settings = Settings(**{**PRODUCTION, "voice_providers": providers})
    assert settings.voice_provider_names == [p.strip() for p in providers.split(",")]


def test_the_mock_voice_is_allowed_outside_production():
    for env in ("development", "test"):
        assert Settings(**{**PRODUCTION, "env": env, "voice_providers": "mock"}).voice_provider_names == [
            "mock"
        ]


def test_the_voice_defaults_and_the_key_stays_out_of_printed_settings():
    settings = Settings(
        **{**PRODUCTION, "voice_providers": "elevenlabs,edge", "elevenlabs_api_key": ELEVEN_KEY}
    )
    assert ELEVEN_KEY not in repr(settings) and ELEVEN_KEY not in str(settings)
    defaults = Settings.model_fields
    assert defaults["voice_providers"].default == "elevenlabs,edge"
    assert defaults["elevenlabs_model"].default == "eleven_multilingual_v2"


def test_these_tests_cannot_reach_a_real_voice_service():
    """The guard in voice_support: the repo's .env holds real keys with a small free quota."""
    import edge_tts

    from .voice_support import NETWORK_ATTEMPTS, RealServiceCalled

    with pytest.raises(RealServiceCalled):
        requests.post(VOICES_URL, timeout=1)
    with pytest.raises(RealServiceCalled):
        requests.Session().get(VOICES_URL, timeout=1)
    with pytest.raises(RealServiceCalled):
        edge_tts.Communicate("hello", DEFAULT_VOICE.edge)
    for url in ("https://pixabay.com/api/videos/", "https://api.cloudflare.com/client/v4/accounts/x"):
        with pytest.raises(RealServiceCalled):
            requests.get(url, timeout=1)
    with pytest.raises(requests.ConnectionError):  # anything else: as if offline, and recorded
        requests.get("https://example.com/a.jpg", timeout=1)
    assert NETWORK_ATTEMPTS[-1] == ("GET", "https://example.com/a.jpg")
    NETWORK_ATTEMPTS.clear()  # these attempts were the point of the test


def test_the_tasks_soft_time_limit_stops_an_edge_voice_as_timed_out(keyless_app, outbox, http, edge):
    """Technical design 7 rule 3: a job past its time limit fails as timed_out. Celery raises
    SoftTimeLimitExceeded inside the running code; it must not be treated as a network error
    and retried (the hard limit would then kill the task and leave the job running)."""
    from celery.exceptions import SoftTimeLimitExceeded

    edge.error = SoftTimeLimitExceeded()
    me = creator(keyless_app)
    _, scene_id = voice_scene(keyless_app, me, "Out of time.")
    job = started(me.browser, scene_id)
    run_jobs(keyless_app)
    failed = get_job(me.browser, job["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "timed_out", failed
    assert len(edge.calls) == 1, "not retried"
