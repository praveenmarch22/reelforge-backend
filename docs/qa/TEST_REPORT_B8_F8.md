# ReelForge: test report for B8 (admin API, file clean-up) and F8 (settings, out of credits, admin console)

| | |
|---|---|
| Date | 2026-11-28 |
| Role | Tester (automated tests only; no product code changed) |
| Build tested | `backend/` and `frontend/` working trees, B8 and F8 uncommitted. The product code changed while this round ran (the QA fixes in [FIX_LOG_B8_F8.md](FIX_LOG_B8_F8.md)); every number below is from runs made **after** those fixes, and the tests cover the fixed behaviour |
| Kind of tests | Backend: pytest inside the existing `api` container against the test database `reelforge_test`, the test bucket and the offline mock providers (one pytest at a time; nothing else started in the stack). Frontend: unit and component only (Vitest, Testing Library, jsdom) against the fake `fetch` in `src/test/fakeApi.ts`. No browser tests, no dev server, no second Docker stack |
| Checked against | [API design](../API_DESIGN.md) group I (I1–I14 and "As built (B8)"), A10–A12, H1–H2; [SCREENS_F8.md](../SCREENS_F8.md); [PUBLISHING_YOUTUBE.md](../PUBLISHING_YOUTUBE.md) F1, F3; [QA_REPORT_B8_F8.md](QA_REPORT_B8_F8.md); [FIX_LOG_B8_F8.md](FIX_LOG_B8_F8.md) |

## 1. Results

| Suite | Command | Result |
|---|---|---|
| Backend, B8 files | `docker compose exec -T api python -m pytest tests/test_admin_b8.py tests/test_admin.py` | **210 tests: 203 passed, 7 failed.** The 7 failures are new tests that find BUG-086 to BUG-089. The 7 starter tests pass |
| Backend, everything | `docker compose exec -T api python -m pytest` | **1917 tests: 1910 passed, 7 failed** (11 min 25 s). The 7 failures are the same new tests (BUG-086 to BUG-089). Every older test passes, two of them after being brought up to date (section 2) |
| Backend lint (test file only) | `ruff check tests/test_admin_b8.py`, `ruff format --check` | Pass (the E501 lines are fixed) |
| Frontend typecheck | `npm run typecheck` (app and `e2e/`) | Pass |
| Frontend unit and component (all) | `npx vitest run` | **890 tests: 889 passed, 1 failed** (24 files), the same on two full runs. The failure is a new test that finds BUG-090 |
| Frontend F8 files alone | `npx vitest run src/features/settings src/features/admin src/components/credits src/api/client.f8.test.ts src/mocks/handlers.f8.test.ts` | 161 tests: 160 passed, 1 failed (BUG-090), the same on three runs |

Not run, as instructed: Playwright (`npm run test:e2e`), so the settings tests in `e2e/shell.spec.ts` were typechecked only.

## 2. What was added

| File | Tests | What |
|---|---|---|
| `backend/api/tests/test_admin_b8.py` | 203 (with `parametrize`) | Every admin endpoint I1–I14, the clean-up of replaced files, the purge, the beat schedule |
| `frontend/src/features/settings/SettingsPage.test.tsx` | 51 | Screen 14: the tabs, Profile, Defaults, YouTube, Credits, Security |
| `frontend/src/features/admin/AdminPage.test.tsx` | 69 | Screen 16: access, Overview, Users, Jobs, Providers, Usage, Music |
| `frontend/src/components/credits/OutOfCredits.test.tsx` | 11 | Screen 15: the out-of-credits dialog |
| `frontend/src/api/client.f8.test.ts` | 12 | The client: a `401 invalid_credentials` is not a lost session; the 402 handler |
| `frontend/src/mocks/handlers.f8.test.ts` | 18 | The new mock endpoints: H2, A11, A12, F3, and the new costs in H1 |

No product file, MSW handler or config was changed. The starter file `tests/test_admin.py` and the existing `handlers.test.ts` were left as they are.

Two older backend tests pinned behaviour that B8 changed on purpose, and failed in the full run until they were brought up to date (no check was removed):

| Test | Was | Now |
|---|---|---|
| `tests/test_worker_and_config.py::test_the_background_tasks_and_their_schedule_are_registered` | The exact beat schedule, five tasks | The same, with `files.clean_replaced` on `maintenance` |
| `tests/test_jobs_and_credits.py::test_finished_jobs_are_counted_for_the_admin_usage_page` | A failed script counted under the provider `none` (`mock`/`script` calls 1) | Counted against the provider that failed (QB8-004): `mock`/`script` has calls 2, failures 1, and is charged once; no other rows |

### Covered: backend

**Admins only (all 14 endpoints)**
- Each endpoint answers `401 not_authenticated` signed out and `403 forbidden` to a signed-in creator, and a refused creator changes nothing (no switch, no credits, no admin role, no disabled user, no track).
- An admin role that is removed stops working on the next request. Admin changes need the CSRF header.

**I1, I2 users**
- Newest first with the documented fields. Cursor paging over 5 users in pages of 2, with no repeats or gaps. A bad `limit` (0, 101, text, negative) or `cursor` is a 400 naming the field.
- Search by email or name in any letter case, trimmed. `_` and `%` are plain characters (QB8-007). Deleted accounts are not listed, opened, changed or credited (404).
- The detail has the niche, the reset date, the YouTube connection and at most 20 recent jobs, newest first. An unknown or malformed id is 404 on I2, I3 and I4.

**I3 update a user**
- Validation (allowance below 0, above 100 000, text, a fraction; text or a number for the three flags; an unknown field).
- Tester, admin and allowance are saved; the balance doesn't change. Making someone a tester sets the tester allowance, and back; an allowance sent with it wins; sending the same tester value again keeps a hand-set allowance (QB8-010).
- Self-protection: `409 own_account` for disabling or demoting yourself, alone or mixed with other fields, and nothing is saved. Everything else about your own account can be changed.
- Disabling ends every session (two browsers: `/me`, refresh and a new project are refused; no live refresh token is left; a new login is refused). Enabling again allows a new login but does not revive the old sessions. Disabling twice keeps the first date.

**I4 adjust credits**
- Up and down, down to exactly 0. Each writes an `adjust` ledger row with the trimmed note and the admin's id; the balance stays the sum of the ledger; the creator sees the rows in H2.
- Below zero is `409 balance_negative` and writes nothing. Validation: amount 0, missing, text, a fraction, a boolean, beyond ±100 000; note missing, empty, spaces, over 200 characters, two lines. 200 characters are accepted.

**I5 jobs**
- Filters by state, type and provider, alone and combined; the fields plus `userEmail` and `attempts`; the error and the refund flag on a failed job. An unknown filter value answers an empty list, not a 500. Cursor paging with jobs that share a moment; a bad limit is a 400.

**I6 retry (after QB8-005, QB8-006)**
- A failed voice job: `202`, a new queued job for the same scene with cost 0, the same input with `previousStatus: "failed"`, handed to the worker; the scene is `queued`; the old job stays failed and carries `output.retriedAs`. Run, it succeeds, the scene is `ready`, and the balance and ledger are unchanged. The same for a failed visual job.
- Refused with `409 not_failed`: queued, running, succeeded, cancelled. Refused with `409 not_retryable`: every other job type (script, rewrite, render, publish, transcribe, music, AI video, keyframe, ideas, hooks, caption); a deleted Reel; a deleted scene; a scene whose voice is no longer `failed` (five statuses); an edited narration; hand-fixed caption words; a Reel that is scripting, rendering or posting; a disabled owner (allowed again once enabled); a deleted owner; a job already retried. If the retry fails too, the new job can be retried and the old one still cannot.
- A voice the creator has remade since the failure is left alone. An unknown or malformed job id is 404.

**I7 usage, I8 and I9 providers**
- The default range is the last 30 days, newest first, then by provider and job type; both ends of a given range are included; only `to` gives the 30 days before it; a year is allowed. Bad dates, a reversed range and more than a year are 400.
- A finished job is counted. A job that every provider failed is counted against each of them, carries the last one tried, shows as a 100 % error rate in I8 and is found by the provider filter of I5 (QB8-004).
- I8 lists the seven providers with today's calls and error rate (yesterday's failures are not today's; no calls gives `null`).
- I9: the reason is required when switching off (missing, empty, spaces, too long); `enabled` must be a boolean; who and why are stored; off twice keeps one row; on again clears the reason; an unknown name is 404.
- A switched-off provider is really skipped: with `elevenlabs` off through I9 a voice is made by `edge` and ElevenLabs is never called; with both off a voice is refused with 503 and costs nothing; on again, work is accepted. With `youtube` off, F5 answers `503 provider_unavailable`, nothing is queued, posted or charged, and publishing works again once it is on (QB8-003).

**I10–I13 music**
- A new track is inactive, with normalised moods and a key that follows the content type, and is not offered to creators. The upload link refuses another content type and takes the declared one; then I12 switches it on and D4 offers it with a playable link.
- I11 validation (20 cases) and each required field. The largest allowed size and length are accepted.
- I12: `409 file_missing` before the upload, also when sent with other edits (nothing of that request is kept); `409 file_too_large` for an empty or over-30 MB file (QB8-009); title, moods, licence and active are saved; validation; an unknown id is 404.
- I13: an unused track loses its row and its file; a track a Reel uses is only deactivated (the file and the Reel's choice stay), and can be deleted once nothing uses it.

**I14 stats**: the four numbers, with deleted Reels and older failed jobs left out.

**Clean-up (`cleanup.clean_replaced`)**
- Only assets replaced more than 7 days ago go (7 days 1 minute goes; 6 days 23 hours stays; an old asset that was never replaced stays), file and row.
- An asset still used by a scene's voice, visual or keyframe, or a project's cover or music, is kept and un-flagged, and is not looked at again. A render's video and cover are kept and the render still plays.
- More than one batch (batch size 3, 8 unused and 2 used). A file storage refuses to delete keeps its row and goes on the next run (QB8-002).

**The purge (`accounts.purge_deleted`)**
- Files of accounts and Reels deleted more than 7 days ago are deleted, those deleted more recently and everyone else's stay. The files are deleted while the rows still exist.
- With storage down, the rows stay until a later run; when one file of one account fails, that account waits and the others are purged (QB8-002).
- A duplicate of a purged Reel keeps its shared voices and visuals, which now belong to it and still download (QB8-001). A deleted account's Reels go with it, deleted or not. With nothing to purge, storage is not touched.

**The beat schedule**: `files.clean_replaced` is scheduled once a day on the `maintenance` queue, the task is registered and runs the clean-up, and every scheduled task exists.

### Covered: frontend

**Settings tabs**: the five tabs; `?tab=` opens each directly; `?tab=instagram` opens YouTube; an unknown tab opens Profile; choosing a tab puts it in the address; arrow keys, Home and End move and choose (one Tab stop, QF8-002); the connect result dialog (`?connected=youtube`, `?connection_error=…`) shows once and the tab is kept.

**Profile**: the name, the email as text, the saved topics; the saved value is listed topics first, then the free text; three topics at most (the rest lock); the free text is one topic (commas become spaces, 40 characters); an empty value clears it; a name is required; the server's reason is shown; "Saved." goes away on the next edit.

**Defaults**: the saved narrator, caption style and visual style; each choice is saved at once as `defaults.<field>` only; a failed save is said and the saved choice stays.

**YouTube**: Connect asks F1 with `returnTo=/app/settings?tab=youtube` and leaves for Google with the button busy; connected shows the channel and "since"; Change channel; Disconnect with a confirm (confirmed: F3, then Connect again; not confirmed: nothing sent; failed: the reason, channel kept); the reconnect state; a failed connect start; Instagram "Coming soon".

**Credits**: the meter (`aria-valuenow`, max, the bar's width, never over 100 %); the renewal date; no buying; costs from H1 only (free, singular, plural, an unknown key, a missing key); the history with signed amounts and the kind when there is no note; Load more with the encoded cursor; empty; errors.

**Security**: change password (success empties the form; a wrong current password shows at that field, the page stays, no refresh and **no sign-out**; mismatch; under 8 characters; all three fields needed; the server's objection at the new-password field; 429 as a notice; a real `401 not_authenticated` still signs out). Delete account (the 7-day text; a confirm; `DELETE /me` with the password, then `/login?deleted=1`; not confirmed; a wrong password at the field with no sign-out; another failure).

**Out of credits**: opens on a 402 from any endpoint and page, and from the app shell; needed, balance and the renewal date; singular; the title with nothing left; **See my credits** opens `/app/settings?tab=credits`; Close; reopens with new numbers; no details; not for other errors; harmless after the page has gone.

**Client**: `401 invalid_credentials` on A11 and A12 does not sign out or refresh, also after a token refresh; other 401s do; the 402 handler gets the details, only for `402 insufficient_credits`.

**Admin**: a non-admin is sent to `/app` and no admin endpoint is called; the six tabs, the address, the arrow keys; Overview numbers and error.
- Users: rows and badges; the search waits for a pause in typing (one request for three keystrokes, trimmed); no match; error; Load more; the detail panel; Tester saves at once; Admin and Disabled ask to confirm, naming the user (cancel sends nothing); own account locked; a refusal is shown; the allowance (500, 0; text, negative and fractions are not sent); adjust credits (the body, the new balance, the emptied form, the typographic minus, six invalid inputs, the server's refusal); a fresh form for another user.
- Jobs: starts on failed; the state, type and provider filters narrow the request (QF8-001); Retry only for failed voice and visual jobs; a retry shows "Retried" and cannot be pressed twice (QF8-007); `output.retriedAs` from the API shows "Retried"; a refusal is shown; Load more; no match.
- Providers: purpose, calls, error rate, the reason when off; switching off prompts for the reason (cancelled, empty and spaces send nothing); switching on needs none; a refusal is shown.
- Usage: the six columns and rows as the API ordered them; empty; error.
- Music: the list; switch off and on; `file_missing`; Delete with its confirm (cancel sends nothing); a track in use stays, switched off; Add track with `Audio` and `fetch` stubbed (I11 body with the file's name, type, size and rounded length, the PUT with the content type and no CSRF header, then I12, in that order; the form and the file box are emptied); each missing field; unreadable audio; over 30 MB; a failed upload (no I12, the form kept); a server validation error.

**Mock handlers**: 401 signed out on all four; H2's shape and the grant; H1's costs; A11 (success, wrong current password with the session kept, short or missing new password); A12 (success, the address is free at once, wrong or missing password); F3 (disconnect, nothing connected, Instagram `404 not_available`).

### Not covered yet

- **Real browsers.** No Playwright ran: focus inside the native `<dialog>`, the layout, a real audio file's length, a real upload to storage from the browser, `window.prompt` and `window.confirm` as the user sees them.
- **The audit log line** of admin changes (PRD 5.11): its content is not asserted.
- **Rate limits** on the admin endpoints and on A11 and A12 from the settings page (the limits themselves have backend tests from B2).
- **Concurrency**: two admins adjusting the same balance at once, a retry racing the creator's own regenerate, the clean-up running while a render starts.
- **I7's cost estimate** (`costEstimateUsd` is always what the table holds; nothing writes it yet).
- **QB8-008** (the voice credit line of an old render after the clean-up): known and not fixed, so no test was added.
- **Real provider names for stock, images and language models through I9**: the switch is checked through the admin API for voice and YouTube; the others have their own older tests that set the switch in the database.
- **The purge with thousands of files**, and S3 errors other than a failed delete.

## 3. Bugs

| Bug | Severity | Area |
|---|---|---|
| BUG-086 | Medium | The clean-up and the purge leave a visual's small still (`-thumb.jpg`) in storage for ever |
| BUG-087 | Medium | A publish already queued still uploads after `youtube` is switched off |
| BUG-088 | Low | I14 counts the Reels and credits of deleted accounts |
| BUG-089 | Low | I7 reports a bad `to` date under `from` |
| BUG-090 | Low | The Credits tab says "Nothing yet." under the error when the history could not be loaded |
| BUG-091 | Low (docs) | API design "As built (B8)" still describes I6 and the admin API as they were before the QA fixes |

### BUG-086 Medium: the small stills of visuals are never deleted

- **Tests:** `backend/api/tests/test_admin_b8.py`, `test_clean_up_removes_a_replaced_visuals_thumbnail_too` and `test_the_purge_leaves_nothing_of_a_finished_reel_in_storage`.
- **Spec:** PRD F-2 "Delete replaced assets after 7 days; delete everything when a project is deleted"; API design "As built (B8)": "deleted, file and row".
- **Steps (clean-up):**
  1. Make an AI image for a scene (`POST /scenes/:id/visual/regenerate` with `visualSource: "ai_image"`), then make another. The first take is now replaced.
  2. Move its `replaced_at` back 8 days and run `cleanup.clean_replaced()`.
- **Expected:** only the current take's two files are left under `users/<id>/projects/<id>/visual/`.
- **Actual:** the replaced take's `<job>.jpg` is gone, but its `<job>-thumb.jpg` is still there, and no row lists it any more.
- **Steps (purge):** render a Reel that has an AI image, delete the account, move `deleted_at` back 8 days, run the purge. **Expected:** nothing under `users/<id>/`. **Actual:** the `-thumb.jpg` files remain while the user, project and asset rows are gone.
- **Cause:** a visual is two files. `visuals._store_clip` and `visuals._make_image` store the still under the key kept in `Asset.meta["thumbnailKey"]`. `cleanup.clean_replaced` and `cleanup.purgeable` delete `Asset.s3_key` only. After the row is deleted nothing knows the still's key, so no later run can remove it. Stock clips have a still too when the provider's thumbnail could be fetched.

### BUG-087 Medium: a queued publish ignores the YouTube switch

- **Test:** `test_admin_b8.py`, `test_a_publish_already_waiting_when_youtube_is_switched_off_does_not_upload`.
- **Spec:** PRD AD2 (P0) and G-3: the kill switch is for a quota that has run out; I9: "Switched off, jobs skip it".
- **Steps:**
  1. A creator with a connected channel publishes a render (F5 answers 202; the job waits in the queue).
  2. An admin switches `youtube` off with a reason (I9).
  3. The worker takes the job.
- **Expected:** nothing is uploaded while the switch is off (the post fails with a clear reason, or waits).
- **Actual:** the video is uploaded to YouTube.
- **Cause:** `connections.platform_enabled` is only called in the F5 route (`publishing.py:92`). The publish job does not look at the switch when it runs. Voice, visual and language-model jobs read the switches at run time (`enabled_voice_names()` and the like), so they stop at once; publishing does not. YouTube uploads are the expensive quota (1600 units each), so the posts already queued are exactly what the switch should stop.

### BUG-088 Low: the Overview counts deleted accounts' Reels and credits

- **Tests:** `test_admin_b8.py`, `test_stats_do_not_count_the_reels_of_a_deleted_account` and `test_stats_do_not_count_the_credits_of_a_deleted_account`.
- **Steps:** a creator deletes their account (A12): in one test they have one Reel, in the other 100 untouched credits. An admin with 100 credits and no Reel opens `GET /admin/stats`.
- **Expected:** `users: 1`, `projects: 0`, `creditsInCirculation: 100` (the tile reads "Credits held by users").
- **Actual:** `users: 1`, but `projects: 1` in the first test and `creditsInCirculation: 200` in the second, until the purge 7 days later.
- **Cause:** `admin_stats()` leaves deleted users out of `users`, but counts every project whose own `deleted_at` is null (A12 does not mark the account's projects deleted) and sums every `credit_accounts.balance`. I1 already reports a project count per live user, so the tiles and the list disagree.

### BUG-089 Low: I7 names the wrong field for a bad `to`

- **Test:** `test_admin_b8.py`, `test_usage_refuses_a_bad_date_range[to=03/01/2026-to]` and `[to=2026-02-30-to]`.
- **Steps:** `GET /admin/usage?to=2026-02-30`.
- **Expected:** `400 validation_error` with `details.fields.to`.
- **Actual:** `400 validation_error` with `details.fields.from: "Use YYYY-MM-DD"`, although `from` was not sent.
- **Cause:** `usage()` parses both dates in one `try` and always answers `{"fields": {"from": …}}`.

### BUG-090 Low: "Nothing yet." under a history that failed to load

- **Test:** `frontend/src/features/settings/SettingsPage.test.tsx`, "settings: credits > when the history can’t be loaded the balance still shows, with the reason".
- **Steps:** open `/app/settings?tab=credits` while `GET /credits/ledger` answers an error (H1 works).
- **Expected:** the balance and costs, and the error under History.
- **Actual:** the error, and right under it "Nothing yet.", which tells the creator their history is empty when it only could not be read.
- **Cause:** in `CreditsTab` the empty text shows whenever there are no entries and the query is not pending: `entries.length ? … : ledger.isPending ? null : <p>Nothing yet.</p>`. A failed query is not pending.

### BUG-091 Low (docs): API design is behind the QA fixes

- **No failing test** (documents are not tested).
- "As built (B8)" still says I6 "only applies to … (`ideas`, `hooks`, `caption`, `voice`, `visual`)"; the code and the admin page now allow only `voice` and `visual`, and the new refusals (scene no longer failed, edited narration, hand-fixed words, busy Reel, disabled or deleted owner, already retried), `output.retriedAs`, `409 file_too_large` on I12, the tester allowance on I3, the `youtube` switch on F5 (`503 provider_unavailable`) and the per-provider failure counts are not written down.
- The heading still reads "I. Admin (13)" and the totals "70 endpoints … 67 on the Flask API", with I14 added; the screens table says "Admin: I1–I13".

### Other observations (not bugs)

- The Tester toggle in the admin panel saves without a confirm; Admin and Disabled ask. SCREENS_F8 3 reads "toggles for tester, admin and disabled (with a confirm)", which can be read either way.
- A monthly allowance that is not a whole number of 0 or more ("abc", "-5", "10.5") is silently not sent; no message appears (QF8-011 lists this as polish).
- With a balance above the allowance (after a top-up by an admin) the meter says `aria-valuenow="250"` with `aria-valuemax="100"`. The bar itself stops at 100 %.
- The voice job's comment says a file it could not delete is "left for the clean-up of unused files (B8)". The clean-up only knows files that have an asset row, so such a file stays.
- The whole backend suite shares one test database. A second pytest started in the same container while the first runs makes dozens of unrelated tests fail (see section 4).

## 4. Flaky or skipped

- None skipped, no `it.todo`, no `xfail`.
- The frontend suite was run in full twice and the F8 files three times: the same single failure every time. The B8 backend files were run four times after the fixes (twice on their own, twice inside the full suite): the same 7 failures every time.
- **One discarded backend run.** The first full backend run overlapped with the QA fixes being made and, it seems, with another pytest using the same test database: 45 failures and 4 errors, 38 of them in the account, session and rate-limit tests ("unhandled error", wiped tables). That run is discarded. The two full runs made after the fixes had settled, with nothing else running, were clean apart from the failures listed: the first showed the 7 new failures plus the two out-of-date older tests of section 2, the second (after updating those two) only the 7.
