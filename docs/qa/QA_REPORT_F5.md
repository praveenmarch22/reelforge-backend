# ReelForge: QA report for F5 (New Reel and Script editor)

| | |
|---|---|
| Date | 2026-11-26 |
| Milestone | F5: New Reel (`/app/new`) and Script editor (`/app/p/:id/script`) |
| Spec | [SCREENS_F5_F6.md](../SCREENS_F5_F6.md) sections 1, 2 and 4, with the PRD, API design (B1, B3–B5, B8–B11, C1–C6, G1, H1) and UI review |
| Build tested | `frontend/` working tree on 2026-11-26 (F5 uncommitted on top of `d743ebe`) |
| Backend | Isolated copy of `backend` HEAD as compose project `rfqa5` (API on 5300), all providers `mock`, rate limits off; Vite `--mode real` on 5175 proxying to it. Torn down afterwards |
| Browser | Playwright Chromium, 1280 × 900 and 375 × 800 |

## Verdict: fail

The main flow works: ideas, Write script, the script loads, costs and balance come from H1 and B10, Generate sends an `Idempotency-Key`. But autosave loses text. Typing that continues while a save is in flight is overwritten by the server's answer, and the overwritten words are gone once the creator types again (QF5-001, reproduced). Leaving the page within 0.8 s of typing also drops the edit (QF5-002, reproduced). The Script editor is the place where creators type the most, so these come first. A failed script rewrite says nothing at all (QF5-003), and on a phone every scene card is cut off on the right (QF5-004).

| Severity | Count | Ids |
|---|---|---|
| Blocker | 1 | QF5-001 |
| Major | 4 | QF5-002 to QF5-005 |
| Minor | 9 | QF5-006 to QF5-014 |
| Polish | 2 | QF5-015, QF5-016 |
| Questions | 3 | Section 4 |

**Verified column:** *Run* means reproduced in the browser against the isolated real backend. *Read* means concluded from the code only. Slow networks were simulated by delaying requests in Playwright (`page.route`). Credit balances were set directly in the QA database.

**Environment notes**

- The earlier QA attempt left compose project `qaf5` (ports 5300, 54330, 63800, 9300, 8035) and a Vite server on 5373 using `scratchpad/vite-qa`. I removed both.
- Compose project **`rff5`** (API on 5200, from `scratchpad/f5stack`) is still running. It is not QA-named, so I left it alone. If it is not the developer's, remove it with `docker compose -p rff5 down -v`.
- `tsc --noEmit` fails on one line in `src/features/create/NewReelPage.test.tsx` (`exact` passed to `getByRole`). That file is the Tester's work in progress, so it is not counted here. The product code type-checks.

---

## 1. Findings

### QF5-001 Blocker: text typed while a scene is saving is reverted, then lost

| | |
|---|---|
| Where | `src/features/create/SceneCard.tsx` lines 53–59 (server text wins when not dirty) and 62–73 (`finally { dirty.current = false }`) |
| Requirement | SCREENS_F5_F6 §2 Scene card: "narration (editable text area, autosaved)"; PRD S5 |
| Verified | Run |

**Steps.** Open a script and delay `PATCH /scenes/:id` by 1.5 s (a slow phone connection). In scene 1, type ` AAA`. Pause 1.2 s, which starts a save. Type ` BBB`. Wait until the first save answers, then type ` CCC`.

**Expected.** The box and the server both end with ` AAA BBB CCC`.

**Actual.** When the first save answers, the box jumps back to ` AAA`. ` BBB` disappears and the cursor goes to the end. Typing ` CCC` replaces the pending ` BBB` save. Final box and server: ` AAA CCC`. The words ` BBB` are lost.

**Cause.** `save()` clears `dirty` when *any* save finishes, even though newer keystrokes are still waiting on the 800 ms timer. `useUpdateScene.onSuccess` then puts the older server text into the cache. The effect sees `dirty === false` and overwrites the box with it.

**Why it matters.** This happens to anyone who pauses for about a second and then keeps typing on a connection with a few hundred ms of delay, which is normal on mobile. The developer's local run has about 20 ms of delay, so it hid the problem.

**Fix.** Clear `dirty` only when nothing newer is pending: no timer is set and the saved text equals the current text. Better still, never copy server text into a field that has focus or unsaved changes. Keep a per-field "last sent" value and ignore answers for older values.

### QF5-002 Major: leaving the page right after typing drops the edit; Generate can run before the save

| | |
|---|---|
| Where | `SceneCard.tsx` line 60 (`useEffect(() => () => clearTimeout(timer.current), [])`); `ScriptEditorPage.tsx` `onGenerate` |
| Requirement | SCREENS_F5_F6 §2 (autosaved); PRD A-4 ("Autosave first") |
| Verified | Run (browser back); Read (tab close, Generate) |

**Steps.** In scene 2, type ` LEAVE` and press the browser's Back button within 0.8 s.

**Expected.** The edit is saved.

**Actual.** It is not saved: the server still has the old narration. When the component unmounts, the pending timer is cleared instead of being sent. Back, Alt+Left and in-app navigation without a blur all lose the edit. Closing the tab also loses it, because there is no `beforeunload` guard while a save is pending or in flight.

**Related (Read).** Clicking **Generate Reel** or **Rewrite** right after typing sends the blur-triggered PATCH and the B11/C6 request at the same time. The server can start generating from the old narration. It then cancels that voice and marks it `outdated` (API C7), so the creator sees an outdated scene they did not expect.

**Fix.** On unmount, send the pending save instead of dropping it. Warn on `beforeunload` while a save is pending or in flight. Before starting Generate or Rewrite, wait for any pending save to finish.

### QF5-003 Major: a failed script shows no reason, and a failed "Rewrite whole script" shows nothing at all

| | |
|---|---|
| Where | `ScriptEditorPage.tsx` lines 172–183 and the "Rewrite whole script" panel |
| Requirement | SCREENS_F5_F6 §2 "While writing": "a failed script shows the reason and **Try again**"; PRD S-1, S-4 (neutral message); API B3, B8 |
| Verified | Run (mock LLM `[refuse]`) |

**Steps.**

1. **First script.** Create a Reel whose topic contains `[refuse]`. The editor shows "The script couldn't be written. Your credit was refunded. Try again". The job's `content_refused` message never appears. With a refusal, **Try again** repeats the same refused topic, and the topic can't be changed on this screen.
2. **Rewrite whole script (B8).** Open **Rewrite whole script**, enter `[refuse] more` and confirm. "Writing your script…" appears, then the old scenes come back unchanged with no message. G2 shows the job as `script failed content_refused`. The creator cannot tell whether anything happened, or that the credit was refunded.

**Cause.** The page never reads the script job. B3 and B8 return the job, and G2 lists it, but neither is used. A failed B8 puts the project back in its earlier status with its scenes, so the page has nothing to show.

**Fix.** Keep the script job id from the B3 and B8 responses, or read the latest `script` job from G2 when the project is a draft with no scenes or leaves `scripting`. Show `job.error.message`, and for B8 add "Your script wasn't changed; the credit was refunded."

### QF5-004 Major: at phone width the Script editor's scene cards are cut off and their actions are off-screen

| | |
|---|---|
| Where | `ScriptEditorPage.tsx` line 154 (`grid` with no single-column definition below `lg`); `SceneCard.tsx` header (`flex`, no wrap) |
| Requirement | PRD NFR "usable on phone width"; PRD §2 ("the web app should still work on a phone browser") |
| Verified | Run (375 × 800) |

**Actual.** The content area is 303 px wide, but each scene header is 379 px wide and cannot shrink, so the column is 437 px. Every card is clipped on the right: the narration is cut mid-word, and **Rewrite** and the **⋯** menu (Move, Duplicate, Delete) are off-screen. They can only be reached by scrolling the page sideways. New Reel overflows a little too (416 px against 303 px; the step header is the widest part).

**Fix.** Use `grid-cols-1` (or `min-w-0` on the grid items) below `lg`, let the card header wrap, and let the step header wrap or shrink.

### QF5-005 Major: New Reel field errors other than topic and script are not shown; the button just stops

| | |
|---|---|
| Where | `NewReelPage.tsx` lines 91 and 228 (the Notice is hidden for `validation_error`; only `fields.topic` and `fields.script` are shown); line 43 (`mode` taken from the URL without checking) |
| Requirement | SCREENS_F5_F6 §1 Errors: "field errors show under their field" |
| Verified | Run |

**Steps.** Open `/app/new?mode=bogus`, type a topic and press **Write script**.

**Expected.** A message.

**Actual.** The server answers `400` with `fields.mode`. Nothing is shown: no alert, and the error line under the box is empty. Neither Visual style chip is selected.

The same silence follows any `fields.voiceId`. B3 refuses with "That voice isn't available" when the account's default voice is no longer in D3, and the New Reel screen sends that default. In that case a creator cannot create a Reel and is told nothing.

**Fix.** Show every returned field error under its control, and put any field without a control in the Notice. Accept `mode` from the URL only if it is `faceless` or `full_ai`.

### QF5-006 Minor: no warning for scenes under 5 seconds

| | |
|---|---|
| Where | `SceneCard.tsx` lines 101–102 and 247–250 (only "over 15 s" and "empty") |
| Requirement | SCREENS_F5_F6 §2 Scene card: "a warning when the scene is over 15 s or under 5 s"; PRD S-3 |
| Verified | Run: a 4-scene script with every scene at about 3 s, 12.8 s in total, showed no warning |

**Fix.** Warn under 5 s (the backend's `TOO_SHORT_SECONDS`).

### QF5-007 Minor: the top bar with credits and Instagram status is missing on both screens

| | |
|---|---|
| Where | `NewReelPage.tsx`, `ScriptEditorPage.tsx` (neither uses `PageHeader`) |
| Requirement | SCREENS_F5_F6 intro: "All three screens share the app shell (sidebar, top bar with the credit balance and Instagram status)"; F1–F4 pages use `PageHeader` |
| Verified | Run |

**Fix.** Use `PageHeader`, or show the `CreditsPill` and `InstagramPill` row above the step header.

### QF5-008 Minor: the out-of-credits message differs on each screen and leaves out the reset date

| | |
|---|---|
| Where | `SceneCard.tsx` line 95; `ScriptEditorPage.tsx` `errorText`; `NewReelPage.tsx` line 231 |
| Requirement | SCREENS_F5_F6 §4 ("A 402 anywhere opens the out-of-credits message"; "Costs are read from GET /credits, never hard-coded"); PRD CR4 flow ("how many credits are needed, how many are left, and the reset date") |
| Verified | Run, with the balance set to 0 |

**Actual.**

- **Scene Rewrite:** "This needs 1 credits; you have 0." This is the raw server text, with no reset date.
- **Generate:** "This needs 2 credits and you have 0. Credits come back on the 1st of the month."
- **New Reel:** "You need 1 credit … you have 0. Credits come back on the 1st of the month." It uses the H1 cost and balance, not `details.needed` and `details.balance`, and falls back to a hard-coded `cost ?? 1`.

"The 1st of the month" is fixed text; `resetsAt` (H1, A9) is not used.

**Fix.** Use one shared out-of-credits notice that takes `details` and `resetsAt`. F8 will replace it with the modal.

### QF5-009 Minor: the scene menu is hard to use from the keyboard; focus is lost and moves are not announced

| | |
|---|---|
| Where | `SceneCard.tsx` lines 130–168 |
| Requirement | SCREENS_F5_F6 §2 Reorder: "move up and down from the menu (keyboard)"; UI review accessibility |
| Verified | Run |

**Actual.**

- Escape does not close the menu, and it closes only on `mouseleave`.
- Focus stays on the trigger. `role="menu"` is set, but there is no arrow-key handling.
- After Move up, Move down, Duplicate or Delete, focus drops to `<body>`. A keyboard user starts again from the top of the page.
- Nothing announces the new position.
- The hook choices under scene 1 also disappear on click and drop focus.

**Fix.**

- Close on Escape and on outside click, and return focus to the trigger.
- Either drop `role="menu"` (a plain list of buttons) or add arrow-key handling.
- After a move, focus the moved scene's menu button and announce "Scene 2 moved to position 3" in a polite live region.

### QF5-010 Minor: two `<main>` landmarks on the Script editor

| | |
|---|---|
| Where | `ScriptEditorPage.tsx` line 155 (inside AppShell's `<main>`) |
| Requirement | Accessibility (one main landmark per page) |
| Verified | Run (`main` count = 2) |

**Fix.** Use `<section aria-label="Script storyboard">`.

### QF5-011 Minor: `waitForJob` polls twice as often as specified, never gives up, and gives up on a single network blip

| | |
|---|---|
| Where | `src/api/projects.ts` `waitForJob` |
| Requirement | API §2 "Slow work: the client polls … every 2 s" |
| Verified | Read |

**Actual.**

- It polls every 1 s (`POLL_MS / 2`).
- The `signal` parameter is never passed by any caller, so polling continues after the page is left.
- There is no overall time limit. If a job is never closed, "Thinking of ideas…" or "Rewriting…" spins forever.
- One failed `GET /jobs/:id` (network) throws. For a scene Rewrite, the creator is told it failed while the paid job carries on and later changes the scene.

**Fix.**

- Poll every 2 s.
- Pass an AbortSignal from the component and abort it on unmount.
- Retry transient errors a few times.
- Stop with a clear message after a generous limit.

### QF5-012 Minor: Get ideas can send text over B1's limits and gets a generic error

| | |
|---|---|
| Where | `NewReelPage.tsx` `askForIdeas` |
| Requirement | API B1 (`niche` ≤ 120 and `keywords` ≤ 200, per `IdeasBody`); SCREENS_F5_F6 §1 |
| Verified | Read (frontend and backend schema) |

**Actual.** The topic box allows 300 characters. With a niche set, the topic goes in as `keywords` (max 200). Without one, it goes in as `niche` (max 120). A long topic gets `400`, shown as "Some details need another look.", which does not say what to change.

**Fix.** Trim to the limit, or show the field message.

### QF5-013 Minor: the title field is reset by any server title change

| | |
|---|---|
| Where | `ScriptEditorPage.tsx` line 156 (`<TitleField key={p.title} …>`) |
| Requirement | SCREENS_F5_F6 §2 Title "Editable inline" |
| Verified | Read |

**Actual.** While the script is being written, the title is empty and editable, and the page polls. When the AI names the project, `key` changes and the field remounts, wiping anything the creator was typing. The empty title also has no placeholder, as seen on the failed-script screen.

**Fix.** Key on `p.id`, and sync from the server only when the field is not focused or dirty. Add a placeholder such as "Untitled Reel".

### QF5-014 Minor: the topic box accepts line breaks that B3 refuses

| | |
|---|---|
| Where | `NewReelPage.tsx` textarea (topic mode) |
| Requirement | API B3 `topic` (one line: the server says "Remove line breaks and other special characters") |
| Verified | Run |

**Actual.** Enter adds a line break, and pasted multi-line topics keep theirs. The creator only finds out after pressing **Write script**. The field error does appear under the box, so there is a workaround.

**Fix.** In topic mode, block Enter (or make it submit) and replace pasted line breaks with spaces.

### QF5-015 Polish: breadcrumb wording

| | |
|---|---|
| Where | `ScriptEditorPage.tsx` lines 140–151 |
| Requirement | SCREENS_F5_F6 §2 Layout: "breadcrumb (Studio / New Reel / Script editor)" |
| Verified | Run |

**Actual.** "Projects / Script". **Fix:** use the spec's wording, or record the change in the spec.

### QF5-016 Polish: loading and result states are not announced

| | |
|---|---|
| Where | `NewReelPage.tsx` lines 162–185; `SceneCard.tsx` line 124 |
| Requirement | UI review accessibility |
| Verified | Read |

**Actual.** "Thinking of ideas…" and the arrival of the ideas are not in a live region. Each scene's live region says "Saving…" and then goes empty, never "Saved". **Fix:** use `role="status"` for the ideas area, and briefly announce "Saved".

---

## 2. What passed

| Check | How |
|---|---|
| `npm run dev:real` style run (`--mode real`, `API_PROXY_TARGET`): `.env.real` turns MSW off and `/api` goes to the real backend; sign-in cookies work through the proxy | Run |
| New Reel footer reads cost and balance from H1: "1 credit to write the script / 100 remaining" | Run |
| Get ideas (B1, G1 polling) shows 5 cards with title, hook and angle; **Use this** fills the topic | Run |
| **Write script** creates the project (B3) and opens `/app/p/:id/script`; the scenes appear when the script is ready | Run |
| Scene card: number "Scene 01", time range, narration, mood menu with the 10 PRD moods, on-screen text chip, word count and estimated seconds, over-15 s warning text | Run (layout), Read (warning) |
| Autosave fires 0.8 s after typing stops and on blur (PATCH C2), when no save is already in flight | Run |
| Right panel: estimated length against target with bar, words, scenes, narrator (D3), "Generating costs" from B10, balance from B10 | Run |
| Generate sends `Idempotency-Key` (B11); 402 on Generate shows needed and balance from `details` | Run |
| Topic field error from the server shows under the box (`fields.topic`) | Run |
| Duplicate (C4) from the menu works from the keyboard | Run |
| A failed first script shows "Try again", which calls B8 as API B3 describes | Run |
| Polling: `useProject` refetches every 2 s while `scripting` or while `activeJobs` is not empty, then stops | Read |
| Reorder sends every id in the new order (C5) and shows the order at once; Move up/down disabled at the ends; Delete disabled for the last scene | Read |
| Rewrite (C6) shows its cost from H1 and sends default instructions when the box is empty (C6 requires them) | Read |
| Text limits match the backend: topic 300, script 5000, narration 1500, on-screen text 80, title 120, instructions 200, 30 scenes | Read |
| Chip moved to `components/ui/Chip.tsx`; the dashboard is unchanged apart from the import | Read |
| 409 and 503 on New Reel show the API's message next to the button; 404 on the Script editor goes to Projects | Read |

**Not tested:** real Gemini (not allowed), Safari and Firefox, mouse drag-and-drop, voice sample playback, the "Writing your script…" card (the mock writes too fast to see it; the logic was read), 409 and 503 in the browser.

---

## 3. Out of scope (not defects)

- `/app/p/:id/edit` is still "Coming soon". Generate navigates there; the Reel editor is F6.
- The full out-of-credits modal is F8 (SCREENS_F5_F6 §4).
- Hook suggestions (B9) are P1 but were built. They are reviewed above only for accessibility.
- Stitch items listed as left out (phone preview, version history, pacing score and the rest) are correctly absent.

---

## 4. Questions for the product owner

1. **Idempotency.** SCREENS_F5_F6 §4 says "Every action that starts work sends an `Idempotency-Key`". The API design marks only B11, C7 and C8 as idempotent, and the server accepts the header only there. B3 (Write script, 1 credit), B8, C6, B1 and B9 send no key (Run: request log). Which is the rule? If a retried B3 must not charge twice, B3 needs to accept the key.
2. **Too-long scripts.** PRD S-2 asks to "offer **Shorten with AI**" when the script is over the target. SCREENS_F5_F6 §2 does not list it, and the screen only says "trim a scene or two". Is **Rewrite whole script** with an instruction the agreed replacement?
3. **Refused first script.** When the first script is refused (`content_refused`), **Try again** repeats B8 with the same topic, which cannot be edited on the Script editor. Should the failed state offer "Change the topic", back to New Reel with the topic filled in, or should B8 accept a new topic?

---

## 5. Recheck (2026-11-26, after [FIX_LOG_F5.md](FIX_LOG_F5.md))

**Verdict: fail.** The original blocker is fixed: typing during a slow save no longer loses text. But the new autosave queue has a regression that leaves text unsaved while the screen says "Saved" (QF5-017). The failed whole-script rewrite is still silent (QF5-003 not fixed). New Reel still overflows at phone width (QF5-004 partly fixed).

**Setup.** Same as before: a fresh isolated copy of `backend` HEAD (`rfqa5`, all providers mock, rate limits off), Vite `--mode real` on 5175, Chromium. Delays and failures were injected with `page.route`. Both were torn down afterwards; the main stack and port 5173 were not touched. Compose project `rff5` is still running and is not mine.

### Status of the original findings

| Id | Status | Evidence |
|---|---|---|
| QF5-001 | **Fixed** | Run, saves delayed 1.5 s: ` AAA`, pause, ` BBB` while saving, ` CCC` after it answered. The box kept ` AAA BBB` throughout. Box and server both ended ` AAA BBB CCC`. "Saving…" cleared afterwards (no stuck state) |
| QF5-002 | **Fixed** | Run. In-app Back within 0.8 s: the edit was sent and saved. Closing the tab within 0.8 s: a `beforeunload` warning appears. Generate right after typing (saves delayed 1.5 s): the PATCH was answered (23.48 s) before B11 was sent (23.51 s), and the server had the new text |
| QF5-003 | **Partly fixed** | First script refused: "The script couldn't be written: This was refused by the AI's content rules. Try a different wording. Your credit was refunded. Try again or change the topic." **Change the topic** opens New Reel with topic, mode and length filled in (Run). Failed **Rewrite whole script**: still no message, 3 of 3 runs (QF5-018) |
| QF5-004 | **Partly fixed** | Script editor at 375 px: 303/303, no overflow; card header wraps, Rewrite and ⋯ visible (Run). New Reel at 375 px still overflows (QF5-019) |
| QF5-005 | **Fixed** | Run, with `voiceId` forced to an unknown voice: "Narrator: That voice isn't available". `?mode=bogus` is ignored (Read) |
| QF5-006 | **Answered** | The spec now says under 2 s, and the warning is there (Read). See question 4 |
| QF5-007 | **Fixed** | Credits pill in the top bar on both screens (Run). At 375 px only the credits pill showed in the screenshot; I did not check whether the Instagram pill is hidden on purpose at that width |
| QF5-008 | **Fixed** | Run, balance 0: "This needs 1 credit and you have 0. Your credits renew on 1 December." The date comes from the 402's `details.resetsAt`, which the backend sends but API design §2 does not list (doc gap only) |
| QF5-009 | **Fixed** | Run. Opening the menu on scene 1 focuses "Move down". Escape closes it and returns focus to the trigger. After Move down, focus is on the moved scene's menu button and "Scene 1 moved to position 2." is announced |
| QF5-010 | **Fixed** | Run: one `main` |
| QF5-011 | **Mostly fixed** | Read. Polls every 2 s and retries 3 connection errors. `signal` is still never passed by any caller, so polling continues after the page is left. Minor; not re-raised |
| QF5-012 to QF5-014 | **Fixed** | Read (limits cut to 120/200; title synced only when not focused; line breaks replaced in `onChange`) |
| QF5-015 | **Answered** | The spec now says "Projects / Script" |
| QF5-016 | **Fixed** | Read: the ideas area and each scene's "Saving…"/"Saved" are `role="status"` |

### New findings

#### QF5-017 Major (regression): a failed save shows "Saved", is never retried, and leaving or Generate don't send it

| | |
|---|---|
| Where | `SceneCard.tsx` `useSceneAutosave` `flush`: the `finally` block advances `settled` and sets `'saved'` whether the save succeeded or failed |
| Requirement | SCREENS_F5_F6 §2 (autosaved); PRD A-4; QF5-002's fix |
| Verified | Run |

**Steps.** Make the first PATCH fail (network error), then type ` FAILED` in scene 1.

**Actual.**

- The header says **"Saved"** while the alert under the card says "Can't reach ReelForge…".
- 3 s later, and again after leaving the field, the server still has the old text, but the box shows ` FAILED`. The failed body is dropped, not queued again, and the edit counts as settled.
- Closing the tab gives **no** `beforeunload` warning, because the unsaved count was decremented.
- **Generate** would generate from the old text, because `flushAll` has nothing to send (Read).

The text stays unsaved until the creator types in that field again. This is the "error leaving a field unsaved forever" case.

**Fix.**

- On failure, put the body back in `pending`, unless a newer edit already replaced it.
- Do not advance `settled`, and keep the unsaved count.
- Show "Not saved" instead of "Saved".
- Retry on the next flush (blur, Generate, unmount) or after a short back-off.
- Have Generate refuse with a message if a flush still fails.

#### QF5-018 Major: a failed "Rewrite whole script" is still silent when it fails quickly (QF5-003 not fixed)

| | |
|---|---|
| Where | `ScriptEditorPage.tsx`: the jobs list (G2) is refetched only when the page has *seen* `scripting` turn into something else; `refresh()` in `api/projects.ts` does not invalidate `['project-jobs', id]` |
| Requirement | SCREENS_F5_F6 §2 "While writing"; API B8 |
| Verified | Run, 3 of 3 attempts with `[refuse]` |

**Trace.** B8 was answered 202 with job `2d68decc`. The project was refetched 300 ms later and was already `draft`, so the page never saw `scripting`, G2 was not refetched and nothing was shown.

In one attempt the page did see "Writing your script…" and G2 was refetched with the failed job first in the list, but no message appeared either. I could not explain that from the code.

With real providers a refusal or `provider_unavailable` usually takes over a second. That is often, but not always, longer than one poll.

**Fix.** Since the page has the B8 job id, wait for that job (`waitForJob(job.id)`) and show its error directly. Or invalidate `project-jobs` in `refresh()` and whenever the project's status changes.

#### QF5-019 Minor: New Reel still overflows at 375 px

| | |
|---|---|
| Where | `NewReelPage.tsx` Settings section (`grid … md:grid-cols-3` with `<fieldset>`s, which default to `min-width: min-content`); the step header also runs to the edge |
| Requirement | PRD NFR "usable on phone width" |
| Verified | Run |

**Actual.** The content area is 303 px; the Tone and Target length fieldsets reach 488 px. "Motivational" is cut off, and the settings panel scrolls sideways.

**Fix.** `grid-cols-1` below `md`, and `min-w-0` on the fieldsets.

#### QF5-020 Minor: Generate can be clicked twice while the pending save is being sent

| | |
|---|---|
| Where | `ScriptEditorPage.tsx` `onGenerate`: the button's `loading` follows only `generate.isPending`, not the `flushAll()` wait before it |
| Requirement | SCREENS_F5_F6 §4 (one Generate per click); PRD CR-3 |
| Verified | Run |

**Actual.** A second click during a slow save sent two B11 requests at the same moment. The server answered one 409 and one 202, so nothing was charged twice, but the 409 briefly sets an error.

**Fix.** Disable the button for the whole flush-then-generate sequence.

### Question added

4. **Short script.** PRD S-3 asks for a warning when the *whole script* is under 5 s. The spec change answered the per-scene warning (now under 2 s), but no script-level warning exists. Is S-3 still wanted on this screen, for example in the summary next to the target?

### Recheck counts

| Severity | Open | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 2 | QF5-017 (regression), QF5-018 (QF5-003 not fixed) |
| Minor | 2 | QF5-019 (rest of QF5-004), QF5-020 |
| Polish | 0 | |

---

## 6. Recheck 2 (2026-11-26, after "Round 3" in [FIX_LOG_F5.md](FIX_LOG_F5.md))

**Verdict: pass with issues.** Every major is fixed and checked in the browser. One new minor and one polish item remain; neither blocks F5.

**Setup.** Same as before: a fresh isolated `rfqa5` (all providers mock, rate limits off), Vite `--mode real` on 5175, Chromium. Failures and delays were injected with `page.route`. For the "leave while waiting" check, G1 was answered with a job that stays `running`. Both were torn down afterwards; the main stack and port 5173 were not touched. `rff5` is still running and is not mine.

### Status

| Id | Status | Evidence (all Run unless noted) |
|---|---|---|
| QF5-017 | **Fixed** | See the steps and results below |
| QF5-018 | **Fixed** | Three quick `[refuse]` whole-script rewrites in a row each showed "A new script couldn't be written: This was refused by the AI's content rules. Try a different wording. Your scenes are unchanged and the credit was refunded." |
| QF5-019 | **Fixed** | New Reel at 375 px: content 303/303, page 375; nothing past the edge |
| QF5-020 | **Fixed** | A double click and a third click on Generate during a 1.5 s save sent **one** B11, which opened the Reel editor |
| QF5-011 | **Fixed** | A scene rewrite and "Try another opening" with a job that never finishes: G1 every 2 s while waiting (3 polls in 4.5 s), **0** polls in the 7 s after leaving the page |
| Question 4 | **Answered** | "Very short: under 5 seconds" is in the summary (Read) |

**QF5-017 steps.** Make every PATCH fail, type ` F1` in scene 1, then:

1. **After the autosave:** the card says "Can't reach ReelForge…", and the header never says "Saved".
2. **Blur:** the save is sent again once.
3. **Generate, scene Rewrite and Rewrite whole script:** all three are refused with "Some changes aren't saved yet. Check your connection and try again." None of B11, C6 or B8 is sent.
4. **Closing the tab:** the `beforeunload` warning appears.
5. **Network back, then blur:** the next blur saves it. The server has ` F1`, the error clears, and Generate then works and opens the Reel editor.

On step 5: run straight after a dismissed `page.close` warning, the retry seemed not to land, so I reran it on its own and it worked. I treat the first result as a Playwright artefact, not a defect.

### New findings

#### QF5-021 Minor: leaving through an in-app link drops text whose save failed, without asking

| | |
|---|---|
| Where | `SceneCard.tsx` unmount cleanup (`flush().catch(ignore).finally(() => dirty() && markUnsaved(-1))`); no router-level guard |
| Requirement | PRD A-4 ("Autosave first"); SCREENS_F5_F6 §2 (autosaved) |
| Verified | Run |

**Steps.** Make PATCH fail and type ` NAV` in scene 2. The card shows the error. Then click **Projects** in the breadcrumb.

**Actual.** The page leaves at once, and the text is gone (the server still has the old narration). Closing the tab in the same state does warn. The creator did see the error first, so this is minor.

**Fix.** While `hasUnsaved()`, block in-app navigation and ask "Leave without saving?" (React Router `useBlocker`), as `beforeunload` does for the tab.

#### QF5-022 Polish: a save refused by the server is reported to Generate as a connection problem

| | |
|---|---|
| Where | `SceneCard.tsx` `flush` (the rejection text is fixed) |
| Requirement | SCREENS_F5_F6 §1/§2 error handling |
| Verified | Read |

**Actual.** If a save fails with a `400` (for example text the server refuses), the card shows the real field message. But Generate and the rewrites say "Some changes aren't saved yet. **Check your connection** and try again", which points the creator at the wrong cause.

**Fix.** Say "Some changes aren't saved yet: see the scene marked in red", or pass on the scene's error.

### Recheck 2 counts

| Severity | Open | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 0 | |
| Minor | 1 | QF5-021 |
| Polish | 1 | QF5-022 |
