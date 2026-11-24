"""The command-line helpers the stack runs at start-up: `flask storage-init`, `flask worker-ping`."""

from __future__ import annotations

import socket
import uuid

import pytest

from .conftest import TEST_BUCKET, build_app


def unused_port() -> int:
    """A local port nothing listens on: connections to it are refused at once."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_storage_init_leaves_an_existing_bucket_alone(app):
    result = app.test_cli_runner().invoke(args=["storage-init"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"bucket {TEST_BUCKET}: already there"


def test_storage_init_creates_the_bucket_when_it_is_missing(app):
    name = f"reelforge-test-cli-{uuid.uuid4().hex[:12]}"
    fresh = build_app(s3_bucket=name)
    storage = fresh.extensions["storage"]
    try:
        first = fresh.test_cli_runner().invoke(args=["storage-init"])
        assert first.exit_code == 0, first.output
        assert first.output.strip() == f"bucket {name}: created"
        storage.check()  # raises if the bucket can't be reached

        # Every start of the stack runs it again.
        second = fresh.test_cli_runner().invoke(args=["storage-init"])
        assert (second.exit_code, second.output.strip()) == (0, f"bucket {name}: already there")
    finally:
        storage.client.delete_bucket(Bucket=name)


def test_storage_init_fails_with_a_plain_message_when_storage_does_not_answer(app):
    unreachable = build_app(s3_endpoint_url=f"http://127.0.0.1:{unused_port()}")

    result = unreachable.test_cli_runner().invoke(args=["storage-init", "--wait", "0"])

    # A non-zero exit stops `flask run` from starting behind it in the compose command.
    assert result.exit_code == 1
    assert "storage did not answer within 0s" in result.output
    assert "Traceback" not in result.output


@pytest.mark.worker
def test_worker_ping_prints_the_workers_answer(app):
    """Needs `docker compose up` (the worker service), like the round-trip test."""
    result = app.test_cli_runner().invoke(args=["worker-ping", "--timeout", "20"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "pong"
