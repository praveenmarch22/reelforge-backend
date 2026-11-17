# ReelForge: project status

**Last updated:** 2026-11-17

This is the single place to see where the project stands. It is updated at the end of every working session. The milestone definitions are in [MILESTONES.md](MILESTONES.md).

## Where we are

**Planning and design are finished. The frontend is under way: F1–F3 are done (setup, sign-in screens, dashboard and projects library), running against a mock API. The backend has not been started; its approach is to be agreed.**

Next up: **F4 Onboarding**, then **F5 New Reel and script editor**.

## Milestones

| # | Milestone | Status | Notes |
|---|---|---|---|
| P1 | Product requirements | Done | [PRD.md](docs/PRD.md) |
| P2 | Pipeline prototype | Done | Works offline and with Edge TTS. Gemini, ElevenLabs, Pexels, Cloudflare and Hugging Face are untested (need API keys) |
| P3 | Technical, API and database design | Done | 70 endpoints, 16 tables; schema tested on PostgreSQL 16 |
| P4 | UI design | Done | 22 screens in Stitch; review notes in [UI_REVIEW.md](docs/UI_REVIEW.md) |
| F1 | Frontend setup and design system | Done | In the reelforge-frontend repository; type-check and production build pass |
| F2 | Sign-in screens | Done | Tested by hand in the browser against the mock API |
| F3 | App shell, dashboard and projects | Done | Tested by hand in the browser against the mock API |
| F4 | Onboarding | Not started | |
| F5 | New Reel and script editor | Not started | |
| F6 | Reel editor | Not started | Largest frontend milestone |
| F7 | Render and publish | Not started | |
| F8 | Settings, credits and admin | Not started | |
| F9 | Frontend hardening | Not started | |
| B1–B8 | Backend | Not started | Approach to be discussed after the frontend |
| I1–I3 | Integration and launch | Not started | |

## What exists today

| Area | Location | State |
|---|---|---|
| Documents | `docs/` | PRD, pipeline strategy, technical, API and database design, schema, Stitch prompts, UI review |
| Pipeline prototype | `pipeline/` (Python), `renderer/` (Remotion) | Command-line: topic → rendered MP4 |
| Web app | [reelforge-frontend](https://github.com/praveenmarch22/reelforge-frontend) | Login, sign up, verify email, forgot and reset password, dashboard, projects library |
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
| Dashboard: demo user | Stats, recent Reels in every status, progress bar advancing through polling |
| Projects library | Grid, status filter, empty filter result, delete with confirmation |

Not verified yet: phone-width layouts, keyboard-only use, automated tests (none written yet; planned in F9), other browsers than the built-in Chromium pane, and the session-refresh path (`token_expired`), which the mock API never triggers.

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
| 2026-11-17 | Two public GitHub repositories under praveenmarch22: [reelforge-frontend](https://github.com/praveenmarch22/reelforge-frontend) (web app) and [reelforge-backend](https://github.com/praveenmarch22/reelforge-backend) (backend, all documents, milestones and this status document). Commits use GitHub's private no-reply email |

## Open questions

| # | Question | Needed by |
|---|---|---|
| 1 | Backend approach (your plan) | Before track B |
| 2 | Render capacity on AWS | B5 / I2 |
| 3 | How the frontend gets the shared Remotion video components now that the renderer is in a separate repository (small package, or copy on release) | F6 |
| 4 | In-app notifications (bell icon in the designs) are left out until render and publish exist | F7 |

## Known gaps and differences from the designs

- Reel thumbnails are generated gradients until real renders exist.
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
