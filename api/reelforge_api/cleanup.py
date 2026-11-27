"""Removing stored files that nothing uses any more (milestone B8; PRD F-2, DB design 5).

- A file replaced by a newer take (a new voice, another visual) is kept 7 days, so an undo or a
  render still running can use it, then deleted with its asset row, unless a scene, a render or
  a project still points at it.
- Accounts and projects deleted more than 7 days ago are purged (tasks.accounts); their files
  are deleted here first, since the database rows are what lists them.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select, union

from .extensions import db
from .models import Asset, Project, Render, Scene
from .sessions import now
from .storage import get_storage

log = logging.getLogger(__name__)

KEEP_REPLACED = timedelta(days=7)
BATCH = 500


def _delete_files(keys: list[str]) -> set[str]:
    """Deletes stored files. Returns the keys that are gone; a failure is tried on the next run,
    and its row is kept until then so the file is never orphaned (QB8-002)."""
    storage = get_storage()
    gone: set[str] = set()
    for key in keys:
        try:
            storage.delete(key)  # S3 answers success for a key that is already gone
            gone.add(key)
        except Exception:  # noqa: BLE001
            log.warning("could not delete a stored file", extra={"key": key}, exc_info=True)
    return gone


def _keys(s3_key: str, meta: dict | None) -> list[str]:
    """An asset's files: its own, and the small still a visual keeps beside it (BUG-086)."""
    thumb = (meta or {}).get("thumbnailKey")
    return [s3_key, thumb] if thumb else [s3_key]


def _in_use(asset_ids: list) -> set:
    """Assets something still points at: a scene, a render (its video or cover) or a project."""
    if not asset_ids:
        return set()
    return set(
        db.session.execute(
            union(
                select(Scene.voice_asset_id).where(Scene.voice_asset_id.in_(asset_ids)),
                select(Scene.visual_asset_id).where(Scene.visual_asset_id.in_(asset_ids)),
                select(Scene.keyframe_asset_id).where(Scene.keyframe_asset_id.in_(asset_ids)),
                select(Render.asset_id).where(Render.asset_id.in_(asset_ids)),
                select(Render.thumbnail_asset_id).where(Render.thumbnail_asset_id.in_(asset_ids)),
                select(Project.thumbnail_asset_id).where(Project.thumbnail_asset_id.in_(asset_ids)),
                select(Project.music_asset_id).where(Project.music_asset_id.in_(asset_ids)),
            )
        ).scalars()
    )


def clean_replaced() -> int:
    """Daily: deletes files replaced more than 7 days ago that nothing uses. Returns how many."""
    cutoff = now() - KEEP_REPLACED
    total = 0
    while True:
        candidates = list(
            db.session.execute(
                select(Asset.id, Asset.s3_key, Asset.meta).where(Asset.replaced_at < cutoff).limit(BATCH)
            ).all()
        )
        if not candidates:
            break
        used = _in_use([a.id for a in candidates])
        unused = [a for a in candidates if a.id not in used]
        # Still used after all (a duplicate reuses it): no longer counted as replaced.
        for a in candidates:
            if a.id in used:
                db.session.execute(Asset.__table__.update().where(Asset.id == a.id).values(replaced_at=None))
        db.session.commit()
        if unused:
            gone = _delete_files([key for a in unused for key in _keys(a.s3_key, a.meta)])
            deleted = [a.id for a in unused if set(_keys(a.s3_key, a.meta)) <= gone]
            if deleted:
                db.session.execute(Asset.__table__.delete().where(Asset.id.in_(deleted)))
                db.session.commit()
            total += len(deleted)
            if len(deleted) < len(unused):
                break  # storage trouble: the rest waits for the next run
        if len(candidates) < BATCH:
            break
    if total:
        log.info("deleted replaced files", extra={"count": total})
    return total


def rehome_shared(project_ids: list) -> int:
    """Before projects are purged: a file one of them owns but a surviving project still uses (a
    duplicated Reel shares its source's files; an uploaded music file can be chosen elsewhere)
    is handed to that surviving project, so neither its row nor its file is removed (QB8-001)."""
    if not project_ids:
        return 0
    owned = select(Asset.id).where(Asset.project_id.in_(project_ids))
    moved = 0
    for column in (Scene.voice_asset_id, Scene.visual_asset_id, Scene.keyframe_asset_id):
        rows = db.session.execute(
            select(column, Scene.project_id).where(column.in_(owned), Scene.project_id.notin_(project_ids))
        ).all()
        for asset_id, keeper in rows:
            moved += db.session.execute(
                Asset.__table__.update()
                .where(Asset.id == asset_id, Asset.project_id.in_(project_ids))
                .values(project_id=keeper)
            ).rowcount
    for column in (Project.music_asset_id, Project.thumbnail_asset_id):
        rows = db.session.execute(
            select(column, Project.id).where(column.in_(owned), Project.id.notin_(project_ids))
        ).all()
        for asset_id, keeper in rows:
            moved += db.session.execute(
                Asset.__table__.update()
                .where(Asset.id == asset_id, Asset.project_id.in_(project_ids))
                .values(project_id=keeper)
            ).rowcount
    db.session.flush()
    return moved


def purgeable(*, user_ids: list, project_ids: list) -> tuple[list, list]:
    """Deletes the files of these deleted accounts and projects. Returns the (users, projects)
    whose files are all gone and whose rows may now be removed; the others wait for the next
    run (QB8-002)."""
    rehome_shared(project_ids)
    db.session.commit()
    ok_projects = []
    for project_id in project_ids:
        rows = db.session.execute(
            select(Asset.s3_key, Asset.meta).where(Asset.project_id == project_id)
        ).all()
        keys = [key for r in rows for key in _keys(r.s3_key, r.meta)]
        if _delete_files(keys) >= set(keys):
            ok_projects.append(project_id)
    ok_users = []
    for user_id in user_ids:
        rows = db.session.execute(select(Asset.s3_key, Asset.meta).where(Asset.user_id == user_id)).all()
        keys = [key for r in rows for key in _keys(r.s3_key, r.meta)]
        if _delete_files(keys) >= set(keys):
            ok_users.append(user_id)
    return ok_users, ok_projects
