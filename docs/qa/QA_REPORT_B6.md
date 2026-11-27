# ReelForge: QA report for B6 (publishing to YouTube Shorts)

| | |
|---|---|
| Date | 2026-11-27 |
| Milestone | B6: platform-neutral publishing, YouTube first. Connect (F1 to F3), AI title and caption (F4), publish and posts (F5, F6), the `publish` and `caption` jobs, migration 0002, token encryption |
| Build tested | `backend/` working tree on top of 86896af (B6 uncommitted) |
| Checked against | [PUBLISHING_YOUTUBE.md](../PUBLISHING_YOUTUBE.md) (P1 to P6, YT-1 to YT-13, API, Data, Backend); PRD 5.8 and 11.7 pointers; API design sections 1 to 3 (conventions, shared objects) and group F (the `returnTo` rule and F2 behaviour); DB design and `0001_baseline.sql` (`posts`, `projects`); technical design 7 (queues, task rules); PROJECT_STATUS decision log |
| Not in scope | Instagram (B6b), the F7 screens, P6 (P1: same render to two platforms), Google's audit itself, quota increases |

## Verdict: pass with issues

The main flow works, and most of the risky parts are right:
- **The OAuth state** is random, kept in Redis for 10 minutes, and used once (`GETDEL`). It is tied to the user and the platform. The callback checks it against the signed-in user (`sessions.signed_in_user_id`) before it reads `error` or `code`. Another person's sign-in link can't connect an account to this user, or this user's account to theirs.
- **The `returnTo` rule** (API F2) holds for every bypass I tried (section 2).
- **Codes and tokens don't leak.** The request log drops query strings and Werkzeug logs only warnings. Token errors carry only a status and a reason. The callback answers `no-store` and `no-referrer`, and it always redirects, never to an error page.
- **Tokens are stored with Fernet.** Production refuses the development key and the mock.
- **The upload** reads S3 ranges in 8 MB chunks, handles `308` with `Range`, and asks YouTube how far it got after a dropped connection. It saves the video id as soon as YouTube gives it.
- **Double F5** is stopped by the project lock, the app check, and the new partial unique index.
- **The JSON** for Post and `connections` matches the design.

The product owner's real end-to-end run (a public 29 s Short) agrees with this.

It is not a clean pass, because of three Major findings:
- **QB6-001:** the ElevenLabs and CC BY music credit lines decided for B6 are missing. Shorts are being published without the attribution the licences require.
- **QB6-002:** "Try again" after a failure always uploads again from scratch, so it can create a duplicate Short. The resume (YT-8) and the saved video id (YT-9) are never used across attempts.
- **QB6-003:** a creator who unticks the upload permission on Google's consent screen is stored as connected. Every publish then fails with "please try again".

| Severity | Count | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 3 | QB6-001 to QB6-003 |
| Minor | 10 | QB6-004 to QB6-013 |
| Polish | 3 | QB6-014 to QB6-016 |
| Questions | 4 | Section 3 |

**How verified.** All findings and passes were **read** from the code and documents (`git diff` against 86896af and the new files). There is one exception. I ran a copy of `safe_return_to` and `with_result` in Python against 14 bypass strings in my scratch folder, so the `returnTo` result is **run**, on that copy. I ran no stack and no tests (the machine is low on memory) and made no call to Google. I didn't touch the main Compose project or the dev server. I scanned the repository for Google client secrets and tokens (`GOCSPX`, `*.apps.googleusercontent.com`, `ya29.`, `1//0`) with `.env` excluded, and found none. `.env` is ignored and not tracked.

---

## 1. Findings

### QB6-001 Major: the ElevenLabs and music credit lines decided for B6 are not added to posts

| | |
|---|---|
| Requirement | PROJECT_STATUS decision log, 2026-11-25 (two entries): "when any scene of a posted Reel used an ElevenLabs voice, the post caption gets a short 'Voice: elevenlabs.io' credit line, added when posting (milestone B6)", and "A posted Reel credits its track in the caption (milestone B6)". API design D4 and B12 (`music.license` is "the credit line for posting"). PRD section 8 (ElevenLabs free tier: "attribution") |
| Where | `publishing.py:49-51` (`description` = caption plus hashtags only), `publishing.py:197-201`; nothing in `PUBLISHING_YOUTUBE.md` |
| Verified | Read |

The description sent to YouTube is the creator's caption plus the hashtags. Nothing adds the ElevenLabs credit or the track's CC BY 4.0 credit ("Kevin MacLeod, incompetech.com"). Both are licence conditions: the ElevenLabs free plan requires attribution, and CC BY requires credit. Both were assigned to this milestone. The B6 design doesn't mention them, so they were lost in the re-cut. The real Short published on 2026-11-26 most likely went out without either line, so the product owner should check it and edit its description by hand.

**Suggested fix**: build the credit lines from the render's own timeline (`renders.timeline`), not from the project as it is now:
- the voice line when any scene's voice asset came from `elevenlabs`;
- the track's `license` text when library music played.

Append them to the YouTube description in `PublishJob.work`, and count them in the 5,000-byte check in `start_publish` (`publishing.py:77`). Add the rule to `PUBLISHING_YOUTUBE.md` (P2, and the F5 description), and say whether F7 shows the lines read-only under the caption. Add a test.

### QB6-002 Major: "Try again" always uploads from scratch, so an interrupted or timed-out publish can create a duplicate Short (YT-8, YT-9)

| | |
|---|---|
| Requirement | YT-8 ("continues from where it stopped"), YT-9 ("a retry of a post with a video id only re-checks processing"), YT-7 (one live post per render and platform), flow step 6 ("Try again") |
| Where | `jobs.py:221-223` (`run` only starts a job that is `queued`); `publishing.py:87-91` (F5 ignores failed posts), `99-108` (a new post with no `container_id`/`media_id`), `143-147`, `216-221`; `publishing.py:224-240` with the 1200 s soft limit |
| Verified | Read |

The upload session address (`container_id`) and the video id (`media_id`) are saved on the post. But only the same job could ever use them, and a job is never run twice:
- If the worker dies or is redelivered, `jobs.run` sees the job `running` and returns. The sweeper fails it after 23 minutes.
- If the soft limit hits during the processing wait, the job fails `timed_out`, though the video is already on YouTube.
- If a YouTube 5xx persists for three tries after the last chunk, the post fails.

The creator then gets "Try again", and F5 creates a **new** post with no session and no video id. The whole MP4 is uploaded again, which costs another ~1,600 quota units. If the first upload had completed, the channel now has **two copies of the Short**, both possibly public. The resume code (`uploaded_so_far` on a saved session) is unreachable outside one job, and so is the "video id already known" branch (`publishing.py:217`). The tests don't cover either.

**Suggested fix**: in `start_publish`, look up the newest failed post for the same render and platform.
- If it has a `media_id`, give the new post that `media_id` (it only re-checks processing). Better still, offer "it's already on YouTube" and mark it published once processing succeeds.
- If it has only a `container_id` less than a week old, copy it so the upload resumes.

Also, once `media_id` is set, a time-out in the processing wait should not fail the post as if nothing was uploaded (see Q2). Add tests for both retry paths.

### QB6-003 Major: a connection without the upload permission is stored as connected, and every publish then fails with "please try again"

| | |
|---|---|
| Requirement | Flow "connect YouTube" step 4 ("If … the creator refused, it shows the reason and stores nothing"); P1; YT-11 and the post error codes (plain-language reason) |
| Where | `connections.py:124-150` (`tokens.scopes` is stored, never checked); `providers/publish.py:64-74` (a 403 `insufficientPermissions`/`forbidden` becomes `upload_failed`) |
| Verified | Read |

Google's consent screen lets the creator untick individual permissions (granular consent) when an app asks for more than one, and F1 asks for `youtube.upload` and `youtube.readonly`. If the creator grants only `readonly`, the code still succeeds:
- the token exchange and `channels.list?mine=true` succeed;
- the account is stored as `connected`, and the page shows "Connected as …".

Every upload then gets a 403. That maps to `upload_failed` with "The upload to YouTube didn't go through. Please try again", which never works. Nothing tells the creator to reconnect and tick the box.

**Suggested fix**: in `callback`, after `exchange`, check that `youtube.upload` is in `tokens.scopes`. If it isn't, revoke the token, store nothing, and redirect with a new `reason=missing_permission` ("Tick 'Upload YouTube videos' and try again"). Document the reason in F2. Also map a 403 with reason `insufficientPermissions` on upload to `reconnect_required`, and mark the account `reconnect_required`.

### QB6-004 Minor: the soft time limit is swallowed during the upload

| | |
|---|---|
| Requirement | Technical design 7, task rules (soft limit, then `timed_out`); design "about 20 minutes allowed" |
| Where | `publishing.py:167-172` (`except Exception`), `publishing.py:243-244` (the outer `except SoftTimeLimitExceeded: raise` never sees it) |
| Verified | Read. Celery's `SoftTimeLimitExceeded` (from billiard) subclasses `Exception` |

Most of the job's time is spent in `get_object(...).read()` and `upload_chunk`, which are inside the `except Exception` block. When the 1200 s soft limit fires there, it is counted as a dropped connection: the job pauses, asks YouTube how far it got, and **keeps uploading**. Celery raises the soft limit only once, so the job runs on until the hard limit (1260 s) kills the process. The job is then left `running` with the project `posting` until the sweeper fails it, about 23 minutes after the start. If it reaches its third try first, it fails as `upload_failed` instead of `timed_out`.

**Suggested fix**: add `except SoftTimeLimitExceeded: raise` before `except Exception` in `_upload`. Add a test that raises it from the mock's `upload_chunk`.

### QB6-005 Minor: several YouTube and Google errors get the wrong code or leave the connection in the wrong state

| | |
|---|---|
| Requirement | Post error codes (design API section); YT-5, YT-6; design "Calls" (retried on 5xx and network errors) |
| Where | `providers/publish.py:64-74`; `connections.py:177-201` |
| Verified | Read |

- **Short-term rate limit.** `rateLimitExceeded` (a short-term limit) becomes `quota_exceeded`, which says "Try again tomorrow" and is not retried. Treat it like 429: retry with backoff.
- **Bad title, description or tags.** `invalidTitle`, `invalidDescription`, `invalidTags` and similar 400s become `upload_failed` with "Please try again", so a retry fails the same way. For example, a hashtag with a comma passes the F5 check but is an invalid YouTube tag. Map these to a code whose message says what to change, and reject commas in hashtags.
- **Our own client misconfigured.** A 401 from the token endpoint (`invalid_client`: wrong or rotated client secret) becomes `reconnect_required`. `access_token()` then marks **every** user's connection `reconnect_required`, and reconnecting can't fix it. Map 401 to `reconnect_required` only for the API calls and for `invalid_grant`. For `invalid_client`, use `internal_error` and log at error level.
- **Access revoked at Google.** If the creator removes ReelForge in their Google account, the upload or `videos.list` gets a 401. The post fails `reconnect_required`, but the account stays `connected` (only a failed refresh marks it). For up to an hour F5 keeps accepting publishes that fail the same way. Mark the account `reconnect_required` on that 401 too.

### QB6-006 Minor: F2 redirects to a relative path, which only works while the callback is served from the web app's own address

| | |
|---|---|
| Requirement | API F2 ("Always redirects (302) to the web app"); technical design base URL `https://api.<domain>/api/v1`; `config.web_origin` |
| Where | `connections.py:63-66`, `102`; `routes/publishing.py:31-39`; `config.py:177` (production only requires `https://`) |
| Verified | Read |

`Location` is `/app/settings?connected=youtube`, relative to the callback's own host. Locally the callback goes through the Vite proxy on 5173, so this works. In production the API is on `api.<domain>`, and the same redirect would land on `https://api.<domain>/app/settings`, which isn't the web app.

**Suggested fix**: prefix `settings.web_origin` to the target. Also have config validation require `youtube_redirect_uri` to start with `web_origin` (or with the API's public address), so a mismatch shows up at start-up.

### QB6-007 Minor: deleting an account, or connecting over an old connection, leaves the old grant valid at Google

| | |
|---|---|
| Requirement | YT-12 ("the tokens are deleted, and revoked at Google"); design flow step 3 |
| Where | `accounts.py:114` (`soft_delete` deletes the row without revoking); `connections.py:137-150` (a reconnect overwrites `refresh_token_enc` without revoking the previous token) |
| Verified | Read |

Disconnecting revokes the token, but deleting the whole ReelForge account doesn't. A deleted user's refresh token stays valid at Google until they remove the app themselves. A reconnect (for example after `reconnect_required`, or a switch to another channel) also leaves the replaced refresh token valid.

**Suggested fix**: in `soft_delete`, decrypt the token and revoke it after the commit, best effort, as `disconnect` does. In `callback`, revoke the old refresh token when it differs from the new one.

### QB6-008 Minor: the daily check that marks expired connections doesn't exist

| | |
|---|---|
| Requirement | Design "Backend": "A daily task marks connections whose refresh fails as `reconnect_required`"; YT-6 ("the publish screen asks to reconnect") |
| Where | `tasks/` has no YouTube task; only `connections.access_token` marks it on use |
| Verified | Read |

In Testing mode every refresh token expires after 7 days. Until the creator tries to publish, Settings and the publish screen show **Connected**. F5 accepts the publish, and only the job fails it with `reconnect_required`. The creator learns of the expiry from a failed post instead of from the publish screen.

**Suggested fix**: add the daily task, which tries a refresh for connections older than 6 days and marks failures. Alternatively, move the sentence to B8 in the design and make F5 refresh the token before it accepts a publish.

### QB6-009 Minor: the project list shows 0 % for the whole time a Reel is posting

| | |
|---|---|
| Requirement | API ProjectSummary: "`progress` is 0–100 while the status is … `posting`" |
| Where | `projects.py:317-318`: `return 0  # the post's progress arrives with milestone B6` |
| Verified | Read |

The post has `progress` (10 to 98), but the summary still hard-codes 0.

**Suggested fix**: return the active post's `progress`, the same way the `rendering` branch reads the render job's.

### QB6-010 Minor: publish jobs take up the shared two-slot worker for up to 20 minutes, most of it asleep

| | |
|---|---|
| Requirement | Technical design 7 ("slow or scarce work … must not block other work"); QB5-002 (the same problem, fixed for renders with a separate worker) |
| Where | `docker-compose.yml:138` (`--concurrency 2 -Q default,llm,voice,visuals,ai_video,publish,maintenance`); `publishing.py:224-238` (up to 60 polls with `sleep(10)`) |
| Verified | Read |

Two creators publishing at once take both slots. For up to 20 minutes, no script, voice, visual, email or sweeper task runs for anyone.

**Suggested fix**: give `publish` its own worker in Compose, as `render` has. Alternatively, do the processing wait as a separate short task re-queued with `countdown=10` instead of sleeping in the slot.

### QB6-011 Minor: cancelling after the upload, and cancelling an outdated-render publish, leave the wrong state

| | |
|---|---|
| Requirement | Design "Then the post is `published` …; on failure … the render is kept"; DB design 5 (project statuses); post error codes |
| Where | `publishing.py:267-282` |
| Verified | Read |

- **The Short is on YouTube but the app says stopped.** G3, or deleting the project, during the processing wait marks the post `failed` with "Publishing was stopped.". By then the video is already on YouTube, perhaps public, and the post's `url` is null. The creator sees no link to it, and "Try again" makes a second copy (QB6-002).
- **The project status is wrong after a cancel.** A publish of an outdated render starts from `ready` or `draft`, because the project was edited after the render. On cancel, `previousStatus` is restored only if it was `rendered`, `post_failed` or `posted`. Otherwise the project becomes `rendered`, which says the current version is rendered.
- **An undocumented error code.** The code is `cancelled`, which isn't in the design's list of post error codes.

**Suggested fix**: once `media_id` is set, either refuse the cancel or finish the post as published with its link. Restore any `previousStatus`. Add `cancelled` to the documented codes.

### QB6-012 Minor: F5's `409 already_running` and the YT-7 behaviour are not what the documents say

| | |
|---|---|
| Requirement | YT-7 ("the second request returns the existing post (idempotent) or `409 already_posted`"); F5 error list |
| Where | `publishing.py:92-97`; `routes/publishing.py:70-82` |
| Verified | Read |

Without an `Idempotency-Key`, a second Publish click while the first is running gets `409 already_running`. That code isn't in F5's documented errors, and YT-7 says the second click gets the existing post. The key does make a true double click return the first answer, so behaviour is safe. But F7 will be written from the documents and won't expect `already_running`. Two other answers are undocumented too: F1's `503 provider_unavailable`, and the extra endpoint `GET /projects/:id/posts`.

**Suggested fix**: add `already_running` to F5, or return the active post with `200`. Document F1's 503 and the list endpoint, or remove the endpoint. Tell F7 to always send `Idempotency-Key` on F5.

### QB6-013 Minor: the tests don't cover the risky paths

| | |
|---|---|
| Requirement | Milestone definition (tests for each feature); YT-4, YT-6, YT-8 to YT-10, YT-13 |
| Where | `tests/test_publishing.py` (9 tests) |
| Verified | Read |

The 9 tests cover the happy path, state reuse, `no_channel`, `returnTo`, refusals, a quota failure, F4 and disconnect. Nothing covers:
- an upload of more than one chunk (the test render is far under 8 MB);
- a `308` with a partial `Range`;
- a retryable error with resume;
- the soft limit (QB6-004);
- a token refresh, and a refresh refused with `invalid_grant` (YT-6);
- `render_outdated` and `confirmOutdated` (YT-13);
- `rejected` processing (YT-10), although the mock has a `processing` knob;
- cancel during upload and during processing;
- concurrent F5;
- `reconnect_required` at F5;
- the migration's downgrade.

**Suggested fix**: add those cases to the mock and the tests. Several are one-line knobs on `MockYouTube`, such as a chunk size override and a partial-range answer.

### QB6-014 Polish: documents and comments disagree with the code and the decision

- **YT-1.** PROJECT_STATUS line 180 ("videos stay private until Google audits the app"), technical design line 1157 ("private until Google's audit") and the comment at `publishing.py:262` ("YouTube keeps every upload private") contradict the design (YT-1: "may be private") and the observed public Short.
- **PROJECT_STATUS** line 35 still says B6 is "Designed".
- **API design** Project shape (line 121) lacks `postTitle`. F4's text there still says `output = {"caption", "hashtags"}`; the section note points to the new document, but the shape line wasn't updated.
- **DB design** 3.13 `posts` wasn't updated with `platform`, `youtube_account_id`, `title`, `visibility`, `progress` and `error_code` (README "Database changes" asks for it). `error_code` isn't in the design document's Data list either.
- **`YOUTUBE_REDIRECT_URI` and `TOKEN_ENCRYPTION_KEY`** are documented as settings but not passed through `docker-compose.yml:45-48`, so values in `.env` are silently ignored (the development key is always used). `.env.example` doesn't list any of the new settings.

### QB6-015 Polish: the downgrade turns YouTube posts into Instagram posts, and nothing stops two users connecting the same channel

`0002_youtube_publishing.py:55-64`: the downgrade drops `platform`, so YouTube posts stay in `posts` and look like Instagram posts, with `media_id` and `permalink` pointing at YouTube. Delete `platform = 'youtube'` rows first, or refuse the downgrade while any exist. For `youtube_accounts.channel_id`, see Q3.

### QB6-016 Polish: stray and unrelated changes in the working tree

- **A stray directory.** Untracked `api/C:/Users/prave/AppData/Local/Temp/x/cryptography-44.0.0-…whl` is a wheel left by a mis-quoted path. `git add -A` would commit it, and it is in the API's Docker build context. Delete it before committing.
- **The baseline edited.** `migrations/versions/0001_baseline.py` was reformatted, although README "Database changes" says not to edit the baseline. `migrations/env.py` and the order of `constraints.txt` were also reformatted. Nothing changes in behaviour, but this is noise in the B6 commit. Revert, or commit separately.

---

## 2. What passed

| Area | Result | How |
|---|---|---|
| State (F1, F2) | 32-byte random state in Redis, 10 min, `GETDEL` (once), bound to user and platform; checked against `signed_in_user_id` before `error`/`code` are read; any mismatch → `/app/settings?connection_error=youtube&reason=failed`. Matches API F2 | Read; covered by `test_a_state_is_used_once_and_belongs_to_its_user` (not run) |
| `returnTo` | `//evil.example`, `/.//evil.example`, `/%2e%2e//evil.example`, `/%2F%2Fevil.example`, `/a/..//evil.example`, `/%2e/%2fevil.example`, `/%09/…`, `/%5C…`, `https://…` and a `%3F`/`%23` smuggle all stay on the site. Over 300 characters, control characters and backslash fall back to the default. Fragments are dropped. `connected`/`connection_error`/`reason`/`instagram`/`platform` are removed, and the result is added with `urlencode` | **Run** on a copy of the two functions; read |
| CSRF | F3 (`DELETE`) needs `X-CSRF-Token` (`auth.py:42-49`). F1 is a GET whose only effect is a Redis state that another site can't read back. F2 is protected by the state's user binding. Cookies are `SameSite=Lax`, so they are sent on Google's top-level redirect | Read |
| Leakage | The request log records `request.path` without the query string. Werkzeug is at WARNING. Token and API errors carry only a status and a reason. The callback answers `Cache-Control: no-store` and `Referrer-Policy: no-referrer` and never shows a stack trace. `youtube_client_secret` and `token_encryption_key` have `repr=False` | Read |
| Encryption and keys | Fernet; the key is validated at start-up; production refuses the development key, the mock and a non-https redirect URI; a key change makes tokens count as missing (reconnect) | Read |
| Disconnect (YT-12) | Row locked, deleted, committed, then the refresh token revoked (best effort); posts kept (`youtube_account_id` set null) | Read; test (not run) |
| Upload mechanics | Session URL saved with `state=uploading` right after it is opened; `Content-Range` per chunk; `308` + `Range` parsed (`bytes=0-N` → N+1; none → 0); S3 `Range` reads from the confirmed offset; after a network error or 5xx/429, asks with `bytes */total` and continues (3 tries, backoff); an expired session (404) fails cleanly; cancel checked before each chunk and each poll; video id saved with `state=processing` straight after the last chunk | Read |
| Metadata | Title, description = caption + `#hashtags`, tags capped at 500 characters, category 22, `selfDeclaredMadeForKids=false`, privacy = chosen visibility (default `public`); description checked at 5,000 **bytes** | Read; test checks the description and privacy (not run) |
| YT-1 | Privacy read back from `videos.list` after processing; `privateUntilVerified` only when the creator didn't choose private and YouTube reports private | Read |
| Time limits | Publish soft/hard 1200/1260 s; sweeper 22 + 1 min = 1380 s, after the hard limit; Redis visibility timeout ≥ 3600 s; `acks_late` with prefetch 1 (apart from QB6-004) | Read |
| Double F5 | `get_project(lock=True)` serialises; app check for a live post on (render, platform) under the lock; partial unique index `(render_id, platform) WHERE state <> 'failed'` as backstop; `rendering`/`posting` refused; idempotency key replays | Read |
| Status transitions | `posting` on F5; `posted` on success (only from `posting`); `post_failed` on failure; render, script regenerate and B11 refused while `posting`; `after_edit` keeps `posting` | Read |
| F5 validation | Title 1–100, caption ≤ 2,200, hashtags ≤ 30 (deduplicated, one word), no `<` `>` in any; visibility enum; `instagram` → `404 not_available`; `not_ready`, `render_outdated` unless `confirmOutdated`, `not_connected`, `reconnect_required`, `already_posted` | Read |
| F4 | 0 credits (`COSTS` and `JOB_COSTS`), `llm` queue, refused while scripting and with no scenes; output `{title, caption, hashtags}` cleaned (≤ 100 / 2,200, `<` `>` stripped, ≤ 10 tags) and saved to `postTitle`/`postCaption`/`hashtags`; prompt asks for ≤ 90-character titles | Read |
| Migration 0002 | `youtube_accounts` as designed (unique cascading `user_id`, bytea tokens, status check, `updated_at` trigger); `posts` columns with checks; old index dropped, new partial index; `projects.post_title` ≤ 100. Consistent with the baseline: `posts.state` check already allows the five states (plus `publishing`), `caption ≤ 2200` matches `POST_CAPTION_MAX`. Downgrade reverses in a valid order (apart from QB6-015) | Read |
| Models | `YoutubeAccount`, `Post`, `Project.post_title` match the migration and baseline (types, defaults, FKs) | Read |
| JSON | Post has exactly the documented fields; `error` only when failed; `latestPost` newest by `created_at`; `connections` = `{youtube: {status, name, pictureUrl, connectedAt} | null, instagram: null}` and the old `instagram` field kept; `postTitle` in Project; F6 404s another user's post or a deleted project's | Read |
| Secrets | No Google client id, secret or token in any tracked or new file; `.env` ignored and untracked; Compose passes the id and secret only through `${…:-}` | Read (pattern scan, values not printed) |

## 3. Questions

- **Q1. Can `reason=not_tester` (YT-2) happen at all?** The code never returns it (`connections.py:113-116`). Its comment says a Testing-mode refusal arrives as `access_denied`, which is reported as `denied`. As far as I know, Google stops a non-test user on its own error page and doesn't redirect back. Either way, YT-2 and the F2 reason list promise something the backend can't tell apart. Should YT-2 change to "Google shows its own message; `denied` covers it", with F7's `denied` text mentioning test users while the app is in Testing?
- **Q2. Is a Short still processing after 10 minutes "published"?** `publishing.py:239-240` marks it published with a link and the privacy as last seen. The design says "poll … for up to 10 minutes until processed (YT-10)", and the error list has `timed_out`. Publishing it is friendlier, and it is on YouTube. But a later `rejected` from YouTube would never be shown, and `privateUntilVerified` may be wrong. Please confirm, and write it down.
- **Q3. Can two ReelForge users connect the same channel?** `channel_id` isn't unique. With brand accounts, two people can manage one channel. Allowed, or refused at connect?
- **Q4. Does the title need a fallback?** `postTitle` starts empty, and F5 requires a title. The design says the title comes "from the Reel's title". Should the API fall back to `project.title` (cut to 100 characters) when `postTitle` is empty, or is that F7's job?

## 4. What F7 will need

- **Connect.** `GET /connections/youtube/connect?returnTo=<current path>` → navigate to `authorizeUrl`. On return, read `connected=youtube` or `connection_error=youtube&reason=denied|no_channel|failed` (treat unknown reasons as `failed`), then strip them from the URL. `user.connections.youtube.status` can be `reconnect_required`. After a `409 reconnect_required` from F5, or a post that failed with that code, re-fetch `/me`, because the job may have just changed it (QB6-005, QB6-008).
- **Pre-fill.** Use `project.postTitle`, falling back to `project.title` (Q4), plus `postCaption` and `hashtags`. F4 returns a job; poll G2, then re-read the project or use `job.output`. If QB6-001 is fixed, show the credit lines read-only.
- **Publish.** F5 body `{platform:"youtube", title, caption, hashtags, visibility:"public"|"unlisted"|"private", confirmOutdated}`, always with `Idempotency-Key`. Expect `409` codes `not_connected`, `reconnect_required`, `already_posted`, `already_running` (QB6-012), `render_outdated` and `not_ready`, and `400 validation_error` with `details.fields`. Check `<` `>`, 100 and 2,200 characters on screen (YT-11).
- **Tracker.** Poll F6 every 2 s: `state` queued → uploading (10–80) → processing (80–98) → published (100). On success show `url` and, if `privateUntilVerified`, "YouTube kept it private until ReelForge is verified". On failure show `error.message` with Try again. Until QB6-002 is fixed, Try again re-uploads. Cancel goes through G3 with the job id from the F5 answer (`job.id`). Note QB6-011.
- **Mock API.** The mock in `frontend/src/mocks` needs `connections` on the user, F1 to F6, `postTitle`, `latestPost` and the `posting` progress.
