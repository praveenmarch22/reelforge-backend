"""The daily autopilot (docs/AUTOPILOT.md): one Reel a day from a topic queue, made and
published without the creator starting each step.

Two scheduled tasks drive it. `start_due` creates today's project for each creator whose hour
has come; `advance_all` moves every running topic one step by looking at its project's status.
Each step calls the same functions the screens call, as the creator, so every rule (credits,
locks, limits) applies unchanged.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from flask import g
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from . import jobs, projects, publishing, reel, renders
from .errors import ApiError
from .extensions import db
from .models import AutopilotSettings, AutopilotTopic, Job, Post, Project, Render, User
from .script_rules import DEFAULT_SETTINGS
from .sessions import now

log = logging.getLogger(__name__)

CATEGORIES = ("football", "f1", "cricket")
CATEGORY_NAMES = {"football": "football", "f1": "Formula 1", "cricket": "cricket"}
# Monday = "0", in UTC. Football and F1 carry the week; cricket has one day (a global audience).
DEFAULT_ROTATION = {
    "0": "football", "1": "f1", "2": "football", "3": "cricket", "4": "football", "5": "f1", "6": "football",
}  # fmt: skip
ACTIVE = ("running", "publishing")
QUEUE_MAX = 60
RUNS_SHOWN = 30
# A run that hasn't finished by then is failed, so it can never block the days after it (QAP-003).
RUN_LIMIT = timedelta(hours=4)

# Given to the script model as rules of its own, outside the creator's quoted text (QAP-001).
SCRIPT_RULES = (
    "This video is about a real person. Use only widely documented facts; if unsure of a number or date, "
    "leave it out. No invented quotes, no private life, no rumours.",
    "visual_prompt and stock_query never name or describe a real person. Show places and objects instead: "
    "stadiums, crowds, trophies, cars, pitches, kit, a figure seen from behind or in silhouette.",
)
# Put in front of every AI image prompt of an autopilot Reel (QAP-001).
IMAGE_RULE = "no recognisable real person, no face, figures only from behind or in silhouette"


def is_autopilot(project_id) -> bool:
    if project_id is None:
        return False
    return (
        db.session.execute(
            select(AutopilotTopic.id).where(AutopilotTopic.project_id == project_id).limit(1)
        ).first()
        is not None
    )


def image_prompt_for(project_id, scene_prompt: str) -> tuple[str, str]:
    """For an autopilot Reel: the scene prompt without the legend's name, and the rule to put in
    front of the whole image prompt. For any other Reel: the prompt unchanged and no rule."""
    topic = db.session.execute(
        select(AutopilotTopic).where(AutopilotTopic.project_id == project_id).limit(1)
    ).scalar_one_or_none()
    if topic is None:
        return scene_prompt, ""
    # The whole name first, then each of its words, as whole words only (BUG-094).
    names = [topic.subject, *sorted(topic.subject.split(), key=len, reverse=True)]
    cleaned = scene_prompt
    for name in names:
        if len(name) > 2:
            cleaned = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", "the athlete", cleaned, flags=re.IGNORECASE)
    return cleaned, IMAGE_RULE


def settings_for(user_id, *, lock: bool = False) -> AutopilotSettings:
    """The creator's settings, created with the defaults on first use (safe when two requests
    arrive together: QAP-010)."""
    db.session.execute(
        insert(AutopilotSettings.__table__)
        .values(user_id=user_id, rotation=dict(DEFAULT_ROTATION))
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    query = select(AutopilotSettings).where(AutopilotSettings.user_id == user_id)
    return db.session.execute(query.with_for_update() if lock else query).scalar_one()


def topic_text(topic: AutopilotTopic) -> str:
    """The project's topic: the legend and the story (the rules travel separately)."""
    return f"{topic.subject} ({CATEGORY_NAMES.get(topic.category, topic.category)} legend): {topic.angle}"[
        :300
    ]


def _fail(topic_id, message: str) -> None:
    db.session.rollback()
    topic = db.session.get(AutopilotTopic, topic_id)
    if topic is not None and topic.status in ACTIVE + ("queued",):
        topic.status, topic.error = "failed", message[:300]
        topic.run_on = topic.run_on or now().date()  # so it shows at the top of the runs (QAP-004)
        db.session.commit()
    log.info("autopilot topic failed", extra={"topic_id": str(topic_id), "reason": message})


def _wait_for_creator(topic_id, message: str | None) -> None:
    """The Reel is made but isn't published; the creator decides (AP-3)."""
    db.session.rollback()
    topic = db.session.get(AutopilotTopic, topic_id)
    if topic is not None:
        topic.status, topic.error = "awaiting_approval", (message or "")[:300] or None
        db.session.commit()


# ---------------------------------------------------------------- starting the day's Reel


def next_topic(user_id, today: datetime) -> AutopilotTopic | None:
    """The first queued topic in today's category (UTC weekday), else the first queued one."""
    settings = settings_for(user_id)
    wanted = (settings.rotation or {}).get(str(today.weekday()))
    queued = list(
        db.session.execute(
            select(AutopilotTopic)
            .where(AutopilotTopic.user_id == user_id, AutopilotTopic.status == "queued")
            .order_by(AutopilotTopic.position, AutopilotTopic.created_at)
        ).scalars()
    )
    return next((t for t in queued if t.category == wanted), queued[0] if queued else None)


def start_next(
    user: User, *, today: datetime | None = None, scheduled: bool = False
) -> AutopilotTopic | None:
    """Creates the project for the creator's next topic and starts its script. One at a time.
    Returns the topic (`running`, or `failed` with its reason), or None when a run is under way
    or the queue is empty (AP-1). `scheduled`: the daily run, which counts as the day's run
    whatever happens, so a failure isn't repeated every hour; a run started by hand doesn't
    (QAP-011)."""
    today = today or datetime.now(UTC)
    g.user = user
    # The settings row stays locked until the project exists, so two starts at the same moment
    # can't take the same topic (BUG-092). Nothing is saved before that either: a fault here
    # leaves the day open for the next hourly check (BUG-093).
    settings = settings_for(user.id, lock=True)
    if scheduled and settings.last_run_on is not None and settings.last_run_on >= today.date():
        db.session.rollback()  # another worker started it meanwhile
        return None
    busy = db.session.execute(
        select(AutopilotTopic.id).where(AutopilotTopic.user_id == user.id, AutopilotTopic.status == "running")
    ).first()
    topic = None if busy else next_topic(user.id, today)
    if topic is None:
        if scheduled and not busy:
            settings.last_run_on = today.date()  # an empty queue: the day is skipped (AP-1)
        db.session.commit()
        return None
    topic_id = topic.id
    try:
        from .routes.projects import script_input, start_llm_job

        project = Project(
            user_id=user.id,
            topic=topic_text(topic),
            tone=settings.tone,
            mode="faceless",
            target_seconds=settings.target_seconds,
            style="cinematic",
            character="",
            voice_id=user.default_voice_id,
            settings={**DEFAULT_SETTINGS, "captionStyle": user.default_caption_style},
            status="scripting",
        )
        db.session.add(project)
        db.session.flush()
        job = start_llm_job(
            "script",
            project=project,
            input=script_input(project, first=True, previousStatus="draft", rules=list(SCRIPT_RULES)),
        )
        topic.status, topic.project_id, topic.run_on, topic.error = "running", project.id, today.date(), None
        if scheduled:
            settings.last_run_on = today.date()
        db.session.commit()
        jobs.dispatch([job])
    except ApiError as e:
        _fail(topic_id, e.message)
        if scheduled:
            # A refusal (no credits, say) counts as the day's run, so it isn't repeated hourly.
            settings_for(user.id, lock=True).last_run_on = today.date()
            db.session.commit()
    return db.session.get(AutopilotTopic, topic_id)


def start_due(at: datetime | None = None) -> int:
    """Hourly: starts today's Reel for every creator whose hour has come (AP-6: or passed)."""
    at = at or datetime.now(UTC)
    due = db.session.execute(
        select(AutopilotSettings.user_id).where(
            AutopilotSettings.enabled.is_(True),
            AutopilotSettings.run_hour_utc <= at.hour,
            (AutopilotSettings.last_run_on.is_(None)) | (AutopilotSettings.last_run_on < at.date()),
        )
    ).scalars()
    started = 0
    for user_id in list(due):
        user = db.session.get(User, user_id)
        if user is None or user.deleted_at is not None or user.disabled_at is not None:
            continue
        try:
            topic = start_next(user, today=at, scheduled=True)
            started += 1 if topic is not None and topic.status == "running" else 0
        except Exception:  # noqa: BLE001 - one creator's trouble never stops the others
            db.session.rollback()
            log.exception("autopilot could not start", extra={"user_id": str(user_id)})
    return started


# ---------------------------------------------------------------- moving a run forward


def _latest_job(project_id, job_type: str) -> Job | None:
    return db.session.execute(
        select(Job)
        .where(Job.project_id == project_id, Job.type == job_type)
        .order_by(Job.queued_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _video(project_id) -> Render | None:
    return db.session.execute(
        select(Render)
        .where(Render.project_id == project_id, Render.state == "succeeded")
        .order_by(Render.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def publish(topic: AutopilotTopic, project: Project) -> None:
    """Publishes the topic's finished Reel to YouTube (the project is locked). Commits and
    dispatches. Raises ApiError (`render_outdated` when the Reel was edited after its video was
    made: the edited Reel is never published as the old video, QAP-002)."""
    from .project_schemas import PublishBody

    render = _video(project.id)
    if render is None:
        raise ApiError(409, "not_ready", "The Reel has no finished video.")
    if render.timeline_version < project.timeline_version or project.status not in (
        "rendered",
        "post_failed",
    ):
        raise ApiError(409, "render_outdated", "The Reel was edited after its video was made.")
    settings = settings_for(topic.user_id)
    try:
        body = PublishBody.model_validate(
            {
                "platform": "youtube",
                "title": (project.post_title or project.title)[:100].replace("<", "").replace(">", "").strip()
                or topic.subject,
                "caption": project.post_caption.replace("<", "").replace(">", ""),
                "hashtags": [h for h in project.hashtags if "<" not in h and ">" not in h],
                "visibility": settings.visibility,
            }
        )
    except ValidationError:
        raise ApiError(
            409, "post_text_invalid", "The title or description can't be published as it is."
        ) from None
    _, job = publishing.start_publish(render, project, body)
    topic.status, topic.error = "publishing", None
    db.session.commit()
    jobs.dispatch([job])


def advance(topic_id) -> None:
    """Moves one topic's run one step, from its project's status (AP-7)."""
    seen = db.session.get(AutopilotTopic, topic_id)
    if seen is None or seen.status not in ACTIVE:
        db.session.rollback()
        return
    user = db.session.get(User, seen.user_id)
    if user is None or user.deleted_at is not None or user.disabled_at is not None or seen.project_id is None:
        return _fail(topic_id, "The account or the Reel is gone.")
    g.user = user
    try:
        project = projects.get_project(user.id, str(seen.project_id), lock=True)
    except ApiError:
        return _fail(topic_id, "The Reel was deleted.")
    # The project first, then the topic: the same order everywhere (QAP-013).
    topic = db.session.execute(
        select(AutopilotTopic).where(AutopilotTopic.id == topic_id).with_for_update()
    ).scalar_one_or_none()
    if topic is None or topic.status not in ACTIVE:
        db.session.rollback()
        return
    status = project.status
    if now() - topic.updated_at > RUN_LIMIT:
        return _fail(topic_id, "This run took too long and was stopped. Open the Reel to carry on by hand.")

    if topic.status == "publishing":
        if status == "posted":
            topic.status, topic.error = "published", None
            db.session.commit()
        elif status != "posting":
            post = db.session.execute(
                select(Post).where(Post.project_id == project.id).order_by(Post.created_at.desc()).limit(1)
            ).scalar_one_or_none()
            _wait_for_creator(topic_id, (post.error_message if post else None) or "Publishing didn't finish.")
        else:
            db.session.rollback()
        return

    if status in ("scripting", "generating", "rendering", "posting"):
        db.session.rollback()  # still working
        return
    if status == "draft" and not project.scenes:
        job = _latest_job(project.id, "script")
        return _fail(topic_id, (job.error_message if job else None) or "The script couldn't be written.")
    if status == "needs_attention":
        return _fail(topic_id, "Some scenes couldn't be made. Open the Reel to retry them.")
    if status == "render_failed":
        return _fail(topic_id, "The video couldn't be rendered. Open the Reel to try again.")
    if status == "posted":
        topic.status = "published"
        db.session.commit()
        return

    try:
        if status == "draft":
            new_jobs, _ = reel.generate(project, None)
            if not new_jobs:
                # Nothing can be started (a scene waits for an upload, say): it needs the creator.
                return _fail(topic_id, "Some scenes need you. Open the Reel to finish them.")
            projects.after_edit(project)
            db.session.commit()
            jobs.dispatch(new_jobs)
        elif status == "ready":
            _, job = renders.start_render(project)
            db.session.commit()
            jobs.dispatch([job])
        elif status == "post_failed":
            post = publishing.latest_post(project.id)
            _wait_for_creator(topic_id, (post.error_message if post else None) or "Publishing didn't finish.")
        elif status == "rendered":
            caption = _latest_job(project.id, "caption")
            if not project.post_title and caption is None:
                _start_caption(topic, project)
            elif caption is not None and caption.state in ("queued", "running"):
                db.session.rollback()
            elif settings_for(user.id).mode == "auto":
                publish(topic, project)
            else:
                topic.status, topic.error = "awaiting_approval", None
                db.session.commit()
    except ApiError as e:
        if e.code == "already_running":
            db.session.rollback()  # another of the creator's Reels is rendering: next round
        elif status == "rendered":
            # The Reel is made: whatever stops the publish, it waits for the creator (AP-3).
            _wait_for_creator(topic_id, e.message)
        else:
            _fail(topic_id, e.message)


def _start_caption(topic: AutopilotTopic, project: Project) -> None:
    """The title and description for a finished Reel. If the language model can't be asked now
    (the hourly limit, no credits), the Reel's own title is used: a made Reel is never failed
    for its post text (QAP-007)."""
    from .routes.projects import start_llm_job

    topic_id, project_id = topic.id, project.id
    script = " ".join(s.narration for s in project.scenes)[:4000]
    try:
        job = start_llm_job(
            "caption",
            project=project,
            input={"title": project.title, "topic": f"{topic.subject}: {topic.angle}", "script": script},
        )
        db.session.commit()
        jobs.dispatch([job])
    except ApiError:
        db.session.rollback()
        project = db.session.get(Project, project_id)
        project.post_title = project.title[:100]
        db.session.commit()
        log.info("autopilot: post text skipped", extra={"topic_id": str(topic_id)})


def advance_all() -> int:
    """Every 2 minutes: one step for every run under way."""
    ids = list(
        db.session.execute(select(AutopilotTopic.id).where(AutopilotTopic.status.in_(ACTIVE))).scalars()
    )
    db.session.commit()
    for topic_id in ids:
        try:
            advance(topic_id)
        except Exception:  # noqa: BLE001 - one run's trouble never stops the others
            db.session.rollback()
            log.exception("autopilot could not advance", extra={"topic_id": str(topic_id)})
    return len(ids)


# ---------------------------------------------------------------- the queue


def add_topics(user_id, items: list[dict]) -> list[AutopilotTopic]:
    """Appends topics to the creator's queue (up to QUEUE_MAX queued ones). Caller commits."""
    queued = db.session.execute(
        select(AutopilotTopic.position, AutopilotTopic.subject, AutopilotTopic.angle).where(
            AutopilotTopic.user_id == user_id, AutopilotTopic.status == "queued"
        )
    ).all()
    known = {(q.subject.lower(), q.angle.lower()) for q in queued}
    position = max([q.position for q in queued], default=0)
    added = []
    for item in items:
        if len(queued) + len(added) >= QUEUE_MAX:
            break
        key = (item["subject"].lower(), item["angle"].lower())
        if key in known:
            continue
        known.add(key)
        position += 1
        topic = AutopilotTopic(
            id=uuid.uuid4(),
            user_id=user_id,
            position=position,
            category=item["category"],
            subject=item["subject"],
            angle=item["angle"],
        )
        db.session.add(topic)
        added.append(topic)
    return added


def topic_json(topic: AutopilotTopic) -> dict:
    out = {
        "id": str(topic.id),
        "category": topic.category,
        "subject": topic.subject,
        "angle": topic.angle,
        "status": topic.status,
        "projectId": str(topic.project_id) if topic.project_id else None,
        "error": topic.error,
        "runOn": topic.run_on.isoformat() if topic.run_on else None,
        "video": None,
        "url": None,
        "title": None,
    }
    if topic.project_id and topic.status in ("awaiting_approval", "publishing", "published", "skipped"):
        project = db.session.get(Project, topic.project_id)
        if project is not None:
            out["title"] = project.post_title or project.title
            render = _video(project.id) if topic.status == "awaiting_approval" else None
            if render is not None:
                out["video"] = renders.render_json(render, project)["video"]
            if topic.status == "published":
                post = db.session.execute(
                    select(Post)
                    .where(Post.project_id == project.id, Post.state == "published")
                    .order_by(Post.created_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
                out["url"] = post.permalink if post else None
    return out


def settings_json(settings: AutopilotSettings) -> dict:
    return {
        "enabled": settings.enabled,
        "mode": settings.mode,
        "runHourUtc": settings.run_hour_utc,
        "rotation": {str(d): (settings.rotation or {}).get(str(d)) for d in range(7)},
        "tone": settings.tone,
        "targetSeconds": settings.target_seconds,
        "visibility": settings.visibility,
        "lastRunOn": settings.last_run_on.isoformat() if settings.last_run_on else None,
    }
