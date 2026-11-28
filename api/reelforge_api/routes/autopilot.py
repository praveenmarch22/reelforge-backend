"""The daily autopilot's API (docs/AUTOPILOT.md, group L)."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from flask import Blueprint, jsonify
from pydantic import Field
from sqlalchemy import select

from .. import autopilot, jobs, projects
from ..auth import current_user, require_user
from ..errors import ApiError
from ..extensions import db
from ..models import AutopilotTopic, YoutubeAccount
from ..project_schemas import _line
from ..schemas import Body, read_body
from ..script_rules import TONES
from .projects import start_llm_job

bp = Blueprint("autopilot", __name__, url_prefix="/autopilot")

Category = Literal["football", "f1", "cricket"]
SUGGEST = 14


def _state() -> dict:
    user = current_user()
    settings = autopilot.settings_for(user.id)
    mine = select(AutopilotTopic).where(AutopilotTopic.user_id == user.id)
    queue = db.session.execute(
        mine.where(AutopilotTopic.status == "queued").order_by(
            AutopilotTopic.position, AutopilotTopic.created_at
        )
    ).scalars()
    # The newest runs only; each needs its Reel's details (QAP-009).
    runs = db.session.execute(
        mine.where(AutopilotTopic.status != "queued")
        .order_by(AutopilotTopic.run_on.desc().nulls_last(), AutopilotTopic.updated_at.desc())
        .limit(autopilot.RUNS_SHOWN)
    ).scalars()
    state = {
        "settings": autopilot.settings_json(settings),
        "queue": [autopilot.topic_json(t) for t in queue],
        "runs": [autopilot.topic_json(t) for t in runs],
    }
    db.session.commit()  # the settings row is created on first use
    return state


@bp.get("")
@require_user()
def get_autopilot():
    """L1. Settings, the queue and the recent runs."""
    return jsonify(_state())


class SettingsBody(Body):
    enabled: bool | None = None
    mode: Literal["approve", "auto"] | None = None
    run_hour_utc: Annotated[int, Field(ge=0, le=23)] | None = None
    rotation: dict[Literal["0", "1", "2", "3", "4", "5", "6"], Category] | None = None
    tone: Literal[TONES] | None = None  # type: ignore[valid-type]
    target_seconds: Literal[15, 30, 60, 90] | None = None
    visibility: Literal["private", "unlisted", "public"] | None = None


@bp.patch("")
@require_user(verified=True)
def update_autopilot():
    """L2. Turning it on needs a connected YouTube channel."""
    body = read_body(SettingsBody)
    user = current_user()
    settings = autopilot.settings_for(user.id, lock=True)
    if body.enabled:
        account = db.session.query(YoutubeAccount).filter_by(user_id=user.id).one_or_none()
        if account is None or account.status != "connected":
            raise ApiError(
                409, "not_connected", "Connect your YouTube channel before turning the autopilot on."
            )
    for field in ("enabled", "mode", "run_hour_utc", "tone", "target_seconds", "visibility"):
        value = getattr(body, field)
        if value is not None:
            setattr(settings, field, value)
    if body.rotation is not None:
        settings.rotation = {**autopilot.DEFAULT_ROTATION, **(settings.rotation or {}), **body.rotation}
    db.session.commit()
    return jsonify(_state())


@bp.post("/topics/suggest")
@require_user(verified=True)
def suggest_topics():
    """L3. The language model proposes two weeks of topics in the rotation (a job, free)."""
    user = current_user()
    settings = autopilot.settings_for(user.id)
    mix: dict[str, int] = {}
    for day in range(SUGGEST):
        category = (settings.rotation or autopilot.DEFAULT_ROTATION).get(str(day % 7), "football")
        mix[category] = mix.get(category, 0) + 1
    known = db.session.execute(
        select(AutopilotTopic.subject, AutopilotTopic.angle)
        .where(AutopilotTopic.user_id == user.id)
        .order_by(AutopilotTopic.created_at.desc())
        .limit(60)
    ).all()
    job = start_llm_job(
        "autopilot_topics", input={"mix": mix, "avoid": [f"{k.subject}: {k.angle}" for k in known]}
    )
    db.session.commit()
    answer = jsonify(job=jobs.job_json(job, False))
    jobs.dispatch([job])
    return answer, 202


class TopicBody(Body):
    category: Category
    subject: Annotated[str, _line(80, required=True)]
    angle: Annotated[str, _line(160, required=True)]


@bp.post("/topics")
@require_user(verified=True)
def add_topic():
    """L4. One topic, added by hand."""
    body = read_body(TopicBody)
    added = autopilot.add_topics(current_user().id, [body.model_dump()])
    if not added:
        raise ApiError(409, "not_added", "That topic is already in the queue, or the queue is full.")
    db.session.commit()
    return jsonify(_state()), 201


def _topic(topic_id: str, *, lock: bool = True) -> AutopilotTopic:
    try:
        wanted = uuid.UUID(topic_id)
    except (ValueError, TypeError):
        raise ApiError(404, "not_found", "We couldn't find that.") from None
    query = select(AutopilotTopic).where(
        AutopilotTopic.id == wanted, AutopilotTopic.user_id == current_user().id
    )
    topic = db.session.execute(query.with_for_update() if lock else query).scalar_one_or_none()
    if topic is None:
        raise ApiError(404, "not_found", "We couldn't find that.")
    return topic


@bp.delete("/topics/<topic_id>")
@require_user()
def delete_topic(topic_id: str):
    """L5. Removes a topic from the queue or a run from the list. For a run under way this is
    the stop: the autopilot takes it no further, and the Reel stays in the library as it is."""
    topic = _topic(topic_id)
    db.session.delete(topic)
    db.session.commit()
    return "", 204


@bp.post("/topics/<topic_id>/approve")
@require_user(verified=True)
def approve(topic_id: str):
    """L6. Publishes a Reel that is waiting. If the Reel was edited since its video was made,
    the autopilot renders it again and it waits here once more (`409 render_outdated`)."""
    seen = _topic(topic_id, lock=False)
    if seen.status != "awaiting_approval" or seen.project_id is None:
        raise ApiError(409, "not_waiting", "This Reel isn't waiting to be published.")
    # The project first, then the topic: the same order as the autopilot's own steps.
    project = projects.get_project(current_user().id, str(seen.project_id), lock=True)
    topic = _topic(topic_id)
    if topic.status != "awaiting_approval":
        raise ApiError(409, "not_waiting", "This Reel isn't waiting to be published.")
    if project.status == "posted":
        topic.status, topic.error = "published", None  # published by hand meanwhile
        db.session.commit()
        return jsonify(_state()), 200
    try:
        autopilot.publish(topic, project)
    except ApiError as e:
        if e.code != "render_outdated":
            raise
        db.session.rollback()
        topic = _topic(topic_id)
        topic.status, topic.error = "running", None
        db.session.commit()
        raise ApiError(
            409,
            "render_outdated",
            "You edited the Reel after this video was made. A new one is being made and will wait here.",
        ) from None
    return jsonify(_state()), 202


@bp.post("/topics/<topic_id>/skip")
@require_user()
def skip(topic_id: str):
    """L7. Doesn't publish it; the Reel stays in the library."""
    topic = _topic(topic_id)
    if topic.status != "awaiting_approval":
        raise ApiError(409, "not_waiting", "This Reel isn't waiting to be published.")
    topic.status, topic.error = "skipped", None
    db.session.commit()
    return jsonify(_state())


@bp.post("/run")
@require_user(verified=True)
def run_now():
    """L8. Starts the next topic now. One run at a time."""
    user = current_user()
    waiting = db.session.execute(
        select(AutopilotTopic.id)
        .where(AutopilotTopic.user_id == user.id, AutopilotTopic.status == "queued")
        .limit(1)
    ).first()
    if waiting is None:
        raise ApiError(409, "queue_empty", "The queue is empty. Add or suggest topics first.")
    topic = autopilot.start_next(user)
    if topic is None:
        raise ApiError(409, "not_started", "A Reel is already being made. Wait for it to finish.")
    if topic.status == "failed":
        raise ApiError(409, "not_started", topic.error or "It couldn't start.")
    return jsonify(_state()), 202
