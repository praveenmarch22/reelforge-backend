"""What each kind of job does: ideas, script, scene rewrite and hooks (milestone B3); the
generation jobs (B4) are in `generation.py`.

Every type has three steps, called by jobs.run():
  work(job)            the slow part (a language-model call), with no database lock held;
                       returns (result, provider name)
  apply(job, result)   saves the result, inside the transaction that marks the job done;
                       returns the job's output
  on_stop(job)         undoes the job's effect on the project when it fails or is cancelled
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import Any

from flask import current_app
from sqlalchemy import select

from . import prompts
from .extensions import db
from .jobs import JobFailed, cancel_active
from .models import Job, Project, ProviderSwitch, Scene
from .projects import after_edit, detach_jobs, mark_voice_outdated, release_assets
from .providers.base import AllProvidersFailed, ProviderError, with_fallback
from .providers.llm import PROVIDERS
from .script_rules import (
    HASHTAG_MAX,
    HASHTAGS_MAX,
    NARRATION_MAX,
    ON_SCREEN_TEXT_MAX,
    POST_CAPTION_MAX,
    POST_TITLE_MAX,
    PROMPT_MAX,
    SCENES_MAX,
    STOCK_QUERY_MAX,
    TITLE_MAX,
    default_visual_source,
    normalise_mood,
)

# ---------------------------------------------------------------- the language model


def enabled_llm_names() -> list[str]:
    """Configured language models that the admin has not switched off (G-3)."""
    names = current_app.extensions["settings"].llm_provider_names
    off = set(
        db.session.execute(
            select(ProviderSwitch.provider).where(
                ProviderSwitch.provider.in_(names), ProviderSwitch.enabled.is_(False)
            )
        ).scalars()
    )
    return [n for n in names if n in PROVIDERS and n not in off]


def llm_available() -> bool:
    """Whether any language model could take a job right now (checked before credits are spent)."""
    settings = current_app.extensions["settings"]
    keys = {"gemini": settings.gemini_api_key, "groq": settings.groq_api_key, "mock": "yes"}
    return any(keys.get(name) for name in enabled_llm_names())


class Unusable(ValueError):
    """The model's answer doesn't have the required shape."""


def ask(kind: str, prompt: str, context: dict, check: Callable[[dict], Any]) -> tuple[Any, str]:
    """Asks the language models in order. An unusable answer is asked again (S-1: up to two
    more times), then the next model is tried. A refusal stops at once (S-4)."""
    settings = current_app.extensions["settings"]
    providers = [PROVIDERS[name](settings) for name in enabled_llm_names()]
    if not providers:
        raise AllProvidersFailed(f"{kind}: no language model is switched on", none_available=True)

    def call(provider) -> Any:
        answer = provider.generate_json(prompt, kind, context)
        if answer.get("refused") is True:
            raise ProviderError("the model refused", refused=True)
        try:
            return check(answer)
        except (Unusable, KeyError, TypeError, AttributeError):
            raise ProviderError("the model's answer had the wrong shape", retryable=True) from None

    delay = 0 if settings.env == "test" else 2

    def pause(seconds: float) -> None:
        if delay:
            import time

            time.sleep(seconds)

    return with_fallback(providers, call, label=kind, sleep=pause)


# ---------------------------------------------------------------- checking answers


def clean(value: Any, limit: int, *, required: bool = False) -> str:
    """One line of text from the model: whitespace collapsed, control characters and broken
    characters removed, cut to `limit` characters."""
    if not isinstance(value, str):
        if required:
            raise Unusable("text expected")
        value = "" if value is None else str(value)
    value = "".join(ch for ch in value if unicodedata.category(ch) not in ("Cc", "Cs", "Co"))
    value = " ".join(value.split())[:limit].strip()
    if required and not value:
        raise Unusable("empty text")
    return value


def clean_hashtags(value: Any) -> list[str]:
    tags: list[str] = []
    for item in value if isinstance(value, list) else []:
        tag = re.sub(r"\s+", "", clean(item, HASHTAG_MAX + 5)).lstrip("#")[:HASHTAG_MAX]
        if tag and tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
    return tags[:HASHTAGS_MAX]


def check_ideas(count: int) -> Callable[[dict], list[dict]]:
    def check(answer: dict) -> list[dict]:
        ideas = [
            {
                "title": clean(i.get("title"), TITLE_MAX, required=True),
                "hook": clean(i.get("hook"), 300, required=True),
                "angle": clean(i.get("angle"), 300),
            }
            for i in answer["ideas"]
            if isinstance(i, dict)
        ]
        if not ideas:
            raise Unusable("no ideas")
        return ideas[:count]

    return check


def _scene_extras(item: dict) -> dict:
    return {
        "on_screen_text": clean(item.get("on_screen_text"), ON_SCREEN_TEXT_MAX),
        "visual_prompt": clean(item.get("visual_prompt"), PROMPT_MAX),
        "stock_query": clean(item.get("stock_query"), STOCK_QUERY_MAX),
        "mood": normalise_mood(item.get("mood")),
    }


def _script_meta(answer: dict) -> dict:
    return {
        "title": clean(answer.get("title"), TITLE_MAX) or "Untitled Reel",
        "music_mood": normalise_mood(answer.get("music_mood")),
        "post_caption": clean(answer.get("post_caption"), POST_CAPTION_MAX),
        "hashtags": clean_hashtags(answer.get("hashtags")),
    }


def check_script(answer: dict) -> dict:
    items = answer["scenes"]
    if not isinstance(items, list) or not 1 <= len(items) <= SCENES_MAX:
        raise Unusable("scene count")
    scenes = []
    for item in items:
        narration = clean(item.get("narration"), NARRATION_MAX + 1, required=True)
        if len(narration) > NARRATION_MAX:
            raise Unusable("narration too long")
        scenes.append({"narration": narration, **_scene_extras(item)})
    return {**_script_meta(answer), "scenes": scenes}


def check_annotations(narrations: list[str]) -> Callable[[dict], dict]:
    """For a pasted script: the model only describes the scenes; the words stay the creator's."""

    def check(answer: dict) -> dict:
        items = answer["scenes"]
        if not isinstance(items, list) or len(items) != len(narrations):
            raise Unusable("scene count differs")
        scenes = [{"narration": n, **_scene_extras(item)} for n, item in zip(narrations, items, strict=True)]
        return {**_script_meta(answer), "scenes": scenes}

    return check


def check_rewrite(answer: dict) -> dict:
    narration = clean(answer.get("narration"), NARRATION_MAX + 1, required=True)
    if len(narration) > NARRATION_MAX:
        raise Unusable("narration too long")
    return {"narration": narration, "on_screen_text": clean(answer.get("on_screen_text"), ON_SCREEN_TEXT_MAX)}


def check_hooks(answer: dict) -> list[str]:
    hooks = [clean(h, 300) for h in answer["hooks"] if isinstance(h, str)]
    hooks = [h for h in hooks if h][:3]
    if not hooks:
        raise Unusable("no hooks")
    return hooks


# ---------------------------------------------------------------- splitting a pasted script

_SCENE_WORDS = 30  # about 12 seconds of speech


def split_script(script: str) -> list[str]:
    """Splits a pasted script into scenes at sentence ends, about 30 words each, never
    changing a word (PRD S2). Paragraph breaks always start a new scene."""
    scenes: list[str] = []
    for paragraph in re.split(r"\n\s*\n", script):
        sentences = [s for s in re.split(r"(?<=[.!?…])\s+", " ".join(paragraph.split())) if s]
        current: list[str] = []
        for sentence in sentences:
            if current and len(" ".join([*current, sentence]).split()) > _SCENE_WORDS:
                scenes.append(" ".join(current))
                current = []
            current.append(sentence)
        if current:
            scenes.append(" ".join(current))
    # A single very long sentence: cut it at word boundaries to fit the narration limit.
    fitted: list[str] = []
    for scene in scenes:
        while len(scene) > NARRATION_MAX:
            cut = scene.rfind(" ", 0, NARRATION_MAX)
            cut = cut if cut > 0 else NARRATION_MAX
            fitted.append(scene[:cut].strip())
            scene = scene[cut:].strip()
        if scene:
            fitted.append(scene)
    # More scenes than allowed: join neighbours until it fits.
    while len(fitted) > SCENES_MAX:
        merged, i = [], 0
        while i < len(fitted):
            pair = " ".join(fitted[i : i + 2])
            if i + 1 < len(fitted) and len(pair) <= NARRATION_MAX:
                merged.append(pair)
                i += 2
            else:
                merged.append(fitted[i])
                i += 1
        if len(merged) == len(fitted):
            break
        fitted = merged
    return fitted


# ---------------------------------------------------------------- the job types


class Ideas:
    @staticmethod
    def work(job: Job):
        i = job.input
        context = {"niche": i["niche"], "keywords": i.get("keywords", ""), "count": i["count"]}
        prompt = prompts.ideas_prompt(i["niche"], i.get("keywords", ""), i["count"])
        return ask("ideas", prompt, context, check_ideas(i["count"]))

    @staticmethod
    def apply(job: Job, ideas: list[dict]) -> dict:  # noqa: ARG004
        return {"ideas": ideas}

    @staticmethod
    def on_stop(job: Job) -> None:
        pass


class Script:
    """Writes a project's script from its topic, or prepares a pasted one (B3, B8)."""

    @staticmethod
    def work(job: Job):
        i = job.input
        if i.get("script"):
            narrations = split_script(i["script"])
            numbered = "\n".join(f"{n}. {text}" for n, text in enumerate(narrations, start=1))
            prompt = prompts.split_script_prompt(numbered, i["tone"], i["mode"], i["style"], i["character"])
            context = {"script": i["script"], "narrations": narrations}
            return ask("annotate_script", prompt, context, check_annotations(narrations))
        prompt = prompts.script_prompt(
            i["topic"],
            i["tone"],
            i["mode"],
            i["targetSeconds"],
            i["style"],
            i["character"],
            i.get("instructions", ""),
        )
        return ask(
            "script", prompt, {"topic": i["topic"], "instructions": i.get("instructions", "")}, check_script
        )

    @staticmethod
    def apply(job: Job, script: dict) -> dict:
        project = db.session.execute(
            select(Project)
            .where(Project.id == job.project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        old = list(project.scenes)
        if old:
            # Work still running on the scenes being replaced is cancelled and refunded,
            # and their files are left for the clean-up of replaced assets.
            cancel_active(scene_ids=[s.id for s in old])
            detach_jobs([s.id for s in old])
            asset_ids = [a for s in old for a in (s.voice_asset_id, s.visual_asset_id, s.keyframe_asset_id)]
            for scene in old:
                db.session.delete(scene)
            db.session.flush()
            release_assets(asset_ids)
        source = default_visual_source(project.mode)
        for position, item in enumerate(script["scenes"], start=1):
            db.session.add(Scene(project_id=project.id, position=position, visual_source=source, **item))
        if job.input.get("first"):
            # The first script names the project; a regenerated one keeps what the creator chose.
            if project.title == "Untitled Reel":  # the creator may have named it meanwhile
                project.title = script["title"]
            project.music_mood = script["music_mood"]
        if not project.post_caption:
            project.post_caption = script["post_caption"]
        if not project.hashtags:
            project.hashtags = script["hashtags"]
        project.status = "draft"
        db.session.flush()
        db.session.expire(project, ["scenes"])
        return {"sceneCount": len(script["scenes"])}

    @staticmethod
    def on_stop(job: Job) -> None:
        project = db.session.get(Project, job.project_id)
        if project is not None and project.status == "scripting":
            previous = job.input.get("previousStatus", "draft")
            project.status = "draft" if previous == "scripting" else previous
            if project.status == "generating":
                # Its voices and visuals may all have ended while the script was being
                # written; then the run is over now (QB4S3-001).
                from .reel import advance_project

                advance_project(project.id)


class RewriteScene:
    """C6: rewrites one scene's narration and on-screen text."""

    @staticmethod
    def work(job: Job):
        i = job.input
        prompt = prompts.rewrite_scene_prompt(
            i["topic"],
            i["tone"],
            i["before"],
            i["narration"],
            i["onScreenText"],
            i["after"],
            i["instructions"],
        )
        context = {
            "narration": i["narration"],
            "on_screen_text": i["onScreenText"],
            "instructions": i["instructions"],
        }
        return ask("rewrite_scene", prompt, context, check_rewrite)

    @staticmethod
    def apply(job: Job, rewrite: dict) -> dict:
        scene = db.session.execute(
            select(Scene)
            .where(Scene.id == job.scene_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if scene is None:  # the scene was deleted while it was being rewritten
            raise JobFailed("scene_changed")
        if scene.narration != job.input["narration"] or scene.on_screen_text != job.input["onScreenText"]:
            raise JobFailed("scene_changed")
        if scene.narration != rewrite["narration"] and scene.voice_status in ("queued", "running"):
            # A voice being made for the old words would be thrown away: stop and refund it.
            cancel_active(scene_ids=[scene.id], job_type="voice")
        scene.narration = rewrite["narration"]
        scene.on_screen_text = rewrite["on_screen_text"]
        mark_voice_outdated(scene)
        after_edit(db.session.get(Project, scene.project_id))
        return {"narration": scene.narration, "onScreenText": scene.on_screen_text}

    @staticmethod
    def on_stop(job: Job) -> None:
        pass


def check_caption(answer: dict) -> dict:
    # YouTube refuses < and > (YT-11): they are removed first, so what is saved can always be
    # published as it is (BUG-079). A title left empty is asked for again.
    def plain(value):
        return value.replace("<", "").replace(">", "") if isinstance(value, str) else value

    title = clean(plain(answer["title"]), POST_TITLE_MAX, required=True)
    caption = clean(plain(answer["caption"]), POST_CAPTION_MAX)
    tags = clean_hashtags([plain(t) for t in answer.get("hashtags") or [] if isinstance(t, str)])
    return {"title": title, "caption": caption, "hashtags": tags[:10]}


class Caption:
    """F4: the post's title, caption and hashtags, written by the language model. Free."""

    @staticmethod
    def work(job: Job):
        i = job.input
        prompt = prompts.caption_prompt(i["title"], i["topic"], i["script"])
        return ask("caption", prompt, {"title": i["title"], "topic": i["topic"]}, check_caption)

    @staticmethod
    def apply(job: Job, written: dict) -> dict:
        project = db.session.get(Project, job.project_id)
        if project is not None:
            project.post_title, project.post_caption = written["title"], written["caption"]
            project.hashtags = written["hashtags"]
        return written

    @staticmethod
    def on_stop(job: Job) -> None:
        pass


class Hooks:
    """B9: three alternative opening lines. Choosing one is an ordinary scene edit."""

    @staticmethod
    def work(job: Job):
        i = job.input
        prompt = prompts.hooks_prompt(i["topic"], i["tone"], i["firstScene"])
        return ask("hooks", prompt, {"topic": i["topic"], "first_scene": i["firstScene"]}, check_hooks)

    @staticmethod
    def apply(job: Job, hooks: list[str]) -> dict:  # noqa: ARG004
        return {"hooks": hooks}

    @staticmethod
    def on_stop(job: Job) -> None:
        pass


from .generation import VoiceJob  # noqa: E402 - generation imports this module's neighbours
from .publishing import PublishJob  # noqa: E402
from .renders import RenderJob  # noqa: E402
from .visuals import VisualJob  # noqa: E402

TYPES = {
    "ideas": Ideas, "script": Script, "rewrite_scene": RewriteScene, "hooks": Hooks,
    "voice": VoiceJob, "visual": VisualJob, "render": RenderJob, "caption": Caption,
    "publish": PublishJob,
}  # fmt: skip
