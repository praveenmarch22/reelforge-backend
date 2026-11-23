"""Command-line helpers: `flask storage-init`, `flask worker-ping`."""

from __future__ import annotations

import time

import click
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask

from .storage import get_storage


def register_cli(app: Flask) -> None:
    @app.cli.command("storage-init")
    @click.option("--wait", default=60, show_default=True, help="Seconds to wait for storage to start.")
    def storage_init(wait: int) -> None:
        """Create the media bucket if it doesn't exist (local MinIO, or a new AWS environment)."""
        storage = get_storage()
        deadline = time.monotonic() + wait
        while True:
            try:
                created = storage.ensure_bucket()
                break
            except (BotoCoreError, ClientError) as e:
                if time.monotonic() >= deadline:
                    raise click.ClickException(f"storage did not answer within {wait}s: {e}") from e
                time.sleep(2)
        click.echo(f"bucket {storage.bucket}: {'created' if created else 'already there'}")

    @app.cli.command("worker-ping")
    @click.option("--timeout", default=10, show_default=True)
    def worker_ping(timeout: int) -> None:
        """Send a task to the worker and wait for the answer."""
        from .tasks.system import ping

        click.echo(ping.delay().get(timeout=timeout))
