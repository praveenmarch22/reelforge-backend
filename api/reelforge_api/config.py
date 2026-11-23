"""Settings, read from environment variables (see backend/.env.example)."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder values that are fine on a developer's machine and must never reach production.
DEV_SECRET_KEY = "dev-only-secret-change-me"  # noqa: S105
DEV_S3_SECRET = "reelforge-dev-secret"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    app_version: str = "0.1.0"
    secret_key: str = DEV_SECRET_KEY

    database_url: str = "postgresql+psycopg://reelforge:reelforge@localhost:5432/reelforge"
    redis_url: str = "redis://localhost:6379/0"

    # Object storage: MinIO locally, Amazon S3 in AWS (leave the endpoint empty there).
    s3_endpoint_url: str = "http://localhost:9000"
    # Address a browser can reach, used in presigned links. Inside Docker the API reaches
    # MinIO as "minio:9000" but a browser on the host needs "localhost:9000".
    s3_public_endpoint_url: str = ""
    s3_bucket: str = "reelforge-media"
    s3_region: str = "us-east-1"
    s3_access_key: str = "reelforge-dev"
    s3_secret_key: str = DEV_S3_SECRET

    # The web app's address, allowed to call the API with cookies.
    web_origin: str = "http://localhost:5173"

    @model_validator(mode="after")
    def _no_dev_secrets_in_production(self) -> Settings:
        if self.env == "production":
            if self.secret_key == DEV_SECRET_KEY or len(self.secret_key) < 32:
                raise ValueError("SECRET_KEY must be set to a long random value in production")
            if self.s3_secret_key == DEV_S3_SECRET:
                raise ValueError("S3_SECRET_KEY must not be the development default in production")
        return self

    @property
    def s3_presign_endpoint_url(self) -> str:
        return self.s3_public_endpoint_url or self.s3_endpoint_url
