import pytest
from pydantic import ValidationError

from reelforge_api.celery_app import QUEUES
from reelforge_api.config import DEV_SECRET_KEY, Settings
from reelforge_api.tasks.system import ping


@pytest.mark.worker
def test_a_task_makes_the_round_trip_through_the_worker(app):
    """Queue -> worker container -> result. Needs `docker compose up` (the worker service)."""
    with app.app_context():
        assert ping.delay().get(timeout=20) == "pong"


def test_ping_runs_with_database_access(app):
    with app.app_context():
        assert ping.run() == "pong"


def test_queues_match_the_technical_design():
    assert set(QUEUES) == {
        "default",
        "llm",
        "voice",
        "visuals",
        "ai_video",
        "render",
        "publish",
        "maintenance",
    }


def test_production_refuses_the_development_secret():
    with pytest.raises(ValidationError):
        Settings(env="production", secret_key=DEV_SECRET_KEY, s3_secret_key="real-secret-value")


def test_production_refuses_a_short_secret():
    with pytest.raises(ValidationError):
        Settings(env="production", secret_key="short", s3_secret_key="real-secret-value")


def test_production_refuses_the_development_storage_secret():
    with pytest.raises(ValidationError):
        Settings(env="production", secret_key="k" * 40)


def test_production_accepts_real_secrets():
    settings = Settings(env="production", secret_key="k" * 40, s3_secret_key="real-secret-value")
    assert settings.env == "production"


def test_presigned_links_use_the_public_address_when_set():
    internal, public = "http://minio:9000", "http://localhost:9000"
    assert Settings(s3_endpoint_url=internal, s3_public_endpoint_url=public).s3_presign_endpoint_url == public
    # Passed explicitly: the container's own environment sets a public address.
    assert Settings(s3_endpoint_url=internal, s3_public_endpoint_url="").s3_presign_endpoint_url == internal
