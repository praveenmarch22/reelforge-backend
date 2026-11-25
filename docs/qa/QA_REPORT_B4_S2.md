# ReelForge: QA report for B4 slice 2 (visuals)

| | |
|---|---|
| Date | 2026-11-25 |
| Milestone | B4 slice 2: API C8 (regenerate a scene's visual), C9 (stock search), C10 (choose a stock clip or a finished upload); Pixabay stock, FLUX schnell images on Cloudflare Workers AI, the free AI-image fallback, `ai_video` scenes as an AI image with motion at 1 credit until B7 |
| Build tested | `backend/` working tree on top of 495ff76 (slice 2 uncommitted), Compose stack running |
| Checked against | PRD 5 (VI, CR1–CR4), 11 (G-1 to G-9, G-12, G-14); API design (conventions, Scene and Job objects, B5, C2, C8–C10, G3); technical design 7–9 and 11; DB design (assets); Pixabay API terms as summarised in the technical design and the code; the slice 1 report (QA_REPORT_B4_S1.md) for the patterns already agreed |
| Not in scope | Generate B11 and timeline B12 (slice 3), uploads D1/D2 and AI video (B7), file clean-up (B8) |

## Verdict: pass with issues

C8, C9 and C10 do what the API design says in the main flows:
- **Credits:** stock is free, an AI image costs 1 credit, an `ai_video` scene costs 1 credit until B7, and the AI-image fallback is free.
- **Status:** the scene's `visualStatus` follows the job the same way the voice does.
- **Edits while a visual is being made:** a C2 edit to the visual fields, or a B5 style change for AI scenes, cancels and refunds the job. The job's files, including the clip's thumbnail, are deleted.
- **C10 uploads:** ownership is checked on every path tried.
- **Scene `visual` object:** it carries the author and source link that stock credits need.
- **Pixabay terms:** search results are cached for 24 hours and clips are copied into our storage. Thumbnails and previews are linked from Pixabay only in search results.

There are no Blocker or Major findings. Five Minor findings break documented behaviour or edge cases, and there are four Polish items.

| Severity | Count | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 0 | |
| Minor | 5 | QB4S2-001 to QB4S2-005 |
| Polish | 4 | QB4S2-006 to QB4S2-009 |
| Questions | 4 | Section 3 |

**How verified.** *Run*: I ran a script inside the `api` container that drives the real app through its test client. It used an isolated database (`reelforge_qa4_test`), Redis database 11 and bucket `reelforge-qa4`, all removed afterwards. The stock and image providers were the mocks, and every outbound HTTP call was stubbed (`requests.request` refused, thumbnail fetches recorded), so nothing reached Pixabay, Cloudflare or any other host. Jobs ran through the real `jobs.run()`. For "while running" cases, the script called the API from inside the job's work step. *Read*: concluded from the code. I also inspected the real Pixabay and FLUX assets the developer made in the development database, read-only.

---

## 1. Findings

### QB4S2-001 Minor: "Regenerate" on a stock scene alternates between the same two clips

| | |
|---|---|
| Requirement | API C8 ("Always makes something new: the next fitting stock clip"; originally "next stock result") |
| Where | `visuals.start_visual_job`: `exclude` holds only the clip currently on the scene; `pick_clip` then picks the best of the rest |
| Verified | Run |

**Steps**: C8 on a stock scene four times. The clips used were `mock:1, mock:2, mock:1, mock:2`. The creator can never reach the third result with Regenerate. Choosing a clip by hand through C9/C10 is the workaround.

**Suggested fix**: remember the clips already shown for this scene. Keep a short list in the scene's last visual asset's `meta`, or count the scene's past stock jobs, and exclude all of them. Start again from the top when the results run out.

### QB4S2-002 Minor: when stock fails, the visual fails instead of falling back to an AI image, and downloads are not retried

| | |
|---|---|
| Requirement | PRD G-1 ("Retry with backoff (3 attempts), then try the fallback provider, then fail the scene only"); API C8 / technical design 9.0 (stock falls back to an AI image) |
| Where | `VisualJob.work`: an error from `stock_search` propagates; `_store_clip` → `download` is not wrapped in `with_retry` |
| Verified | Run (search failure); Read (download) |

**Steps**: stock query `"[stock-fail] beach"` (the mock's failure switch), then C8. The job ends `failed` with `provider_failed`, and the scene shows `failed`, although an image provider is available. `visual_available("stock")` even admits stock scenes on the grounds that "stock falls back to an AI image". A Pixabay download that drops (`ConnectionError`, `5xx`) also fails the job straight away, with no retry and no try of the next fitting clip.

**Suggested fix**: wrap the download in `with_retry`. If the chosen clip still can't be fetched, try the next candidate. If searching or downloading fails altogether, fall back to `_make_image(fallback=True)`, as when nothing fits.

### QB4S2-003 Minor: cancelling a C8 that changed the source or prompt shows the old visual as `ready` for the new settings

| | |
|---|---|
| Requirement | API C8 (fields sent are saved to the scene first) and the status model (`ready` means the visual matches the scene); DB design 5 |
| Where | `routes/scenes.regenerate_visual` saves the fields before `start_visual_job`, so the job's input already holds the new values. `VisualJob.on_stop` restores `previousStatus` when nothing "changed" since the job started. The same applies to C10 stock, which sets `visual_source = "stock"` first |
| Verified | Run |

**Steps**: a scene with a ready stock clip. C8 with `{"visualSource": "ai_image"}`, then G3 cancel. The scene is now `visualSource: ai_image`, `visualStatus: ready`, `visual.kind: video` (the old stock clip). During the take, `after_edit` also counts that scene as ready, because `previousStatus` is `ready`. So the project can be `ready` while a scene's visual doesn't match its settings.

**Why it matters**: slice 3's Generate only redoes `missing`/`outdated` scenes, so this stock clip would be rendered in an "AI image" scene.

**Suggested fix**: when C8 or C10 changes the visual fields, apply C2's rules first (`mark_visual_outdated`) before recording `previousStatus`. Or record the old field values in the job input and have `on_stop` compare against those.

### QB4S2-004 Minor: the free AI-image fallback has no limit, so one creator can use up the app's daily image allowance

| | |
|---|---|
| Requirement | PRD principle 3 / CR1–CR4 (every action that costs money is tracked in credits); G-3 (free provider quota is app-wide) |
| Where | `VisualJob.work` (fallback image at 0 credits); C8 has no rate limit (C9 has 60/hour) |
| Verified | Run |

A stock scene whose query finds nothing (any nonsense query) gets a FLUX image for 0 credits on every C8. In the run, 6 regenerates in a row made 6 images and the balance did not move. One job runs per scene at a time, but a script can repeat C8 across scenes as fast as the jobs finish. Cloudflare's daily free allowance is shared by every user. When it is used up, AI image and `ai_video` scenes fail for everyone (G-3).

**Suggested fix**: decide on a limit (Q2). For example, a per-user hourly rate limit on C8 like C9's. Or a small daily count of free fallbacks per user, after which the fallback costs the normal 1 credit.

### QB4S2-005 Minor: stock downloads trust the URL in Pixabay's answer completely

| | |
|---|---|
| Requirement | Technical design (security: the worker should only fetch what it means to fetch); the download safety asked for in this review |
| Where | `providers/stock.download` and `visuals._store_clip` (thumbnail) |
| Verified | Read |

The clip and thumbnail URLs come from Pixabay's JSON answer. That is acceptable as a source, but nothing limits where they can point:
- **Hosts and schemes:** `download_url` may use any scheme accepted by `requests`, including `http://`, and any host. Only the thumbnail is checked for `https://`.
- **Redirects:** they are followed to any address, including the private network: `minio:9000`, `postgres`, `redis`, or `169.254.169.254` in AWS.
- **Thumbnail memory:** it is fetched without `stream=True`, so the whole body is read into memory before the 2 MB check.

Pixabay changing its answer, a compromised CDN, or a man-in-the-middle on an `http://` link would let the worker fetch internal addresses and store the result as a "clip". The `ftyp` check limits what can be stored, but not what is fetched.

**Suggested fix**:
- Accept only `https://` URLs on the provider's own hosts (`*.pixabay.com` today, Pexels later), and allow redirects only within those hosts.
- Stream the thumbnail with the same size cap as the clip.

### QB4S2-006 Polish: stock-fallback AI images ignore project style changes

`B5` marks visuals `outdated` only for `ai_image`/`ai_video` scenes, and `VisualJob.apply` checks the project's style only when the source is not `stock`. A stock scene that got an AI image as a fallback was drawn in the project's style anchor. After a style or character change, it keeps the old look and stays `ready`. Verified: Read. Fix: treat a scene whose visual asset has `meta.stockFallback` like an AI scene in B5 and in `apply`.

### QB4S2-007 Polish: the Pixabay lookup for C10 is not cached, and an unknown id may be reported as a provider failure

`PixabayStock.get` (used by C10) goes to Pixabay every time, while the technical design says search results are kept for 24 hours. For an id Pixabay rejects (a `400` for an out-of-range id), `http()` raises a non-retryable error, which ends as `provider_failed` instead of `stock_unavailable`. Verified: Read. Fix: cache `get` like `search`, and map a `400` from the id lookup to `stock_unavailable`.

### QB4S2-008 Polish: the mock stock provider makes real outbound HTTP calls

The mock's thumbnail URLs are `https://example.com/clip/<n>.jpg`. Because they start with `https://`, `_store_clip` fetches them for real. In my run (stubbed) that was 12 calls. In CI or offline development, each costs up to the 10 s timeout and leaves the machine, which goes against the "offline mock" promise in technical design 9.0. Verified: Run (calls recorded) + Read. Fix: give the mock `mock://` thumbnail URLs and serve a tiny JPEG locally, as `download` already does for clips.

### QB4S2-009 Polish: an image's `thumbnailUrl` is the full-size image

For `kind: image`, `thumbnailUrl` equals `url`. The real FLUX image in the development database is 1024×1024 and 738 KB, so a scene list with 10 AI scenes loads about 7 MB of "thumbnails". Verified: Read + development database. Fix: store a small JPEG (for example 320 px) next to the image, as is done for clips. Alternatively, document that `thumbnailUrl` may be the full image.

---

## 2. What passed

| Check | Verified |
|---|---|
| C8 stock: `202`, 0 credits, scene `queued` → `ready`; `visual` has `kind: video`, `thumbnailUrl`, `provider`, `author`, `sourceUrl`, `width`, `height`, `stockFallback` | Run |
| C8 `ai_image` 1 credit, `ai_video` 1 credit (until B7); image `kind: image` | Run |
| No stock fits → AI image, 0 credits, `stockFallback: true` in the job output and on the scene (G-6) | Run |
| C8 refuses `visualSource: upload` and upload scenes (`400`); idempotent replay returns the same job | Run |
| C9: documented fields including `source` and `pageUrl`; `page` 0, 26, non-numeric and `q` over 100 characters → `400`; another user's scene → `404` | Run |
| C9 and the job share a 24-hour Redis cache of search results (Pixabay terms); the key is not part of the cache key | Read |
| C10 stock: `202`, 0 credits, `visualSource` becomes `stock`; a clip that no longer exists → `stock_unavailable` with its message, scene `failed` with `lastError`; malformed id, both or neither field → `400` | Run |
| C10 upload refused for another user's asset, another project's asset, an audio upload, an unfinished upload, a non-upload asset, a released upload; accepted for the project's own finished video (`200`, `visualSource: upload`, `ready`) | Run |
| C2 prompt edit while queued → job cancelled and refunded, scene `outdated`; stock query edit while running → cancelled, file deleted | Run |
| B5 style change while an AI visual is queued → cancelled, scene `outdated` | Run |
| G3 cancel mid-run → the clip **and its thumbnail** are deleted from storage | Run |
| A project becomes `ready` when every scene got its voice (C7) and visual (C8) | Run |
| B10 estimate: one `ai_image` scene = 1 credit, stock 0 | Run |
| Downloads: 150 MB cap while streaming, `ftyp` check, spooled to disk above 16 MB, streamed to S3 with `upload_fileobj` | Read |
| FLUX answer decoded strictly (`validate=True`) and checked with Pillow (JPEG or PNG only) | Read |
| The Pixabay key goes in the query string (Pixabay has no header option); `http()` replaces network errors with their type name, so the URL with the key is not in error messages; no key found in API or worker logs | Read + Run (logs) |
| Real assets made by the developer: Pixabay clips 1920×1080 MP4 (14–19 MB) with stored thumbnails, author, page and license; FLUX image 1024×1024 JPEG with model and prompt in `meta` | Run (read-only database) |
| Production check refuses `mock` in `STOCK_PROVIDERS` / `IMAGE_PROVIDERS` | Read |

## 3. Questions for the product owner

1. **User-Agent.** Every outbound call now sends `ReelForge/1.0 (+https://github.com/praveenmarch22/reelforge-backend)` to Pixabay, Cloudflare, ElevenLabs, Gemini and Groq. This names a personal GitHub account. If the repository is private, the link also leads nowhere. Is that the intended contact, or should it be a product address?
2. **Free fallback limit** (QB4S2-004). How many free fallback images per user per day, or should a fallback cost the normal 1 credit?
3. **Output sharpness.** FLUX on Workers AI returns 1024×1024 and takes no size. Pixabay clips are mostly 1920×1080 landscape. Filling a 1080×1920 frame means cropping and enlarging both by about 1.8–1.9 times. Accept this for version 1? Or download Pixabay's larger rendition when one exists (more storage and bandwidth), or look at another image model that can produce a 9:16 image?
4. **G-6 wording.** The PRD says "retry with simpler keywords **from the LLM**"; the code keeps the first two words of the query. Is that accepted for now?

## 4. Notes for the editor (F6)

- **Search results:** show where they come from, for example "Videos from Pixabay" with a link. Pixabay's terms require it. `thumbnailUrl`/`previewUrl` in C9 results point at Pixabay and are for search results only. Once chosen, use the scene's `visual.url` (our copy).
- **Stock credits:** show the author and a source link from `visual.author` and `visual.sourceUrl`.
- **Media keys:** key media by `visual.assetId`; the signed `url` and `thumbnailUrl` change on every response.
- **Badges:** show one for `stockFallback` ("No stock clip matched; AI image used"), and one for `kind: image` (stills will move with a slow zoom).
- **Statuses and errors:**
  - `visualStatus` `queued`/`running`;
  - `409 already_running` on C8 while a visual is busy;
  - job error `stock_unavailable` → let the creator pick again from C9;
  - C10 with a stock id returns a job (`202`), but with an upload it returns the scene (`200`).
- **Costs:** show them before C8 (`GET /credits` → `costs.aiImage`; `ai_video` costs the same until B7).

## 5. For slice 3 (Generate, timeline) to keep in mind

- **Clip length:** `scene_seconds` is fixed when the visual job starts (the voice's length, or the estimate). If the voice is made or redone afterwards, the clip may be shorter than the scene. The timeline needs G-7's rules (slow down, then hold), or Generate should start visuals after the voices.
- **Pixabay rate limit:** about 100 requests a minute per key, shared by the whole app. Generate for a 10-scene Reel makes up to 20 searches (two queries per scene), partly served from the cache. Several users generating at once can hit `429`s; `with_retry` honours `Retry-After`.
- **Fix QB4S2-003 before Generate,** which relies on `outdated`/`missing`.
- **Stuck jobs and orphaned files:** the visuals queue shares the worker's concurrency of 2 with voice. A 150 MB download plus three 60 s FLUX attempts can approach the 360 s soft limit. An interrupted job can leave an `.mp4` in storage with no asset row. The same happens if storing the thumbnail raises a storage error after the clip was uploaded (B8 clean-up should list storage).
- **Thumbnails in clean-up:** they live only in `meta.thumbnailKey`. B8 (and project delete, G-14) must delete them with the asset.
- **Rendering stills and crops:** the renderer must crop landscape clips and square images to 9:16 (G-9) and animate stills (`meta.motion: zoom`).

---

## 6. Recheck (2026-11-25, after FIX_LOG_B4_S2.md)

**Build**: working tree with the fixes (uncommitted). **Method**: as in the first pass, on fresh isolated resources: database `reelforge_qa5_test`, Redis database 10 and bucket `reelforge-qa5`, all removed afterwards. The mock providers were used. To exercise the real code paths for the download allow-list and for download failures, a stand-in "pixabay" provider returned clips on `cdn.pixabay.com`, and `requests.get` was stubbed. No call left the machine; the generic HTTP helper was set to refuse any call. 34 checks: 33 passed, 1 failed (QB4S2-010).

### Verdict: pass

All nine findings are fixed, and all four questions are answered: the User-Agent is now plain `ReelForge/1.0`, and G-6's two-word simplification is recorded in technical design 9.0. The regression checks you asked for found three small new problems, all Polish (QB4S2-010 to 012). None is Blocker, Major or Minor.

### Status of the findings

| Id | Status | How verified |
|---|---|---|
| QB4S2-001 | **Fixed.** Regenerate went `mock:1` → `2` → `3` → `4` → `5` without repeats. See QB4S2-011 for what happens after the last result | Run |
| QB4S2-002 | **Fixed.** A failing stock search falls back to an AI image. With the first two downloads answering `503`, the job moved on and stored the third clip (`pixabay:102`) | Run |
| QB4S2-003 | **Fixed.** Both cancels now leave the scene `outdated`: C8 with a new source, and C10 swapping an AI scene to stock. A failed take after a change shows `failed`. C10 stock → stock, then cancel, correctly stays `ready`. A plain regenerate keeps a ready project `ready`; a regenerate with a changed query makes it `draft` during the take and `ready` again after | Run |
| QB4S2-004 | **Fixed.** 21st free fallback of the day → `no_stock_match`, scene `failed`, nothing charged. C8 rate limit: a `409` while busy does not count. The 61st request → `429` with no job, no credits reserved, and the fields sent in the body not saved | Run |
| QB4S2-005 | **Fixed.** The allow-list accepts `https://cdn.pixabay.com/…` (in any letter case). It refuses `http://`, `cdn.pixabay.com.evil.io`, a Pixabay name in the path, port 8443, `user@169.254.169.254`, `minio:9000`, and Pixabay hosts for a `mock:` clip. Downloads pass `allow_redirects=False`, and a `302` is refused. A 3 MB "thumbnail" is dropped while it is being read | Run |
| QB4S2-006 | **Fixed** for the cases reported: B5 marks a stock scene showing a fallback image `outdated`, and a fallback image made in the old style is dropped (`scene_changed`). One gap remains; see QB4S2-010 | Run |
| QB4S2-007 | **Fixed.** An id Pixabay refuses (non-retryable) → `stock_unavailable`. A `mock:` id is refused with `400` when the mock is not configured | Run |
| QB4S2-008 | **Fixed.** No outbound call during a mock stock job | Run |
| QB4S2-009 | **Fixed.** AI images get a separate small thumbnail file (`-thumb.jpg`, at most 360×640) | Run |

### New findings

#### QB4S2-010 Polish: a style change during a stock take on a fallback scene, followed by a cancel, shows the old-style image as `ready`

**Requirement**: B5 (a style change outdates AI-styled visuals), the QB4S2-006 fix. **Where**: B5 cancels visual jobs only for `ai_image`/`ai_video` scenes, and `mark_visual_outdated` skips `queued`/`running`. `VisualJob.on_stop` restores `previousStatus` (`ready`). **Verified**: Run.

**Steps**: a stock scene showing a fallback AI image, `ready`. Run C8 (queued), then B5 `style`, then G3 cancel. The scene is `ready` with the old-style image (`stockFallback: true`). While the take runs, `after_edit` also counts it as ready. If the take finishes instead, it is correct: a clip is fine in any style, and a fallback image is dropped.

**Fix**: in B5, also cancel visual jobs of scenes whose current visual is a fallback image before marking them. Or record the style in the job input and treat a style change as "changed" in `on_stop` when the current visual is an image.

#### QB4S2-011 Polish: once a page of results has been used, Regenerate switches a stock scene to a free AI image instead of the next page

**Requirement**: API C8 ("the next fitting stock clip"). **Verified**: Run (after the mock's 5 results, the 6th regenerate gave `kind: image`, `stockFallback: true`).

The job searches only page 1 (20 clips with Pixabay, for the full query and the two-word query) and excludes the last 30 clips seen. When they are all used, the scene quietly becomes an AI image, which counts against the 20 free a day. The creator can still pick from later pages with C9/C10.

**Fix**: when every result on a page is excluded, try the next page (up to a small limit) before falling back. Or document that Regenerate falls back after the first page.

#### QB4S2-012 Polish: the daily free-fallback count is used by failed attempts and by stock outages, and the refusal message then blames the search words

**Verified**: Run (two image attempts that failed left the counter at 2) + Read.

`_free_fallback_allowed` counts before the image is made. So failed FLUX calls, cancelled jobs and dropped (`scene_changed`) images all use up the day's 20. A Pixabay outage, or Pixabay starting to redirect downloads, also sends every stock scene to the fallback; a run with all downloads redirected succeeded silently as an AI image. When the count runs out, the message says "No stock clip fits this scene. Try other search words", which is misleading when stock is down.

**Fix**: count a fallback only when its image is stored (or give the count back in `discard` and on failure). Use a different message (and log level) when the fallback was caused by a provider failure rather than by no match.

### Counts after the recheck

| Severity | Open | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 0 | |
| Minor | 0 | |
| Polish | 3 | QB4S2-010 to QB4S2-012 |
| Fixed | 9 | QB4S2-001 to QB4S2-009 |
