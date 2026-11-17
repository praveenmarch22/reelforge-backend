# ReelForge: generation pipeline strategy

This document records how ReelForge turns a script into a finished Reel. The prototype in this repository implements it, and the product will reuse the same code: the Python pipeline becomes the Celery workers' core, and the Remotion renderer becomes the render service.

These are the default decisions. Each one can be changed later without rewriting the rest, because every step talks to the others only through files and the timeline JSON.

## The 8 decisions

### 1. Order of generation: voice first

1. Write the script (scenes).
2. Generate the voiceover for every scene.
3. The voiceover length sets each scene's duration.
4. Generate or find visuals that fit each scene's duration.
5. Choose music.
6. Build the timeline.

Why: narration is what the viewer follows, so it must never be cut or rushed. Visuals are easier to stretch, loop, trim or hold than speech.

### 2. Parallel, with a limit per provider

Scenes are generated at the same time, but each provider has its own limit on simultaneous requests (default 2, set in `.env`). This keeps free tiers from returning "too many requests" errors while still being much faster than one scene at a time. In the product, Celery workers and per-provider rate limits do the same job.

### 3. Fitting a video clip to its scene

| Clip vs scene | What happens |
|---|---|
| Clip is longer | Trimmed to the scene length |
| Clip is a little shorter | Slowed down, to no less than 0.8× speed |
| Clip is much shorter | Slowed to 0.8×, then the last frame is held with a slow zoom so it doesn't look frozen |
| Clip length unknown | Treated as long enough |

Stills (AI images, uploaded images) always get a slow zoom or pan for the whole scene.

### 4. Consistent look in Full AI mode

Free video models can't reliably keep a character consistent. The prototype uses the cheapest techniques that help:

- one **style anchor** (visual style plus character description) appended to every image prompt;
- the same **seed** for every scene where the provider supports seeds;
- **image-to-video**: each scene first gets a keyframe image with the style anchor, and the video model animates that image.

True reference-image conditioning (one approved image steering every scene, PRD feature VI4) needs a model that accepts reference images. It is deferred until a paid or better model is chosen.

### 5. Audio mixing happens in Remotion

Voice and music are separate files. Remotion mixes them while rendering:

- voice at full volume per scene;
- music looped across the whole video at a base volume;
- music lowered ("ducked") while the voice is speaking, with short ramps so it doesn't jump;
- music fades in at the start and out at the end.

No separate audio-mixing step is needed, so changing a volume never needs regeneration, only a re-render.

### 6. Preview equals render

The browser preview (Remotion Player, in the product) and the final render (Remotion renderer) use the same React components and the same timeline JSON. What the creator sees is what gets posted.

### 7. Partial regeneration through a cache

Every generated file is stored with a hash of the inputs that produced it (text, voice, mood, prompt, provider). When the pipeline runs again:

| You change | What is regenerated |
|---|---|
| One scene's narration | That scene's voice. Its duration and later scenes' start times are recalculated (cheap). |
| One scene's visual prompt or source | That scene's visual only |
| The voice | Every scene's voice |
| The style or character (Full AI) | Every AI visual |
| Volumes, caption style, scene order | Nothing; only the timeline and render |

To force a fresh result for something whose inputs didn't change, delete its entry with `--regenerate <scene-id>` (see the README).

### 8. Failures

- Every provider call is retried up to 3 times with growing delays. "Too many requests" respects the provider's suggested wait.
- If a provider still fails, the next provider in that step's fallback list is tried, for example ElevenLabs then Edge TTS.
- Last-resort fallbacks keep the video possible: stock clip → AI image; AI video → AI image with motion.
- If a scene still fails, only that scene is marked failed. Everything that succeeded is kept in the cache, the build report lists what failed and why, and rendering is blocked until every scene has a voice and a visual.
- Content refusals (a provider's safety filter) are not retried; the report says to change the prompt.

## The timeline JSON

The timeline is the contract between the pipeline and the renderer. It is written to `renderer/public/projects/<project-id>/timeline.json`.

```json
{
  "version": 1,
  "projectId": "demo",
  "fps": 30, "width": 1080, "height": 1920,
  "durationInFrames": 900,
  "captionStyle": "bold-pop",
  "showCaptions": true,
  "voiceVolume": 1.0,
  "music": { "src": "projects/demo/music.wav", "volume": 0.25, "duckedVolume": 0.08,
             "fadeInSeconds": 1.0, "fadeOutSeconds": 1.5 },
  "scenes": [
    {
      "id": "s1",
      "startFrame": 0,
      "durationInFrames": 120,
      "mood": "excited",
      "onScreenText": "3 habits that changed my mornings",
      "visual": { "kind": "video", "src": "projects/demo/visual_s1.mp4",
                  "durationSeconds": 3.2, "motion": "zoom-in" },
      "voice": { "src": "projects/demo/voice_s1.mp3", "durationSeconds": 3.7 },
      "words": [ { "text": "Three", "start": 0.0, "end": 0.32 } ]
    }
  ]
}
```

Word times are in seconds from the start of their scene. Asset `src` values are paths inside `renderer/public`, or full `https://` URLs (which the product will use with S3).
