# ReelForge: test report for B5 (rendering)

| | |
|---|---|
| Date | 2026-11-26 |
| Role | Tester (automated tests only; no product code changed) |
| Build tested | `backend/` working tree, B5 uncommitted on top of 1454fbe, **after the QA round 1 fixes** (QA_REPORT_B5.md). The first pass ran on the build before those fixes; everything was re-run on the fixed build |
| How run | A copy of the working tree (no `.env`, `node_modules`, `renderer/out`, `renderer/public/projects`) run as its own Compose project `b5test`. Ports moved (Postgres 54330, Redis 63800, MinIO 9010/9011, Mailpit 8035, API 5010, renderer 3110) and `LLM/VOICE/STOCK/IMAGE_PROVIDERS=mock`. pytest inside `b5test-api-1`; the Node tests in a container of the `b5test-renderer` image on the `b5test_default` network. The main stack was not touched. The stack was taken down with `down -v` afterwards |
| Checked against | API design group E, group K, the Render object, error table, B2 summary (`thumbnailUrl`, `progress`); PRD 5.7, 11.6 R-1 to R-5, CR-1/CR-2; TECHNICAL_DESIGN "Render service" |

## 1. Results

| Run | Result |
|---|---|
| New and starter render tests (`test_renders.py`, `test_renders_rules.py`, `test_render_service_http.py`) | **77 cases: 74 passed, 3 failed** (BUG-069, BUG-070, BUG-071), the same in a targeted run and in the full run |
| Full pytest suite, one run (26 min, with the main stack rendering at the same time) | **1457 tests: 1433 passed, 24 failed**. 3 are BUG-069 to BUG-071. The other 21 are older production-settings and provider tests in `test_worker_and_config.py`, `test_voice_providers.py` and `test_visual_providers.py`; they fail only because this stack was started with `VOICE/STOCK/IMAGE_PROVIDERS=mock` (observation 3). Re-run with the compose defaults (`elevenlabs,edge` / `pixabay` / `cloudflare`): 277 passed, 0 failed |
| `ruff check .` / `ruff format --check .` (in the api container) | All checks passed / 91 files already formatted |
| Node render service tests (`renderer/test/server.test.mjs`), run twice | **12 tests: 11 passed, 1 failed**, the same both runs (BUG-072) |
| Real render, end to end (API, worker, render-worker, renderer, MinIO) | AI-image Reel of 3.9 s: succeeded, `rendered`, 2 credits kept. Stock-clip Reel (the mock's fake 2 KB MP4): failed at the renderer, retried once, `render_failed`, refunded |

The three failing pytest cases are BUG-069 (`too_long` details), BUG-070 (E1 order) and BUG-071 (empty `RENDER_SERVICE_URL`); the failing Node test is BUG-072. Each failed the same way on every run.

One test failed on the first build and passes after QA round 1: `test_cancelling_a_new_render_of_a_rendered_reel_keeps_it_rendered` (QB5-007).

## 2. What was added

| File | Tests |
|---|---|
| `api/tests/render_support.py` | Helpers: a ready Reel, E1/E2/E3 calls, the Render field list, per-attempt storage keys, running a second request on its own thread while the worker is busy |
| `api/tests/test_renders_rules.py` | 53 functions, 58 cases, with the offline stand-in (`RENDER_SERVICE_URL=mock`) |
| `api/tests/test_render_service_http.py` | 11 functions, 15 cases: `HttpRenderService` against a fake K1/K2/K2b service on 127.0.0.1 that uploads to the presigned links like the real one |
| `renderer/test/server.test.mjs` | 12 tests (`node --test`) against a running render service, with tiny real renders (108x192 colour scenes) |
| `api/tests/test_renders.py` (starter, unchanged) | 4 tests |

How to run the Node tests (also in the file's header): start a render service that allows the test's upload address, then run the test beside it on the same network.

```
docker run -d --name rut --network <project>_default --shm-size 1g \
  -e RENDER_SERVICE_TOKEN=<16+ characters> -e RENDER_ALLOWED_ORIGINS=http://rtest:4100 \
  -e RENDER_SCALE=0.5 <project>-renderer
docker run --rm --network <project>_default --network-alias rtest -v <path to renderer>:/r \
  -e RENDER_URL=http://rut:3000 -e RENDER_TOKEN=<the token> -e RENDER_SCALE=0.5 \
  -e UPLOAD_BASE=http://rtest:4100 <project>-renderer node --test /r/test/server.test.mjs
```

### Covered

- **E1 refusals**:
  - `not_ready` lists every scene of a draft, or only the edited scene of a ready Reel.
  - `not_ready` while generating, and while the script is being rewritten.
  - `too_long` over 90 s (230 words of mock voice); `too_short` under 3 s ("Go now."). A 4.3 s Reel is accepted.
  - `already_running` for the same Reel, for a `posting` Reel, and for another of the creator's Reels (R-4). Another creator's render doesn't block. A cancelled or failed render no longer blocks.
  - R-4 under a race: two of a creator's Reels asked for at the same moment from two browsers start exactly one, and only one charge is made.
  - 402 with `needed`/`balance`; nothing is created and the status is unchanged.
  - Order: with another Reel rendering and a balance of 0, a draft gets `not_ready`, a long Reel `too_long`, a short one `too_short`, a ready one `already_running`; with nothing rendering, 402.
  - 404 for another user's project, a malformed id and an unknown id; 401 signed out; 403 unverified; 404 for a deleted project.
- **Idempotency**: the same key replays the first 202, before and after the render finished, with one render and one charge. The same key with another body is `409 idempotency_conflict`. A refused request can be retried with its key.
- **Credits**: 2 reserved (ledger `reserve -2`), kept on success, refunded on failure, cancel, time-out, project delete and `media_missing`. The ledger always matches the balance.
- **R-1**:
  - One failed attempt is retried as `{renderId}-2`, with one charge.
  - Two failures give `render_failed` with the documented message.
  - No second attempt once more than half the soft limit is used.
  - Over HTTP, an attempt fails when K2 says `failed`, answers 404 (service restarted), or K1 keeps refusing. A K1 refusal is asked 12 times (`START_TRIES`) before the attempt counts as failed.
  - Over HTTP, an attempt also fails when K2 errors 15 times in a row (`UNREACHABLE_POLLS`); that attempt is then stopped with K2b.
  - A service that answers 503 twice and then takes the render uses one attempt. A service that is down fails the render with a refund.
  - The files an attempt uploaded before failing are deleted. The second attempt's own files are kept and served. Two such failures leave no files.
- **R-2**: a voice file deleted after E1 fails the job with `media_missing`, without calling the service. The service gets signed `http` links that download.
- **R-3**:
  - The service receives the timeline as it was at E1, even after an edit.
  - An edit during the render: the render is `outdated`. The project is `ready`, or `draft` after a narration edit, and keeps the new cover.
  - A failure after an edit gives the edit's status, not `render_failed`.
  - A finished render becomes `outdated` at the next edit.
- **Cancel (G3) and delete**:
  - A queued render: cancelled, refunded, never sent to the service, `409 job_finished` on a second cancel.
  - A running render (cancelled from another request while the worker polls): K2b is sent for that attempt, the render is `cancelled`, the files the stand-in had written are deleted, and the project returns to `ready`.
  - A re-render of a `rendered` or `render_failed` Reel, cancelled, restores that status. After an edit, the edit's status (`draft`) applies.
  - Deleting the project mid-render: the job is cancelled at the service, refunded, the files are deleted, and E2/E3 answer 404.
  - Running out of time (`SoftTimeLimitExceeded`): K2b is sent, the job fails `timed_out`, the render error says `timed_out`, and the project is `render_failed`, refunded.
- **Progress**: while the service says `running`, the project (B2), the job (G1) and the render (E2) show the same number, between 0 and 100. `progress` is null once rendered.
- **Generate and script rewrite** are refused (`409 already_running`) while rendering, without charge.
- **JSON**:
  - The Render fields are exact, with ISO times and `jobId`. `video` has exact fields; the duration matches B12.
  - `downloadUrl` carries `attachment; filename="Caf-Morning-habits.mp4"` for `Café "Morning" / habits`, or `reel.mp4` for a title with no ASCII letters. `url` plays inline; both links download, and the size matches.
  - `latestRender` is the newest render in any state.
  - `thumbnailUrl` (project and B2 list) is null before a render, the finished render's cover afterwards. It is kept when a newer render fails, and replaced by a newer finished render.
  - A duplicate starts with no renders.
  - E3 is newest first and capped at 50.
  - The size the service reports (`width`/`height`, RENDER_SCALE) is saved on the assets.
- **E2/E3 ownership**: 404 for another user, a malformed or unknown id, and a deleted project; 401 signed out.
- **Production settings**: `RENDER_SERVICE_URL=mock`, no token, a 31-character token, a `dev-only…` token, and either setting left out are refused; a real URL and token are accepted; development may use `mock`.
- **HTTP contract (worker side)**:
  - K1 is `POST /renders` with exactly `renderId`, `timeline`, `videoUploadUrl`, `thumbnailUploadUrl`. The `renderId` is `{id}-{attempt}`, within the service's pattern, and the timeline has no `onMediaError`.
  - Every call carries the Bearer token; K2 polls the same id.
  - A "succeeded" with no uploaded files does not succeed: the job fails and is refunded.
- **Render service (Node, a real Remotion render)**:
  - Health needs no token and reports the Remotion version from `package.json`. Without the token, with a wrong one or with a wrong scheme, every other call is 401.
  - 400 for bad JSON, an empty body, `null`, an array, and a missing, empty, slashed or 65-character `renderId`. Also 400 for a timeline without `scenes`, and for missing upload links.
  - 400 for any link outside `RENDER_ALLOWED_ORIGINS`: another host, port or scheme, `user@host` tricks, `file:` or not a URL. This covers both upload links, a visual, a voice and the music.
  - A 6 MB body is refused and the service keeps answering. Unknown ids and addresses answer 404.
  - It refuses to start without a token, with a 15-character token, without allowed origins, or with `RENDER_SCALE` 0, 1.5 or `big`.
  - A tiny render uploads an MP4 (`ftyp`) and a JPEG cover with the right content types. It reports `sizeBytes`, `durationSeconds` and the scaled width and height. The same `renderId` again answers with its status and renders nothing.
  - A visual that 404s fails the render with an error (at most 500 characters), and nothing is uploaded.
  - Queue: while one render runs, the others wait (`busy`, `waiting: 3`). K2b drops a waiting render and stops the running one, which uploads nothing. The next two then run in order, and the service is idle afterwards. K2b of a finished render changes nothing.
- **End to end** (one-off script, not committed):
  - Before the round 1 fixes, a 3.9 s AI-image Reel rendered in about 50 s on the shared worker. ffprobe: H.264 1080x1920 at 30 fps, AAC, 3.95 s, 507 KB (R1).
  - After the fixes, on the new render-worker, the same Reel took 162 s, because the main stack was rendering at the same time. It succeeded at 720x1280 (`RENDER_SCALE`) with matching job and render progress.
  - The stock-clip Reel failed at the renderer ("Invalid data found when processing input"), tried twice, ended `render_failed`, and was refunded.

### Not covered (deliberately)

- The Celery round trip of a render on the real worker inside pytest: the worker uses the development database, so it was checked by the one-off end-to-end script instead.
- A worker restart mid-render (QB5-006, a documented known gap).
- R-5 for a 90 s Reel at 1080x1920 (about 20 minutes per render here). The bitrate cap is in `server.mjs` but no 90 s file was measured.
- The 5 MB body answer code: the Node test accepts 400, 413 or a closed connection, as long as the service keeps running.

## 3. Bugs

### BUG-069 (Minor): `409 too_long` has no `details.seconds`

- **Test**: `api/tests/test_renders_rules.py::test_too_long_names_the_length`
- **Steps**: B3 with a pasted 230-word script → B11 → run the jobs (`ready`; B12 says 94.43 s, `tooLong: true`) → `POST /projects/{id}/render`.
- **Expected**: `409 too_long` with `details.seconds: 94.43` (API design error table: "Timeline over 90 s; `details.seconds`").
- **Actual**: `{"error": {"code": "too_long", "message": "This Reel is longer than 90 seconds. Shorten it to render."}}`, no `details`.
- **Cause (reading)**: `renders.start_render` raises `ApiError(409, "too_long", …)` without details. The new `too_short` has none either; the error table doesn't list `too_short` at all (see observation 4).

### BUG-070 (Minor): a rendering Reel that was edited into a not-ready state answers `already_running`, not `not_ready`

- **Test**: `api/tests/test_renders_rules.py::test_a_rendering_reel_with_a_scene_no_longer_ready_answers_not_ready_first`
- **Steps**: ready Reel → E1 (202, `rendering`) → `PATCH /scenes/{first}` with new narration (the voice is now outdated) → E1 again.
- **Expected**: `409 not_ready` with `details.scenes: [first]`. E1 says the errors are "checked in this order: not_ready, too_long, too_short, already_running …".
- **Actual**: `409 already_running` ("This Reel is already rendering.").
- **Cause (reading)**: `start_render` checks `project.status in ("rendering", "posting")` before building the snapshot. QA noted the same as part of QB5-011 (Nit). Either the code or the sentence in E1 should change; the test follows the document as written.

### BUG-071 (Minor): production starts with an empty `RENDER_SERVICE_URL`, and then every render fails

- **Test**: `api/tests/test_renders_rules.py::test_production_refuses_an_unsafe_render_service_setting[empty-url]`
- **Steps**: `Settings(env="production", …, render_service_url="", render_service_token=<34 random characters>)`, with real providers named.
- **Expected**: refused like `mock` ("RENDER_SERVICE_URL and a long random RENDER_SERVICE_TOKEN must be set in production"). A deployment with `RENDER_SERVICE_URL=` in its environment has not set it.
- **Actual**: accepted. `HttpRenderService` then calls `"" + "/renders"`; `requests` raises `MissingSchema` on every start. Each attempt is asked 12 times, so every render fails after about 2 minutes, and is refunded.
- **Cause (reading)**: `config.py` refuses only `== "mock"`. Checking for an `http(s)://` URL would also catch the empty value.

### BUG-072 (Minor): the render service accepts a numeric `renderId`, then can't find it

- **Test**: `renderer/test/server.test.mjs` › "a renderId that is not a string is refused, since it could never be polled or stopped" (fails the same way on both runs).
- **Steps**: `POST /renders` with `"renderId": 123456789` (a JSON number), a valid timeline and allowed upload links → `GET /renders/123456789` → `DELETE /renders/123456789`.
- **Expected**: `400`, like any other `renderId` outside `[A-Za-z0-9-]{1,64}`.
- **Actual**: `202 {"state": "running"}`, but GET and DELETE answer `404 unknown render`. The render runs to the end, holds the one render slot, can't be stopped and uploads to the given links.
- **Cause (reading)**: `/^[A-Za-z0-9-]{1,64}$/.test(renderId ?? '')` turns the number into a string to test it. The `Map` then stores the number as the key, while the URL lookup uses a string. The worker always sends a string, so only another caller with the token can do this. A `typeof renderId === 'string'` check fixes it.

## 4. Observations (not counted as bugs)

1. **A "succeeded" render with no files fails as `internal_error`, without a retry.** If K2 says `succeeded` but the MP4 isn't in storage, `storage.size` raises in `work`. The job fails `internal_error` (refunded, project `render_failed`). It is neither `render_failed` nor retried (`test_a_success_without_the_files_does_not_succeed` accepts any failure).
2. **Remotion prints a non-JSON stack trace after renders.** After a successful render, `b5test-renderer-1` printed `ProtocolError: Protocol error (Page.bringToFront): Target closed` with a multi-line stack. It is harmless, but it breaks the one-JSON-object-per-line log format the API and worker follow.
3. **The production tests depend on the container's provider variables.** In a stack started with `VOICE_PROVIDERS=mock` (as here), `Settings(**PRODUCTION)` is refused for the voice provider. My new tests pin real providers to avoid this. The older ones in `test_worker_and_config.py`, `test_voice_providers.py` and `test_visual_providers.py` do not: 21 of them fail in such a stack (see the full-suite result above). They pass with the compose defaults. Pinning the providers in their `PRODUCTION` dicts would make them independent of the environment.
4. **`too_short` is missing from the API design's error table** (it is only in the E1 text), and neither length error carries the length.
5. **The local render size differs from the spec.** Locally, renders come out at 720x1280 (`RENDER_SCALE=0.6667`). This is documented as a product decision, but R1 ("1080×1920") holds only where `RENDER_SCALE=1`.
