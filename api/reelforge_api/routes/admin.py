"""Admin (API design group I: I1 to I13; milestone B8). Every route needs an admin, and every
change is logged with the admin's id (PRD 5.11)."""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from typing import Annotated, Literal

from flask import Blueprint, current_app, jsonify, request
from pydantic import Field
from sqlalchemy import and_, func, or_, select, text

from .. import credits, jobs
from ..auth import current_user, require_user
from ..errors import ApiError
from ..extensions import db
from ..models import (
    CreditAccount,
    CreditLedger,
    Job,
    MusicTrack,
    Project,
    ProviderSwitch,
    Scene,
    User,
)
from ..music import track_json
from ..project_schemas import _line, decode_cursor, encode_cursor, read_limit
from ..schemas import Body, read_body
from ..script_rules import MOODS
from ..sessions import now, revoke_all_sessions
from ..storage import get_storage

bp = Blueprint("admin", __name__, url_prefix="/admin")
log = logging.getLogger(__name__)

# Every provider the admin can switch off (G-3, AD2), by the job types that use it.
PROVIDERS = {
    "gemini": "language model",
    "groq": "language model",
    "elevenlabs": "voice",
    "edge": "voice",
    "pixabay": "stock video",
    "cloudflare": "AI images",
    "youtube": "publishing",
}
MUSIC_TYPES = ("audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav", "audio/ogg")
MUSIC_MAX_BYTES = 30 * 1024 * 1024
# I6: only a scene's voice or visual. Ideas, hooks and post text are asked again by the creator
# with one click, and their result would go nowhere (QB8-005).
RETRYABLE = ("voice", "visual")


def _iso(value) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        raise ApiError(404, "not_found", "We couldn't find that.") from None


def _audit(action: str, **details) -> None:
    """The record of an admin change (PRD 5.11: logged with the admin's id)."""
    log.info("admin change", extra={"admin_id": str(current_user().id), "action": action, **details})


# ---------------------------------------------------------------- users (I1 to I4)


def _user_row(user: User, projects: int) -> dict:
    account = user.credit_account
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "isAdmin": user.is_admin,
        "isTester": user.is_tester,
        "disabled": user.disabled_at is not None,
        "emailVerified": user.email_verified_at is not None,
        "balance": account.balance if account else 0,
        "monthlyAllowance": account.monthly_allowance if account else 0,
        "projectCount": projects,
        "lastLoginAt": _iso(user.last_login_at),
        "createdAt": _iso(user.created_at),
    }


def _project_count(user_id) -> int:
    return db.session.execute(
        select(func.count()).where(Project.user_id == user_id, Project.deleted_at.is_(None))
    ).scalar_one()


def _get_user(user_id: str) -> User:
    user = db.session.get(User, _uuid(user_id))
    if user is None or user.deleted_at is not None:
        raise ApiError(404, "not_found", "We couldn't find that.")
    return user


@bp.get("/users")
@require_user(admin=True)
def list_users():
    """I1. Search by email or name, newest first, with balance and project count."""
    limit = read_limit(request.args.get("limit"))
    cursor = decode_cursor(request.args.get("cursor"))
    q = (request.args.get("q") or "").strip()[:100]
    query = select(User).where(User.deleted_at.is_(None))
    if q:
        # autoescape: % and _ in the search are plain characters (QB8-007).
        query = query.where(
            or_(User.email.icontains(q, autoescape=True), User.name.icontains(q, autoescape=True))
        )
    if cursor:
        moment, last_id = cursor
        query = query.where(or_(User.created_at < moment, and_(User.created_at == moment, User.id < last_id)))
    rows = list(
        db.session.execute(query.order_by(User.created_at.desc(), User.id.desc()).limit(limit + 1)).scalars()
    )
    page = rows[:limit]
    return jsonify(
        items=[_user_row(u, _project_count(u.id)) for u in page],
        nextCursor=encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None,
    )


@bp.get("/users/<user_id>")
@require_user(admin=True)
def get_user(user_id: str):
    """I2. One user with credits, recent jobs and their publishing connection."""
    user = _get_user(user_id)
    recent = db.session.execute(
        select(Job).where(Job.user_id == user.id).order_by(Job.queued_at.desc()).limit(20)
    ).scalars()
    youtube = user.youtube_account
    return jsonify(
        user={
            **_user_row(user, _project_count(user.id)),
            "niche": user.niche,
            "resetsAt": _iso(user.credit_account.resets_at) if user.credit_account else None,
            "youtube": {"status": youtube.status, "name": youtube.channel_title} if youtube else None,
            "recentJobs": jobs.jobs_json(list(recent)),
        }
    )


class AdminUserBody(Body):
    is_tester: bool | None = None
    monthly_allowance: Annotated[int, Field(ge=0, le=100_000)] | None = None
    disabled: bool | None = None
    is_admin: bool | None = None


@bp.patch("/users/<user_id>")
@require_user(admin=True)
def update_user(user_id: str):
    """I3. Tester, allowance, disabled, admin."""
    body = read_body(AdminUserBody)
    user = _get_user(user_id)
    me = current_user()
    if user.id == me.id and (body.disabled is True or body.is_admin is False):
        raise ApiError(
            409, "own_account", "You can't disable your own account or remove your own admin role."
        )
    changes = body.model_dump(exclude_none=True)
    if body.is_tester is not None and body.is_tester != user.is_tester:
        user.is_tester = body.is_tester
        # A tester gets the larger monthly allowance from the next reset (DB design; QB8-010).
        settings = current_app.extensions["settings"]
        if body.monthly_allowance is None and user.credit_account is not None:
            user.credit_account.monthly_allowance = (
                settings.monthly_credits_tester if body.is_tester else settings.monthly_credits
            )
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.monthly_allowance is not None and user.credit_account is not None:
        user.credit_account.monthly_allowance = body.monthly_allowance
    if body.disabled is not None:
        if body.disabled and user.disabled_at is None:
            user.disabled_at = now()
            revoke_all_sessions(user.id)  # signed out everywhere at once
        elif not body.disabled:
            user.disabled_at = None
    db.session.commit()
    _audit("update_user", user_id=str(user.id), changes=changes)
    return jsonify(user=_user_row(user, _project_count(user.id)))


class AdjustBody(Body):
    amount: Annotated[int, Field(ge=-100_000, le=100_000)]
    note: Annotated[str, _line(200, required=True)]


@bp.post("/users/<user_id>/credits")
@require_user(admin=True)
def adjust_credits(user_id: str):
    """I4. An `adjust` ledger row; the balance never goes below zero."""
    body = read_body(AdjustBody)
    if body.amount == 0:
        raise ApiError(
            400,
            "validation_error",
            "Some details need another look.",
            {"fields": {"amount": "Use a number other than 0"}},
        )
    user = _get_user(user_id)
    account = credits.lock_account(user.id)
    if account.balance + body.amount < 0:
        raise ApiError(
            409, "balance_negative", f"That would take the balance below zero (it is {account.balance})."
        )
    account.balance += body.amount
    db.session.add(
        CreditLedger(
            user_id=user.id, amount=body.amount, kind="adjust", note=body.note, created_by=current_user().id
        )
    )
    db.session.commit()
    _audit("adjust_credits", user_id=str(user.id), amount=body.amount)
    return jsonify(balance=account.balance), 201


# ---------------------------------------------------------------- jobs (I5, I6)


@bp.get("/jobs")
@require_user(admin=True)
def list_jobs():
    """I5. Jobs for troubleshooting, newest first; filter by state, type and provider."""
    limit = read_limit(request.args.get("limit"))
    cursor = decode_cursor(request.args.get("cursor"))
    query = select(Job, User.email).join(User, User.id == Job.user_id)
    for arg, column in (("state", Job.state), ("type", Job.type), ("provider", Job.provider)):
        if request.args.get(arg):
            query = query.where(column == request.args[arg])
    if cursor:
        moment, last_id = cursor
        query = query.where(or_(Job.queued_at < moment, and_(Job.queued_at == moment, Job.id < last_id)))
    rows = db.session.execute(query.order_by(Job.queued_at.desc(), Job.id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    items = [{**jobs.job_json(job), "userEmail": email, "attempts": job.attempts} for job, email in page]
    return jsonify(
        items=items,
        nextCursor=encode_cursor(page[-1][0].queued_at, page[-1][0].id) if len(rows) > limit else None,
    )


@bp.post("/jobs/<job_id>/retry")
@require_user(admin=True)
def retry_job(job_id: str):
    """I6. Starts a failed job again as a new job with the same input, free of charge.
    Only jobs whose input is all they need (language-model work, a scene's voice or visual)."""
    old = db.session.get(Job, _uuid(job_id))
    if old is None:
        raise ApiError(404, "not_found", "We couldn't find that.")
    if old.state != "failed":
        raise ApiError(409, "not_failed", "Only failed jobs can be retried.")
    if old.type not in RETRYABLE:
        raise ApiError(409, "not_retryable", "This kind of job is started again by the creator.")
    if (old.output or {}).get("retriedAs"):
        raise ApiError(409, "not_retryable", "This job was already retried.")
    owner = db.session.get(User, old.user_id)
    if owner is None or owner.deleted_at is not None or owner.disabled_at is not None:
        raise ApiError(409, "not_retryable", "Its account is disabled or deleted.")
    project = db.session.execute(
        select(Project).where(Project.id == old.project_id).with_for_update()
    ).scalar_one_or_none()
    if project is None or project.deleted_at is not None:
        raise ApiError(409, "not_retryable", "Its Reel has been deleted.")
    if project.status in ("scripting", "rendering", "posting"):
        raise ApiError(409, "not_retryable", "Its Reel is busy right now.")
    scene = (
        db.session.execute(
            select(Scene).where(Scene.id == old.scene_id).with_for_update()
        ).scalar_one_or_none()
        if old.scene_id
        else None
    )
    if scene is None:
        raise ApiError(409, "not_retryable", "Its scene has been deleted.")
    # Only while that part of the scene is still failed: the creator may have redone or changed
    # it since, and their work is never replaced from here (QB8-005).
    if getattr(scene, f"{old.type}_status") != "failed":
        raise ApiError(409, "not_retryable", "The scene is no longer failed.")
    if old.type == "voice" and (scene.words_edited or scene.narration != old.input.get("narration")):
        raise ApiError(409, "not_retryable", "The scene was edited since; the creator starts it again.")
    new = jobs.new_job(
        old.user_id,
        old.type,
        project_id=old.project_id,
        scene_id=old.scene_id,
        input={**old.input, "previousStatus": "failed"},
        cost=0,
    )
    setattr(scene, f"{old.type}_status", "queued")
    old.output = {**(old.output or {}), "retriedAs": str(new.id)}
    db.session.commit()
    _audit("retry_job", job_id=str(old.id), new_job_id=str(new.id))
    answer = jsonify(job=jobs.job_json(new, False))
    jobs.dispatch([new])
    return answer, 202


# ---------------------------------------------------------------- usage and providers (I7 to I9)


@bp.get("/usage")
@require_user(admin=True)
def usage():
    """I7. Calls, failures and credits per day, provider and job type (last 30 days by default)."""

    def day(name: str, default: date) -> date:
        try:
            return date.fromisoformat(request.args[name]) if request.args.get(name) else default
        except ValueError:
            raise ApiError(
                400,
                "validation_error",
                "Some details need another look.",
                {"fields": {name: "Use YYYY-MM-DD"}},
            ) from None

    end = day("to", date.today())
    start = day("from", end - timedelta(days=29))
    if start > end or (end - start).days > 366:
        raise ApiError(
            400, "validation_error", "Some details need another look.", {"fields": {"from": "Up to a year"}}
        )
    rows = db.session.execute(
        text(
            "SELECT day, provider, job_type, calls, failures, credits_charged, cost_estimate_usd "
            "FROM usage_daily WHERE day BETWEEN :start AND :end ORDER BY day DESC, provider, job_type"
        ),
        {"start": start, "end": end},
    ).all()
    return jsonify(
        items=[
            {
                "day": r.day.isoformat(),
                "provider": r.provider,
                "jobType": r.job_type,
                "calls": r.calls,
                "failures": r.failures,
                "creditsCharged": r.credits_charged,
                "costEstimateUsd": float(r.cost_estimate_usd),
            }
            for r in rows
        ],
        **{"from": start.isoformat(), "to": end.isoformat()},
    )


def _provider_rows() -> list[dict]:
    switches = {s.provider: s for s in db.session.execute(select(ProviderSwitch)).scalars()}
    today = {
        r.provider: (r.calls, r.failures)
        for r in db.session.execute(
            text(
                "SELECT provider, sum(calls) AS calls, sum(failures) AS failures FROM usage_daily "
                "WHERE day = current_date GROUP BY provider"
            )
        ).all()
    }
    rows = []
    for name, purpose in PROVIDERS.items():
        switch = switches.get(name)
        calls, failures = today.get(name, (0, 0))
        rows.append(
            {
                "name": name,
                "purpose": purpose,
                "enabled": switch.enabled if switch else True,
                "reason": switch.reason if switch else "",
                "callsToday": int(calls or 0),
                "errorRateToday": round(int(failures or 0) / int(calls), 3) if calls else None,
            }
        )
    return rows


@bp.get("/providers")
@require_user(admin=True)
def list_providers():
    """I8. Every provider with its switch and today's error rate (usage is counted per day)."""
    return jsonify(items=_provider_rows())


class ProviderBody(Body):
    enabled: bool
    reason: Annotated[str, _line(200)] = ""


@bp.patch("/providers/<name>")
@require_user(admin=True)
def switch_provider(name: str):
    """I9. The kill switch (AD2). Switched off, jobs skip it and use the next provider."""
    if name not in PROVIDERS:
        raise ApiError(404, "not_found", "We couldn't find that.")
    body = read_body(ProviderBody)
    if not body.enabled and not body.reason:
        raise ApiError(
            400,
            "validation_error",
            "Some details need another look.",
            {"fields": {"reason": "Say why it is off"}},
        )
    switch = db.session.get(ProviderSwitch, name)
    if switch is None:
        switch = ProviderSwitch(provider=name)
        db.session.add(switch)
    switch.enabled, switch.reason = body.enabled, body.reason
    db.session.flush()
    # Who switched it and when (columns the model doesn't map).
    db.session.execute(
        text("UPDATE provider_switches SET updated_by = :admin, updated_at = now() WHERE provider = :name"),
        {"admin": current_user().id, "name": name},
    )
    db.session.commit()
    _audit("switch_provider", provider=name, enabled=body.enabled)
    return jsonify(provider=next(p for p in _provider_rows() if p["name"] == name))


# ---------------------------------------------------------------- music (I10 to I13)


def _admin_track(track: MusicTrack) -> dict:
    return {**track_json(track), "isActive": track.is_active, "createdAt": _iso(track.created_at)}


@bp.get("/music")
@require_user(admin=True)
def list_music():
    """I10. The library, inactive tracks included."""
    tracks = db.session.execute(select(MusicTrack).order_by(MusicTrack.title)).scalars()
    return jsonify(items=[_admin_track(t) for t in tracks])


def _moods(value: list[str]) -> list[str]:
    moods = sorted({m.strip().lower() for m in value})
    if not moods or any(m not in MOODS for m in moods):
        raise ApiError(
            400,
            "validation_error",
            "Some details need another look.",
            {"fields": {"moods": f"Use some of: {', '.join(MOODS)}"}},
        )
    return moods


class NewTrackBody(Body):
    title: Annotated[str, _line(120, required=True)]
    moods: Annotated[list[str], Field(min_length=1, max_length=10)]
    license: Annotated[str, _line(300, required=True)]
    source_url: Annotated[str, _line(300)] = ""
    filename: Annotated[str, _line(200, required=True)]
    content_type: Literal["audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav", "audio/ogg"]
    size_bytes: Annotated[int, Field(gt=0, le=MUSIC_MAX_BYTES)]
    duration_seconds: Annotated[float, Field(gt=0, le=3600)]


@bp.post("/music")
@require_user(admin=True)
def add_music():
    """I11. A new track, inactive until its file is uploaded to the returned link and the admin
    switches it on (I12)."""
    body = read_body(NewTrackBody)
    extension = {"audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/ogg": "ogg"}.get(body.content_type, "wav")
    track = MusicTrack(
        id=uuid.uuid4(),
        title=body.title,
        moods=_moods(body.moods),
        license=body.license,
        source_url=body.source_url,
        duration_seconds=body.duration_seconds,
        is_active=False,
    )
    track.s3_key = f"library/music/{track.id}.{extension}"
    db.session.add(track)
    db.session.commit()
    _audit("add_music", track_id=str(track.id))
    upload_url = get_storage().presigned_put(track.s3_key, body.content_type)
    return jsonify(track=_admin_track(track), uploadUrl=upload_url), 201


class TrackBody(Body):
    title: Annotated[str, _line(120, required=True)] | None = None
    moods: Annotated[list[str], Field(min_length=1, max_length=10)] | None = None
    license: Annotated[str, _line(300, required=True)] | None = None
    is_active: bool | None = None


def _get_track(track_id: str) -> MusicTrack:
    track = db.session.get(MusicTrack, _uuid(track_id))
    if track is None:
        raise ApiError(404, "not_found", "We couldn't find that.")
    return track


@bp.patch("/music/<track_id>")
@require_user(admin=True)
def update_music(track_id: str):
    """I12. Title, moods, licence, active."""
    body = read_body(TrackBody)
    track = _get_track(track_id)
    if body.title is not None:
        track.title = body.title
    if body.moods is not None:
        track.moods = _moods(body.moods)
    if body.license is not None:
        track.license = body.license
    if body.is_active is not None:
        if body.is_active:
            storage = get_storage()
            if not storage.exists(track.s3_key):
                raise ApiError(409, "file_missing", "Upload the track's file before switching it on.")
            if not 0 < storage.size(track.s3_key) <= MUSIC_MAX_BYTES:
                raise ApiError(409, "file_too_large", "The track's file is empty or over 30 MB.")
        track.is_active = body.is_active
    db.session.commit()
    _audit("update_music", track_id=str(track.id), changes=body.model_dump(exclude_none=True))
    return jsonify(track=_admin_track(track))


@bp.delete("/music/<track_id>")
@require_user(admin=True)
def delete_music(track_id: str):
    """I13. Deactivates it; deleted for real only when no project uses it."""
    track = _get_track(track_id)
    used = db.session.execute(select(Project.id).where(Project.music_track_id == track.id).limit(1)).first()
    if used:
        track.is_active = False
        db.session.commit()
        _audit("deactivate_music", track_id=str(track.id))
        return jsonify(deleted=False, track=_admin_track(track))
    key = track.s3_key
    db.session.delete(track)
    db.session.commit()
    try:
        get_storage().delete(key)
    except Exception:  # noqa: BLE001 - the row is gone; the file is left for the clean-up
        log.warning("could not delete a music file", exc_info=True)
    _audit("delete_music", track_id=track_id)
    return jsonify(deleted=True, track=None)


def admin_stats() -> dict:
    """The numbers at the top of the admin console."""
    return {
        "users": db.session.execute(select(func.count()).where(User.deleted_at.is_(None))).scalar_one(),
        "projects": db.session.execute(
            select(func.count())
            .select_from(Project)
            .join(User, User.id == Project.user_id)
            .where(Project.deleted_at.is_(None), User.deleted_at.is_(None))
        ).scalar_one(),
        "failedJobsToday": db.session.execute(
            select(func.count()).where(Job.state == "failed", Job.finished_at >= func.current_date())
        ).scalar_one(),
        "creditsInCirculation": db.session.execute(
            select(func.coalesce(func.sum(CreditAccount.balance), 0))
            .join(User, User.id == CreditAccount.user_id)
            .where(User.deleted_at.is_(None))
        ).scalar_one(),
    }


@bp.get("/stats")
@require_user(admin=True)
def stats():
    """The admin console's summary (users, Reels, today's failed jobs, credits held)."""
    return jsonify(admin_stats())
