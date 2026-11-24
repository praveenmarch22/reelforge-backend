"""The error format and request ids from docs/API_DESIGN.md (Conventions)."""

import pytest
from flask import request
from werkzeug.exceptions import ServiceUnavailable, TooManyRequests

from reelforge_api import create_app
from reelforge_api.errors import ApiError

from .conftest import make_settings
from .support import assert_error

ONE_MEGABYTE = 1024 * 1024


@pytest.fixture(scope="module")
def probe_client():
    """An app with extra routes that fail on purpose, or that read a body as an endpoint would."""
    app = create_app(make_settings())

    @app.get("/api/v1/_test/api-error")
    def _api_error():
        raise ApiError(
            402, "insufficient_credits", "This needs 12 credits; you have 5.", {"needed": 12, "balance": 5}
        )

    @app.get("/api/v1/_test/crash")
    def _crash():
        raise RuntimeError("database password is hunter2")

    @app.post("/api/v1/_test/echo")
    def _echo():
        return {"received": request.get_json()}

    @app.get("/api/v1/_test/busy")
    def _busy():
        raise TooManyRequests(retry_after=30)

    @app.get("/api/v1/_test/maintenance")
    def _maintenance():
        raise ServiceUnavailable(retry_after=120)

    return app.test_client()


def test_unknown_route_uses_the_error_format(client):
    res = client.get("/api/v1/nope")
    assert res.status_code == 404
    assert res.get_json() == {"error": {"code": "not_found", "message": "We couldn't find that."}}


def test_wrong_method_uses_the_error_format_and_lists_the_methods_that_work(client):
    res = client.post("/api/v1/health")
    assert_error(res, 405, "method_not_allowed")
    allowed = {method.strip() for method in res.headers["Allow"].split(",")}
    assert "GET" in allowed and "POST" not in allowed


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


# ---------------------------------------------------------------- request bodies


def json_of_size(size: int) -> bytes:
    """A valid JSON body of exactly `size` bytes."""
    frame = b'{"padding":""}'
    return b'{"padding":"' + b"x" * (size - len(frame)) + b'"}'


def test_a_request_body_of_exactly_one_megabyte_is_read(probe_client):
    body = json_of_size(ONE_MEGABYTE)
    res = probe_client.post("/api/v1/_test/echo", data=body, content_type="application/json")
    assert res.status_code == 200
    assert len(res.get_json()["received"]["padding"]) == ONE_MEGABYTE - 14


def test_a_request_body_over_one_megabyte_is_refused_as_too_large(probe_client):
    body = json_of_size(ONE_MEGABYTE + 1)
    res = probe_client.post("/api/v1/_test/echo", data=body, content_type="application/json")
    # Not file_too_large: that code is for the upload endpoints and talks about a file.
    error = assert_error(res, 413, "payload_too_large")
    assert "file" not in error["message"].lower()


def test_a_real_endpoint_refuses_a_body_over_one_megabyte(client):
    body = json_of_size(ONE_MEGABYTE + 1)
    res = client.post("/api/v1/auth/login", data=body, content_type="application/json")
    assert_error(res, 413, "payload_too_large")


def test_a_body_that_is_not_declared_as_json_is_an_unsupported_media_type(probe_client):
    res = probe_client.post("/api/v1/_test/echo", data="name=Asha", content_type="text/plain")
    # Not unsupported_file_type: that code is for the upload endpoints.
    error = assert_error(res, 415, "unsupported_media_type")
    assert "file" not in error["message"].lower()


def test_malformed_json_is_a_validation_error(probe_client):
    res = probe_client.post("/api/v1/_test/echo", data="{not json", content_type="application/json")
    assert_error(res, 400, "validation_error")


# ---------------------------------------------------------------- headers that belong to an error


@pytest.mark.parametrize(
    ("path", "status", "seconds"),
    [("/api/v1/_test/busy", 429, "30"), ("/api/v1/_test/maintenance", 503, "120")],
)
def test_errors_raised_by_the_framework_keep_their_retry_after(probe_client, path, status, seconds):
    res = probe_client.get(path)
    assert res.status_code == status
    assert set(res.get_json()) == {"error"}
    assert res.headers["Retry-After"] == seconds


def test_a_429_raised_by_the_framework_has_the_documented_code(probe_client):
    assert_error(probe_client.get("/api/v1/_test/busy"), 429, "rate_limited")


# ---------------------------------------------------------------- request ids


def test_every_response_has_a_request_id(client):
    res = client.get("/api/v1/health")
    assert len(res.headers["X-Request-ID"]) == 32


def test_error_responses_have_a_request_id_too(probe_client):
    for path in ("/api/v1/nope", "/api/v1/_test/crash", "/api/v1/_test/api-error"):
        assert len(probe_client.get(path).headers["X-Request-ID"]) == 32


def test_a_plain_request_id_from_the_caller_is_kept(client):
    res = client.get("/api/v1/health", headers={"X-Request-ID": "abc-123_x.y"})
    assert res.headers["X-Request-ID"] == "abc-123_x.y"


@pytest.mark.parametrize("bad", ["has space", "x" * 65, "semi;colon", "<script>"])
def test_an_unsafe_request_id_is_replaced(client, bad):
    res = client.get("/api/v1/health", headers={"X-Request-ID": bad})
    assert res.headers["X-Request-ID"] != bad
    assert len(res.headers["X-Request-ID"]) == 32


# ---------------------------------------------------------------- CORS


def test_cors_allows_the_web_app_with_cookies(client, app):
    origin = app.extensions["settings"].web_origin
    res = client.get("/api/v1/health", headers={"Origin": origin})
    assert res.headers["Access-Control-Allow-Origin"] == origin
    assert res.headers["Access-Control-Allow-Credentials"] == "true"


def test_cors_does_not_allow_other_sites(client):
    res = client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in res.headers


def test_error_responses_carry_the_cors_headers(probe_client):
    """Otherwise the web app could not read the error it is being given."""
    origin = "http://localhost:5173"
    for path in ("/api/v1/nope", "/api/v1/_test/crash", "/api/v1/_test/busy"):
        res = probe_client.get(path, headers={"Origin": origin})
        assert res.headers["Access-Control-Allow-Origin"] == origin, path


def test_the_browser_may_reuse_a_permission_check_for_ten_minutes(client, app):
    """Every change the web app makes is preflighted; without a max-age each costs two round trips."""
    res = client.options(
        "/api/v1/me",
        headers={
            "Origin": app.extensions["settings"].web_origin,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type, x-csrf-token, idempotency-key",
        },
    )
    assert res.status_code == 200
    assert res.headers["Access-Control-Max-Age"] == "600"
    allowed = res.headers["Access-Control-Allow-Headers"].lower()
    assert all(name in allowed for name in ("content-type", "x-csrf-token", "idempotency-key"))
