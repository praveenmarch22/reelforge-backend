"""Tests run inside the api container against real Postgres, Redis and MinIO:

    docker compose exec api pytest

They use their own database (reelforge_test) and their own bucket, never development data.
"""

from __future__ import annotations

import os

import pytest
from flask_migrate import upgrade
from sqlalchemy import text

from reelforge_api import create_app
from reelforge_api.config import Settings
from reelforge_api.extensions import db

TEST_BUCKET = "reelforge-test"


def make_settings(**overrides) -> Settings:
    values = {
        "env": "test",
        "database_url": os.environ["TEST_DATABASE_URL"],
        "s3_bucket": TEST_BUCKET,
        # Tests fetch presigned links from inside the container, so they must use the internal address.
        "s3_public_endpoint_url": os.environ.get("S3_ENDPOINT_URL", ""),
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(scope="session")
def app():
    app = create_app(make_settings())
    with app.app_context():
        # Start from an empty database and build it with the real migrations.
        db.session.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        db.session.execute(text("CREATE SCHEMA public"))
        db.session.commit()
        upgrade()
        app.extensions["storage"].ensure_bucket()
    return app


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
