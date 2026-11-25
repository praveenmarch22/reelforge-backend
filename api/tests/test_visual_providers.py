"""The real visual code paths with every network call stubbed (no real service is called and
no real key is used): Pixabay search and lookup, the rendition choice, clip and thumbnail
downloads, Cloudflare FLUX answers, the image helpers, and the STOCK/IMAGE settings.
Technical design "Stock (Pixabay, later Pexels)"; API design C8-C10; PRD VI2, VI6, G-6."""

from __future__ import annotations

import base64
import json

import pytest
import requests
from pydantic import ValidationError

from reelforge_api.config import Settings
from reelforge_api.providers import stock as stock_module
from reelforge_api.providers.base import USER_AGENT, ProviderError
from reelforge_api.providers.image import PROMPT_MAX, checked_image, image_prompt
from reelforge_api.providers.stock import MAX_CLIP_BYTES, PixabayStock, StockClip, _pick_rendition, download

from .conftest import build_app
from .projects_support import creator, get_job, run_jobs
from .support import assert_error
from .test_worker_and_config import PRODUCTION
from .visual_support import (
    MP4_HEAD,
    gif_bytes,
    jpeg_bytes,
    png_bytes,
    regenerate_visual,
    scene_of,
    select_visual,
    stock_search,
    stored_visual_files,
    visual_assets,
    visual_project,
    visual_started,
)
from .voice_support import download as fetch_link
from .voice_support import no_real_voice_services  # noqa: F401 - autouse fixture

PIXABAY_KEY = "pixabay-test-key-0123456789"
CF_ACCOUNT = "cf-account-test"
CF_TOKEN = "cf-token-test-0123456789"
PIXABAY_URL = "https://pixabay.com/api/videos/"
CF_URL = (
    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/@cf/black-forest-labs/flux-1-schnell"
)
CLIP_URL = "https://cdn.pixabay.com/video/medium.mp4"
THUMB_URL = "https://cdn.pixabay.com/video/tiny.jpg"  # the small rendition still


@pytest.fixture(scope="module")
def pixabay_app(app):
    return build_app(stock_providers="pixabay", pixabay_api_key=PIXABAY_KEY)


@pytest.fixture(scope="module")
def cloudflare_app(app):
    return build_app(
        image_providers="cloudflare", cloudflare_account_id=CF_ACCOUNT, cloudflare_api_token=CF_TOKEN
    )


def response(status: int = 200, body=None, *, text: str | None = None) -> requests.Response:
    res = requests.Response()
    res.status_code = status
    res._content = (text if text is not None else json.dumps(body or {})).encode()
    return res


def rendition(height: int, width: int | None = None, size: int = 5_000_000, name: str = "medium") -> dict:
    return {
        "url": f"https://cdn.pixabay.com/video/{name}.mp4",
        "width": width or height * 16 // 9,
        "height": height,
        "size": size,
        "thumbnail": f"https://cdn.pixabay.com/video/{name}.jpg",
    }


def hit(clip_id: int = 123, duration: int = 30, **videos) -> dict:
    videos = videos or {
        "large": rendition(2160, name="large", size=60_000_000),
        "medium": rendition(1080, name="medium"),
        "small": rendition(720, name="small", size=2_000_000),
        "tiny": rendition(540, name="tiny", size=900_000),
    }
    return {
        "id": clip_id, "pageURL": f"https://pixabay.com/videos/id-{clip_id}/", "tags": "sea, waves",
        "duration": duration, "user": "anna", "videos": videos,
    }  # fmt: skip


class FakeAPI:
    """Stands in for requests.request (the provider HTTP helper)."""

    def __init__(self):
        self.calls: list[dict] = []
        self.pixabay: list = []
        self.cloudflare: list = []

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        queue = self.pixabay if url == PIXABAY_URL else self.cloudflare if url == CF_URL else None
        assert queue, f"unexpected call to {url}"
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer(dict(kwargs)) if callable(answer) else answer


class FakeDownload:
    """A streamed answer for requests.get."""

    def __init__(self, status: int = 200, chunks: list[bytes] | None = None, error: Exception | None = None):
        self.status_code, self.chunks, self.error = status, chunks if chunks is not None else [], error
        self.ok = status < 400
        self.content = b"".join(self.chunks)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=1):
        yield from self.chunks


class FakeGet:
    """Stands in for requests.get: answers by URL, records calls."""

    def __init__(self):
        self.calls: list[dict] = []
        self.answers: dict[str, FakeDownload | Exception] = {}

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        answer = self.answers.get(url)
        assert answer is not None, f"unexpected download of {url}"
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def api(monkeypatch) -> FakeAPI:
    fake = FakeAPI()
    monkeypatch.setattr("reelforge_api.providers.base.requests.request", fake)
    return fake


@pytest.fixture
def get(monkeypatch) -> FakeGet:
    fake = FakeGet()
    monkeypatch.setattr("requests.get", fake)
    return fake


def clip_file(size: int = 4096) -> list[bytes]:
    return [MP4_HEAD + b"\x00" * (size - len(MP4_HEAD))]


def search_answer(*hits, total: int | None = None) -> requests.Response:
    hits = hits or (hit(),)
    return response(200, {"total": total or len(hits), "totalHits": total or len(hits), "hits": list(hits)})


# ---------------------------------------------------------------- Pixabay search (C9)


def test_pixabay_is_searched_with_the_key_safe_search_and_paging(pixabay_app, outbox, api):
    api.pixabay = [search_answer(hit(1), hit(2), total=45)]
    me = creator(pixabay_app)
    _, scene_id = visual_project(pixabay_app, me)
    res = stock_search(me.browser, scene_id, q="ocean%20waves", page=2)
    assert res.status_code == 200, res.get_data(as_text=True)
    (call,) = api.calls
    assert call["method"] == "GET" and call["url"] == PIXABAY_URL
    assert call["params"] == {
        "key": PIXABAY_KEY,
        "safesearch": "true",
        "q": "ocean waves",
        "page": 2,
        "per_page": 20,
    }
    assert call["headers"] == {"User-Agent": USER_AGENT}
    body = res.get_json()
    assert body["nextPage"] == 3, "45 results: a third page exists"
    first = body["items"][0]
    assert first == {
        "providerId": "pixabay:1", "thumbnailUrl": "https://cdn.pixabay.com/video/tiny.jpg",
        "previewUrl": "https://cdn.pixabay.com/video/tiny.mp4", "durationSeconds": 30.0,
        "width": 3840, "height": 2160, "author": "anna", "source": "pixabay",
        "pageUrl": "https://pixabay.com/videos/id-1/",
    }  # fmt: skip
    assert PIXABAY_KEY not in json.dumps(body)


@pytest.mark.parametrize(
    ("total", "page", "next_page"), [(45, 3, None), (40, 2, None), (1000, 24, 25), (1000, 25, None)]
)
def test_the_last_page_has_no_next_page(pixabay_app, outbox, api, total, page, next_page):
    """Pixabay serves at most 500 results per query."""
    api.pixabay = [search_answer(hit(), total=total)]
    me = creator(pixabay_app)
    _, scene_id = visual_project(pixabay_app, me)
    assert stock_search(me.browser, scene_id, q="ocean", page=page).get_json()["nextPage"] == next_page


@pytest.mark.parametrize(
    "failure",
    [
        lambda: response(500, text="down"),
        lambda: response(429, text="slow down"),
        lambda: response(200, {"total": 0}),
        lambda: response(200, text="<html>"),
        lambda: requests.ConnectionError("unreachable"),
    ],
)
def test_pixabay_failing_is_retried_then_503(pixabay_app, outbox, api, failure):
    api.pixabay = [failure()]
    me = creator(pixabay_app)
    _, scene_id = visual_project(pixabay_app, me)
    assert_error(stock_search(me.browser, scene_id, q="ocean"), 503, "provider_unavailable")
    assert len(api.calls) == 3


def test_a_rejected_key_is_not_retried(pixabay_app, outbox, api):
    api.pixabay = [response(400, text="[ERROR 400] Invalid or missing API key")]
    me = creator(pixabay_app)
    _, scene_id = visual_project(pixabay_app, me)
    assert_error(stock_search(me.browser, scene_id, q="ocean"), 503, "provider_unavailable")
    assert len(api.calls) == 1


# ---------------------------------------------------------------- choosing a rendition


def test_the_smallest_rendition_at_least_1080_high_is_downloaded():
    videos = hit()["videos"]
    assert _pick_rendition(videos)["height"] == 1080
    del videos["medium"]
    assert _pick_rendition(videos)["height"] == 2160
    videos["large"]["size"] = MAX_CLIP_BYTES + 1
    assert _pick_rendition(videos)["height"] == 720, "nothing tall enough under 150 MB: the tallest"
    assert _pick_rendition({"x": {"url": "", "height": 1080}}) is None
    assert _pick_rendition({}) is None


def test_a_hit_without_a_usable_rendition_is_left_out(pixabay_app, outbox, api):
    broken = hit(7, large=rendition(2160, size=MAX_CLIP_BYTES + 1, name="large"))
    api.pixabay = [search_answer(broken, hit(8))]
    me = creator(pixabay_app)
    _, scene_id = visual_project(pixabay_app, me)
    items = stock_search(me.browser, scene_id, q="ocean").get_json()["items"]
    assert [i["providerId"] for i in items] == ["pixabay:8"]


# ---------------------------------------------------------------- a Pixabay clip in a job


def test_a_pixabay_clip_is_downloaded_with_its_thumbnail_and_credits(pixabay_app, database, outbox, api, get):
    api.pixabay = [search_answer(hit(123, duration=30))]
    get.answers = {CLIP_URL: FakeDownload(chunks=clip_file()), THUMB_URL: FakeDownload(chunks=[jpeg_bytes()])}
    me = creator(pixabay_app)
    project, scene_id = visual_project(pixabay_app, me, stockQuery="ocean")
    job = visual_started(me.browser, scene_id)
    run_jobs(pixabay_app)
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["provider"] == "pixabay", done
    assert [c["url"] for c in get.calls] == [CLIP_URL, THUMB_URL]
    assert all(c["headers"] == {"User-Agent": USER_AGENT} for c in get.calls)
    assert get.calls[0]["stream"] is True

    (asset,) = visual_assets(database, project["id"])
    assert asset["provider"] == "pixabay" and asset["kind"] == "video_clip" and asset["size_bytes"] == 4096
    assert (asset["width"], asset["height"]) == (1920, 1080), "the downloaded rendition"
    assert asset["meta"]["providerId"] == "pixabay:123"
    assert asset["meta"]["license"] == "Pixabay Content License"
    base = f"users/{me.id}/projects/{project['id']}/visual/{job['id']}"
    assert asset["meta"]["thumbnailKey"] == f"{base}-thumb.jpg"
    assert stored_visual_files(pixabay_app, me.id, project["id"]) == [f"{base}-thumb.jpg", f"{base}.mp4"]

    visual = scene_of(me.browser, project["id"], scene_id)["visual"]
    assert visual["provider"] == "pixabay" and visual["author"] == "anna"
    assert visual["sourceUrl"] == "https://pixabay.com/videos/id-123/"
    assert fetch_link(visual["thumbnailUrl"])[:3] == b"\xff\xd8\xff"
    assert fetch_link(visual["url"])[4:8] == b"ftyp"


@pytest.mark.parametrize(
    "thumbnail",
    [
        lambda: FakeDownload(404),
        lambda: FakeDownload(302),
        lambda: FakeDownload(chunks=[b"<html>not an image</html>"]),
        lambda: FakeDownload(chunks=[b"\xff\xd8\xff" + b"\x00" * (2 * 1024 * 1024)]),
        lambda: requests.ConnectionError("unreachable"),
    ],
)
def test_a_missing_or_odd_thumbnail_does_not_fail_the_clip(
    pixabay_app, database, outbox, api, get, thumbnail
):
    api.pixabay = [search_answer(hit(5))]
    get.answers = {CLIP_URL: FakeDownload(chunks=clip_file()), THUMB_URL: thumbnail()}
    me = creator(pixabay_app)
    project, scene_id = visual_project(pixabay_app, me, stockQuery="ocean")
    job = visual_started(me.browser, scene_id)
    run_jobs(pixabay_app)
    assert get_job(me.browser, job["id"])["state"] == "succeeded"
    visual = scene_of(me.browser, project["id"], scene_id)["visual"]
    assert visual["kind"] == "video" and visual["thumbnailUrl"] is None
    assert len(stored_visual_files(pixabay_app, me.id, project["id"])) == 1


def hit_at(clip_id: int, url: str, *, thumbnail: str = "", duration: int = 30) -> dict:
    """A hit with one 1080-high rendition at this address."""
    video = {"url": url, "width": 1920, "height": 1080, "size": 4096, "thumbnail": thumbnail}
    return {
        "id": clip_id, "pageURL": f"https://pixabay.com/videos/id-{clip_id}/", "duration": duration,
        "user": "anna", "videos": {"medium": video},
    }  # fmt: skip


def clip_url(n: int) -> str:
    return f"https://cdn.pixabay.com/video/{n}/medium.mp4"


@pytest.mark.parametrize(
    "broken",
    [
        lambda: FakeDownload(404),
        lambda: FakeDownload(chunks=[b"<html>not a video</html>"]),
        lambda: requests.ConnectionError("unreachable"),
        lambda: FakeDownload(302),
    ],
)
def test_a_clip_that_cannot_be_downloaded_makes_way_for_the_next_one(
    pixabay_app, database, outbox, api, get, broken
):
    """Fix log QB4S2-002: a download failure tries the next fitting clip."""
    api.pixabay = [search_answer(hit_at(1, clip_url(1)), hit_at(2, clip_url(2)))]
    get.answers = {clip_url(1): broken(), clip_url(2): FakeDownload(chunks=clip_file())}
    me = creator(pixabay_app)
    project, scene_id = visual_project(pixabay_app, me, stockQuery="ocean")
    job = visual_started(me.browser, scene_id)
    run_jobs(pixabay_app)
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["output"]["stockFallback"] is False, done
    (asset,) = visual_assets(database, project["id"])
    assert asset["meta"]["providerId"] == "pixabay:2"
    assert stored_visual_files(pixabay_app, me.id, project["id"]) == [asset["s3_key"]]
    assert all(c.get("allow_redirects") is False for c in get.calls), "redirects are never followed"


def test_three_failed_downloads_end_in_the_free_ai_image(pixabay_app, database, outbox, api, get):
    hits = [hit_at(n, clip_url(n)) for n in range(1, 5)]
    api.pixabay = [search_answer(*hits)]
    get.answers = {clip_url(n): FakeDownload(500) for n in range(1, 5)}
    me = creator(pixabay_app)
    project, scene_id = visual_project(pixabay_app, me, stockQuery="ocean")
    job = visual_started(me.browser, scene_id)
    run_jobs(pixabay_app)
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["output"]["stockFallback"] is True
    assert sorted(c["url"] for c in get.calls) == [clip_url(n) for n in range(1, 4)], "up to three clips"
    (asset,) = visual_assets(database, project["id"])
    assert asset["kind"] == "image" and done["creditsReserved"] == 0
    assert not any(k.endswith(".mp4") for k in stored_visual_files(pixabay_app, me.id, project["id"]))


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.pixabay.com/video/1/medium.mp4",
        "https://evil.example/video.mp4",
        "https://cdn.pixabay.com:8443/video/1/medium.mp4",
        "https://cdn.pixabay.com.evil.example/video.mp4",
        "https://cdn.pixabay.com@evil.example/video.mp4",
        "https://vimeo.com/video.mp4",
        "ftp://cdn.pixabay.com/video.mp4",
    ],
)
def test_clips_are_only_downloaded_from_the_providers_own_servers(
    pixabay_app, database, outbox, api, get, url
):
    """Fix log QB4S2-005: only https on cdn.pixabay.com or pixabay.com; nothing else is fetched."""
    api.pixabay = [search_answer(hit_at(1, url, thumbnail=url.replace(".mp4", ".jpg")))]
    me = creator(pixabay_app)
    project, scene_id = visual_project(pixabay_app, me, stockQuery="ocean")
    job = visual_started(me.browser, scene_id)
    run_jobs(pixabay_app)
    assert get.calls == [], "the refused address is never called"
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["output"]["stockFallback"] is True


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://cdn.pixabay.com/v/1.mp4", True),
        ("https://pixabay.com/v/1.mp4", True),
        ("https://cdn.pixabay.com:443/v/1.mp4", True),
        ("http://cdn.pixabay.com/v/1.mp4", False),
        ("https://cdn.pixabay.com:8443/v/1.mp4", False),
        ("https://player.pixabay.com/v/1.mp4", False),
        ("https://cdn.pixabay.com.evil.example/v.mp4", False),
        ("https://cdn.pixabay.com@evil.example/v.mp4", False),
        ("//cdn.pixabay.com/v/1.mp4", False),
        ("", False),
    ],
)
def test_allowed_download_as_a_unit(url, allowed):
    from reelforge_api.visuals import allowed_download

    assert allowed_download(clip_at(url), url) is allowed
    assert allowed_download(StockClip("mock:1", 1, 1, 1, "", "", "", "", url, 0, 1, 1), url) is False


def test_a_thumbnail_on_another_server_is_not_fetched(pixabay_app, database, outbox, api, get):
    api.pixabay = [search_answer(hit_at(1, clip_url(1), thumbnail="https://evil.example/t.jpg"))]
    get.answers = {clip_url(1): FakeDownload(chunks=clip_file())}
    me = creator(pixabay_app)
    project, scene_id = visual_project(pixabay_app, me, stockQuery="ocean")
    visual_started(me.browser, scene_id)
    run_jobs(pixabay_app)
    assert [c["url"] for c in get.calls] == [clip_url(1)]
    assert scene_of(me.browser, project["id"], scene_id)["visual"]["thumbnailUrl"] is None


def test_choosing_a_pixabay_clip_looks_it_up_by_id(pixabay_app, database, outbox, api, get):
    api.pixabay = [search_answer(hit(777))]
    get.answers = {CLIP_URL: FakeDownload(chunks=clip_file()), THUMB_URL: FakeDownload(404)}
    me = creator(pixabay_app)
    project, scene_id = visual_project(pixabay_app, me)
    job = select_visual(me.browser, scene_id, {"stock": "pixabay:777"}).get_json()["job"]
    run_jobs(pixabay_app)
    assert get_job(me.browser, job["id"])["state"] == "succeeded"
    assert api.calls[0]["params"] == {"key": PIXABAY_KEY, "safesearch": "true", "id": "777"}
    assert visual_assets(database, project["id"])[0]["meta"]["providerId"] == "pixabay:777"


def test_a_pixabay_clip_that_is_gone_is_stock_unavailable(pixabay_app, database, outbox, api, get):
    api.pixabay = [response(200, {"total": 0, "totalHits": 0, "hits": []})]
    me = creator(pixabay_app)
    project, scene_id = visual_project(pixabay_app, me)
    job = select_visual(me.browser, scene_id, {"stock": "pixabay:404"}).get_json()["job"]
    run_jobs(pixabay_app)
    assert get_job(me.browser, job["id"])["error"]["code"] == "stock_unavailable"
    assert get.calls == []


def test_an_id_the_provider_refuses_is_stock_unavailable(pixabay_app, database, outbox, api, get):
    api.pixabay = [response(400, text="[ERROR 400] invalid id")]
    me = creator(pixabay_app)
    _, scene_id = visual_project(pixabay_app, me)
    job = select_visual(me.browser, scene_id, {"stock": "pixabay:12345678901234567890"}).get_json()["job"]
    run_jobs(pixabay_app)
    assert get_job(me.browser, job["id"])["error"]["code"] == "stock_unavailable"
    assert len(api.calls) == 1, "a refusal is not retried"


def test_only_set_up_and_switched_on_stock_providers_ids_are_accepted(pixabay_app, database, outbox, api):
    """Fix log, Tester obs. 3 and BUG-042."""
    me = creator(pixabay_app)
    _, scene_id = visual_project(pixabay_app, me)
    assert_error(select_visual(me.browser, scene_id, {"stock": "mock:1"}), 400, "validation_error")
    database.run("INSERT INTO provider_switches (provider, enabled) VALUES ('pixabay', false)")
    assert_error(select_visual(me.browser, scene_id, {"stock": "pixabay:1"}), 400, "validation_error")
    assert api.calls == []


@pytest.fixture(scope="module")
def pixabay_without_key(app):
    return build_app(stock_providers="pixabay", pixabay_api_key="")


def test_a_provider_without_a_key_has_no_valid_ids(pixabay_without_key, outbox, api):
    me = creator(pixabay_without_key)
    _, scene_id = visual_project(pixabay_without_key, me)
    assert_error(select_visual(me.browser, scene_id, {"stock": "pixabay:1"}), 400, "validation_error")


def test_the_user_agent_names_only_the_product():
    """Fix log question 1: no personal address in every request."""
    assert USER_AGENT == "ReelForge/1.0"


# ---------------------------------------------------------------- download() as a unit


def big_clip(n_chunks: int, chunk: int) -> list[bytes]:
    return [MP4_HEAD + b"\x00" * (chunk - len(MP4_HEAD))] + [b"\x00" * chunk] * (n_chunks - 1)


def clip_at(url: str = CLIP_URL) -> StockClip:
    return StockClip("pixabay:1", 10, 1920, 1080, "a", "", "", "", url, 0, 1920, 1080)


def test_download_returns_the_clip_as_a_file(get):
    get.answers = {CLIP_URL: FakeDownload(chunks=big_clip(3, 1000))}
    with download(clip_at()) as f:
        data = f.read()
    assert len(data) == 3000 and data[4:8] == b"ftyp"


def test_download_stops_at_the_size_cap(get, monkeypatch):
    monkeypatch.setattr(stock_module, "MAX_CLIP_BYTES", 2500)
    get.answers = {CLIP_URL: FakeDownload(chunks=big_clip(3, 1000))}
    with pytest.raises(ProviderError, match="too large") as raised:
        download(clip_at())
    assert not raised.value.retryable
    assert MAX_CLIP_BYTES == 150 * 1024 * 1024


@pytest.mark.parametrize(
    ("answer", "retryable"),
    [
        (FakeDownload(404), False),
        (FakeDownload(403), False),
        (FakeDownload(302), False),
        (FakeDownload(301), False),
        (FakeDownload(503), True),
        (requests.Timeout("slow"), True),
        (requests.ConnectionError("down"), True),
        (FakeDownload(chunks=[b"GIF89a" + b"\x00" * 100]), False),
        (FakeDownload(chunks=[]), False),
    ],
)
def test_download_errors_are_provider_errors(get, answer, retryable):
    get.answers = {CLIP_URL: answer}
    with pytest.raises(ProviderError) as raised:
        download(clip_at())
    assert raised.value.retryable is retryable


# ---------------------------------------------------------------- Cloudflare FLUX


def cf_ok(data: bytes | None = None) -> requests.Response:
    return response(
        200, {"result": {"image": base64.b64encode(data or jpeg_bytes()).decode()}, "success": True}
    )


def test_cloudflare_is_asked_with_the_token_and_no_seed(cloudflare_app, database, outbox, api):
    api.cloudflare = [cf_ok()]
    me = creator(cloudflare_app)
    project, scene_id = visual_project(cloudflare_app, me, visualPrompt="a lighthouse", mood="calm")
    job = visual_started(me.browser, scene_id, {"visualSource": "ai_image"})
    run_jobs(cloudflare_app)
    done = get_job(me.browser, job["id"])
    assert done["state"] == "succeeded" and done["provider"] == "cloudflare"
    (call,) = api.calls
    assert call["method"] == "POST" and call["url"] == CF_URL
    assert call["headers"] == {"User-Agent": USER_AGENT, "Authorization": f"Bearer {CF_TOKEN}"}
    assert (
        set(call["json"]) == {"prompt", "steps"} and call["json"]["steps"] == 4
    ), "no seed: FLUX schnell takes none"
    assert call["json"]["prompt"].startswith("a lighthouse")
    (asset,) = visual_assets(database, project["id"])
    assert asset["provider"] == "cloudflare" and asset["content_type"] == "image/jpeg"
    assert (asset["width"], asset["height"]) == (64, 96)
    assert asset["meta"]["model"] == "@cf/black-forest-labs/flux-1-schnell"
    assert CF_TOKEN not in json.dumps(done)


def test_a_png_from_cloudflare_is_stored_as_png(cloudflare_app, database, outbox, api):
    api.cloudflare = [cf_ok(png_bytes())]
    me = creator(cloudflare_app)
    project, scene_id = visual_project(cloudflare_app, me)
    job = visual_started(me.browser, scene_id, {"visualSource": "ai_image"})
    run_jobs(cloudflare_app)
    (asset,) = visual_assets(database, project["id"])
    assert asset["content_type"] == "image/png" and asset["s3_key"].endswith(f"{job['id']}.png")


@pytest.mark.parametrize(
    "answer",
    [
        lambda: response(200, {"result": {}, "success": True}),
        lambda: response(200, {"result": None}),
        lambda: response(200, {"result": "image"}),
        lambda: response(200, {"result": {"image": "!!not base64!!"}}),
        lambda: response(200, {"result": {"image": base64.b64encode(b"just some text").decode()}}),
        lambda: cf_ok(gif_bytes()),
        lambda: response(500, text="down"),
        lambda: response(200, text="<html>"),
    ],
)
def test_unusable_cloudflare_answers_are_retried_then_fail_and_refund(
    cloudflare_app, database, outbox, api, answer
):
    api.cloudflare = [answer()]
    me = creator(cloudflare_app)
    project, scene_id = visual_project(cloudflare_app, me)
    job = visual_started(me.browser, scene_id, {"visualSource": "ai_image"})
    run_jobs(cloudflare_app)
    failed = get_job(me.browser, job["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "provider_failed"
    assert failed["creditsRefunded"] is True
    assert len(api.calls) == 3
    assert stored_visual_files(cloudflare_app, me.id, project["id"]) == []


def test_a_cloudflare_nsfw_refusal_is_content_refused_without_retries(cloudflare_app, database, outbox, api):
    api.cloudflare = [response(400, text='{"errors":[{"message":"NSFW content detected"}]}')]
    me = creator(cloudflare_app)
    _, scene_id = visual_project(cloudflare_app, me)
    job = visual_started(me.browser, scene_id, {"visualSource": "ai_image"})
    run_jobs(cloudflare_app)
    failed = get_job(me.browser, job["id"])
    assert failed["error"]["code"] == "content_refused" and failed["creditsRefunded"] is True
    assert len(api.calls) == 1


@pytest.fixture(scope="module")
def cloudflare_without_token(app):
    return build_app(image_providers="cloudflare", cloudflare_account_id=CF_ACCOUNT, cloudflare_api_token="")


def test_without_cloudflare_credentials_an_ai_image_is_503(cloudflare_without_token, outbox, api):
    me = creator(cloudflare_without_token)
    _, scene_id = visual_project(cloudflare_without_token, me)
    assert_error(
        regenerate_visual(me.browser, scene_id, {"visualSource": "ai_image"}), 503, "provider_unavailable"
    )
    assert api.calls == []


# ---------------------------------------------------------------- image helpers


def test_checked_image_accepts_jpeg_and_png_only():
    assert checked_image(jpeg_bytes(10, 20)) == ("image/jpeg", "jpg", 10, 20)
    assert checked_image(png_bytes(30, 40)) == ("image/png", "png", 30, 40)
    for bad in (gif_bytes(), b"", b"not an image", jpeg_bytes()[:40]):
        with pytest.raises(ProviderError) as raised:
            checked_image(bad)
        assert raised.value.retryable


def test_the_image_prompt_carries_scene_mood_and_style_anchor():
    prompt = image_prompt("a runner", "sad", "film noir", "an old man")
    assert prompt.startswith("a runner, overcast cool light")
    assert "film noir style, featuring an old man" in prompt
    assert prompt.endswith("vertical 9:16 composition, high detail, no text, no watermark")
    assert "cinematic style" in image_prompt("a runner", "calm", "", "")
    assert "featuring" not in image_prompt("a runner", "calm", "", "")
    assert len(image_prompt("x" * 5000, "calm", "y", "z")) == PROMPT_MAX


# ---------------------------------------------------------------- settings


@pytest.mark.parametrize("env", ["development", "test", "production"])
@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("stock_providers", "pexels"), ("stock_providers", ""), ("stock_providers", " , "),
        ("image_providers", "dalle"), ("image_providers", ""), ("image_providers", "cloudflare,midjourney"),
    ],
)  # fmt: skip
def test_no_environment_accepts_unknown_visual_providers(env, setting, value):
    with pytest.raises(ValidationError, match=setting.upper()):
        Settings(**{**PRODUCTION, "env": env, setting: value})


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("stock_providers", "mock"),
        ("stock_providers", "pixabay,mock"),
        ("image_providers", "mock"),
        ("image_providers", " cloudflare , mock "),
    ],
)
def test_production_refuses_the_visual_mocks(setting, value):
    with pytest.raises(ValidationError, match=setting.upper()):
        Settings(**{**PRODUCTION, setting: value})


def test_the_visual_defaults_and_keys_stay_out_of_printed_settings():
    settings = Settings(**{**PRODUCTION, "pixabay_api_key": PIXABAY_KEY, "cloudflare_api_token": CF_TOKEN})
    assert settings.stock_provider_names == ["pixabay"] and settings.image_provider_names == ["cloudflare"]
    assert settings.cloudflare_image_model == "@cf/black-forest-labs/flux-1-schnell"
    for secret in (PIXABAY_KEY, CF_TOKEN):
        assert secret not in repr(settings) and secret not in str(settings)
    for env in ("development", "test"):
        assert Settings(**{**PRODUCTION, "env": env, "stock_providers": "mock", "image_providers": "mock"})


def test_pixabay_without_a_key_is_skipped():
    with pytest.raises(ProviderError):
        PixabayStock(Settings(**{**PRODUCTION, "pixabay_api_key": ""})).search("ocean")


def test_at_most_three_downloads_are_tried_across_result_pages(pixabay_app, database, outbox, api, get):
    """Fix log round 2: `downloads_left` is 3 for the whole job, not per page."""
    page_one = search_answer(hit_at(1, clip_url(1)), hit_at(2, clip_url(2)), total=45)
    page_two = search_answer(hit_at(3, clip_url(3)), hit_at(4, clip_url(4)), total=45)
    api.pixabay = [page_one, page_two, page_two]
    get.answers = {clip_url(n): FakeDownload(404) for n in range(1, 5)}
    me = creator(pixabay_app)
    project, scene_id = visual_project(pixabay_app, me, stockQuery="ocean")
    job = visual_started(me.browser, scene_id)
    run_jobs(pixabay_app)
    assert [c["url"] for c in get.calls] == [clip_url(1), clip_url(2), clip_url(3)]
    assert [c["params"]["page"] for c in api.calls] == [1, 2]
    assert get_job(me.browser, job["id"])["output"]["stockFallback"] is True
