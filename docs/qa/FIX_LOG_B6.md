# B6 publishing to YouTube: fix log

Fixes for [QA_REPORT_B6.md](QA_REPORT_B6.md) and [TEST_REPORT_B6.md](TEST_REPORT_B6.md).

## Real end-to-end check (2026-11-26)

- **Connect:** a real channel was connected through Google OAuth (Testing mode, the product owner as test user).
- **Caption (F4):** Gemini wrote the title, caption and hashtags.
- **Publish:** the octopus Reel's 720p render was uploaded and processed. The Short went up on the product owner's channel, and the project went to `posted`.
- **Privacy:** YouTube reported it **public**, not locked private.
- **Credits:** that Short went out before the credit lines were added (QB6-001). The product owner was asked to add them in YouTube Studio by hand.

## QA round 1

| Finding | Fix |
|---|---|
| QB6-001 Major: credit lines missing | The description now ends with the licence credits: the track's CC BY line ("Music: …"), and "Voice: elevenlabs.io" when any voice came from ElevenLabs. They are counted in the 5,000-byte check and shown in the Post's `credits` |
| QB6-002 Major: "Try again" uploads from scratch | A new F5 for the same render, platform and account continues the latest failed post's upload session, or only re-checks the video YouTube already has, unless YouTube rejected it. A retry never leaves a second copy |
| QB6-003 Major: granted permissions not checked | A grant without `youtube.upload` gives `reason=missing_permission`; the token is revoked and nothing is stored |
| QB6-004: the soft time limit is swallowed during the upload | It is re-raised before the generic "dropped connection" branch |
| QB6-005: error mapping | Mapping corrected: `invalid_grant`/401 → `reconnect_required`, which now also marks the connection; `invalid_client` (our setting) → `upload_failed`; short-term rate limits → retryable `upload_failed`; invalid title, description or tags → a new `invalid_metadata` ("change them and try again") |
| QB6-006: relative F2 redirect | The redirect goes to `WEB_ORIGIN` + path |
| QB6-007: tokens not revoked on account deletion or reconnect | Both now revoke at Google (best effort) |
| QB6-008: no daily connection check | `connections.check` runs daily at 04:20 and marks refused connections `reconnect_required` |
| QB6-009: posting progress always 0 | The project's progress while posting is the post's progress |
| QB6-010: publish jobs use the shared worker | New `publish-worker` (queue `publish`, concurrency 2); the main worker no longer takes `publish` |
| QB6-011: cancel after upload marks it failed; cancel restores the wrong status | A cancel after the video id is saved counts as published (the Short is live). Otherwise the previous status is restored unless it was a busy one |
| QB6-012: undocumented 409/503 and posts list | Documented: F5 `409 already_running`, F1 `503`, `GET /projects/:id/posts`, the retry behaviour and the error codes |
| QB6-013: test gaps | The Tester's B6 suite covers them |
| QB6-014: docs and Compose out of date | PROJECT_STATUS, the technical design and a code comment updated; `YOUTUBE_REDIRECT_URI` and `TOKEN_ENCRYPTION_KEY` passed through Compose; `.env.example` lists the new settings |
| QB6-015: downgrade disguises YouTube posts | The downgrade deletes YouTube posts first |
| QB6-016: stray wheel folder and formatting churn | The folder (left by a `pip download` whose path Git Bash rewrote) is deleted; `0001_baseline.py`, `env.py` and `constraints.txt` restored, with only the `cryptography` pin added |
| Q1: `not_tester` | Removed: Google shows its own "access blocked" page and never redirects. The connect screen (F7) explains beforehand |
| Q2: processing after 10 minutes | Counts as published; YouTube finishes it (documented) |
| Q3: two users, one channel | Allowed (documented) |
| Q4: empty title | The API requires a title; the publish screen (F7) fills it from the Reel's title |

## Tester round 1

| Bug | Fix |
|---|---|
| BUG-073 Major: a wrong OAuth client secret marks connections `reconnect_required` | `invalid_client` / `unauthorized_client` are checked before the 401 check, because Google answers 401 for them |
| BUG-074 Major: Try again after connecting another channel reuses the old channel's video | Each publish job records the channel id. A retry continues an earlier upload only on the same channel |
| BUG-075: a video YouTube no longer returns ends `published` | The post now fails as `rejected` |
| BUG-076: a single 5xx outside the upload pieces fails the post | Every single call (token, channel, start of upload, processing check) is tried 3 times on a 5xx, a rate limit or a dropped connection |
| BUG-077: an encoded `?` in `returnTo` adds our own result parameters | The decoded path is encoded again, so `?` and `#` stay part of the path |
| BUG-078: F2 without a `code` goes to Settings | Now back to the `returnTo` page with `reason=failed` |
| BUG-079: F4 can save text F5 refuses | `<` and `>` are removed before the checks, including in hashtags |

Test updates: the Tester's callback test expected one 5xx or dropped connection to fail the connect. With BUG-076's retries, those answers now repeat on every try in the test.

Observations: `docs/schema.sql` stays the baseline schema (CI compares it with migration 0001). Changes since are in `api/migrations/versions/0002_youtube_publishing.py` and described in [PUBLISHING_YOUTUBE.md](../PUBLISHING_YOUTUBE.md). The stray wheel folder was already deleted.
