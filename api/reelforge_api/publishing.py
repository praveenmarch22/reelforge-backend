"""Publishing a render (milestone B6; API F5 and F6; docs/PUBLISHING_YOUTUBE.md).

F5 creates a post and a `publish` job. The job uploads the MP4 to YouTube with a resumable
upload, saving where it got to (YT-8) and the video id as soon as YouTube gives it (YT-9),
then waits for YouTube's processing (YT-10). The project is `posting` meanwhile, then
`posted`, or `post_failed` with the reason. Publishing is free.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from flask import current_app
from sqlalchemy import select, update

from . import connections, jobs
from .auth import current_user
from .errors import ApiError
from .extensions import db
from .models import Asset, Job, Post, Project, Render, YoutubeAccount
from .providers.publish import CHUNK, PublishError, pause, youtube
from .sessions import now
from .storage import get_storage

log = logging.getLogger(__name__)

ACTIVE = ("queued", "uploading", "processing")
PROCESSING_POLLS = 60  # every 10 s: up to 10 minutes for YouTube's processing (YT-10)
UPLOAD_TRIES = 3  # a dropped connection continues where it stopped (YT-8)
DESCRIPTION_BYTES = 5000
TAGS_CHARS = 500
SHORTS_URL = "https://youtube.com/shorts/{}"

MESSAGES = {
    "not_connected": "Connect your YouTube channel first.",
    "reconnect_required": "Your YouTube connection has expired. Connect YouTube again, then try again.",
    "quota_exceeded": "YouTube's daily upload limit is reached. Try again tomorrow.",
    "rejected": "YouTube couldn't process this video. Render it again and try once more.",
    "upload_failed": "The upload to YouTube didn't go through. Please try again.",
    "invalid_metadata": (
        "YouTube didn't accept the title, description or hashtags. Change them and try again."
    ),
}


# ---------------------------------------------------------------- F5


def description(caption: str, hashtags: list[str], credits: list[str] | None = None) -> str:
    """Caption, hashtags, then the credit lines the licences require (QB6-001)."""
    parts = [caption.strip(), " ".join(f"#{h}" for h in hashtags), "\n".join(credits or [])]
    return "\n\n".join(p for p in parts if p)


def credit_lines(render: Render) -> list[str]:
    """What this render's music and voices require to be credited: the track's CC BY line, and
    "Voice: elevenlabs.io" when any voice came from ElevenLabs (decided 2026-11-25)."""
    lines = []
    music = (render.timeline or {}).get("music") or {}
    if music.get("license"):
        lines.append("Music: " + music["license"])
    voice_keys = [
        s["voice"]["src"] for s in (render.timeline or {}).get("scenes", []) if s.get("voice", {}).get("src")
    ]
    if voice_keys:
        used = db.session.execute(
            select(Asset.provider).where(Asset.s3_key.in_(voice_keys)).distinct()
        ).scalars()
        if "elevenlabs" in set(used):
            lines.append("Voice: elevenlabs.io")
    return lines


def youtube_tags(hashtags: list[str]) -> list[str]:
    """YouTube allows about 500 characters of tags in all; the rest are left out."""
    kept, used = [], 0
    for tag in hashtags:
        if used + len(tag) + 1 > TAGS_CHARS:
            break
        kept.append(tag)
        used += len(tag) + 1
    return kept


def start_publish(render: Render, project: Project, body) -> tuple[Post, Job]:
    """F5. The project must be locked. Caller commits, then dispatches."""
    user = current_user()
    connections.check_platform(body.platform)
    if not connections.platform_enabled(body.platform):
        raise ApiError(
            503, "provider_unavailable", "Publishing to YouTube is paused for now. Try again later."
        )
    if render.state != "succeeded" or render.asset_id is None:
        raise ApiError(409, "not_ready", "This render isn't finished yet.")
    if render.timeline_version < project.timeline_version and not body.confirm_outdated:
        raise ApiError(
            409,
            "render_outdated",
            "The Reel was edited after this render. Render it again, or publish this one.",
        )
    credits = credit_lines(render)
    if len(description(body.caption, body.hashtags, credits).encode("utf-8")) > DESCRIPTION_BYTES:
        raise ApiError(
            400, "validation_error", "Some details need another look.",
            {"fields": {"caption": "The caption and hashtags are too long for YouTube"}},
        )  # fmt: skip
    account = db.session.query(YoutubeAccount).filter_by(user_id=user.id).one_or_none()
    if account is None:
        raise ApiError(409, "not_connected", MESSAGES["not_connected"])
    if account.status != "connected":
        raise ApiError(409, "reconnect_required", MESSAGES["reconnect_required"])
    existing = db.session.execute(
        select(Post).where(
            Post.render_id == render.id, Post.platform == body.platform, Post.state != "failed"
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.state == "published":
            raise ApiError(409, "already_posted", "This video is already on YouTube.")
        raise ApiError(409, "already_running", "This is already being published.")
    if project.status in ("rendering", "posting"):
        raise ApiError(409, "already_running", "This Reel is busy. Try again when it's done.")
    # "Try again" after an interrupted publish continues the earlier upload, or only checks the
    # video YouTube already has, so a retry never leaves a second copy on the channel (QB6-002).
    # Not after YouTube refused the video (it would only be refused again).
    earlier = db.session.execute(
        select(Post)
        .where(
            Post.render_id == render.id,
            Post.platform == body.platform,
            Post.state == "failed",
            Post.youtube_account_id == account.id,
            (Post.media_id.is_not(None)) | (Post.container_id.is_not(None)),
            Post.error_code != "rejected",
        )
        .order_by(Post.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    # Only on the same channel: connecting another channel keeps the same account row (BUG-074).
    if earlier is not None:
        earlier_job = db.session.get(Job, earlier.job_id) if earlier.job_id else None
        if earlier_job is None or earlier_job.input.get("channelId") != account.channel_id:
            earlier = None

    post = Post(
        id=uuid.uuid4(),
        project_id=project.id,
        render_id=render.id,
        platform=body.platform,
        youtube_account_id=account.id,
        title=body.title,
        caption=body.caption,
        visibility=body.visibility,
        container_id=earlier.container_id if earlier else None,
        media_id=earlier.media_id if earlier else None,
    )
    db.session.add(post)
    db.session.flush()
    job = jobs.new_job(
        user.id,
        "publish",
        project_id=project.id,
        input={
            "postId": str(post.id),
            "previousStatus": project.status,
            "hashtags": body.hashtags,
            "credits": credits,
            "continues": str(earlier.id) if earlier else None,
            "channelId": account.channel_id,
        },
    )
    post.job_id = job.id
    # The screen remembers what was written, for a retry or another platform.
    project.post_title, project.post_caption, project.hashtags = body.title, body.caption, body.hashtags
    project.status = "posting"
    return post, job


# ---------------------------------------------------------------- the job type


def _set(post_id, **values) -> None:
    """Saves the post's progress for the app to show, without locks."""
    db.session.execute(update(Post).where(Post.id == post_id, Post.state.in_(ACTIVE)).values(**values))
    db.session.commit()


def _cancelled(job_id) -> bool:
    state = db.session.execute(select(Job.state).where(Job.id == job_id)).scalar_one_or_none()
    db.session.commit()
    return state != "running"


def _upload(client, token: str, post: dict, key: str, size: int, job_id) -> str | None:
    """Sends the MP4 in pieces, continuing after a dropped connection. The video id, or None if
    the job was cancelled meanwhile."""
    storage = get_storage()
    session_url = post["sessionUrl"]
    if session_url is None:
        session_url = client.start_upload(token, post["metadata"], size)
        _set(post["id"], container_id=session_url, state="uploading", progress=10)
    sent, video_id = client.uploaded_so_far(session_url, size) if post["sessionUrl"] else (0, None)
    tries = 0
    while video_id is None:
        if _cancelled(job_id):
            return None
        end = min(sent + CHUNK, size) - 1
        piece = storage.client.get_object(Bucket=storage.bucket, Key=key, Range=f"bytes={sent}-{end}")[
            "Body"
        ].read()
        try:
            sent, video_id = client.upload_chunk(session_url, piece, sent, size)
            tries = 0
        except PublishError as e:
            if not e.retryable:
                raise
            tries += 1
            if tries >= UPLOAD_TRIES:
                raise
            pause(current_app.extensions["settings"], 2**tries)
            sent, video_id = client.uploaded_so_far(session_url, size)
        except SoftTimeLimitExceeded:
            raise  # out of time: never mistaken for a dropped connection (QB6-004)
        except Exception:  # noqa: BLE001 - a dropped connection: ask YouTube how far it got
            tries += 1
            if tries >= UPLOAD_TRIES:
                raise PublishError("upload_failed") from None
            pause(current_app.extensions["settings"], 2**tries)
            sent, video_id = client.uploaded_so_far(session_url, size)
        _set(post["id"], progress=10 + round(70 * sent / max(size, 1)))
    return video_id


class PublishJob:
    @staticmethod
    def on_start(job: Job) -> None:
        pass

    @staticmethod
    def work(job: Job):
        settings = current_app.extensions["settings"]
        post_row = db.session.get(Post, uuid.UUID(job.input["postId"]))
        render = db.session.get(Render, post_row.render_id) if post_row else None
        asset = db.session.get(Asset, render.asset_id) if render and render.asset_id else None
        if post_row is None or asset is None:
            raise jobs.JobFailed("internal_error")
        hashtags = job.input.get("hashtags", [])
        post = {
            "id": post_row.id,
            "sessionUrl": post_row.container_id,
            "videoId": post_row.media_id,
            "accountId": post_row.youtube_account_id,
            "visibility": post_row.visibility,
            "metadata": {
                "snippet": {
                    "title": post_row.title,
                    "description": description(post_row.caption, hashtags, job.input.get("credits")),
                    "tags": youtube_tags(hashtags),
                    "categoryId": "22",  # People & Blogs
                },
                "status": {
                    "privacyStatus": post_row.visibility,
                    "selfDeclaredMadeForKids": False,
                },
            },
        }
        key, size = asset.s3_key, int(asset.size_bytes or get_storage().size(asset.s3_key))
        db.session.commit()  # no transaction stays open during the upload

        client = youtube(settings)
        # Switched off since the request (AD2): nothing new is uploaded (BUG-087). A video that
        # is already on YouTube is still checked to its end.
        if post["videoId"] is None and not connections.platform_enabled("youtube"):
            raise jobs.JobFailed(
                "provider_unavailable", "Publishing to YouTube is paused for now. Try again later."
            )
        db.session.commit()
        try:
            token = connections.access_token(post["accountId"])
            video_id = post["videoId"]
            if video_id is None:
                video_id = _upload(client, token, post, key, size, job.id)
                if video_id is None:
                    return {"cancelled": True}, "youtube"
                _set(post["id"], media_id=video_id, state="processing", progress=80)  # YT-9
            # YT-10: wait for YouTube's processing.
            privacy = post["visibility"]
            for poll in range(PROCESSING_POLLS):
                video = client.video(connections.access_token(post["accountId"]), video_id)
                if video is None:
                    # YouTube no longer has it (deleted, or never kept): not published (BUG-075).
                    raise jobs.JobFailed("rejected", MESSAGES["rejected"])
                status = video.get("status", {})
                upload_status = status.get("uploadStatus")
                privacy = status.get("privacyStatus", privacy)
                if upload_status in ("failed", "rejected", "deleted"):
                    reason = status.get("rejectionReason") or status.get("failureReason") or upload_status
                    log.warning("YouTube refused a video", extra={"job_id": str(job.id), "reason": reason})
                    raise jobs.JobFailed("rejected", MESSAGES["rejected"])
                if upload_status == "processed":
                    break
                if _cancelled(job.id):
                    return {"cancelled": True}, "youtube"
                _set(post["id"], progress=min(98, 80 + poll))
                pause(settings, 10)
            # Still processing after 10 minutes: it is on YouTube and will finish there.
            return {"videoId": video_id, "privacy": privacy}, "youtube"
        except PublishError as e:
            if e.code == "reconnect_required":
                connections.mark_reconnect(post["accountId"])
            raise jobs.JobFailed(e.code, MESSAGES.get(e.code)) from None

    @staticmethod
    def apply(job: Job, made: dict) -> dict:
        post = db.session.execute(
            select(Post).where(Post.id == uuid.UUID(job.input["postId"])).with_for_update()
        ).scalar_one_or_none()
        if post is None or made.get("cancelled"):
            raise jobs.JobFailed("internal_error")
        post.media_id = made["videoId"]
        post.permalink = SHORTS_URL.format(made["videoId"])
        post.state, post.progress, post.published_at = "published", 100, now()
        project = db.session.get(Project, job.project_id)
        if project is not None and project.status == "posting":
            project.status = "posted"
        return {
            "postId": str(post.id),
            "url": post.permalink,
            # YT-1: YouTube may keep uploads from an unaudited app private; read back, not assumed.
            "privateUntilVerified": post.visibility != "private" and made.get("privacy") == "private",
        }

    @staticmethod
    def on_stop(job: Job) -> None:
        post = db.session.execute(
            select(Post).where(Post.id == uuid.UUID(job.input["postId"])).with_for_update()
        ).scalar_one_or_none()
        live = post is not None and post.media_id is not None and job.state == "cancelled"
        if post is not None and post.state in ACTIVE:
            if live:
                # Stopped after the upload: the Short is on YouTube already (QB6-011).
                post.state, post.progress, post.published_at = "published", 100, now()
                post.permalink = SHORTS_URL.format(post.media_id)
            else:
                post.state = "failed"
                post.error_code = job.error_code or "cancelled"
                post.error_message = job.error_message if job.state == "failed" else "Publishing was stopped."
        project = db.session.get(Project, job.project_id)
        if project is None or project.status != "posting":
            return
        if live:
            project.status = "posted"
        elif job.state == "failed":
            project.status = "post_failed"
        else:
            previous = job.input.get("previousStatus")
            busy = ("posting", "rendering", "scripting", "generating")
            project.status = previous if previous and previous not in busy else "rendered"


# ---------------------------------------------------------------- JSON


def _iso(value) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def post_json(post: Post) -> dict[str, Any]:
    output = None
    if post.state == "published" and post.job_id:
        job = db.session.get(Job, post.job_id)
        output = job.output if job is not None else None
    return {
        "id": str(post.id),
        "renderId": str(post.render_id),
        "projectId": str(post.project_id),
        "platform": post.platform,
        "state": post.state,
        "progress": post.progress,
        "title": post.title,
        "caption": post.caption,
        "visibility": post.visibility,
        "credits": (db.session.get(Job, post.job_id).input.get("credits", []) if post.job_id else []),
        "url": post.permalink,
        "privateUntilVerified": bool(output and output.get("privateUntilVerified")),
        "error": {"code": post.error_code or "internal_error", "message": post.error_message}
        if post.state == "failed"
        else None,
        "createdAt": _iso(post.created_at),
        "publishedAt": _iso(post.published_at),
    }


def latest_post(project_id) -> Post | None:
    return db.session.execute(
        select(Post).where(Post.project_id == project_id).order_by(Post.created_at.desc()).limit(1)
    ).scalar_one_or_none()


def get_post(user_id, post_id: str) -> Post:
    try:
        wanted = uuid.UUID(post_id)
    except (ValueError, TypeError):
        raise ApiError(404, "not_found", "We couldn't find that.") from None
    found = db.session.execute(
        select(Post)
        .join(Project, Project.id == Post.project_id)
        .where(Post.id == wanted, Project.user_id == user_id, Project.deleted_at.is_(None))
    ).scalar_one_or_none()
    if found is None:
        raise ApiError(404, "not_found", "We couldn't find that.")
    return found
