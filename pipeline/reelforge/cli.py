"""Command line for the prototype.

    python -m reelforge ideas --niche fitness
    python -m reelforge new --topic "3 morning habits" --length 30 [--mode full_ai] [--render]
    python -m reelforge build demo [--regenerate s2] [--render]
    python -m reelforge render demo

Add --mock to any command to run offline with placeholder providers.
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime

from .config import Settings
from .models import TONES
from .pipeline import Pipeline, save_script


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "reel"
    return f"{slug}-{datetime.now():%m%d-%H%M%S}"


def _settings(args) -> Settings:
    s = Settings()
    if args.mock:
        s.use_mocks_everywhere()
    return s


def cmd_ideas(args) -> int:
    pipe = Pipeline(_settings(args))
    for i, idea in enumerate(pipe.ideas(args.niche, args.keywords, args.count), 1):
        print(f"{i}. {idea.get('title')}\n   Hook: {idea.get('hook')}\n   Why: {idea.get('angle')}\n")
    return 0


def cmd_new(args) -> int:
    s = _settings(args)
    pipe = Pipeline(s, on_progress=print)
    script, warnings = pipe.write_script(
        args.topic, tone=args.tone, mode=args.mode, target_seconds=args.length,
        style=args.style, character=args.character, voice=args.voice,
    )
    project_id = args.project or _slug(script.title)
    project_dir = s.projects_dir / project_id
    if project_dir.exists() and not args.overwrite:
        print(f"Project {project_id} already exists; use --overwrite or another --project name.")
        return 1
    if project_dir.exists():
        shutil.rmtree(project_dir)
    path = save_script(project_dir, script)
    print(f"\nScript saved: {path}")
    print(f"Title: {script.title}  |  {len(script.scenes)} scenes  |  about {script.estimated_seconds():.0f}s")
    for sc in script.scenes:
        print(f"  {sc.id} [{sc.mood}] {sc.narration}")
    for w in warnings:
        print(f"WARNING: {w}")
    if args.script_only:
        print(f"\nEdit the script if you like, then run:  python -m reelforge build {project_id}")
        return 0
    return _build(pipe, s, project_id, [], args.render)


def cmd_build(args) -> int:
    s = _settings(args)
    return _build(Pipeline(s, on_progress=print), s, args.project, args.regenerate, args.render)


def _build(pipe: Pipeline, s: Settings, project_id: str, regenerate: list[str], render: bool) -> int:
    project_dir = s.projects_dir / project_id
    report = pipe.build(project_dir, regenerate)
    print(f"\nBuild {report.status}: {len(report.generated)} generated, {len(report.reused)} reused from cache")
    for w in report.warnings:
        print(f"WARNING: {w}")
    for key, msg in report.failures.items():
        print(f"FAILED {key}: {msg}")
    if report.status != "complete":
        print(f"Fix the problems above, then run:  python -m reelforge build {project_id}")
        return 2
    print(f"Timeline: {project_dir / 'timeline.json'}  ({report.total_seconds}s)")
    if render:
        return _render(s, project_id)
    print(f"Preview:  cd renderer && npm run studio -- --props=public/projects/{project_id}/timeline.json")
    print(f"Render:   python -m reelforge render {project_id}")
    return 0


def cmd_render(args) -> int:
    return _render(_settings(args), args.project)


def _render(s: Settings, project_id: str) -> int:
    timeline = s.projects_dir / project_id / "timeline.json"
    if not timeline.exists():
        print(f"No timeline for {project_id}; run build first.")
        return 1
    node = shutil.which("node")
    if not node:
        print("Node.js is not installed or not on PATH.")
        return 1
    print("Rendering with Remotion...", flush=True)
    return subprocess.call([node, "scripts/render.mjs", project_id], cwd=s.renderer_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reelforge", description="ReelForge generation pipeline prototype")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--mock", action="store_true", help="use offline placeholder providers")

    p = sub.add_parser("ideas", help="suggest video ideas")
    common(p)
    p.add_argument("--niche", required=True)
    p.add_argument("--keywords", default="")
    p.add_argument("--count", type=int, default=5)
    p.set_defaults(func=cmd_ideas)

    p = sub.add_parser("new", help="write a script for a topic, then build it")
    common(p)
    p.add_argument("--topic", required=True)
    p.add_argument("--tone", choices=TONES, default="educational")
    p.add_argument("--mode", choices=["faceless", "full_ai"], default="faceless")
    p.add_argument("--length", type=int, choices=[15, 30, 60, 90], default=30)
    p.add_argument("--style", default="cinematic")
    p.add_argument("--character", default="", help="main character for Full AI mode")
    p.add_argument("--voice", default="", help="voice id for the voice provider (blank = default)")
    p.add_argument("--project", help="project id (default: made from the title)")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--script-only", action="store_true", help="stop after writing script.json")
    p.add_argument("--render", action="store_true")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("build", help="(re)build a project from its script.json, reusing cached steps")
    common(p)
    p.add_argument("project")
    p.add_argument("--regenerate", action="append", default=[],
                   help="scene id (s2), step key (voice:s2), 'music' or 'all'; repeatable")
    p.add_argument("--render", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("render", help="render a built project to MP4")
    common(p)
    p.add_argument("project")
    p.set_defaults(func=cmd_render)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        if args.verbose:
            raise
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
