# Generation pipeline prototype

This prototype turns a topic into a finished vertical Reel (MP4) from the command line:

```
topic → script (LLM) → voiceover per scene → visuals per scene → music → timeline.json → Remotion render → MP4
```

It is built to be reused in the product:

- `pipeline/` (Python) becomes the core of the Flask API's Celery workers.
- `renderer/` (Remotion, React) becomes the render service, and its components become the browser preview.

The reasoning behind every step is in [docs/PIPELINE_STRATEGY.md](docs/PIPELINE_STRATEGY.md). The product requirements are in [docs/PRD.md](docs/PRD.md).

## Folder layout

```
backend/
├── pipeline/                   Python
│   ├── .env.example            API keys and provider fallback order
│   └── reelforge/
│       ├── cli.py              commands: ideas, new, build, render
│       ├── pipeline.py         orchestration
│       ├── prompts.py          LLM prompts and mood → visual cues
│       ├── timeline.py         builds timeline.json
│       ├── cache.py            reuses unchanged steps
│       ├── errors.py           retries and provider fallback
│       └── providers/          gemini, groq, elevenlabs, edge, pexels, cloudflare, hf_space, music, mocks
└── renderer/                   Remotion
    ├── src/Reel.tsx            scenes + music with ducking and fades
    ├── src/Scene.tsx           visuals (zoom/pan, clip fitting), headline, voice
    ├── src/Captions.tsx        word-by-word captions, 3 styles
    ├── scripts/render.mjs      renders a project to out/<id>.mp4
    └── public/projects/<id>/   script.json, generated files, timeline.json (created by the pipeline)
```

## Setup (once)

Requirements: Python 3.12, Node.js 18 or newer. FFmpeg is not needed; Remotion includes its own.

```bash
cd pipeline
```

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

```bash
copy .env.example .env
```

```bash
cd ../renderer
```

```bash
npm install
```

The first render downloads a headless Chrome (about 100 MB) automatically.

## Try it without any API keys

`--mock` uses offline placeholder providers: a tone instead of a voice, gradient images, and a synth pad for music.

```bash
cd pipeline
```

```bash
.venv\Scripts\python -m reelforge new --mock --topic "3 morning habits" --length 15 --render
```

The video is written to `renderer/out/<project-id>.mp4`.

## Use real providers (all have free tiers)

Put your keys in `pipeline/.env`. Each `*_PROVIDERS` line is a fallback order; providers without a key are skipped.

| Step | Provider | Where to get a free key |
|---|---|---|
| Script | Gemini | Google AI Studio → Get API key |
| Script fallback | Groq | console.groq.com → API keys |
| Voice | ElevenLabs | elevenlabs.io → Profile → API keys |
| Voice fallback | Edge TTS | no key needed |
| Stock video | Pexels | pexels.com/api |
| AI images | Cloudflare Workers AI | Cloudflare dashboard → AI → Workers AI (account ID + API token) |
| AI video | Any Hugging Face Space | huggingface.co → Settings → Access tokens; see "AI video" below |
| Music | Your own library | see "Music" below |

Then:

```bash
.venv\Scripts\python -m reelforge ideas --niche "personal finance"
```

```bash
.venv\Scripts\python -m reelforge new --topic "Why your savings account is losing you money" --tone educational --length 30 --render
```

Full AI mode:

```bash
.venv\Scripts\python -m reelforge new --mode full_ai --topic "A lonely robot finds a friend" --tone storytelling --style "3D animation" --character "a small rusty robot with big blue eyes" --length 30 --render
```

## Edit and regenerate only what changed

1. Write the script and stop:

   ```bash
   .venv\Scripts\python -m reelforge new --topic "3 morning habits" --length 30 --script-only
   ```

2. Edit `renderer/public/projects/<id>/script.json`: narration, on-screen text, mood, visual prompt, or `visual_source` (`stock`, `ai_image`, `ai_video`, `upload` with an `upload_path`).
3. Build. Only scenes whose inputs changed are regenerated; the rest come from the cache:

   ```bash
   .venv\Scripts\python -m reelforge build <id> --render
   ```

4. To get a fresh result for something you didn't edit, for example a different stock clip for scene 2:

   ```bash
   .venv\Scripts\python -m reelforge build <id> --regenerate visual:s2 --render
   ```

   `--regenerate` takes a scene id (`s2`: voice and visual), a step (`voice:s2`, `visual:s2`), `music`, or `all`.

## Preview in Remotion Studio

```bash
cd renderer
```

```bash
npm run studio -- --props=public/projects/<id>/timeline.json
```

Studio opens in the browser. You can scrub the timeline and change props live, such as `captionStyle` (`bold-pop`, `clean`, `neon`), `showCaptions`, or the music volumes.

## AI video (Full AI mode)

With `VIDEO_PROVIDERS` empty, Full AI scenes use their AI keyframe image with slow motion. To use a real image-to-video model:

1. Find an image-to-video Space on Hugging Face (search for Wan or LTX-Video) and test it in the browser.
2. Click **Use via API** on the Space to see its endpoint name and arguments.
3. Install the client: `.venv\Scripts\python -m pip install gradio_client`
4. Set in `.env`, for example:

   ```
   VIDEO_PROVIDERS=hf_space
   HF_TOKEN=hf_...
   HF_VIDEO_SPACE=owner/space-name
   HF_VIDEO_API_NAME=/generate
   HF_VIDEO_ARGS={"image": "{image}", "prompt": "{prompt}", "seed": "{seed}"}
   ```

Free Spaces queue and sometimes fail. When that happens, the scene falls back to its keyframe image and the build report says so.

## Music

Add royalty-free tracks (for example from Pixabay Music) to `renderer/public/music/` and list them in `renderer/public/music/library.json`:

```json
[
  {"file": "upbeat.mp3", "moods": ["excited", "joyful", "funny"], "license": "Pixabay Content License"},
  {"file": "ambient.mp3", "moods": ["calm", "mysterious", "sad"], "license": "Pixabay Content License"}
]
```

Without a library, the mock synth pad is used. Set `MUSIC_PROVIDERS=library` to have no music instead.

## Output files per project

| File | What it is |
|---|---|
| `script.json` | The editable script |
| `voice_s*.mp3` / `visual_s*.*` / `music.*` | Generated assets |
| `cache.json` | Which inputs produced each asset |
| `build_report.json` | Status, failures, warnings, providers used, reused vs generated |
| `timeline.json` | What the renderer plays |

## What was tested

- Offline end to end with `--mock`: faceless and Full AI modes, rendered to 1080×1920, 30 fps, H.264 and AAC.
- Cache: editing one scene's narration regenerated only that scene's voice (1 generated, 8 reused).
- Clip fitting: a 2-second clip in a 3.5-second scene played at 0.8× speed, then held its last frame with a zoom.
- Edge TTS with real word timings, rendered to MP4.

Not yet tested against real accounts: Gemini, Groq, ElevenLabs, Pexels, Cloudflare and Hugging Face. They need your API keys. Run one real build per provider and check the `build_report.json`.

## Known limits of the prototype

- Fonts are system fonts (Arial Black). The product's Linux render servers need bundled fonts (for example `@remotion/google-fonts`).
- Uploaded voiceovers and Whisper transcription are not in the prototype yet.
- Rendering copies the whole `public/` folder into each bundle; the product will load assets from S3 URLs instead.
