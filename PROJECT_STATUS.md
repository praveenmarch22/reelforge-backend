# ReelForge: project status

**Last updated:** 2026-11-26

This is the single place to see where the project stands. It is updated at the end of every working session. The milestone definitions are in [MILESTONES.md](MILESTONES.md).

## Where we are

**Planning and design are finished. The frontend is under way: F1–F3 are done (setup, sign-in screens, dashboard and projects library), running against a mock API, and they have been through a QA review and automated testing with all findings fixed or scheduled. F4 (onboarding) is done too, after three test rounds. The backend is built one task at a time: B1 (setup), B2 (accounts) and B3 (projects, scripts, scenes, jobs and credits) are done, and so are B4 (generation: voiceovers, visuals, music, Generate Reel and the preview timeline) and B5 (rendering the final MP4 with a Remotion render service), each reviewed by QA and covered by automated tests. Scripts are written by real AI models (Gemini, with Groq as backup); voiceovers by ElevenLabs, with the free Edge voices as backup; visuals are Pixabay stock clips and FLUX images on Cloudflare; music comes from a 15-track CC BY library.**

Next up: **F7** (render and publish screens, spec [SCREENS_F7.md](docs/SCREENS_F7.md)), built against the real backend; then Instagram (B6b), and B7, B8, F8. F5 (New Reel, Script editor) and F6 (Reel editor with the live preview) are done and talk to the real backend (`npm run dev:real`): a Reel can be written, generated, previewed and edited on screen and **F5 New Reel and script editor** on the frontend. The web app still runs on its mock API; connecting it to the real one is milestone I1.

## Milestones

| # | Milestone | Status | Notes |
|---|---|---|---|
| P1 | Product requirements | Done | [PRD.md](docs/PRD.md) |
| P2 | Pipeline prototype | Done | Works offline and with Edge TTS. Gemini, ElevenLabs, Pexels, Cloudflare and Hugging Face are untested (need API keys) |
| P3 | Technical, API and database design | Done | 70 endpoints, 16 tables; schema tested on PostgreSQL 16 |
| P4 | UI design | Done | 22 screens in Stitch; review notes in [UI_REVIEW.md](docs/UI_REVIEW.md) |
| F1 | Frontend setup and design system | Done | In the reelforge-frontend repository; type-check and production build pass |
| F2 | Sign-in screens | Done | QA reviewed; covered by automated tests |
| F3 | App shell, dashboard and projects | Done | QA reviewed; covered by automated tests. Seed data has 6 of the 11 statuses; the rest arrive with F5-F7 |
| F4 | Onboarding | Done | QA reviewed; covered by automated tests. See "F4 result" below |
| F5 | New Reel and script editor | Done | Against the real backend (`npm run dev:real`); four review rounds |
| F6 | Reel editor | Done | Live Remotion preview using the renderer's own code; four review rounds |
| F7 | Render and publish | Not started | |
| F8 | Settings, credits and admin | Not started | |
| F9 | Frontend hardening | Not started | |
| B1 | Backend setup | Done | Flask API, Postgres (16 tables from the baseline migration), Redis, Celery worker, MinIO, Docker Compose. QA reviewed; see "B1 and B2 result" below |
| B2 | Accounts | Done | Sign-up with email verification, login, 30-day sessions, password reset, profile, change password, delete account (API A1-A12). QA reviewed; both rule decisions made |
| B3 | Projects, scripts and jobs | Done | API B1–B10, C1–C6, G, H; language models (Gemini, Groq, offline mock); credits reserved, refunded and reset monthly. QA verdict was fail (a credit race); fixed and retested. See [FIX_LOG_B3.md](docs/qa/FIX_LOG_B3.md) |
| B4 | Generation | Done: voice (C7, D3), visuals (C8–C10), music (D4), Generate (B11), timeline (B12) | Three slices: 1 voice (ElevenLabs, Edge TTS backup), 2 visuals (Pexels stock, Cloudflare FLUX images), 3 music, Generate Reel (B11) and timeline (B12). Tester and QA after each slice |
| B5 | Rendering | Done | API E1–E3; a Remotion render service in Docker (`renderer/server.mjs`) and its own render worker. A real Reel rendered end to end. Local renders are 720x1280 (slow laptop), production 1080x1920. QA: fail, then pass with issues; all fixed but one known gap. See [FIX_LOG_B5.md](docs/qa/FIX_LOG_B5.md) |
| B6 | Publishing: YouTube Shorts | Done | Connect a channel (Google OAuth, tokens encrypted), AI title and caption (F4), publish with resumable upload and processing checks (F5, F6), licence credits added. A real Short published on 2026-11-26. QA: pass with issues; Tester: 7 bugs; all fixed. [PUBLISHING_YOUTUBE.md](docs/PUBLISHING_YOUTUBE.md), [FIX_LOG_B6.md](docs/qa/FIX_LOG_B6.md) |
| B6b | Publishing: Instagram | Not started | After YouTube, in the same flow |
| B7–B8 | Backend features | Not started | Built one task at a time, each agreed with the product owner first |
| I1–I3 | Integration and launch | Not started | |

## What exists today

| Area | Location | State |
|---|---|---|
| Documents | `docs/` | PRD, pipeline strategy, technical, API and database design, schema, Stitch prompts, UI review |
| Pipeline prototype | `pipeline/` (Python), `renderer/` (Remotion) | Command-line: topic → rendered MP4 |
| Backend API | `api/`, `docker-compose.yml` | Foundations (health check, error format, logging, migrations, background worker, file storage) and accounts (the 12 auth and profile endpoints, emails to a local test inbox). How to run: [README.md](README.md) |
| Web app | [reelforge-frontend](https://github.com/praveenmarch22/reelforge-frontend) | Login, sign up, verify email, forgot and reset password, onboarding, dashboard, projects library |
| UI designs | Stitch project (link in [STITCH_PROMPTS.md](docs/STITCH_PROMPTS.md)) | 22 screens |

### How to run the web app

```bash
cd frontend
```

```bash
npm install
```

```bash
npm run dev
```

Open http://localhost:5173. The demo login and mock-mode notes are in [the frontend README](https://github.com/praveenmarch22/reelforge-frontend/blob/main/README.md).

## What was verified, and how

| Check | Result |
|---|---|
| `npm run typecheck` | Passes |
| `npm run build` | Passes (342 kB JavaScript, 105 kB gzipped) |
| Sign up → "check your inbox" → verification link → app | Works |
| Reusing a verification link | Shows "link expired or already used" |
| Unverified user | Can open the app; sees a "verify your email" banner |
| Log out, wrong password, demo login | Work, with the right messages |
| Forgot password → reset link → mismatched passwords → new password → login notice | Works |
| Form validation (empty sign-up form) | Field messages shown |
| Dashboard: new user | Welcome and empty state with starter ideas |
| Dashboard: demo user | Stats, recent Reels in six statuses (the seed data has 6 of the 11), progress bar advancing through polling |
| Projects library | Grid, status filter, empty filter result, delete with confirmation |

The table above is the first hand check. Since then (2026-11-18) the QA and Tester roles reviewed and tested F1-F3; see "Quality" below.

Not verified yet: tablet width, colour contrast, screen readers, browsers other than Chromium, and real server behaviour (cookies, rate limits, lockout), which needs the backend.

## Quality

The project has two extra roles besides the developer, both AI agents that start without the developer's assumptions (defined in [.claude/agents/](.claude/agents/) in this repository):

| Role | Job | Output |
|---|---|---|
| QA | Reviews each finished milestone against the PRD, API design and UI designs; explores the app in a real browser. Never changes code | [docs/qa/TEST_PLAN.md](docs/qa/TEST_PLAN.md), test cases and a QA report per milestone |
| Tester | Writes and runs the automated tests. Never changes product code | Tests in the frontend repository, a pass/fail report, bug reports |

**How a milestone goes:** developer builds → Tester writes and runs tests, QA reviews → developer fixes → Tester updates and reruns the tests → this document records the result.

### F1–F3 result (2026-11-18)

| | |
|---|---|
| QA verdict | Pass with issues: 0 blockers, 3 major, 10 minor, 8 polish, 4 deferred. See [QA_REPORT_F1-F3.md](docs/qa/QA_REPORT_F1-F3.md) |
| Bugs from the Tester | 8 (BUG-001 to BUG-008), two of them in the fixes themselves |
| After fixes | All 3 major fixed; all but 4 of the rest fixed. Details and the deferred items in [FIX_LOG_F1-F3.md](docs/qa/FIX_LOG_F1-F3.md) |
| Automated tests | 180 unit and component tests and 120 browser tests, all passing locally; they also run on GitHub on every push |
| Test cases | 78 written by QA in [TEST_CASES_F1-F3.md](docs/qa/TEST_CASES_F1-F3.md) |

Deferred, with their milestone: seed data for the other five statuses (F5–F7), "load more" beyond 100 Reels (F9), Instagram pill on phones (F9), focus return and arrow keys in dialogs and menus, page titles and placeholder contrast (F9).

Limits of this round: QA compared the designs from their text descriptions, not the images; only Chromium was used; and QA did not re-review the fixes (the Tester's rerun covers them).

### F4 result (2026-11-24)

| | |
|---|---|
| QA verdict | Pass with issues: 0 blockers, 1 major, 7 minor, 3 polish. See [QA_REPORT_F4.md](docs/qa/QA_REPORT_F4.md) |
| Bugs from the Tester | 11 over three rounds (BUG-010 to BUG-020), five of them found while checking the first fixes |
| After fixes | All fixed except two items deferred to F9. Details in [FIX_LOG_F4.md](docs/qa/FIX_LOG_F4.md) |
| Automated tests | 424 unit and component tests and 223 browser tests, all passing locally |
| Design changes that came out of it | The exact rule for the Instagram return address, the "same signed-in user" check on the Instagram callback, and the niche format are now written into the API design; the PRD no longer mentions a language choice |

Deferred to F9: the whole voice card selecting the voice; returning to a deep link after onboarding. Open for F8: the Instagram profile picture field.

Limits of this round: only Chromium; the browser's back/forward cache could not be tested with the dev server; two browser tests are sensitive to a very slow machine (they passed when rerun).

### B1 and B2 result (2026-11-24)

| | B1 Backend setup | B2 Accounts |
|---|---|---|
| QA verdict | Pass with issues: 0 blockers, 1 major, 10 minor, 4 polish. [QA_REPORT_B1.md](docs/qa/QA_REPORT_B1.md) | Pass with issues: 0 blockers, 2 major, 7 minor, 4 polish. [QA_REPORT_B2.md](docs/qa/QA_REPORT_B2.md) |
| Bugs from the Tester | Covered by the B2 rounds | 13 over four rounds (BUG-021 to BUG-033), six of them found while checking earlier fixes |
| After fixes | The major and 7 others fixed or partly fixed; the rest scheduled. [FIX_LOG_B1.md](docs/qa/FIX_LOG_B1.md) | Both majors and all bugs fixed; both rule decisions made; 1 finding deferred to deployment. [FIX_LOG_B2.md](docs/qa/FIX_LOG_B2.md) |

Automated tests for the backend: 515, all passing locally. They run inside Docker against real Postgres, Redis, MinIO and a small test mail server.

Deferred from these reviews: upload limits and a migration guard (B3); proxy, cookie-domain and health-check choices (I2); type checking and a dependency audit in CI (I1). Web app changes needed when it is connected to the real API are listed in FIX_LOG_B2.

Limits of this round: QA did not run a successful sign-up through the API (the sign-up limit was used up; the Tester's tests cover it); behaviour with Redis or the queue really down was reasoned about, not tested; nothing has run on AWS.

## Decisions log

| Date | Decision |
|---|---|
| 2026-11-10 | Product: faceless Reels up to 90 s for solo creators; English only; name ReelForge |
| 2026-11-10 | Two visual modes: faceless (stock + AI images) and Full AI (AI clips with mood per scene) |
| 2026-11-10 | Free AI video model for now; AWS hosting later |
| 2026-11-11 | Whisper through an API (Groq); MinIO locally, S3 in AWS; no Google sign-in in version 1 |
| 2026-11-11 | UI: dark, futuristic, minimal; Stitch design system approved |
| 2026-11-17 | Build the frontend first against a mock API; backend approach to be discussed afterwards |
| 2026-11-17 | Frontend stack: React 18, TypeScript, Vite 5, Tailwind 3, TanStack Query, React Hook Form + Zod, MSW for mocks (Tailwind 3 and Vite 5 because this machine has Node 18) |
| 2026-11-17 | API additions found while building: `GET /projects` accepts a comma-separated `status` and returns `counts`; `ProjectSummary` has `progress` (recorded in API_DESIGN.md) |
| 2026-11-17 | Folder layout: `reelforge/` with `frontend/` and `backend/` side by side |
| 2026-11-23 | Backend is built one task at a time, with the product owner saying what is wanted from each task before it starts |
| 2026-11-23 | Local backend stack runs in Docker Compose. MinIO's own images are no longer published, so Chainguard's build of MinIO is used, pinned by digest. Postgres and Redis use host ports 54320 and 63790 to avoid clashing with other projects |
| 2026-11-23 | Backend dependencies: direct ones pinned in `requirements.txt`, every indirect one in `constraints.txt` (an unpinned queue library had crashed the worker) |
| 2026-11-23 | The baseline migration runs an exact copy of `docs/schema.sql`; CI fails if the two differ. Schema changes go in new migrations |
| 2026-11-23 | API design: `GET /instagram/connect` takes `returnTo`; health check reports storage; every response carries `X-Request-ID` |
| 2026-11-24 | Accounts: sessions are built directly on PyJWT (not Flask-JWT-Extended); emails go to a local test inbox (Mailpit) in development; forgot-password does its work in a background task so its timing reveals nothing; the local stack listens on this machine only |
| 2026-11-24 | Deleting an account releases its email at once; deleted accounts cannot be restored (they are purged after 7 days) |
| 2026-11-24 | The emailed verification link signs a browser in only when nobody else is signed in there; otherwise it verifies the address and leaves the session alone |
| 2026-11-24 | Onboarding: no language choice (English only); the free-text topic is one topic of up to 40 characters; unverified users cannot open onboarding; an Instagram connection completes only for the signed-in user who started it |
| 2026-11-18 | QA and Tester roles added; every milestone goes through both before it counts as done. Test tools: Vitest + Testing Library, Playwright; GitHub Actions runs them on every push to the frontend |
| 2026-11-18 | API design clarified from QA's questions: verify-email signs the user in; resend-verification takes no body and returns `409 already_verified`; public endpoints ignore the CSRF header; the 11 statuses, sort order and empty-string rule are listed |
| 2026-11-25 | Language models: `gemini-3.5-flash` first, Groq `openai/gpt-oss-120b` as backup (the earlier choices were retired; `gemini-3.8-flash` was refusing requests as busy). Keys live only in the gitignored backend `.env` |
| 2026-11-25 | B4 is built in three slices, each tested and reviewed before the next: voice, visuals, then music + Generate Reel + timeline |
| 2026-11-25 | Voice: ElevenLabs first (free plan about 10 minutes a month, non-commercial), Edge TTS takes over when it fails or runs out |
| 2026-11-25 | Music: a starter library of about 10 free-license tracks, one or two per mood, picked by the developer and approved by the product owner before download; license kept with each track |
| 2026-11-25 | Until B7, a Full AI scene gets a Cloudflare FLUX image with slow motion, charged as an AI image (1 credit) |
| 2026-11-25 | ElevenLabs free plan: when any scene of a posted Reel used an ElevenLabs voice, the post caption gets a short "Voice: elevenlabs.io" credit line, added when posting (milestone B6). A paid plan removes the need later |
| 2026-11-25 | Voice pricing: Generate charges 2 credits per started 30 s of the whole Reel's narration (60 s costs 4), not per scene. Redoing one scene's voice costs 1 credit, or 2 per started 30 s for a scene over 15 s |
| 2026-11-25 | Stock video: Pixabay is the stock source for now, because Pexels has paused new API keys. The stock code is built so Pexels can go in front of Pixabay when it reopens. Pixabay's terms are followed: clips are downloaded to our storage, searches are kept for 24 hours, and stock search shows where results come from |
| 2026-11-25 | When no stock clip fits a scene, the AI image used instead is not charged (the Workers AI free allowance covers it) |
| 2026-11-25 | Music library: 15 tracks by Kevin MacLeod (incompetech.com, CC BY 4.0), 1 to 4 per mood, approved by the product owner and filled by `flask music-seed`. A posted Reel credits its track in the caption, like the ElevenLabs line (milestone B6) |
| 2026-11-25 | Generate runs every scene's voice and visual side by side (visuals fitted to the estimated length) rather than visuals after voices: faster, and the renderer fits clips to the real voice length |
| 2026-11-25 | F5 and F6 are built against the real local backend (Vite's proxy to the Docker API), not the mock; the mock stays for the older screens' unit tests. Most of milestone I1 happens here |
| 2026-11-25 | The editor's preview uses a copy of the renderer's Reel components inside the web app, with a check that fails if the copies differ (answers open question 3) |
| 2026-11-25 | F5 and F6 are each reviewed by the Tester and QA and committed before the next; the screens follow Stitch screens 9 to 11, without the invented labels listed in the UI review |
| 2026-11-26 | B5 rendering: the render service runs as a Docker container in the same Compose stack (Linux, like production). The worker starts each render and polls the service for progress (no callback). Creators are told in the app only for now; email is later (PRD R4 email is P1) |
| 2026-11-26 | Render speed: the development laptop draws about 0.75 frames a second at 1080x1920 (a 29 s Reel took 20 minutes), so local renders are 720x1280 (`RENDER_SCALE=2/3`) and production renders 1080x1920 on bigger machines or Remotion Lambda. Production speed is measured on its host before launch, and the render time limit (`RENDER_TIME_LIMIT_SECONDS`) is set from it |
| 2026-11-26 | One active render per user (a second is refused with 409); different users' renders queue on a one-slot render worker. Reels under 3 s are refused (Instagram's minimum). Audio is AAC 128 kbit/s |
| 2026-11-26 | Publishing is platform-neutral, YouTube Shorts first (direct upload, no public video link needed; Google may keep uploads private until it audits the app, though the first real test came out public), Instagram after (B6b). Milestones re-cut: B6 YouTube backend and AI caption, then F7 against it. Visibility: the creator picks, Public by default. Publish screens adapted from the Stitch designs in a screen spec, no new Stitch screens |
| 2026-11-17 | Two public GitHub repositories under praveenmarch22: [reelforge-frontend](https://github.com/praveenmarch22/reelforge-frontend) (web app) and [reelforge-backend](https://github.com/praveenmarch22/reelforge-backend) (backend, all documents, milestones and this status document). Commits use GitHub's private no-reply email |

## Open questions

| # | Question | Needed by |
|---|---|---|
| 1 | Backend: what you want from each task. B2 Accounts is next | Before each backend task |
| 2 | Render capacity on AWS: measure frames per second on the production host; bigger machines or Remotion Lambda | I2 |
| 4 | In-app notifications (bell icon in the designs) are left out until render and publish exist | F7 |
| 5 | Landing page at `/` (PRD flow 1 step 1): it is in no milestone. Build one, or keep sending visitors straight to login? | Before launch |
| 6 | Unverified users can browse the app with a "verify your email" banner (creating is blocked). Keep that, or lock them to the verify screen? | F5 |
| 7 | Maximum project title length (proposed: 120 characters) | F5 |
| 10 | Load balancer health check: the full one or a lighter one? Does staging run with production settings? | I2 |

## Known gaps and differences from the designs

- Reel thumbnails are generated gradients in the mock-API screens; the real API now returns the cover of the latest render (B5).
- B5 known gap: if the render worker restarts mid-render, the job stays running until the stuck-job sweep fails it.
- B5: renders are slow on the development laptop (about 17 minutes for a 29 s Reel at 720x1280).
- B5: captions in renders use Liberation Sans (Arial's metrics) rather than Arial Black; bundling the fonts is a later improvement.
- Decorative labels Stitch invented ("ENGINE V4.2", GPU figures, "PRO TIER", the Trending Audio strip) are intentionally left out, as listed in the UI review.
- The sidebar has Home, Projects, Settings (and Admin for admins); the Music item from the designs is removed.
- Screens not built yet show a "not built yet" placeholder with their milestone number.

## Session log

| Date | Work |
|---|---|
| 2026-11-10 | PRD; pipeline prototype (Python + Remotion) with offline test renders |
| 2026-11-11 | Technical design, API design (70 endpoints), database design and tested schema; first Stitch screens |
| 2026-11-17 | UI review; 6 missing screens designed (22 total); milestones and this status document; frontend F1–F3 built and checked in the browser |
| 2026-11-17 | Restructured into `frontend/` and `backend/`; split into two git repositories with the documents in the backend one; links and paths updated; frontend build, prototype build and renderer type-check re-run and pass; first commits pushed to GitHub |
| 2026-11-18 | QA and Tester roles set up and run on F1–F3: QA report, 78 test cases, 300 automated tests; 3 major and most minor findings fixed; automatic test runs on GitHub added |
| 2026-11-23 | Frontend F4 onboarding built and reviewed by QA (session restart interrupted the first review; it was rerun). Backend B1 built: Flask app, Docker Compose stack, baseline migration, storage, worker, 39 tests |
| 2026-11-24 | F4 onboarding: QA and Tester findings fixed over three rounds, API design and PRD updated, committed. Backend work paused: Docker Desktop's engine stopped responding |
| 2026-11-24 | Docker Desktop restarted. Backend B1 reviewed by QA and its findings fixed. Backend B2 Accounts built from the design docs, tested (492 backend tests) and reviewed by QA; fixes over three rounds; API design updated |
| 2026-11-24 | Two account rules decided and built: the verification link signs in only when nobody else is signed in; deleting an account frees its email at once. 515 backend tests |
| 2026-11-25 | Backend B3 built from the design docs: projects, scripts (written or pasted), scenes, background jobs, credits. QA found a credit race (blocker) and 13 more issues, the Tester 2 bugs; all fixed. 938 backend tests |
| 2026-11-25 | Gemini and Groq keys added; a real script written end to end through the app. Model defaults updated. B4 planned with the product owner |
| 2026-11-25 | ElevenLabs key added. B4 slice 1 built: voiceovers per scene (C7), voice list (D3), Idempotency-Key. QA found 16 issues and the Tester 6 bugs over three rounds; all fixed. Voice pricing and the ElevenLabs credit line decided. 1090 backend tests |
| 2026-11-25 | Pexels paused new keys, so Pixabay is the stock source; Pixabay and Cloudflare keys added. B4 slice 2 built: visuals per scene (C8), stock search (C9), choosing a visual (C10). QA found 12 issues and the Tester 2 bugs over three rounds; all fixed. 1291 backend tests |
| 2026-11-25 | B4 slice 3 built: music library (15 tracks, `flask music-seed`), Generate Reel (B11), timeline (B12), music list (D4). A real 5-scene Reel generated in 26 s for 2 credits. QA found 6 issues and the Tester 1 bug over two rounds; all fixed. B4 complete. 1380 backend tests |
| 2026-11-26 | Frontend F5 built against the real backend: New Reel (ideas, topic or pasted script, settings) and the Script editor (autosaved scenes, rewrites, reorder, hooks, Shorten with AI, Generate). Designs read from Stitch through Chrome. QA found 22 issues and the Tester 12 bugs over four rounds; all fixed but one deferred to F9. 530 frontend unit tests, 26 real-backend browser tests |
| 2026-11-26 | Frontend F6 built: the Reel editor with a live Remotion preview (a copy of the renderer's drawing code, checked identical by a test), scene list, inspector (visual, voice, text), stock search, audio bar. Renderer accepts voiceless scenes, trims and a preview-only media error hook. QA found 19 issues and the Tester 12 bugs over four rounds (one backend: a failed voice's reason kept); all fixed but two deferred to F9. 622 frontend unit tests, 24 real-backend editor browser tests |
| 2026-11-26 | Backend B5 built: the render service (Remotion in Docker, token and storage-only links), render jobs with one retry, a separate render worker, API E1–E3, latest render and cover in the project JSON. A real 29 s Reel rendered end to end (20 min at 1080x1920; 17 min at 720x1280). QA found 2 Major and 18 smaller issues over two rounds, the Tester 4 bugs; all fixed but one known gap. Render speed decided: 720p locally |
| 2026-11-26 | YouTube publishing decided (YouTube first, Instagram after). Google Cloud project and OAuth client set up with the product owner. B6 built: connect, AI post text, publish. The first real Short published (it came out public). QA found 3 Major and 10 Minor issues, the Tester 7 bugs; all fixed. Docker Desktop's engine hung (machine low on memory) and was restarted with the product owner's OK |
