# ReelForge: QA report for F7 (render and publish screens)

| | |
|---|---|
| Date | 2026-11-27 |
| Milestone | F7: render progress (`/app/p/:id/render`), publish (`/app/p/:id/publish`), "Your Short is live", connect results, onboarding step 3 (YouTube), editor Render and Publish buttons, top-bar channel pill |
| Spec | [SCREENS_F7.md](../SCREENS_F7.md) §1–§6; [PUBLISHING_YOUTUBE.md](../PUBLISHING_YOUTUBE.md) (F1, F4–F7, Post, YT-1 to YT-13); API design E1–E3 and the Render object; PRD CR2; [UI_REVIEW.md](../UI_REVIEW.md); conventions from [SCREENS_F5_F6.md](../SCREENS_F5_F6.md) and [QA_REPORT_F6.md](QA_REPORT_F6.md) |
| Build tested | `frontend/` working tree (F7 uncommitted); `backend/api` working tree (B6 uncommitted), read only for the API's actual behaviour |
| Checks run | `npm run typecheck`: clean. `npx vitest run`: 17 files, 633 tests, all pass (including the 11 new cases in `RenderAndPublish.test.tsx`) |
| Browser | **Not run.** The dev server on 5173 is in real mode against the main stack, which this review was told not to touch, and the machine is low on memory, so no isolated stack or second dev server was started. Every finding below is therefore *Read* (concluded from the code). Where the backend decides the outcome, the backend line is cited |

## Verdict: pass with issues

The screens follow SCREENS_F7 closely. The render page shows queued and running states with a progress bar, Stop (G3 with a confirm), the finished video with Download and Publish, failed and stopped attempts, and earlier renders. After a stopped or failed attempt it keeps the newest finished video, as decided overnight. The publish page has the two platform tiles (Instagram "Coming soon"), the account row with the testing-mode note before Google, the title, description and hashtag limits with counters, `< >` refused before sending, Public as the default, the outdated-render confirm, the four-step tracker polling F6, and the "Your Short is live" view with the `privateUntilVerified` note. The connect-result dialog shows once and strips the query on the publish page. Idempotency keys are sent on E1 and F5, and double clicks are blocked by the loading state.

Two findings need fixing before commit:

- **QF7-001.** The publish page's autosave of the post text goes through B5 `PATCH /projects/:id`. The backend treats every B5 call as a Reel edit. So the first keystroke in the title makes the render **outdated**, and moves a `rendered`, `posted` or `post_failed` Reel back to `ready`. The fix is most likely in the backend (B6), but the F7 flow is where it shows.
- **QF7-002.** The editor's Publish button disappears after a failed render attempt, even when an earlier finished video exists. That contradicts the overnight decision, and the editor offers no other way to reach that video.

The rest are Minor or Polish.

| Severity | Count | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 2 | QF7-001, QF7-002 |
| Minor | 8 | QF7-003 to QF7-010 |
| Polish | 6 | QF7-011 to QF7-016 |
| Questions | 7 | Section 4 |

**Verified column:** *Read* means concluded from the code only. *Test* means covered by a passing unit test that was read and run.

---

## 1. Findings

### QF7-001 Major: typing the post text marks the render outdated and moves a rendered or posted Reel back to "ready"

| | |
|---|---|
| Where | `frontend/src/features/publish/PublishPage.tsx:172-189` (autosave sends `postTitle`, `postCaption`, `hashtags` through `useUpdateProject`, B5); `backend/api/reelforge_api/routes/projects.py:274` (`update_project` always calls `projects.after_edit`); `backend/api/reelforge_api/projects.py:97-101` (`after_edit` bumps `timeline_version` and re-settles the status to `ready`/`draft`); `backend/api/reelforge_api/renders.py:471` (`outdated = render.timeline_version < project.timeline_version`) |
| Requirement | SCREENS_F7 §3 "Unsaved text … saved to the project as they are typed"; API design, Render: "`outdated` is true once the **Reel** was edited after this render"; PUBLISHING_YOUTUBE YT-13; API design B4 statuses (`rendered`, `posted`, `post_failed`) |
| Verified | Read (backend path is unambiguous; no backend test PATCHes the post fields and then publishes) |

**Steps.** Rendered Reel, latest render `succeeded`, not outdated. Open the publish page and add one character to the title. Wait 1 s.

**Expected.** The text is saved. The render stays current, and the Reel stays `rendered`.

**Actual (from the code).**

1. The PATCH bumps `timelineVersion`. The response is written into the project cache (`useUpdateProject` `setQueryData`), so `latestRender.outdated` becomes true.
2. The form then shows "You edited the Reel after this render", and **Publish now** stays disabled until "Publish this version anyway" is ticked. Without the tick, F5 would answer `409 render_outdated`.
3. The status is re-settled to `ready`, so a `rendered` Reel loses that status. Its dashboard card then opens the editor instead of the publish page (`ReelCard.tsx:12`).
4. The same happens to a `posted` or `post_failed` Reel whenever its text is edited.
5. Every later keystroke bumps the version again.

**Fix.** In the backend, leave out `after_edit` (the version bump and the status change) when a B5 body holds only `postTitle`, `postCaption` and/or `hashtags`. Post text is not part of the timeline. Still update `updated_at` if the dashboard order should change. Add a backend test: PATCH the post fields on a rendered project, then check that `latestRender.outdated` is false, the status is still `rendered`, and F5 without `confirmOutdated` answers 202. Add a frontend test that the outdated notice does not appear after typing. The alternative is a separate endpoint for the post text, which is a design change for the product owner.

### QF7-002 Major: after a failed render attempt the editor hides Publish, although an earlier finished video exists

| | |
|---|---|
| Where | `frontend/src/features/editor/ReelEditorPage.tsx:205` |
| Requirement | SCREENS_F7 §2 last row: "The publish page and the editor's Publish button use that video too" (the overnight decision) |
| Verified | Read |

**Steps.** A Reel with a finished render. Render again, and let the attempt fail (`render_failed`) or stop it while the Reel was `ready`.

**Expected.** The editor still shows **Publish**, which opens the publish page with the earlier video.

**Actual.** The condition is `latestRender.state === 'succeeded' || status in (rendered, posting, posted, post_failed)`. After a failed attempt, `latestRender` is the failed one and the status is `render_failed`, so Publish is hidden. The same happens after a stopped attempt that returns the Reel to `ready`, and while a new render is running. The comment on line 204 says the opposite. The editor has no link to the render page in these states, and the dashboard card for `render_failed` opens the editor (`ReelCard.tsx:10-15`). The earlier video can only be reached by typing the URL.

**Fix.** API design, Render: "the project's `latestRender` … its `thumbnailUrl` is the cover of its latest finished render". So `!!p.latestRender?.thumbnailUrl` already means "a finished video exists" without an extra request. Alternatively use `useRenders(id)` as the render and publish pages do. Add a test for `render_failed` with an earlier `succeeded` render.

### QF7-003 Minor: F5 refusals all show as one line of server text; the specified pointers are missing

| | |
|---|---|
| Where | `frontend/src/features/publish/PublishPage.tsx:245-263` (`onError` → `setProblem(errorText(e))`); `AccountRow` 434-486 reads `user.connections` from `useMe`, which nothing refreshes after a refusal |
| Requirement | SCREENS_F7 §3 Publish now: "`not_connected` and `reconnect_required` point to the account row, and `already_posted` links to the Short"; PUBLISHING_YOUTUBE YT-6, YT-7, YT-13 |
| Verified | Read |

**Actual.**

- **`reconnect_required`** (YT-6; testing-mode tokens expire after 7 days, so this is the common case): the account row keeps showing the channel as connected with "Change", because `me` is not refetched. The message under the button contradicts the row above it. A failed post with error code `reconnect_required` has the same problem: the tracker hands over to the form, and `me` is still stale.
- **`already_posted`**: shows "This video is already on YouTube." with no link. The page keeps the form instead of switching to the live view.
- **`render_outdated`** when the client still thinks the render is current (for example, edited in another tab): the message shows, but there is no confirm checkbox, because `outdated` comes from the cached render. The project is not refetched.

**Fix.** In `onError`:

- for `not_connected` and `reconnect_required`, invalidate `['me']` and move focus to the account row;
- for `already_posted`, refetch the project's posts (F7) or the project, and show the live view or a link to `post.url`;
- for `render_outdated`, invalidate the project so the confirm appears.

Also invalidate `['me']` when a followed post ends `failed` with `reconnect_required`.

### QF7-004 Minor: autosave gaps (a pending save dropped on leaving, one invalid field blocks all, a failed save isn't retried)

| | |
|---|---|
| Where | `frontend/src/features/publish/PublishPage.tsx:172-189` |
| Requirement | SCREENS_F7 §3 Unsaved text ("leaving and coming back keeps them"); FIX_LOG_F5 autosave rules (a failed save stays unsaved and is sent again) |
| Verified | Read |

**Actual.**

1. **A pending save is dropped on leaving.** The 800 ms timer is cleared on unmount and there is no final flush. Text typed less than 0.8 s before leaving is lost. That covers the back arrow, the steps header and **Connect YouTube** (the F1 round trip may or may not beat the timer). Publish itself is safe, because F5 saves the text on the server (`publishing.py:173`).
2. **One invalid field blocks all three.** While the title is empty or either field contains `<` or `>`, nothing is saved, including valid description and hashtag edits. No note says so.
3. **A failed save isn't retried.** `saved.current` is updated before the request succeeds, so it is not sent again until the next keystroke. The error line also doesn't say which text is unsaved.
4. **Over-long text is sent anyway.** A title over 100 characters is sent and refused by the API (400), unlike the `<` `>` case.

**Fix.** Flush the pending save on unmount, and before **Connect YouTube**: await the PATCH, then call F1. Save each valid field on its own, and skip only the invalid one. Update `saved.current` in `onSuccess`. Apply the same length checks before sending.

### QF7-005 Minor: a failed post on an older render is shown as "Try again, it continues where it stopped" for the new render

| | |
|---|---|
| Where | `frontend/src/features/publish/PublishPage.tsx:131` (`failed={post?.state === 'failed' ? post : null}`), 418-428 |
| Requirement | SCREENS_F7 §3 tracker ("Try again, which continues the earlier upload"); PUBLISHING_YOUTUBE F5 (continuation is per render) |
| Verified | Read |

**Steps.** Publish render A and let the post fail. Edit the Reel, render B, then open the publish page.

**Actual.** The form shows A's failure reason, "Press Publish to try again; it continues where it stopped", and the button reads **Try again**. But it publishes B, which is a fresh upload.

**Fix.** Pass `failed` only when `post.renderId === render.id`. Otherwise show the normal form, optionally with a muted "The last try, on an earlier video, didn't work".

### QF7-006 Minor: onboarding keeps the connect result in the address, shows it again on reload, and lacks the no-channel link

| | |
|---|---|
| Where | `frontend/src/features/onboarding/OnboardingPage.tsx:136-146` (passes `connected`, `connection_error`, `reason` through and never removes them); `frontend/src/features/onboarding/YouTubeStep.tsx:36, 73-78` |
| Requirement | SCREENS_F7 §5: "Any page that gets these (publish, onboarding, settings) shows a dialog, then removes them from the address"; `no_channel` "with a link to create one"; §6 onboarding step 3 |
| Verified | Read |

**Actual.**

- After Google sends the browser back to `/onboarding?step=3&connection_error=youtube&reason=denied`, the error shows inline, which is a reasonable adaptation. But the query stays, so a reload or Back shows the same failure again.
- `no_channel` has no link to youtube.com, while the publish page's dialog has one.
- A connection in `reconnect_required` shows a plain "Connect YouTube" with no word about the expired connection.

**Fix.** Mount `ConnectionResult` on the onboarding page, or strip the three parameters after reading them, as `ConnectionResult` does (`setParams(next, {replace: true})`). Add the "Create a channel" link. Say "Your YouTube connection has expired" for `reconnect_required`.

### QF7-007 Minor: progress and outcome changes are not announced; focus is lost when the form is replaced

| | |
|---|---|
| Where | `frontend/src/features/publish/RenderPage.tsx:109-129` (state heading and percentage); `PublishPage.tsx:121-137` (form → tracker → live view swap), 490-534 (tracker); `ReelEditorPage.tsx:199` (reason for a disabled Render only in `title`) |
| Requirement | Brief: live regions for progress. Existing convention: `role="status"` / `aria-live="polite"` for changing status (`ScriptEditorPage.tsx:203`, `Inspector.tsx:61`, `SceneCard.tsx:196`) |
| Verified | Read |

**Actual.**

- **Render page.** "Rendering" turning into "Your video is ready" or "Failed" is not in a live region. Neither is the percentage. Screen-reader users on that page hear nothing when the render ends. The `role="progressbar"` is correct, but it is not announced on change.
- **Publish page.** Pressing **Publish now** unmounts the form, including the focused button, so focus drops to `<body>`. The tracker's step changes and the switch to "Your Short is live" are not announced either.
- **Editor.** The disabled Render button explains itself only through a `title` tooltip. Keyboard and touch users never see it. F6 used a visible note with `aria-describedby`.

**Fix.** Put a visually hidden `role="status" aria-live="polite"` line on both pages that states the state ("Rendering, 40%"; announce every 10% or only state changes; "Your video is ready"; "Uploading"; "Your Short is live"). When the tracker or live view appears, move focus to its heading (`tabIndex={-1}`). Restore a visible reason next to the disabled Render, linked with `aria-describedby`.

### QF7-008 Minor: "Render again" and "Try again" on the render page spend 2 credits without showing the cost

| | |
|---|---|
| Where | `frontend/src/features/publish/RenderPage.tsx:55, 170-174, 191-199` |
| Requirement | PRD CR2 "Show the credit cost before every action that spends credits"; SCREENS_F7 §1 (the editor asks "… 2 credits, refunded if it fails") |
| Verified | Read |

**Actual.** The buttons call E1 at once, with no cost shown and no confirm. The editor asks first (`ReelEditorPage.tsx:104`).

**Fix.** Reuse the editor's confirm, with the cost from H1 `costs.render`, or show "· 2 credits" on the buttons.

### QF7-009 Minor: E1 refusals show the server's sentence, without the scene names or the length

| | |
|---|---|
| Where | `frontend/src/features/editor/ReelEditorPage.tsx:111-121`; server texts at `backend/api/reelforge_api/renders.py:145-160` |
| Requirement | SCREENS_F7 §1: "`not_ready` names the scenes and selects the first one; `too_long` and `too_short` give the length" |
| Verified | Read |

**Actual.**

- **`not_ready`** selects the first listed scene, but shows only "Some scenes aren't ready yet. Generate them first." It doesn't say which scenes (`details.scenes` holds ids; positions are in the project).
- **`too_long` and `too_short`** ignore `details.seconds`.
- **`too_short`** reads "Instagram needs Reels of at least 3 seconds", which is wrong for YouTube-first. That text comes from the backend.

**Fix.** Map the codes in the client: "Scenes 2 and 4 aren't ready yet", "This Reel is 94 s; the limit is 90 s", "This Reel is 2 s; it needs at least 3 s". Ask the backend to drop "Instagram" from `too_short`.

### QF7-010 Minor: a failed "Change" (reconnect while connected) shows no error

| | |
|---|---|
| Where | `frontend/src/features/publish/PublishPage.tsx:451-462` (the connected branch has no `connect.isError` notice; only the not-connected branch at 477-481 has one) |
| Requirement | Brief: error handling for every API refusal; PUBLISHING_YOUTUBE F1 `503 provider_unavailable` |
| Verified | Read |

**Actual.** If F1 fails (503 when the OAuth client isn't configured, network, 403), the "Change" button stops spinning and nothing is shown.

**Fix.** Render the same error notice under the connected row.

### QF7-011 Polish: the render page polls the whole project every 2 s instead of E2 every 3 s; the E2 hook is unused

| | |
|---|---|
| Where | `frontend/src/features/publish/RenderPage.tsx:36, 50` (reads `project.latestRender`; polling comes from `useProject`, `POLL_MS = 2000`); `frontend/src/api/publishing.ts:48-56` (`useRender`, never used) |
| Requirement | SCREENS_F7 §2: "Polls E2 every 3 s" |
| Verified | Read |

**Actual.** Works, but each poll is the full B4 project, with every scene and a fresh set of presigned links. That is heavier than E2, and the interval differs from the spec. The E3 history is also not refetched when the render ends, so the "Earlier renders" list can lag.

**Fix.** Poll `useRender(latestRender.id)` while it is active, and invalidate the project and renders when it settles. Or record in the spec that B4 polling is the chosen approach, and delete `useRender`.

### QF7-012 Polish: publish page while a render is running

| | |
|---|---|
| Where | `frontend/src/features/publish/PublishPage.tsx:91-102`, 238-243 |
| Requirement | SCREENS_F7 §3; PUBLISHING_YOUTUBE F5 `409 already_running` ("the Reel is rendering") |
| Verified | Read |

**Actual.**

- With no earlier video, a Reel that is rendering shows "Render the Reel first", which is wrong while it renders.
- With an earlier video, the form is enabled, and **Publish now** gets "This Reel is busy. Try again when it's done."

**Fix.** When `isRenderActive(p.latestRender)`, say "Your video is being made" and link to the render page. Disable Publish with that reason.

### QF7-013 Polish: semantics of the platform tiles and the counters

| | |
|---|---|
| Where | `frontend/src/features/publish/PublishPage.tsx:271-288` (tiles are `div`s with `aria-current="true"` / `aria-disabled`); 293-300, 320-326, 342-350 (the counter sits inside the `<label>`) |
| Requirement | Accessibility conventions (F5/F6 use `aria-pressed` chips and labelled groups) |
| Verified | Read |

**Actual.** Screen readers don't hear "YouTube Shorts, selected" or "Instagram Reels, unavailable". The fields' accessible names include the live counter ("Title 12/100"), which changes on every keystroke.

**Fix.** Use a radio group: one checked radio, plus a disabled radio for Instagram with "Coming soon". Move the counters outside the label and link them with `aria-describedby`.

### QF7-014 Polish: copy and small UI differences

| | |
|---|---|
| Where | `PublishPage.tsx:577-581` ("Publish the newer render"; spec §4 "Publish again"); `PublishPage.tsx:538-541` (Copy link: no message if the clipboard is refused, and "Link copied" is not announced); `ReelEditorPage.tsx:199` ("Every scene needs its voice and visual first" also shows for `posting` and `needs_attention`, where it isn't the reason); `RenderPage.tsx:53` (while E3 loads after a failed attempt, the page first shows the failure as the main state with a primary **Try again**, then switches to "Your video is ready") |
| Requirement | SCREENS_F7 §2, §4; plain-English copy |
| Verified | Read |

**Fix.** Use "Publish again" as the spec says, or update the spec. Give Copy link an `aria-live` confirmation and a fallback message when the clipboard is refused. Use a status-specific reason on the disabled Render. Show the spinner until E3 has loaded when `latestRender` isn't `succeeded`.

### QF7-015 Polish: "Write with AI" keeps polling after the page is left

| | |
|---|---|
| Where | `frontend/src/api/publishing.ts:96-117` (`waitForJob(job.id, signal)` with no signal passed); `PublishPage.tsx:211` |
| Requirement | Convention: `useHooks` and the F5 screens pass an `AbortSignal` so polling stops on unmount |
| Verified | Read |

**Fix.** Hold an `AbortController` in the form, pass its signal, and abort on unmount.

### QF7-016 Polish: duplicated reason texts; pasted comma lists become one hashtag

| | |
|---|---|
| Where | `frontend/src/features/onboarding/YouTubeStep.tsx:11-16` repeats `ConnectionResult.tsx:8-13`; `PublishPage.tsx:40` (`cleanTag` removes spaces but keeps commas) |
| Requirement | SCREENS_F7 §3 Hashtags ("type and press Enter or comma") |
| Verified | Read |

**Actual.** Pasting "fitness, gym" and pressing Enter makes one tag, `fitness,gym`.

**Fix.** Export the reason texts from one place. In `addTag`, split pasted text on commas and spaces and add each part.

---

## 2. What passed

| Area | Check | Verified |
|---|---|---|
| Build | Typecheck clean; 633 unit tests pass | Run |
| Routes | `/app/p/:id/render` and `/publish` replace the F7 placeholders; a 404 project goes to the library | Read |
| Editor §1 | Render is enabled for `ready`, `rendered`, `render_failed`, `posted`, `post_failed`. It saves unsaved edits (`flushAll`), confirms with the H1 cost, calls E1 with an Idempotency-Key, and opens the render page. `already_running` on this Reel opens the render page. `not_ready` selects the first scene. 402 uses the shared credit message. While `rendering` the button reads "Rendering…" and opens the render page | Read, Test |
| Render §2 | Queued and running states with labels, an accessible progress bar, and the leave note. Stop calls G3 on the render's job after a confirm. Done plays `video.url` with the poster, shows length and size, Download (`downloadUrl`) and Publish. Outdated note with Render again. Failed shows `error.message` plus "credits were given back" and Try again. History is limited to 5 rows with Download for finished renders. No telemetry chips | Read, Test |
| Newest finished video | Render and publish pages pick `latestRender` if it succeeded, else the newest `succeeded` render from E3. The failed or stopped outcome shows under it with Render again (see QF7-002 for the editor) | Read, Test |
| Publish §3 | Tiles (Instagram disabled, "Coming soon"); account row with picture and name, the testing-mode note before Google, reconnect warning; title required, ≤ 100 with counter, starts from `postTitle` else the title; description ≤ 2,200; hashtags with Enter/comma, ×, Backspace, ≤ 30, case-insensitive de-duplication, `#` shown in front; Write with AI with a confirm when text exists; credits note; Public default and the YouTube note; outdated confirm sends `confirmOutdated`; `< >` refused before sending; F5 with an Idempotency-Key | Read, Test |
| Tracker and live view | Queued, Uploading, Processing, Published with `aria-current="step"` and progress, polling F6 every 3 s while active. A failed post returns to the form with the reason and Try again. Live view with Open on YouTube (new tab, `rel=noreferrer`), Copy link, `privateUntilVerified` note, Create another Reel, Back to the dashboard; a published post on an older render offers publishing the newer one | Read, Test |
| OAuth leave | Buttons stay busy from F1 until the page unloads; `pageshow` with `persisted` resets them when the browser restores the page from cache | Read |
| Connect results §5 | Publish page: dialog once, reason allow-listed (`hasOwnProperty`, unknown → `failed`), parameters removed with `replace`, `me` invalidated, channel name shown, "Create a channel" link for `no_channel`. StrictMode's double effect still shows one dialog | Read, Test |
| Channel pill | Channel name with a green dot when connected, "Reconnect YouTube" when needed, nothing otherwise; tolerant of users without `connections` (`?.`) | Read |
| Onboarding §6 | Step 3 calls F1 with `returnTo=/onboarding?step=3`; Skip kept; stepper ticks on `connections.youtube.status === 'connected'` | Read |
| Mock API | `/connections/:platform/connect` added: `instagram` → 404 `not_available`, unverified → 403, otherwise stores "Demo Channel" and returns the sanitised `returnTo` with `connected=youtube`. `/me` includes `connections`. Older databases without `youtube` still load. The older screens' tests pass | Read, Test |
| Mobile | Render and publish pages stack below `md` (video max 320/300 px); editor header actions wrap (`flex-wrap`) | Read only; not checked in a browser |

---

## 3. Out of scope

- Settings and its YouTube row (connect and disconnect, F3): F8. Connect results that land on `/app/settings` (the backend's default `returnTo` when `state` is invalid, `connections.py:36, 107`) therefore show nothing yet; see Question 2.
- Instagram publishing: B6b ("Coming soon" by decision).
- Dashboard and library cover (`thumbnailUrl`, SCREENS_F7 §6): uses the existing card thumbnail. Not separately checked, because nothing in the F7 diff touches it.

## 4. Questions

1. **InstagramStep.** `InstagramStep.tsx` and `InstagramStep.test.tsx` are now unused, but still compiled and tested. Keeping them for B6b is acceptable only if PROJECT_STATUS says so. B6b will use the platform-neutral `connected` / `connection_error` parameters and `/connections/instagram/connect`, not the `instagram=` parameters and `/instagram/connect` mock this component expects, so it will need rewriting anyway. Recommendation: delete it and its test, and the `/instagram/*` mock handlers once nothing uses them. Git history keeps them.
2. **Connect results without a `returnTo` page.** Should they show anywhere before F8, for example via `ConnectionResult` in the app shell? Today an expired or reused `state` sends the creator to the Settings placeholder with no message.
3. **Stitch comparison.** The spec says the screens weren't compared with Stitch screens 12, 13, 20, 21 and 22. Layout details (two columns, tile style, tracker) may still move. Nothing here blocks on it.
4. **Render page "No render yet".** The spec says "Goes back to the editor with a note to press Render". The build shows a card with "Open the editor" instead of redirecting. That seems fine; please confirm.
5. **Order in §1.** The spec says save first, then confirm. The build confirms, then saves. The order is harmless; please confirm.
6. **Choices across the Google round trip.** Visibility and the outdated confirm reset after connecting. Only the text is specified to survive. OK?
7. **Post text and `updatedAt`.** Related to QF7-001: should editing the post text move the Reel to the top of the dashboard at all?
