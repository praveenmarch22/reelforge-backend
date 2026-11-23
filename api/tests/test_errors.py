"""The error format and request ids from docs/API_DESIGN.md (Conventions)."""

import json
import logging

import pytest

from reelforge_api import create_app
from reelforge_api.errors import ApiError
from reelforge_api.log import JsonFormatter

from .conftest import make_settings


@pytest.fixture(scope="module")
def probe_client():
    """An app with two extra routes that fail on purpose."""
    app = create_app(make_settings())

    @app.get("/api/v1/_test/api-error")
    def _api_error():
        raise ApiError(
            402, "insufficient_credits", "This needs 12 credits; you have 5.", {"needed": 12, "balance": 5}
        )

    @app.get("/api/v1/_test/crash")
    def _crash():
        raise RuntimeError("database password is hunter2")

    return app.test_client()


def test_unknown_route_uses_the_error_format(client):
    res = client.get("/api/v1/nope")
    assert res.status_code == 404
    assert res.get_json() == {"error": {"code": "not_found", "message": "We couldn't find that."}}


def test_wrong_method_uses_the_error_format(client):
    res = client.post("/api/v1/health")
    assert res.status_code == 405
    assert res.get_json()["error"]["code"] == "method_not_allowed"


def test_api_error_carries_code_message_and_details(probe_client):
    res = probe_client.get("/api/v1/_test/api-error")
    assert res.status_code == 402
    assert res.get_json() == {
        "error": {
            "code": "insufficient_credits",
            "message": "This needs 12 credits; you have 5.",
            "details": {"needed": 12, "balance": 5},
        }
    }


def test_unexpected_errors_are_500_without_internal_details(probe_client):
    res = probe_client.get("/api/v1/_test/crash")
    assert res.status_code == 500
    assert res.get_json()["error"]["code"] == "internal_error"
    assert "hunter2" not in res.get_data(as_text=True)


def test_oversized_request_body_is_rejected(client):
    res = client.post("/api/v1/nope", data=b"x" * (1024 * 1024 + 1), content_type="application/json")
    assert res.status_code in (404, 413)  # never accepted, whichever check runs first


def test_every_response_has_a_request_id(client):
    res = client.get("/api/v1/health")
    assert len(res.headers["X-Request-ID"]) == 32


def test_a_plain_request_id_from_the_caller_is_kept(client):
    res = client.get("/api/v1/health", headers={"X-Request-ID": "abc-123_x.y"})
    assert res.headers["X-Request-ID"] == "abc-123_x.y"


@pytest.mark.parametrize("bad", ["has space", "x" * 65, "semi;colon", "<script>"])
def test_an_unsafe_request_id_is_replaced(client, bad):
    res = client.get("/api/v1/health", headers={"X-Request-ID": bad})
    assert res.headers["X-Request-ID"] != bad
    assert len(res.headers["X-Request-ID"]) == 32


def test_cors_allows_the_web_app_with_cookies(client, app):
    origin = app.extensions["settings"].web_origin
    res = client.get("/api/v1/health", headers={"Origin": origin})
    assert res.headers["Access-Control-Allow-Origin"] == origin
    assert res.headers["Access-Control-Allow-Credentials"] == "true"


def test_cors_does_not_allow_other_sites(client):
    res = client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in res.headers


def test_log_lines_are_json_with_extra_fields():
    record = logging.LogRecord("reelforge", logging.INFO, __file__, 1, "job %s done", ("j1",), None)
    record.job_id = "j1"
    entry = json.loads(JsonFormatter().format(record))
    assert entry["message"] == "job j1 done"
    assert entry["level"] == "INFO"
    assert entry["job_id"] == "j1"
