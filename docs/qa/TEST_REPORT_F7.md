# ReelForge: test report for F7 (render and publish screens)

| | |
|---|---|
| Date | 2026-11-27 |
| Role | Tester (automated tests only; no product code changed) |
| Build tested | `frontend/` working tree, F7 uncommitted (after the fixes in [FIX_LOG_F7.md](FIX_LOG_F7.md)); backend at ebbc721 for reference only |
| Kind of tests | Unit and component only (Vitest, Testing Library, jsdom), against the fake `fetch` in `src/test/fakeApi.ts`. The machine is low on memory, so no browser tests ran, and no Docker stack or dev server was started. The main stack (Compose project `backend`) and the dev server on 5173 were not touched |
| Checked against | [SCREENS_F7.md](../SCREENS_F7.md); [PUBLISHING_YOUTUBE.md](../PUBLISHING_YOUTUBE.md) F1–F7 and the Post shape; [API design](../API_DESIGN.md) group E (Render, E1–E3) and G3; [QA_REPORT_F7.md](QA_REPORT_F7.md); [FIX_LOG_F7.md](FIX_LOG_F7.md) (overnight decisions and QF7 fixes) |

## 1. Results

| Suite | Command | Result |
|---|---|---|
| Typecheck | `npm run typecheck` (app and `e2e/`) | Pass |
| Unit and component (all) | `npx vitest run` | **728 tests: 723 passed, 5 failed** (19 files). The 5 failures are new tests that find BUG-080 to BUG-084. Every test that existed before this round passes, including the 11 F7 starter tests. In one of three full runs, 2 more tests failed under load (see section 4) |
| F7 files alone, run twice | `npx vitest run src/features/publish src/features/onboarding src/features/editor src/mocks` | The same 5 failures on both runs, everything else passing |

## 2. What was added

| File | Tests | What |
|---|---|---|
| `frontend/src/test/f7.tsx` | (helpers) | Shared Render and Post builders, a connected and an expired channel, a page opener that records every address the router shows (to check that parameters are removed), the `<dialog>` polyfill and a `location.assign` stub |
| `frontend/src/features/publish/RenderPage.test.tsx` | 25 | Screen 12: every state, E2 polling, the newest-finished-video rule, history, Stop (G3), Render again and Try again, E1 errors |
| `frontend/src/features/publish/PublishPage.test.tsx` | 64 (57 written, with `it.each`) | Screens 13, 20 and 21–22: the channel row, the fields and their limits, autosave, Write with AI, visibility, the outdated confirm, F5 refusals, the tracker, the live view, the connect-result dialog, the top bar channel pill |
| `frontend/src/features/onboarding/OnboardingPage.test.tsx` | 15 | Onboarding step 3: YouTube connect, the result read once and removed from the address, Skip, Finish, Back |
| `frontend/src/features/editor/ReelEditorPage.test.tsx` (new describe block at the end) | 28 | The editor's Render and Publish: enabled and disabled states with their reasons, save first, the confirm with the cost, E1 with an Idempotency-Key, each E1 refusal, Publish visibility |
| `frontend/src/mocks/handlers.test.ts` (new describe block) | 9 | The mock `GET /connections/:platform/connect` |
| `frontend/src/mocks/db.test.ts` (new describe block) | 3 | `connections.youtube` with databases saved before F7 |

No product file, MSW handler or config was changed. The starter file `RenderAndPublish.test.tsx` was left as it is.

### Covered

**Render page (screen 12)**
- No render: "This Reel hasn't been rendered yet", Open the editor, and no E1 call. The steps header has four steps, ending with Publish.
- Queued: "Waiting for the renderer", 0 %, the placeholder, the leave-the-page note, and no Publish or Download.
- Running: E2 (`/renders/r1`) is polled every 3 s (fake clock). The progress bar follows E2's `progress`. The live text moves in steps of 10 % (`role=status`, `aria-live=polite`). When E2 says `succeeded`, the video shows without waiting for the project, and E2 polling stops.
- Succeeded: the video with `src` and `poster`, "29 s · 11.4 MB", Download (`downloadUrl`), Publish (opens the publish page). No outdated note, no Stop.
- Outdated: the note, and "Render again · n credits", where n comes from H1 (checked with a cost of 3). The confirm names the cost. E1 is sent with an Idempotency-Key, and a new key for each try. Cancelling the confirm sends nothing.
- Failed with no earlier video: the reason, "Your credits were given back", and "Try again · 2 credits". Cancelled: "The render was stopped and its credits given back".
- Nothing about a failure shows before E3 has answered (no failed-state flash).
- The newest finished video: after a failed or stopped attempt, the earlier video stays on screen with Download and Publish. "Your last render failed: …" or "was stopped" shows under it, with Render again. The publish page then publishes that earlier video. A newer render that is still running shows its progress, not the old video.
- History: at most 5 earlier renders, with time, state label, length and Download for finished ones only. The video on screen and the latest attempt are not listed again. There is no section when nothing else exists.
- Stop (G3): asks first, and cancelling sends nothing. It stops through the render's `jobId` (`/jobs/j-render/cancel`) and then shows the stopped state. A refusal shows its message. There is no Stop button when the render has no job.
- E1 errors on this page: 402 shows the shared credit message; other refusals show their message. A started render shows its progress. A 404 project goes to `/app/projects`.

**Editor (Render and Publish)**
- Render is enabled for `ready`, `rendered`, `render_failed`, `posted` and `post_failed`, with no description. It is disabled for `generating`, `posting`, `draft`, `needs_attention` and over 90 s. Each disabled state shows its reason as visible text, and the button's accessible description is that text.
- The confirm names the cost from H1. Cancelling sends nothing. E1 goes to `/projects/p1/render` with an Idempotency-Key, then the render page opens.
- Unsaved inspector text is saved first (the PATCH comes before E1). A double click during that save starts one render. A save that fails stops the render and shows the reason.
- `not_ready` names the scenes by number ("Scenes 2, 3 aren't ready yet…", and the singular form for one) and selects the first scene listed. Without `details.scenes` it shows the server text. `too_long` and `too_short` give the length to one decimal. 402 shows the credit message. `already_running` for another Reel shows the message and stays.
- While `rendering`, the button reads "Rendering…" and opens the render page without calling E1.
- Publish shows when `latestRender` succeeded, or when the project has a `thumbnailUrl` (an earlier video after a failed attempt). It is hidden while a first render runs, after a failed first render, and when nothing was ever rendered. It opens the publish page.

**Publish page (screen 13)**
- Layout: the latest finished video with its poster. The platform tiles are a radio group: YouTube checked, Instagram "Coming soon" and `aria-disabled`. Back to the video; the credits note.
- Channel row and pill:
  - Connected: the picture and name, Change, and the top bar pill linking to `/app/settings?tab=youtube` with "Connected".
  - Not connected: the testers explanation, Connect YouTube, Publish disabled, and no pill.
  - `reconnect_required`: the warning, Reconnect YouTube, the pill reads "Reconnect YouTube", and Publish is disabled.
- Connect, Reconnect and Change each call F1 with `returnTo=/app/p/p1/publish` and go to `authorizeUrl`. The button stays busy until the page leaves, and stops being busy when the page comes back from the back/forward cache (`pageshow` with `persisted`). A failed Connect and a failed Change each show their reason under the row.
- Title:
  - starts from `postTitle`, else the Reel title cut to 100 characters;
  - the counter reads "n of 100 characters";
  - empty, over 100 characters, and `<` or `>` each give a reason (`aria-invalid`, and the reason is the field's description) and disable Publish;
  - exactly 100 characters is allowed.
- Description: over 2,200 characters and `<` `>` are refused in the same way.
- Hashtags:
  - Enter and comma add a chip, and `#` is dropped;
  - duplicates (in any letter case) and a lone `#` are ignored;
  - × removes a chip, and Backspace in the empty box removes the last one;
  - pasting "a, b c" adds three;
  - leaving the box adds what was typed;
  - at most 30: a paste stops at the limit, the box is disabled at 30, and the counter reads 30/30.
- Visibility: Public by default (`aria-pressed`), one choice at a time, sent with F5, and the verification note shows. `confirmOutdated` is not sent when the render is current.
- Autosave:
  - opening the page sends nothing;
  - each field is sent on its own after the pause;
  - an invalid title (empty, too long, `<` `>`) is not sent, while a valid description still is;
  - an invalid description is not sent;
  - a failed save shows the error, and the field is sent again with the next change;
  - unsent text, and a field whose save failed, are sent when the page closes, including the trip to Google.
- Write with AI:
  - with nothing typed it doesn't ask;
  - it fills the title, description and chips, and doesn't PATCH them again (F4 saved them);
  - with text typed it asks first: No sends nothing and keeps the text, and OK replaces it;
  - a failed job and a refused start each show their reason;
  - polling G1 stops when the page closes (fake clock).
- Outdated: the notice, "Or render it again" (to the render page), and Publish disabled until "Publish this version anyway" is ticked; `confirmOutdated: true` is then sent. A `render_outdated` refusal shows the same confirm with guidance, and the next try sends `confirmOutdated`.
- F5 refusals:
  - each try sends a new Idempotency-Key;
  - `not_connected` and `reconnect_required` show their guidance, A9 is fetched again, and the channel row changes;
  - `already_running` and `not_ready` show their message and leave Publish enabled;
  - a 400 shows the field's reason;
  - while a new render runs, the "publish this video now" note shows.

**Tracker and live view (screen 20)**
- The tracker replaces the form, and its heading takes focus. It polls `/posts/post1` every 3 s (fake clock) through Queued, Uploading, Processing and Published:
  - `aria-current="step"` moves along;
  - the sr-only status text follows;
  - finished steps show ✓;
  - the progress bar follows `progress`;
  - the "You can leave this page" note shows.
- On `published` the live view replaces the tracker, its heading takes focus, and polling stops. A Reel that is being published opens straight on the tracker.
- Failed post on the same render: the reason, "Press Try again; it continues where it stopped", and Try again sends F5 again to the same render. Failed post on an older render: "This video is a newer render", and Publish now goes to the new render.
- Live view:
  - `role=status`, the post title, and the `privateUntilVerified` note only when it is true;
  - Open on YouTube links to `url` and opens a new tab;
  - Create another Reel goes to `/app/new`, and Back to the dashboard to `/app`;
  - Copy link shows "Link copied" when copying works, and "Copy failed; select the link" when it is refused or there is no clipboard;
  - Publish again appears only when there is a newer render; it opens the form with the new video, publishes `/renders/r2/publish`, and goes back to the tracker.

**Connect results (screens 21–22)**
- `connected`: the dialog names the channel. The result parameters (`connected`, `platform`) leave the address and the others stay (`?tab=x`). A9 is fetched again. OK closes the dialog for good.
- `denied`, `missing_permission`, `failed`, an unknown reason (including `toString`, which is inherited by every object), an empty reason and no reason at all each show their text. The parameters leave the address.
- `no_channel` links to `https://www.youtube.com/create_channel` in a new tab.
- The dialog is labelled by its title and described by its text.

**Onboarding step 3**
- The heading, the explanation and the testers note. The stepper reads Niche, Voice, YouTube (no Instagram). The document title is "YouTube · ReelForge setup".
- Connect YouTube calls F1 with `returnTo=/onboarding?step=3`. Connect, Skip and Back stay busy after that. A refused connect shows its reason. Reconnect YouTube shows for an expired connection.
- `?connected=youtube` shows "Connected as" the channel, then the address becomes `/onboarding?step=3`, and the message stays (it is read once). An already connected user sees "Connected:" and the address is not rewritten.
- Each `reason` (including an unknown one) shows its text and Try again, the address is cleaned, and the text stays. `no_channel` links to creating a channel.
- Skip for now and Finish both send `PATCH /me {onboardingCompleted: true}` and open `/app`. A failed finish shows its reason. Back goes to step 2.

**Mocks**
- `/connections/:platform/connect`:
  - 401 when signed out, 403 when the email isn't verified, 404 `not_available` for Instagram;
  - the answer has only `authorizeUrl`, which returns to `returnTo` with `connected=youtube` and keeps `returnTo`'s own query;
  - an unsafe or missing `returnTo` goes to `/app/settings`;
  - `/me` then shows the channel in the Connection shape, and other users are not affected.
- `db`: a database saved before F7 (no `youtube` key) still loads, and every user gets `youtube: null`. The seed has no channel. A stored channel belongs to its user only, and the older `instagram` field is kept.

### Not covered yet (deliberately)
- Browser runs (Playwright, mock or real backend): not run, because the machine is low on memory and the rules for this round say unit and component tests only. A real `<video>` playing, real `<dialog>` focus trapping, and the real Google round trip need a browser.
- The Stitch comparison (screens 12, 13, 20–22): the spec says it is still to check.
- Settings' YouTube row (F8) and the dashboard and library covers (`thumbnailUrl` on cards): unchanged in this round.
- Two autosave PATCHes in flight at the same time arriving out of order (not specified for this form).

## 3. Bugs

| Bug | Severity | Area |
|---|---|---|
| BUG-080 | Medium | Try again after a failed post forgets the chosen visibility: a Private Short is retried as Public |
| BUG-081 | Medium | `already_posted` doesn't link to the Short when the page didn't already know about the post |
| BUG-082 | Low | The editor shows `already_running` as raw text instead of opening the render page when this Reel is already rendering |
| BUG-083 | Low | A hashtag still in the box that matches a chip is sent twice |
| BUG-084 | Low (mock) | The mock F1 keeps an earlier `connection_error` and `reason` in `returnTo`; the real API removes them |
| BUG-085 | Polish (observation, no failing test) | The render page polls the whole project every 2 s as well as E2 every 3 s |

### BUG-080 Medium: Try again after a failed post forgets the chosen visibility

- **Test:** `frontend/src/features/publish/PublishPage.test.tsx`, "Publish: the tracker (F6) and "Your Short is live" > Try again after a failed post keeps the visibility that was chosen (Private stays Private)".
- **Steps:**
  1. Open the publish page with a connected channel.
  2. Choose **Private**, then press **Publish now**. F5 is sent with `visibility: "private"`.
  3. F6 reports `failed`, for example with `upload_failed`.
  4. The form comes back with **Try again**.
- **Expected:** Private is still selected, and Try again sends `visibility: "private"`.
- **Actual:** Public is selected again, and Try again sends `visibility: "public"`. PUBLISHING_YOUTUBE F5 says a retry continues the earlier upload, but if the video never reached YouTube the new metadata is used. A video the creator wanted private can then go out public.
- **Cause:** `PublishForm` is unmounted while the tracker shows and mounted again after the failure. `visibility` is local state, `useState<Visibility>('public')`, so it resets. The title, description and hashtags survive only because they are autosaved. The form could start from `failed.visibility` (the Post carries it).

### BUG-081 Medium: `already_posted` doesn't link to the Short

- **Test:** `PublishPage.test.tsx`, "Publish: F5 refusals and their guidance > already_posted links to the Short that is already on YouTube".
- **Spec:** SCREENS_F7 3: "`already_posted` links to the Short"; FIX_LOG QF7-003.
- **Steps:**
  1. Open the publish page of a Reel whose loaded `latestPost` is null. This happens when it was published from another tab, or when the page is stale.
  2. Press **Publish now**. F5 answers `409 already_posted`, and the project now has a published `latestPost` with a `url`.
- **Expected:** the message, with **Open it on YouTube** linking to the Short.
- **Actual:** only the message, with no link.
- **Cause:** the link is `postedUrl: code === 'already_posted' ? latestPostUrl : null`. `latestPostUrl` is read when the error arrives, from the post the page already follows, and is only set for a published post. That is exactly the case where the form isn't shown. Invalidating the project afterwards doesn't help: `postId` was fixed by `useState(p.latestPost?.id)`, and `problem` already holds `null`. Related: after **Publish again** on a newer render, an `already_posted` for the new render would link to the *old* render's Short, because `latestPostUrl` is the old post's URL.

### BUG-082 Low: the editor doesn't open the render page on `already_running` for this Reel

- **Test:** `frontend/src/features/editor/ReelEditorPage.test.tsx`, "Reel editor F7: Render and Publish … > already_running because this Reel is already rendering (started from another tab) opens the render page".
- **Spec:** SCREENS_F7 1: "`already_running` opens the render page".
- **Steps:**
  1. Open the editor of a `ready` Reel.
  2. Start a render of the same Reel elsewhere, for example in another tab.
  3. Press **Render** and confirm. E1 answers `409 already_running`.
- **Expected:** the render page opens, because this Reel is rendering.
- **Actual:** "This Reel is already rendering." shows as an error and the editor stays. The project isn't fetched again, so the header keeps offering Render.
- **Cause:** `render()` navigates only when `project.data?.status === 'rendering'`. That value comes from the render where the click handler was created. In that render the status can't be `rendering`, because then the button would read "Rendering…". The check needs fresh data, for example `refetch()` the project or E3 after the refusal. The other-Reel case (R-4) should still stay, and it does (tested).

### BUG-083 Low: a typed hashtag that matches a chip is sent twice

- **Test:** `PublishPage.test.tsx`, "Publish: title, description and hashtags > a hashtag still in the box is sent with Publish, once".
- **Steps:**
  1. Open the publish page with the chip `#shorts`.
  2. Type `Shorts` in the hashtag box, without pressing Enter.
  3. Press **Publish now**.
- **Expected:** `hashtags: ["shorts"]`. The chips ignore duplicates in any letter case.
- **Actual:** `hashtags: ["shorts", "Shorts"]`.
- **Cause:** `submit()` builds the body as `[...tags, cleanTag(tagDraft)]` without the duplicate check and limit that `addTag` applies.

### BUG-084 Low (mock): the mock F1 keeps an earlier result in `returnTo`

- **Test:** `frontend/src/mocks/handlers.test.ts`, "F1 GET /connections/:platform/connect (YouTube, F7 mock) > drops an earlier result left in returnTo, as the real API does …".
- **Steps:** call the mock `GET /connections/youtube/connect?returnTo=/onboarding?step=3&connection_error=youtube&reason=denied&platform=youtube`.
- **Expected:** `/onboarding?step=3&connected=youtube`, because the backend strips `connected`, `connection_error`, `platform`, `reason` and `instagram` (`connections.py` `_OUR_PARAMS`).
- **Actual:** `/onboarding?step=3&connection_error=youtube&platform=youtube&connected=youtube`. The address then carries both a success and a failure. The publish dialog picks `connected`, but the mock no longer behaves like the API.
- **Cause:** the mock reuses `safeReturnTo` from the Instagram mock, which removes only `instagram` and `reason`.

### BUG-085 Polish (observation): the render page still polls the project every 2 s

- **No failing test** (the spec doesn't forbid it). While a render runs, `useRender` polls E2 every 3 s as QF7-011 asked. `useProject` also keeps polling `GET /projects/:id` every 2 s, because `isBusy` is true for `rendering`. QA's finding was about the project poll *instead of* E2. Now both run. Worth a decision, not a fix tonight.

### Other observations (not bugs)
- When `postTitle` is empty, the first autosave after opening the page stores the Reel's title as `postTitle` (the field starts filled), with no typing. Harmless since QF7-001 (a post-text PATCH isn't an edit), but a later rename of the Reel won't reach the post title.
- Onboarding shows the connect result inline (a notice, or the "YouTube connected" heading) rather than the dialog SCREENS_F7 5 describes for "any page". The content and the read-once rule match. Check against Stitch screens 21–22.
- "No render yet" shows a card with **Open the editor** instead of going back to the editor by itself. That is a reasonable reading of "Goes back to the editor with a note".

## 4. Flaky or skipped
- None skipped, and no `it.todo`. The F7 files were run twice on their own, and the whole suite three times. The same 5 failures came up every time.
- **Load flakes in one full run (not new tests, not bugs):** in the second of three full runs, two more tests failed: "New Reel: the prompt box plays the narrator sample and stops it on a second click" (`NewReelPage.test.tsx`) and "Reel editor: header, player and states > shows the title, status, credits, the length, the player with the timeline and Render (F7)" (`ReelEditorPage.test.tsx`). Both pass when their files are run alone, and both passed in the other two full runs. They look like waits that run out of time when the machine is short of memory. Worth watching. Polling tests use fake timers (`shouldAdvanceTime`). Autosave tests wait for the 800 ms debounce with `waitFor` (3 s timeout), not fixed sleeps. The only fixed waits are short ones that check something did *not* happen (no PATCH, no second E1).
