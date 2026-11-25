# ReelForge: test report for B4 slice 2 (visuals)

| | |
|---|---|
| Date | 2026-11-25 |
| Role | Tester (automated tests only; no product code changed) |
| Build tested | `backend/` working tree, slice 2 uncommitted on top of 495ff76, Compose stack running |
| How run | `docker compose exec -T api python -m pytest -q` in the `api` container, one full run at a time |
| Checked against | API design C8, C9, C10, Scene `visual`, job codes (`stock_unavailable`), B5, B10, C2; technical design "Stock (Pixabay, later Pexels)", 7; PRD VI1-VI6, G-5 to G-9; pipeline strategy 3-4 |

## 1. Results

| Run | Result |
|---|---|
| Before slice 2 (round 3 of slice 1) | 1090 passed |
| Full suite with slice 2 and the new tests | **1248 tests: 1245 passed, 3 failed** (5 min 29 s) |
| New visual files alone, run twice | 155 passed, 3 failed, both times |

The 3 failures are BUG-042 (1 test) and BUG-043 (2 cases). No older test fails.

| Test | Bug |
|---|---|
| `tests/test_visuals.py::test_the_offline_mock_stock_makes_no_network_call` | BUG-042 |
| `tests/test_visuals.py::test_cancelling_a_take_started_with_new_fields_does_not_bring_back_the_old_visual_as_ready[C8 with a new prompt]` | BUG-043 |
| `tests/test_visuals.py::test_cancelling_a_take_started_with_new_fields_does_not_bring_back_the_old_visual_as_ready[C10 stock on an AI scene]` | BUG-043 |

## 2. Existing tests changed

| Test | Why |
|---|---|
| `test_projects_manage.py::test_the_estimate_counts_what_is_missing_or_outdated` | `ai_video` now costs `visual_credits("ai_video")` (1 until B7); read from the product, not written out |
| `test_voice_providers.py`: the two ElevenLabs header checks (4 cases) | `http` now always sends `User-Agent`; expected headers are `{"User-Agent": USER_AGENT, "xi-api-key": ...}`, with `USER_AGENT` imported from the product |
| `test_projects_manage.py::test_duplicating_copies_script_and_settings_and_reuses_files_for_free` | **Flaky, cause found.** Its last line compared the whole project before and after (`get_project(...) == source`), including presigned links, which change when a second passes between the two reads. This was in the test since slice 1 (voice `url`); I fixed only the scene-copy comparison then. Now both comparisons go through `unsigned()`. Measured: 2 failures in 10 paired runs before, 0 in 15 after |
| `projects_support.unsigned()` | Also cuts `thumbnailUrl` (a presigned link for images and stored stock thumbnails) |
| `voice_support.no_real_voice_services` (the network guard, used by every voice and visual test file) | Extended: calls to ElevenLabs, Edge, Pixabay, Cloudflare, Google and Groq hosts fail the test (also checked at teardown); any other address gets `requests.ConnectionError`, as if offline, and is recorded in `NETWORK_ATTEMPTS`, so a test can catch code that reaches out. The guard test now covers the new hosts |

## 3. What was added

| File | Tests |
|---|---|
| `api/tests/visual_support.py` | Helpers: C8/C9/C10 calls, storage listing, asset rows, an upload asset as D1/D2 will leave it, JPEG/PNG/GIF bytes |
| `api/tests/test_visuals.py` | 49 functions, 88 cases, with the offline mocks |
| `api/tests/test_visual_providers.py` | 25 functions, 70 cases, with Pixabay, Cloudflare and downloads stubbed |

### Covered

- **C8 stock**:
  - Job shape, `queued` → `running` (read during the search) → `ready`, 0 credits, nothing in the ledger.
  - The Scene `visual` with all 11 fields (`kind video`, `provider`, `author`, `sourceUrl`, `width`/`height`, `stockFallback`).
  - The job output, the stored MP4 behind the link, the asset row and key, and `meta.providerId`/`author`/`pageUrl`.
- **Clip choice**:
  - The first clip at least as long as the scene; the longest when none is.
  - The voice's length when the voice is made.
  - Regenerating never gives the current clip again, and replaced files are released.
- **Query order and fallbacks**:
  - The query falls back from stock query to visual prompt to narration.
  - The simpler-keywords retry (G-6) finds a clip.
  - Nothing found leads to a free AI image with `stockFallback: true` (job and Scene), `kind image`, `thumbnailUrl` = `url`, and a JPEG behind the link.
  - Without a stock provider, a stock scene gets the free image and C9 is 503.
  - A failing stock provider gives `provider_failed`, the scene shows `failed` with `lastError`, and no file is kept.
- **C8 AI image and ai_video**:
  - 1 credit each (`ai_video` is an image until B7).
  - The prompt carries the scene prompt, the mood cue, `<style> style, featuring <character>` and the 9:16 suffix.
  - Each take is new (new seed, old file released).
  - `[image-fail]` gives `provider_failed` and `[image-refuse]` gives `content_refused`; both are refunded with no files.
- **C8 body and refusals**:
  - Sent fields are saved first and used.
  - Invalid bodies are refused and change nothing: `upload`, an unknown source, null, too long, snake_case, unknown fields.
  - An upload scene needs a new source, and nothing is saved when refused.
  - Errors: 404 (other user, unknown, malformed id), 401, 409 when busy or while scripting, 403 unverified.
  - 402 for an AI image, with nothing saved (stock stays free).
  - 503 with the mocks switched off, with nothing saved.
  - Idempotency-Key replay and conflict.
  - A stuck job counts after 8 minutes (not at 7.5).
  - The `visuals` queue gets 360/420 s limits (checked with Celery dispatch and `apply_async` replaced).
- **C9**:
  - Response shape `{items, nextPage, query}`, with item fields as documented.
  - `nextPage` is null on the last page, and on an empty result.
  - The default query is the stock query, else the visual prompt; with neither, 400.
  - Results are cached for 24 h and shared by case and whitespace variants; another page is another search.
  - Validation of `q` (101 characters, blank) and `page` (0, 26, text, negative).
  - Access: 404 other user, 401, 403 unverified.
  - 503 when the provider fails or is switched off.
  - 60 searches an hour, then 429 with `Retry-After` = `details.retryAfterSeconds`, counted per creator (checked on the rate-limited app).
- **C10 stock**:
  - 202 with 0 credits; the source becomes `stock`; the clip with the chosen id is stored.
  - A clip that no longer exists gives `stock_unavailable` with the documented message.
  - Bad ids give 400 `stock`; neither or both fields give 400.
  - A visual being made is cancelled and refunded first.
  - User level (an unverified creator may choose).
- **C10 upload**:
  - Video and image uploads answer 200 `{scene, estimate}`: `upload` source, `ready` status, `kind`, provider `upload`, `thumbnailUrl` for images. The old visual is released and no job is created.
  - A visual being made is cancelled.
  - These are refused with 400 `assetId` and leave the scene unchanged: an unknown id, a malformed id, another user's asset, another project's asset, an asset with no project, `voice` kind, `pending_upload`, `rejected`, `audio/*`, and a replaced asset.
- **Edits while a visual is made**:
  - C2 `visualPrompt`/`stockQuery`/`visualSource` (with and without an earlier visual) cancels and refunds the job; the scene ends `outdated` or `missing`.
  - An edit during the search drops the downloaded clip and deletes the files.
  - Narration and on-screen text edits let it finish.
  - B5 `style` cancels only the AI visual job; B5 `character` during generation drops the image and deletes the file.
  - A direct database change of the stock query is caught at save time (`scene_changed`, file deleted).
  - Deleting the scene mid-job cancels it, sets `sceneId: null` and deletes the file.
- **Project status (`after_edit`)**:
  - A ready project stays ready during a new visual, during an unrelated edit, and after the take.
  - A failed take sends it to `draft`; a retry brings it back to `ready`.
  - A draft becomes `ready` when its last visual arrives (an upload).
  - The B10 estimate counts `ai_video` at 1.
- **Regression**: a duplicated scene shares the visual; the shared file is released only when neither scene uses it.
- **Pixabay (stubbed)**:
  - Request: URL, `key`, `safesearch=true`, `q`, `page`, `per_page=20`, `User-Agent`, and the key never in the answer.
  - The C9 item built from a hit; `nextPage` with the 500-result cap.
  - 500, 429, a missing `hits` list, HTML and connection errors are tried 3 times, then 503; a rejected key is not retried.
  - The rendition rule: smallest at least 1080 high, else the tallest, nothing over 150 MB; a hit without a usable rendition is left out.
- **Pixabay clip in a job**:
  - The clip is streamed with the `User-Agent` header.
  - The thumbnail (the small rendition's still) is stored as `-thumb.jpg` and served by `thumbnailUrl`.
  - The asset keeps the downloaded rendition's size, the license, the author and the page.
  - A 404, non-JPEG, too-large or unreachable thumbnail doesn't fail the clip; `thumbnailUrl` is then null.
  - A clip that 404s, isn't an MP4, or is unreachable fails the job and leaves no file.
  - C10 looks the clip up by `id`; a gone clip gives `stock_unavailable` without a download.
- **`download()` on its own**: returns the file; stops at the size cap (not retryable).
  - Retryable: 503, timeout, connection error.
  - Not retryable: 404, 403, non-MP4, empty answer.
- **Cloudflare (stubbed)**:
  - URL with account and model, `Bearer` token plus `User-Agent`, body exactly `{prompt, steps: 4}` (no seed).
  - JPEG and PNG are stored with the right type and extension; the token never appears in the job.
  - These are tried 3 times, then `provider_failed`, refunded, no file: an empty `result`, a null `result`, a string `result`, bad base64, base64 of text, GIF, 500, HTML.
  - 400 NSFW gives `content_refused` without a retry.
  - Without a token, an AI image is 503 and nothing is sent.
- **Helpers**:
  - `checked_image` accepts JPEG and PNG only; GIF, empty, text and truncated input are retryable errors.
  - `image_prompt` builds the anchor, falls back to "cinematic", and caps at 2000 characters.
- **Settings**:
  - Unknown or empty `STOCK_PROVIDERS`/`IMAGE_PROVIDERS` are refused in every environment.
  - The mocks are refused in production, also when mixed in or padded with spaces.
  - Defaults are `pixabay`/`cloudflare` and the FLUX model.
  - The Pixabay key and Cloudflare token are not in `repr`/`str`.
  - The mocks are allowed in development and test; Pixabay without a key is skipped.

### Not covered (deliberately)

- Real Pixabay and Cloudflare calls (forbidden: real keys). Real answer shapes are assumed from the docs and the prototype.
- Clips larger than 16 MB spilling to disk, and a real 150 MB transfer. The cap is tested by lowering the limit.
- The Celery round trip on the real worker.
- D1/D2 uploads (milestone B7). Upload assets are inserted as those endpoints are expected to leave them.

## 4. Bugs

### BUG-042 (Minor): the offline mock stock provider fetches its thumbnails from the internet

- **Test**: `tests/test_visuals.py::test_the_offline_mock_stock_makes_no_network_call`
- **Steps**: test settings (`STOCK_PROVIDERS=mock`); C8 on a stock scene; run the job; look at the network calls the guard recorded.
- **Expected**: no network call (technical design 9.0: every provider has an offline mock for local development and tests).
- **Actual**: `GET https://example.com/clip/1.jpg` from `visuals._store_clip`. In the tests the guard answers it as offline. In local development with the mock it really goes out to example.com, gets HTML, and stores no thumbnail.
- **Cause (reading)**: `MockStock._clip` gives `thumbnail_url="https://example.com/clip/N.jpg"`. `_store_clip` downloads any `https://` thumbnail, whoever the provider is. As a result, mock-made visuals always have `thumbnailUrl: null`, so the web app can't be tried with thumbnails offline.

### BUG-043 (Minor): cancelling a visual started with new fields brings the old visual back as `ready`

- **Test**: `tests/test_visuals.py::test_cancelling_a_take_started_with_new_fields_does_not_bring_back_the_old_visual_as_ready` (cases `[C8 with a new prompt]`, `[C10 stock on an AI scene]`)
- **Steps (C8)**: scene with a ready AI image for "a cat"; `POST /scenes/{id}/visual/regenerate {"visualPrompt": "a dog"}` (202); `POST /jobs/{job}/cancel`; `GET /projects/{id}`.
- **Steps (C10)**: scene with a ready AI image; `POST /scenes/{id}/visual/select {"stock": "mock:1"}` (the source becomes `stock`); cancel the job.
- **Expected**: `outdated`. C8 saves sent fields "as with C2", and C2 marks the visual outdated when the prompt or source changes (API design C2, C8; DB design 5). This is the same rule as slice 1's BUG-037 for voices.
- **Actual**: `ready`. The scene shows the cat image as the finished visual for "a dog", or an AI image as the visual of a stock scene.
- **Cause (reading)**: C8 and C10 change the fields before `start_visual_job` records `previousStatus` ("ready") and the job input. `VisualJob.on_stop` then compares the scene with an input that already has the new fields, sees no change, and restores `ready`. The same gap makes `after_edit` count the scene as ready while the new take runs, so the project shows `ready` meanwhile.

## 5. Observations (not counted as bugs)

1. **Regenerating cycles through two clips.** `exclude` holds only the current clip, so repeated regenerating alternates between the two best clips (A, B, A, …). That matches "never the current one". The API design's "next fitting stock clip" could be read as moving on through the results. Product question.
2. **A failing stock provider fails the job** (`provider_failed`) instead of falling back to the free AI image. G-6 names only "no result", and the code falls back when no stock provider is configured. Product question whether an outage should also fall back.
3. **C10 accepts a `mock:N` id when the mock isn't configured.** It answers 202 and the job then fails `provider_unavailable`. This can't happen through C9 results.
4. **B5 doesn't cancel a stock job heading for the image fallback.** A stock job isn't cancelled by B5 `style`/`character`. If it ends in the AI-image fallback, that image uses the old style and is kept: `apply` checks style only for AI sources.
5. **A blank `q` on C9 is 400.** `q=%20%20` is refused rather than meaning "use the scene's query". It is tested as 400; tell the web app to leave `q` out instead of sending blanks.

---

## Round 2 (after FIX_LOG_B4_S2.md)

### Results

| Run | Result |
|---|---|
| Visual files alone, before the new tests | 185 passed |
| Full suite, one run | **1284 tests: 1284 passed, 0 failed** (5 min 33 s) |

BUG-042 and BUG-043 are fixed; their tests pass unchanged. No new bugs.

### Tests changed because behaviour changed on purpose

| Change | Test change |
|---|---|
| Downloads only from `cdn.pixabay.com` / `pixabay.com` | Stub addresses moved from `cdn.pixabay.example` to `cdn.pixabay.com`. The thumbnail is the small rendition's still |
| Download failure tries the next clip; search failure or nothing downloadable goes to the AI image | `test_a_clip_that_cannot_be_downloaded_fails_the_job...` replaced by `test_a_clip_that_cannot_be_downloaded_makes_way_for_the_next_one` (404, non-MP4, connection error, 302 redirect), plus `test_three_failed_downloads_end_in_the_free_ai_image` (exactly 3 clips tried; no `.mp4` kept). `test_a_failing_stock_provider_fails_the_job...` became `test_a_failing_stock_search_goes_to_the_free_ai_image` |
| AI images get a 360-pixel thumbnail | The fallback test checks `meta.thumbnailKey` = `<key>-thumb.jpg`, `thumbnailUrl` != `url`, and a JPEG at most 360 wide behind it |
| Redirects are not followed | `download()` 301/302 → non-retryable error; thumbnail 302 → no thumbnail; every clip and thumbnail request is sent with `allow_redirects=False` |

### New tests

- **Download hosts (QB4S2-005)**:
  - In a job, these addresses are never called and the job ends in the free image: `http://`, another host, port 8443, `cdn.pixabay.com.evil.example`, `cdn.pixabay.com@evil.example`, vimeo, `ftp://`.
  - `allowed_download` as a unit (10 cases), including `:443` allowed, `player.pixabay.com` refused, and a mock clip never allowed.
  - A thumbnail on another server is not fetched.
- **Ids (QB4S2-007, obs. 3, BUG-042)**:
  - A provider 400 on get-by-id gives `stock_unavailable`, not retried.
  - `mock:1` is refused with 400 when only Pixabay is set up.
  - `pixabay:N` is refused with 400 when Pixabay is switched off, or has no key.
- **`meta.seen` (QB4S2-001)**: five regenerates give five different clips, and `seen` lists the earlier four. With 35 older entries, the new `seen` keeps the last 30, ending with the clip it replaced.
- **Free fallback cap (QB4S2-004)**:
  - With 19 used, the 20th is allowed; with 20 used, `no_stock_match` with the documented message. The scene shows `failed` and `lastError`, no file is kept, nothing is charged.
  - The count is per creator and per day (another creator's or yesterday's count doesn't block); the key expires within 2 days.
- **C8 rate limit**:
  - With 59 used, the next C8 starts.
  - A refused 409 is not counted.
  - The 61st is 429 with `Retry-After` = `details.retryAfterSeconds`, and nothing is saved or queued.
- **BUG-043 fix**: C8 with unchanged fields keeps a ready project `ready`. C8 with a new stock query makes it `draft` at once, and `ready` again when the take succeeds.
- **Fallback images and style (QB4S2-006)**:
  - B5 `character` outdates a stock scene showing a fallback image, but not a scene with a stock clip.
  - A fallback image being made when B5 `style` changes is dropped (`scene_changed`), its file is deleted, and the scene shows `missing`.
- `USER_AGENT` is `ReelForge/1.0`.

### Observation (not a bug)

- `meta.seen` exhausts the first results page. Regenerate only searches page 1 (20 clips on Pixabay, 5 with the mock). Once a scene has had all of them, the next regenerate goes to the free AI image, which counts toward the 20 a day, rather than to page 2. With one-word queries there is no simpler query to try first. Product question whether regenerate should move on to the next page.

---

## Round 3 (QB4S2-010 to 012, FIX_LOG_B4_S2.md "Round 2")

### Results

| Run | Result |
|---|---|
| Full suite, one run | **1291 tests: 1291 passed, 0 failed** (7 min 13 s) |

No new bugs. No existing test needed changing. The 7 tests below are new and pass.

### New tests

| Test | What it checks |
|---|---|
| `test_visuals.py::test_a_style_change_cancels_a_new_take_on_a_scene_showing_a_fallback_image` | QB4S2-010: scene showing a fallback image, C8 queued, B5 `style` → the job is `cancelled`; the scene is `outdated` right away and still `outdated` after the worker runs (never `ready` with the old image) |
| `test_visuals.py::test_regenerate_looks_at_later_result_pages_before_the_ai_image` | QB4S2-011: `STOCK_PAGES` is 3. With all of page 1 already seen, the take searches page 2 and gets `mock:6`. The provider is asked for pages 1 and 2 only; cached searches are cleared first so every page reaches it |
| `test_visuals.py::test_after_three_pages_of_seen_clips_the_scene_gets_the_ai_image` | With 15 clips seen, pages 1, 2 and 3 are searched (no page 4; a one-word query has no simpler query), then the free AI image |
| `test_visuals.py::test_only_fallback_images_actually_made_are_counted` | QB4S2-012: a fallback image that fails leaves the day's count at 0; a successful one makes it 1; a stock clip doesn't count |
| `test_visuals.py::test_with_the_days_free_images_used_the_error_says_why` (2 cases) | With 20 used: stock down (`[stock-fail]`) → `provider_unavailable`; nothing fits (`[no-stock]`) → `no_stock_match`. The count stays 20 and no file is kept |
| `test_visual_providers.py::test_at_most_three_downloads_are_tried_across_result_pages` | `downloads_left` is 3 for the whole job: two clips fail on page 1 and one on page 2. A fourth is never fetched, only pages 1 and 2 are searched, and the job ends with the free AI image |

The round 2 observation about page 1 is answered by QB4S2-011 and covered by the two page tests.
