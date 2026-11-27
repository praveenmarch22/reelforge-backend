# ReelForge: QA report for B8 (admin API, file clean-up) and F8 (settings, out-of-credits dialog, admin console)

| | |
|---|---|
| Date | 2026-11-28 |
| Milestones | B8: admin API I1–I14, daily clean-up of replaced files, purge deleting files. F8: `/app/settings` (five tabs), the out-of-credits dialog, `/admin` |
| Spec | [API_DESIGN.md](../API_DESIGN.md) group I with the "As built (B8)" notes, A10–A12, H1–H2, error codes; [SCREENS_F8.md](../SCREENS_F8.md); [PRD.md](../PRD.md) 5.1, 5.10, 5.11, flow 7, A-5, G-3, F-2, CA-4; [UI_REVIEW.md](../UI_REVIEW.md); [PUBLISHING_YOUTUBE.md](../PUBLISHING_YOUTUBE.md) F1, F3, F5; [DB_DESIGN.md](../DB_DESIGN.md) users and retention tables |
| Build reviewed | `backend/` and `frontend/` working trees, both uncommitted. Backend: `routes/admin.py`, `cleanup.py`, `tasks/accounts.py`, `tasks/jobs.py`, the beat schedule in `__init__.py`, `tests/test_admin.py`, plus the code they call (`jobs.py`, `credits.py`, `generation.py`, `job_types.py`, `visuals.py`, `publishing.py`, `projects.py`, `storage.py`, `auth.py`, `log.py`). Frontend: `api/account.ts`, `api/admin.ts`, `api/client.ts`, `SettingsPage.tsx`, `AdminPage.tsx`, `OutOfCredits.tsx`, `AppShell.tsx`, `App.tsx`, `mocks/handlers.ts`, `e2e/shell.spec.ts` |
| Checks run | `npm run typecheck` in `frontend/`: clean. Nothing else was run |
| Browser, backend tests | **Not run.** The review was told not to touch the running servers or start stacks, and the machine is low on memory and disk. **Every finding and every pass below is *Read*** (concluded from the code), with the deciding lines cited. None is confirmed by execution |
| Note | `api/tests/test_admin_b8.py` (1,455 lines) appeared in the backend tree while this review was under way. It was not there at the start, is outside the stated scope, and was not run or reviewed. Several of its test names expect behaviour that the product code does not have at the time of writing (see QB8-002, QB8-003, QB8-006, QB8-009), so expect those tests to fail until the code changes |

## Verdict

- **B8: fail.** One Blocker (the purge deletes files a duplicated Reel still uses) and four Major findings.
- **F8: pass with issues.** No Blocker or Major in the frontend alone; the admin console inherits the backend findings (the YouTube switch and the error rate it displays).

What is sound: every admin route carries `require_user(admin=True)`; bodies reject unknown fields; the credit adjustment locks the account and writes the ledger in the same transaction; the retry cannot charge and respects the project → scene lock order; the I9 raw SQL is parameterised; a non-admin opening `/admin` is redirected before any admin request is made; the `invalid_credentials` exception in `client.ts` cannot hide a real sign-out.

| Severity | Backend | Frontend |
|---|---|---|
| Blocker | 1 (QB8-001) | 0 |
| Major | 4 (QB8-002 to QB8-005) | 0 |
| Minor | 6 (QB8-006 to QB8-011) | 8 (QF8-001 to QF8-008) |
| Polish | 1 (QB8-012) | 4 (QF8-009 to QF8-012) |
| Questions | 7 (section 4) | |

---

## 1. Backend findings

### QB8-001 Blocker: the purge deletes the files of a deleted Reel even when a duplicate of it still uses them

| | |
|---|---|
| Where | `api/reelforge_api/cleanup.py:90-103` (`files_of` selects by `Asset.project_id` only); `tasks/accounts.py:26-30`; `routes/projects.py:356-357` and `projects.py:166-189` (`copy_scene` copies `voice_asset_id`, `visual_asset_id`, `keyframe_asset_id`: the copy's scenes point at the **source project's** asset rows); `routes/projects.py:345` (`music_asset_id` is copied the same way); `models.py:186` (`assets.project_id … ON DELETE CASCADE`), `models.py:214-216` (`ON DELETE SET NULL`) |
| Requirement | API B7 "reuses the generated files"; PRD F-2 "delete everything when a project is deleted" (of that project, not of others); API "As built (B8)": the clean-up keeps what a scene, render or project uses. Data loss |
| Verified | Read |

**Steps.** Generate a Reel. Duplicate it (B7). Delete the original. Wait for the purge 7 days later (03:10).

**Expected.** The copy keeps its voices and visuals.

**Actual (from the code).** `delete_files_of(project_ids=[original])` deletes every stored file whose asset row belongs to the original, including those the copy's scenes point at. `clean_replaced` guards against this with `_in_use`; the purge has no such check. The asset rows then go with the project (cascade) and the copy's scene columns become NULL while `voice_status`/`visual_status` still say `ready`. The row half of this predates B8 (the purge has deleted project rows since B6); B8 adds the file deletion, which makes it unrecoverable. The same applies to an uploaded music file chosen for another project (`_choose_music` checks the owner, not the project, `routes/projects.py:296-309`).

**Fix.** Before purging a project, find its assets still referenced by a scene, render or project **outside the set being purged** (the `_in_use` query, restricted to surviving projects), move those rows to a surviving project (`UPDATE assets SET project_id = …`) and leave their files. Delete files only for the rest. Add a test: duplicate, delete the source, backdate `deleted_at`, run the purge, then assert the copy's scenes still have their asset ids and `storage.exists` is true. Account purges are not affected (everything belongs to the one user).

### QB8-002 Major: when storage refuses a delete, the rows are deleted anyway and the files stay for ever

| | |
|---|---|
| Where | `cleanup.py:28-37` (`_delete_files` swallows each failure; the comment says "tried again on the next run"); `cleanup.py:78-81` (rows of all `unused` deleted regardless); `tasks/accounts.py:28-31` (projects and users deleted regardless) |
| Requirement | PRD A5 "Delete account and all data", A-5 "delete files and tokens", F-2; DB design retention table ("purged with files") |
| Verified | Read |

**Actual.** The asset rows are the only list of what is stored. If MinIO/S3 is unreachable or returns an error for some keys at 03:10 or 03:50, the warning is logged, the rows are deleted, and nothing can ever find those files again. For a deleted account that is the user's voice, uploads and videos kept indefinitely after the product said they were removed.

Related: two comments promise a clean-up of files that have no row (`generation.py:197` "left for the clean-up of unused files (B8)", `routes/admin.py:509` "the file is left for the clean-up"). No such clean-up exists; B8 only works from asset rows.

**Fix.** `_delete_files` returns the keys that failed. `clean_replaced` deletes only the rows whose file went. The purge skips (leaves soft-deleted) any user or project with a failed key and retries the next night. For accounts, deleting by the prefix `users/<id>/` as well would also catch files without rows. Also delete by the ids selected, not by re-evaluating `deleted_at < cutoff` (`tasks/accounts.py:29-30`), so the files deleted and the rows deleted are always the same set.

### QB8-003 Major: the "youtube" kill switch does nothing

| | |
|---|---|
| Where | `routes/admin.py:47` (`"youtube": "publishing"` in `PROVIDERS`); no reader: `ProviderSwitch` is only consulted in `generation.py:45`, `job_types.py:52`, `visuals.py:74` (voice, language model, stock, images). `publishing.py` and `connections.py` never look at it |
| Requirement | PRD AD2 (P0) "Kill switch per provider"; API I9; the console's own text "Switched off, jobs skip that provider" (`AdminPage.tsx:378`) |
| Verified | Read (grep of `ProviderSwitch` across `reelforge_api`) |

**Actual.** Switching YouTube off answers 200, shows "Off" with the reason, and publishing carries on (F5 accepts, the publish job uploads). An admin who switches it off because the daily quota is burning believes it has stopped.

**Fix.** Check the switch in `start_publish` (refuse with `503 provider_unavailable`) and at the start of `PublishJob.work`, and in F1 if connecting should stop too. Or remove `youtube` from `PROVIDERS` until it is wired.

### QB8-004 Major: I8's error rate is always 0%, and failed jobs cannot be found by provider

| | |
|---|---|
| Where | `jobs.py:317` (`record_usage(job.type, None, failed=True, …)`) and `jobs.py:397` (`provider or "none"`): every failure is counted under provider `none`. `routes/admin.py:328-348` looks failures up by provider name. `jobs.py:277` sets `job.provider` only on success, so `routes/admin.py:228-230` `?provider=` never matches a failed job |
| Requirement | API I8 "error rate" (today's, per the B8 note); I5 `?state=failed&…&provider=`; PRD AD1 "failures … per provider", G-3 |
| Verified | Read |

**Actual.** For each named provider `failures` is always 0, so `errorRateToday` is `0.0` whenever it had a call and `null` otherwise. A provider that fails every call and is rescued by its fallback also shows 0% (`providers/base.py:with_fallback` records nothing per provider). The admin cannot see which provider to switch off, which is the point of the screen. The Usage tab shows the failures in a row named `none`.

**Fix.** Record a failed call against the provider that failed (inside `with_fallback`, or return the list of providers tried and record each), and store the last provider tried on a failed job. Until then, show failures per job type rather than a per-provider rate.

### QB8-005 Major: I6 retry skips the creator-side guards and can overwrite the creator's work

| | |
|---|---|
| Where | `routes/admin.py:243-279`; effects in `generation.py:172-176` (voice apply: replaces the voice, sets `words`, `words_edited = False`), `job_types.py:413-417` (caption apply: overwrites `post_title`, `post_caption`, `hashtags`) |
| Requirement | PRD CA-4 and API `409 words_edited` (C7 path: `routes/scenes.py:238-239`); API `already_running` while scripting (`refuse_while_scripting`, `routes/scenes.py:237`); API I6 "re-enqueue a failed job" |
| Verified | Read |

The retry is safe on money and locks (see "What passed"). It is not safe on content, because it re-runs old input with none of the checks the creator's own retry goes through:

1. **Hand-fixed caption words are replaced without the confirmation.** A voice failed; the creator's later take succeeded; they fixed caption words by hand. If the narration and mood are unchanged, the admin retry passes the `scene_changed` check (`generation.py:156`), replaces the voice and resets `words_edited`. C7 would have asked for `confirmReplaceWords`.
2. **A good, newer take is replaced.** The route does not look at the scene's current status: it sets `voice_status`/`visual_status` to `queued` even when it is `ready` (`admin.py:273-274`). It also reuses the old `previousStatus` from `old.input` (`admin.py:271`), so a cancel of the retry puts the scene back to a status from before the failure (for example `missing` on a scene that has a voice).
3. **A caption retry overwrites the post text.** `Caption.apply` has no "unchanged since" check, so retrying a days-old failed `caption` job replaces the title, description and hashtags the creator has typed since, including on a posted Reel.
4. **The old job stays `failed`**, so the same job can be retried again after each run finishes; each run is free. Only a concurrent second retry is refused (`jobs_one_active`).
5. No `refuse_while_scripting`, and the provider-availability check that answers 503 up front is skipped (the job fails later instead).

**Fix.** For `voice`/`visual`: retry only when the scene's status for that part is still `failed` and `scene.last_error` came from this job (or simply: the old job is the newest job of that type for the scene); refuse when `words_edited`; write a fresh `previousStatus`. For `caption`: only when the project's post text is unchanged since (or drop `caption` from `RETRYABLE`). Mark the old job as retried (for example `output = {"retriedAs": id}`) and refuse a second retry. Add tests for 1 to 4.

### QB8-006 Minor: I6 runs work for disabled and deleted accounts

| | |
|---|---|
| Where | `routes/admin.py:248-272` (no check of the job's owner); `accounts.py:99-124` (`soft_delete` does not soft-delete the user's projects, so the project check at `admin.py:261` passes) |
| Requirement | API A12 "cancels jobs"; PRD A-5 |
| Verified | Read |

A failed job of a soft-deleted or disabled user can be retried; `ideas` jobs have no project at all. Provider quota is spent and new files are written for an account awaiting purge. **Fix:** load the owner; refuse with `409 not_retryable` when `deleted_at` or `disabled_at` is set.

### QB8-007 Minor: the search drops `_` and `%` instead of escaping them

| | |
|---|---|
| Where | `routes/admin.py:113` |
| Requirement | API I1 "Search users (email, name)" |
| Verified | Read |

`q=john_doe` searches for `johndoe`, so an address with an underscore is never found by typing it. `q=_` or `q=%` becomes `%%` and lists everyone. There is no injection: the value is bound. **Fix:** `User.email.icontains(q, autoescape=True)` (and the same for the name), or escape `\`, `%`, `_` and pass `escape="\\"`.

### QB8-008 Minor: after the clean-up, publishing an older render loses the "Voice: elevenlabs.io" credit

| | |
|---|---|
| Where | `publishing.py:58-73` (`credit_lines` finds the voice provider by looking up `Asset` rows from the render's snapshot keys); `cleanup.py:80` deletes those rows once the voice was replaced 7 days ago |
| Requirement | PUBLISHING_YOUTUBE F5 "The description ends with the credit lines the licences require … 'Voice: elevenlabs.io'" (QB6-001) |
| Verified | Read |

This answers the brief's question about snapshots. A finished render no longer needs the replaced voice **file** (the MP4 is made, and publishing uploads the MP4, which `_in_use` protects through `Render.asset_id`). A render still queued is safe too: it waits at most 3 hours against a 7-day keep. But the asset **row** is still needed for the credit line. Render with an ElevenLabs voice, regenerate that voice, publish the old render 8 days later with `confirmOutdated`: the credit line is silently missing. **Fix:** record the credit lines (or the voice providers) in the render's timeline snapshot at render time, as is already done for the music licence.

### QB8-009 Minor: nothing checks what was uploaded as a music track

| | |
|---|---|
| Where | `routes/admin.py:423-455` (I11: `filename` and `sizeBytes` are validated and then discarded; `durationSeconds` is the browser's claim); `admin.py:485` (I12 activates on `exists()` alone); `storage.py:112-118` (the link signs the key and Content-Type, not the size; valid 15 minutes and reusable) |
| Requirement | API I11 (`sizeBytes`, 30 MB), error codes `413 file_too_large`, `422 upload_invalid` as applied to creator uploads |
| Verified | Read |

Through the link an admin (or anyone holding the link for 15 minutes) can store any bytes of any size up to the storage's single-upload limit, labelled as audio, and overwrite them again after the track was switched on. Activation then offers the file to every creator, with a duration nobody measured; a wrong duration or a non-audio file surfaces as failed renders for creators. Admins are trusted, so this is Minor. **Fix:** at activation, `head_object` and refuse when `ContentLength` exceeds 30 MB or the stored type differs; measure the duration on the server (`mutagen` is already used by the seeder) instead of trusting the body; or issue a presigned POST with a `content-length-range` condition.

### QB8-010 Minor: `isTester` changes nothing

| | |
|---|---|
| Where | `routes/admin.py:167-168`; `is_tester` has no other reader in `reelforge_api` |
| Requirement | DB design, users: "is_tester … Larger monthly credit allowance"; technical design "gets the larger test allowance"; PRD 5.10 |
| Verified | Read (grep) |

Setting the flag stores it and shows a badge. The allowance stays what it was; the console labels the box "Tester (more monthly credits)" (`AdminPage.tsx:236`). **Fix:** either apply the test allowance when the flag is set (and at the monthly reset), or change the documents and the label to say the flag is a marker only and the allowance is set separately.

### QB8-011 Minor: test gaps in `tests/test_admin.py`

Seven tests. Not covered: authorisation of the 12 routes other than three GETs, and of signed-out callers; removing one's own admin role; any successful I6 retry, or its refusals for deleted Reels and scenes; I7; I13 on a track in use; `clean_replaced` keeping a render's video and cover, a project thumbnail or music file, and the 7-day boundary; the purge deleting files at all; the beat entry. (The tester's `test_admin_b8.py`, which appeared during this review, seems to target most of these; it was not run here.)

### QB8-012 Polish

- The audit line (`routes/admin.py:65-67`) contains ids, flags and amounts only: no secrets, no email, no note. For `update_music` it does not say what changed (`admin.py:489`), and for `adjust_credits` the note is only in the ledger.
- API design still says "I. Admin (13)" and "Admin: I1–I13" in section 5; there are 14.
- `routes/admin.py:123`: one count query per listed user (up to 100 per page).
- `routes/admin.py:452-454`: the track row is committed before the link is signed; a signing failure leaves an inactive row without a file.

---

## 2. Frontend findings

### QF8-001 Minor: the Jobs tab has no provider filter

`AdminPage.tsx:282-312` has State and Type only; `useAdminJobs` accepts `provider` (`api/admin.ts:97`) but nothing sets it. SCREENS_F8 §3: "Filters for state, type and provider". Read. **Fix:** add the third select from the I8 names. (Until QB8-004 is fixed it would find succeeded jobs only.)

### QF8-002 Minor: the tabs on both pages ignore the arrow keys

`SettingsPage.tsx:77-95`, `AdminPage.tsx:77-95`. Both use `role="tablist"`/`"tab"` but every tab is a Tab stop and ←/→/↑/↓/Home/End do nothing. This is the same defect as QF6-012, which was fixed in the editor (`Inspector.tsx:147`, "one Tab stop; arrow keys, Home and End"). The Settings list also declares `aria-orientation="vertical"` while it is laid out horizontally below `md`. Read. **Fix:** reuse the Inspector's handler: roving `tabIndex`, arrows by orientation, Home and End.

### QF8-003 Minor: deleting the account is one native confirm away, and the confirm does not say what the spec asks

| | |
|---|---|
| Where | `SettingsPage.tsx:465-491`; `api/account.ts:29-32` |
| Requirement | SCREENS_F8 §1 "Delete account (password, a confirm with the 7-day purge explained)"; API A12 |
| Verified | Read |

1. The confirm reads "Delete your ReelForge account for good?". The 7-day purge and "can't be undone" are only in the paragraph above it, not in the confirm.
2. The password field is `autoComplete="current-password"` and stands outside any form, on the same tab as the change-password form. A password manager is likely to fill it when the tab opens, which enables the red button; the only step left is a `window.confirm`, which browsers let users suppress ("don't show more dialogs") and which then returns false silently.
3. After deletion the browser goes to `/login?deleted=1`, but nothing reads `deleted`, so the person lands on a plain login page with no word that the account is gone.

**Fix.** Open the app's `Dialog` from the button; put the explanation (7 days, cannot be restored, Shorts stay on YouTube) and the password field inside it, with `autoComplete="off"` or a field that is not filled until the dialog opens. Show a one-line notice on the login page for `deleted=1`.

### QF8-004 Minor: the out-of-credits dialog has no suggestion, and its title is wrong when credits remain

`OutOfCredits.tsx:30-34`. PRD flow 7 step 3: "Suggestions: switch expensive scenes to stock or AI images"; UI review §3 keeps the modal "exactly the PRD's flow 7, including the 'use stock instead' suggestion". SCREENS_F8 §2 lists needed, balance, renewal and the link and is silent on the suggestion, so confirm which document wins (question 6). The title "You're out of credits" is also shown when the balance is, say, 5 of a needed 12. Read. **Fix:** add the suggestion line; title "Not enough credits" unless the balance is 0.

### QF8-005 Minor: Add track advertises M4A and 30 MB, but the checks are the server's alone and its answer is generic

`AdminPage.tsx:544-550`, `api/admin.ts:147`. The body sends `contentType: file.type`. Browsers commonly report `.m4a` as `audio/x-m4a` (and an empty type when the OS does not know the extension), neither of which is in the API's list (`routes/admin.py:429`), so the upload is refused with "Some details need another look" or the first field message. A file over 30 MB gets the same generic line. To confirm in a browser. Read. **Fix:** check type and size in the form with a specific message; map `audio/x-m4a` to `audio/mp4` (or accept it in I11).

### QF8-006 Minor: a failed upload leaves a dead track, and "try again" makes a second one

`api/admin.ts:144-153`. If the PUT or the final PATCH fails, the row created by I11 stays, inactive and without a file; it cannot be switched on (`409 file_missing`) and there is no way to upload to it again. The message says "try again", which creates a second row with the same title. Also `AdminPage.tsx:491`: after a successful add the state is cleared but the file input is uncontrolled, so it still shows the previous file name while the form answers "choose a file". Read. **Fix:** on failure delete the row just created (I13), or keep its id and offer "upload again" (needs a new link from the API); reset the input with a `key` or a ref.

### QF8-007 Minor: Retry gives no sign that it worked

`AdminPage.tsx:341-347`. On success the list is refetched; the retried job is still `failed` with its Retry button (the retry is a new job, which the default "failed" filter hides). Nothing says "started". The natural next step is to press Retry again (see QB8-005 item 4). Read. **Fix:** show "Started again" on the row and hide the button once the job has been retried (needs the marker from QB8-005).

### QF8-008 Minor: no automated checks for most of F8

No unit test exists for `SettingsPage`, `AdminPage` or `OutOfCredits`, and `client.test.ts` was not extended for the two new behaviours (401 `invalid_credentials` is not a sign-out; 402 calls the handler). The mock has no `/admin/*` handlers, so `/admin` shows only errors in mock mode and cannot be exercised by the mock e2e suite. `e2e/shell.spec.ts` covers the settings tabs and the wrong-password case. Read. **Fix:** at least: client tests for both branches; a dialog test (opens on 402, once, link goes to the Credits tab); a non-admin is redirected from `/admin` without any `/admin/*` request; delete-account with a wrong password stays signed in.

### QF8-009 Polish: the YouTube tab does not show the channel picture

`SettingsPage.tsx:260-265`; `Connection.pictureUrl` exists (`api/types.ts:36`). SCREENS_F8 §1: "Connected: channel picture and name". The testing note is also shown only when not connected, not when a reconnect is needed.

### QF8-010 Polish: `window.prompt` and `window.confirm` instead of the app's dialog

`AdminPage.tsx:213, 370, 519`; `SettingsPage.tsx:275, 487`. SCREENS_F8 asks for "a confirm", so these meet the letter. The reason for switching a provider off comes from `window.prompt`: when the browser suppresses dialogs it returns `null` and the P0 kill switch silently does nothing; there is no length hint (over 200 characters is a 400); and it looks unlike every other dialog in the app.

### QF8-011 Polish: number fields that fail silently

`AdminPage.tsx:258-260`: the label reads "use − to take away" with a typographic minus (U+2212); typing or pasting that character gives `NaN` and a disabled button with no message. `1e3` and `0x10` are accepted as 1000 and 16. `AdminPage.tsx:246-247`: an invalid allowance does nothing, with no message.

### QF8-012 Polish: copy and small layout points

- Phone and tablet: the user detail panel is placed after the whole users table (`AdminPage.tsx:144, 190`); tapping a row far up the list shows nothing on screen. The tables themselves scroll sideways inside `overflow-x-auto` and the ledger has three narrow columns, so they are usable.
- `SettingsPage.tsx:222`: the mode chips are headed "Visual style"; SCREENS_F8 calls it "mode for new Reels".
- `SettingsPage.tsx:214-228`: the chips are not disabled while a save is pending, so two quick choices send two PATCHes that may land out of order (the risk noted in the test plan, section 7).
- `SettingsPage.tsx:332`: after an admin top-up the line reads, for example, "125 of 100 left".
- `SettingsPage.tsx:383`: no loading state for the history; `SettingsPage.tsx:64`: a failed `GET /me` renders an empty page.
- `SettingsPage.tsx:438, 446`: the server's field error stays under the field while the person types a correction (it clears on the next submit).
- The change-password form has no username field for password managers (`autocomplete="username"`, may be hidden).

---

## 3. What passed (all *Read*)

**Admin security**

- Authorisation: all 15 handlers in `routes/admin.py` carry `@require_user(admin=True)`; `auth.py:62-67` authenticates, refuses disabled accounts, checks CSRF on changes, then `403 forbidden`. The role is read from the database on every request, so a removed role takes effect at once.
- Self-lockout: `admin.py:162-165` refuses `disabled: true` and `isAdmin: false` on one's own id with `409 own_account`, before anything is changed. Because nobody can demote themselves, the last admin cannot be removed through I3 (but see question 1).
- Mass assignment: `Body` is `extra="forbid", strict=True` (`schemas.py:28`); I3 sets four named fields only; the allowance is bounded 0 to 100,000.
- Search: bound parameter, cut to 100 characters; no injection (the functional defect is QB8-007).
- I4: the account row is locked with `FOR UPDATE` and re-read (`credits.py:35-43`), the balance check, the change and the ledger row are one transaction, and the table's `balance >= 0` check backs it. I3's allowance change touches another column and cannot lose a concurrent balance change.
- I6 and money: the new job has `cost=0`, so nothing is reserved and nothing can be refunded for it (`credits.refund` returns 0); the old job's refund was written once (`credit_ledger_once_per_job`). No double charge and no double refund. Locks: project, then scene, the workers' order. A second retry while the first is active is refused by `jobs_one_active`. Deleted Reels and scenes are refused.
- I9: both raw statements are parameterised; the provider name is checked against `PROVIDERS` first.
- Audit: every changing route logs after its commit with the admin's id; the entries hold no passwords, tokens, emails or notes. The JSON formatter keeps `extra` fields (`log.py:31`), and none of the keys used collide with log-record attributes.
- Disabling a user sets `disabled_at` and revokes every refresh token; `_authenticate` refuses the still-valid access token on the next request.

**Clean-up**

- `clean_replaced` re-checks use across scenes (voice, visual, keyframe), renders (video, cover) and projects (thumbnail, music file) of **any** project, so a duplicate keeps a replaced file alive and the flag is cleared.
- Order and crashes: files first, rows second, in both tasks. A crash in between leaves rows whose files are gone; the next run repeats the delete (S3 answers success for a missing key) and removes the rows. Nothing can pick such an asset up in between: `use_upload` refuses replaced assets (`visuals.py:567`).
- A queued or running render reads files replaced at most hours ago, far inside the 7 days.
- Music library files live under `library/music/` in `music_tracks`, not in `assets`; neither task touches them.
- The task is registered (`files.clean_replaced`), scheduled daily at 03:50 on the `maintenance` queue, which the worker consumes (`docker-compose.yml:141`).

**Frontend**

- `client.ts:89-90`: only `POST /auth/login` (a public path, never reaches this branch) and the password check of A11/A12 (`routes/me.py:26`, after authentication) answer `401 invalid_credentials`. A missing or dead session answers `not_authenticated` or `token_expired` first, so a real sign-out is still handled.
- 402: `OutOfCredits` is mounted once, in `AppShell`, which wraps every app route. Several 402s at once set the same state, so one dialog shows. It uses the native modal `<dialog>`, which opens above another open dialog and returns focus on close. Mutations are not retried, so one action calls the handler once. The page's own message still shows (`errors.ts`).
- `/admin` for a non-admin: `AdminPage` waits for `useMe`, then returns `<Navigate to="/app">` before any tab component mounts, so no `/admin/*` request is sent. The sidebar link is shown only when `isAdmin`.
- The console disables the Admin and Disabled boxes on one's own row; both ask before sending; the boxes are bound to the server's value, so a cancelled confirm changes nothing.
- Settings: tabs are kept in `?tab=`, an unknown value falls back to Profile, `tab=instagram` opens YouTube; the credits pill and the channel pill link to their tabs. Name is required and capped at 80; topics reuse the onboarding rules. Change password needs all three fields, 8 characters and a match; the wrong-password answer shows at the field and the session stays. Costs come from H1, never hard-coded; the history pages with H2's cursor. No "buy credits" anywhere.
- `npm run typecheck`: clean.

## 4. Questions

1. **Last admin.** I3 cannot remove the last admin, but an admin can delete their own account through A12, and two admins can demote each other at the same moment. Should A12 refuse the last admin, and is a CLI command the accepted way back in?
2. **A disabled user's running jobs.** Disabling signs the user out but their queued and running jobs carry on, save their results and spend provider quota; no document says what should happen (A-5 covers deleted accounts only). Cancel and refund them, as A12 does?
3. **Audit record.** "Every change is logged with the admin's id" is met by a log line on standard output. Is that enough, or should changes be kept in a table that outlives log rotation?
4. **I6 for `ideas` and `hooks`.** The retry creates a new job, but the creator's screen was polling the old job id, so nobody sees the result. Is the retry meant for these types?
5. **Adjustment notes.** The admin's note is shown to the user as the history line (`SettingsPage.tsx:375`), and the console's prompt for a provider says "shown to other admins". Should the adjust form say "the user sees this"?
6. **Out-of-credits suggestion.** PRD flow 7 and the UI review ask for the "switch to stock" suggestion; SCREENS_F8 §2 does not list it (QF8-004).
7. **Providers.** The UI review lists Groq Whisper, Pexels and Hugging Face video; the console lists what is built (`pixabay`, `cloudflare`, no transcription or AI video). Assumed intended until B7; and should old finished renders' videos ever be cleaned (they are never marked replaced)?
