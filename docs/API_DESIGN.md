# ReelForge: API design

| | |
|---|---|
| Version | 1.0 |
| Date | 2026-11-11 |
| Base URL | `https://api.<domain>/api/v1` (locally `http://localhost:5000/api/v1`) |
| Related | [Database design](DB_DESIGN.md), [Technical design](TECHNICAL_DESIGN.md), [PRD](PRD.md) |

---

## 1. How many APIs

**70 endpoints in total**: 67 on the Flask API and 3 on the internal render service.

| Group | Count | Who calls it |
|---|---|---|
| A. Auth and account | 12 | Web app |
| B. Ideas and projects | 12 | Web app |
| C. Scenes | 10 | Web app |
| D. Uploads, voices and music | 4 | Web app |
| E. Rendering | 3 | Web app |
| F. Instagram and posts | 6 | Web app (and Instagram's redirect) |
| G. Jobs | 3 | Web app |
| H. Credits | 2 | Web app |
| **Creator-facing total** | **52** | |
| I. Admin | 13 | Admin pages (admins only) |
| J. System and internal | 2 | Load balancer, render service |
| **Flask API total** | **67** | |
| K. Render service (Node, private network) | 3 | Workers |
| **Grand total** | **70** | |

Of the 52 creator-facing endpoints, 49 are needed for version 1 (P0) and 3 are P1: `DELETE /me`, `POST /projects/:id/duplicate`, `POST /projects/:id/hooks`.

## 2. Conventions

| Topic | Rule |
|---|---|
| Format | JSON. Field names `camelCase` in JSON, `snake_case` in Python and the database |
| IDs | UUIDs |
| Times | ISO 8601 UTC, for example `2026-11-11T09:30:00Z` |
| Auth | httpOnly cookies `rf_access` (holds a token valid for 15 min; the cookie itself is kept for 30 days so that an expired token is answered with `401 token_expired` rather than looking like no session) and `rf_refresh` (30 days, path `/api/v1/auth`). Every POST, PATCH and DELETE sends `X-CSRF-Token` (value from the `rf_csrf` cookie). The server checks it only on endpoints that need a session; the Public endpoints (signup, login, refresh, verify-email, forgot-password, reset-password) ignore it, because no `rf_csrf` cookie exists before the first login. `rf_csrf` is readable by JavaScript and set for the parent domain, so the web app can read it when the API is on `api.<domain>` |
| Auth levels in this document | **Public**: no login. **User**: logged in. **Verified**: logged in and email verified. **Admin**: `is_admin`. **Internal**: private network with a shared secret |
| Slow work | `202 Accepted` with job ids; the client polls `GET /jobs/:id` or `GET /projects/:id` every 2 s |
| Pagination | `?limit=20&cursor=…` (max 100). Response: `{"items": [...], "nextCursor": "…" or null}` |
| Idempotency | `Idempotency-Key` header (up to 100 letters, digits, `-`, `_`, `:` or `.`) accepted on endpoints marked **idempotent**. Same key + same body → the first response again; same key + different body → `409 idempotency_conflict`; the same key sent again while the first request is still being handled → `409 already_running`. Only successful responses are kept, for 24 h, so a refused request can be retried with its key |
| Validation | Pydantic; unknown fields are rejected with `400 validation_error` |
| Rate limits | `429 rate_limited` with `Retry-After` (seconds) and the same number in `details.retryAfterSeconds` |
| Ownership | Anything belonging to another user returns `404 not_found` (never `403`), so ids can't be probed |

### Error format

```json
{"error": {"code": "insufficient_credits", "message": "This needs 12 credits; you have 5.",
           "details": {"needed": 12, "balance": 5}}}
```

### Error codes

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `validation_error` | `details.fields`: `{"narration": "Required"}` |
| 401 | `not_authenticated` | No session |
| 401 | `token_expired` | Access token expired; call `/auth/refresh` and retry |
| 401 | `invalid_credentials` | Wrong email or password |
| 402 | `insufficient_credits` | `details.needed`, `details.balance` |
| 403 | `email_not_verified` | Must verify email first |
| 403 | `account_disabled` | Blocked by admin |
| 403 | `forbidden` | Not an admin |
| 403 | `csrf_failed` | Missing or wrong CSRF token |
| 404 | `not_found` | Missing or not yours |
| 405 | `method_not_allowed` | The address exists but not with this method; `Allow` header lists the ones that do |
| 409 | `email_taken` | Sign-up with an existing email |
| 409 | `already_verified` | Resend verification when the email is already verified |
| 409 | `already_running` | Same job already active, or the project's script is still being written (scene edits wait for it) |
| 409 | `job_finished` | Cancelling a job that has already finished |
| 409 | `no_script` | Hook suggestions for a project that has no scenes yet |
| 409 | `not_ready` | Render with scenes not ready; `details.scenes` |
| 409 | `too_long` | Timeline over 90 s; `details.seconds` |
| 409 | `render_outdated` | Publishing a render older than the current timeline (allowed with `"confirmOutdated": true`) |
| 409 | `already_posted` | This render already has a live post |
| 409 | `instagram_not_connected` | |
| 409 | `instagram_reconnect_required` | Token expired or revoked |
| 409 | `idempotency_conflict` | Key reused with a different body |
| 409 | `words_edited` | A new voice for a scene whose caption words were fixed by hand, without `"confirmReplaceWords": true` (CA-4) |
| 410 | `token_invalid` | Verification or reset link expired or used |
| 413 | `file_too_large` | An upload over its size limit |
| 413 | `payload_too_large` | A request body over 1 MB (uploads go straight to storage, never through the API) |
| 415 | `unsupported_file_type` | An upload of a type that isn't accepted |
| 415 | `unsupported_media_type` | A body that isn't JSON |
| 422 | `content_refused` | A provider's safety filter refused the prompt |
| 422 | `upload_invalid` | Uploaded file is corrupt, silent or too long |
| 429 | `rate_limited` | |
| 500 | `internal_error` | Something failed on the server. The details are in the server log under the response's `X-Request-ID`, never in the response |
| 503 | `provider_unavailable` | Provider switched off or all fallbacks down |

### Shared objects

These appear in many responses.

**User**
```json
{"id": "…", "email": "a@b.com", "name": "Praveen", "niche": "fitness", "emailVerified": true,
 "defaults": {"voiceId": "…", "captionStyle": "bold-pop", "mode": "faceless"},
 "onboardingCompleted": true, "isAdmin": false,
 "credits": {"balance": 86, "monthlyAllowance": 100, "resetsAt": "2026-12-01T00:00:00Z"},
 "instagram": {"connected": true, "username": "praveen.fit", "status": "connected", "expiresAt": "2027-01-09T00:00:00Z"}}
```

**ProjectSummary** (lists)
```json
{"id": "…", "title": "3 morning habits", "mode": "faceless", "status": "rendered",
 "targetSeconds": 30, "durationSeconds": 31.4, "thumbnailUrl": "https://…", "progress": null, "updatedAt": "…"}
```

`durationSeconds` and `thumbnailUrl` are null until they exist. `progress` is 0–100 while the status is `scripting`, `generating`, `rendering` or `posting`, otherwise null.

`status` is one of the 11 project statuses in [DB_DESIGN.md](DB_DESIGN.md): `scripting`, `draft`, `generating`, `needs_attention`, `ready`, `rendering`, `render_failed`, `rendered`, `posting`, `post_failed`, `posted`. The PRD's shorter list (D1) is the creator-facing grouping: the web app shows `render_failed` and `post_failed` as "Failed", and groups `scripting` with drafts in the library filter while still showing its progress. Lists are sorted by `updatedAt`, newest first. Text fields that have no value yet (for example `niche`, `defaults.voiceId`) are empty strings, never null.

**Project** (full): ProjectSummary plus `topic`, `tone`, `style`, `character`, `voiceId`, `musicMood`, `music` (`{"trackId"}` or `{"assetId"}` or null), `settings`, `estimatedSeconds`, `wordCount` (live count for the editor, PRD S7), `timelineVersion`, `postCaption`, `hashtags`, `scenes` (array of Scene), `activeJobs` (array of Job), `latestRender` (Render or null), `latestPost` (Post or null), `createdAt`.

**Scene**
```json
{"id": "…", "position": 1, "narration": "…", "onScreenText": "…", "visualPrompt": "…",
 "stockQuery": "…", "mood": "calm", "visualSource": "stock",
 "voiceStatus": "ready", "visualStatus": "running",
 "voice": {"assetId": "…", "url": "https://…", "durationSeconds": 3.6},
 "visual": {"assetId": "…", "kind": "video", "url": "https://…", "thumbnailUrl": "https://…", "durationSeconds": 7.0, "provider": "pexels"},
 "words": [{"text": "Most", "start": 0.05, "end": 0.31}], "wordsEdited": false,
 "trimStart": 0, "trimEnd": null, "lastError": null, "estimatedSeconds": 3.2}
```

`estimatedSeconds` is the spoken length estimated from the narration (2.5 words per second, PRD S4); the real length comes from the voiceover. `voice` and `visual` are null until generated. Their `url` is a link to the file in storage, valid for 2 hours and different on every response (key media by `assetId`); fetch the project again for fresh links. `voice` also has `provider` (`elevenlabs` or `edge`), `voiceId` and `estimatedTimings` (true when the caption timings were spread evenly and are only roughly in sync). `visual` also has `kind` (`video` or `image`; stills get a slow zoom or pan), `thumbnailUrl` (a small still for lists: the clip's still, or a 360-pixel copy of an AI image), `provider` (`pixabay`, `cloudflare`, `upload`…), `author` and `sourceUrl` (stock credits), `width`, `height` and `stockFallback` (an AI image used because no stock clip fitted). `words` are the voiceover's word timings in seconds (empty until the first voice).

**Job**
```json
{"id": "…", "type": "voice", "projectId": "…", "sceneId": "…", "state": "running", "progress": 40,
 "provider": "elevenlabs", "creditsReserved": 2, "creditsRefunded": false,
 "error": null, "output": null, "queuedAt": "…", "finishedAt": null}
```

When `state` is `failed`, `error` is `{"code", "message"}`; the message is plain language for the creator. Codes: `content_refused` (the AI's content rules refused it; edit the text), `provider_unavailable` (no AI service available right now), `provider_failed` (the AI service did not answer usefully after retries and fallbacks), `scene_changed` (a rewrite or voice whose scene was edited or deleted meanwhile; the voice then shows `outdated`, or `missing` if the scene never had one), `timed_out` (stopped after its time limit), `stock_unavailable` (a stock clip chosen with C10 is no longer offered), `no_stock_match` (no stock clip fits the scene and the day's free AI images are used up), `queue_unavailable` (could not be started), `internal_error`. Credits are refunded for every failure (`creditsRefunded: true`). Jobs of a deleted scene are kept with `sceneId: null`.

**Render**
```json
{"id": "…", "state": "succeeded", "progress": 100, "timelineVersion": 4, "outdated": false,
 "video": {"url": "https://…", "durationSeconds": 31.4, "sizeBytes": 9420311}, "thumbnailUrl": "https://…",
 "error": null, "createdAt": "…"}
```

**Post**
```json
{"id": "…", "renderId": "…", "state": "published", "caption": "…", "shareToFeed": true,
 "permalink": "https://www.instagram.com/reel/…", "error": null, "publishedAt": "…"}
```

URLs to media are presigned and valid for 2 hours.

---

## 3. Endpoints

### A. Auth and account (12)

**A1. `POST /auth/signup`**: Public. Create an account and send the verification email.
- Body: `email` (a plain address such as `a@b.com`, sent without surrounding spaces; `Name <a@b.com>` is refused), `password` (8–128 characters, not a common password), `name` (1–80).
- `201` → `{"user": User}` and session cookies (the user is logged in but unverified).
- Errors: `400 validation_error`, `409 email_taken`, `429`.
- Side effects: `credit_accounts` row with the allowance; verification token emailed (24 h).

**A2. `POST /auth/verify-email`**: Public. Body: `token`. Marks the address verified; the link works once. What else happens depends on who is signed in on that browser:
- nobody: `200` → `{"user": User}` and session cookies, so the link also works on a different device from the one used to sign up;
- the same user: `200` → `{"user": User}`; their session is kept as it is;
- a different user: `200` → `{"user": User, "verifiedOtherAccount": true}`, where `user` is the person who is signed in. Their session is not touched and no cookies are set, so a link can never move someone into the sender's account.

"Signed in" means the browser's login still has a usable refresh token; an access token that has only passed its 15 minutes counts. A session of a disabled or deleted account counts as nobody. Errors: `410 token_invalid`, `403 account_disabled`.

**A3. `POST /auth/resend-verification`**: User. No body (the email comes from the session; this replaces the `email` body mentioned in the technical design). `204`. Limited to 3 per hour. Errors: `409 already_verified`, `429 rate_limited`.

**A4. `POST /auth/login`**: Public. Body: `email`, `password`. `200` → `{"user": User}` and cookies. Errors: `401 invalid_credentials` (same message for unknown email), `403 account_disabled`, `429` (5 per minute per IP and per email).

**A5. `POST /auth/refresh`**: Refresh cookie. Rotates the refresh token. `200` → `{"user": User}`. Errors: `401 not_authenticated`. Errors also include `403 account_disabled`. Reuse of an old refresh token while its replacement is in use revokes that login on every tab (the login, not the user's other devices). Two tabs of one browser may refresh at the same moment: for 10 seconds after a token is exchanged, presenting it again is answered with `401` without revoking anything and without clearing cookies.

**A6. `POST /auth/logout`**: User. Revokes the refresh token and clears cookies. `204`.

**A7. `POST /auth/forgot-password`**: Public. Body: `email`. `204` whether or not the email exists; the lookup and the sending happen in the background, so the response time is the same too. Sends a reset link (1 h). Errors: `400 validation_error`, `429 rate_limited` (counted per address typed, existing or not, so it reveals nothing).

**A8. `POST /auth/reset-password`**: Public. Body: `token`, `password`. `204`; all sessions are revoked. Errors: `410 token_invalid`, `400`.

**A9. `GET /me`**: User. `200` → `{"user": User}`. The web app calls this on load.

**A10. `PATCH /me`**: User. Body (all optional): `name`, `niche`, `defaults.voiceId`, `defaults.captionStyle`, `defaults.mode`, `onboardingCompleted` (true only). `200` → `{"user": User}`. `niche` is one text value of up to 120 characters holding at most three topics separated by commas, with no repeats (for example `Fitness, Finance, gardening`); an empty string clears it. The web app builds it from up to three choices: listed topics, plus at most one free-text topic of up to 40 characters (counted in Unicode code points, the unit the server counts in; the web app never cuts through the middle of an emoji) that cannot itself contain a comma. The server rejects more than three comma-separated topics with `400 validation_error`.

**A11. `POST /me/password`**: User. Body: `currentPassword`, `newPassword`. `204`; other sessions revoked. Errors: `401 invalid_credentials` (the current password is wrong; the session is still valid, so the web app must not treat this 401 as signed out), `400`, `429`.

**A12. `DELETE /me`** (P1): User. Body: `password`. `204`. Soft-deletes the account, ends every session, disconnects Instagram, cancels jobs; purged after 7 days. The email address is released at once, so it can be used for a new sign-up straight away. A deleted account cannot be restored. Errors: `401 invalid_credentials` (wrong password; the session is still valid), `429`.

### B. Ideas and projects (12)

**B1. `POST /ideas`**: Verified. Body: `niche` (required), `keywords`, `count` (1–10, default 5). `202` → `{"job": Job}`. When done, `job.output.ideas` = `[{"title", "hook", "angle"}]`. Free (0 credits). 20 per hour.

**B2. `GET /projects`**: User. Query: `status` (one status or a comma-separated list, for example `render_failed,post_failed`), `q` (title search), `limit`, `cursor`. `200` → `{"items": [ProjectSummary], "nextCursor", "counts": {"total": 12, "posted": 8}}`. `counts` covers all of the user's projects, ignoring the filters; the dashboard tiles use it. While any returned item is in progress, the client polls every 2 s.

**B3. `POST /projects`**: Verified. Create a project and start writing its script.
- Body: `topic` (≤ 300 characters) **or** `script` (pasted text, ≤ 5,000 characters); neither gives `fields.topic`, both gives `fields.script`, `tone`, `mode`, `targetSeconds` (15/30/60/90), `style`, `character`, `voiceId` (defaults from the profile).
- `202` → `{"project": Project, "job": Job}` (status `scripting`; becomes `draft` when the script is ready).
- Cost: 1 credit. Errors: `402`, `429`, `503 provider_unavailable` (no language model available; nothing is charged); `content_refused` arrives on the job.
- A pasted script is split into scenes by the server at sentence ends (about 30 words per scene, a paragraph break always starts a new one), so the creator's words never change; the language model only adds the title, on-screen text, visual descriptions and moods.
- If writing fails, the job fails with its error and the credit is refunded; the project stays, as a `draft` with no scenes, and **Write script** (B8) tries again exactly as the first attempt (a pasted script is prepared again, and the AI names the project).

**B4. `GET /projects/:id`**: User. `200` → `{"project": Project}`. Polled every 2 s while `activeJobs` is not empty.

**B5. `PATCH /projects/:id`**: User. Body (all optional): `title`, `tone`, `style`, `character`, `voiceId`, `musicMood`, `music` (`{"trackId"}`, `{"assetId"}` or null), `settings` (partial), `postCaption`, `hashtags`.
- `200` → `{"project": Project, "estimate": Estimate}`.
- Changing `voiceId` marks all voices `outdated`; changing `style` or `character` marks Full AI visuals `outdated`. Settings changes only rebuild the timeline.

**B6. `DELETE /projects/:id`**: User. `204`. Cancels active jobs, refunds them, soft-deletes; files purged after 7 days.

**B7. `POST /projects/:id/duplicate`** (P1): User. `201` → `{"project": Project}`. Copies script and settings; reuses existing assets (no credits). `409 already_running` while the source's script is being written.

**B8. `POST /projects/:id/script/regenerate`**: Verified. Body: optional `instructions` ("make it funnier"). Replaces all scenes; work still running on them is cancelled and refunded. The title, chosen music mood and an edited caption are kept. `202` → `{"job": Job}`. 1 credit. Error `409 already_running` (also while rendering or posting). If it fails, the project returns to its previous status with its scenes untouched.

**B9. `POST /projects/:id/hooks`** (P1): Verified. `202` → `{"job": Job}`; `output.hooks` = up to 3 alternative opening lines. 0 credits. `409 no_script` when the project has no scenes yet.

**B10. `GET /projects/:id/estimate`**: User. Credits needed to generate everything missing or outdated.
```json
{"credits": 6, "balance": 86,
 "items": [{"type": "voice", "count": 6, "credits": 4}, {"type": "ai_image", "count": 2, "credits": 2},
           {"type": "visual", "count": 4, "credits": 0}, {"type": "music", "count": 1, "credits": 0}]}
```
The voice item is priced on the total length of the voices needed (here about 45 s: 2 per started 30 s), not per scene.

**B11. `POST /projects/:id/generate`**: Verified, **idempotent**. Generates all missing or outdated voices, visuals and music.
- Body: optional `sceneIds` (limit to these scenes).
- `202` → `{"jobs": [Job], "creditsReserved": 6, "balance": 80}`.
- Errors: `402 insufficient_credits` (nothing started), `409 already_running` (everything already in progress), `503 provider_unavailable`.

**B12. `GET /projects/:id/timeline`**: User. The timeline for the Remotion Player, with presigned URLs. `200` → `{"timelineVersion": 4, "timeline": {…}}` (shape in [PIPELINE_STRATEGY.md](PIPELINE_STRATEGY.md)). Scenes not ready yet have empty `src` and show a placeholder.

### C. Scenes (10)

**C1. `POST /projects/:id/scenes`**: User. Body: `narration` (required), `onScreenText`, `visualPrompt`, `stockQuery`, `mood`, `visualSource`, `position` (default: last). `201` → `{"scene": Scene, "estimate": Estimate}`.

**C2. `PATCH /scenes/:id`**: User. Body (all optional): `narration`, `onScreenText`, `visualPrompt`, `stockQuery`, `mood`, `visualSource`, `words` (edited caption words; text only, times kept), `trimStart`, `trimEnd`.
- `200` → `{"scene": Scene, "estimate": Estimate}`.
- `narration` or `mood` → voice `outdated`. `visualPrompt`, `stockQuery` or `visualSource` → visual `outdated`. `onScreenText`, `words`, trims → timeline rebuild only.

**C3. `DELETE /scenes/:id`**: User. `204`. Remaining scenes are renumbered.

**C4. `POST /scenes/:id/duplicate`**: User. `201` → `{"scene": Scene, "estimate": Estimate}` placed after the original, reusing its assets.

**C5. `POST /projects/:id/scenes/reorder`**: User. Body: `sceneIds` (every scene id, in the new order). `200` → `{"scenes": [Scene], "estimate": Estimate}`. Error `400` if the list doesn't match the project's scenes. After C3 (delete) the client fetches B10 for the new estimate.

While the project's script is being written (status `scripting`), C1–C6 answer `409 already_running`: the scenes are about to be replaced.

**C6. `POST /scenes/:id/rewrite`**: Verified. Body: `instructions` (for example "make this punchier", ≤ 200 characters). `202` → `{"job": Job}`; when done the scene's text is updated and its voice marked `outdated`. 1 credit.

**C7. `POST /scenes/:id/voice/regenerate`**: Verified, **idempotent**. Body (optional): `voiceId` (one of D3; used for this scene's new voice only, the project's voice is unchanged), `confirmReplaceWords`. `202` → `{"job": Job}`. 1 credit for a scene up to 15 s, else 2 per started 30 s (the length is estimated at 2.5 words per second). Generate (B11) instead charges 2 per started 30 s of all the voices it makes together, so short scenes are not each rounded up. Always makes a new take, even if nothing changed.
- The voice comes from the request's `voiceId`, else the project's, else the account default, else the first voice in D3. ElevenLabs is tried first; when it fails or its monthly allowance is used up, the matching free Edge voice is used (the job's `provider` says which).
- While the job runs, the scene's `voiceStatus` is `queued`, then `running`; then `ready` with `voice` and `words` filled, or `failed` with `lastError`. Cancelling it puts the status back.
- Editing the scene's narration or mood (C2), or the project's voice (B5), while a voice is being made cancels that job and refunds it; the scene then shows `outdated` (or `missing` if it never had a voice). Fixing caption words (C2 `words`) while the voice is being made is refused with `409 already_running`.
- A failed or cancelled voice keeps the scene's earlier voice file, if any. A `ready` project goes back to `draft` when a voice fails; it stays `ready` while a new take is being made.
- Errors: `409 words_edited` if `wordsEdited` is true and `confirmReplaceWords` is not (CA-4; the new voice replaces the hand-fixed words), `409 already_running` (the scene's voice is already being made, or the script is being written), `402 insufficient_credits`, `503 provider_unavailable`.

**C8. `POST /scenes/:id/visual/regenerate`**: Verified, **idempotent**. Body (optional): `visualSource` (`stock`, `ai_image` or `ai_video`), `visualPrompt`, `stockQuery`; what is sent is saved to the scene first, as with C2. Always makes something new: a stock clip this scene hasn't had before (the last 30 are remembered; up to 3 pages of results are looked through), or a new AI image. `202` → `{"job": Job}`. Cost by source: 0 stock, 1 AI image, and until AI video arrives (milestone B7) an `ai_video` scene gets an AI image with slow motion for 1 credit (10 once real clips exist).
- **Stock:** searches with `stockQuery` (else `visualPrompt`, else the narration), then with a shorter query, and picks a clip at least as long as the scene (its voice if made, else the estimate), sharp enough for the vertical frame. Nothing fits, or the stock provider is down → an AI image at no extra charge (PRD G-6; up to 20 a day per creator, counted only when an image is made, then the job fails with `no_stock_match`, or `provider_unavailable` if stock was down); the job's `output.stockFallback` is then true. A clip that fails to download makes way for the next fitting one. Clips are downloaded to our storage with their thumbnail; the author and source page are kept for credits.
- **AI image:** FLUX from the visual prompt, the mood's look and the project's style and character (the same style anchor for every scene). The renderer animates stills with a slow zoom or pan.
- Status follows the job like the voice (C7): `queued`, `running`, then `ready` or `failed`. Editing the scene's `visualSource`, `visualPrompt` or `stockQuery` (C2), or the project's `style` or `character` for AI scenes (B5), while a visual is being made cancels and refunds it.
- If the request changes the source, prompt or query, the current visual is marked `outdated` first, so cancelling the new take leaves the scene `outdated` rather than showing the old visual as ready.
- 60 an hour per creator, counted only when work starts.
- Errors: `400` for `visualSource: "upload"` (uploads are chosen with C10), `409 already_running` (the visual is already being made, or the script is being written), `402 insufficient_credits`, `429 rate_limited`, `503 provider_unavailable`. Job error codes `stock_unavailable` (a clip chosen with C10 is gone) and `no_stock_match` (no clip fits and today's free AI images are used up).

**C9. `GET /scenes/:id/stock-search`**: Verified. Query: `q` (≤ 100 characters; default the scene's stock query, else its visual prompt), `page` (1–25). `200` → `{"items": [{"providerId": "pixabay:12345", "thumbnailUrl", "previewUrl", "durationSeconds", "width", "height", "author", "source": "pixabay", "pageUrl"}], "nextPage", "query"}`, 20 a page, `nextPage` null on the last. Results are kept for 24 hours (Pixabay's terms). `thumbnailUrl` and `previewUrl` point at the stock site and are only for showing results; the screen must show where results come from (for example "Videos from Pixabay"). 60 per hour; `503 provider_unavailable` when no stock provider answers. Pixabay is the source until Pexels issues keys again (decided 2026-11-25).

**C10. `POST /scenes/:id/visual/select`**: User. Body: `{"stock": "pixabay:12345"}` (from C9) **or** `{"assetId": "…"}` (a finished image or video upload of this project, milestone B7). `202` → `{"job": Job}` for stock (downloaded in the background; the scene's `visualSource` becomes `stock`), or `200` → `{"scene": Scene, "estimate": Estimate}` for an upload (`visualSource` becomes `upload`). Any visual being made for the scene is cancelled and refunded first. 0 credits.

### D. Uploads, voices and music (4)

**D1. `POST /uploads`**: Verified. Get a presigned URL to upload directly to storage.
- Body: `purpose` (`scene_visual`, `voiceover`, `music`), `projectId`, `filename`, `contentType`, `sizeBytes`.
- Limits: video ≤ 200 MB (`video/mp4`, `video/quicktime`, `video/webm`); audio ≤ 50 MB (`audio/mpeg`, `audio/wav`, `audio/mp4`); image ≤ 20 MB (`image/jpeg`, `image/png`, `image/webp`).
- `201` → `{"assetId", "uploadUrl", "method": "PUT", "headers": {"Content-Type": …}, "expiresAt"}` (15 min).
- Errors: `413 file_too_large`, `415 unsupported_file_type`, `429` (30 per hour).

**D2. `POST /uploads/:assetId/complete`**: User. Call after the PUT succeeds. The server checks the real type, size and length.
- `200` → `{"asset": {"id", "kind", "durationSeconds", "width", "height", "url"}}`.
- For `purpose: voiceover`: also starts a `transcribe` job (Groq Whisper) and returns `{"asset", "job"}` (`202`). 1 credit.
- Errors: `422 upload_invalid` (corrupt, silent, voiceover over 90 s).

**D3. `GET /voices`**: User. `200` → `{"items": [{"id", "name", "provider": "elevenlabs", "gender", "accent", "style", "previewUrl"}]}`. A curated list of 8 voices, the first being the default. `previewUrl` is ElevenLabs' public sample clip (asked for once a day); it is `""` when ElevenLabs can't be reached. `voiceId` values elsewhere (A10 defaults, B3, B5, C7) must be one of these ids or `""` (the default voice).

**D4. `GET /music`**: User. Query: `mood`. `200` → `{"items": [{"id", "title", "durationSeconds", "moods", "license", "previewUrl"}]}`.

### E. Rendering (3)

**E1. `POST /projects/:id/render`**: Verified, **idempotent**. `202` → `{"render": Render, "creditsReserved": 2}`.
- Errors: `409 not_ready` (`details.scenes`), `409 too_long`, `409 already_running`, `402`.
- Uses the current timeline version; the snapshot is stored with the render.

**E2. `GET /renders/:id`**: User. `200` → `{"render": Render}`. Polled while `state` is `queued` or `running`.

**E3. `GET /projects/:id/renders`**: User. `200` → `{"items": [Render]}`, newest first (render history).

### F. Instagram and posts (6)

**F1. `GET /instagram/connect`**: Verified. Query: `returnTo` (optional; the app page to come back to, for example `/onboarding?step=3`; default `/app/settings`). `200` → `{"authorizeUrl": "https://www.instagram.com/oauth/authorize?…"}`. The web app redirects the browser there. Includes a signed `state` that is valid for 10 minutes, can be used once, and carries the user and the sanitised `returnTo`. Errors: `403 email_not_verified`.

**Rule for `returnTo`** (it ends up in a redirect, so it must never lead off the site). The value is replaced by the default unless all of these hold:
- it is at most 300 characters;
- it starts with `/` and does not start with `//`;
- it contains no backslash and no control characters (tab, carriage return, line feed and the rest of U+0000–U+001F, U+007F), because browsers read `\` as `/` and skip those characters;
- resolved against the web app's own origin, it stays on that origin;
- the resolved path does not start with `//`. Resolving removes `/./` and `/../` segments (also when written as `%2e`), so `/.//evil.example` passes the checks above and then becomes `//evil.example`.

The value that is stored and used is the resolved path and query, never the text as given. Any `#fragment` is dropped and any `instagram` or `reason` query parameters already in it are removed.

**F2. `GET /instagram/callback`**: Public (called by Instagram's redirect). Query: `code`, `state`, or `error`.
- Verifies `state` (signature, not expired, not used before) and that the signed-in user is the user inside `state`; the session cookie is sent on this request because it is a top-level navigation. This stops one person's approval link from connecting another person's account. Then it exchanges the code for a long-lived token, checks the account type and stores the encrypted token.
- Always redirects (`302`) to the web app, to the `returnTo` page from F1 with the result added to its query string: `instagram=connected`, or `instagram=error&reason=personal_account|denied|not_tester|failed`. For example `/onboarding?step=3&instagram=connected`, or `/app/settings?instagram=error&reason=denied`. The parameters are set with URL handling, never by joining strings. If `state` is missing, invalid, expired, already used, or belongs to a different user than the one signed in, it redirects to `/app/settings?instagram=error&reason=failed`. The web app treats any `reason` it does not know as `failed`.

**F3. `DELETE /instagram`**: User. `204`. Removes the token; post history is kept (IG-10).

**F4. `POST /projects/:id/caption/generate`**: Verified. `202` → `{"job": Job}`; `output` = `{"caption", "hashtags"}`, also saved to the project. 0 credits.

**F5. `POST /renders/:id/publish`**: Verified, **idempotent**.
- Body: `caption` (≤ 2,200 characters including hashtags), `hashtags` (≤ 30), `shareToFeed` (default true), `confirmOutdated` (optional).
- `202` → `{"post": Post}`.
- Errors: `409 instagram_not_connected`, `409 instagram_reconnect_required`, `409 already_posted`, `409 render_outdated`, `409 not_ready` (render not finished), `400`.

**F6. `GET /posts/:id`**: User. `200` → `{"post": Post}`. Polled while publishing. On failure, `error.message` explains in plain words, and the client can call F5 again.

### G. Jobs (3)

**G1. `GET /jobs/:id`**: User. `200` → `{"job": Job}`.

**G2. `GET /projects/:id/jobs`**: User. Query: `active=true`, `limit`, `cursor`. `200` → `{"items": [Job], "nextCursor"}`.

**G3. `POST /jobs/:id/cancel`**: User. `200` → `{"job": Job}` (state `cancelled`, credits refunded). Error `409` if already finished.

### H. Credits (2)

**H1. `GET /credits`**: User. `200` → `{"balance": 86, "monthlyAllowance": 100, "resetsAt": "…", "costs": {"script": 1, "voicePer30s": 2, "voiceSceneShort": 1, "aiImage": 1, "aiVideoClip": 10, "render": 2, "transcribe": 1, "ideas": 0, "hooks": 0, "rewrite": 1}}`. The web app shows costs from this, never hard-coded.

**H2. `GET /credits/ledger`**: User. Query: `limit`, `cursor`. `200` → `{"items": [{"id", "amount", "kind", "note", "jobId", "projectId", "createdAt"}], "nextCursor"}`.

### I. Admin (13)

All require **Admin**. Every change is logged with the admin's id.

| # | Endpoint | Purpose |
|---|---|---|
| I1 | `GET /admin/users?q=&limit=&cursor=` | Search users (email, name), with balance and project count |
| I2 | `GET /admin/users/:id` | One user: profile, credits, recent jobs, Instagram status |
| I3 | `PATCH /admin/users/:id` | Body: `isTester`, `monthlyAllowance`, `disabled` (true/false), `isAdmin` |
| I4 | `POST /admin/users/:id/credits` | Body: `amount` (+/−), `note` (required). Creates an `adjust` ledger row |
| I5 | `GET /admin/jobs?state=failed&type=&provider=&limit=&cursor=` | Job list for troubleshooting |
| I6 | `POST /admin/jobs/:id/retry` | Re-enqueue a failed job (no new charge) |
| I7 | `GET /admin/usage?from=&to=` | Daily calls, failures, credits and estimated cost per provider and job type |
| I8 | `GET /admin/providers` | All providers with `enabled`, `reason`, last-hour error rate |
| I9 | `PATCH /admin/providers/:name` | Body: `enabled`, `reason` (the kill switch, AD2) |
| I10 | `GET /admin/music` | Music library including inactive tracks |
| I11 | `POST /admin/music` | Body: `title`, `moods`, `license`, `sourceUrl`, `filename`, `contentType`, `sizeBytes` → track id + presigned upload URL |
| I12 | `PATCH /admin/music/:id` | Body: `title`, `moods`, `license`, `isActive` |
| I13 | `DELETE /admin/music/:id` | Deactivates; deleted for real only if no project uses it |

### J. System and internal (2)

**J1. `GET /health`**: Public. `200` → `{"status": "ok", "db": "ok", "redis": "ok", "storage": "ok", "version": "0.1.0"}`. If a dependency is down: `503` with `"status": "degraded"` and that dependency as `"down"` (no error details are exposed). Used by the load balancer.

Every response, including errors, carries an `X-Request-ID` header; the same id is on every log line for that request. A caller may send its own `X-Request-ID` (letters, digits, `.`, `_`, `-`, up to 64 characters); anything else is replaced.

**J2. `POST /internal/renders/:id/callback`**: Internal (render service only). Header `X-Signature`: HMAC-SHA256 of the body with a shared secret. Body: `{"state": "succeeded"|"failed", "outputKey", "durationSeconds", "sizeBytes", "error"}`. `204`. Not exposed through the public load balancer.

### K. Render service (3, internal Node service)

Reachable only from the workers' private network; requests carry the shared secret.

| # | Endpoint | Purpose |
|---|---|---|
| K1 | `POST /renders` | Body: `{"renderId", "timeline" (with presigned URLs), "outputKey", "thumbnailKey", "callbackUrl"}` → `202 {"renderId"}`. One render at a time per instance; `429` if busy |
| K2 | `GET /renders/:id` | `{"state", "progress", "error"}`, used by the worker as a backup to the callback |
| K3 | `GET /health` | `{"status": "ok", "busy": false, "remotionVersion": "4.0.530"}` |

---

## 4. Rate limits

| Endpoint | Limit |
|---|---|
| A1 signup | 3 per hour per IP |
| A4 login | 5 per minute per IP and per email |
| A3 resend verification | 3 per hour per user |
| A7 forgot password | 3 per hour per email address typed, and 10 per hour per IP |
| A2, A8 link tokens | 30 per hour per IP |
| A11, A12 current-password checks | 5 per minute per user |
| B1 ideas, B3/B8 scripts, B9 hooks, C6 rewrite, F4 caption | 20 per hour per user combined; only requests that start work count |
| C9 stock search | 60 per hour per user |
| C8 visual regenerate | 60 per hour per user; only requests that start work count |
| D1 uploads | 30 per hour per user |
| Everything else | 300 per minute per user |

## 5. Main screens and the endpoints they use

This helps when designing the UI in Stitch.

| Screen | Endpoints |
|---|---|
| Sign up / log in / verify / forgot / reset | A1, A2, A3, A4, A7, A8 |
| Onboarding | A10, D3, F1 |
| Dashboard | A9, B2, H1 |
| New Reel (idea step) | B1, G1, B3 |
| Script editor | B4, B5, C1, C2, C3, C5, C6, B8, B9, B10, B11 |
| Editor (preview) | B4 (polling), B12, B5, C2, C3, C4, C5, C7, C8, C9, C10, D1, D2, D3, D4, G3 |
| Render and publish | E1, E2, F4, F5, F6, F1 |
| Settings | A9, A10, A11, A12, F1, F3, H1, H2 |
| Admin | I1–I13 |

## 6. Example flow: idea to posted Reel

```
POST /ideas                         → job (poll GET /jobs/:id) → 5 ideas
POST /projects {topic}              → project (scripting) + job → poll → draft with scenes
PATCH /scenes/:id                   → edit narration, see estimate
GET  /projects/:id/estimate         → 14 credits
POST /projects/:id/generate         → jobs; poll GET /projects/:id until status = ready
GET  /projects/:id/timeline         → preview in the Player
POST /projects/:id/render           → render; poll GET /renders/:id until succeeded
POST /projects/:id/caption/generate → caption + hashtags
POST /renders/:id/publish           → post; poll GET /posts/:id until published → permalink
```
