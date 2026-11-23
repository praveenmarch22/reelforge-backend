from celery import shared_task
from sqlalchemy import text

from ..extensions import db


@shared_task(name="system.ping")
def ping() -> str:
    """Proves a worker is running and can reach the database. Used by tests and `flask worker-ping`."""
    db.session.execute(text("SELECT 1"))
    return "pong"
