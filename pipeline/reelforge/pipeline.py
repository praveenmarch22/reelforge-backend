"""The generation pipeline. See docs/PIPELINE_STRATEGY.md for the reasoning behind each step.

script.json ─► voices (parallel) ─► visuals (parallel) ─► music ─► timeline.json
"""

from __future__ import annotations

import json
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from pydantic import ValidationError

from . import prompts, providers
from .cache import Cache, input_hash
from .config import Settings
from .errors import ProviderError, with_fallback
from .media import media_duration
from .models import MAX_SECONDS, MusicResult, Scene, Script, VisualResult, VoiceResult
from .timeline import TimelineTooLong, build_timeline

log = logging.getLogger("reelforge")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".m4v")


@dataclass
class BuildReport:
    project_id: str
    status: str = "incomplete"  # complete | incomplete | too_long
    failures: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    generated: list[str] = field(default_factory=list)
    providers: dict[str, str] = field(default_factory=dict)
    total_seconds: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__


class Pipeline:
    def __init__(self, settings: Settings, on_progress: Optional[Callable[[str], None]] = None):
        self.s = settings
        self.llm = providers.build("llm", settings.llm_providers, settings)
        self.voice = providers.build("voice", settings.voice_providers, settings)
        self.stock = providers.build("stock", settings.stock_providers, settings)
        self.image = providers.build("image", settings.image_providers, settings)
        self.video = providers.build("video", settings.video_providers, settings)
        self.music = providers.build("music", settings.music_providers, settings)
        self.progress = on_progress or (lambda msg: log.info(msg))

    # ------------------------------------------------------------------ ideas and script

    def ideas(self, niche: str, keywords: str = "", count: int = 5) -> list[dict]:
        prompt = prompts.ideas_prompt(niche, keywords, count)

        def call(p):
            data = p.generate_json(prompt)
            ideas = data.get("ideas")
            if not isinstance(ideas, list) or not ideas:
                raise ProviderError("no ideas in the answer", retryable=True)
            return ideas[:count]

        return with_fallback(self.llm, call, label="ideas")

    def write_script(self, topic: str, *, tone: str = "educational", mode: str = "faceless",
                     target_seconds: int = 30, style: str = "cinematic", character: str = "",
                     voice: str = "") -> tuple[Script, list[str]]:
        prompt = prompts.script_prompt(topic, tone, mode, target_seconds, style, character)
        source = "ai_video" if mode == "full_ai" else "stock"

        def call(p):
            data = p.generate_json(prompt)
            raw_scenes = data.get("scenes") or []
            try:
                return Script(
                    title=data.get("title") or topic[:60],
                    topic=topic, tone=tone, mode=mode, target_seconds=target_seconds,
                    style=style, character=character, voice=voice,
                    music_mood=data.get("music_mood", "calm"),
                    post_caption=data.get("post_caption", ""),
                    hashtags=[h.lstrip("#") for h in data.get("hashtags", [])][:30],
                    scenes=[
                        Scene(id=f"s{i + 1}", visual_source=source, **{
                            k: sc.get(k, "") for k in
                            ("narration", "on_screen_text", "visual_prompt", "stock_query", "mood")
                        })
                        for i, sc in enumerate(raw_scenes)
                    ],
                )
            except (ValidationError, AttributeError, TypeError) as e:
                raise ProviderError(f"script did not match the expected shape: {e}", retryable=True) from e

        script = with_fallback(self.llm, call, label="script")
        warnings = check_script(script)
        return script, warnings

    # ------------------------------------------------------------------ build

    def build(self, project_dir: Path, regenerate: list[str] | None = None) -> BuildReport:
        project_id = project_dir.name
        script = load_script(project_dir)
        cache = Cache(project_dir)
        report = BuildReport(project_id=project_id, warnings=check_script(script))
        for target in regenerate or []:
            removed = cache.forget(target)
            self.progress(f"Will regenerate: {', '.join(removed) or f'nothing matched {target!r}'}")

        # 1. Voices: their lengths decide every scene's duration.
        self.progress(f"Voiceover for {len(script.scenes)} scenes...")
        voices = self._run_scenes(script.scenes, lambda sc: self._voice(sc, script, project_dir, cache, report), report)

        # 2. Visuals: fitted to the voice durations.
        self.progress("Visuals...")
        visuals = self._run_scenes(
            script.scenes, lambda sc: self._visual(sc, script, voices.get(sc.id), project_dir, cache, report), report,
        )

        # 3. Music: optional; a failure only produces a warning.
        music = None
        try:
            music = self._music(script, project_dir, cache, report)
        except ProviderError as e:
            report.warnings.append(f"No music: {e}")

        # 4. Timeline, only when every scene has a voice and a visual.
        missing = [sc.id for sc in script.scenes if sc.id not in voices or sc.id not in visuals]
        if missing:
            report.status = "incomplete"
            self.progress(f"Not ready to render; failed scenes: {', '.join(missing)}")
        else:
            try:
                timeline = build_timeline(project_id, script, voices, visuals, music, self.s.public_dir)
                (project_dir / "timeline.json").write_text(json.dumps(timeline, indent=2), "utf-8")
                report.total_seconds = round(timeline["durationInFrames"] / timeline["fps"], 2)
                report.status = "complete"
            except TimelineTooLong as e:
                report.status = "too_long"
                report.failures["timeline"] = str(e)

        (project_dir / "build_report.json").write_text(json.dumps(report.to_dict(), indent=2), "utf-8")
        return report

    # ------------------------------------------------------------------ steps

    def _run_scenes(self, scenes: list[Scene], fn, report: BuildReport) -> dict:
        """Run fn for every scene in parallel; collect failures per scene instead of stopping."""
        results = {}
        with ThreadPoolExecutor(max_workers=max(1, self.s.concurrency)) as pool:
            futures = {sc.id: pool.submit(fn, sc) for sc in scenes}
            for scene_id, fut in futures.items():
                try:
                    results[scene_id] = fut.result()
                except Exception as e:  # one scene failing must not stop the others
                    report.failures[scene_id] = f"{type(e).__name__}: {e}"
                    log.error("scene %s failed: %s", scene_id, e)
        return results

    def _cached(self, cache: Cache, key: str, digest: str, model, report: BuildReport, make):
        hit = cache.get(key, digest)
        if hit:
            report.reused.append(key)
            return model(**hit)
        result = make()
        cache.put(key, digest, result.model_dump())
        report.generated.append(key)
        report.providers[key] = result.provider
        return result

    def _voice(self, scene: Scene, script: Script, project_dir: Path, cache: Cache, report: BuildReport) -> VoiceResult:
        digest = input_hash(text=scene.narration, voice=script.voice, mood=scene.mood,
                            providers=self.s.voice_providers)

        def make():
            out = project_dir / f"voice_{scene.id}"
            result = with_fallback(
                self.voice, lambda p: p.synthesize(scene.narration, script.voice, scene.mood, out),
                label=f"voice {scene.id}",
            )
            self.progress(f"  voice {scene.id}: {result.duration:.1f}s via {result.provider}")
            return result

        return self._cached(cache, f"voice:{scene.id}", digest, VoiceResult, report, make)

    def _visual(self, scene: Scene, script: Script, voice: Optional[VoiceResult], project_dir: Path,
                cache: Cache, report: BuildReport) -> VisualResult:
        if voice is None:
            raise ProviderError("skipped because the voiceover failed")
        out = project_dir / f"visual_{scene.id}"
        full_prompt = prompts.visual_prompt(scene.visual_prompt, scene.mood, script.style, script.character)

        if scene.visual_source == "upload":
            return self._upload(scene, out)

        if scene.visual_source == "stock":
            digest = input_hash(source="stock", query=scene.stock_query or scene.visual_prompt,
                                providers=self.s.stock_providers, fallback=self._image_digest(full_prompt, scene))

            def make_stock():
                query = scene.stock_query or scene.visual_prompt
                found = None
                try:
                    found = with_fallback(self.stock, lambda p: p.find_video(query, voice.duration, out),
                                          label=f"stock {scene.id}")
                except ProviderError as e:
                    log.warning("stock search failed for %s: %s", scene.id, e)
                if found:
                    self.progress(f"  visual {scene.id}: stock clip via {found.provider}")
                    return found
                self.progress(f"  visual {scene.id}: no stock clip, using an AI image")
                return self._make_image(full_prompt, scene, out)

            return self._cached(cache, f"visual:{scene.id}", digest, VisualResult, report, make_stock)

        if scene.visual_source == "ai_image":
            return self._cached(cache, f"visual:{scene.id}", self._image_digest(full_prompt, scene),
                                VisualResult, report, lambda: self._make_image(full_prompt, scene, out))

        # ai_video: a keyframe image first (style anchor + seed), then image-to-video.
        keyframe = self._cached(
            cache, f"keyframe:{scene.id}", self._image_digest(full_prompt, scene), VisualResult, report,
            lambda: self._make_image(full_prompt, scene, project_dir / f"keyframe_{scene.id}"),
        )
        if not self.video:
            report.warnings.append(f"{scene.id}: no AI video provider set; using the keyframe image with motion")
            return keyframe
        digest = input_hash(prompt=full_prompt, keyframe=keyframe.path, seed=self.s.seed,
                            providers=self.s.video_providers)

        def make_video():
            try:
                clip = with_fallback(
                    self.video,
                    lambda p: p.generate(full_prompt, Path(keyframe.path), voice.duration, self.s.seed, out),
                    label=f"video {scene.id}",
                )
                self.progress(f"  visual {scene.id}: AI clip via {clip.provider}")
                return clip
            except ProviderError as e:
                if e.refused:
                    raise
                report.warnings.append(f"{scene.id}: AI video failed ({e}); using the keyframe image with motion")
                return keyframe

        return self._cached(cache, f"visual:{scene.id}", digest, VisualResult, report, make_video)

    def _image_digest(self, full_prompt: str, scene: Scene) -> str:
        return input_hash(source="image", prompt=full_prompt, seed=self.s.seed, providers=self.s.image_providers)

    def _make_image(self, full_prompt: str, scene: Scene, out: Path) -> VisualResult:
        result = with_fallback(self.image, lambda p: p.generate(full_prompt, scene.mood, self.s.seed, out),
                               label=f"image {scene.id}")
        self.progress(f"  image {scene.id} via {result.provider}")
        return result

    def _upload(self, scene: Scene, out: Path) -> VisualResult:
        src = Path(scene.upload_path or "")
        if not src.is_file():
            raise ProviderError(f"upload not found: {src}")
        dest = out.with_suffix(src.suffix.lower())
        shutil.copyfile(src, dest)
        if dest.suffix in VIDEO_EXTENSIONS:
            return VisualResult(kind="video", path=str(dest), duration=media_duration(dest), provider="upload")
        return VisualResult(kind="image", path=str(dest), provider="upload")

    def _music(self, script: Script, project_dir: Path, cache: Cache, report: BuildReport) -> Optional[MusicResult]:
        if not self.music:
            return None
        digest = input_hash(mood=script.music_mood, providers=self.s.music_providers)

        def make():
            result = with_fallback(self.music, lambda p: p.pick(script.music_mood, project_dir / "music"),
                                   label="music")
            self.progress(f"Music via {result.provider}")
            return result

        return self._cached(cache, "music", digest, MusicResult, report, make)


# ---------------------------------------------------------------------- helpers

def check_script(script: Script) -> list[str]:
    warnings = []
    est = script.estimated_seconds()
    if est > MAX_SECONDS:
        warnings.append(f"Script is about {est:.0f}s, over the {MAX_SECONDS}s limit; rendering will be blocked.")
    elif est > script.target_seconds * 1.25:
        warnings.append(f"Script is about {est:.0f}s, longer than the {script.target_seconds}s target.")
    elif est < 5:
        warnings.append("Script is under 5 seconds.")
    return warnings


def load_script(project_dir: Path) -> Script:
    path = project_dir / "script.json"
    if not path.exists():
        raise FileNotFoundError(f"No script.json in {project_dir}")
    # utf-8-sig: Windows editors often add a byte-order mark when saving.
    return Script.model_validate_json(path.read_text("utf-8-sig"))


def save_script(project_dir: Path, script: Script) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / "script.json"
    path.write_text(script.model_dump_json(indent=2), "utf-8")
    return path
