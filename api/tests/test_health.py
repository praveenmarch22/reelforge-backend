import socket
import threading
import time

from reelforge_api.extensions import db
from reelforge_api.storage import Storage

from .conftest import build_app


def test_health_reports_ok_when_everything_answers(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.get_json() == {"status": "ok", "version": "0.1.0", "db": "ok", "redis": "ok", "storage": "ok"}


def test_health_is_503_and_names_the_dependency_that_is_down(client, app, monkeypatch):
    def refuse():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(app.extensions["redis"], "ping", refuse)
    res = client.get("/api/v1/health")
    assert res.status_code == 503
    body = res.get_json()
    assert body["status"] == "degraded"
    assert body["redis"] == "down"
    assert body["db"] == "ok" and body["storage"] == "ok"


def test_health_reports_storage_down(client, monkeypatch):
    def refuse(self):
        raise ConnectionError("no storage")

    monkeypatch.setattr(Storage, "check", refuse)
    res = client.get("/api/v1/health")
    assert res.status_code == 503
    assert res.get_json()["storage"] == "down"


def test_health_does_not_leak_error_details(client, app, monkeypatch):
    def refuse():
        raise ConnectionError("secret-host:6379 refused")

    monkeypatch.setattr(app.extensions["redis"], "ping", refuse)
    assert "secret-host" not in client.get("/api/v1/health").get_data(as_text=True)


def test_health_reports_the_database_down(client, monkeypatch):
    def refuse(*args, **kwargs):
        raise ConnectionError("no database")

    monkeypatch.setattr(db.session, "execute", refuse)
    res = client.get("/api/v1/health")
    assert res.status_code == 503
    body = res.get_json()
    assert (body["status"], body["db"]) == ("degraded", "down")
    assert body["redis"] == "ok" and body["storage"] == "ok"


def test_health_answers_within_seconds_when_the_database_accepts_the_connection_but_never_speaks(app):
    """What a wrong security group or a network fault looks like. A load balancer waits a few
    seconds for a health check, so an answer after 20 is the same as none (QA-045)."""
    listener = socket.create_server(("127.0.0.1", 0))  # connections open, and then nothing
    # If the API waited for ever, so would this test: hang up after a while so it can fail.
    give_up = threading.Timer(15, listener.close)
    give_up.start()
    port = listener.getsockname()[1]
    stuck = build_app(
        database_url=f"postgresql+psycopg://reelforge:reelforge@127.0.0.1:{port}/reelforge_test"
    )
    started = time.monotonic()
    try:
        res = stuck.test_client().get("/api/v1/health")
        waited = time.monotonic() - started
    finally:
        give_up.cancel()
        listener.close()

    assert res.status_code == 503
    body = res.get_json()
    assert (body["db"], body["redis"], body["storage"]) == ("down", "ok", "ok")
    assert waited < 8, f"the health check took {waited:.1f} s"
