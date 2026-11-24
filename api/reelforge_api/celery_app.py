"""Celery wired into the Flask app, so every task runs with the app's database and settings."""

from __future__ import annotations

from celery import Celery, Task
from celery.signals import setup_logging
from flask import Flask

from .log import use_json_logs

# One queue per kind of work, so slow or scarce work (AI video) never blocks the rest.
# See docs/TECHNICAL_DESIGN.md section 7. Tasks are routed to these as they are added.
QUEUES = ("default", "llm", "voice", "visuals", "ai_video", "render", "publish", "maintenance")


@setup_logging.connect
def _worker_logging(**_: object) -> None:
    """The worker logs the same JSON lines as the API instead of Celery's own format."""
    use_json_logs()


def celery_init_app(app: Flask) -> Celery:
    class FlaskTask(Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.config_from_object(app.config["CELERY"])
    celery_app.set_default()
    app.extensions["celery"] = celery_app
    return celery_app
