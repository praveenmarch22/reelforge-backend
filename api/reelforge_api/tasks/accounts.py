import logging
from datetime import timedelta

from celery import shared_task
from sqlalchemy import delete

from ..extensions import db
from ..models import AuthToken, RefreshToken, User
from ..sessions import now

log = logging.getLogger(__name__)

PURGE_AFTER = timedelta(days=7)


@shared_task(name="accounts.purge_deleted", ignore_result=True)
def purge_deleted() -> int:
    """Permanently removes accounts deleted more than 7 days ago (API A12).

    Deleting the users row removes everything that belongs to it (the foreign keys cascade).
    Stored media files are removed with the projects' assets when those exist (milestone B3).
    """
    result = db.session.execute(delete(User).where(User.deleted_at < now() - PURGE_AFTER))
    db.session.commit()
    if result.rowcount:
        log.info("purged deleted accounts", extra={"count": result.rowcount})
    return result.rowcount


@shared_task(name="accounts.clean_tokens", ignore_result=True)
def clean_tokens() -> None:
    """Removes link tokens 7 days and refresh tokens 30 days after they expired (DB design 8)."""
    db.session.execute(delete(AuthToken).where(AuthToken.expires_at < now() - timedelta(days=7)))
    db.session.execute(delete(RefreshToken).where(RefreshToken.expires_at < now() - timedelta(days=30)))
    db.session.commit()
