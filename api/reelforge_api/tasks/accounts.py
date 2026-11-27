import logging
from datetime import timedelta

from celery import shared_task
from sqlalchemy import delete, select

from ..extensions import db
from ..models import AuthToken, Project, RefreshToken, User
from ..sessions import now

log = logging.getLogger(__name__)

PURGE_AFTER = timedelta(days=7)


@shared_task(name="accounts.purge_deleted", ignore_result=True)
def purge_deleted() -> int:
    """Permanently removes accounts and projects deleted more than 7 days ago (API A12, B6).

    Deleting a row removes everything that belongs to it (the foreign keys cascade). Their stored
    files are deleted first (B8), since the asset rows are what lists them.
    """
    from ..cleanup import purgeable

    cutoff = now() - PURGE_AFTER
    gone_users = list(db.session.execute(select(User.id).where(User.deleted_at < cutoff)).scalars())
    # A deleted account's projects go with it, deleted or not.
    gone_projects = list(
        db.session.execute(
            select(Project.id).where((Project.deleted_at < cutoff) | Project.user_id.in_(gone_users))
        ).scalars()
    )
    # Files first; only what lost all its files is removed, the rest waits for the next run.
    ok_users, ok_projects = purgeable(user_ids=gone_users, project_ids=gone_projects)
    projects = db.session.execute(
        delete(Project).where(Project.id.in_(ok_projects), Project.deleted_at < cutoff)
    ).rowcount
    users = db.session.execute(delete(User).where(User.id.in_(ok_users))).rowcount
    db.session.commit()
    if users or projects:
        log.info("purged deleted accounts and projects", extra={"accounts": users, "projects": projects})
    return users


@shared_task(name="accounts.clean_tokens", ignore_result=True)
def clean_tokens() -> None:
    """Removes link tokens 7 days and refresh tokens 30 days after they expired (DB design 8)."""
    db.session.execute(delete(AuthToken).where(AuthToken.expires_at < now() - timedelta(days=7)))
    db.session.execute(delete(RefreshToken).where(RefreshToken.expires_at < now() - timedelta(days=30)))
    db.session.commit()
