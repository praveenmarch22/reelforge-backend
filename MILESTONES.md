# ReelForge: milestones

The project is split into four tracks. The frontend is built first against a mock API that follows [API_DESIGN.md](docs/API_DESIGN.md) exactly, so it can be finished and reviewed before any backend exists. Switching to the real backend later is one setting (`VITE_USE_MOCKS=false`).

Live progress is in [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Track P: planning and design (done)

| # | Milestone | Output |
|---|---|---|
| P1 | Product requirements | [PRD.md](docs/PRD.md) |
| P2 | Pipeline prototype | `pipeline/`, `renderer/`, [PIPELINE_STRATEGY.md](docs/PIPELINE_STRATEGY.md) |
| P3 | Technical, API and database design | [TECHNICAL_DESIGN.md](docs/TECHNICAL_DESIGN.md), [API_DESIGN.md](docs/API_DESIGN.md), [DB_DESIGN.md](docs/DB_DESIGN.md), [schema.sql](docs/schema.sql) |
| P4 | UI design | 22 screens in Stitch, [STITCH_PROMPTS.md](docs/STITCH_PROMPTS.md), [UI_REVIEW.md](docs/UI_REVIEW.md) |

## Track F: frontend ([reelforge-frontend](https://github.com/praveenmarch22/reelforge-frontend), React + TypeScript + Vite + Tailwind)

| # | Milestone | Screens and work | Done when |
|---|---|---|---|
| F1 | Project setup and design system | Vite app, Tailwind theme from the Stitch design system, fonts, UI kit (button, input, glass card, pills), router, API client, mock API (MSW) | `npm run dev` opens the app; type-check and build pass |
| F2 | Sign-in screens | Login, sign up, check your email / verify, forgot password, set new password; route guards; session refresh | A new user can sign up, verify, log out, log in and reset a password against the mock API |
| F3 | App shell, dashboard and projects | Sidebar and top bar, dashboard (with empty state), projects library (search, filters, delete) | Dashboard and library show mock projects in every status |
| F4 | Onboarding | Niche, voice (with previews), connect Instagram; skippable | New users see onboarding once and can skip it |
| F5 | New Reel and script editor | Idea step (ideas, settings), script editor (edit, reorder, rewrite, add or delete scenes), estimate, generate | A script can be created, edited and sent to generation |
| F6 | Reel editor | Scene list with statuses, Remotion Player preview from the timeline, inspector (voice, visual, text), audio bar, regenerate, stock search, uploads, autosave | A generated project can be previewed and edited end to end |
| F7 | Render and publish | Render progress, publish screen (platform choice, title, caption, hashtags, visibility), publishing tracker, "Your Short is live", connect result dialogs | A project can be rendered and published to YouTube against the real backend (after B6) |
| F8 | Settings, credits and admin | Settings tabs (profile, defaults, Instagram, credits, security), out-of-credits modal, admin console | All remaining designed screens exist |
| F9 | Frontend hardening | 404 and error pages, phone-width layouts, keyboard and screen-reader pass, unit and end-to-end tests | Checklist in the status document is green |

## Track B: backend (this repository, plan to be agreed)

The approach for the backend will be discussed after the frontend. The scope below comes from the technical design and is provisional.

| # | Milestone | Scope |
|---|---|---|
| B1 | Backend setup | Flask app, PostgreSQL (schema.sql), Redis, Celery, MinIO, Docker Compose |
| B2 | Accounts | Auth endpoints (A1–A12), email |
| B3 | Projects, scripts and jobs | Ideas, scripts, scenes, jobs, credits: B1–B10, C1–C6, G, H; the language models (Gemini, Groq); credit reservation, refunds and monthly reset |
| B4 | Generation pipeline | Voice, visuals, music, timeline (built on the prototype in `pipeline/`), in three slices: (1) voice: C7, D3; (2) visuals: C8–C10, Full AI scenes get an AI image with motion until B7; (3) music D4, Generate B11, timeline B12 |
| B5 | Rendering | Render service from the prototype renderer |
| B6 | Publishing: YouTube Shorts | Platform-neutral connect and publish (F group), YouTube first, AI title and caption (F4). Design: [PUBLISHING_YOUTUBE.md](docs/PUBLISHING_YOUTUBE.md) |
| B6b | Publishing: Instagram | Instagram connect and publish into the same endpoints (needs a Meta app and a public video link) |
| B7 | Full AI mode and audio extras | AI video provider, uploads (D1, D2), Whisper |
| B8 | Admin and scheduled tasks | Admin endpoints, cleanup of replaced files, Instagram token refresh (the monthly credit reset and stuck-job check came with B3) |

## Track I: integration and launch

| # | Milestone | Scope |
|---|---|---|
| I1 | Connect frontend to the real API | Turn off mocks, fix differences, end-to-end tests against the real backend |
| I2 | Deploy to AWS | Phase A from the technical design |
| I3 | Meta App Review | Needed before non-tester Instagram accounts can connect |
