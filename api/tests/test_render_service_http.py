"""The worker talking to the real render service's HTTP contract (API design group K;
TECHNICAL_DESIGN "Render service"; milestone B5), with a fake service on 127.0.0.1.

The fake answers like renderer/server.mjs: K1 POST /renders, K2 GET /renders/:id, K2b DELETE,
with the Bearer token. On success it uploads to the one-time links it was given, as the real
service does, so the worker finds the files in storage.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
import urllib3

from reelforge_api.renders import START_TRIES, UNREACHABLE_POLLS

from .projects_support import balance, creator, get_job, get_project, run_jobs
from .render_support import (
    get_render,
    in_another_thread,
    ready_project,
    render_keys,
    second_browser,
    started,
)
from .voice_support import no_real_voice_services  # noqa: F401 - autouse fixture

TOKEN = "k2Vq8sLm4Rt7Wx1Nz5Bc9Df3Gh6Jp0Ya"  # 34 characters, as production asks
VIDEO = b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 2000
COVER = b"\xff\xd8\xff\xe0" + b"\x02" * 300
_REAL_SESSION_REQUEST = requests.Session.request  # before any test's network guard replaces it


class FakeService:
    """What each attempt does is set per attempt number (1, 2): `succeed`, `fail` (K2 says
    failed), `lost` (K2 answers 404), `refuse` (K1 answers 500), `broken` (K2 answers 500),
    `run` (K2 says running until told otherwise), `empty` (says succeeded, uploads nothing),
    `restarting` (K1 answers 503 twice, then takes it and succeeds), `late` (uploads its files,
    then says failed). A render it was told to stop (K2b) is `cancelled` from then on."""

    def __init__(self):
        self.plan: dict[int, str] = {1: "succeed", 2: "succeed"}
        self.calls: list[dict] = []
        self.bodies: dict[str, dict] = {}
        self.cancelled: set[str] = set()
        self.video = {1: VIDEO, 2: VIDEO}
        self.starts: dict[str, int] = {}
        self.on_poll = None
        self.polls = 0
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _answer(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _record(self, body=None) -> None:
                with fake.lock:
                    fake.calls.append(
                        {
                            "method": self.command,
                            "path": self.path,
                            "auth": self.headers.get("Authorization"),
                            "body": body,
                        }
                    )

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self._record(body)
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self._answer(401, {"error": "unauthorised"})
                attempt = int(body["renderId"].rsplit("-", 1)[1])
                fake.bodies[body["renderId"]] = body
                fake.starts[body["renderId"]] = fake.starts.get(body["renderId"], 0) + 1
                if fake.plan[attempt] == "refuse":
                    return self._answer(500, {"error": "boom"})
                if fake.plan[attempt] == "restarting" and fake.starts[body["renderId"]] <= 2:
                    return self._answer(503, {"error": "starting"})
                return self._answer(202, {"state": "queued", "progress": 0, "error": None})

            def do_GET(self):  # noqa: N802
                self._record()
                render_id = self.path.rsplit("/", 1)[1]
                fake.polls += 1
                if fake.on_poll is not None:
                    fake.on_poll(fake.polls)
                attempt = int(render_id.rsplit("-", 1)[1])
                what = fake.plan[attempt]
                if render_id in fake.cancelled:
                    return self._answer(200, {"state": "cancelled", "progress": 40, "error": None})
                if what == "late":
                    body = fake.bodies[render_id]
                    upload(body["videoUploadUrl"], fake.video[attempt], "video/mp4")
                    upload(body["thumbnailUploadUrl"], COVER, "image/jpeg")
                    return self._answer(200, {"state": "failed", "progress": 99, "error": "late failure"})
                if what == "lost":
                    return self._answer(404, {"error": "unknown render"})
                if what == "broken":
                    return self._answer(500, {"error": "boom"})
                if what == "fail":
                    return self._answer(200, {"state": "failed", "progress": 30, "error": "Remotion said no"})
                if what == "run":
                    return self._answer(200, {"state": "running", "progress": 40, "error": None})
                if what in ("succeed", "restarting"):
                    body = fake.bodies[render_id]
                    upload(body["videoUploadUrl"], fake.video[attempt], "video/mp4")
                    upload(body["thumbnailUploadUrl"], COVER, "image/jpeg")
                return self._answer(
                    200,
                    {"state": "succeeded", "progress": 100, "error": None, "sizeBytes": len(VIDEO),
                     "durationSeconds": 12.34, "width": 720, "height": 1280},
                )  # fmt: skip

            def do_DELETE(self):  # noqa: N802
                self._record()
                fake.cancelled.add(self.path.rsplit("/", 1)[1])
                return self._answer(200, {"state": "cancelled", "progress": 40, "error": None})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def of(self, method: str) -> list[dict]:
        return [c for c in self.calls if c["method"] == method]

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def upload(url: str, data: bytes, content_type: str) -> None:
    answer = urllib3.PoolManager().request("PUT", url, body=data, headers={"Content-Type": content_type})
    assert answer.status == 200, answer.data


@pytest.fixture
def service(app, monkeypatch, no_real_voice_services):  # noqa: F811 - the guard runs first
    """A fake render service, with the worker pointed at it. Only 127.0.0.1 is let through the
    test's network guard."""
    fake = FakeService()
    settings = app.extensions["settings"]
    monkeypatch.setattr(settings, "render_service_url", fake.url + "/")
    monkeypatch.setattr(settings, "render_service_token", TOKEN)
    guard = requests.request

    def local_only(method, url, *args, **kwargs):
        if str(url).startswith(fake.url):
            with requests.Session() as session:
                return _REAL_SESSION_REQUEST(session, method, url, *args, **kwargs)
        return guard(method, url, *args, **kwargs)

    monkeypatch.setattr("requests.request", local_only)
    yield fake
    fake.stop()


@pytest.fixture
def down(app, monkeypatch, no_real_voice_services):  # noqa: F811
    """The worker pointed at a port where nothing listens."""
    fake = FakeService()
    fake.stop()
    settings = app.extensions["settings"]
    monkeypatch.setattr(settings, "render_service_url", fake.url)
    monkeypatch.setattr(settings, "render_service_token", TOKEN)

    def local_only(method, url, *args, **kwargs):
        with requests.Session() as session:
            return _REAL_SESSION_REQUEST(session, method, url, *args, **kwargs)

    monkeypatch.setattr("requests.request", local_only)
    return fake


def exists(app, key: str) -> bool:
    with app.app_context():
        return app.extensions["storage"].exists(key)


def test_a_render_through_the_service_follows_the_contract(app, service):
    me = creator(app)
    project = ready_project(app, me.browser)
    body = started(me.browser, project["id"])
    run_jobs(app)

    posts = service.of("POST")
    assert len(posts) == 1 and posts[0]["path"] == "/renders"
    assert all(c["auth"] == f"Bearer {TOKEN}" for c in service.calls)
    sent = posts[0]["body"]
    assert set(sent) == {"renderId", "timeline", "videoUploadUrl", "thumbnailUploadUrl"}
    assert sent["renderId"] == f"{body['render']['id']}-1"
    assert len(sent["renderId"]) <= 64 and all(ch.isalnum() or ch == "-" for ch in sent["renderId"])
    assert "onMediaError" not in sent["timeline"]
    assert sent["timeline"]["fps"] == 30 and sent["timeline"]["scenes"]
    for scene in sent["timeline"]["scenes"]:
        for part in ("visual", "voice"):
            got = urllib3.PoolManager().request("GET", scene[part]["src"])
            assert got.status == 200 and got.data, scene[part]["src"]
    assert all(c["path"] == f"/renders/{sent['renderId']}" for c in service.of("GET"))
    assert service.of("DELETE") == []

    done = get_render(me.browser, body["render"]["id"])
    assert done["state"] == "succeeded"
    assert done["video"]["sizeBytes"] == len(VIDEO) and done["video"]["durationSeconds"] == 12.34
    assert urllib3.PoolManager().request("GET", done["video"]["url"]).data == VIDEO
    assert urllib3.PoolManager().request("GET", done["thumbnailUrl"]).data == COVER
    assert get_project(me.browser, project["id"])["status"] == "rendered"


@pytest.mark.parametrize("first", ["fail", "lost", "refuse"])
def test_a_failed_first_attempt_is_tried_once_more(app, service, first):
    """R-1: the service reports failure, or has forgotten the render (restart), or refuses it."""
    service.plan = {1: first, 2: "succeed"}
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    body = started(me.browser, project["id"])
    run_jobs(app)
    ids = [c["body"]["renderId"] for c in service.of("POST")]
    first_id, second_id = f"{body['render']['id']}-1", f"{body['render']['id']}-2"
    assert ids == [first_id] * (START_TRIES if first == "refuse" else 1) + [second_id]
    assert get_render(me.browser, body["render"]["id"])["state"] == "succeeded"
    assert balance(me.browser) == before - 2


def test_a_service_that_stops_answering_fails_the_attempt(app, service):
    service.plan = {1: "broken", 2: "succeed"}
    me = creator(app)
    project = ready_project(app, me.browser)
    body = started(me.browser, project["id"])
    run_jobs(app)
    first = f"/renders/{body['render']['id']}-1"
    assert len([c for c in service.of("GET") if c["path"] == first]) == UNREACHABLE_POLLS
    # If it comes back, the abandoned attempt must not go on (QA round 1).
    assert [d["path"] for d in service.of("DELETE")] == [first]
    assert get_render(me.browser, body["render"]["id"])["state"] == "succeeded"


@pytest.mark.parametrize("plan", [("fail", "fail"), ("lost", "refuse"), ("broken", "fail")])
def test_two_failed_attempts_fail_the_render_with_a_refund(app, service, plan):
    service.plan = {1: plan[0], 2: plan[1]}
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    body = started(me.browser, project["id"])
    run_jobs(app)
    assert len({c["body"]["renderId"] for c in service.of("POST")}) == 2
    job = get_job(me.browser, body["job"]["id"])
    assert job["state"] == "failed" and job["error"]["code"] == "render_failed" and job["creditsRefunded"]
    assert get_render(me.browser, body["render"]["id"])["error"]["code"] == "render_failed"
    assert get_project(me.browser, project["id"])["status"] == "render_failed"
    assert balance(me.browser) == before


def test_a_service_that_is_down_fails_the_render_with_a_refund(app, down):
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    body = started(me.browser, project["id"])
    run_jobs(app)
    job = get_job(me.browser, body["job"]["id"])
    assert job["state"] == "failed" and job["error"]["code"] == "render_failed" and job["creditsRefunded"]
    assert get_project(me.browser, project["id"])["status"] == "render_failed"
    assert balance(me.browser) == before


def test_cancelling_the_job_stops_the_render_at_the_service(app, service):
    service.plan = {1: "run", 2: "run"}
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    body = started(me.browser, project["id"])
    other = second_browser(app, me.account)
    answers: list = []

    def on_poll(n):
        if n == 2:
            answers.append(in_another_thread(other.post, f"/jobs/{body['job']['id']}/cancel"))

    service.on_poll = on_poll
    run_jobs(app)
    assert answers and answers[0].status_code == 200
    deletes = service.of("DELETE")
    assert [d["path"] for d in deletes] == [f"/renders/{body['render']['id']}-1"]
    assert deletes[0]["auth"] == f"Bearer {TOKEN}"
    assert len(service.of("POST")) == 1  # not tried again after a cancel
    assert get_render(me.browser, body["render"]["id"])["state"] == "cancelled"
    assert get_project(me.browser, project["id"])["status"] == "ready"
    assert balance(me.browser) == before


def test_a_success_without_the_files_does_not_succeed(app, service):
    """The service says succeeded but nothing reached storage: the job must not succeed, and
    the credits come back."""
    service.plan = {1: "empty", 2: "empty"}
    me = creator(app)
    project = ready_project(app, me.browser)
    before = balance(me.browser)
    body = started(me.browser, project["id"])
    run_jobs(app)
    job = get_job(me.browser, body["job"]["id"])
    assert job["state"] == "failed" and job["creditsRefunded"] is True
    assert get_render(me.browser, body["render"]["id"])["state"] == "failed"
    assert get_project(me.browser, project["id"])["status"] == "render_failed"
    assert balance(me.browser) == before
    for n in (1, 2):
        video, cover = render_keys(me.id, project["id"], body["render"]["id"], n)
        assert not exists(app, video) and not exists(app, cover)


def test_a_restarting_service_is_asked_again_within_the_same_attempt(app, service):
    """A service that refuses a start or two (restarting, 503 while it bundles) is asked again;
    that does not use up the attempt (QA round 1)."""
    service.plan = {1: "restarting", 2: "succeed"}
    me = creator(app)
    project = ready_project(app, me.browser)
    body = started(me.browser, project["id"])
    run_jobs(app)
    ids = [c["body"]["renderId"] for c in service.of("POST")]
    assert ids == [f"{body['render']['id']}-1"] * 3
    assert get_render(me.browser, body["render"]["id"])["state"] == "succeeded"


def test_the_files_of_a_failed_attempt_are_deleted_and_the_retry_keeps_its_own(app, service):
    """Each attempt uploads to its own files; an attempt that uploaded and then failed leaves
    nothing behind, and the render plays the second attempt's MP4."""
    service.plan = {1: "late", 2: "succeed"}
    service.video[2] = VIDEO + b"second"
    me = creator(app)
    project = ready_project(app, me.browser)
    body = started(me.browser, project["id"])
    run_jobs(app)
    first, second = (render_keys(me.id, project["id"], body["render"]["id"], n) for n in (1, 2))
    assert not any(exists(app, key) for key in first)
    assert all(exists(app, key) for key in second)
    done = get_render(me.browser, body["render"]["id"])
    assert urllib3.PoolManager().request("GET", done["video"]["url"]).data == VIDEO + b"second"
    assert done["video"]["sizeBytes"] == len(VIDEO) + len(b"second")


def test_two_failed_attempts_that_uploaded_leave_no_files(app, service):
    service.plan = {1: "late", 2: "late"}
    me = creator(app)
    project = ready_project(app, me.browser)
    body = started(me.browser, project["id"])
    run_jobs(app)
    assert get_render(me.browser, body["render"]["id"])["state"] == "failed"
    for n in (1, 2):
        assert not any(exists(app, key) for key in render_keys(me.id, project["id"], body["render"]["id"], n))


def test_the_size_the_service_rendered_at_is_saved(app, service, database):
    """RENDER_SCALE: the service reports the size it made (720x1280 on a developer's machine)."""
    me = creator(app)
    project = ready_project(app, me.browser)
    body = started(me.browser, project["id"])
    run_jobs(app)
    rows = database.rows(
        "SELECT a.kind, a.width, a.height FROM renders r JOIN assets a ON a.id IN (r.asset_id, "
        "r.thumbnail_asset_id) WHERE r.id = :r ORDER BY a.kind",
        r=body["render"]["id"],
    )
    assert rows == [
        {"kind": "render", "width": 720, "height": 1280},
        {"kind": "thumbnail", "width": 720, "height": 1280},
    ]
