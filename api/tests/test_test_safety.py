"""The guards that keep a test run away from development data (QA-042, QA-049).

The tests drop the schema and empty tables of the database they are given, and the queue
they could reach is the development one.
"""

from __future__ import annotations

import os

import pytest

from reelforge_api.config import DEV_DATABASE_URL
from reelforge_api.tasks.system import ping

from .conftest import NotATestDatabase, make_settings, require_test_database

SERVER = "postgresql+psycopg://reelforge:reelforge@postgres:5432"


@pytest.mark.parametrize(
    "url",
    [
        DEV_DATABASE_URL,
        f"{SERVER}/reelforge",
        f"{SERVER}/reelforge_test_copy",
        f"{SERVER}/test_reelforge",
        f"{SERVER}/postgres?options=-csearch_path%3Dreelforge_test",
        f"{SERVER}/",
        SERVER,
    ],
)
def test_a_database_whose_name_does_not_end_with_test_is_refused(url):
    with pytest.raises(NotATestDatabase, match="_test"):
        require_test_database(url)


@pytest.mark.parametrize("url", [f"{SERVER}/reelforge_test", f"{SERVER}/ci_test?sslmode=disable"])
def test_a_test_database_is_accepted(url):
    assert require_test_database(url) == url


def test_no_test_app_can_be_built_on_the_development_database(monkeypatch):
    """Every app the tests build takes its database from here, including the ones that
    empty tables between tests."""
    monkeypatch.setenv("TEST_DATABASE_URL", os.environ["DATABASE_URL"])
    with pytest.raises(NotATestDatabase):
        make_settings()


def test_a_test_cannot_put_a_task_on_the_development_queue(app):
    with app.app_context(), pytest.raises(AssertionError, match="development worker"):
        ping.delay()
