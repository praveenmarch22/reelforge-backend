# ReelForge: project status

**Last updated:** 2026-11-23

This is the single place to see where the project stands. It is updated at the end of every working session. The milestone definitions are in [MILESTONES.md](MILESTONES.md).

## Where we are

**Planning and design are finished. The frontend is under way: F1–F3 are done (setup, sign-in screens, dashboard and projects library), running against a mock API, and they have been through a QA review and automated testing with all findings fixed or scheduled. Onboarding (F4) is built and in QA. The backend has started, one task at a time: B1 (setup) is built and its tests pass.**

Next up: finish F4 (fix the QA findings), then **B2 Accounts** on the backend and **F5 New Reel and script editor** on the frontend.

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
| F4 | Onboarding | Built, in QA | QA verdict: pass with issues (1 major, 7 minor). Fixes and the Tester's run are in progress; not committed yet |
| F5 | New Reel and script editor | Not started | |
| F6 | Reel editor | Not started | Largest frontend milestone |
| F7 | Render and publish | Not started | |
| F8 | Settings, credits and admin | Not started | |
| F9 | Frontend hardening | Not started | |
| B1 | Backend setup | Built, review pending | Flask API, Postgres (16 tables from the baseline migration), Redis, Celery worker, MinIO, Docker Compose. 39 tests pass locally |
| B2–B8 | Backend features | Not started | Built one task at a time, each agreed with the product owner first |
| I1–I3 | Integration and launch | Not started | |

## What exists today

| Area | Location | State |
|---|---|---|
| Documents | `docs/` | PRD, pipeline strategy, technical, API and database design, schema, Stitch prompts, UI review |
| Pipeline prototype | `pipeline/` (Python), `renderer/` (Remotion) | Command-line: topic → rendered MP4 |
| Backend API | `api/`, `docker-compose.yml` | Foundations only: health check, error format, logging, database migrations, background worker, file storage. No product features yet. How to run: [README.md](README.md) |
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
| 2026-11-18 | QA and Tester roles added; every milestone goes through both before it counts as done. Test tools: Vitest + Testing Library, Playwright; GitHub Actions runs them on every push to the frontend |
| 2026-11-18 | API design clarified from QA's questions: verify-email signs the user in; resend-verification takes no body and returns `409 already_verified`; public endpoints ignore the CSRF header; the 11 statuses, sort order and empty-string rule are listed |
| 2026-11-17 | Two public GitHub repositories under praveenmarch22: [reelforge-frontend](https://github.com/praveenmarch22/reelforge-frontend) (web app) and [reelforge-backend](https://github.com/praveenmarch22/reelforge-backend) (backend, all documents, milestones and this status document). Commits use GitHub's private no-reply email |

## Open questions

| # | Question | Needed by |
|---|---|---|
| 1 | Backend: what you want from each task. B2 Accounts is next | Before each backend task |
| 2 | Render capacity on AWS | B5 / I2 |
| 3 | How the frontend gets the shared Remotion video components now that the renderer is in a separate repository (small package, or copy on release) | F6 |
| 4 | In-app notifications (bell icon in the designs) are left out until render and publish exist | F7 |
| 5 | Landing page at `/` (PRD flow 1 step 1): it is in no milestone. Build one, or keep sending visitors straight to login? | Before launch |
| 6 | Unverified users can browse the app with a "verify your email" banner (creating is blocked). Keep that, or lock them to the verify screen? | F5 |
| 7 | Maximum project title length (proposed: 120 characters) | F5 |

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
| 2026-11-18 | QA and Tester roles set up and run on F1–F3: QA report, 78 test cases, 300 automated tests; 3 major and most minor findings fixed; automatic test runs on GitHub added |
| 2026-11-23 | Frontend F4 onboarding built and reviewed by QA (session restart interrupted the first review; it was rerun). Backend B1 built: Flask app, Docker Compose stack, baseline migration, storage, worker, 39 tests |
