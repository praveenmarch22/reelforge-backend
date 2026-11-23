"""ReelForge API. See docs/TECHNICAL_DESIGN.md and docs/API_DESIGN.md."""

from __future__ import annotations

from pathlib import Path

import redis
from flask import Flask
from flask_cors import CORS

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
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        # Uploads go straight to storage; nothing large should ever be posted to the API itself.
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
        CELERY={
            "broker_url": settings.redis_url,
            "result_backend": settings.redis_url,
            "result_expires": 3600,
            "task_default_queue": "default",
            "task_acks_late": True,
            "worker_prefetch_multiplier": 1,
            "broker_connection_retry_on_startup": True,
        },
    )
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
    )

    from . import tasks  # noqa: F401 - registers the Celery tasks
    from .routes import system

    app.register_blueprint(system.bp, url_prefix=API_PREFIX)
    register_cli(app)
    return app
