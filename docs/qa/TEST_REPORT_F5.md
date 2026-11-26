# ReelForge: test report for F5 (New Reel and Script editor)

| | |
|---|---|
| Date | 2026-11-26 |
| Role | Tester (automated tests only; no product code changed) |
| Build tested | `frontend/` working tree, F5 uncommitted on top of d743ebe |
| Backend for the browser tests | A copy of `backend/` HEAD e6be03e (`git archive`, no `.env`), Compose project `rff5`: API 5200, Postgres 54340, Redis 63810, MinIO 9200/9201, Mailpit 8225, every provider `mock`, rate limits off, `WEB_ORIGIN=http://localhost:5174`. Web app: `vite --mode real --port 5174` with `API_PROXY_TARGET=http://127.0.0.1:5200` |
| Checked against | [SCREENS_F5_F6.md](../SCREENS_F5_F6.md) sections 1, 2 and 4; API design B1, B3–B5, B8–B11, C1–C6, G1, H1 |
| Related | [QA_REPORT_F5.md](QA_REPORT_F5.md) (written in parallel; overlaps are cross-referenced below) |

## 1. Results

| Suite | Command | Result |
|---|---|---|
| Typecheck | `npm run typecheck`, plus `npx tsc --noEmit -p e2e-real` | Pass, pass |
| Unit and component (all) | `npm test` | **491 tests: 477 passed, 14 failed**. The 424 tests from before F5 all pass. The 14 failures are new tests that find bugs BUG-045 to BUG-052 |
| F5 component tests alone | `npx vitest run src/features/create` | 67 tests: 53 passed, 14 failed (the same 14) |
| Browser, real backend | `npx playwright test -c playwright.real.config.ts` | **13 tests: 11 passed, 2 failed** in each of 3 runs (the typing-race test was added after run 1, which had 12: 11 passed, 1 failed). The failures are BUG-045 and BUG-046, every run |
| Browser, mock (existing) | `e2e/` suite on port 5176 (see 1.1) | 202 passed, 21 failed. 7 were load timeouts and passed when rerun alone. 14 asserted the placeholders F5 replaced; after updating them, the 3 affected specs pass (51 passed) |

### 1.1 Note on the mock suite (`npm run test:e2e`)

Port 5173 had a `vite --mode real` server running (not the mock), and that server was not to be touched. `npm run test:e2e` reuses whatever answers on 5173, so it would have run the mock-based tests against the real backend. The unchanged suite was run instead with a temporary config that starts the mock dev server on port 5176 (`vite --port 5176`). It imports `playwright.config.ts` and changes only the port; it was deleted afterwards.

| Run | Result |
|---|---|
| Full suite (run while the unit suite was also running) | 223 tests: 202 passed, 21 failed (26.5 min) |
| The 21 failures again, nothing else running | 7 passed. These were load timeouts (auth, phone, resilience); none fails on its own |
| The other 14 | Expected changes, not bugs. They asserted the "coming soon" placeholders that F5 replaced: the heading "New Reel" on `/app/new`, and the placeholder for `/app/new` and `/app/p/:id/script` in `shell.spec.ts` |
| After updating those assertions: `dashboard.spec.ts`, `projects.spec.ts`, `shell.spec.ts` | 51 passed, 0 failed |

Updated tests (expected value only):

- `e2e/dashboard.spec.ts` `newReelParams` and `e2e/projects.spec.ts` "New Reel opens the New Reel screen" now expect the built screen's heading "Craft your next Reel";
- `e2e/shell.spec.ts` no longer lists `/app/new` and `/app/p/:id/script` as placeholders (Reel editor, Render, Publish and Settings still are).

The full mock suite was not run a second time end to end. It takes about 25 minutes with port 5173 unavailable.

## 2. What was added

| File | What |
|---|---|
| `src/test/fakeApi.ts` | A `fetch` stand-in for component tests. It records every request (method, path, body, headers), and each test says how each endpoint answers. It also builds valid `Project`, `Scene`, `Job`, `Estimate` and H1 objects. The MSW handlers in `src/mocks` are not changed |
| `src/features/create/NewReelPage.test.tsx` | 28 tests |
| `src/features/create/ScriptEditorPage.test.tsx` | 39 tests |
| `playwright.real.config.ts` | Config for the real-backend browser tests. The header says how to start the stack, run the tests and tear down. It starts `vite --mode real` on 5174 itself, or reuses one |
| `e2e-real/global-setup.ts` | Stops at once, with a clear message, if the API or Mailpit is not reachable |
| `e2e-real/helpers.ts` | Each test signs up a new creator through the API, reads the verification link from Mailpit, verifies, and completes onboarding. Also: `setBalance` (psql in the test stack only), and `holdProjectFetches`, which keeps "Writing your script…" on screen long enough to check it; the data stays the backend's |
| `e2e-real/new-reel.spec.ts` | 6 tests |
| `e2e-real/script-editor.spec.ts` | 7 tests |
| `e2e-real/tsconfig.json`, `e2e-real/node.d.ts` | Type checking without adding `@types/node` |

A previous Tester attempt had left `fakeApi.ts` and the two component test files half-finished. They were reviewed and kept where they were sound, then finished. Changes:

- fixed sleeps replaced by fake timers;
- a `status` role check fixed (the loading spinner is a `status` too);
- a `getByRole(..., {exact})` call that did not type-check fixed;
- dead code removed;
- longer timeouts for the two slowest renders (30 scenes; duplicate and delete).

### Covered

- **New Reel (component):**
  - the topic limit (300) and the pasted-script limit (5000), with counts;
  - an empty topic or script is refused before sending, with `aria-invalid` and the error tied to the box through `aria-describedby`;
  - the B3 body for a topic, with tone, length, mode and voice;
  - a pasted script is sent as `script` with its paragraphs;
  - the account defaults for mode and narrator;
  - the voice sample plays and stops;
  - the query parameters `topic`, `mode`, `length` and `ideas=1`, including unknown values;
  - ideas: B1, G1 polling, "Use this", a failed job with Try again, and the niche falling back to the topic;
  - the cost and balance come from H1 (tested with a cost of 3, so it is not hard-coded);
  - 402, 409 and 503 messages;
  - field errors, for every B3 field;
  - the step header's `aria-current`.
- **Script editor (component):**
  - while scripting: progress shown, scenes locked, Generate and Rewrite disabled;
  - a failed script: the notice and Try again (B8);
  - an unknown project goes back to Projects;
  - the title: Enter saves, an empty title is put back;
  - narration autosave: debounced at 800 ms, "Saving…" shown, on blur, never twice, never empty;
  - typing during a save;
  - on-screen text and mood (10 moods);
  - each scene's time range, word count and seconds;
  - the over 15 s, under 5 s and empty-narration warnings;
  - save errors shown under the scene;
  - scene rewrite (C6): cost from H1, the job polled, success, a failed job, 402;
  - move up and down from the menu, with first and last limited;
  - reorder with the keyboard alone (Tab, Enter);
  - Escape closes the menu;
  - drag by the handle;
  - a reorder the server refuses is put back;
  - duplicate, and delete only after confirming; Delete disabled with one scene;
  - add a scene; disabled at 30;
  - hooks (B9): three openings, one click saves it to scene 1; a 409 is shown;
  - the panel: length against the target, over target, over 90 s, words, scenes, B10 credits and balance;
  - narrator change (B5) shows the new estimate;
  - Rewrite whole script (B8): confirm, cancel, instructions, then the scripting state;
  - Generate (B11): an `Idempotency-Key` sent, a new key for every click, and navigation to `/app/p/:id/edit`; 402, 503 and 409 shown;
  - Idempotency keys on B3, B8 and C6.
- **Real backend (browser):**
  - Dashboard → New Reel with topic, Full AI and 60 s in the URL. Tone and narrator chosen. "1 credit" and the balance read from H1. Write script → "Writing your script…" → 4 real scenes. The saved project has the chosen tone, mode, length and voice, and the balance went down by the script cost.
  - Dashboard Ideas (`ideas=1`).
  - Get ideas → 5 ideas → Use this → the script's title is the idea.
  - A pasted script keeps the creator's words, one scene per paragraph.
  - 300-character cap; empty topic; 402 with a balance of 0, and no project created.
  - A failed script (the mock model's `[fail]` word): the notice, the credit refunded, Try again sends B8, it fails again, nothing charged.
  - Title, narration (debounced, without leaving the field), on-screen text (on blur), mood and a long scene all survive a reload. The panel's words and length match B4.
  - Rewrite a scene with "Rewrite · 1 credit": new text, balance down by 1.
  - Duplicate (the copy is placed after the original) and delete with the confirmation dialog.
  - Move down with the keyboard only; drag scene 4 onto scene 1; add a scene. The order holds after a reload and matches the server.
  - 30 scenes: Add scene is disabled.
  - Hooks: the chosen opening is saved.
  - B10 credits and balance shown.
  - Narrator change saved and shown after a reload.
  - Rewrite whole script with instructions: the scenes are replaced and the title kept.
  - Generate sends an `Idempotency-Key`, `creditsReserved` equals the B10 estimate, and the Reel editor opens.
  - Generate with 0 credits shows "This needs N credits and you have 0." and stays.
  - Typing while a save is on its way.

### Not covered yet (deliberately)

- Leaving the page within 0.8 s of typing (QF5-002 in the QA report): not automated here.
- Phone widths, the missing top bar, two `<main>` landmarks, and announcements of loading states (QF5-004, 007, 010, 016): QA's findings, not automated.
- The voice sample in a real browser. With mock providers `previewUrl` is `""`, so the button is disabled. It is covered in a component test with a stubbed `Audio`.
- 409 and 503 from B3 and B11 on the real backend. Mock providers never produce them, so they are only in component tests.
- 429. Rate limits are off on the test stack.
- Network loss while polling jobs (QF5-011).
- Auto-scroll while dragging to a scene that is off-screen. The drag test uses a 1300-pixel-tall window so source and target are both visible. At 720 pixels the handle of scene 4 is below the fold, and Playwright's pointer cannot start a drag there. That is a test limit, not a product fault.
- The "Writing your script…" state after Rewrite whole script, in the real browser. The mock model finishes in about 0.6 s, so the state may never show. It is covered in a component test. The New Reel path is checked in the browser by holding the project fetches.
- The Reel editor (`/app/p/:id/edit`, F6): only the navigation to it is checked.

## 3. Bugs

Severity: High (loses work or blocks a main flow), Medium, Low.

### BUG-045 High: text typed while a scene is being saved disappears from the box (QF5-001)

**Steps (real backend).**
1. Open a script in the Script editor.
2. Make the save slow: the test holds the first `PATCH /scenes/:id`. In real life this is a slow connection.
3. In scene 1, type ` abc`. Wait about 1 s, so a save starts.
4. Type `def` while that save is still on its way.
5. Let the save answer.

**Expected.** The box keeps showing `… abcdef` throughout, and the server ends with `… abcdef`.

**Actual.**
- When the first save answers, the box jumps back to `… abc`, and `def` vanishes.
- If the creator stops typing, the pending 800 ms save brings `def` back a moment later.
- If the creator keeps typing, the next save sends the reverted text, and `def` is lost for good. QA reproduced this permanent loss.

**Reproduced by:**
- `src/features/create/ScriptEditorPage.test.tsx`, "Script editor: editing > keeps what is typed while an earlier save is still on its way". It expects `… abcd` and receives `… abc`.
- `e2e-real/script-editor.spec.ts`, "typing while an earlier autosave is on its way never loses characters". A frame-by-frame record of the box shows `… abc` after `def` was typed.

**Cause (best reading).** In `SceneCard.save()`, `finally { dirty.current = false }` runs when any save ends, even with newer keystrokes still waiting on the timer. `useUpdateScene` then writes the older server text into the cache. The `useEffect` that copies `scene.narration` into the box sees `dirty === false` and overwrites what the creator typed.

### BUG-046 Medium: the dashboard's Ideas button stays on "Thinking of ideas…" for good (development, StrictMode)

**Steps (real backend).**
1. Run the app with `npm run dev:real`.
2. Sign in as a creator with a niche.
3. On the dashboard, press **Ideas**.

**Expected.** The New Reel screen opens and shows 5 idea cards in about a second.

**Actual.**
- `POST /ideas` (202) is sent and `GET /jobs/:id` answers with the finished job, which holds the ideas.
- The screen still shows "Thinking of ideas…" indefinitely, and no further requests are made.
- Pressing **Get ideas** on the same screen works.
- A production build (`vite build --mode real` + `vite preview`) shows the ideas, so only development (StrictMode) is affected. That is the mode F5 is developed and checked in.

**Reproduced by:**
- `src/features/create/NewReelPage.test.tsx`, "New Reel: arriving from the dashboard > ideas=1 shows the ideas when the app runs in StrictMode with the user already loaded". The same flow without StrictMode passes, and so does "Get ideas works in StrictMode too (a click, not an effect)".
- `e2e-real/new-reel.spec.ts`, "ideas from the dashboard: asked for at once (ideas=1)".

**Cause (best reading).** `NewReelPage` calls `ideas.mutate()` from a `useEffect`, guarded by the `askedForIdeas` ref. `src/main.tsx` renders inside `<StrictMode>`, so in development the effect runs, the component is unmounted and mounted again, and the `useMutation` observer is detached from the mutation that is already running. The finished result never reaches the screen, and the ref stops a second request. Likely fix directions: ask from an event handler or a `useQuery` that is enabled by `ideas=1`, rather than calling `mutate` in an effect.

### BUG-047 Medium: New Reel swallows field errors other than topic and script (QF5-005)

**Steps.** On New Reel, send a request that B3 refuses on `voiceId`, `mode`, `targetSeconds` or `tone`. For example, open `/app/new?topic=x&mode=banana` (see BUG-048) and press **Write script**. A voice taken out of the curated list does the same.

**Expected.** The error appears next to the field, or at least as a message.

**Actual.** Nothing is shown. The button stops spinning and the creator is left guessing. On the real stack, B3 answers `400 {"fields": {"mode": "That value isn't allowed", "targetSeconds": "That value isn't allowed", "voiceId": "That voice isn't available"}}`.

**Reproduced by.** `src/features/create/NewReelPage.test.tsx`, "New Reel: cost and errors > a field error on voiceId / mode / targetSeconds / tone is shown, not swallowed" (4 cases).

**Cause.** `onError` stores `error.fields`, but only `fieldErrors.topic` and `fieldErrors.script` are displayed. The general error notice is hidden for every `validation_error`.

### BUG-048 Low: an unknown `mode` in the New Reel address is accepted

**Steps.** Open `/app/new?topic=x&mode=banana` (or `mode=FULL_AI`).

**Expected.** One of Faceless and Full AI is selected (the default), and a valid mode is sent. This is how an unknown `length` is already treated: it falls back to 30 s.

**Actual.** Neither chip is pressed, and `mode: "banana"` is sent, which B3 refuses. With BUG-047, nothing is shown.

**Reproduced by.** `src/features/create/NewReelPage.test.tsx`, "ignores an unknown mode "banana" / "FULL_AI" in the address (one chip stays chosen)".

**Cause.** `useState((params.get('mode') as Mode) || …)` casts any string to `Mode` without checking it.

### BUG-049 Low: no warning for a scene under 5 seconds (QF5-006)

**Steps.** Open a script with a scene of 5 words (about 2 s).

**Expected.** SCREENS_F5_F6 section 2: "a warning when the scene is over 15 s or under 5 s".

**Actual.** Only the over-15 s and empty-narration warnings exist.

**Reproduced by.** `src/features/create/ScriptEditorPage.test.tsx`, "Script editor: editing > warns about a scene under 5 s".

**Cause.** `SceneCard` has `LONG_SCENE = 15` and no lower bound.

### BUG-050 Medium: a failed script does not show the reason (QF5-003)

**Steps.** Create a Reel whose script fails. On the test stack, a topic containing `[fail]` makes the mock model fail; the job's error is "The AI service didn't answer properly. Please try again.". Another case is `content_refused`, where retrying the same topic fails again.

**Expected.** SCREENS_F5_F6 section 2: "a failed script shows the reason and **Try again**". The reason is the failed job's `error.message`, available from G2 (`GET /projects/:id/jobs`) or G1.

**Actual.** Always the same text: "The script couldn't be written. Your credit was refunded." No request is made for the job.

**Reproduced by.** `src/features/create/ScriptEditorPage.test.tsx`, "Script editor: while the script is written > a script that failed shows the reason from its job".

**Cause.** `ScriptEditorPage` detects the failure only as "not scripting and no scenes". It never reads the script job.

### BUG-051 Low: the scene menu cannot be closed with Escape (QF5-009)

**Steps.**
1. Tab to a scene's "More actions for scene N".
2. Press Enter. The menu opens.
3. Press Escape.

**Expected.** The menu closes, and focus returns to the button (the WAI-ARIA menu pattern).

**Actual.** The menu stays open. It closes only on mouse leave or when an item is chosen. Choosing an item with the keyboard works: "reorders with the keyboard alone" passes, in jsdom and in the real browser.

**Reproduced by.** `src/features/create/ScriptEditorPage.test.tsx`, "Script editor: scene actions > the scene menu can be closed with Escape (keyboard users)".

**Cause.** The menu in `SceneCard` has no key handling and no outside-click handling.

### BUG-052 Low, needs a decision: Write script, Rewrite whole script and Rewrite scene send no `Idempotency-Key`

**Steps.** Press **Write script** (B3), **Write a new script** (B8) or a scene's **Rewrite · 1 credit** (C6), and look at the request headers.

**Expected.** SCREENS_F5_F6 section 4: "Every action that starts work sends an `Idempotency-Key`". All three start paid work.

**Actual.** Only Generate (B11) sends one.

**Reproduced by:**
- `NewReelPage.test.tsx`, "Write script (a paid action that starts work) sends an Idempotency-Key";
- `ScriptEditorPage.test.tsx`, "Rewrite whole script (B8), a paid action, sends an Idempotency-Key" and "Rewrite a scene (C6), a paid action, sends an Idempotency-Key".

**Note.** API design section 2 marks only B11, C7 and C8 as idempotent, so the server ignores the header on B3, B8 and C6 today. The screen spec and the API design disagree. QA raised the same question (QA_REPORT_F5, question 1). If the rule is narrowed to the idempotent endpoints, these three tests should be removed along with the spec line.

## 4. Flaky or skipped

- No test is skipped or marked `fixme`.
- The real-backend suite ran 3 times with the same result each time: the two bug tests failed and the rest passed.
- Two component tests got longer timeouts: 30 scenes (20 s), and duplicate and delete (15 s). The default 5 s was exceeded when the whole suite ran next to the browser suite. Their assertions are unchanged.

## 5. Environment

- A leftover `rff5` stack from the earlier attempt (9 hours old) was taken down with `-v` and rebuilt from backend HEAD.
- At the end, `rff5` was taken down with `-v`, and the Vite servers on 5174, 5175 (a production preview, used once for BUG-046) and 5176 were stopped.
- The `backend` stack, the `qaf5` stack (QA's) and the server on 5173 were not touched.
- `npm run test:e2e` reuses whatever server is on 5173. While a `dev:real` server runs there, the mock suite runs against the real backend and fails. Stop it, or run the suite on another port as in 1.1.
- No real AI, voice, stock or image provider was called: every provider was `mock`.

---

# Round 2 (after FIX_LOG_F5)

| | |
|---|---|
| Date | 2026-11-26 |
| Build tested | `frontend/` working tree with the F5 fixes (still uncommitted on top of d743ebe) |
| Backend for the browser tests | A fresh copy of `backend/` HEAD in Compose project `rff5`, same ports and mock providers as round 1 |
| Checked against | [FIX_LOG_F5.md](FIX_LOG_F5.md), including "Intended changes that the existing tests should follow" |

## R2.1 Results

| Suite | Command | Result |
|---|---|---|
| Typecheck | `npm run typecheck`, `npx tsc --noEmit -p e2e-real` | Pass, pass |
| Unit and component | `npm test` | **516 tests: 512 passed, 4 failed.** The 424 tests from before F5 all pass. The 4 failures are BUG-053 to BUG-056 |
| Browser, real backend | `npx playwright test -c playwright.real.config.ts` | **23 tests: 19 passed, 4 failed** on the final run. The 4 failures are BUG-053 to BUG-056 (run history in R2.5) |

Every round-1 bug test now passes, except the ones changed or removed by decision (R2.2). BUG-045, 046, 047, 048, 050 and 051 are fixed. BUG-049 and 052 were settled by a spec change. The mock-based `e2e/` suite was not run again in this round: it was not asked for, and no file it covers changed except the F5 screens it no longer checks.

## R2.2 Tests changed because behaviour changed on purpose

| Change (FIX_LOG_F5) | Tests updated |
|---|---|
| No `Idempotency-Key` on B3, B8 or C6 (BUG-052, decided) | The 3 tests that expected one were removed. Generate (B11) must still send one, and does (component and browser tests) |
| Short-scene warning is under 2 s (BUG-049, spec changed) | Now "warns about a scene under 2 s, and not at 2 s": 4 words (1.6 s) warns, 5 words (2 s) doesn't |
| One out-of-credits message with the renewal date | The New Reel and scene-rewrite 402 tests now expect "This needs 1 credit and you have 0. Your credits renew on <date from `resetsAt`>." The date is formatted the same way in the test, so the result doesn't depend on the machine's locale. On the real stack: `^This needs 1 credit and you have 0\. Your credits renew on .+\.$` |
| Rewrite box label "How should scene N change?" | Component and browser tests |
| The scene menu focuses its first available item | The keyboard reorder tests no longer press Tab after opening the menu |
| Field errors are listed with the field's name | The 4 cases now expect "Narrator: …", "Visual style: …" and so on; one new test checks several errors at once |
| A failed script shows the job's reason | Browser test: "The script couldn't be written: The AI service didn't answer properly. Please try again. Your credit was refunded." |

## R2.3 New tests

**Component tests** (`src/features/create/*.test.tsx`, `src/api/projects.test.ts`):

- **Autosave:**
  - saves one at a time, in order, with the latest text saved last;
  - "Saved" is a status message;
  - `beforeunload` warns while an edit is unsaved or still being sent, and not after it lands;
  - unsaved text is sent when the editor unmounts;
  - Generate waits for a held save, then generates (the save comes before B11);
  - a scene rewrite and Rewrite whole script send unsaved text first;
  - a failed save (BUG-053).
- **Failures:**
  - a failed first script shows the G2 reason;
  - "change the topic" links to `/app/new` with the topic, mode and length, and opens it;
  - a whole-script rewrite that fails during this visit is reported;
  - one that has already failed by the next fetch (BUG-054);
  - an older failed script job raises no notice.
- **Shorten with AI:** offered over 115% of the target, with B8 instructions that name the target; not offered at 112%.
- **Title:** not replaced by a server title while focused, and saved on blur.
- **TopBar:** the credit and Instagram pills on both screens, and no second `main` landmark.
- **Scene menu:**
  - Escape returns focus to the button;
  - the first scene's menu opens on Move down;
  - a single scene's opens on Duplicate (BUG-056);
  - a second press of the button closes it (BUG-055).
- **New Reel:**
  - line breaks typed or pasted into the topic become spaces; a pasted script keeps them;
  - the ideas niche is cut to 120 characters and the keywords to 200;
  - ideas from `ideas=1` show in StrictMode.
- **`waitForJob`** (new file `src/api/projects.test.ts`, 7 tests):
  - polls exactly every 2 s;
  - returns a failed job as it is;
  - stops on abort without asking again; an already-aborted signal asks nothing;
  - rides out 3 connection errors in a row and gives up on the 4th with `network_error`;
  - a 404 is final.

**Browser tests, real backend:**

- `new-reel.spec.ts` (now 7 tests):
  - topic line breaks become spaces;
  - "change the topic" fills in New Reel;
  - `?mode=banana` is ignored and the script is written;
  - `ideas=1` now passes.
- New file `e2e-real/script-editor-round2.spec.ts` (9 tests):
  - Generate pressed right after typing saves the text before B11, and the top bar shows the balance;
  - leaving through the breadcrumb right after typing keeps the edit;
  - closing the tab while a save is stuck asks first;
  - closing after a failed save (BUG-053);
  - a failed rewrite is reported (BUG-054);
  - Shorten with AI: costs 1 credit, asks B8 for 15 s, leaves the script within 115% of the target, and the button goes;
  - the menu's button closes the menu (BUG-055);
  - a single scene's menu opens on Duplicate (BUG-056);
  - at 375 px, nothing scrolls sideways, and every card, menu button and on-screen text field fits the width, on New Reel too.

## R2.4 New bugs

### BUG-053 Medium: after a save fails, the edit counts as saved

**Steps.**
1. Open a script.
2. Make `PATCH /scenes/:id` fail: the test answers 500; in real life this is a server error or a dropped connection.
3. Type in scene 1's narration and leave the field. The scene shows "Something failed on our side." and the box keeps the typed text.
4. Close or reload the tab, or press **Generate Reel**.

**Expected.** The text is still not on the server, so closing warns, and Generate either saves it first or doesn't start.

**Actual.**
- The tab closes without asking.
- Generate goes ahead with the old narration that is on the server, while the box shows the new text.
- Nothing retries the save until the creator types in that scene again.

**Reproduced by:**
- `ScriptEditorPage.test.tsx`, "Script editor: autosave (round 2) > after a failed save the edit still counts as unsaved: leaving warns, and Generate does not use the old text";
- `e2e-real/script-editor-round2.spec.ts`, "after a save fails, closing the tab still asks (the text is not on the server)": the page closes with no `beforeunload` dialog.

**Cause (best reading).** In `useSceneAutosave.flush()`, the `finally` block sets `settled = upTo` and calls `markUnsaved(-1)` whether the save succeeded or failed, and `pending` was already cleared. The failed edit is then neither pending nor counted as unsaved, so `flushAll()` has nothing to send and `hasUnsaved()` is false.

### BUG-054 Medium: a whole-script rewrite (or Shorten with AI) that fails quickly is not reported

**Steps (real backend).**
1. Open a script that has scenes.
2. Press **Rewrite whole script**, type an instruction that makes the job fail (on the test stack, one containing `[fail]`), and press **Write a new script**.

**Expected.** "A new script couldn't be written: The AI service didn't answer properly. Please try again. Your scenes are unchanged and the credit was refunded." (FIX_LOG_F5, QF5-003)

**Actual.**
- Usually nothing is shown: the scenes stay, but the creator is not told that the rewrite failed.
- Without held requests, the notice appeared in 1 of 6 runs.
- When the screen's next project fetch arrives after the failure is recorded, it never appears (6 of 6).
- On this stack the failing job ends about 0.1 s after B8 answers 202. Polling the API every 0.1 s from 129 ms on, the project was a `draft` with no active job every time.

**Reproduced by:**
- `ScriptEditorPage.test.tsx`, "Script editor: failures and shortening (round 2) > a whole-script rewrite that has already failed by the next project fetch is still reported" (deterministic);
- `e2e-real/script-editor-round2.spec.ts`, "a failed whole-script rewrite says why and keeps the scenes, even when it failed before the next project fetch" (deterministic: it holds the browser's project fetch until the backend has recorded the failure).

**Cause (best reading).**
- The notice needs `useProjectJobs` to be refetched, and that happens only when the screen sees the project go from `scripting` to not `scripting`.
- `useRegenerateScript`'s `refresh()` invalidates the project, estimate, credits, me and projects queries, but not `['project-jobs', id]`.
- A job that fails before the next project fetch is never seen as `scripting`, so the jobs list is never refetched.
- The screen already has the B8 job id (`watchedJob`), so it could wait on that job with G1 (`waitForJob`), or invalidate the jobs query after B8.

### BUG-055 Low: pressing a scene's menu button again does not close the menu

**Steps.** Click "More actions for scene 2" to open the menu, then click the same button again.

**Expected.** The menu closes and `aria-expanded` is false.

**Actual.** It stays open. In the real browser it closes and immediately opens again.

**Reproduced by:**
- `ScriptEditorPage.test.tsx`, "Script editor: scene actions > pressing the menu button again closes the menu";
- `e2e-real/script-editor-round2.spec.ts`, "the scene menu closes on a second press of its button".

**Cause (best reading).**
1. When the menu opens, focus moves into it (`autoFocus`).
2. Pressing the button moves focus out, and the menu's `onBlur` closes it.
3. The button's `onClick` then toggles `menuOpen` from the new `false` back to `true`.

### BUG-056 Low: with one scene, the menu doesn't take focus, and Escape can't close it

**Steps.**
1. Open a script that has a single scene.
2. Open its "More actions" menu, with the mouse or with Enter.
3. Press Escape.

**Expected.** Focus is on the first available item, Duplicate (FIX_LOG_F5: "focus starts on the first available item"). Escape closes the menu and focus returns to the button.

**Actual.** Focus stays on the button. Escape does nothing, because its handler is on the menu and focus is not inside the menu.

**Reproduced by:**
- `ScriptEditorPage.test.tsx`, "Script editor: scene actions > the first scene's menu starts on Move down; a single scene's on Duplicate (the first available item)";
- `e2e-real/script-editor-round2.spec.ts`, "with one scene, the menu opens on Duplicate and Escape returns to its button".

**Cause.** `autoFocus={i === (index === 0 ? 1 : 0)}` picks Move down for the first scene. With one scene, Move down is disabled too, so nothing in the menu takes focus.

## R2.5 Flaky, skipped, notes

- Nothing is skipped.
- One test was removed because its result depended on timing: a browser test of BUG-054 without held requests failed in 5 of 6 runs, because the outcome depends on the product's own race. The deterministic version above replaces it.
- Run history of the real-backend suite:
  - First full run: 23 tests, 19 passed, 4 failed. That run still included the unheld BUG-054 test, which happened to pass, and the two menu checks were still one test.
  - After the menu test was split: 24 tests, 19 passed, 5 failed.
  - Final run, after the unheld test was removed: 23 tests, 19 passed, 4 failed.
- The 375 px check measures sideways scrolling and the right edges of the cards, menu buttons and on-screen text fields. It does not judge the look.
- Observation, not a bug: `toLocaleDateString(undefined, …)` formats the renewal date in the viewer's own time zone. For a `resetsAt` at midnight UTC, a creator west of UTC sees the day before, for example "November 30" in New York for a 1 December reset. That is correct local time.
- Not filed: topic line breaks are replaced only as the creator types. A `?topic=` in the address that holds line breaks is not cleaned, and B3 would refuse it. The dashboard caps the topic and sends no line breaks.
- Environment, as in round 1: a fresh `rff5` stack, taken down with `-v` at the end, and the Vite server on 5174 stopped. The `backend` stack and the server on 5173 were not touched. No real provider was called.

---

# Round 3 (after FIX_LOG_F5, "Round 3")

| | |
|---|---|
| Date | 2026-11-26 |
| Build tested | `frontend/` working tree with the round-3 fixes (uncommitted on top of d743ebe) |
| Backend for the browser tests | A fresh copy of `backend/` HEAD in Compose project `rff5`, same ports and mock providers as before |

## R3.1 Results

| Suite | Command | Result |
|---|---|---|
| Typecheck | `npm run typecheck`, `npx tsc --noEmit -p e2e-real` | Pass, pass |
| Unit and component | `npm test` | **528 tests: 528 passed** (12 files). The 424 from before F5 all pass |
| Browser, real backend | `npx playwright test -c playwright.real.config.ts` | **26 tests: 26 passed** (one run, as asked) |

BUG-053, 054, 055 and 056 are fixed: their round-2 tests pass unchanged, in the component suite and in the browser.

**No new bugs in round 3.**

## R3.2 Test changed on purpose

"a whole-script rewrite that fails during this visit says why…" now also answers `GET /jobs/:id` with the failed job. The page reads B8's result from G1 for the job id that B8 returned, and no longer from the jobs list (FIX_LOG_F5 Round 3).

## R3.3 New tests

**Component (12 in the Script editor, 2 on New Reel):**

- **A failed save stays unsaved:**
  - the error shows, "Saved" never does, and the leave warning stays on;
  - the next blur sends the same text again; when it succeeds, "Saved" shows and the warning clears.
- **Generate after a failed save:**
  - it tries the save again; if that fails too, it stops with "Some changes aren’t saved yet. Check your connection and try again." and B11 is never called;
  - if the retried save succeeds, it goes ahead.
- **Rewrites stop too:** a scene rewrite (message in the card) and Rewrite whole script (message in the panel) both stop while a save keeps failing.
- **A removed card is released:** after a scene with unsaved, failed text is deleted, the leave warning clears once its last save ends.
- **B8 failure read from G1:** covered by the round-2 test "already failed by the next project fetch", which now passes.
- **Stop signals when the page is left:**
  - a hooks wait and a scene-rewrite wait stop polling G1 (no request in the next 10 s);
  - New Reel's Get ideas wait (started by a click) stops too;
  - the automatic `ideas=1` request keeps polling until its job ends. This is intended.
- **Generate busy guard:** three clicks while text is still being saved start B11 once.
- **Scene menu toggle and single-scene focus:** covered by the round-2 tests (BUG-055 and BUG-056), which now pass.
- **"Very short: under 5 seconds":** shown at 4 s, not at 5.2 s.
- **No stray "0":** with no scenes, the summary's length block holds no loose "0". I suspected `{scenes.length && …}` would render one. It doesn't, because `?:` binds more loosely than `&&`; the test stays as a guard.

**Browser, real backend (`e2e-real/script-editor-round2.spec.ts`, now 12 tests):**

- A save forced to fail (500) stops Generate with the "not saved" message and sends no B11. Once saves work again, Generate saves the text and opens the Reel editor, and the server has the text.
- A double click on Generate while text is being saved sends B11 once.
- New Reel at 375 px with ideas shown: no button, field, section, fieldset or idea card ends past 375 px, and nothing scrolls sideways.

## R3.4 Notes

- Nothing is skipped and nothing was flaky this round. I ran the browser suite once, as asked, so the 3 new browser tests have one run each.
- The New Reel 375 px test was written after the fix, so it was never seen failing on the old layout. It measures the right edge of every control and card inside the page's `main`.
- Environment: a fresh `rff5`, torn down with `-v` at the end, and Playwright's server on 5174 stopped. The `backend` stack and the server on 5173 were not touched. No real provider was called.
