"""Celery wired into the Flask app, so every task runs with the app's database and settings."""

from __future__ import annotations

from celery import Celery, Task
from flask import Flask

# One queue per kind of work, so slow or scarce work (AI video) never blocks the rest.
# See docs/TECHNICAL_DESIGN.md section 7. Tasks are routed to these as they are added.
QUEUES = ("default", "llm", "voice", "visuals", "ai_video", "render", "publish", "maintenance")


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
