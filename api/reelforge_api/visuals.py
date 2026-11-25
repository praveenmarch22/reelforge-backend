"""Visual jobs (milestone B4, slice 2): one scene's stock clip or AI image.

- **stock**: search the stock providers with the scene's stock query, pick a clip long enough
  for the scene, download it to our storage. Nothing fits → a simpler query → an AI image (G-6).
- **ai_image**: a FLUX image from the scene's visual prompt, mood and the project's style.
- **ai_video**: until AI video arrives (milestone B7), the same as ai_image; the renderer gives
  every still a slow zoom or pan (decided 2026-11-25).
- A clip chosen by hand from stock search (C10) is downloaded the same way.

The scene's `visualStatus` follows the job like the voice does: queued, running, then ready or
failed. Uploads (C10 with an asset) need no job.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

import requests
from flask import current_app
from PIL import Image
from sqlalchemy import select, update

from . import credits, jobs
from .auth import current_user
from .errors import ApiError
from .extensions import db
from .models import Asset, Job, Project, ProviderSwitch, Scene
from .projects import after_edit, release_assets
from .providers.base import (
    USER_AGENT,
    AllProvidersFailed,
    NotConfigured,
    ProviderError,
    with_fallback,
    with_retry,
)
from .providers.image import PROVIDERS as IMAGE_PROVIDERS
from .providers.image import image_prompt
from .providers.stock import PROVIDERS as STOCK_PROVIDERS
from .providers.stock import StockClip, download
from .script_rules import estimated_seconds, visual_credits
from .storage import get_storage

log = logging.getLogger(__name__)

VISUAL_CHANGED = "The scene was edited while its visual was being made, so that visual was not used."
STOCK_GONE = "That clip is no longer available. Pick another one."
SEARCH_SECONDS = 24 * 3600  # Pixabay asks for search results to be kept for a day
SEEN_MAX = 30
STOCK_PAGES = 3  # result pages regenerate looks through before falling back
# Free AI images used when no stock clip fits, per creator per day (QB4S2-004): the Workers AI
# allowance is shared by everyone.
FREE_FALLBACKS_PER_DAY = 20
# Stock files are only fetched from the provider's own servers, over https (QB4S2-005).
DOWNLOAD_HOSTS = {"pixabay": ("cdn.pixabay.com", "pixabay.com")}
THUMB_MAX = 2 * 1024 * 1024
VISUAL_FIELDS = ("visual_source", "visual_prompt", "stock_query")


# ---------------------------------------------------------------- providers


def _enabled(configured: list[str], known: dict) -> list[str]:
    off = set(
        db.session.execute(
            select(ProviderSwitch.provider).where(
                ProviderSwitch.provider.in_(configured), ProviderSwitch.enabled.is_(False)
            )
        ).scalars()
    )
    return [n for n in configured if n in known and n not in off]


def stock_names() -> list[str]:
    return _enabled(current_app.extensions["settings"].stock_provider_names, STOCK_PROVIDERS)


def image_names() -> list[str]:
    return _enabled(current_app.extensions["settings"].image_provider_names, IMAGE_PROVIDERS)


def _has_key(name: str) -> bool:
    s = current_app.extensions["settings"]
    return {
        "pixabay": bool(s.pixabay_api_key),
        "cloudflare": bool(s.cloudflare_account_id and s.cloudflare_api_token),
        "mock": True,
    }.get(name, False)


def stock_available() -> bool:
    return any(_has_key(n) for n in stock_names())


def image_available() -> bool:
    return any(_has_key(n) for n in image_names())


def visual_available(source: str) -> bool:
    # Stock falls back to an AI image, so either is enough for it.
    return (stock_available() or image_available()) if source == "stock" else image_available()


def _pause_for(settings):
    def pause(seconds: float) -> None:
        if settings.env != "test":
            import time

            time.sleep(seconds)

    return pause


# ---------------------------------------------------------------- stock search (C9 and the job)


def stock_search(query: str, page: int = 1) -> tuple[list[StockClip], bool]:
    """Results from the first stock provider that answers, kept in Redis for 24 hours."""
    settings = current_app.extensions["settings"]
    names = [n for n in stock_names() if _has_key(n)]
    if not names:
        raise AllProvidersFailed("stock: no stock provider is switched on", none_available=True)
    query = " ".join(query.split())[:100]
    slot = "stock:" + hashlib.sha256(json.dumps([names, query.lower(), page]).encode()).hexdigest()
    redis = current_app.extensions["redis"]
    try:
        cached = redis.get(slot)
        if cached is not None:
            data = json.loads(cached)
            return [StockClip(**c) for c in data["clips"]], data["more"]
    except Exception:  # noqa: BLE001 - searching still works without the cache
        log.warning("could not read cached stock results", exc_info=True)
    providers = [STOCK_PROVIDERS[n](settings) for n in names]
    (clips, more), _ = with_fallback(
        providers, lambda p: p.search(query, page), label="stock search", sleep=_pause_for(settings)
    )
    try:
        payload = {"clips": [dataclasses.asdict(c) for c in clips], "more": more}
        redis.set(slot, json.dumps(payload), ex=SEARCH_SECONDS)
    except Exception:  # noqa: BLE001
        log.warning("could not cache stock results", exc_info=True)
    return clips, more


def pick_clip(clips: list[StockClip], seconds: float, exclude: set[str]) -> StockClip | None:
    """A clip long enough for the scene if there is one (a shorter one is slowed, then held:
    pipeline strategy 3), sharp enough for the vertical frame, and not the one being replaced."""
    usable = [c for c in clips if c.provider_id not in exclude]
    if not usable:
        return None
    return min(
        usable, key=lambda c: (c.duration < seconds, c.download_height < 1080, -min(c.duration, seconds))
    )


def _simpler(query: str) -> str:
    words = query.split()
    return " ".join(words[:2]) if len(words) > 2 else ""


# ---------------------------------------------------------------- starting


def scene_seconds(scene: Scene) -> float:
    """How long the scene lasts: its voice if made, else the estimate from the narration."""
    if scene.voice_status == "ready" and scene.voice_asset and scene.voice_asset.duration_seconds:
        return float(scene.voice_asset.duration_seconds)
    return max(1.0, estimated_seconds(scene.narration))


def start_visual_job(project: Project, scene: Scene, *, stock_id: str | None = None) -> Job:
    """Creates a visual job for one scene and reserves its credits (C8, C10). The project
    must be locked. Caller commits, then dispatches.

    Checked in this order: a provider is available (503), the scene's visual is not already
    being made (409), there are enough credits (402).
    """
    user = current_user()
    source = "stock" if stock_id else scene.visual_source
    if not visual_available(source):
        raise ApiError(503, "provider_unavailable", jobs.MESSAGES["provider_unavailable"])
    meta = (scene.visual_asset.meta or {}) if scene.visual_asset else {}
    # Clips this scene has already had, newest last, so "regenerate" moves on (QB4S2-001).
    seen = [c for c in [*meta.get("seen", []), meta.get("providerId")] if c][-SEEN_MAX:]
    account = credits.lock_account(user.id)
    job = jobs.new_job(
        user.id,
        "visual",
        project_id=project.id,
        scene_id=scene.id,
        input={
            "source": source,
            "stockId": stock_id,
            "visualPrompt": scene.visual_prompt,
            "stockQuery": scene.stock_query,
            "narration": scene.narration,
            "mood": scene.mood,
            "style": project.style,
            "character": project.character,
            "seconds": scene_seconds(scene),
            # "Regenerate" always gives something new: the next stock clip, or a new seed.
            "exclude": [] if stock_id else seen,
            "seen": seen,
            "seed": secrets.randbelow(2**31),
            "previousStatus": scene.visual_status,
        },
        cost=0 if stock_id else visual_credits(source),
    )
    credits.reserve(account, [job])
    scene.visual_status = "queued"
    return job


# ---------------------------------------------------------------- the job type


def _lock_scene(scene_id) -> Scene | None:
    if scene_id is None:
        return None
    return db.session.execute(
        select(Scene).where(Scene.id == scene_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()


def allowed_download(clip: StockClip, url: str) -> bool:
    """Only https links on the clip's own provider's servers (no redirects are followed)."""
    hosts = DOWNLOAD_HOSTS.get(clip.provider_id.split(":", 1)[0], ())
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in hosts and parsed.port in (None, 443)


def _fetch_thumbnail(clip: StockClip) -> bytes | None:
    """The clip's still, if the provider has one: a JPEG of at most 2 MB, read in pieces."""
    if not allowed_download(clip, clip.thumbnail_url):
        return None
    data = bytearray()
    try:
        with requests.get(
            clip.thumbnail_url,
            stream=True,
            timeout=10,
            allow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        ) as resp:
            if resp.status_code != 200:
                return None
            for chunk in resp.iter_content(64 * 1024):
                data += chunk
                if len(data) > THUMB_MAX:
                    return None
    except requests.RequestException:
        log.info("no thumbnail for a stock clip", extra={"clip": clip.provider_id})
        return None
    return bytes(data) if data[:3] == b"\xff\xd8\xff" else None


def _thumbnail_of(image: bytes) -> bytes:
    """A small JPEG of an AI image for lists (QB4S2-009)."""
    with Image.open(io.BytesIO(image)) as img:
        small = img.convert("RGB")
        small.thumbnail((360, 640))
        buf = io.BytesIO()
        small.save(buf, "JPEG", quality=80)
        return buf.getvalue()


def _store_clip(job: Job, clip: StockClip) -> dict:
    storage = get_storage()
    base = f"users/{job.user_id}/projects/{job.project_id}/visual/{job.id}"
    keys = []
    if not clip.download_url.startswith("mock://") and not allowed_download(clip, clip.download_url):
        raise ProviderError("the stock clip's address is not on the provider's servers")
    with download(clip) as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(0)
        storage.put_fileobj(f"{base}.mp4", f, "video/mp4")
        keys.append(f"{base}.mp4")
    thumbnail_key = None
    thumb = _fetch_thumbnail(clip)
    if thumb:
        thumbnail_key = f"{base}-thumb.jpg"
        storage.put_bytes(thumbnail_key, thumb, "image/jpeg")
        keys.append(thumbnail_key)
    return {
        "kind": "video_clip",
        "keys": keys,
        "key": f"{base}.mp4",
        "content_type": "video/mp4",
        "size": size,
        "duration": clip.duration or None,
        "width": clip.download_width or None,
        "height": clip.download_height or None,
        "meta": {
            "providerId": clip.provider_id,
            "author": clip.author,
            "pageUrl": clip.page_url,
            "license": "Pixabay Content License" if clip.provider_id.startswith("pixabay:") else "",
            "thumbnailKey": thumbnail_key,
            "seen": list(job.input.get("seen") or []),
        },
    }


def _make_image(job: Job, settings, *, fallback: bool) -> tuple[dict, str]:
    i = job.input
    providers = [IMAGE_PROVIDERS[n](settings) for n in image_names()]
    if not providers:
        raise AllProvidersFailed("image: no image provider is switched on", none_available=True)
    scene_prompt = i["visualPrompt"] or i["stockQuery"] or i["narration"]
    prompt = image_prompt(scene_prompt, i["mood"], i["style"], i["character"])
    result, provider = with_fallback(
        providers, lambda p: p.generate(prompt, i["seed"]), label="image", sleep=_pause_for(settings)
    )
    base = f"users/{job.user_id}/projects/{job.project_id}/visual/{job.id}"
    key = f"{base}.{result.extension}"
    storage = get_storage()
    storage.put_bytes(key, result.data, result.content_type)
    keys = [key]
    thumbnail_key = f"{base}-thumb.jpg"
    try:
        storage.put_bytes(thumbnail_key, _thumbnail_of(result.data), "image/jpeg")
        keys.append(thumbnail_key)
    except (OSError, ValueError):
        thumbnail_key = None
    made = {
        "kind": "image",
        "keys": keys,
        "key": key,
        "content_type": result.content_type,
        "size": len(result.data),
        "duration": None,
        "width": result.width,
        "height": result.height,
        "meta": {
            **result.meta,
            "prompt": prompt,
            "motion": "zoom",
            "stockFallback": fallback,
            "thumbnailKey": thumbnail_key,
            "seen": list(i.get("seen") or []),
        },
    }
    return made, provider


class VisualJob:
    @staticmethod
    def on_start(job: Job) -> None:
        db.session.execute(
            update(Scene)
            .where(Scene.id == job.scene_id, Scene.visual_status == "queued")
            .values(visual_status="running")
        )

    @staticmethod
    def work(job: Job):
        i = job.input
        settings = current_app.extensions["settings"]
        if i["source"] != "stock":
            made, provider = _make_image(job, settings, fallback=False)
            return {**made, "provider": provider}, provider

        if i.get("stockId"):
            name, _, clip_id = i["stockId"].partition(":")
            if name not in STOCK_PROVIDERS or name not in stock_names():
                raise jobs.JobFailed("provider_unavailable")
            try:
                clip = with_retry_get(STOCK_PROVIDERS[name](settings), clip_id, settings)
            except NotConfigured:
                raise jobs.JobFailed("provider_unavailable") from None
            except ProviderError as e:
                if e.retryable:
                    raise
                clip = None  # the provider refused the id: the clip is gone (QB4S2-007)
            if clip is None:
                raise jobs.JobFailed("stock_unavailable", STOCK_GONE)
            return {**_store_clip(job, clip), "provider": name}, name

        exclude = set(i.get("exclude") or [])
        stock_down = not stock_available()
        downloads_left = 3  # a clip that fails to download makes way for the next fitting one
        if not stock_down:
            first = i["stockQuery"] or i["visualPrompt"] or i["narration"]
            for query in filter(None, (first, _simpler(first))):
                # Later pages too, so a scene that has had every clip on page 1 still gets a
                # new one (QB4S2-011).
                for page in range(1, STOCK_PAGES + 1):
                    try:
                        clips, more = stock_search(query, page)
                    except ProviderError as e:
                        if e.refused:
                            raise
                        log.warning("stock search failed; using an AI image", extra={"job_id": str(job.id)})
                        stock_down = True  # go straight to the AI image (QB4S2-002)
                        break
                    while downloads_left:
                        clip = pick_clip(clips, float(i["seconds"]), exclude)
                        if clip is None:
                            break
                        try:
                            name = clip.provider_id.split(":", 1)[0]
                            return {**_store_clip(job, clip), "provider": name}, name
                        except ProviderError:
                            log.warning(
                                "stock download failed; trying the next clip", extra={"job_id": str(job.id)}
                            )
                            exclude.add(clip.provider_id)
                            downloads_left -= 1
                    if not more or not downloads_left:
                        break
                if stock_down or not downloads_left:
                    break
        # Nothing fits (G-6): an AI image, at no extra charge, a limited number a day per creator.
        if not _free_fallback_left(job.user_id):
            # When stock was simply down, say so rather than suggesting other words (QB4S2-012).
            raise jobs.JobFailed("provider_unavailable" if stock_down else "no_stock_match")
        made, provider = _make_image(job, settings, fallback=True)
        _count_free_fallback(job.user_id)  # only images actually made count (QB4S2-012)
        return {**made, "provider": provider}, provider

    @staticmethod
    def apply(job: Job, made: dict) -> dict:
        i = job.input
        scene = _lock_scene(job.scene_id)
        if scene is None or scene.visual_prompt != i["visualPrompt"] or scene.stock_query != i["stockQuery"]:
            raise jobs.JobFailed("scene_changed", VISUAL_CHANGED)
        if scene.visual_source != i["source"]:
            raise jobs.JobFailed("scene_changed", VISUAL_CHANGED)
        project = db.session.get(Project, job.project_id)
        if (
            (i["source"] != "stock" or made["kind"] == "image")
            and project is not None
            and (project.style != i["style"] or project.character != i["character"])
        ):
            raise jobs.JobFailed("scene_changed", VISUAL_CHANGED)
        asset = Asset(
            user_id=job.user_id,
            project_id=job.project_id,
            kind=made["kind"],
            s3_key=made["key"],
            content_type=made["content_type"],
            size_bytes=made["size"],
            duration_seconds=made["duration"],
            width=made["width"],
            height=made["height"],
            provider=made["provider"],
            meta=made["meta"],
        )
        db.session.add(asset)
        db.session.flush()
        old = scene.visual_asset_id
        scene.visual_asset_id = asset.id
        scene.visual_status = "ready"
        scene.last_error = None
        db.session.flush()
        release_assets([old])
        db.session.expire(scene, ["visual_asset"])
        if project is not None:
            after_edit(project)
        return {
            "visual": {
                "assetId": str(asset.id),
                "kind": "video" if made["kind"] == "video_clip" else "image",
                "durationSeconds": made["duration"],
            },
            "stockFallback": bool(made["meta"].get("stockFallback")),
        }

    @staticmethod
    def discard(job: Job | None, made: dict) -> None:  # noqa: ARG004
        for key in made.get("keys", []):
            try:
                get_storage().delete(key)
            except Exception:  # noqa: BLE001 - left for the clean-up of unused files (B8)
                log.warning("could not delete an unused visual file", exc_info=True)

    @staticmethod
    def on_stop(job: Job) -> None:
        scene = _lock_scene(job.scene_id)
        if scene is None or scene.visual_status not in ("queued", "running"):
            return
        i = job.input
        changed = (
            scene.visual_prompt != i["visualPrompt"]
            or scene.stock_query != i["stockQuery"]
            or scene.visual_source != i["source"]
        )
        if job.error_code == "scene_changed":
            scene.visual_status = "outdated" if scene.visual_asset_id else "missing"
        elif job.state == "failed":
            scene.visual_status, scene.last_error = "failed", job.error_message
        else:
            previous = i.get("previousStatus", "missing")
            if previous in ("queued", "running") or (previous == "ready" and changed):
                previous = "outdated" if scene.visual_asset_id else "missing"
            scene.visual_status = previous
        project = db.session.get(Project, scene.project_id)
        if project is not None:
            after_edit(project)


def _fallback_slot(user_id) -> str:
    return f"fallback:{user_id}:{datetime.now(UTC):%Y-%m-%d}"


def _free_fallback_left(user_id) -> bool:
    try:
        used = int(current_app.extensions["redis"].get(_fallback_slot(user_id)) or 0)
    except Exception:  # noqa: BLE001 - without Redis the fallback is still allowed
        return True
    return used < FREE_FALLBACKS_PER_DAY


def _count_free_fallback(user_id) -> None:
    redis = current_app.extensions["redis"]
    try:
        if redis.incr(_fallback_slot(user_id)) == 1:
            redis.expire(_fallback_slot(user_id), 2 * 24 * 3600)
    except Exception:  # noqa: BLE001
        log.warning("could not count a free fallback image", exc_info=True)


def with_retry_get(provider, clip_id: str, settings) -> StockClip | None:
    return with_retry(lambda: provider.get(clip_id), label="stock get", sleep=_pause_for(settings))


# ---------------------------------------------------------------- uploads (C10 with an asset)


def use_upload(project: Project, scene: Scene, asset_id: str) -> None:
    """Puts a finished upload on the scene at once. The project must be locked. Caller commits."""
    try:
        key = uuid.UUID(asset_id)
    except ValueError:
        key = None
    asset = db.session.get(Asset, key) if key else None
    if (
        asset is None
        or asset.user_id != project.user_id
        or asset.project_id != project.id
        or asset.kind != "upload"
        or asset.status != "ready"
        or not asset.content_type.startswith(("video/", "image/"))
        or asset.replaced_at is not None
    ):
        raise ApiError(
            400, "validation_error", "Some details need another look.",
            {"fields": {"assetId": "Choose a finished image or video upload of this project"}},
        )  # fmt: skip
    jobs.cancel_active(scene_ids=[scene.id], job_type="visual")
    old = scene.visual_asset_id
    scene.visual_asset_id = asset.id
    scene.visual_source = "upload"
    scene.visual_status = "ready"
    scene.last_error = None
    db.session.flush()
    if old != asset.id:
        release_assets([old])
    db.session.expire(scene, ["visual_asset"])
    after_edit(project)


def is_stock_id(value: str) -> bool:
    """An id from C9 of a stock provider that is set up and switched on."""
    name, sep, rest = value.partition(":")
    return bool(sep) and name in stock_names() and _has_key(name) and rest.isdigit() and len(rest) <= 20
