"""What the API and the worker write to their logs (docs/TECHNICAL_DESIGN.md, Observability
and Security; docs/API_DESIGN.md: the id in X-Request-ID is on every log line of that request).

Most tests here start an API inside the test and read what it writes to its standard output,
so they hold the whole chain in place: the logger set-up, the formatter and the line.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime

import pytest
from celery.app.trace import LOG_RECEIVED, LOG_SUCCESS
from celery.signals import setup_logging
from flask_migrate import upgrade
from sqlalchemy import text

from reelforge_api import mail
from reelforge_api.extensions import db
from reelforge_api.log import JsonFormatter

from .conftest import build_app
from .support import sign_up

REQUEST_LOGGER = "reelforge_api.request"
QUERY_VALUE = "value-in-a-query@example.com"
LINK_TOKEN = "the-one-time-token-in-the-link"


@pytest.fixture
def logged_app(app, stdout):
    """The API as a newly started process would log: its lines can be read from `stdout`."""
    logged = stdout.start(build_app)

    @logged.get("/api/v1/_test/crash")
    def _crash():
        raise RuntimeError("the disk is full")

    @logged.get("/api/v1/_test/db-error")
    def _db_error():
        db.session.execute(text("SELECT 1 / 0 WHERE :email <> ''"), {"email": QUERY_VALUE})
        return {}

    stdout.text()  # anything written while starting is not part of the test
    return logged


def request_lines(entries: list[dict]) -> list[dict]:
    return [entry for entry in entries if entry["logger"] == REQUEST_LOGGER]


# ---------------------------------------------------------------- one line per request


def test_a_request_writes_one_json_line_with_its_id_and_without_its_query_string(logged_app, stdout):
    res = logged_app.test_client().get("/api/v1/me?token=secret123&email=asha%40example.com")
    written = stdout.text()

    assert res.status_code == 401
    (line,) = request_lines(stdout.parse(written))
    assert line["request_id"] == res.headers["X-Request-ID"]
    assert (line["method"], line["path"], line["status"]) == ("GET", "/api/v1/me", 401)
    assert line["user_id"] is None
    assert isinstance(line["duration_ms"], int | float) and 0 <= line["duration_ms"] < 60_000
    assert line["level"] == "INFO"
    assert datetime.fromisoformat(line["time"]).tzinfo is not None
    # A query string can carry a token or an address (the Instagram callback's code, later).
    assert "secret123" not in written and "asha" not in written


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/api/v1/nope", 404),
        ("GET", "/api/v1/me/password", 405),
        ("POST", "/api/v1/auth/logout", 401),
        ("POST", "/api/v1/auth/login", 400),
        ("GET", "/api/v1/_test/crash", 500),
    ],
)
def test_requests_that_fail_are_logged_like_any_other(logged_app, stdout, method, path, status):
    res = logged_app.test_client().open(path, method=method)

    assert res.status_code == status
    (line,) = request_lines(stdout.lines())
    assert (line["method"], line["path"], line["status"]) == (method, path, status)
    assert line["request_id"] == res.headers["X-Request-ID"]


def test_the_request_line_names_the_logged_in_user(logged_app, stdout, outbox):
    account = sign_up(logged_app)
    stdout.text()

    assert account.browser.get("/me").status_code == 200
    assert account.browser.patch("/me", {"name": "Asha R"}).status_code == 200

    lines = request_lines(stdout.lines())
    assert [(line["method"], line["path"], line["status"]) for line in lines] == [
        ("GET", "/api/v1/me", 200),
        ("PATCH", "/api/v1/me", 200),
    ]
    assert {line["user_id"] for line in lines} == {account.user["id"]}


def test_health_checks_are_not_logged(logged_app, stdout):
    """The load balancer asks every few seconds; those lines would bury everything else."""
    assert logged_app.test_client().get("/api/v1/health").status_code == 200
    assert stdout.lines() == []


def test_a_callers_request_id_ties_the_response_to_its_log_lines(logged_app, stdout):
    res = logged_app.test_client().get("/api/v1/nope", headers={"X-Request-ID": "trace-me-42"})

    assert res.headers["X-Request-ID"] == "trace-me-42"
    assert [line["request_id"] for line in stdout.lines()] == ["trace-me-42"]


# ---------------------------------------------------------------- unexpected errors


def test_an_unexpected_error_is_logged_with_its_traceback_under_the_request_id(logged_app, stdout):
    res = logged_app.test_client().get("/api/v1/_test/crash")

    assert res.status_code == 500
    assert "disk" not in res.get_data(as_text=True)
    entries = stdout.lines()
    (error,) = [entry for entry in entries if entry["level"] == "ERROR"]
    assert error["request_id"] == res.headers["X-Request-ID"]
    assert "RuntimeError: the disk is full" in error["exception"]
    assert "Traceback" in error["exception"]
    (line,) = request_lines(entries)
    assert (line["status"], line["request_id"]) == (500, res.headers["X-Request-ID"])


def test_a_database_error_is_logged_without_the_values_of_the_query(logged_app, stdout):
    """Query values are emails, password hashes and token hashes (QB2-007)."""
    res = logged_app.test_client().get("/api/v1/_test/db-error")
    written = stdout.text()

    assert res.status_code == 500
    (error,) = [entry for entry in stdout.parse(written) if entry["level"] == "ERROR"]
    assert "division by zero" in error["exception"], "the error itself should still be in the log"
    assert "SELECT 1 / 0" in error["exception"], "and so should the statement, without its values"
    assert QUERY_VALUE not in written


# ---------------------------------------------------------------- the formatter


def test_log_lines_are_json_with_extra_fields():
    record = logging.LogRecord("reelforge", logging.INFO, __file__, 1, "job %s done", ("j1",), None)
    record.job_id = "j1"
    entry = json.loads(JsonFormatter().format(record))
    assert entry["message"] == "job j1 done"
    assert entry["level"] == "INFO"
    assert entry["job_id"] == "j1"


def celery_record(logger: str, template: str, context: dict) -> logging.LogRecord:
    """A record as Celery makes its task lines: the values fill the message and are attached
    again, whole, as `data` (celery.app.trace.info and celery.worker.strategy)."""
    record = logging.LogRecord(logger, logging.INFO, __file__, 1, template, (context,), None)
    record.data = context
    return record


def an_email_task() -> dict:
    link = f"http://localhost:5173/verify?token={LINK_TOKEN}"
    message = {"to": "asha@example.com", "subject": "Verify your email", "text": link, "html": link}
    return {"id": "0d6f9d6e-1b7c-4d0e-9f43-6a3f3f1d2c11", "name": "mail.send", "args": repr((message,))}


def test_a_received_task_is_logged_without_its_arguments():
    context = {**an_email_task(), "kwargs": repr({"link": LINK_TOKEN}), "eta": None}

    line = JsonFormatter().format(celery_record("celery.worker.strategy", LOG_RECEIVED, context))

    assert LINK_TOKEN not in line and "asha@example.com" not in line
    entry = json.loads(line)
    assert entry["message"] == f"Task mail.send[{context['id']}] received"
    assert entry["data"] == {"id": context["id"], "name": "mail.send", "eta": None}


def test_a_finished_task_is_logged_without_its_arguments_or_its_result():
    context = {**an_email_task(), "kwargs": "{}", "return_value": repr(LINK_TOKEN), "runtime": 0.25}

    line = JsonFormatter().format(celery_record("celery.app.trace", LOG_SUCCESS, context))

    assert LINK_TOKEN not in line and "asha@example.com" not in line
    entry = json.loads(line)
    assert entry["message"] == f"Task mail.send[{context['id']}] succeeded"
    assert entry["data"] == {"id": context["id"], "name": "mail.send", "runtime": 0.25}


# ---------------------------------------------------------------- the worker


def test_the_worker_writes_the_same_json_lines_as_the_api(stdout):
    """Celery announces `setup_logging` when a worker starts. When something answers, Celery
    leaves the logging to it; when nothing does, the worker logs in Celery's own text format."""
    import reelforge_api.celery_app  # noqa: F401 - what a worker imports first

    answered = stdout.start(
        setup_logging.send, sender=None, loglevel=logging.INFO, logfile=None, format="", colorize=False
    )
    assert answered, "nothing answers Celery's setup_logging, so the worker would not log JSON"

    context = {**an_email_task(), "kwargs": "{}", "eta": None}
    logging.getLogger("celery.worker.strategy").info(LOG_RECEIVED, context, extra={"data": context})

    written = stdout.text()
    (entry,) = stdout.parse(written)
    assert entry["logger"] == "celery.worker.strategy"
    assert entry["message"] == f"Task mail.send[{context['id']}] received"
    assert LINK_TOKEN not in written


@pytest.mark.parametrize("mail_server", ["up", "down"])
def test_running_the_email_task_never_logs_the_link(app, stdout, outbox, monkeypatch, mail_server):
    """The real task run by Celery's own task runner, with the lines Celery writes about it."""
    worker = stdout.start(build_app)
    stdout.text()
    with worker.app_context():
        message = mail.verification_email("asha@example.com", LINK_TOKEN)
    if mail_server == "down":

        def refuse(email):
            raise ConnectionError("mail server is down")

        monkeypatch.setattr("reelforge_api.tasks.mail.send_now", refuse)

    result = worker.extensions["celery"].tasks["mail.send"].apply(args=[asdict(message)], throw=False)

    written = stdout.text()
    task_lines = [entry for entry in stdout.parse(written) if entry["logger"] == "celery.app.trace"]
    if mail_server == "up":
        assert result.state == "SUCCESS" and outbox == [message]
        assert [entry["message"] for entry in task_lines] == [f"Task mail.send[{result.id}] succeeded"]
    else:
        assert result.state == "FAILURE" and outbox == []
        assert task_lines[-1]["level"] == "ERROR"
        assert "mail server is down" in task_lines[-1]["message"]
        assert any("retry" in entry["message"] for entry in task_lines), "a failed send is retried"
    assert LINK_TOKEN not in written
    assert "asha@example.com" not in written


# ---------------------------------------------------------------- migrations and logging


def test_running_the_migrations_leaves_the_api_logging_as_it_was(app, stdout):
    """QA-049: alembic.ini's logging set-up used to replace the JSON handler and switch off
    every logger that already existed, in any process that ran a migration after starting."""
    started = stdout.start(build_app)
    errors = logging.getLogger("reelforge_api.errors")
    assert not errors.disabled
    stdout.text()

    with started.app_context():
        upgrade()  # the database is already up to date, so this changes nothing in it

    assert not errors.disabled, "the API's loggers are switched off"
    assert started.test_client().get("/api/v1/nope").status_code == 404
    (line,) = [entry for entry in stdout.lines() if entry["logger"] == REQUEST_LOGGER]
    assert line["status"] == 404, "after a migration the API still writes its JSON request line"
