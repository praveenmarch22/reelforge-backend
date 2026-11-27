# ReelForge: test report for B6 (publishing to YouTube Shorts)

| | |
|---|---|
| Date | 2026-11-27 |
| Role | Tester (automated tests only; no product code changed) |
| Build tested | `backend/` working tree, B6 uncommitted on top of HEAD, **after the QA round 1 fixes** (QA_REPORT_B6.md, QB6-001 to QB6-011). The first pass ran on the build before those fixes; the copy was re-synced and everything was re-run on the fixed build |
| How run | A copy of the working tree (no `.env`, `node_modules`, `renderer/out`, `renderer/public/projects`, and not the stray `api/C:/…` folder, see observation 6) run as its own Compose project `b6test`, with only `postgres`, `redis`, `minio`, `mailpit` and `api`. Ports moved (Postgres 54330, Redis 63800, MinIO 9010/9011, Mailpit 8035, API 5010) and `LLM/VOICE/STOCK/IMAGE/PUBLISH_PROVIDERS=mock`. pytest and ruff inside `b6test-api-1`. Google and YouTube were never called: the real client ran against a fake `requests`. The main stack was not touched. The stack was taken down with `down -v` afterwards |
| Checked against | [PUBLISHING_YOUTUBE.md](../PUBLISHING_YOUTUBE.md) (P1 to P6, YT-1 to YT-13, API F1 to F7, Post, Data, Backend), API design (the `returnTo` rule, Conventions, the B2 `progress` rule), technical design ("retried on 5xx and network errors") |

## 1. Results

| Run | Result |
|---|---|
| Publishing tests (`test_publishing.py` starter plus the three new files), fixed build, run twice | **245 cases: 236 passed, 9 failed**, the same 9 both runs (BUG-073 to BUG-079; BUG-076 has two cases). One more failure in the second run was a defect in my own test (a SQL cast), fixed and re-run twice |
| Same files on the first build (before the QA fixes) | 3 more failures that pass now: a "Try again" uploaded the video a second time (QB6-002), the project's `progress` stayed 0 while posting, and cancelling the publish of an outdated render gave `rendered` instead of `ready` |
| Full pytest suite, fixed build | **1702 tests: 1671 passed, 31 failed** (28 min). 9 are BUG-073 to BUG-079. 20 are older production-settings and provider tests in `test_worker_and_config.py`, `test_voice_providers.py` and `test_visual_providers.py` that fail only because this stack runs with `*_PROVIDERS=mock` (observation 5); re-run with the compose defaults (`elevenlabs,edge` / `pixabay` / `cloudflare` / `youtube`): 276 passed, 0 failed. 2 (`test_cli.py::test_worker_ping_prints_the_workers_answer`, `test_worker_and_config.py::test_a_task_makes_the_round_trip_through_the_worker`) need a running Celery worker, which this stack doesn't start by instruction |
| `ruff check .` / `ruff format --check .` (in the api container) | All checks passed / 101 files already formatted |

## 2. What was added

| File | Tests |
|---|---|
| `api/tests/publish_support.py` | Helpers: F1/F2 as the browser does them, the Post and Connection field lists, a rendered Reel, F5/F6 calls, resetting the stand-in (and the manual job queue) |
| `api/tests/test_publishing_connections.py` | 45 functions, 104 cases: F1, the `returnTo` rule, F2, F3, the access token (YT-4, YT-6), the user's `connections`, account deletion, the daily check |
| `api/tests/test_publishing_publish.py` | 49 functions, 70 cases: F4, F5, the `publish` job, G3 cancel, project statuses, F6/F7 |
| `api/tests/test_publishing_youtube_http.py` | 27 functions, 62 cases: the real `YouTube` client against a fake Google (no network), error mapping, production settings, the migration's unique index and downgrade |
| `api/tests/test_publishing.py` (starter, updated by the developer for the absolute redirect) | 9 tests |

Older tests brought up to the B6 contract (they compared exact field sets that B6 extends): `projects_support.PROJECT_FIELDS` gains `postTitle`; `support.new_user_json` gains `connections`; `test_schema.py` expects `youtube_accounts` (17 tables) and revision `0002`. `test_worker_and_config.PRODUCTION` was updated by the developer.

### Covered

- **F1**: 401 signed out, 403 unverified; `instagram` 404 `not_available`, unknown platform 404 `not_found`; 503 `provider_unavailable` with the real client and an empty client id (and with an id but no secret), nothing sent. The state is random (≥ 32 characters), new per click, stored in Redis with a TTL of 600 s and the user, platform and sanitised `returnTo`.
- **`returnTo`** (the pure function and through F1/F2):
  - Default for: none, empty, relative, `//`, `///`, `https://`, `http:/`, `javascript:`, a backslash (raw, `%5C`, `%5c`), each control character raw and encoded (tab, CR, LF, NUL, ESC, DEL, `%0d%0a` header injection), and 301 characters. Exactly 300 is kept.
  - Dot segments (raw and `%2e`), and encoded slashes, never resolve to another site. Fragments are dropped. `connected`, `connection_error`, `reason`, `platform`, `instagram` are stripped. A non-ASCII path gives an ASCII `Location`.
  - Through F2 the browser always lands on `http://localhost:5173/…` (WEB_ORIGIN).
- **F2**:
  - State used once, expired (key gone), unknown, empty, over 200 characters, another user's, signed out, and made for another platform: all go to `/app/settings?connection_error=youtube&reason=failed`, nothing stored. The Instagram callback never connects.
  - `error=access_denied` → `reason=denied` on the `returnTo` page. No channel → `no_channel`. No refresh token → `failed`. A grant without `youtube.upload` → `missing_permission`, nothing stored, token revoked.
  - Exchange or channel failures (PublishError, network error, KeyError, ValueError, anything else) → `failed`, never a stack trace. A wrong code → `failed`.
  - `302`, `Cache-Control: no-store`, `Referrer-Policy: no-referrer`; the code is never in the `Location`.
  - Tokens are stored Fernet-encrypted (no plaintext in the row), with the scopes and an expiry about 1 hour ahead.
  - Reconnect updates the same row (channel, title, picture, status back to `connected`) and revokes the earlier refresh token. A failed reconnect keeps the existing connection. Two users may connect the same channel.
- **F3**: 204 with an empty body; revoked at Google; row deleted; posts kept with their link, `youtube_account_id` set null, `latestPost` unchanged. A second disconnect is 204 and revokes nothing. Google down: still disconnected. Only my own connection is removed. 401 signed out; 404 for `instagram` and unknown platforms.
- **Tokens**: a fresh token is used as is; one expiring within 5 minutes is refreshed (expiry moves ~1 h ahead, refresh token kept); an unreadable one (key changed) is refreshed; `invalid_grant` marks `reconnect_required`; a 5xx on refresh leaves the connection `connected`; a `reconnect_required` connection gives no token. The daily `connections.check_connections()` marks only the refused connection.
- **User JSON**: `connections` is `{"youtube": null, "instagram": null}`, then exactly `status`, `name`, `pictureUrl` (null when empty), `connectedAt` (ISO). The login answer carries the same. `reconnect_required` shows. The older `instagram` field stays.
- **Account deletion**: the YouTube account is deleted and its token revoked.
- **F4**: 202 `{"job"}`, type `caption`, 0 credits reserved, no ledger row, balance unchanged; output exactly `title` (1–100, no `<` `>`), `caption`, `hashtags`; all three saved to the project. `no_script` (409) for a Reel whose script failed; `already_running` while scripting; a model refusal fails the job `content_refused` and changes nothing. 401/403/404.
- **F5**:
  - The full Post contract (exact fields including `credits`, ISO times), the job (type `publish`, 0 credits), project `posting` → `posted`, B2 status and `counts.posted`, balance unchanged. Hashtags lose `#` and duplicates. The metadata YouTube receives (title, description = caption + hashtags + credit lines, tags, category 22, `selfDeclaredMadeForKids: false`, privacy) and the size.
  - Credit lines: "Voice: elevenlabs.io" when a voice came from ElevenLabs; "Music: <licence>" from the render's timeline.
  - `privateUntilVerified` for five combinations of chosen and actual privacy: true only when YouTube kept a non-private choice private.
  - 409 `not_connected`, `reconnect_required`, `already_running` (same render queued; another render of a Reel that is posting), `already_posted`, `render_outdated` and its `confirmOutdated` path, `not_ready` (render queued, render failed); 404 `not_available` for Instagram. None of them change the project.
  - 400 for empty, blank, 101-character, `<`, `>` and two-line titles; a 2,201-character or `<` caption; 31 hashtags, a hashtag with a space, a 101-character one, a `<tag>`; an unknown visibility or platform; a missing title; a non-boolean `confirmOutdated`. 100/2,200/30 are accepted.
  - The 5,000-byte description: exactly 5,000 bytes is accepted and sent; 5,001 bytes and 1,700 "€" (5,100 bytes) are 400 on `caption`. Tags sent stay within 500 characters.
  - Idempotency: the same key replays the 202 before and after the job ran, with one post and one upload; the same key with another body is `409 idempotency_conflict`.
  - Ownership: another user's render, a malformed and an unknown id, and a deleted Reel are 404; 401 signed out; 403 unverified.
- **The publish job**:
  - A 10,000-byte video in 4,096-byte pieces: three pieces with the right offsets and total, the bytes YouTube receives equal the file; mid-upload the post is `uploading` with rising progress and the project is `posting`; the project's `progress` equals the post's.
  - The real client (fake Google, 600,000 bytes in 256 KB pieces): the resumable POST (params, the three headers, the JSON), each PUT's `Content-Range` and `Content-Length`, no Authorization on the session URL, the `videos.list` call. A 308 that took only part of a piece makes the next piece start there. A 503 on a piece asks `bytes */600000` and continues.
  - Resume after a retryable error and after a dropped connection (YouTube is asked how far it got; one upload session). Three failures in a row → `upload_failed` with the documented message; the render is kept. `quota_exceeded` on a piece or at the start fails at once, never retried, credits untouched. `SoftTimeLimitExceeded` → `timed_out`, not retried.
  - `reconnect_required` (invalid_grant during the job): the post fails with the documented message, the connection is marked, F5 answers 409, and after reconnecting a retry publishes. Disconnecting while a post waits fails it with `reconnect_required`.
  - `rejected`, `failed` and `deleted` from processing → `rejected` with its message, project `post_failed`, render kept. Still processing after 60 polls → published (as designed). `invalid_metadata` from YouTube → its message.
  - Try again: continues an interrupted upload (pieces `0`, then `4096`, `8192`, one upload); after a crash past the upload only re-checks the saved video (one upload, same link); after `rejected` uploads afresh.
  - Cancel (G3): queued (nothing uploaded, post `failed` with `cancelled` / "Publishing was stopped.", project back to `rendered`, second cancel 409 `job_finished`, publishable again); during the upload (no more pieces sent); during processing (counts as published, project `posted`); a retry after a failure (back to `post_failed`); an outdated render's publish (back to `ready`); while the Reel was generating (the Reel ends `ready` once the voice is made).
- **F6/F7**: `GET /posts/:id` and `GET /projects/:id/posts` (newest first, exact fields, `latestPost` equals the first item) belong to their owner: 404 for another user, malformed and unknown ids, a deleted Reel; 401 signed out.
- **`_raise_for`**: 401 and `invalid_grant` → `reconnect_required`; `unauthorized_client`/`invalid_client` (400) → `upload_failed`; `quotaExceeded`, `uploadLimitExceeded`, `dailyLimitExceeded` → `quota_exceeded`; 5xx, 429, `rateLimitExceeded`, `userRateLimitExceeded` → retryable `upload_failed`; the five `invalid*` reasons → `invalid_metadata`; other 4xx → non-retryable `upload_failed`. `_received` reads `bytes=0-1048575` as 1,048,576 and no header as 0. `uploaded_so_far` 404 → non-retryable `upload_failed`.
- **Settings**: production accepts `youtube`, a new Fernet key and an https redirect, and hides the key and secret from `repr`. It refuses the mock (alone or listed), the development key, a forgotten key, an http or empty redirect URI. Any environment refuses a non-Fernet key and an unknown or empty `PUBLISH_PROVIDERS`. Development may use the mock, the dev key and http.
- **Migration 0002**: one live post per render and platform (queued, uploading, processing and published all refused by `posts_one_per_render_platform`; failed ones don't count; Instagram has its own; a failed post can't be revived next to a live one); unknown platforms and 101-character titles refused; the downgrade works with a YouTube and an Instagram post on the same render, keeping the Instagram one.

### Not covered (deliberately)

- A real Google sign-in and upload (the product owner's own end-to-end run covered it; the isolation rules forbid calling Google).
- The Celery round trip on the new `publish-worker` and the beat schedule firing (the schedule entry is checked in `test_worker_and_config.py`).
- A real worker crash mid-job (simulated by an exception at the same point).
- Two F5 requests at the same instant from two browsers (the project lock and the unique index are covered separately).

## 3. Bugs

### BUG-073 (Major): a wrong OAuth client secret marks every creator's connection `reconnect_required`

- **Test**: `api/tests/test_publishing_youtube_http.py::test_a_wrong_client_secret_does_not_cost_the_creator_their_connection`
- **Steps**: connect YouTube; make the access token due for refresh; Google's token endpoint answers `401 {"error": "invalid_client", "error_description": "Unauthorized"}` (what it answers for a wrong client id or secret); publish and run the job.
- **Expected**: the post fails, the connection stays `connected` (QB6-005: "Our own OAuth client is wrong (a server setting), not the creator's connection").
- **Actual**: the connection is `reconnect_required`.
- **Cause (reading)**: `_raise_for` checks `answer.status_code == 401` before the `invalid_client`/`unauthorized_client` reasons, so the QB6-005 fix only works for a 400. The daily `connections.check` uses the same path, so after a bad secret rotation it would mark every connection in one night and every creator would have to reconnect.

### BUG-074 (Major): "Try again" after connecting another channel reports channel A's video as published

- **Test**: `api/tests/test_publishing_publish.py::test_try_again_after_connecting_another_channel_does_not_reuse_the_old_channels_video`
- **Steps**: connect channel A → publish; the upload reaches YouTube (video id saved), then the job fails → connect again with channel B (F2: "A second connect replaces the first") → Try again.
- **Expected**: the Short goes to channel B (a new upload), and the post's link is B's video.
- **Actual**: no upload; the post is `published` with `https://youtube.com/shorts/mockvid1`, the video on channel A.
- **Cause (reading)**: the continuation in `start_publish` matches `Post.youtube_account_id == account.id`, but a reconnect updates the same `youtube_accounts` row, so its id stays when the channel changes. With the real API, `videos.list` with B's token wouldn't find A's video, which then hits BUG-075 (published after 10 minutes with a link to A's video). Matching the channel id (or clearing the old posts' upload state on a channel change) would fix it.

### BUG-075 (Minor): a video YouTube doesn't return is reported published after 10 minutes

- **Test**: `api/tests/test_publishing_publish.py::test_a_video_youtube_no_longer_has_is_not_reported_published`
- **Steps**: publish; `videos.list` answers with no item (the creator deleted the video on YouTube while it processed, or BUG-074's other channel).
- **Expected**: the post fails; it doesn't end `published` with a link to nothing.
- **Actual**: the job polls 60 times (10 minutes in production) and the post is `published`, project `posted`.
- **Cause (reading)**: in `PublishJob.work`, `(video or {}).get("status", {})` makes "not found" look like "still processing", and the loop then ends in the "still processing after 10 minutes" success path.

### BUG-076 (Minor): only the upload pieces are retried; one 5xx anywhere else fails the post

- **Tests**: `api/tests/test_publishing_youtube_http.py::test_one_5xx_from_youtube_is_retried_not_fatal[starting-the-upload]` and `[checking-processing]`
- **Steps**: real client; Google answers one `503` to the resumable-upload POST (or to the first `videos.list`), then answers normally.
- **Expected**: published. The design says calls are "retried on 5xx and network errors with backoff, never on 4xx".
- **Actual**: `failed` with `upload_failed` (or, for the processing check, failed after the video is already on YouTube; Try again then only re-checks, so it recovers, but the creator first sees a failure).
- **Cause (reading)**: `_raise_for` marks 5xx as `retryable`, but only `_upload`'s piece loop looks at it. `start_upload`, `video`, `refresh`, `exchange` and `channel` are single calls. A network exception during the processing check is not a `PublishError` at all and fails the job as `internal_error`.

### BUG-077 (Minor): an encoded `?` in `returnTo` smuggles our own result parameters

- **Test**: `api/tests/test_publishing_connections.py::test_return_to_with_an_encoded_question_mark_can_not_smuggle_a_result`
- **Steps**: F1 with `returnTo=/app/p/x%3Fconnection_error%3Dyoutube%26reason%3Dno_channel` (sent encoded once more in the query), then a successful F2.
- **Expected**: one result, `connected=youtube` (API design: "any … query parameters already in it are removed"; the stored value is the resolved path, where `%3F` is part of the path).
- **Actual**: `/app/p/x?connection_error=youtube&reason=no_channel&connected=youtube`: a successful connect shows as an error (the page reads the first values).
- **Cause (reading)**: `safe_return_to` returns `unquote(parts.path)`, so `%3F` becomes a real `?` after the query was filtered; `with_result` then splits it into a query. Keeping the path percent-encoded (or filtering after decoding) fixes it. It stays on the site, so it is not an open redirect.

### BUG-078 (Minor): F2 without a `code` goes to `/app/settings`, not the `returnTo` page

- **Test**: `api/tests/test_publishing_connections.py::test_f2_without_a_code_goes_back_to_the_page_with_failed`
- **Steps**: F1 with `returnTo=/app/p/x/publish` → F2 with the valid `state` and no `code` or `error`.
- **Expected**: `…/app/p/x/publish?connection_error=youtube&reason=failed` (F2: "Always 302 to returnTo"; only a bad state falls back to `/app/settings`).
- **Actual**: `http://localhost:5173/app/settings?connection_error=youtube&reason=failed`.
- **Cause (reading)**: `connections.callback` returns `failed` (the default page) for a missing code instead of `error("failed")`.

### BUG-079 (Minor): F4 can save a post text that F5 then refuses

- **Test**: `api/tests/test_publishing_publish.py::test_f4_output_can_always_be_published_as_it_is`
- **Steps**: the model answers `{"title": "<<>>", "caption": "A <b>bold</b> claim.", "hashtags": ["<tag>", "facts"]}` → F4 → publish the saved `postTitle`, `postCaption`, `hashtags` as they are.
- **Expected**: either the job refuses the answer as unusable, or what it saves is publishable.
- **Actual**: the job succeeds and saves an **empty** title and the hashtag `<tag>`; F5 answers `400` (`title: Required`, then the hashtag rule).
- **Cause (reading)**: `check_caption` removes `<` and `>` from the title after `clean(..., required=True)` checked it was non-empty, and `clean_hashtags` doesn't remove them at all.

## 4. Observations (not counted as bugs)

1. **`/.//evil.example` becomes `/evil.example`, not the default.** The API design says a value whose resolved path starts with `//` is replaced by the default; `posixpath.normpath` collapses the double slash instead. The result stays on the site, so it is safe; the text and the code just differ.
2. **A publish cancelled during processing shows `privateUntilVerified: false`** even when YouTube kept it private, because the cancelled job has no output to read it from.
3. **`docs/schema.sql` has no `youtube_accounts`** or the new `posts`/`projects` columns; the DB design points to migration 0002 instead.
4. **Older exact-field tests had to be updated** (section 2). They failed on the B6 build only because B6 adds documented fields.
5. **Provider-dependent production tests**: as in B5, older production-settings tests depend on the container's `*_PROVIDERS`; this stack ran with them set to `mock` (see the full-suite row).
6. **A stray folder `api/C:/Users/prave/AppData/Local/Temp/x/` (a `cryptography` wheel)** is untracked in the working tree, apparently from a mistyped Windows path. It should be deleted, not committed.
