"""Tests run inside the api container against real Postgres, Redis and MinIO:

    docker compose exec api pytest

They use their own database (reelforge_test), their own bucket and their own Redis database
for rate limits and revoked sessions, never development data.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
from contextlib import contextmanager

import pytest
import redis
from flask_migrate import upgrade
from sqlalchemy import text
from sqlalchemy.engine import make_url

from reelforge_api import create_app, jobs, mail
from reelforge_api.config import Settings
from reelforge_api.extensions import db

TEST_BUCKET = "reelforge-test"
# Rate-limit counters and revoked-session notes go to Redis database 15, which nothing else
# uses, so tests can empty it. The Celery queue stays on the normal database (the worker
# round-trip test needs the real worker).
TEST_REDIS_DB = 15


class NotATestDatabase(RuntimeError):
    pass


def require_test_database(url: str) -> str:
    """The tests drop the schema and empty tables. They only ever do that to a database whose
    name ends with `_test`, so a wrong TEST_DATABASE_URL can't cost anyone their data."""
    name = make_url(url).database or ""
    if not name.endswith("_test"):
        raise NotATestDatabase(
            f"TEST_DATABASE_URL names the database {name!r}. The tests erase the database they run "
            "against, so its name must end with '_test' (for example reelforge_test)."
        )
    return url


@contextmanager
def logging_kept():
    """Leaves the process's logging as it was found, for tests that start an app or a worker
    of their own (starting one points the root logger at that process's standard output)."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    disabled = {
        name: logger.disabled
        for name, logger in logging.root.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
        for name, logger in logging.root.manager.loggerDict.items():
            if isinstance(logger, logging.Logger):
                logger.disabled = disabled.get(name, False)


def make_settings(**overrides) -> Settings:
    values = {
        "env": "test",
        "database_url": require_test_database(os.environ["TEST_DATABASE_URL"]),
        "s3_bucket": TEST_BUCKET,
        # Tests fetch presigned links from inside the container, so they must use the internal address.
        "s3_public_endpoint_url": os.environ.get("S3_ENDPOINT_URL", ""),
        # Emails are kept in reelforge_api.mail.outbox instead of being sent.
        "email_provider": "memory",
        # Off unless a test is about rate limits (see the `limited_app` fixture).
        "rate_limits_enabled": False,
        "web_origin": "http://localhost:5173",
        "cookie_secure": None,
        "cookie_domain": "",
        "trusted_proxy_hops": 0,
        # The offline stand-in; tests about the real providers stub their HTTP calls.
        "llm_providers": "mock",
        "gemini_api_key": "",
        "groq_api_key": "",
        "voice_providers": "mock",
        "elevenlabs_api_key": "",
        "stock_providers": "mock",
        "pixabay_api_key": "",
        "image_providers": "mock",
        "cloudflare_account_id": "",
        "cloudflare_api_token": "",
    }
    values.update(overrides)
    return Settings(**values)


def _test_redis(settings: Settings) -> redis.Redis:
    base = settings.redis_url.rsplit("/", 1)[0]
    return redis.Redis.from_url(f"{base}/{TEST_REDIS_DB}", socket_connect_timeout=2, socket_timeout=2)


def build_app(**overrides):
    """An app with test settings whose rate limits and revoked sessions live in the test Redis."""
    settings = make_settings(**overrides)
    app = create_app(settings)
    app.extensions["redis"] = _test_redis(settings)
    return app


@pytest.fixture(scope="session")
def app():
    try:
        app = build_app()
    except NotATestDatabase as refused:
        pytest.exit(str(refused), returncode=pytest.ExitCode.USAGE_ERROR)
    with app.app_context():
        # The address is checked above; this checks the database actually connected to.
        connected_to = db.session.execute(text("SELECT current_database()")).scalar_one()
        if not connected_to.endswith("_test"):
            pytest.exit(f"refusing to erase the database {connected_to!r}", pytest.ExitCode.USAGE_ERROR)
        # Start from an empty database and build it with the real migrations.
        db.session.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        db.session.execute(text("CREATE SCHEMA public"))
        db.session.commit()
        upgrade()
        app.extensions["storage"].ensure_bucket()
    return app


@pytest.fixture(autouse=True)
def _no_task_reaches_the_development_worker(request, monkeypatch):
    """The queue is the development one (QA-042): a task queued by a test would be run by the
    development worker against the development database. Only tests marked `worker` may queue;
    a test about queueing replaces Task.apply_async itself (see the `queued` fixture)."""
    if request.node.get_closest_marker("worker"):
        return

    def refuse(task, *args, **kwargs):
        raise AssertionError(f"{task.name} was about to be queued on the development worker")

    monkeypatch.setattr("celery.app.task.Task.apply_async", refuse)


@pytest.fixture
def queued(monkeypatch) -> list[tuple[str, tuple]]:
    """Every background task the API hands to the queue during the test, as (name, args).
    Nothing is really queued."""
    calls: list[tuple[str, tuple]] = []

    def record(task, args=None, kwargs=None, **options):
        assert not kwargs, "tasks are queued with positional arguments"
        calls.append((task.name, tuple(args or ())))

    monkeypatch.setattr("celery.app.task.Task.apply_async", record)
    return calls


@pytest.fixture
def keep_logging():
    """For a test that changes the process's logging set-up: puts it back afterwards."""
    with logging_kept():
        yield


class StandardOutput:
    """Stands in for the standard output of an API or worker started during the test, and
    reads what was written to it as the JSON lines it should be."""

    def __init__(self):
        self._stream = io.StringIO()

    def start(self, starter, *args, **kwargs):
        """Runs `starter` with this as the standard output. A process decides where its logs
        go while it starts, so everything it logs afterwards arrives here too."""
        with contextlib.redirect_stdout(self._stream):
            return starter(*args, **kwargs)

    def text(self) -> str:
        """Everything written since the last read."""
        written = self._stream.getvalue()
        self._stream.seek(0)
        self._stream.truncate()
        return written

    @staticmethod
    def parse(text: str) -> list[dict]:
        """One entry per line. Fails if a line is not a JSON object."""
        entries = []
        for line in text.splitlines():
            if line.strip():
                entry = json.loads(line)
                assert isinstance(entry, dict), f"not a JSON object: {line}"
                entries.append(entry)
        return entries

    def lines(self) -> list[dict]:
        return self.parse(self.text())


@pytest.fixture
def stdout(keep_logging) -> StandardOutput:
    return StandardOutput()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def session(app):
    """A database session whose changes are thrown away after the test."""
    with app.app_context():
        yield db.session
        db.session.rollback()
        db.session.remove()


# ---------------------------------------------------------------- account tests (milestone B2)


class Database:
    """Runs SQL against the test database in its own committed transaction."""

    def __init__(self, app):
        self._app = app

    def rows(self, statement: str, **params) -> list[dict]:
        with self._app.app_context(), db.engine.begin() as connection:
            return [dict(row._mapping) for row in connection.execute(text(statement), params)]

    def one(self, statement: str, **params) -> dict:
        rows = self.rows(statement, **params)
        assert len(rows) == 1, f"expected one row, got {len(rows)}: {statement}"
        return rows[0]

    def value(self, statement: str, **params):
        return next(iter(self.one(statement, **params).values()))

    def run(self, statement: str, **params) -> int:
        with self._app.app_context(), db.engine.begin() as connection:
            return connection.execute(text(statement), params).rowcount


def _wipe(app) -> None:
    with app.app_context(), db.engine.begin() as connection:
        # Everything a user owns hangs off users, so this empties all account data.
        connection.execute(text("DELETE FROM users"))
        # Not owned by a user: the admin's provider switches and the usage counters.
        connection.execute(text("DELETE FROM provider_switches"))
        connection.execute(text("DELETE FROM usage_daily"))
        connection.execute(text("DELETE FROM music_tracks"))
    app.extensions["redis"].flushdb()
    mail.outbox.clear()
    jobs.manual_queue.clear()


@pytest.fixture
def clean_state(app):
    """The endpoints commit, so account tests start (and leave) with empty tables, an empty
    test Redis (rate limits, revoked sessions) and an empty outbox."""
    _wipe(app)
    yield
    _wipe(app)


@pytest.fixture
def database(app, clean_state) -> Database:
    return Database(app)


@pytest.fixture
def outbox(clean_state) -> list[mail.Email]:
    """Every email the API "sent" during the test, oldest first."""
    return mail.outbox


@pytest.fixture(scope="module")
def limited_app(app):
    """The same API with rate limits switched on."""
    return build_app(rate_limits_enabled=True)
