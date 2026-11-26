# B5 rendering: fix log

Fixes for [QA_REPORT_B5.md](QA_REPORT_B5.md) and [TEST_REPORT_B5.md](TEST_REPORT_B5.md).

## Product owner decision

- **Render speed (2026-11-26).** On the development laptop a 1080x1920 render draws about 0.75 frames a second: a 29 s Reel took 20 minutes. Decision: local renders come out at 720x1280 (`RENDER_SCALE=2/3` on the render service); production renders 1080x1920 on bigger machines or Remotion Lambda.

## QA round 1

| Finding | Fix |
|---|---|
| QB5-001 Major: time limits don't fit 90 s Reels; the retry shares the clock; Redis redelivers after 1 hour | Renders run on their own worker (below), so the clock starts when drawing starts. Limits are 50 / 51 minutes, under Redis's 1-hour redelivery. The retry starts only if at least half the soft limit is left. Local renders are 720x1280 (decision above). Comments and the technical design were corrected |
| QB5-002 Major: renders take up the shared worker's slots | New `render-worker` Compose service: queue `render`, concurrency 1, prefetch 1. The main worker no longer takes `render`. Queued renders may wait 3 hours (`QUEUED_LIMITS`); other jobs keep 30 minutes |
| QB5-003 Minor: a retry doesn't stop the first attempt, and both upload to the same links | Each attempt uploads to its own keys (`…/{renderId}-{attempt}.mp4` / `.jpeg`). An attempt whose service stopped answering is cancelled at the service. Files of failed attempts are deleted |
| QB5-004 Minor: a restarting service uses up both attempts in seconds | A start that is refused or answered 503 is asked again, up to 12 times (about 1 minute), within the same attempt |
| QB5-005 Minor: cancelling during the upload leaves orphan files | On cancel the worker stops the attempt and waits up to about 1 minute for the service to finish with it, then deletes the files |
| QB5-006 Minor: a worker restart mid-render leaves the job stuck | Not fixed. Documented as a known gap in the technical design (the sweeper fails it after about 53 minutes) |
| QB5-007 Minor: a cancelled re-render hides the earlier status | A cancelled render with an unchanged timeline restores `previousStatus` (ready, rendered, render_failed, post_failed, posted). A failure still sets `render_failed`; earlier renders stay in E3 |
| QB5-008 Minor: the service runs open without a token; SSRF; token compare not constant-time | The service won't start without `RENDER_SERVICE_TOKEN` (16+ characters) and `RENDER_ALLOWED_ORIGINS`. Every timeline link and upload link must be at an allowed origin, else `400`. The token is compared with `timingSafeEqual` |
| QB5-009 Minor: the progress numbers disagree | The job and the render store the same number (10 + 0.88 × the service's progress), only while the service says `running` |
| QB5-010 Minor: tests miss the risky paths | The Tester added 73 render cases and 12 Node service tests |
| QB5-011 Nit: documents disagree with the code | Technical design (limits, no callback, link wording, speed, known gap), API design (E1 order, `too_short`, J2 marked not built, K1 links) |
| QB5-012 Nit: two extra queries per Reel in the list | Not changed: the list is at most one page, and the queries are by primary key / index. To be revisited with F7 |
| QB5-013 Nit: render image base not pinned | `node:22-bookworm-slim` pinned by digest |
| Q1: R-4 queue vs refuse | One active render per user, refused with 409 `already_running`. Different users' renders queue on the render worker |
| Q3: audio 320k, no 3 s minimum | Audio is AAC 128 kbit/s. E1 refuses Reels under 3 s with `409 too_short` |
| Q2, Q4, Q5 | For F7 / B6: in-app notification design, re-render pricing, re-rendering a posted Reel |

Found while checking the fixes: a scale of `0.6667` gave a 1280.064-pixel height, which Remotion refuses (both attempts failed, the credits were refunded). `RENDER_SCALE` is now a fraction such as `2/3`, and the service refuses a scale that doesn't give whole pixels.

## Tester round 1

| Bug | Fix |
|---|---|
| BUG-069 Minor: `too_long` has no `details.seconds` | `too_long` and `too_short` carry `details.seconds` |
| BUG-070 Minor: "already rendering" is checked before "not ready" | The checks now run in the documented order: not_ready, too_long, too_short, already_running, 402 |
| BUG-071 Minor: production accepts an empty `RENDER_SERVICE_URL` | Production needs an http(s) URL |
| BUG-072 Minor: the service accepts a numeric `renderId` | `renderId` must be a string |

After the fixes, the render and config tests pass (198) and so do the Node service tests (12).

## QA round 2

QA round 2 verdict: pass with issues (no Blocker or Major). Fixes:

| Finding | Fix |
|---|---|
| QB5-004 partly fixed: a slow restart can still fail both attempts | Unchanged retry window (about 1 minute per attempt, two attempts). The start check now tells permanent refusals apart (QB5-014) |
| QB5-014 Minor: the start retry repeats permanent 400/401 answers and doesn't cancel | A 4xx other than 429 fails the attempt at once. Giving up sends a cancel to the service; a cancel while retrying waits for the service to settle |
| QB5-015 Minor: cancel during the upload still leaves orphan files | The service reports `cancelling` until drawing and any upload under way have stopped, then `cancelled`. The worker's wait treats `cancelling` as still busy. Checked with a real render cancelled at 16%: status restored to `rendered`, no files left |
| QB5-016 Minor: a timed-out render leaves its files | `work()` deletes every key it made before re-raising the time-out |
| QB5-017 Minor: the half-limit rule still starts retries that can't finish | The retry starts only if the time left is more than the first attempt took (×1.1). The render time limit is now a setting, `RENDER_TIME_LIMIT_SECONDS` (default 3000 s; 5400 s locally, since a 90 s Reel needs about 53 minutes on the laptop), and Redis's redelivery timeout is set above it |
| QB5-018 Nit: odd sides and loose fraction parsing | `RENDER_SCALE` must be a number or a single fraction, and must give whole, even sides; the reported size is the exact scaled size |
| QB5-019 Nit: leftover callback and "one-time" wording, PRD R-4, K2b | Fixed in the technical design, API design, PRD, `server.mjs`, `renders.py` and `docker-compose.yml` |
| QB5-020 Nit: render-worker has no health check; origins not normalised | Health check added (Celery ping); allowed origins are normalised with `new URL(o).origin` |
| Q6: production render speed | Recorded in the technical design: measure it on the production host before launch (I2), then set `RENDER_TIME_LIMIT_SECONDS` |
| Q7: `latestRender` after a cancelled re-render has no video | Kept as the newest render in any state; F7 takes the last good video from E3. Noted for F7 |

Clean timing after the fixes: the 29 s octopus Reel rendered at 720x1280 in 17 minutes (about 0.85 frames a second), 12 MB.
