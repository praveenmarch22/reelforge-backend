# ReelForge: QA report for B5 (rendering)

| | |
|---|---|
| Date | 2026-11-26 |
| Milestone | B5: render service (`renderer/server.mjs`, Dockerfile), `render` job, API E1 to E3, Render JSON, project `latestRender` / `thumbnailUrl` / rendering progress |
| Build tested | `backend/` working tree on top of 1454fbe (B5 uncommitted) |
| Checked against | PRD 5.7 (R1 to R4), 11.6 (R-1 to R-5), CR-2; API design Render JSON, E1 to E3, G3, group K; technical design 7 (queues, task rules) and "Render service"; schema `0001_baseline.sql` (`renders`, `assets`) |
| Not in scope | Render speed itself (raised with the product owner separately; only its consequences are reported here), Instagram publishing (B6), email notification (P1), clean-up of old files (B8) |

## Verdict: fail

The design is sound and most of the hard parts are right:
- **E1** locks the project, then the credit account (the same order as Generate), so two Render clicks, or two Reels of one user, can't both start (R-4). Credits are reserved in the same transaction and refunded on failure, cancel and time-out (R-1, CR-2).
- **The worker** keeps no transaction open while it renders. It locks project, then job, then render, the same order the requests use. Progress writes take no project lock, so there is no lock cycle.
- **Cancel and edits mid-render:** cancel (G3) and project deletion stop the render at the service within one poll. Edits don't touch the stored snapshot, and the result is marked `outdated` (R-3).
- **Storage links:** every file is checked and re-signed at the internal address before the render (R-2). Browsers only get 2-hour links at the public address.
- **Image and config:** the image runs as `node`, the port is bound to 127.0.0.1, and production refuses the mock or a weak token.
- **Output:** H.264/AAC, CRF 20, capped at 8 Mbit/s, fast start (R-5).
- **Contract:** JSON shapes match the updated API design.

It fails because of two Major findings. Both come from renders taking tens of minutes:

- **QB5-001 (Major):** at the measured speed, the time limits can't fit what E1 accepts. A Reel over about 80 s can never finish. The R-1 retry can't finish for most lengths. Waiting behind another creator's render counts against the same clock.
- **QB5-002 (Major):** a render occupies one of the stack's only two worker slots for 20 to 55 minutes. These slots are shared with every other queue, including the stuck-job sweeper. Two renders stop all generation for everyone, and jobs queued longer than 30 minutes are then failed.

| Severity | Count | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 2 | QB5-001, QB5-002 |
| Minor | 8 | QB5-003 to QB5-010 |
| Nit | 3 | QB5-011 to QB5-013 |
| Questions | 5 | Section 3 |

**How verified.** All findings and passes were **read** from the code and documents (`git diff` against 1454fbe, the new files, Remotion 4.0.530's code in `renderer/node_modules`). I ran no part of the stack, and I did not touch the main Compose project or the dev server. The render speed figures are the developer's measurement (a 29 s Reel in about 19.5 min, about 0.75 frames a second). Nothing in this report re-measures them.

---

## 1. Findings

### QB5-001 Major: the render time limits don't fit the Reels E1 accepts, so long Reels, retries and queued renders time out

| | |
|---|---|
| Requirement | PRD R1 (render the final MP4), R-1 (retry once automatically), E1 (accepts up to 90 s); technical design "Render service" (time limit) |
| Where | `jobs.py:42-50` (soft 3300 s, hard 3360 s, sweeper 57+1 min); `renders.py:39, 296-306` (two attempts in one job); `server.mjs:39, 116-125` (one render at a time, the rest wait) |
| Verified | Read, with the measured speed |

- **Long Reels can't finish.** At about 0.75 frames a second, a 30 fps Reel renders at about 40 s of wall time per second of video. The 55-minute soft limit therefore covers at most about 80 s of Reel, yet E1 accepts up to 90 s (`timeline.too_long`). A 90 s Reel (2,700 frames, about 60 min) always ends `timed_out`, after holding a worker for 55 minutes.
- **The retry can't finish.** Both attempts share the same 55 minutes. If the first attempt fails late, a second one fits only for Reels under about 40 s. For most Reels, R-1 is a retry that is bound to time out.
- **Waiting counts against the clock.** The job's clock starts when the worker takes it, not when the service starts drawing. A 60 s Reel (about 40 min) that waits behind a 29 s Reel (about 20 min) times out.
- **The limits can't simply be raised.** No `broker_transport_options.visibility_timeout` is set, so Redis's default of 1 hour applies. With `acks_late`, a task that runs past an hour is delivered again.
- **The comments and documents are wrong.** The comment at `jobs.py:48-49` says "about 2 frames a second … 25 minutes" for 90 s, which is 2.5 times faster than measured. The technical design says 23 minutes (line 1024) and 10 minutes (task rule 3, line 938).

**Suggested fix**: until the speed question is settled, base the limit on the Reel's frame count and the measured rate, plus a margin. Start the clock when the service reports `running`, not while the render waits. Only retry if enough time is left. Set Redis's `visibility_timeout` above the longest hard limit. If a Reel can't be rendered on this hardware, refuse it in E1 or cap its length, rather than charging and then timing out. Correct the comment and both places in the technical design.

### QB5-002 Major: renders take up the shared worker slots for up to an hour, which stops all other jobs and the stuck-job sweeper

| | |
|---|---|
| Requirement | Technical design 7, Queues table (`render` has its own concurrency; "slow or scarce work … must not block other work"), task rule 3; PRD G-11 (stuck jobs fail and refund); technical design Phase A (production is this same Compose stack) |
| Where | `docker-compose.yml:131` (one worker, `--concurrency 2`, `-Q default,llm,voice,visuals,ai_video,render,publish,maintenance`, beat in the same process); `jobs.py:52` (`QUEUED_LIMIT` 30 min) |
| Verified | Read |

- **A render holds a worker slot for its whole length**, 20 to 55 minutes, while it polls (`renders.py:238-260`).
- **Two renders from different creators take both slots.** For that time, no voice, visual, script, email or `jobs.fail_stuck` task runs, for anyone.
- **Jobs left waiting are then failed.** Any job that waits more than 30 minutes is failed `timed_out` by the sweeper once it runs again. The creator still gets the refund, but loses the work.
- **The second render waits twice.** The render service draws one render at a time, so the second render also waits at the service while holding its worker slot. Its time limit runs during that wait (QB5-001).

**Suggested fix**: add a separate `worker-render` service to Compose with `-Q render --concurrency 1`, matching the render service's capacity. Run beat and the `maintenance` queue in their own small process. Give renders their own queued limit, because with one render slot, waiting in Celery for over 30 minutes is normal. Document the topology in technical design 7 and in the Phase A deployment notes.

### QB5-003 Minor: a retry doesn't stop the first attempt, and both attempts upload to the same reusable links

| | |
|---|---|
| Requirement | PRD R-1; API K1 ("one-time upload links") |
| Where | `renders.py:232-235` (a failed K1 call; `start` can time out after the service took the render), `246-251` (30 s without an answer returns `None` with no `service.cancel`), `296-299` (next attempt starts at once); `storage.py` `internal_put` (a presigned PUT, reusable for 3 h); `server.mjs:208-213` |
| Verified | Read |

**Scenario**: the network drops for 30 s while attempt 1 is drawing. The worker gives up on attempt 1 without cancelling it and starts attempt 2. At the service, attempt 2 waits behind attempt 1, which keeps running. Both have the same `videoUploadUrl`:
- The job can take twice as long, which makes QB5-001 worse.
- A late upload can replace the MP4 after `apply` has saved its asset row, so `sizeBytes` no longer matches the file.
- If attempt 2 times out, attempt 1 can still upload into a render that was refunded and discarded.

**Suggested fix**: call `service.cancel(<previous id>)` before the next attempt, and on every `None` exit from `_attempt`. Give each attempt its own output keys and keep the winning attempt's. Change "one-time" in API K1 and the technical design to "short-lived, for one file".

### QB5-004 Minor: a render service that is restarting (503) or refusing uses up both attempts in seconds

| | |
|---|---|
| Requirement | PRD R-1 |
| Where | `renders.py:69` (`raise_for_status` on K1), `233-235`; `server.mjs:197` (`503 starting` while the bundle builds; the health check allows 240 s) |
| Verified | Read |

The Remotion bundle is rebuilt after every restart of the service, which can take minutes. Any job the worker takes during that time gets `503` twice, in well under a second, and fails `render_failed`. Connection refused behaves the same. The creator is refunded, but is told "The video couldn't be made", when waiting a minute would have worked.

**Suggested fix**: within an attempt, retry K1 with backoff (for example for up to 5 minutes) on connection errors and `503`. Only count a failure reported by the service, or no answer during a render, as a failed attempt.

### QB5-005 Minor: cancelling during the upload can leave an MP4 in storage that nothing points to

| | |
|---|---|
| Requirement | Technical design 7, task rule 4 (cancelled work is dropped); PRD F-2 clean-up (only files with asset rows are cleaned) |
| Where | `renders.py:240-242, 300-301` → `jobs.run` → `RenderJob.discard` deletes the keys at once; `server.mjs:93-96` (the uploads can't be cancelled; the check happens only before and after them) |
| Verified | Read |

If the job is cancelled, the project deleted, or the soft time limit reached while the service is uploading (seconds for 25 MB, longer for 90 MB), the worker deletes the keys first and the upload finishes afterwards. The MP4 and the cover stay under `users/…/renders/` with no asset row, so the B8 clean-up never finds them.

**Suggested fix**: after `service.cancel`, poll until the service reports `cancelled`, `failed` or `succeeded` (with a short bound), then discard. Alternatively, have the B8 clean-up also remove render files that have no asset row and are older than a day.

### QB5-006 Minor: if the worker restarts mid-render, the job is stuck for up to an hour and the service keeps drawing

| | |
|---|---|
| Requirement | PRD G-11; technical design 7, task rule 3 |
| Where | `jobs.py:197-200` (a redelivered task finds the job `running` and returns); `renders.py:232` (`service.start` sits outside the `try` that cancels at the service on `SoftTimeLimitExceeded`) |
| Verified | Read |

**What happens after a worker restart**: a deploy, crash, or hard kill (`time_limit`) while a render runs leaves the job `running` with nobody polling. Then:
- The sweeper fails it only 58 minutes after it started.
- Meanwhile, the service keeps drawing a render nobody will collect, and every other render waits behind it.

With renders of 20 to 55 minutes, a deploy during a render is likely.

**Suggested fix**: when a `render` job is delivered again and is still `running`, pick up polling `{renderId}-{attempt}` again. K1 is already idempotent by id, so this can be done safely. Otherwise, at least send K2b for both attempt ids. Also cover `start` with the `SoftTimeLimitExceeded` handler.

### QB5-007 Minor: a cancelled or failed re-render hides a good, current render

| | |
|---|---|
| Requirement | PRD R3 (download the MP4); API Render ("`latestRender` is the newest render in any state"); DB design project statuses |
| Where | `renders.py:170` (`previousStatus` is saved but never read), `358-371` (`on_stop`) |
| Verified | Read |

**Steps**: a Reel is `rendered` at timeline version 4. The creator presses Render again without editing, then cancels.
- The project becomes `ready`, not `rendered`.
- If the re-render fails instead, the project becomes `render_failed`, although a current, successful render of the same version exists.
- In both cases `latestRender` is the cancelled or failed render, so its `video` is `null`. The project card and editor lose the download. Only E3 still has it.

**Suggested fix**: on cancel, restore `previousStatus` when `timeline_version` hasn't changed. On failure, go back to `rendered` if a succeeded render of the current version exists. Consider adding the latest *succeeded* render to the project JSON for F7 and B6, which will need it to publish. See also question Q4.

### QB5-008 Minor: the render service accepts any caller when it has no token, and fetches or uploads any URL it is given

| | |
|---|---|
| Requirement | API group K ("Every request except K3 carries `Authorization: Bearer …`; others get `401`"); technical design, Security |
| Where | `server.mjs:27, 186` (no token: no check), `186` (plain `!==` comparison), `205` (`videoUploadUrl` and `thumbnailUploadUrl` are only checked to be present; the timeline `src` values aren't checked at all) |
| Verified | Read |

- **No token, no check.** The API refuses to start in production without a strong token (`config.py`), but the service itself runs open if its own `RENDER_SERVICE_TOKEN` is missing or misspelled in a deployment. The image sets `NODE_ENV=production` and starts anyway.
- **Any caller can use it as a proxy.** Anyone who can reach it can make headless Chrome and FFmpeg fetch any internal URL, such as the cloud metadata address, and PUT the result to any URL. That is SSRF and data theft from inside the private network.
- **Bounded on the dev machine.** There, it is limited by the 127.0.0.1 port binding and the dev token.

**Suggested fix**: refuse to start without a token of at least 32 characters, unless `ALLOW_NO_TOKEN=1` is set for local runs. Compare with `crypto.timingSafeEqual`. Accept only `http(s)` URLs whose origin equals a configured `STORAGE_ORIGIN`, for every timeline `src` and both upload links.

### QB5-009 Minor: three progress numbers disagree, move backwards on a retry, and don't show waiting

| | |
|---|---|
| Requirement | PRD R2 (progress bar for the render); API ProjectSummary `progress`, Render `progress` |
| Where | `renders.py:205-216` (`render.progress` = the attempt's raw 0–99; `job.progress` = 10 + 0.88 × that); `projects.py:308-316` (the project summary uses the job's number); `server.mjs:57, 210` (`queued` at the service) |
| Verified | Read |

**What the screens would show**:
- **Two different percentages.** Halfway through, E2 says 50 while the dashboard card and the job say 54.
- **The bar goes backwards on a retry.** On attempt 2, both drop back, to 0 and 10.
- **Waiting looks like running.** While the render waits behind another one at the service, the render is `running` at 0%, possibly for 20 minutes or more, with nothing to tell the creator it is waiting in line.

**Suggested fix**: write one number to the job, the render and the summary. Keep it from going down across attempts, for example by splitting the range between the two attempts. Keep `render.state` `queued` until the service reports `running`, or add a `waiting` flag, and document it in the Render JSON.

### QB5-010 Minor: the tests don't cover the risky paths

| | |
|---|---|
| Requirement | Test plan (each API error and edge case has an automated test); PRD R-1 to R-4 |
| Where | `api/tests/test_renders.py` (4 tests); `MockRenderService.start` finishes synchronously, so a render is never seen `running` |
| Verified | Read |

**Not covered by any test**:
- cancel (G3) during a render;
- project deletion during a render;
- a second Reel of the same user (R-4);
- `402`;
- `too_long`;
- `media_missing` (R-2);
- the time-out path;
- the status after a cancelled re-render (QB5-007);
- `HttpRenderService` (a 404 meaning `lost`, errors, the token);
- `server.mjs` itself: authorization, the 400s, the size limit, cancel of a waiting and of a running render.

CI only runs `node --check` and a type check on the service.

**Suggested fix**: give the mock a "stays running for N polls" mode and a cancel record, then add the API tests above. Add a small Node test for `server.mjs` that stubs `renderMedia`.

### QB5-011 Nit: the documents and comments disagree with the code

- **Time limit:** technical design "Render service" says 23 minutes, task rule 3 (line 938) says 10 minutes, and the code uses 55/56/57 minutes. The comment at `jobs.py:48-49` says 2 frames a second (see QB5-001).
- **Callback:** technical design section 2's component table (line 56) still says the render service talks to a "worker callback". There is no callback (B5 decision).
- **E1 error order:** API E1 says "checked in this order: `not_ready` …, `already_running` …". The code checks "rendering or posting" first (`renders.py:139`). This only matters when a rendering Reel was also edited into a not-ready state.
- **"One-time" links:** API K1 and the technical design call the upload links "one-time". They are presigned PUTs, usable any number of times for 3 hours (see QB5-003).

### QB5-012 Nit: the dashboard list makes two extra queries per Reel on every 2-second poll

`projects.py:308-327`: `summary_json` does one `db.session.get(Asset)` plus a presign for `thumbnailUrl`, and one query for a rendering project's progress, for each item. API B2 says the client polls every 2 s while any item is in progress. Load the thumbnail assets and the active render jobs for the page in one query each.

### QB5-013 Nit: the render image's base isn't pinned

`renderer/Dockerfile:3` uses `node:22-bookworm-slim` by tag. MinIO in the same Compose file is pinned by digest. A rebuild can bring a different Chrome library set or Node minor version without notice. Pin it by digest, as for MinIO.

---

## 2. What passed (all read)

| Area | Result |
|---|---|
| E1 locking and double clicks | The project is locked (`get_project(lock=True)`), then the credit account (same order as B11). The R-4 check runs under the account lock, so two Reels of one user can't both start. A second E1 on the same Reel gets `409 already_running`. The same idempotency key replays the first answer. |
| E1 refusals | `not_ready` with `details.scenes`, `too_long` (> 90 s), `already_running` (this Reel rendering or posting; another Reel rendering), `402` from `credits.reserve`. Scripting and generating are refused. |
| Credits | 2 credits reserved in the E1 transaction. Refunded once on failure, cancel, time-out and project or account deletion (`jobs.fail` / `jobs.cancel`). Charged on success, including an outdated render (R-3: the creator got the video they asked for). |
| Lock order in the worker | `jobs._lock` locks project, then job; `apply` and `on_stop` then lock the render. `_report` updates job then render without the project lock, and commits at once. There is no cycle with G3, B6 or the sweeper. |
| Cancel and project deletion mid-render | The job is cancelled and refunded. The render becomes `cancelled`. The project is settled (`ready`/`draft`) before `deleted_at`. The worker notices within one poll, sends K2b, and the result is discarded if it finished meanwhile (`jobs.run` re-checks under the lock). Apart from QB5-005. |
| Edits mid-render (R-3) | The snapshot (storage keys and the version) is stored with the render. `after_edit` keeps `rendering`. The result is `outdated: true` and the project settles to `ready`/`draft`. B11 and B8 are refused while rendering. Replaced files stay 7 days, longer than any render. |
| R-2 | Every key is checked (`storage.exists`) before rendering. Links are made fresh at the internal address and are valid for 3 h. |
| Storage addresses | The render service gets links from the internal client (`internal_get`/`internal_put`). Browsers get links from `presign_client`, valid for 2 h. The download name is reduced to ASCII letters, digits and `-_`, so the `Content-Disposition` header can't be broken. |
| R-5 and R1 output | 1080×1920, 30 fps (timeline), H.264 CRF 20 with `maxrate 8M` / `bufsize 16M` (90 s ≈ 90 MB plus audio, far under Instagram's limit). AAC audio. Remotion 4.0.530 moves the index to the front (`finalize-fast-start.js`). The measured file was 6.2 Mbit/s. |
| Render service input | 5 MB body limit. `renderId` restricted to `[A-Za-z0-9-]{1,64}`. The same `renderId` answers with its status instead of starting again. `onMediaError` is never part of the timeline sent. |
| Docker image | Runs as `node` after `chown`. Chrome comes from `remotion browser ensure`. `shm_size: 1gb`. Health check on `/health`. The host port is bound to 127.0.0.1. `.dockerignore` keeps sample outputs and scripts out. |
| Secrets | `render_service_token` has `repr=False` and is in the secret-leak test. Production refuses `mock`, tokens shorter than 32 characters and `dev-only…` tokens. Only the dev token is in Compose. No real secret is in any committed file. |
| Schema | `models.Render` matches `renders` in `0001_baseline.sql` (columns, states, cascades). Asset kinds `render`/`thumbnail` are allowed. `projects.thumbnail_asset_id` is set to the new cover. |
| JSON shapes | The Render JSON (including `projectId`, `jobId`, `downloadUrl`, `finishedAt`, `error` only when failed), the E1 answer (`render`, `job`, `creditsReserved`, `balance`), E2 `404` for another user's render or a deleted project's, E3 newest first and capped at 50, and project `latestRender`/`thumbnailUrl` all match the updated API design. |
| Sweeper and Celery limits | Sweeper 57+1 min is after the hard 56 min. The soft limit cancels the render at the service before failing the job `timed_out`. |

## 3. Questions

- **Q1. R-4 says "queue the rest"; the API answers 409.** PRD R-4 says to limit to one active render per user and queue the rest. The updated API E1 and the code refuse a second Reel with `409 already_running`. Which should it be? If 409 stays, update PRD R-4.
- **Q2. What is the in-app notification for R4?** PRD R4 (P0) asks for an in-app notification when a render finishes or fails, and the decision is "in the app only". There is no notifications endpoint: the app only learns from polling E2 or the project list. Is a toast while the app is open, plus the status on the dashboard, enough? Or does F7 need a global poller or a notifications list?
- **Q3. Is Remotion's audio within Instagram's Reels limits?** Remotion's default AAC bitrate is 320 kbit/s (`@remotion/renderer/dist/compress-audio.js:20`). Meta's Reels specification lists 128 kbit/s audio. Instagram also requires Reels of at least 3 s, and E1 has no minimum. Confirm both against Meta's current specification before B6, or set `audioBitrate: '128k'` now.
- **Q4. Should re-rendering an unchanged Reel be charged?** E1 accepts a Reel whose latest render is current and not outdated, and charges 2 credits for an identical video. Should it answer with the existing render instead, or warn first?
- **Q5. What status should a re-rendered posted Reel have?** Re-rendering a `posted` Reel moves it to `rendered` or `render_failed`, so the status no longer shows it was posted. Is that intended, given B6?

## 4. What F7 (render and publish screens) will need

- **Calls:** E1 → `{render, job, creditsReserved, balance}`. Poll E2 every 2 s while `queued`/`running`. Cancel with G3 using `render.jobId`, not a render endpoint.
- **Waiting time:** the render takes about 20 minutes for 30 s on current hardware. The screen should say so, say that leaving the page is fine, and show the result on return (G-13). Until QB5-009 is fixed, use `render.progress`. Expect it to go back to 0 on a retry and to sit at 0 while waiting behind another render.
- **Results:** `video.url` (to play) and `video.downloadUrl` (to save as `<title>.mp4`) expire after 2 h, so re-fetch E2 before downloading on a long-open page. Show the `outdated` badge with "Render again".
- **Failures:** `error.code` is `media_missing` ("generate the scenes again"), `render_failed` or `timed_out`. The job's `creditsRefunded` confirms the refund.
- **Project fields:** `latestRender` can be cancelled or failed while an older good render exists (QB5-007), so F7 may need E3 for the download. `thumbnailUrl` on cards is the latest finished render's cover.
- **Mock API:** the mock in `frontend/src/mocks` must add E1 to E3, `latestRender`, `thumbnailUrl` and the `rendering` progress. In the backend's offline mode the stock clips are fake MP4s, so a real render of an offline Reel always fails. Use `RENDER_SERVICE_URL=mock` for end-to-end runs without provider keys.

---

## Round 2 (2026-11-26)

| | |
|---|---|
| Build tested | `backend/` working tree, B5 still uncommitted on top of HEAD; fixes per [FIX_LOG_B5.md](FIX_LOG_B5.md) |
| Files reviewed | `api/reelforge_api/renders.py`, `jobs.py`, `config.py`, `__init__.py` (Celery settings); `renderer/server.mjs`, `renderer/Dockerfile`; `docker-compose.yml`; `docs/API_DESIGN.md`, `docs/TECHNICAL_DESIGN.md`; the new render tests (read only, to see what they model) |
| How verified | **Read only.** I ran nothing against either stack and did not run the tests (a timing render was running on the main stack). The one thing I executed was a one-line Node calculation that `1080 * (2/3)` and `1920 * (2/3)` are exact integers in floating point (they are: 720 and 1280), and I read Remotion 4.0.530's `validate-even-dimensions-with-codec.js` |
| Product decision taken as given | Local renders 720x1280 (`RENDER_SCALE=2/3`), production 1080x1920. Not reported as a PRD R1 defect |

### Verdict: pass with issues

Both Majors are fixed. Renders have their own one-slot worker (`docker-compose.yml:144-149`, the shared worker no longer takes `render`), limits are 50/51 min under Redis's 1-hour redelivery, and a queued render may wait 3 h. No Blocker or Major remains.

The issues left are Minor, and three of them are in the fixes themselves:
- **QB5-015:** the fix for QB5-005 (wait for an upload before deleting on cancel) does not work. The service reports `cancelled` the moment it is asked, so the worker never waits.
- **QB5-016:** a render that times out still leaves its files behind.
- **QB5-014:** the new start-retry loop retries permanent errors and does not stop an attempt it gives up on.

| Severity | New in round 2 | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 0 | |
| Minor | 4 | QB5-014 to QB5-017 |
| Nit | 3 | QB5-018 to QB5-020 |
| Questions | 2 | Q6, Q7 |

### R2.1 Status of round-1 items

| Item | Status | Notes (all read) |
|---|---|---|
| QB5-001 Major | **Fixed** (for the local configuration) | Separate worker with concurrency 1 and prefetch 1, so the Celery clock starts when the service is free. `task_acks_late` with prefetch 1 means no second message is held unacked. Hard limit 3060 s < 3600 s visibility timeout. Sweeper 52+1 min > hard 51 min. The comment at `jobs.py:48-50` and technical design rule 3 and "Render service" are corrected. Remaining: the retry rule is too coarse for long Reels (QB5-017), and production speed is open (Q6) |
| QB5-002 Major | **Fixed** | `render-worker` takes only `-Q render` with `--concurrency 1 --prefetch-multiplier 1`. The main worker's `-Q` list no longer has `render`. `QUEUED_LIMITS["render"]` = 3 h is applied in `fail_stuck` (`jobs.py:348`); a queued render failed by the sweeper is later skipped by `run()` (state no longer `queued`). Nit: no health check on the new service (QB5-020) |
| QB5-003 Minor | **Fixed** | Keys are `…/{renderId}-{attempt}.mp4/.jpeg` (`renders.py:338-342`), and the service ids are `{renderId}-{attempt}`. `service.cancel` is sent on the unreachable exit (`:272`). `apply` deletes the other attempts' keys (`:387-389`); `run()`'s cancel and failure paths delete all of them. Gap: the start-failure exits don't cancel (part of QB5-014) |
| QB5-004 Minor | **Partly fixed** | Start is retried 12 times about 5 s apart, so roughly 1 min per attempt and 2 min in total. The service's health check allows 240 s for the bundle build, so a slow restart can still fail both attempts. Also see QB5-014 |
| QB5-005 Minor | **Not fixed** | The wait in `_stop` returns at once, see QB5-015. The time-out case named in round 1 is also still open, see QB5-016 |
| QB5-006 Minor | **Deferred, accepted** | Documented as a known gap in the technical design. One consequence is not in that text: after a worker restart, the next render waits at the service behind the orphan render, and its Celery clock is running meanwhile. That briefly brings back QB5-001's "waiting counts against the clock". Worth one more sentence in the known gap |
| QB5-007 Minor | **Fixed** (cancel). Failure behaviour is a documented decision | `on_stop` restores `previousStatus` (`renders.py:421-428`) only when the timeline is unchanged; an edit falls back to `settle_status`. Tests cover rendered, render_failed and edited. See Q7 for how a restored status relates to `latestRender` |
| QB5-008 Minor | **Fixed** | No start without a 16+ character token (`server.mjs:28-29`) or `RENDER_ALLOWED_ORIGINS` (`:31-32`). `timingSafeEqual` is used after the length check (`:163-167`). Both upload links and every scene `visual.src` / `voice.src` and `music.src` must be at an allowed origin. Empty or missing `src` is let through and draws the placeholder. Non-strings, `file:`, `data:` and relative paths have origin `null` or don't parse, so they are refused. The composition (`renderer/src/*.tsx`) loads no other URL from the timeline. Nit: the origins aren't normalised (QB5-020) |
| QB5-009 Minor | **Fixed** | One number, `10 + 0.88 × p` capped at 98, is written to both job and render, only while the service says `running` (`renders.py:222-230, 283-284`). Two leftovers, both cosmetic: (1) until the first report the job shows 10 and the render 0, because `on_start` doesn't set the render's progress; (2) a retry drops back to 10, which the code comment accepts |
| QB5-010 Minor | **Fixed** | Render rules tests and Node service tests were added. The mocks don't model the real service's immediate `cancelled` answer, which is how QB5-015 got through |
| QB5-011 Nit | **Mostly fixed** | E1 order, `too_short`, J2, K1/K2/K2b/K3 and the technical design tables are updated. Leftovers are in QB5-019 |
| QB5-012 Nit | **Deferred to F7, accepted** | Bounded by one page, and the lookups use primary keys and indexes |
| QB5-013 Nit | **Fixed** | `renderer/Dockerfile:4` is pinned by digest |
| Q1 | **Answered** (409 `already_running`) | PRD R-4 still says "queue the rest" (QB5-019) |
| Q2, Q4, Q5 | **Deferred to F7 / B6, accepted** | They are product questions, and the screens and publishing that would act on them are not built yet |
| Q3 | **Answered** | `audioBitrate: '128k'` (`server.mjs:91`). E1 refuses under 3 s with `too_short` and `details.seconds` |
| BUG-069 | **Fixed** | `too_long` / `too_short` carry `details.seconds` (`renders.py:150-163`) |
| BUG-070 | **Fixed** | Order: not_ready, too_long, too_short, already_running (this Reel), then the account lock, already_running (another Reel), 402. This matches API E1 |
| BUG-071 | **Fixed** | Production requires an `http://`/`https://` URL (`config.py`) |
| BUG-072 | **Fixed** | `typeof renderId !== 'string'` is refused (`server.mjs:252`) |

### R2.2 New findings

#### QB5-014 Minor: the start-retry loop treats every error alike, and an attempt it gives up on is not stopped

| | |
|---|---|
| Requirement | PRD R-1; round-1 QB5-003/QB5-004 suggestions ("only count a failure reported by the service … as a failed attempt"; "cancel on every `None` exit") |
| Where | `renders.py:72` (`raise_for_status` turns every non-2xx into `RequestException`); `renders.py:247-257` (the loop) |
| Verified | Read |

- **Permanent refusals are retried.** A `400` (for example a link outside `RENDER_ALLOWED_ORIGINS` after a configuration slip, see QB5-020) or a `401` (token mismatch) is retried 12 times, about 1 minute, then the second attempt does the same. The creator waits about 2 minutes for a generic `render_failed`. The log only says "did not take a render", without the status code or the service's error body.
- **Exits from the loop don't stop the service.**
  - The give-up exit (`:252-254`) and the cancel exit (`:256-257`) return without `service.cancel(service_id)`.
  - If an earlier try was accepted but its answer timed out (10 s), the service draws that attempt anyway.
  - Attempt 2 then waits behind it with the clock running. On cancel, `_discard` deletes keys the orphan uploads to later.
  - This is rare, because the service answers `202` straight after reading the body, but it is the QB5-003 situation again.

**Suggested fix**:
- Retry only on connection errors, time-outs and `503`. Fail the attempt at once on any other 4xx, and log the status and body.
- Call `service.cancel(service_id)` (it never raises) before both early returns.
- Consider a longer start window for the first attempt, close to the 240 s the health check allows.

#### QB5-015 Minor: the "wait for the upload to end" on cancel never waits, so cancelling during the upload still leaves orphan files

| | |
|---|---|
| Requirement | Round-1 QB5-005; technical design 7 task rule 4; technical design "Render service" ("waits up to a minute for an upload under way to end before deleting the files"); API K2b ("a running one is cancelled and nothing is uploaded") |
| Where | `server.mjs:194-196` (`cancelRender` sets `status.state = 'cancelled'` immediately for a running render); `server.mjs:115-117` (uploads already started go on; the check comes after them); `renders.py:290-300` (`_stop` stops waiting as soon as the state is not `queued`/`running`) |
| Verified | Read |

**Sequence**:
1. The service is in `upload()` (`server.mjs:115`).
2. The creator cancels (G3), or deletes the project.
3. The worker's `_stop` sends DELETE. The service marks the render `cancelled` and answers. The upload is not touched: the cancel signal only reaches Remotion.
4. `_stop`'s first status poll reads `cancelled` and returns.
5. `run()` deletes the keys at once (`_discard`).
6. The upload then finishes. The MP4 (up to about 90 MB) and the cover stay under `users/…/renders/` with no asset row, so the B8 clean-up never finds them.

In this case K2b's "nothing is uploaded" is also untrue. The tests don't show the bug, because the mock service has no upload phase and doesn't answer `cancelled` early.

**Suggested fix** (either):
- In `server.mjs`, keep a running render's state `running` (or a new `stopping`) until `run()` finishes. Set `cancelled` in its `catch`/`finally` only. `_stop` then waits as designed.
- Or make `_stop` wait until the K2 status has `finishedAt`. `send` already serialises it, so this needs no change to the service.

Also add an abort to `upload()` (an `AbortController` tied to `cancel`), so a cancel stops the upload instead of waiting for it.

#### QB5-016 Minor: a render that times out leaves its files in storage

| | |
|---|---|
| Requirement | Round-1 QB5-005 (named "the soft time limit" explicitly); technical design 7 task rule 4 |
| Where | `renders.py:285-287` (on `SoftTimeLimitExceeded`: `service.cancel`, then re-raise); `renders.py:356` (`discard` is reached only when both attempts fail normally); `jobs.py:216-218` (`run()` fails the job, with no `_discard`, because `work()` returned nothing) |
| Verified | Read |

A time-out raises out of `work()` past the clean-up. Two kinds of file are left with no asset row:
- the current attempt's files, if the service was uploading at that moment;
- the files of an earlier attempt that stopped answering and uploaded later.

The same happens if the soft limit falls inside `_stop` during a cancel.

**Suggested fix**:
- In `work()`, catch `SoftTimeLimitExceeded` around the attempt loop.
- Stop the attempt with a short bounded wait, delete `made["keys"]`, then re-raise. The hard limit is only 60 s after the soft one, so either keep the wait short or raise the hard limit to soft + 120 s. The hard limit stays under Redis's 1 hour either way.

#### QB5-017 Minor: "retry only if half the limit is left" still starts retries that can't finish for long Reels

| | |
|---|---|
| Requirement | PRD R-1; round-1 QB5-001 ("only retry if enough time is left") |
| Where | `renders.py:332-335` |
| Verified | Read, using the code's own figures |

The second attempt starts whenever elapsed time is at most 1,500 s, so it may get as little as 25 minutes. By the comment at `jobs.py:49`, a 90 s Reel takes about 30 minutes at 720x1280, so anything over about 75 s cannot finish in 25 minutes.

For those Reels, a first attempt that fails at 20 to 25 minutes starts a retry that is bound to time out. The creator then waits the full 50 minutes for `timed_out`, instead of getting `render_failed` at about 25 minutes. The credits are refunded either way.

**Suggested fix**: estimate the time the retry needs, and start it only if that fits in the time left, with a margin. Two ways to estimate it:
- from attempt 1's own rate: progress gained ÷ seconds spent drawing;
- or from the frame count and a configured frames-per-second figure for the deployment.

#### QB5-018 Nit: `RENDER_SCALE` accepts scales that give odd sides, and then reports a size different from the file

| | |
|---|---|
| Where | `server.mjs:38-47` (parsing and the whole-pixel check); `server.mjs:120-121` (reported width and height) |
| Verified | Read; Remotion `validate-even-dimensions-with-codec.js` read |

- **What is correct now.** `2/3` and `1` are correct: 720x1280 and 1080x1920, both even. The floating-point product is exact, which I checked.
- **Odd sides pass the check.** A scale such as `1/8` turns 1080 into 135, which passes the whole-pixel check. For H.264, Remotion then shrinks the source until the side is even, so the file is 134 wide. The service reports `Math.round(135/2)*2` = 136, and that number goes into the asset row.
- **Malformed values are partly accepted.** `"2/3/4"` is silently read as 2/3.

**Suggested fix**: also require that `side × scale` is even, and refuse values with more than one `/`.

#### QB5-019 Nit: document and comment leftovers

- `docs/TECHNICAL_DESIGN.md:115`: the render sequence diagram still says `R-->>WK: Callback (signed) with result; the task also polls as backup`. There is no callback.
- "One-time" links (round-1 QB5-011): `renders.py:5`, `server.mjs:12` and `docker-compose.yml:158` still say "one-time". They are presigned PUTs, valid for 3 hours, one per file per attempt.
- PRD R-4 still says "queue the rest". The answer to Q1 was `409 already_running`, and round 1 asked for PRD R-4 to be updated if 409 stayed.
- API K2b says "nothing is uploaded" for a cancelled running render. That is untrue during the upload until QB5-015 is fixed.

#### QB5-020 Nit: small configuration gaps around the new services

- **No health check on the new worker.** `docker-compose.yml:144-153`: `render-worker` has none. The main worker has a `celery inspect ping` check, and the same check with `-d render@$$HOSTNAME` would work here. Without it, a render worker that can't reach Redis looks healthy, and renders just sit queued for 3 hours.
- **Origins are compared exactly as written.** `server.mjs:31` compares `RENDER_ALLOWED_ORIGINS` entries to `new URL(link).origin` without normalising them. Any of these written in the config makes every render fail with `400`:
  - a trailing slash (`http://minio:9000/`);
  - capital letters;
  - an explicit default port (`https://s3.example.com:443`).

  The worker then reports a generic `render_failed` after about 2 minutes of retries (QB5-014). This fails closed, so it is not a security issue. Normalise each entry with `new URL(entry).origin` at start, and refuse to start on an entry that doesn't parse.

### R2.3 Passed in round 2 (all read)

| Area | Result |
|---|---|
| Per-attempt keys and clean-up | Each attempt has its own keys and service id. Success keeps only the winning pair; `apply` deletes the rest before commit, and if `apply` fails, `run()` deletes all of them. Cancel after success (`run()` re-lock) deletes all. Both attempts failing deletes all (`:356`). Exceptions: QB5-015 and QB5-016 |
| Cancel check in the loops | Polling checks `_cancelled` before each status call, and the start loop checks between tries. `_cancelled` and `_report` commit at once, so no transaction or lock is held while waiting. `_report` writes only while the job is `running`, so it can't overwrite a cancel |
| Half-limit arithmetic | `started` is taken in `work()`, and the comparison uses the same soft limit `dispatch` gives Celery. Attempt 1 is never skipped. Only how long the retry needs is open (QB5-017) |
| Restoring `previousStatus` | It is restored only on cancel, only while the project is still `rendering`, only when `timeline_version` is unchanged, and only from an allowed list. `draft` and anything else fall back to `settle_status`. A failure with an unchanged timeline gives `render_failed` (documented). Project deletion and account deletion are unaffected |
| `RENDER_SCALE` | Defaults to 1. Compose passes `2/3` as a quoted string. A value of 0, empty, above 1 or non-numeric is refused at start. The whole-pixel check works for 2/3. Odd sides: QB5-018 |
| Allowed origins | Checked before the body is queued. Both upload links are required and checked. Empty `src` is allowed by design; non-`http(s)` and relative paths are refused. The duplicate-`renderId` replay comes after the check, so a replay can't bypass it |
| E1 refusal order | Matches API E1 exactly. The R-4 cross-Reel check runs under the account lock, so two Reels of one user can't both start |
| Render worker command | Takes only the `render` queue, has no `-B` (so the scheduler still runs once, in the main worker), a distinct node name, concurrency 1 and prefetch 1. `dispatch` sends to `render` with (3000, 3060). Celery creates missing queues by default, so no `task_queues` entry is needed |
| `QUEUED_LIMITS` in `fail_stuck` | Keyed by job type (`render`); other types keep 30 minutes. A running render is still checked against `TIME_LIMITS["render"]` + 1 min = 53 min, after the 51 min hard limit |
| Config | Production needs an `http(s)` render URL, a 32+ character token, and not `dev-only…`. The service's 16-character minimum is enough, because the API refuses a short token in production and the two must match |

### R2.4 Questions

- **Q6. Is production fast enough for the limits?** At `RENDER_SCALE=1`, the laptop's 0.75 frames a second gives about 60 minutes for a 90 s Reel, which is over the 50-minute soft limit. A single attempt at 90 s needs at least 2,700 frames ÷ 3,000 s = 0.9 frames a second, and a retry needs about twice that. The decision says production uses bigger machines or Remotion Lambda. The technical design's Phase A, though, describes production as this same Compose stack. Should "measured frames per second at 1080x1920 on the production host ≥ about 1.8" be a release check for B5? Or should the limits come from a per-deployment setting?
- **Q7. Should a restored `rendered` status come with a download?** After a cancelled re-render of a `rendered` Reel, the project goes back to `rendered`. Its `latestRender` is the cancelled render, though, whose `video` is `null` (API: "the newest render in any state"), so the card says "Rendered" with no download unless F7 also calls E3. Should the project JSON also carry the latest *succeeded* render, which B6 will need to publish anyway? Or should F7 handle this from E3? Please decide before F7.
