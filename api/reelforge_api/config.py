"""Settings, read from environment variables (see backend/.env.example)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder values that are fine on a developer's machine and must never reach production.
DEV_SECRET_KEY = "dev-only-secret-change-me"  # noqa: S105
DEV_S3_SECRET = "reelforge-dev-secret"  # noqa: S105
DEV_DATABASE_URL = "postgresql+psycopg://reelforge:reelforge@localhost:5432/reelforge"
DEV_DATABASE_LOGIN = "reelforge:reelforge@"
# One exact origin: scheme, host, optional port. Nothing that the CORS library could read
# as a pattern, which would let look-alike sites call the API with the user's cookies.
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*(:\d{1,5})?$")


class Settings(BaseSettings):
    # hide_input_in_errors: a refused configuration must not print the values it was given
    # (they include passwords) in the start-up error.
    model_config = SettingsConfigDict(env_file=None, extra="ignore", hide_input_in_errors=True)

    env: Literal["development", "test", "production"] = "development"
    app_version: str = "0.1.0"
    secret_key: str = Field(default=DEV_SECRET_KEY, repr=False)

    database_url: str = Field(default=DEV_DATABASE_URL, repr=False)
    redis_url: str = Field(default="redis://localhost:6379/0", repr=False)

    # Object storage: MinIO locally, Amazon S3 in AWS (leave the endpoint empty there).
    s3_endpoint_url: str = "http://localhost:9000"
    # Address a browser can reach, used in presigned links. Inside Docker the API reaches
    # MinIO as "minio:9000" but a browser on the host needs "localhost:9000".
    s3_public_endpoint_url: str = ""
    s3_bucket: str = "reelforge-media"
    s3_region: str = "us-east-1"
    s3_access_key: str = "reelforge-dev"
    s3_secret_key: str = Field(default=DEV_S3_SECRET, repr=False)

    # The web app's address, allowed to call the API with cookies. Emailed links point here.
    web_origin: str = "http://localhost:5173"

    # Sessions (API design, Conventions: Auth).
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    # Cookies are sent over HTTPS only when this is on. Empty = on in production, off elsewhere.
    cookie_secure: bool | None = None
    # Parent domain for the rf_csrf cookie when the API is on api.<domain>. Empty = this host only.
    cookie_domain: str = ""
    # How many proxies (load balancer) sit in front of the API and set X-Forwarded-For.
    # 0 = none: the connecting address is the client. Needed for per-IP rate limits.
    trusted_proxy_hops: int = 0
    rate_limits_enabled: bool = True

    # Email. "smtp" sends through SMTP_HOST (Mailpit locally), "console" writes the message
    # to the log, "memory" keeps it in a list for tests. Amazon SES is added at deployment (I2).
    email_provider: Literal["smtp", "console", "memory"] = "console"
    email_from: str = "ReelForge <no-reply@reelforge.local>"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = Field(default="", repr=False)
    smtp_starttls: bool = False

    # Language models, tried in this order (technical design 9.0). "mock" is an offline
    # stand-in for development and tests; production refuses it.
    llm_providers: str = "gemini,groq"
    gemini_api_key: str = Field(default="", repr=False)
    gemini_model: str = "gemini-3.5-flash"
    groq_api_key: str = Field(default="", repr=False)
    groq_model: str = "openai/gpt-oss-120b"

    # Voiceover providers, tried in this order (technical design 9.0). Edge TTS needs no key.
    voice_providers: str = "elevenlabs,edge"
    elevenlabs_api_key: str = Field(default="", repr=False)
    elevenlabs_model: str = "eleven_multilingual_v2"

    # Visuals. Stock video: Pixabay (Pexels goes in front once it issues keys again).
    # AI images: FLUX on Cloudflare Workers AI. "mock" stands in offline for either.
    stock_providers: str = "pixabay"
    pixabay_api_key: str = Field(default="", repr=False)
    image_providers: str = "cloudflare"
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = Field(default="", repr=False)
    cloudflare_image_model: str = "@cf/black-forest-labs/flux-1-schnell"

    # How background jobs are started. "celery": sent to the worker. "manual": kept in a list
    # for tests to run one by one. Empty = manual in the test environment, celery elsewhere.
    job_dispatch: Literal["celery", "manual"] | None = None

    # Credits granted at sign-up and every month (PRD 5.10).
    monthly_credits: int = 100
    monthly_credits_tester: int = 500

    @model_validator(mode="after")
    def _no_dev_secrets_in_production(self) -> Settings:
        # With cookies allowed, "any origin" would let every website call the API as the user.
        # fullmatch, not match: "$" would also accept a value that ends with a line break.
        if not _ORIGIN.fullmatch(self.web_origin):
            raise ValueError("WEB_ORIGIN must be one exact address, for example https://app.example.com")
        unknown = set(self.llm_provider_names) - {"gemini", "groq", "mock"}
        if unknown or not self.llm_provider_names:
            raise ValueError("LLM_PROVIDERS must list gemini, groq or mock, separated by commas")
        for names, allowed, setting in (
            (self.stock_provider_names, {"pixabay", "mock"}, "STOCK_PROVIDERS"),
            (self.image_provider_names, {"cloudflare", "mock"}, "IMAGE_PROVIDERS"),
        ):
            if set(names) - allowed or not names:
                raise ValueError(f"{setting} must list {' or '.join(sorted(allowed))}, separated by commas")
        unknown = set(self.voice_provider_names) - {"elevenlabs", "edge", "mock"}
        if unknown or not self.voice_provider_names:
            raise ValueError("VOICE_PROVIDERS must list elevenlabs, edge or mock, separated by commas")
        if self.env == "production":
            if DEV_DATABASE_LOGIN in self.database_url:
                raise ValueError("DATABASE_URL must not use the development login in production")
            if self.email_provider == "smtp" and not self.smtp_starttls:
                raise ValueError("SMTP_STARTTLS must be on in production")
            if self.secret_key == DEV_SECRET_KEY or len(self.secret_key) < 32:
                raise ValueError("SECRET_KEY must be set to a long random value in production")
            if self.s3_secret_key == DEV_S3_SECRET:
                raise ValueError("S3_SECRET_KEY must not be the development default in production")
            if "mock" in self.llm_provider_names:
                raise ValueError("LLM_PROVIDERS must not include the mock in production")
            if "mock" in self.voice_provider_names:
                raise ValueError("VOICE_PROVIDERS must not include the mock in production")
            if "mock" in self.stock_provider_names or "mock" in self.image_provider_names:
                raise ValueError(
                    "STOCK_PROVIDERS and IMAGE_PROVIDERS must not include the mock in production"
                )
            if self.cookie_secure is False:
                raise ValueError("COOKIE_SECURE cannot be off in production")
            if self.email_provider != "smtp":
                raise ValueError("EMAIL_PROVIDER must be a real mail service in production")
            if not self.web_origin.startswith("https://"):
                raise ValueError("WEB_ORIGIN must be an https address in production")
        return self

    @property
    def llm_provider_names(self) -> list[str]:
        return [name.strip() for name in self.llm_providers.split(",") if name.strip()]

    @property
    def voice_provider_names(self) -> list[str]:
        return [name.strip() for name in self.voice_providers.split(",") if name.strip()]

    @property
    def stock_provider_names(self) -> list[str]:
        return [name.strip() for name in self.stock_providers.split(",") if name.strip()]

    @property
    def image_provider_names(self) -> list[str]:
        return [name.strip() for name in self.image_providers.split(",") if name.strip()]

    @property
    def jobs_dispatch(self) -> str:
        return self.job_dispatch or ("manual" if self.env == "test" else "celery")

    @property
    def cookies_secure(self) -> bool:
        return self.env == "production" if self.cookie_secure is None else self.cookie_secure

    @property
    def s3_presign_endpoint_url(self) -> str:
        return self.s3_public_endpoint_url or self.s3_endpoint_url
