from reelforge_api.storage import Storage


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
