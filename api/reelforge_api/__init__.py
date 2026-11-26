"""ReelForge API. See docs/TECHNICAL_DESIGN.md and docs/API_DESIGN.md."""

from __future__ import annotations

from pathlib import Path

import redis
from celery.schedules import crontab
from flask import Flask
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from .celery_app import celery_init_app
from .cli import register_cli
from .config import Settings
from .errors import register_error_handlers
from .extensions import db, migrate
from .log import configure_logging, register_request_id
from .storage import Storage

API_PREFIX = "/api/v1"
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=settings.secret_key,
        SQLALCHEMY_DATABASE_URI=settings.database_url,
        SQLALCHEMY_ENGINE_OPTIONS={
            "pool_pre_ping": True,
            # Query values (emails, password hashes) stay out of error messages and logs.
            "hide_parameters": True,
            # Give up on an unreachable database quickly instead of holding the request.
            "connect_args": {"connect_timeout": 3},
        },
        # Uploads go straight to storage; nothing large should ever be posted to the API itself.
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
        CELERY={
            "broker_url": settings.redis_url,
            "result_backend": settings.redis_url,
            "result_expires": 3600,
            "task_default_queue": "default",
            "task_acks_late": True,
            "worker_prefetch_multiplier": 1,
            # A task not finished in this time is delivered again; it must outlast a render.
            "broker_transport_options": {
                "visibility_timeout": max(3600, settings.render_time_limit_seconds + 600)
            },
            "broker_connection_retry_on_startup": True,
            # Housekeeping, run by the worker's built-in scheduler (celery beat).
            "beat_schedule": {
                "purge-deleted-accounts": {
                    "task": "accounts.purge_deleted",
                    "schedule": crontab(hour=3, minute=10),
                    "options": {"queue": "maintenance"},
                },
                "clean-expired-tokens": {
                    "task": "accounts.clean_tokens",
                    "schedule": crontab(hour=3, minute=40),
                    "options": {"queue": "maintenance"},
                },
                "fail-stuck-jobs": {
                    "task": "jobs.fail_stuck",
                    "schedule": crontab(minute="*/5"),
                    "options": {"queue": "maintenance"},
                },
                "reset-monthly-credits": {
                    "task": "credits.reset_monthly",
                    "schedule": crontab(minute=5),
                    "options": {"queue": "maintenance"},
                },
            },
            "task_routes": {"jobs.run": {"queue": "llm"}},
        },
    )
    if settings.trusted_proxy_hops:
        # Behind a load balancer the connecting address is the balancer's; take the client's
        # address and scheme from the headers it sets. Only on when configured, because
        # anyone can send these headers to an API that is reached directly.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=settings.trusted_proxy_hops, x_proto=1)
    app.extensions["settings"] = settings
    app.extensions["redis"] = redis.Redis.from_url(
        settings.redis_url, socket_connect_timeout=2, socket_timeout=2
    )
    app.extensions["storage"] = Storage(settings)

    configure_logging(app)
    register_request_id(app)
    register_error_handlers(app)

    db.init_app(app)
    migrate.init_app(app, db, directory=str(MIGRATIONS_DIR))
    celery_init_app(app)

    # The web app calls the API with cookies, so only its own origin is allowed.
    CORS(
        app,
        resources={f"{API_PREFIX}/*": {"origins": [settings.web_origin]}},
        supports_credentials=True,
        expose_headers=["X-Request-ID", "Retry-After"],
        max_age=600,  # browsers may reuse the permission check for 10 minutes
    )

    from . import tasks  # noqa: F401 - registers the Celery tasks
    from .routes import auth, catalogue, jobs_credits, me, projects, renders, scenes, system

    for blueprint in (
        system.bp,
        auth.bp,
        me.bp,
        projects.bp,
        scenes.bp,
        jobs_credits.bp,
        catalogue.bp,
        renders.bp,
    ):
        app.register_blueprint(blueprint, url_prefix=API_PREFIX + (blueprint.url_prefix or ""))
    register_cli(app)
    return app
