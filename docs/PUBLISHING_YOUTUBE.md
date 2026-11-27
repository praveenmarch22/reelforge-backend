# Publishing: YouTube Shorts first (milestone B6)

Decided with the product owner on 2026-11-26: publishing becomes platform-neutral. **YouTube Shorts is built first**, and Instagram Reels is added later in the same flow (B6b). The PRD, API design, DB design and technical design point here for the YouTube details.

## Why YouTube first

- The upload goes from our worker straight to YouTube, so the video doesn't need a public web address. Instagram fetches the video from a URL, which a local stack can't offer without a tunnel.
- The setup is one Google Cloud project with an OAuth client. Instagram needs a Meta app and a Business or Creator account.
- A vertical video up to 3 minutes is a Short, so a ReelForge Reel (90 s at most) qualifies as it is.

**Limits we accept:**
- **May be private until audited (YT-1).** Google's policy is that videos uploaded through an unaudited API project are restricted to private (the YouTube API Services compliance audit lifts it). In our first real test (2026-11-26) the Short came out **public**, so the lock may apply later, or not to every project. The app therefore reads the actual privacy back from YouTube after processing and tells the creator only when YouTube kept it private (`privateUntilVerified`).
- **Quota.** The default is 10,000 units a day per Google project, and one upload costs about 1,600, so about 6 uploads a day. That is enough to build and test (YT-5).
- **Testing mode.** While the OAuth consent screen is in "Testing", only Google accounts listed as test users can connect, and their refresh tokens expire after 7 days (YT-2, YT-6).

## Product behaviour (PRD 5.8)

| ID | Feature | Priority |
|---|---|---|
| P1 | Connect one YouTube channel through Google sign-in; show its name and picture; disconnect | P0 |
| P2 | AI-written title, description and hashtags, editable (shared with Instagram's caption) | P0 |
| P3 | Visibility: Private, Unlisted or Public (Public selected by default; see YT-1) | P0 |
| P4 | Publish now, with live status (uploading, processing, published, failed) and a link to the Short | P0 |
| P5 | Choose the platform on the publish screen; Instagram is shown as "coming soon" until B6b | P0 |
| P6 | Post the same render to YouTube and, later, Instagram (one live post per render per platform) | P1 |

### Flow: connect YouTube
1. From onboarding, Settings or the publish screen, the creator clicks **Connect YouTube**.
2. Google's sign-in asks them to choose an account and approve uploading videos.
3. Google redirects back with a code. The backend exchanges it for an access token and a refresh token, reads the channel (id, title, picture), and stores the tokens encrypted.
4. The page shows **Connected as \<channel title\>**. If the account has no channel, or the creator refused, it shows the reason and stores nothing.

### Flow: publish a Short
1. On a rendered project, the creator clicks **Publish**.
2. They choose **YouTube** (Instagram shows "coming soon"). If no channel is connected, they connect first and come back.
3. The screen shows:
   - the video;
   - the title (from the Reel's title, ≤ 100 characters);
   - the description and hashtags (AI-written on request, editable);
   - visibility.
4. They click **Publish now**. The worker uploads the MP4 to YouTube (resumable upload), then checks YouTube's processing.
5. The tracker shows Uploading → Processing → Published, then **Your Short is live** with a link. The project becomes **posted**.
6. On failure: a plain-language reason and **Try again**. The render is never lost.

### Edge cases (PRD 11.7, YouTube part)

| # | Case | Handling |
|---|---|---|
| YT-1 | App not yet audited: uploads may be made private | The visibility chosen is sent; after processing the app reads YouTube's actual privacy, and the success screen says "YouTube kept it private until ReelForge is verified" only when that happened (`privateUntilVerified`). |
| YT-2 | Account not a test user while in Testing mode | Google shows its own "access blocked" page and never sends the browser back, so ReelForge can't explain it; the connect screen says beforehand that only test accounts can connect for now |
| YT-3 | Google account has no YouTube channel | `reason=no_channel`: "Create a channel on YouTube first" with a link; nothing stored |
| YT-4 | Access token expired | Refreshed automatically before each call (it lasts 1 hour) |
| YT-5 | Daily quota used up (`quotaExceeded`) | Post fails with "YouTube's daily upload limit is reached. Try again tomorrow." Credits are not involved (publishing is free) |
| YT-6 | Refresh token revoked or expired (`invalid_grant`; Testing-mode tokens expire after 7 days) | Connection marked `reconnect_required`; the publish screen asks to reconnect; the post can be retried after |
| YT-7 | Creator clicks Publish twice | One live post per render and platform; the second request returns the existing post (idempotent) or `409 already_posted` |
| YT-8 | Upload interrupted | The resumable upload continues from where it stopped (up to 3 tries) before the post fails |
| YT-9 | Upload succeeded but the worker crashed before saving | The video id is saved as soon as YouTube returns it; a retry of a post with a video id only re-checks processing |
| YT-10 | YouTube rejects the video in processing (`rejected` / `failed`) | Post fails with YouTube's reason in plain words, and offers to re-render and try again |
| YT-11 | Title or description invalid (over 100 / 5,000 characters, or `<` `>`) | Checked on the publish screen and by the API (`400`) |
| YT-12 | Creator disconnects after posting | Post history is kept; the tokens are deleted, and revoked at Google |
| YT-13 | Render outdated (Reel edited since) | Publishing asks to confirm (`409 render_outdated` unless `confirmOutdated`) |

## API (API design group F, platform-neutral)

`:platform` is `youtube` (and `instagram` from B6b; until then `instagram` answers `404 not_available`).

| # | Endpoint | Notes |
|---|---|---|
| F1 | `GET /connections/:platform/connect?returnTo=` | Verified. `503 provider_unavailable` when the OAuth client isn't configured. `200 {"authorizeUrl"}`. Google's URL with `access_type=offline`, `prompt=consent`, scopes `youtube.upload` and `youtube.readonly`, and a signed one-use `state` (10 minutes) carrying the user and the sanitised `returnTo` (the same rules as before) |
| F2 | `GET /connections/:platform/callback` | Public (Google's redirect). Checks `state` and the signed-in user, exchanges the code, reads the channel, stores the encrypted tokens. Always `302` to `returnTo` with `connected=youtube` or `connection_error=youtube&reason=denied\|no_channel\|missing_permission\|failed`, at the web app's own address (`WEB_ORIGIN`). `missing_permission`: the creator unticked "Manage your YouTube videos"; nothing is stored. A second connect replaces the first and revokes its token. Two ReelForge users may connect the same channel |
| F3 | `DELETE /connections/:platform` | User. `204`. Revokes the token at Google (best effort) and deletes it; posts are kept |
| F4 | `POST /projects/:id/caption/generate` | Verified. `202 {"job"}`; `output` = `{"title", "caption", "hashtags"}`, also saved to the project (`postTitle`, `postCaption`, `hashtags`). 0 credits. Uses the language models |
| F5 | `POST /renders/:id/publish` | Verified, **idempotent**. Body: `platform` (`youtube`), `title` (YouTube, 1–100 characters, no `<` `>`), `caption` (≤ 2,200 characters, no `<` `>` for YouTube), `hashtags` (≤ 30), `visibility` (`private`, `unlisted`, `public`; default `public`), `shareToFeed` (Instagram), `confirmOutdated`. `202 {"post"}`. Errors: `409 not_connected`, `409 reconnect_required`, `409 already_posted` (published already), `409 already_running` (this render is being published, or the Reel is rendering or posting), `409 render_outdated`, `409 not_ready`, `404 not_available` (Instagram before B6b), `400`. Try again after a failure continues the earlier upload, or only re-checks a video YouTube already has, so the channel never gets a second copy (not after `rejected`); new text is then not applied to a video already uploaded. The description ends with the credit lines the licences require (music CC BY, "Voice: elevenlabs.io"), shown in the Post's `credits` |
| F7 | `GET /projects/:id/posts` | User. `{"items": [Post]}`, newest first (50) |
| F6 | `GET /posts/:id` | User. `{"post"}`; polled while publishing |

The user's JSON (A4, A9) gains `connections`: `{"youtube": Connection | null, "instagram": Connection | null}`, where Connection = `{"status": "connected" | "reconnect_required", "name", "pictureUrl", "connectedAt"}`. The older `instagram` field stays until B6b replaces it.

**Post** = `{"id", "renderId", "projectId", "platform", "state": "queued" | "uploading" | "processing" | "published" | "failed", "progress", "title", "caption", "visibility", "credits", "url", "privateUntilVerified", "error": {"code", "message"} | null, "createdAt", "publishedAt"}`. The project's `latestPost` is the newest post.

Error codes for a failed post: `quota_exceeded`, `reconnect_required` (the connection is then marked so), `rejected` (YouTube's processing refused the video), `invalid_metadata` (YouTube refused the title, description or tags), `upload_failed` (including YouTube's short-term rate limit, retried first), `cancelled`, `timed_out`, `internal_error`. A publish stopped after the upload counts as published: the Short is on YouTube. A Short still processing after 10 minutes counts as published too; YouTube finishes it.

## Data (DB design; migration 0002)

- **youtube_accounts** (new): `user_id` (unique, cascade), `channel_id`, `channel_title`, `picture_url`, `access_token_enc`, `refresh_token_enc` (bytea, Fernet), `token_expires_at`, `scopes`, `status` (`connected`, `reconnect_required`), `created_at`, `updated_at`.
- **posts**: adds
  - `platform` (`youtube`, `instagram`; existing rows `instagram`);
  - `youtube_account_id` (set null);
  - `title`;
  - `visibility`;
  - `progress`.

  `media_id` and `permalink` hold the YouTube video id and the Short's link. The one-live-post rule becomes unique `(render_id, platform)` where state is not `failed`.
- **projects**: adds `post_title` (≤ 100 characters).
- Token encryption: Fernet with `TOKEN_ENCRYPTION_KEY` (32 url-safe base64 bytes). Production refuses a missing or development key.

## Backend (technical design)

- **Settings:**
  - `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REDIRECT_URI` (locally `http://localhost:5173/api/v1/connections/youtube/callback`, through the web app's proxy so the sign-in cookie is sent);
  - `TOKEN_ENCRYPTION_KEY`;
  - `PUBLISH_PROVIDERS` (`youtube` or `mock`; tests and offline development use `mock`, production refuses it).
- **The `publish` job:** queue `publish`, about 20 minutes allowed, 0 credits. In order:
  1. refresh the access token if needed;
  2. start a resumable upload with the snippet (title, description = caption plus hashtags, tags = hashtags, category 22, `selfDeclaredMadeForKids=false`) and the status (visibility);
  3. stream the MP4 from storage in 8 MB chunks, saving the progress;
  4. save the video id at once (YT-9);
  5. poll `videos.list?part=status,processingDetails` every 10 s for up to 10 minutes until processed (YT-10).

  Then the post is `published` with `https://youtube.com/shorts/<id>`, and the project becomes `posted`. On failure the post is `failed`, the project `post_failed` (or back to `rendered`), and the render is kept.
- **Calls:** plain HTTPS calls with `requests` (no Google client library), retried on 5xx and network errors with backoff, never on 4xx.
- **No scheduled refresh** is needed: access tokens are refreshed on use. A daily task (04:20, `connections.check`) asks Google for a fresh token for every connection and marks those it refuses as `reconnect_required`.
- **Workers:** publish jobs have their own worker (`publish-worker`, queue `publish`, two at a time), so long uploads never hold up other jobs. Project progress while `posting` is the post's progress.
- Deleting the ReelForge account revokes the YouTube token at Google too.

## Setup the product owner does once (Google Cloud console)

1. Create a project "ReelForge" and enable **YouTube Data API v3**.
2. OAuth consent screen: External, Testing. App name ReelForge; scopes `youtube.upload` and `youtube.readonly`; add your Google account as a test user.
3. Credentials → OAuth client ID → Web application. Authorised redirect URI: `http://localhost:5173/api/v1/connections/youtube/callback` (the web app's address; it passes `/api` to the backend).
4. Put the client id and secret in the backend's `.env` (never in chat if avoidable; never committed).
