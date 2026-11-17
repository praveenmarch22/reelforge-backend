# PRD: ReelForge

**AI video studio for solo creators: idea → script → voice → visuals → captions → post to Instagram, in one place.**

| | |
|---|---|
| Version | 1.0 (draft) |
| Date | 2026-11-10 |
| Owner | Praveen |
| Status | Agreed for version 1 (2026-11-10) |
| Project type | Learning project, built as a real SaaS product |

---

## 1. Summary

ReelForge lets a solo creator turn a topic into a finished vertical video of up to 90 seconds and post it to Instagram as a Reel without leaving the app.

The creator can choose between two ways of making the visuals:

- **Faceless mode**: stock video clips and AI images, animated with motion graphics. Free to produce, fast, and reliable.
- **Full AI mode**: every scene is an AI-generated video clip, with a mood or emotion set per scene. Costs more credits, slower, and the quality varies.

Both modes share the same script, voiceover, music, captions, editor and posting features. The creator can also mix them: any single scene can use stock, an AI image, an AI clip, or their own upload.

## 2. Goals

### Product goals

1. A creator can go from a topic to a posted Reel in under 15 minutes of their own time.
2. The creator can regenerate any part (script, a scene, the voice, the music) without redoing everything else.
3. Every action that costs money is tracked in credits, and a creator cannot exceed their allowance.

### Learning goals (for the builder)

1. Integrate several third-party AI APIs behind a common interface, with retries and fallbacks.
2. Run long jobs in the background and show live progress in the UI.
3. Build a multi-service system: React frontend, Flask API, Celery workers, and a Node render service.
4. Implement OAuth with a real platform (Instagram).
5. Handle files at scale: upload, storage, public URLs, and cleanup.

## 3. Non-goals for version 1

- Videos longer than 90 seconds.
- Teams, agencies, workspaces or approval flows.
- Talking avatars or lip-synced faces.
- Voice cloning.
- Scheduling posts and a content calendar.
- Analytics from Instagram.
- Brand kit.
- YouTube Shorts, TikTok, Facebook or LinkedIn.
- Real payments (credits are a fixed monthly allowance; Stripe test mode can be added at the end).
- Mobile app (the web app should still work on a phone browser).

## 4. Target user

**Solo creator**, posting 3–7 Reels a week in a niche such as fitness, finance tips, motivation, facts, tech or food.

- Has an Instagram **Business or Creator** account (required by Instagram's API).
- Not a video editor; wants results quickly.
- Cares about the hook in the first 3 seconds, readable captions, and posting consistently.

**Main jobs to be done**

1. "Give me ideas for my niche when I'm stuck."
2. "Turn my idea into a ready Reel without editing for an hour."
3. "Let me fix the part I don't like without starting over."
4. "Post it for me."

## 5. Feature list

Priority: **P0** = required for version 1, **P1** = in version 1 if time allows, **P2** = later.

### 5.1 Account

| ID | Feature | Priority |
|---|---|---|
| A1 | Sign up with email and password, with email verification | P0 |
| A2 | Log in, log out, forgot and reset password | P0 |
| A3 | Sign in with Google | P2 (not in version 1) |
| A4 | Profile: name, niche, default language, default voice, default caption style, default mode | P0 |
| A5 | Delete account and all data | P1 |

### 5.2 Ideas and script

| ID | Feature | Priority |
|---|---|---|
| S1 | Generate 5 video ideas from a niche and optional keywords | P0 |
| S2 | Enter your own topic or a full script instead | P0 |
| S3 | Settings: tone (educational, funny, motivational, storytelling, dramatic), target length (15, 30, 60 or 90 seconds), mode. Version 1 is English only. | P0 |
| S4 | AI writes a script split into scenes, each with: narration text, on-screen text, visual description, mood/emotion, and estimated duration | P0 |
| S5 | Edit any scene's text, add, delete or reorder scenes | P0 |
| S6 | Regenerate the whole script, or rewrite a single scene ("make this punchier") | P0 |
| S7 | Live word count and estimated duration, with a warning when over the target length | P0 |
| S8 | Hook suggestions: 3 alternative opening lines to choose from | P1 |

### 5.3 Voiceover and audio

| ID | Feature | Priority |
|---|---|---|
| V1 | Choose a voice from a curated list, with a preview of each | P0 |
| V2 | Generate the voiceover from the script with ElevenLabs, per scene | P0 |
| V3 | Emotion in the voice: each scene's mood adjusts the voice delivery (voice settings and, where the model supports it, emotion tags) | P0 |
| V4 | Regenerate the voiceover for the whole video or for one scene | P0 |
| V5 | Change voice speed | P1 |
| V6 | Upload your own voiceover (MP3, WAV or M4A) for the whole video | P0 |
| V7 | Choose background music from a library filtered by mood, or upload your own track | P0 |
| V8 | Regenerate or swap the music without touching anything else | P0 |
| V9 | Audio mix: separate volume for voice and music, automatic lowering of music while the voice speaks, fade in and fade out | P0 |
| V10 | No-voice option: music and text only | P1 |

### 5.4 Visuals

| ID | Feature | Priority |
|---|---|---|
| VI1 | Each scene has a visual source: **stock video**, **AI image**, **AI video clip**, or **upload** | P0 |
| VI2 | Faceless mode: automatically pick a vertical stock clip per scene from Pexels, falling back to an AI image when nothing fits | P0 |
| VI3 | Full AI mode: generate an AI video clip for every scene, with mood and emotion written into the prompt | P0 |
| VI4 | Style lock for Full AI mode: generate one reference image (character and look) first and use it for every scene, so the scenes look consistent | P0 |
| VI5 | Per-scene options: swap the clip, search stock manually, regenerate the AI image or clip, edit the visual prompt, or upload your own image or video | P0 |
| VI6 | Animate still images with slow zoom and pan so they feel like video | P0 |
| VI7 | Visual style presets for AI images and clips (cinematic, anime, 3D, realistic, illustration) | P1 |

### 5.5 Captions and text overlays

| ID | Feature | Priority |
|---|---|---|
| C1 | Auto-captions synced word by word to the voiceover | P0 |
| C2 | 3–5 caption style presets (font, size, colour, highlight on the active word, position) | P0 |
| C3 | Edit caption text to fix a word without regenerating the voice | P0 |
| C4 | Text overlays per scene (the on-screen text from the script), with simple entrance animations | P0 |
| C5 | Turn captions off | P0 |
| C6 | Emoji in captions | P1 |

### 5.6 Editor and preview

| ID | Feature | Priority |
|---|---|---|
| E1 | Live preview in the browser, before rendering, using the Remotion player | P0 |
| E2 | Scene timeline: reorder, trim, delete, duplicate scenes | P0 |
| E3 | Transitions between scenes (cut, fade, slide) | P1 |
| E4 | Undo and redo | P1 |
| E5 | Autosave | P0 |

### 5.7 Rendering

| ID | Feature | Priority |
|---|---|---|
| R1 | Render the final MP4: 1080×1920, 30 fps, H.264 video, AAC audio | P0 |
| R2 | Progress bar for the render | P0 |
| R3 | Download the MP4 | P0 |
| R4 | Notification (in app, and email P1) when a render finishes or fails | P0 |

### 5.8 Instagram

| ID | Feature | Priority |
|---|---|---|
| I1 | Connect one Instagram Business or Creator account through Instagram Login | P0 |
| I2 | Show connected account (username, profile picture), with disconnect | P0 |
| I3 | AI-generated post caption and hashtags, editable | P0 |
| I4 | Choose whether the Reel also appears on the main profile grid | P1 |
| I5 | Post now, with live status (uploading, processing, published, failed) and a link to the published Reel | P0 |
| I6 | Choose a cover frame | P1 |
| I7 | Schedule a post | P2 |

### 5.9 Dashboard

| ID | Feature | Priority |
|---|---|---|
| D1 | List of projects with thumbnail, title, mode, length and status (draft, generating, ready, rendering, rendered, posting, posted, failed) | P0 |
| D2 | Filter and search projects | P1 |
| D3 | Duplicate a project as a starting point | P1 |
| D4 | Delete a project | P0 |
| D5 | Credits panel: remaining credits, reset date, and history of what used them | P0 |

### 5.10 Credits

| ID | Feature | Priority |
|---|---|---|
| CR1 | Every account gets a monthly credit allowance | P0 |
| CR2 | Show the credit cost before every action that spends credits | P0 |
| CR3 | Reserve credits when a job starts, charge when it succeeds, refund when it fails | P0 |
| CR4 | Block actions when credits run out, with a clear message | P0 |
| CR5 | Paid plans with Stripe test mode | P2 |

**Starting credit costs (to tune later)**

| Action | Credits |
|---|---|
| Generate ideas | 0 |
| Generate or rewrite a script | 1 |
| Voiceover, per 30 seconds of audio | 2 |
| Stock clip | 0 |
| AI image | 1 |
| AI video clip (about 5 seconds) | 10 |
| Render | 2 |
| Post to Instagram | 0 |

Monthly allowance: **100 credits**. During testing, accounts get a larger allowance (for example 500) so Full AI mode is fully usable; the admin can adjust this (AD3). A faceless 60-second Reel costs about 10–15 credits. A Full AI 60-second Reel (about 10 clips) costs about 110, so it needs a few scenes in faceless style or a larger allowance. These numbers are deliberate: they show the creator what AI video really costs.

### 5.11 Admin (internal, for you)

| ID | Feature | Priority |
|---|---|---|
| AD1 | Simple admin page: users, jobs, failures, credit usage per provider | P1 |
| AD2 | Kill switch per provider (turn off AI video if the free quota runs out) | P0 |
| AD3 | Adjust a user's credits | P1 |

## 6. User flows

### Flow 1: Sign up and onboarding

1. The visitor opens the landing page and clicks **Get started**.
2. Enters email and password. The system sends a verification email.
3. Clicks the link. The account is active and gets 100 credits.
4. Onboarding, 3 short steps (all skippable):
   1. Pick a niche and language.
   2. Pick a default voice (with previews).
   3. Connect Instagram, or **Skip for now**.
5. Lands on the empty dashboard with a **Create your first Reel** button.

### Flow 2: Connect Instagram

1. From onboarding or Settings, the creator clicks **Connect Instagram**.
2. The app explains the requirement: a Business or Creator account. A link explains how to switch for free.
3. The creator is redirected to Instagram, logs in and approves the permissions.
4. Instagram redirects back with a code. The backend exchanges it for a short-lived token, then a long-lived token (about 60 days), and stores it encrypted.
5. The backend fetches the account type and username.
   - Business or Creator: show **Connected as @username**.
   - Personal account, or the permissions were refused: show the reason and how to fix it. Store nothing.

### Flow 3: Create a Reel, faceless mode (main flow)

1. Dashboard → **New Reel**.
2. **Idea step**
   - Option A: enter a topic.
   - Option B: click **Give me ideas**, get 5 ideas for the niche, pick one, or click **More ideas**.
   - Option C: paste a full script.
3. **Settings**: tone, length, language, mode = Faceless, voice.
4. **Script step**: the AI returns scenes. The creator reviews them, edits text, reorders, rewrites single scenes, and sees the estimated duration. Clicks **Looks good**.
5. **Generation**: the app shows the credit cost and the creator confirms. Background jobs start in parallel:
   - voiceover per scene (ElevenLabs, with timestamps);
   - a visual per scene (Pexels search, or an AI image as fallback);
   - music matched to the overall mood.
   The page shows progress per scene. The creator can leave and come back.
6. **Editor step**: the live preview plays the full Reel. The creator can:
   - swap or regenerate any scene's visual;
   - regenerate one scene's voice, or change the voice for all;
   - swap the music, change volumes;
   - change caption style, fix caption words, edit overlays;
   - reorder, trim or delete scenes.
7. **Render**: the creator clicks **Render**, sees the cost, confirms. A progress bar runs. When done, the video plays and can be downloaded.
8. **Post**: covered in Flow 6.

### Flow 4: Create a Reel, Full AI mode

Same as Flow 3, except:

1. At settings, the creator chooses **Full AI**, a visual style, and optionally describes a main character ("a young woman runner in a red jacket").
2. The script includes a mood per scene (for example: calm, tense, joyful, sad, triumphant) and a detailed visual prompt.
3. **Style lock step**: the app generates one reference image (1 credit). The creator approves it or regenerates. This image anchors the look of every scene.
4. **Generation**: for each scene, an AI clip is generated from the reference image plus the scene prompt plus the mood (image-to-video). The total cost is shown first, with a warning that clips take several minutes each.
5. Clips shorter than their scene's narration are handled automatically (see edge case G7).
6. In the editor, any scene whose clip failed or looks wrong can be regenerated, or switched to stock or an AI image to save credits.

### Flow 5: Change the audio

1. In the editor, the creator opens the **Audio** panel.
2. **Change the voice**: picks another voice, sees the cost, confirms. All scenes regenerate. Captions re-sync automatically.
3. **Regenerate one scene**: clicks regenerate on a scene's voice. Only that scene changes. Timing of later scenes shifts if the length changed.
4. **Upload own voiceover**: uploads a file. The system checks format and length, transcribes it to get word timings for captions, and fits scenes to the new timing.
5. **Music**: browse by mood and preview, or upload a track. The music loops or trims to the video length with a fade-out.
6. **Mix**: voice volume, music volume, music lowering on or off. The preview updates immediately.

### Flow 6: Post to Instagram

1. On a rendered project, the creator clicks **Post to Instagram**.
2. If no account is connected, they go through Flow 2 and come back here.
3. The post screen shows the video, an AI-generated caption and hashtags (editable), and options (show on grid, cover frame).
4. The creator clicks **Post now**.
5. Backend:
   1. Makes sure the MP4 is available at a public URL that stays valid for at least a few hours.
   2. Creates a Reel media container on Instagram with the video URL and caption.
   3. Polls the container status until it is finished, failed, or timed out.
   4. Publishes the container.
   5. Saves the Instagram media ID and permalink.
6. The UI shows each stage, then **Published** with a link to the Reel. The project status becomes **posted**.
7. On failure, the UI shows a plain-language reason and a **Retry** button. The rendered video is never lost.

### Flow 7: Credits run out

1. The creator starts an action that costs more credits than they have.
2. The action is blocked before any job starts. The message shows how many credits are needed, how many are left, and the reset date.
3. Suggestions: switch expensive scenes to stock or AI images. (Later: buy credits.)

### Flow 8: Return to a project

1. From the dashboard the creator opens any project.
2. The app opens at the right step: script step for a draft, editor for a generated project, the result screen for a rendered or posted one.
3. If jobs are still running, progress is shown live.

## 7. Video generation pipeline

### 7.1 Steps

```
Topic / ideas ─► Script (LLM, structured JSON: scenes)
                    │
                    ▼
        ┌───────────┼────────────────────┐
        ▼           ▼                    ▼
   Voiceover    Visuals per scene      Music
   per scene    (stock / AI image /    (library by mood
   (ElevenLabs   AI clip / upload)      or upload)
   + word
   timings)
        └───────────┼────────────────────┘
                    ▼
        Timeline (JSON describing every scene, asset,
        caption word, overlay and audio level)
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
   Browser preview      Final render
   (Remotion Player)    (Remotion on the Node render service)
                              │
                              ▼
                       MP4 in storage ─► Instagram
```

### 7.2 The timeline is the centre of the system

The timeline is one JSON document per project. The editor changes it, the preview plays it, and the render service renders it. This keeps preview and final video identical.

Each scene in the timeline holds:

- order and duration (set by the voiceover length, plus a little padding);
- narration text and word timings;
- on-screen text and its animation;
- mood;
- visual: source type, asset URL, prompt, and motion (zoom or pan) for still images;
- transition to the next scene.

Project-level fields: voice ID, music asset, volume levels, caption style, aspect ratio, frame rate.

### 7.3 Scene duration rules

- Narration speed is about 2.5 words per second. A 90-second Reel is about 225 words.
- A scene lasts as long as its voiceover plus 0.3 seconds, with a minimum of 1.5 and maximum of 15 seconds.
- Total length must not exceed 90 seconds. If it does, the creator is asked to shorten the script before rendering.

### 7.4 How mood and emotion are used

The mood of each scene affects four things:

| Part | Effect |
|---|---|
| Voice | Voice settings (stability, style) and, where the model supports it, emotion tags such as excited or whispering |
| AI visuals | Added to the prompt: facial expression, lighting, colour, camera movement |
| Music | Default music track is chosen by the overall mood |
| Captions | Optional emphasis styling on key words for intense moods (P1) |

### 7.5 Jobs and states

Every generation step is a background job with its own record.

| Job state | Meaning |
|---|---|
| queued | Waiting for a worker |
| running | In progress, with percentage when known |
| succeeded | Output saved |
| failed | Failed after retries, with an error code and message |
| cancelled | Cancelled by the creator or because the project was deleted |

Project states: `draft → generating → ready → rendering → rendered → posting → posted`, with `failed` possible from generating, rendering and posting. A project can go back from `rendered` to `ready` when the creator edits it again.

## 8. Tools and APIs

| Purpose | Version 1 choice | Free option | Notes |
|---|---|---|---|
| Ideas, scripts, captions, hashtags | Google Gemini API | Free tier | Use structured JSON output for scenes. Groq as fallback. |
| Voiceover and word timings | ElevenLabs (text-to-speech with timestamps) | Free tier, about 10 minutes a month, non-commercial, attribution | Timings drive the word-by-word captions. |
| Transcribing uploaded voiceovers | Groq's hosted Whisper API | Free tier | Needed for captions on uploaded audio. |
| Stock video and images | Pexels API | Free | Pixabay as fallback. Search with orientation = portrait. |
| AI images | Cloudflare Workers AI (FLUX) | Daily free allowance | Hugging Face as fallback. |
| AI video clips | Whichever free model works best when milestone 10 starts (for example Wan or LTX-Video on Hugging Face Spaces) | Hugging Face Spaces free with queue | Quality is accepted as is for version 1. Behind a provider interface so a paid model (Kling, Veo, fal.ai) can replace it later. |
| Music | A small self-hosted library tagged by mood, plus Jamendo API | Free (non-commercial) | Store license info per track. |
| Rendering and motion graphics | Remotion | Free for individuals and very small companies | Runs in a Node service. Same components power the browser preview. |
| Instagram publishing | Instagram API with Instagram Login (content publishing) | Free | Business or Creator accounts only. Development mode works for your own accounts without App Review. |
| File storage | Amazon S3 (production is on AWS) | AWS free tier or credits | Cloudflare R2 can be used during development; it has the same API as S3. Signed URLs for Instagram. |
| Email | Resend or Brevo | Free tier | Verification, password reset, notifications. |

Check every free tier's current limits when signing up; they change often.

### 8.1 Provider interface

Each AI category (LLM, voice, image, video, stock, transcription) has a small Python interface with one or more implementations, for example:

```
class VideoProvider:
    def generate(self, prompt, reference_image_url, duration_s, aspect_ratio) -> JobHandle
    def status(self, handle) -> Status
    def result_url(self, handle) -> str
```

Providers are chosen by configuration, with a fallback order. This lets you swap free and paid providers without changing the rest of the code, and is the main integration skill this project teaches.

## 9. System architecture

| Component | Technology | Responsibility |
|---|---|---|
| Frontend | React (Vite), TypeScript, Remotion Player | Dashboard, script editor, timeline editor, preview, posting screen |
| API | Python, Flask | Auth, projects, timeline, credits, Instagram OAuth, job creation |
| Workers | Celery with Redis | All calls to AI providers, stock search, Instagram publishing |
| Render service | Node.js with Remotion | Renders the timeline JSON to MP4 and uploads to storage |
| Database | PostgreSQL | All persistent data |
| Queue and cache | Redis | Job queue, progress updates, rate limiting |
| Storage | Amazon S3 (R2 allowed in development) | Audio, images, clips, final videos, thumbnails |
| Hosting | AWS (services chosen in the design document) | Production environment |
| Live updates | Server-sent events or polling | Job progress in the UI |

**Security basics**

- All API keys live on the server only, never in the frontend.
- Instagram tokens are encrypted in the database.
- Passwords are hashed (bcrypt or argon2).
- Rate limits on login, idea generation and job creation per user.
- Uploaded files are checked for type and size and stored with random names.

## 10. Data model (main entities)

| Entity | Key fields |
|---|---|
| User | id, email, password hash, email verified, name, niche, language, defaults, created at |
| InstagramAccount | id, user id, Instagram user id, username, account type, encrypted token, token expiry, status |
| Project | id, user id, title, mode, tone, target length, language, status, timeline JSON, created and updated at |
| Scene | id, project id, order, narration, on-screen text, visual prompt, mood, visual source, duration |
| Asset | id, user id, project id, type (voice, music, image, clip, upload, render, thumbnail), storage key, duration, size, source provider, license info |
| Job | id, project id, scene id, type, provider, state, progress, attempts, error code, error message, credits reserved, started and finished at |
| Render | id, project id, timeline version, asset id of the MP4, state |
| Post | id, project id, Instagram account id, caption, container id, media id, permalink, state, error |
| CreditLedger | id, user id, amount (+ or −), reason, job id, created at |

Credits balance = sum of the ledger, so every change is traceable.

## 11. Edge cases and how to handle them

### 11.1 Account and auth

| # | Case | Handling |
|---|---|---|
| A-1 | Email already registered | Show "account exists", offer login or password reset. Don't reveal more than that on the reset page. |
| A-2 | Verification link expired or used twice | Show a clear message and a **Resend** button. |
| A-3 | Many failed logins | Rate limit and temporary lockout. |
| A-4 | Session expires during editing | Autosave first; after re-login, return to the same project. |
| A-5 | Account deleted while jobs are running | Cancel jobs, delete files and tokens. |

### 11.2 Script

| # | Case | Handling |
|---|---|---|
| S-1 | LLM returns invalid JSON or the wrong structure | Validate against a schema, retry up to 2 times, then show an error with **Try again**. No credits charged. |
| S-2 | Script too long for the target length | Warn with the estimated duration; offer **Shorten with AI**. Block rendering above 90 seconds. |
| S-3 | Script too short (under 5 seconds) | Warn; allow it. |
| S-4 | Topic violates content rules (hate, sexual content, violence, real-person deception) | The LLM or a moderation check refuses; show a neutral message. Log it. |
| S-5 | Topic in a language the voice doesn't support | Restrict voices to those supporting the chosen language. |
| S-6 | User pastes a very long script | Hard limit on characters; suggest splitting into several Reels. |
| S-7 | User edits the script after audio was generated | Mark affected scenes' audio as outdated; ask to regenerate only those scenes. |

### 11.3 Generation (voice, visuals, AI video)

| # | Case | Handling |
|---|---|---|
| G-1 | Provider timeout or 5xx error | Retry with backoff (3 attempts), then try the fallback provider, then fail the scene only. |
| G-2 | Provider rate limit (429) | Wait and retry using the provider's retry header; queue rather than fail. |
| G-3 | Provider free quota exhausted for the whole app | Admin kill switch; UI shows that the option is temporarily unavailable; suggest another visual source. |
| G-4 | Provider refuses the prompt (safety filter) | Mark the scene failed with "prompt rejected"; let the creator edit the prompt. Refund credits. |
| G-5 | Some scenes succeed and some fail | Keep the successful ones; show the failed ones with **Retry**. The creator can preview and edit with placeholders but cannot render until every scene has a visual and audio. |
| G-6 | No stock result for a scene | Retry with simpler keywords from the LLM, then fall back to an AI image. |
| G-7 | AI clip shorter than the scene narration | In order: slow the clip slightly (down to 0.8× speed), then hold the last frame with a slow zoom, or generate a second clip for the scene if the creator chooses. |
| G-8 | AI clip longer than the scene | Trim to scene length. |
| G-9 | AI clip in the wrong aspect ratio | Crop or scale to fill 9:16. |
| G-10 | AI clip looks broken (distorted faces, flicker) | Not detectable automatically in version 1; the creator regenerates or switches the scene to another source. |
| G-11 | AI video takes very long (over 10 minutes) | Job timeout; mark failed and refund; offer retry. |
| G-12 | Creator clicks Generate twice | Ignore duplicates: one active job per scene and job type. |
| G-13 | Creator leaves the page during generation | Jobs continue on the server; progress is shown on return; notification when done. |
| G-14 | Creator deletes the project during generation | Cancel the jobs, refund unspent credits, delete files. |
| G-15 | Word timings missing from the voice provider | Fall back to transcribing the generated audio with Whisper. |

### 11.4 Audio

| # | Case | Handling |
|---|---|---|
| AU-1 | Uploaded file is the wrong format, corrupt or silent | Validate with FFmpeg before accepting; show a clear error. |
| AU-2 | Uploaded voiceover longer than 90 seconds | Reject, or let the creator trim it in the editor before continuing. |
| AU-3 | Uploaded voiceover doesn't match the scene text | Scene timing follows the transcription; on-screen captions use the transcription, not the script. |
| AU-4 | Music shorter than the video | Loop with a crossfade. |
| AU-5 | Music longer than the video | Trim with a fade-out. |
| AU-6 | Uploaded music may be copyrighted | Show a notice that the creator is responsible for rights, and that Instagram may mute it. |
| AU-7 | Voice and music volumes produce clipping | Normalise loudness in the render (target around −14 LUFS). |
| AU-8 | Regenerated scene audio changes length | Shift later scenes; keep captions in sync automatically. |

### 11.5 Captions and text

| # | Case | Handling |
|---|---|---|
| CA-1 | Long words overflowing the screen | Auto-shrink font or wrap within safe margins. |
| CA-2 | Captions covered by Instagram's interface (buttons on the right, caption at the bottom) | Keep text inside the Reels safe zone; show the safe zone in the preview. |
| CA-3 | Emoji or non-Latin scripts not rendering | Use fonts that support the chosen language; include an emoji font. |
| CA-4 | Creator edits a caption word, then regenerates the voice | Warn that manual caption edits for that scene will be replaced. |

### 11.6 Rendering

| # | Case | Handling |
|---|---|---|
| R-1 | Render fails | Retry once automatically; then fail with a reason; refund credits. |
| R-2 | An asset URL is missing or expired at render time | Check all assets before starting; re-sign URLs. |
| R-3 | Creator edits while a render is running | The render uses the timeline version it started with; the result is marked outdated if the timeline changed. |
| R-4 | Several renders at once from one user | Limit to one active render per user; queue the rest. |
| R-5 | Output file too large for Instagram | Use a target bitrate that keeps 90 seconds well under Instagram's limit. |

### 11.7 Instagram

| # | Case | Handling |
|---|---|---|
| IG-1 | Personal account | Block at connect time with instructions to switch to Creator or Business. |
| IG-2 | Token expired or revoked | Refresh long-lived tokens before expiry with a scheduled job; if refresh fails, mark disconnected and ask to reconnect. The post waits as a draft. |
| IG-3 | Container processing fails (bad format or length) | Show Instagram's error in plain words; offer re-render and retry. |
| IG-4 | Container processing takes long | Poll with backoff up to a timeout (about 10 minutes), then fail with retry. |
| IG-5 | Daily publishing limit reached | Check the account's remaining quota before posting; show when posting will be possible again. |
| IG-6 | Caption over 2,200 characters or more than 30 hashtags | Validate in the post screen before sending. |
| IG-7 | Creator clicks Post twice | One post per render at a time; the second click is ignored. |
| IG-8 | Publish succeeded but the app crashed before saving | On retry, check recent media on the account for a match before publishing again. |
| IG-9 | Storage URL not reachable by Instagram | Use a public or signed URL valid for at least several hours; verify it returns the file before creating the container. |
| IG-10 | Creator disconnects Instagram after posting | Keep the post history; remove the token. |
| IG-11 | App still in Meta development mode | Only accounts added as testers can connect; show a clear message to others. |

### 11.8 Credits

| # | Case | Handling |
|---|---|---|
| CR-1 | Not enough credits for the whole batch | Block before starting; never start a partial batch silently. |
| CR-2 | Job fails | Refund the reserved credits automatically. |
| CR-3 | Two actions at once that together exceed the balance | Reserve credits inside a database transaction so the balance can't go negative. |
| CR-4 | Monthly reset | Scheduled job adds the allowance; unused credits don't carry over. |

### 11.9 Storage and files

| # | Case | Handling |
|---|---|---|
| F-1 | Upload too large | Limits: 200 MB per video, 50 MB per audio file, 20 MB per image. |
| F-2 | Unused generated assets pile up | Delete replaced assets after 7 days; delete everything when a project is deleted. |
| F-3 | Free storage limit approaching | Per-user storage limit; admin warning. |

## 12. Non-functional requirements

| Area | Requirement |
|---|---|
| Speed | Ideas and script in under 15 seconds. Faceless generation for a 60-second Reel in under 3 minutes. Render in under 3 minutes. |
| Reliability | No credits lost on failure. No duplicate Instagram posts. |
| Video output | 1080×1920, 30 fps, H.264, AAC, under 100 MB for 90 seconds. |
| Browsers | Latest Chrome, Edge, Safari and Firefox; usable on phone width. |
| Privacy | Creator content is private. Tokens encrypted. Account deletion removes all data. |
| Observability | Every job logs provider, duration, cost and result. Errors are reported to a tool such as Sentry (free tier). |

## 13. Metrics

For a learning project these show whether the product works:

- Time from new project to rendered video.
- Percentage of projects that reach rendered, and that reach posted.
- Regenerations per project (high means the first result is poor).
- Job failure rate per provider.
- Credits used per rendered video, faceless versus Full AI.

## 14. Build plan (milestones)

| # | Milestone | Done when |
|---|---|---|
| 1 | Setup | React app, Flask API, PostgreSQL, Redis, Celery and S3 (or R2) talk to each other locally |
| 2 | Accounts | Sign up, verify, log in, reset password, profile |
| 3 | Ideas and script | Ideas and scene-based scripts from Gemini, editable |
| 4 | Voice | ElevenLabs voiceover per scene with word timings, voice choice, regeneration |
| 5 | Faceless visuals | Pexels clips and FLUX images per scene, swap and upload |
| 6 | Preview | Remotion player shows the full Reel with captions, overlays and music |
| 7 | Render | Node render service produces the MP4 and stores it |
| 8 | Instagram | Connect account and post a Reel to your own account |
| 9 | Credits and dashboard | Ledger, costs shown before actions, refunds, dashboard |
| 10 | Full AI mode | Reference image, image-to-video clips with mood, duration fixing |
| 11 | Audio extras | Uploads, transcription, music library and mixing |
| 12 | Hardening | Edge cases in section 11, rate limits, cleanup jobs, error tracking |

Milestones 1–9 give a complete faceless product. Milestone 10 adds Full AI video.

## 15. Risks

| Risk | Mitigation |
|---|---|
| Free tiers change or run out | Provider interface with fallbacks; admin kill switch |
| AI video quality is poor | Accepted for version 1; creators can mix in stock or images per scene |
| Instagram App Review needed for public users | Build and demo in development mode; apply for review once the product is stable |
| ElevenLabs free tier is non-commercial | Fine for learning; switch to a paid plan or another provider before charging users |
| Music copyright | Use only licensed tracks with stored license info |
| Render service is slow on a basic server | One render at a time per user, queue, and later move rendering to a cloud function (Remotion Lambda) |

## 16. Decisions

| Question | Decision |
|---|---|
| Product name | ReelForge. The domain is handled separately. |
| Languages | English only in version 1. |
| Full AI mode during testing | Usable: test accounts get a larger credit allowance. |
| AI video model | Use whichever free model works; choose a better or paid one later. |
| Hosting | AWS. Specific services are decided in the design document. |
