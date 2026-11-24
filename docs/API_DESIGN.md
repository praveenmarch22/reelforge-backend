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
| Idempotency | `Idempotency-Key` header accepted on endpoints marked **idempotent**. Same key + same body → the first response again; same key + different body → `409 idempotency_conflict`. Keys last 24 h |
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
| 409 | `already_running` | Same job already active |
| 409 | `not_ready` | Render with scenes not ready; `details.scenes` |
| 409 | `too_long` | Timeline over 90 s; `details.seconds` |
| 409 | `render_outdated` | Publishing a render older than the current timeline (allowed with `"confirmOutdated": true`) |
| 409 | `already_posted` | This render already has a live post |
| 409 | `instagram_not_connected` | |
| 409 | `instagram_reconnect_required` | Token expired or revoked |
| 409 | `idempotency_conflict` | Key reused with a different body |
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

**Project** (full): ProjectSummary plus `topic`, `tone`, `style`, `character`, `voiceId`, `musicMood`, `music` (`{"trackId"}` or `{"assetId"}` or null), `settings`, `estimatedSeconds`, `timelineVersion`, `postCaption`, `hashtags`, `scenes` (array of Scene), `activeJobs` (array of Job), `latestRender` (Render or null), `latestPost` (Post or null), `createdAt`.

**Scene**
```json
{"id": "…", "position": 1, "narration": "…", "onScreenText": "…", "visualPrompt": "…",
 "stockQuery": "…", "mood": "calm", "visualSource": "stock",
 "voiceStatus": "ready", "visualStatus": "running",
 "voice": {"assetId": "…", "url": "https://…", "durationSeconds": 3.6},
 "visual": {"assetId": "…", "kind": "video", "url": "https://…", "thumbnailUrl": "https://…", "durationSeconds": 7.0, "provider": "pexels"},
 "words": [{"text": "Most", "start": 0.05, "end": 0.31}], "wordsEdited": false,
 "trimStart": 0, "trimEnd": null, "lastError": null}
```

**Job**
```json
{"id": "…", "type": "voice", "projectId": "…", "sceneId": "…", "state": "running", "progress": 40,
 "provider": "elevenlabs", "creditsReserved": 2, "creditsRefunded": false,
 "error": null, "output": null, "queuedAt": "…", "finishedAt": null}
```

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

**A2. `POST /auth/verify-email`**: Public. Body: `token`. `200` → `{"user": User}` and session cookies (opening the link signs the user in on that browser, so it also works on a different device from the one used to sign up). Errors: `410 token_invalid`, `403 account_disabled`.

**A3. `POST /auth/resend-verification`**: User. No body (the email comes from the session; this replaces the `email` body mentioned in the technical design). `204`. Limited to 3 per hour. Errors: `409 already_verified`, `429 rate_limited`.

**A4. `POST /auth/login`**: Public. Body: `email`, `password`. `200` → `{"user": User}` and cookies. Errors: `401 invalid_credentials` (same message for unknown email), `403 account_disabled`, `429` (5 per minute per IP and per email).

**A5. `POST /auth/refresh`**: Refresh cookie. Rotates the refresh token. `200` → `{"user": User}`. Errors: `401 not_authenticated`. Errors also include `403 account_disabled`. Reuse of an old refresh token while its replacement is in use revokes that login on every tab (the login, not the user's other devices). Two tabs of one browser may refresh at the same moment: for 10 seconds after a token is exchanged, presenting it again is answered with `401` without revoking anything and without clearing cookies.

**A6. `POST /auth/logout`**: User. Revokes the refresh token and clears cookies. `204`.

**A7. `POST /auth/forgot-password`**: Public. Body: `email`. `204` whether or not the email exists; the lookup and the sending happen in the background, so the response time is the same too. Sends a reset link (1 h). Errors: `400 validation_error`, `429 rate_limited` (counted per address typed, existing or not, so it reveals nothing).

**A8. `POST /auth/reset-password`**: Public. Body: `token`, `password`. `204`; all sessions are revoked. Errors: `410 token_invalid`, `400`.

**A9. `GET /me`**: User. `200` → `{"user": User}`. The web app calls this on load.

**A10. `PATCH /me`**: User. Body (all optional): `name`, `niche`, `defaults.voiceId`, `defaults.captionStyle`, `defaults.mode`, `onboardingCompleted` (true only). `200` → `{"user": User}`. `niche` is one text value of up to 120 characters holding at most three topics separated by commas, with no repeats (for example `Fitness, Finance, gardening`); an empty string clears it. The web app builds it from up to three choices: listed topics, plus at most one free-text topic of up to 40 characters (counted in Unicode code points, the unit the server counts in; the web app never cuts through the middle of an emoji) that cannot itself contain a comma. The server rejects more than three comma-separated topics with `400 validation_error`.

**A11. `POST /me/password`**: User. Body: `currentPassword`, `newPassword`. `204`; other sessions revoked. Errors: `401 invalid_credentials` (the current password is wrong; the session is still valid, so the web app must not treat this 401 as signed out), `400`, `429`.

**A12. `DELETE /me`** (P1): User. Body: `password`. `204`. Soft-deletes the account, disconnects Instagram, cancels jobs; purged after 7 days.

### B. Ideas and projects (12)

**B1. `POST /ideas`**: Verified. Body: `niche` (required), `keywords`, `count` (1–10, default 5). `202` → `{"job": Job}`. When done, `job.output.ideas` = `[{"title", "hook", "angle"}]`. Free (0 credits). 20 per hour.

**B2. `GET /projects`**: User. Query: `status` (one status or a comma-separated list, for example `render_failed,post_failed`), `q` (title search), `limit`, `cursor`. `200` → `{"items": [ProjectSummary], "nextCursor", "counts": {"total": 12, "posted": 8}}`. `counts` covers all of the user's projects, ignoring the filters; the dashboard tiles use it. While any returned item is in progress, the client polls every 2 s.

**B3. `POST /projects`**: Verified. Create a project and start writing its script.
- Body: `topic` **or** `script` (pasted text, ≤ 5,000 characters), `tone`, `mode`, `targetSeconds` (15/30/60/90), `style`, `character`, `voiceId` (defaults from the profile).
- `202` → `{"project": Project, "job": Job}` (status `scripting`; becomes `draft` when the script is ready).
- Cost: 1 credit. Errors: `402`, `422 content_refused` (arrives on the job).

**B4. `GET /projects/:id`**: User. `200` → `{"project": Project}`. Polled every 2 s while `activeJobs` is not empty.

**B5. `PATCH /projects/:id`**: User. Body (all optional): `title`, `tone`, `style`, `character`, `voiceId`, `musicMood`, `music` (`{"trackId"}`, `{"assetId"}` or null), `settings` (partial), `postCaption`, `hashtags`.
- `200` → `{"project": Project, "estimate": Estimate}`.
- Changing `voiceId` marks all voices `outdated`; changing `style` or `character` marks Full AI visuals `outdated`. Settings changes only rebuild the timeline.

**B6. `DELETE /projects/:id`**: User. `204`. Cancels active jobs, refunds them, soft-deletes; files purged after 7 days.

**B7. `POST /projects/:id/duplicate`** (P1): User. `201` → `{"project": Project}`. Copies script and settings; reuses existing assets (no credits).

**B8. `POST /projects/:id/script/regenerate`**: Verified. Body: optional `instructions` ("make it funnier"). Replaces all scenes. `202` → `{"job": Job}`. 1 credit. Error `409 already_running`.

**B9. `POST /projects/:id/hooks`** (P1): Verified. `202` → `{"job": Job}`; `output.hooks` = 3 alternative opening lines. 0 credits.

**B10. `GET /projects/:id/estimate`**: User. Credits needed to generate everything missing or outdated.
```json
{"credits": 14, "balance": 86,
 "items": [{"type": "voice", "count": 6, "credits": 12}, {"type": "ai_image", "count": 2, "credits": 2},
           {"type": "visual", "count": 4, "credits": 0}, {"type": "music", "count": 1, "credits": 0}]}
```

**B11. `POST /projects/:id/generate`**: Verified, **idempotent**. Generates all missing or outdated voices, visuals and music.
- Body: optional `sceneIds` (limit to these scenes).
- `202` → `{"jobs": [Job], "creditsReserved": 14, "balance": 72}`.
- Errors: `402 insufficient_credits` (nothing started), `409 already_running` (everything already in progress), `503 provider_unavailable`.

**B12. `GET /projects/:id/timeline`**: User. The timeline for the Remotion Player, with presigned URLs. `200` → `{"timelineVersion": 4, "timeline": {…}}` (shape in [PIPELINE_STRATEGY.md](PIPELINE_STRATEGY.md)). Scenes not ready yet have empty `src` and show a placeholder.

### C. Scenes (10)

**C1. `POST /projects/:id/scenes`**: User. Body: `narration` (required), `onScreenText`, `visualPrompt`, `stockQuery`, `mood`, `visualSource`, `position` (default: last). `201` → `{"scene": Scene, "estimate": Estimate}`.

**C2. `PATCH /scenes/:id`**: User. Body (all optional): `narration`, `onScreenText`, `visualPrompt`, `stockQuery`, `mood`, `visualSource`, `words` (edited caption words; text only, times kept), `trimStart`, `trimEnd`.
- `200` → `{"scene": Scene, "estimate": Estimate}`.
- `narration` or `mood` → voice `outdated`. `visualPrompt`, `stockQuery` or `visualSource` → visual `outdated`. `onScreenText`, `words`, trims → timeline rebuild only.

**C3. `DELETE /scenes/:id`**: User. `204`. Remaining scenes are renumbered.

**C4. `POST /scenes/:id/duplicate`**: User. `201` → `{"scene": Scene}` placed after the original, reusing its assets.

**C5. `POST /projects/:id/scenes/reorder`**: User. Body: `sceneIds` (every scene id, in the new order). `200` → `{"scenes": [Scene]}`. Error `400` if the list doesn't match the project's scenes.

**C6. `POST /scenes/:id/rewrite`**: Verified. Body: `instructions` (for example "make this punchier", ≤ 200 characters). `202` → `{"job": Job}`; when done the scene's text is updated and its voice marked `outdated`. 1 credit.

**C7. `POST /scenes/:id/voice/regenerate`**: Verified, **idempotent**. Body: optional `voiceId` (this scene only). `202` → `{"job": Job}`. 2 credits per started 30 s. If `wordsEdited` is true, body must include `"confirmReplaceWords": true` (CA-4).

**C8. `POST /scenes/:id/visual/regenerate`**: Verified, **idempotent**. Body: optional `visualSource`, `visualPrompt`, `stockQuery`. Forces a new result even if inputs are unchanged (new seed or next stock result). `202` → `{"job": Job}`. Cost depends on source (0 stock, 1 AI image, 10 AI clip).

**C9. `GET /scenes/:id/stock-search`**: Verified. Query: `q` (default the scene's stock query), `page`. `200` → `{"items": [{"providerId": "pexels:12345", "thumbnailUrl", "previewUrl", "durationSeconds", "width", "height", "author"}], "nextPage"}`. 60 per hour.

**C10. `POST /scenes/:id/visual/select`**: User. Body: `{"stock": "pexels:12345"}` **or** `{"assetId": "…"}` (a completed upload). `202` → `{"job": Job}` for stock (downloaded in the background), or `200` → `{"scene": Scene}` for an upload. 0 credits.

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

**D3. `GET /voices`**: User. `200` → `{"items": [{"id", "name", "provider": "elevenlabs", "gender", "accent", "style", "previewUrl"}]}`. A curated list, cached.

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

**H1. `GET /credits`**: User. `200` → `{"balance": 86, "monthlyAllowance": 100, "resetsAt": "…", "costs": {"script": 1, "voicePer30s": 2, "aiImage": 1, "aiVideoClip": 10, "render": 2, "transcribe": 1}}`. The web app shows costs from this, never hard-coded.

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
| B1 ideas, B3/B8 scripts, C6 rewrite, F4 caption | 20 per hour per user combined |
| C9 stock search | 60 per hour per user |
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
