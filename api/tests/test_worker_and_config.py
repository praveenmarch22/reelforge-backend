import pytest
from pydantic import ValidationError

from reelforge_api.celery_app import QUEUES
from reelforge_api.config import (
    DEV_DATABASE_LOGIN,
    DEV_DATABASE_URL,
    DEV_S3_SECRET,
    DEV_SECRET_KEY,
    Settings,
)
from reelforge_api.tasks.system import ping

from .conftest import build_app


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


def test_the_background_tasks_and_their_schedule_are_registered(app):
    """The names are the contract between the API (which queues by name) and the worker."""
    celery = app.extensions["celery"]
    for name in ("system.ping", "mail.send", "mail.password_reset"):
        assert name in celery.tasks, f"{name} is not registered"
    scheduled = {entry["task"]: entry["options"]["queue"] for entry in celery.conf.beat_schedule.values()}
    assert scheduled == {
        "accounts.purge_deleted": "maintenance",
        "accounts.clean_tokens": "maintenance",
        "jobs.fail_stuck": "maintenance",
        "credits.reset_monthly": "maintenance",
        "connections.check": "maintenance",
        "files.clean_replaced": "maintenance",  # B8
    }
    assert set(scheduled) <= set(celery.tasks)
    # Language-model jobs run on their own queue (technical design 7).
    assert "jobs.run" in celery.tasks
    assert celery.conf.task_routes["jobs.run"] == {"queue": "llm"}


def test_the_schedule_matches_the_technical_design(app):
    """fail_stuck_jobs every 5 minutes, reset_monthly_credits hourly (technical design 7)."""
    schedules = {
        entry["task"]: entry["schedule"] for entry in app.extensions["celery"].conf.beat_schedule.values()
    }
    assert schedules["jobs.fail_stuck"].minute == set(range(0, 60, 5))
    assert schedules["jobs.fail_stuck"].hour == set(range(24))
    assert len(schedules["credits.reset_monthly"].minute) == 1
    assert schedules["credits.reset_monthly"].hour == set(range(24))


# ---------------------------------------------------------------- settings

# A complete production configuration; each test below breaks one part of it.
DATABASE_PASSWORD = "Xq7vTz2mLp9sKd4w"
PRODUCTION = {
    "env": "production",
    "secret_key": "Jf3kPw8xNc5hBv1q" * 3,
    "database_url": f"postgresql+psycopg://reelforge_api:{DATABASE_PASSWORD}@db.internal:5432/reelforge",
    "s3_secret_key": "Zr4nMx7pQc2vLb9s",
    "email_provider": "smtp",
    "smtp_starttls": True,
    "smtp_password": "Hn5cRb8yWj3fGt6u",
    "web_origin": "https://app.reelforge.example",
    "cookie_secure": None,
    # The container's environment says LLM_PROVIDERS=mock, which production refuses.
    "llm_providers": "gemini,groq",
    "render_service_url": "http://renderer.internal:3000",
    "render_service_token": "Vd6tKs2wQm9xLp4h" * 2,
    "publish_providers": "youtube",
    "youtube_redirect_uri": "https://app.reelforge.example/api/v1/connections/youtube/callback",
    # A real Fernet key (32 random bytes, url-safe base64), not the development one.
    "token_encryption_key": "q3V9mL2xR8tY4wZ1nB6cD0fG5hJ7kP9sA2eU4iO6rT8=",
}
SECRETS = (
    "secret_key", "database_url", "s3_secret_key", "smtp_password", "render_service_token",
    "token_encryption_key",
)  # fmt: skip


def shows_part_of(text: str, secret: str, length: int = 6) -> bool:
    """True if any `length` characters in a row of the secret appear in the text."""
    return any(secret[start : start + length] in text for start in range(len(secret) - length + 1))


def test_production_accepts_real_secrets():
    settings = Settings(**PRODUCTION)
    assert settings.env == "production"


def test_production_cookies_are_secure_unless_nothing_says_otherwise():
    assert Settings(**PRODUCTION).cookies_secure is True
    assert Settings(**{**PRODUCTION, "cookie_secure": True}).cookies_secure is True


@pytest.mark.parametrize(
    ("unsafe", "setting"),
    [
        ({"secret_key": DEV_SECRET_KEY}, "SECRET_KEY"),
        ({"secret_key": "short"}, "SECRET_KEY"),
        ({"secret_key": "Jf3kPw8xNc5hBv1" + "q" * 16}, "SECRET_KEY"),  # 31 characters
        ({"s3_secret_key": DEV_S3_SECRET}, "S3_SECRET_KEY"),
        ({"database_url": DEV_DATABASE_URL}, "DATABASE_URL"),
        # The development login on any server, which is what the compose file uses.
        (
            {"database_url": f"postgresql+psycopg://{DEV_DATABASE_LOGIN}db.internal:5432/reelforge"},
            "DATABASE_URL",
        ),
        ({"smtp_starttls": False}, "SMTP_STARTTLS"),
        ({"cookie_secure": False}, "COOKIE_SECURE"),
        ({"email_provider": "console"}, "EMAIL_PROVIDER"),
        ({"email_provider": "memory"}, "EMAIL_PROVIDER"),
        ({"web_origin": "http://app.reelforge.example"}, "WEB_ORIGIN"),
        ({"web_origin": "http://localhost:5173"}, "WEB_ORIGIN"),
        ({"llm_providers": "mock"}, "LLM_PROVIDERS"),
        ({"llm_providers": "gemini,mock"}, "LLM_PROVIDERS"),
        ({"llm_providers": " groq , mock "}, "LLM_PROVIDERS"),
    ],
)
def test_production_refuses_settings_that_are_only_safe_in_development(unsafe, setting):
    """Each is refused for its own reason: the message names the setting to fix."""
    with pytest.raises(ValidationError, match=rf"\b{setting}\b"):
        Settings(**{**PRODUCTION, **unsafe})


@pytest.mark.parametrize(
    ("forgotten", "variable"),
    [
        ("secret_key", "SECRET_KEY"),
        ("database_url", "DATABASE_URL"),
        ("s3_secret_key", "S3_SECRET_KEY"),
        ("smtp_starttls", "SMTP_STARTTLS"),
        ("email_provider", "EMAIL_PROVIDER"),
        ("web_origin", "WEB_ORIGIN"),
    ],
)
def test_production_does_not_start_when_a_setting_is_forgotten(monkeypatch, forgotten, variable):
    """A deployment that leaves a variable out must fail, not run on the development default."""
    monkeypatch.delenv(variable, raising=False)
    with pytest.raises(ValidationError, match=rf"\b{variable}\b"):
        Settings(**{key: value for key, value in PRODUCTION.items() if key != forgotten})


@pytest.mark.parametrize("env", ["development", "test", "production"])
@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://*.reelforge.example",
        "https://app.reelforge.example, *",
        "app.reelforge.example",
        "//app.reelforge.example",
        "ftp://app.reelforge.example",
        "",
        # Characters the CORS library reads as a pattern (BUG-029).
        "https://app.reelforge.example?",
        "https://(app|www).reelforge.example",
        "https://[a-z]+.reelforge.example",
        "https://app.reelforge.example$",
        "https://app\\.reelforge\\.example",
        # More than an origin.
        "https://app.reelforge.example/",
        "https://app.reelforge.example/app",
        "https://app.reelforge.example,https://www.reelforge.example",
        "https://user:secret@app.reelforge.example",
        " https://app.reelforge.example",
        "https://app.reelforge.example ",
        "https://app.reelforge.example\n",
        "https://",
        "https://app..reelforge.example",
    ],
)
def test_no_environment_accepts_a_web_origin_that_is_not_one_address(env, origin):
    """The API answers with cookies allowed, so "any origin" would let every website act as
    the logged-in user."""
    with pytest.raises(ValidationError, match="WEB_ORIGIN"):
        Settings(**{**PRODUCTION, "env": env, "web_origin": origin})


@pytest.mark.parametrize(
    "pattern",
    [
        "https://app.reelforge.example?",
        "https://(app|www).reelforge.example",
        "https://[a-z]+.reelforge.example",
    ],
)
def test_a_web_origin_written_as_a_pattern_does_not_let_other_sites_in(pattern):
    """Whatever WEB_ORIGIN the settings accept, a site that merely starts like the web app's
    address must not be answered with the cookie-allowing headers."""
    try:
        patterned = build_app(web_origin=pattern)
    except ValidationError:
        return  # refused at start-up: safe
    for other_site in ("https://app.reelforge.example.evil.example", "https://appXreelforge.example"):
        res = patterned.test_client().get("/api/v1/health", headers={"Origin": other_site})
        assert "Access-Control-Allow-Origin" not in res.headers, f"{other_site} may call with cookies"


@pytest.mark.parametrize(
    "origin",
    [
        "https://app.reelforge.example",
        "https://reelforge.example:8443",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://web-app",
    ],
)
def test_one_exact_origin_is_accepted_and_is_the_only_one_allowed(origin):
    exact = build_app(web_origin=origin)
    ours = exact.test_client().get("/api/v1/health", headers={"Origin": origin})
    assert ours.headers["Access-Control-Allow-Origin"] == origin
    for other_site in (origin + ".evil.example", origin.replace(".", "x"), origin + "0", "null"):
        if other_site == origin:
            continue
        res = exact.test_client().get("/api/v1/health", headers={"Origin": other_site})
        assert "Access-Control-Allow-Origin" not in res.headers, f"{other_site} may call with cookies"


def test_printing_the_settings_does_not_show_the_secrets():
    settings = Settings(**{**PRODUCTION, "redis_url": "rediss://:Wd6gYk1tSe9aPm3x@cache.internal:6379/0"})
    assert "Wd6gYk1tSe9aPm3x" not in repr(settings) + str(settings), "the Redis password is shown"
    for shown in (repr(settings), str(settings)):
        for name in SECRETS:
            assert PRODUCTION[name] not in shown, f"{name} is shown"
            assert f"{name}=" not in shown
    assert "web_origin='https://app.reelforge.example'" in repr(settings), "ordinary settings stay readable"


@pytest.mark.parametrize(
    ("broken", "value", "setting"),
    [
        ("SMTP_STARTTLS", None, "SMTP_STARTTLS"),
        ("WEB_ORIGIN", "https://*.reelforge.example", "WEB_ORIGIN"),
        ("COOKIE_SECURE", "false", "COOKIE_SECURE"),
        ("ENV", "staging", "env"),
        ("SMTP_PORT", "not-a-number", "smtp_port"),
    ],
)
def test_a_refused_production_configuration_does_not_print_its_secrets(monkeypatch, broken, value, setting):
    """The start-up error ends up in the deployment's log. The settings are read from
    environment variables, as a real start does, with every secret set and one thing wrong."""
    for name in ("SMTP_STARTTLS", "COOKIE_SECURE", "MONTHLY_CREDITS", "MONTHLY_CREDITS_TESTER"):
        monkeypatch.delenv(name, raising=False)
    for name, setting_value in PRODUCTION.items():
        if setting_value is not None:
            monkeypatch.setenv(name.upper(), str(setting_value))
    if value is None:
        monkeypatch.delenv(broken)
    else:
        monkeypatch.setenv(broken, value)

    with pytest.raises(ValidationError, match=setting) as refused:
        Settings()

    shown = str(refused.value)
    secrets = {**{name: PRODUCTION[name] for name in SECRETS}, "database_url": DATABASE_PASSWORD}
    leaked = [name for name, value in secrets.items() if shows_part_of(shown, value)]
    assert leaked == [], f"the error message shows (part of) {leaked}: {shown}"


@pytest.mark.parametrize("env", ["development", "test", "production"])
@pytest.mark.parametrize("providers", ["openai", "gemini,openai", "", " , "])
def test_no_environment_accepts_an_unknown_language_model(env, providers):
    with pytest.raises(ValidationError, match="LLM_PROVIDERS"):
        Settings(**{**PRODUCTION, "env": env, "llm_providers": providers})


@pytest.mark.parametrize("providers", ["mock", "gemini,groq,mock"])
def test_the_mock_language_model_is_allowed_outside_production(providers):
    for env in ("development", "test"):
        assert Settings(**{**PRODUCTION, "env": env, "llm_providers": providers}).llm_provider_names


def test_outside_production_cookies_are_not_secure_by_default_so_plain_http_works():
    assert Settings(env="development", cookie_secure=None).cookies_secure is False
    assert Settings(env="development", cookie_secure=True).cookies_secure is True


def test_presigned_links_use_the_public_address_when_set():
    internal, public = "http://minio:9000", "http://localhost:9000"
    assert Settings(s3_endpoint_url=internal, s3_public_endpoint_url=public).s3_presign_endpoint_url == public
    # Passed explicitly: the container's own environment sets a public address.
    assert Settings(s3_endpoint_url=internal, s3_public_endpoint_url="").s3_presign_endpoint_url == internal
