# ReelForge: test report for F6 (Reel editor with live preview)

| | |
|---|---|
| Date | 2026-11-26 |
| Role | Tester (automated tests only; no product code changed) |
| Build tested | `frontend/` working tree, F6 uncommitted on top of bc606b3 |
| Backend for the browser tests | A copy of `backend/` HEAD 459d2c5 (`git archive`, no `.env`), Compose project `rff6`: API 5200, Postgres 54340, Redis 63810, MinIO 9200/9201 (presigned links use `S3_PUBLIC_ENDPOINT_URL=http://localhost:${MINIO_PORT}`, so the browser loads media from the isolated MinIO), Mailpit 8225, every provider `mock`, rate limits off, `WEB_ORIGIN=http://localhost:5174`. Web app: `vite --mode real --port 5174` with `API_PROXY_TARGET=http://127.0.0.1:5200` (started by `playwright.real.config.ts`). The uncommitted `backend/renderer/src` changes are not in this copy; the API stack doesn't use the renderer, and the frontend's copy is compared with the working tree by `sync.test.ts` |
| Music in the test stack | `flask music-seed` downloads from incompetech.com, so it was not run. Two generated 20-second WAV tones were stored in the test MinIO and added to `music_tracks` (all 10 moods) with a script run in the `rff6` API container. The audio bar test needs some tracks there (the spec's header says so) |
| Checked against | [SCREENS_F5_F6.md](../SCREENS_F5_F6.md) sections 3 and 4; API design B4, B5, B10–B12, C2, C7–C10, D3, D4, G1, H1; [FIX_LOG_F5.md](FIX_LOG_F5.md) (autosave rules) |

## 1. Results

| Suite | Command | Result |
|---|---|---|
| Typecheck | `npm run typecheck`, plus `npx tsc --noEmit -p e2e-real` | Pass, pass |
| Unit and component (all) | `npm test` | **593 tests: 586 passed, 7 failed**. The 535 tests from before F6 all pass. The 7 failures are new tests that find BUG-057 to BUG-063 |
| F6 component tests alone | `npx vitest run src/features/editor src/remotion` | 63 tests: 56 passed, 7 failed (the same 7). `sync.test.ts` passes: the preview's copy equals the renderer's working tree |
| Browser, real backend, F6 spec | `npx playwright test -c playwright.real.config.ts e2e-real/reel-editor.spec.ts` (with `REAL_COMPOSE_PROJECT=rff6`) | **16 tests: 15 passed, 1 failed**, the same in each of 2 full runs (BUG-064) |
| Browser, real backend, everything | `npx playwright test -c playwright.real.config.ts` | 42 tests: 41 passed, 1 failed (BUG-064). The 26 F5 tests all pass |
| Browser, mock (existing) | `e2e/` suite on port 5177 (see 1.1) | 221 tests: 218 passed, 3 failed. 1 was the expected placeholder change; 2 were load timeouts that pass alone |

### 1.1 Note on the mock suite (`npm run test:e2e`)

A dev server was running on port 5173, and that server was not to be touched; `npm run test:e2e` reuses whatever answers there. As in F5, the unchanged suite was run with a temporary config that imports `playwright.config.ts` and only moves the mock dev server to port 5177 (5175 was already used by another process). The temporary config was deleted after each run.

| Run | Result |
|---|---|
| Full suite (23.5 min) | 221 tests: 218 passed, 3 failed |
| `shell.spec.ts` "/app/p/some-id/edit shows the "Reel editor" placeholder" | An expected change, not a bug: F6 replaced the placeholder. The route was removed from the placeholder list in `e2e/shell.spec.ts`, with a comment, as F5 did for its screens. Render, Publish and Settings are still checked |
| `onboarding.spec.ts:167` and `onboarding-instagram.spec.ts:378` | Load timeouts during the full run; both pass when run alone |
| Rerun: `shell.spec.ts` (updated) and the two onboarding tests | 20 passed, 0 failed |

## 2. What was added

| File | What |
|---|---|
| `frontend/src/features/editor/ReelEditorPage.test.tsx` | 54 component tests for the whole screen (page, scene list, inspector, stock search, audio bar). The backend is the existing `src/test/fakeApi.ts` `fetch` stand-in, with a fake B12 built from the project the way the API builds it (voice + 0.3 s, at least 1.5 s, or the estimate). `@remotion/player` is replaced by a stand-in that shows the frames, fps, caption style and on-screen texts it was given, and records `seekTo` calls |
| `frontend/src/remotion/Reel.test.ts` | 4 tests of `musicVolume` (the renderer's music level): ducking under a voice, a voice with no length yet (new in this renderer version), ducking off, fades, no music |
| `frontend/e2e-real/reel-editor.spec.ts` | 16 browser tests against the real backend (15 tests, one of them run at 375 px and at 1440 px) |

No product file or MSW handler was changed. The only existing test changed is `e2e/shell.spec.ts` (see 1.1). `fakeApi.ts` and `helpers.ts` are used as they are.

### Covered

**Page and header (component and browser)**
- Title, status pill, credits pill, back link to the script, length from B12, Render disabled with its note; no second `main` landmark.
- `scripting` opens the Script editor; 404 goes to the projects list; a load error offers Try again.
- Generate on the Script editor opens `/app/p/:id/edit`; the progress line ("Making your Reel: n of 8 parts ready", a live status) while generating, then Ready, the length and no Generate missing (browser). In the component test the counts are checked exactly (3 of 6), polling every 2 s, and the timeline is fetched again when generation ends; the "coloured placeholder" note shows only while scenes are unmade.
- `tooLong`: "over 90 s: shorten it before rendering" (component, and browser with 4 × 28 s narrations before any voice exists).
- `needs_attention`: the notice, the pill, the red scenes with Retry only on failed scenes, the scene's `lastError` in the inspector.
- Generate missing: the cost from B10, `Idempotency-Key` and a new key per click, 402 with the shared out-of-credits message, unsaved text saved first, a failing save stops it with the reason.

**Scene list and player**
- Number, mood, first words, voice and visual status (as text for screen readers), the first scene selected, `aria-current`.
- Selecting a scene opens it in the inspector and seeks the player to the scene's `startFrame` (component: the seek; browser: Remotion's clock shows the scene's start time against the total).
- The player is there with the timeline's length ("0:00 / 0:13"), and plays without page errors when the visuals are AI images (browser).
- The timeline is fetched again after an on-screen text edit, an audio bar change, and when a job ends; the player then gets the new timeline (component and browser).
- Keyboard: Tab between scenes, Enter selects (component and browser).

**Inspector**
- Tabs: a labelled tablist with three tabs; the selected tab controls a panel labelled by it; every field in every panel has a name; Tab and Enter or Space switch tabs.
- Visual: the clip credit link; source chips (C2 `visualSource`), Upload shown but disabled; stock words and Search stock only for stock scenes; costs from H1 (stock free, AI image and AI video cost `aiImage`, checked with a non-default cost of 4); Regenerate visual (C8) with an `Idempotency-Key`, after an unsaved prompt is saved; 409 message; the "no stock clip fitted" note for a fallback image (browser, `[no-stock]`).
- Voice: narration (debounced) and mood saves; the 10 moods; the voice cost from H1 following the narration length (short scene, 16 s, 32 s, checked with non-default costs 3 and 5); Regenerate voice (C7) with the chosen voice for this take only, an `Idempotency-Key`, and no project change; CA-4: the confirm appears only when `wordsEdited`, Cancel sends nothing, OK sends `confirmReplaceWords: true`; after the new take the words are the voice's again (browser).
- Text: on-screen text; caption word fixes send only the texts, the times are kept (browser checks the stored `start`), an empty word can't be saved, "Fixed by hand" appears; "Caption words appear here once the voice is made".
- Busy states: while a voice is made, narration, mood, voice picker, Regenerate voice and caption words are disabled and the visual side is not; while a visual is made, prompt, stock words, source chips and Regenerate visual are disabled (with `aria-busy`) and the voice side is not; the scene unlocks when the job ends.
- Browser: each tab's fields saved through the API (prompt, stock words, narration, mood, on-screen text), the statuses becoming `outdated`, the values there after a reload; Regenerate voice charges `voiceSceneShort` and Regenerate visual as AI image charges `aiImage` (H1 balance before and after), new asset ids.

**Autosave (the F5 rules, on the inspector fields)**
- One save at a time, in order, the latest text last; text typed while a save is out is kept; the server's older answer doesn't replace newer text.
- Closing the tab warns while unsaved and not after the save lands (component; browser: `beforeunload` dialog).
- Selecting another scene, or leaving the editor, sends unsaved text.
- A failed save: the error, never "Saved", still warns, and Regenerate voice stops with "Some changes aren’t saved yet: …".
- A caption fix being typed is kept while the project is polled for another scene's job.

**Stock search (C9, C10)**
- First search without `q` (the scene's words), More and Previous with `page`, More disabled on the last page, a typed search sends `q` and page 1, "Videos from Pixabay"; using a clip sends `{stock: providerId}` and refreshes the project; the clip in use is marked and disabled; 503 message. Browser: paging changes the results, the chosen clip becomes the scene's visual (`sourceUrl`) and its author is credited.

**Audio bar (B5, D4)**
- Music mood (PATCH `musicMood`, the D4 list fetched for the new mood), a track (`music: {trackId}`), its preview, Automatic (`music: null`); voice and music volume saved once, 500 ms after the slider stops; ducking, caption style and show captions saved to `settings`; the player follows (caption style, captions off); a refused change is shown.
- Browser: all of the above reach `GET /projects/:id` and B12 (`captionStyle`, `showCaptions`, `music.trackId`, `duckedVolume == volume` with ducking off), and are there after a reload.

**Layout (browser)**
- 375 px: nothing wider than the screen; the player first, then the scenes, then the inspector; every audio bar control within the screen.
- 1440 px: no overflow; scenes, player and inspector side by side.

**Regressions**
- The whole unit suite (535 tests before F6) and the mock browser suite (see 1.1); `sync.test.ts` (the preview's renderer copy equals `backend/renderer/src`).

### Not covered yet (deliberately)

- What the Remotion Player draws (captions word by word, transitions, motion, trims on screen). The renderer code is the backend's own (checked by `sync.test.ts`); only `musicVolume` has unit tests. A visual check belongs with the render milestone (F7), where frames can be compared.
- Audio actually playing (voice, music preview); jsdom has no media, and the browser tests only check the controls and the links.
- Upload (disabled until B7) and Render (F7).
- Real stock clips: the mock stock "clip" is an MP4 header only, so the browser can't play it (see note 5.2). The player test uses AI images.
- A 402 on Regenerate voice or visual (the same `errorText` path as Generate, which is tested).
- Leaving through an in-app link while a save is failing (QF5-021, deferred to F9).

## 3. Bugs

| Bug | Severity | Area |
|---|---|---|
| BUG-057 | Medium | Generate missing and Retry start twice on a double click while text is being saved (QF5-020 again) |
| BUG-058 | Medium | Regenerate voice (and visual) start twice on a double click while text is being saved |
| BUG-059 | Medium | Moving the voice volume and then the music volume within 500 ms loses the voice change |
| BUG-060 | Low | A volume moved just before leaving the editor is never saved |
| BUG-061 | Low (spec gap) | The scene list can't be reordered by dragging |
| BUG-062 | Low (accessibility) | The inspector tabs don't follow the tabs keyboard pattern |
| BUG-063 | Low | The inspector goes back to the Visual tab whenever another scene is selected |
| BUG-064 | Medium (backend) | A scene whose voice failed loses its reason when its visual finishes |

### BUG-057 Medium: Generate missing and Retry can start twice (QF5-020 again)

- **Test:** `frontend/src/features/editor/ReelEditorPage.test.tsx`, "Reel editor: Generate missing (B10, B11) > is busy from the first click: a double click while text is being saved starts one run".
- **Steps:** open the editor of a draft with something missing; on the Voice tab type into Narration; while that save is still in flight (held by the fake), click **Generate missing** twice; let the save answer.
- **Expected:** one `POST /projects/:id/generate`. The button is busy from the first click, as the Script editor's Generate is since QF5-020.
- **Actual:** two `POST /generate` requests, each with its own `Idempotency-Key`. On the real backend the second one gets `409 already_running`, and the editor shows that as an error. If the first run ends quickly, the second one starts another run.
- **Cause:** `ReelEditorPage.generateMissing` waits for `flushAll()` before `generate.mutateAsync`. The button's `loading` is only `generate.isPending`, which is false while the flush runs. **Retry** uses the same function and the same `retrying={generate.isPending}`, so it has the same gap.

### BUG-058 Medium: Regenerate voice (and visual) can start twice

- **Test:** `ReelEditorPage.test.tsx`, "Reel editor: Voice tab (C2, C7, D3) > Regenerate voice is busy from the first click: a double click while text is saved starts one take".
- **Steps:** on the Voice tab type into Narration; while the save is in flight, click **Regenerate voice** twice.
- **Expected:** one `POST /scenes/:id/voice/regenerate`.
- **Actual:** two, with different `Idempotency-Key`s. The backend refuses the second with `409 already_running`, and the inspector shows it as an error under a voice that is in fact being made.
- **Cause:** in `Inspector.regenerateVoice`, `flushAll()` runs before `voice.mutateAsync`, and the button's `loading` is `voice.isPending || voiceBusy`. Neither is true during the flush. `regenerateVisual` has the same shape (not tested separately).

### BUG-059 Medium: moving both volume sliders within 500 ms loses the first change

- **Test:** `ReelEditorPage.test.tsx`, "Reel editor: audio bar (B5, D4) > moving the voice and then the music slider within 500 ms saves both".
- **Steps:** set **Voice volume** to 0.5; within 500 ms set **Music volume** to 0.4; wait.
- **Expected:** both settings are saved, and the voice slider stays at 0.5.
- **Actual:** only `{settings: {musicVolume: 0.4}}` is sent. The project's `voiceVolume` stays 1. When the music save answers, the voice slider jumps back to 1.
- **Cause:** `AudioBar` keeps one `timer` ref for both sliders. `slide()` calls `clearTimeout(timer.current)`, which cancels the other slider's pending save. The effect on `project.settings.voiceVolume/musicVolume` then resets the local value from the server.

### BUG-060 Low: a volume moved just before leaving is dropped

- **Test:** `ReelEditorPage.test.tsx`, "Reel editor: audio bar (B5, D4) > a slider moved just before leaving the editor is still saved".
- **Steps:** move **Music volume**; within 500 ms leave the editor (in the test: unmount the page).
- **Expected:** the change is sent, as unsaved text is (SCREENS 4: "Leaving the page sends unsaved text"; screen 3: "Every edit saves at once").
- **Actual:** nothing is sent. Closing the tab doesn't warn either, because the slider never counts as unsaved.
- **Cause:** the unmount effect `() => clearTimeout(timer.current)` cancels the pending save without sending it, and the slider doesn't take part in `markUnsaved`.

### BUG-061 Low (spec gap): the scene list can't be reordered by dragging

- **Test:** `ReelEditorPage.test.tsx`, "Reel editor: scene list and player > scenes can be reordered by dragging (C5)".
- **Spec:** SCREENS_F5_F6 section 3, Scene list: "Click selects the scene; drag reorders | B4, C5".
- **Steps:** open the editor of a project with three scenes; drag scene 3 onto scene 1.
- **Expected:** a scene row can be dragged onto another, which sends C5 (as in the Script editor).
- **Actual:** no row is draggable, and C5 is never called. `SceneList.tsx` has no drag handling. Reordering is only possible on the Script editor.
- **Note:** if this is left out on purpose, the spec should say so, and this test should change.

### BUG-062 Low (accessibility): the inspector tabs don't follow the tabs keyboard pattern

- **Test:** `ReelEditorPage.test.tsx`, "Reel editor: inspector tabs and accessibility > arrow keys move between the tabs (WAI-ARIA tabs pattern)".
- **Steps:** focus the **Visual** tab and press Right arrow.
- **Expected (WAI-ARIA tabs):** focus and selection move to **Voice**; Left arrow goes back. Only the selected tab is in the Tab order (`tabindex="-1"` on the others).
- **Actual:** the arrows do nothing. All three tabs are separate Tab stops. They can still be used with Tab and Enter or Space (tested and passing, in jsdom and in Chromium).
- **Also seen in the code, not tested:** the unselected tabs' `aria-controls` point at `panel-voice` or `panel-text`, which are not in the page; only the selected panel is rendered, under an id that changes with the tab.

### BUG-063 Low: the selected tab resets when another scene is selected

- **Test:** `ReelEditorPage.test.tsx`, "Reel editor: inspector tabs and accessibility > the selected tab is kept when another scene is selected".
- **Steps:** select the **Text** tab, then click scene 2 in the list.
- **Expected:** the inspector shows scene 2 on the **Text** tab. `Inspector.tsx` itself says "Another scene: start on the same tab with a clean slate". Going through every scene's captions or voice should not need a tab click per scene.
- **Actual:** the inspector shows scene 2 on the **Visual** tab.
- **Cause:** `ReelEditorPage` renders `<Inspector key={selected.id} …>`, which remounts the inspector and resets its `tab` state. The `useEffect` on `scene.id` that was meant to keep the tab never matters.

### BUG-064 Medium (backend): a scene whose voice failed loses its reason when its visual finishes

- **Test:** `frontend/e2e-real/reel-editor.spec.ts`, "a scene whose voice failed shows why, even after its visual was made". It fails in every run (3 of 3).
- **Steps:** draft a project and put `[voice-fail]` in scene 2's narration; Generate (B11); when it's done, open the editor and select scene 2.
- **Expected (API B11, C7):** the failed voice shows `failed` with `lastError`, and the inspector shows that reason ("The AI service didn't answer properly. Please try again.", the failed job's `error.message` from G2).
- **Actual:** `voiceStatus: "failed"`, `visualStatus: "ready"`, `lastError: null`. The scene is red with Retry, but no reason is shown anywhere in the editor.
- **Evidence (test database):** for each such scene, the voice job failed and the scene's visual job succeeded about 1 s later. `last_error` was empty in all 4 cases.
- **Cause:** a scene has one `last_error` column for both parts. `visuals.py` (visual job success, about line 466) sets `scene.last_error = None` after the voice has failed. The reverse case is the same: `generation.py` (voice success, about line 177) clears a failed visual's reason. So does selecting an upload (`visuals.py`, about line 575). Generate starts both jobs side by side, so this happens whenever one part fails and the other succeeds after it. A fix belongs in the backend, either clearing only when the other part isn't failed, or keeping a reason per part. The editor only shows what B4 sends.

## 4. Flaky or skipped

- Nothing is skipped and nothing was flaky. The F6 browser spec gave the same result in 2 full runs and in the whole-suite run: 15 passed, and BUG-064 failed each time. The component tests gave the same 7 failures in 3 runs.
- The two mock-suite failures in the full run (onboarding) were load timeouts with 3 workers and the dev server compiling on demand. Both pass alone; F5 saw the same with other tests.
- With the mock providers the whole Generate run can finish before the editor's first fetch. So the browser test accepts the progress line *or* an already-ready Reel, then requires Ready. The exact progress counts are checked in the component test.

## 5. Notes (not filed as bugs)

1. **"Saved / Saving…" is per field, not in the header.** Screen 3 says the note is in the header. The editor shows it under each text field, and the audio bar shows nothing on success (only an alert on failure). This works, but it differs from the spec.
2. **Unplayable clips throw uncaught page errors.** The mock stock "clip" is only an MP4 header, so the browser can't play it. Remotion then throws an uncaught error: "The browser threw an error while playing the video …: Code 4 … Pass an onError() prop to handle the error." The player keeps running with a black frame. Real Pixabay clips play, so this was not filed as a bug. The same thing would happen with any real clip that fails to load, for example a presigned link past its 2 hours if the timeline wasn't refetched. An `onError` on the video, falling back to the mood placeholder, would avoid it. Because of this, the player test uses AI images.
3. **React warnings in the browser console from Remotion's `AbsoluteFill`** (development build only): "`ref` is not a prop" and "Function components cannot be given refs". Nothing visible breaks.
4. **Track lengths are rounded to whole minutes** ("(0 min)" for the 20-second test tones). This is harmless with the real library, whose tracks are minutes long.
5. **The music library is empty in a fresh stack until `flask music-seed` runs.** The editor then offers only "Automatic for this mood", and B12 has no music. The audio bar browser test needs tracks; see the header.
6. The test stack (`rff6`) was taken down with `docker compose -p rff6 down -v`. The main stack and the server on 5173 were not touched.

---

# Round 2 (after [FIX_LOG_F6.md](FIX_LOG_F6.md))

| | |
|---|---|
| Build tested | `frontend/` working tree with the F6 fixes (uncommitted on top of bc606b3) |
| Backend for the browser tests | A fresh copy of `backend/` HEAD (`git archive`), with the working tree's `api/reelforge_api/generation.py` and `visuals.py` copied over it (the uncommitted BUG-064 fix), Compose project `rff6`, same ports and mock providers as round 1. The two test music tones were added again (new database). Taken down afterwards with `docker compose -p rff6 down -v` |

## R2.1 Results

| Suite | Command | Result |
|---|---|---|
| Typecheck | `npm run typecheck`, plus `npx tsc --noEmit -p e2e-real` | Pass, pass |
| Unit and component (all) | `npm test` | **614 tests: 612 passed, 2 failed** (BUG-065, BUG-066). The 535 tests from before F6 pass. The 7 bug tests from round 1 (BUG-057 to BUG-063) pass |
| F6 component tests | `src/features/editor` (70 + 5) and `src/remotion` (4 + the 5 sync tests) | the same 2 failures |
| Browser, real backend | `npx playwright test -c playwright.real.config.ts e2e-real/reel-editor.spec.ts --workers=2` | **22 tests: 21 passed, 1 failed** (BUG-067) in the second full run; see R2.5 for the first |

BUG-064 is fixed: "a scene whose voice failed shows why, even after its visual was made" passes in both runs, and so does a new test for the reverse case.

## R2.2 Tests changed because behaviour changed on purpose

These follow "Intended changes that the tests should follow" in FIX_LOG_F6:

- The Render note is visible text ("Rendering to a video comes next") and is the button's accessible description; it is no longer a `title`.
- "the tabs work with Tab and Enter" is now "the tabs are one Tab stop; arrows wrap, Home and End jump, and only the shown panel is referenced". It checks `tabindex`, focus leaving the tablist on Tab, ArrowLeft wrapping from Visual to Text, Home, End, ArrowRight wrapping, and that every `aria-controls` points at an element on the page. The browser keyboard test uses ArrowRight and End and checks that Tab leaves the tabs.
- Caption words: the Save button test is now "saved as typed, only the texts are sent, spaces are left out" (0.8 s debounce, and on leaving a word). The browser CA-4 test fixes the word and leaves it, instead of clicking Save.
- "Videos from Pixabay": "Pixabay" is a link to https://pixabay.com/.
- Music: the track list is fetched once without a mood (`GET /music`) and is grouped by this mood's tracks first. The fake library now has tracks with different moods.
- Two caption tests have a 15 s time budget (a test that waits for two real 2-second polls, and a long typing test); with the whole suite running in parallel they took 5–11 s. The assertions are unchanged.

## R2.3 New tests

Component (`ReelEditorPage.test.tsx`, unless noted):
- **QF6-001:** a failing save keeps the editor on the scene with "Some changes aren’t saved yet: …", and it moves on once the save works. **QF6-007:** a switch waits for a save in flight, and coming back shows the saved text.
- **Caption autosave (QF6-002):**
  - a failed save keeps the fix, shows the error, never shows "Saved", keeps the leave warning, and the next blur sends it again;
  - an emptied word is refused (400) and the API's message shows under the words;
  - Generate sends a fix that is still being typed first;
  - a fix whose save failed survives switching tab (fails: BUG-066).
- **Sliders (BUG-059, BUG-060):** a move not yet sent counts for the leave warning until it is saved; Generate sends it first; `aria-valuetext` reads "70 percent". The round-1 tests for per-slider timers and the send on unmount now pass.
- **Drag reorder (C5):** dragging scene 3 onto scene 1 sends `[s3, s1, s2]` and the list follows; a refused order shows its message.
- **Narrator picker:** it asks first; Cancel sends nothing and keeps the value; OK sends `{voiceId}`. The narrator and "Voice for this take" are two pickers with distinct ids, and changing one doesn't change the other.
- **Music:**
  - a chosen track stays selected and named after a mood change, and moves to "Other moods";
  - its license is shown;
  - lengths read as m:ss;
  - "Automatic for this mood" when a track is chosen;
  - the group label before a vowel (fails: BUG-065).
- **Busy from the first click:** a Regenerate visual double click while text is saved starts one job (BUG-058).
- **Player:** a timeline fetched again keeps the links the player already has. When the player's error fallback is used, "The preview couldn't load one of its files." and **Reload preview** are shown, and Reload fetches B12 again with fresh links. (In this test the stand-in player calls `errorFallback` itself, so it checks the page's side only; see BUG-067.)
- **`links.test.ts` (5 tests):**
  - the first timeline is used as it is;
  - the same file's link is reused under 90 minutes while everything else comes from the new timeline;
  - a new link is taken at 90 minutes, counted from when the old one was first seen, and then kept;
  - a new file gets its own link, and empty sources stay empty;
  - without the earlier timeline (Reload preview) every link is fresh.

Browser (`e2e-real/reel-editor.spec.ts`, 6 new):
- **BUG-064, reverse case:** a failed visual (`[image-fail]`) keeps its reason after the voice is made, and the inspector shows it.
- **Unplayable stock clip:** the preview should show the error and Reload preview (fails: BUG-067).
- **Caption fixes:** they autosave as typed (no blur needed) and are there after a reload.
- **Drag reorder:** dragging scene 3 onto scene 1 reorders the project (C5).
- **Narrator:** dismissing the confirm changes nothing; accepting saves `voiceId` and makes every voice `outdated`, and Generate missing appears.
- **Scene rows:** with a 30-word narration the text is at most two lines, every `.sr-only` text stays inside its row, and nothing overflows sideways.
- The keyboard test also checks that no id appears twice on the page (two voice pickers).

Not covered: QF6-017's "Saving…" note in the audio bar (seen in the code, not asserted); a slider save that fails (it is not retried: see R2.6).

## R2.4 New bugs

### BUG-065 Low: "For a inspiring Reel"

- **Test:** `ReelEditorPage.test.tsx`, "Reel editor round 2: drag reorder, narrator, music, pickers > the mood group reads naturally before a vowel ("For an inspiring Reel")".
- **Steps:** open the editor of a project whose music mood is *inspiring* (or *excited*); open the Music track list.
- **Expected:** the first group reads "For an inspiring Reel".
- **Actual:** "For a inspiring Reel".
- **Cause:** `AudioBar.tsx` builds the label as `` `For a ${project.musicMood} Reel` ``.

### BUG-066 Medium: a caption fix whose save failed is lost by switching tab

- **Test:** `ReelEditorPage.test.tsx`, "Reel editor round 2: caption-word autosave (QF6-002) > a caption fix whose save failed is not lost by switching to another tab and back".
- **Steps:** on the Text tab change caption word 1 to "Sceen" and leave the word while saves fail (500): the error shows. Click the **Voice** tab, then **Text** again. Then click **Generate missing**.
- **Expected:** the same guarantees as every other field (FIX_LOG_F6, QF6-002): the fix is still in the box, or at least it still counts as unsaved; leaving warns; Generate stops with "Some changes aren’t saved yet…".
- **Actual:** the box shows the old word "Scene" again. Leaving doesn't warn. Generate starts. The fix is gone without a word.
- **Cause:** `CaptionWords` (and the `useAutosavedField` inside it) is rendered only while the Text tab is open. Switching tab unmounts it. The unmount path sends the text once more and then, when that fails, calls `markUnsaved(-1)` and unregisters the field, so nothing remembers the edit. The other inspector fields don't have this problem: their hooks live in `Inspector`, which stays mounted across tabs. Moving the caption field's hook up into `Inspector` (or keeping the component mounted and hidden) would close the gap.

### BUG-067 Medium: the preview's error fallback never appears for a video that can't play

- **Test:** `e2e-real/reel-editor.spec.ts`, "a stock clip that can’t play shows the preview error and Reload preview fetches the timeline again". It fails in both runs and in a separate rerun (3 of 3).
- **Steps (real backend, mock providers):** generate a project (stock scenes; the mock "clip" is an MP4 header only), open the editor, press Play in the preview.
- **Expected (QF6-009):** the preview shows "The preview couldn't load one of its files." and a **Reload preview** button.
- **Actual:** the player keeps running, showing a black frame where the clip should be, and its clock advances (for example "0:08 / 0:13"). The page throws uncaught errors: "The browser threw an error while playing the video http://localhost:9200/…mp4…: Code 4 … Pass an onError() prop to handle the error." No message, no Reload preview. Confirmed with a separate exploratory run (deleted afterwards).
- **Cause (best reading):** Remotion reports a media playback error by throwing from the video element's error handler when no `onError` is given to `OffthreadVideo`/`Video`. That happens outside React's rendering, so the Player's `errorFallback` (an error boundary for the composition) is never used. A real clip that fails, such as an expired link or a bad download, behaves the same way. A fix needs the video's `onError` (in the shared renderer `Scene.tsx`, so the renderer and preview stay identical), or the Player's error event, to set the page's "failed" state. The component test of the fallback passes only because its stand-in player calls `errorFallback` directly.

## R2.5 Flaky, skipped, notes

- **First browser run on the fresh stack:** 22 tests, 16 passed, 6 failed.
  - 2 were my tests: CA-4 still clicked the removed Save button, and the audio test waited for "the next timeline answer", which an earlier change could also trigger. It now waits for the answer that carries the chosen track, as the on-screen text test already did.
  - 3 were the test stack failing under heavy host load: Postgres restarted at 08:04 ("server process … exited with exit code 2"), giving a sign-up 500 and an empty stock search, and the dev server took over 15 s to compile the editor the first time.
  - 1 was BUG-067.
  - The 6 were rerun on their own: 5 passed, BUG-067 failed. The second full run: 21 passed, BUG-067 failed.
  - Both runs used `--workers=2` because the machine was heavily loaded; the worker container's health check was timing out, though it kept working.
- **First component run (while the machine was loaded):** 8 failures, of which 6 were timeouts and their knock-on effects. On their own, and in the final full-suite run, only BUG-065 and BUG-066 fail.
- Nothing is skipped.

## R2.6 Notes (not filed)

1. **A slider save that fails is not retried** (read in `AudioBar.tsx`'s `useVolumes`, not tested). `send()` drops the pending value before saving and calls `markUnsaved(-1)` in `finally`, so after a failed save the slider shows a value the server doesn't have, with only the audio bar's alert to say so. Generate stops that one time (the flush rejects) but goes ahead on the next click. This is less strict than the text fields; it may be acceptable for a slider, but it is not "kept and retried".
2. In the browser, Remotion's development warnings ("`ref` is not a prop") are still printed; nothing visible breaks.

---

# Round 3 (after FIX_LOG_F6 "Round 3")

| | |
|---|---|
| Build tested | `frontend/` working tree with the round-3 fixes (uncommitted on top of bc606b3) |
| Backend for the browser tests | A fresh `git archive` of `backend/` HEAD with the working tree's `api/reelforge_api/generation.py`, `visuals.py` and `renderer/src/` copied over it; Compose project `rff6`, same ports and mock providers; the two test music tones added again. Taken down afterwards with `docker compose -p rff6 down -v` |

## R3.1 Results

| Suite | Command | Result |
|---|---|---|
| Typecheck | `npm run typecheck`, plus `npx tsc --noEmit -p e2e-real` | Pass, pass |
| F6 component tests | `npx vitest run src/features/editor src/remotion` | **92 tests, 92 passed** (78 + 5 `links` + 4 `Reel` + 5 `sync`) |
| Unit and component (all) | `npm test` | **622 tests: 619 passed, 3 failed**. The 3 were "Test timed out in 5000ms" while the machine was overloaded (each took 11–15 s of wall time; one was an F5 test). Each passes on its own. Three further runs of the two large files each timed out a *different* 1–2 tests, and every one of those passes alone too (see R3.5). No product failures |
| Browser, real backend | `npx playwright test -c playwright.real.config.ts e2e-real/reel-editor.spec.ts --workers=2` | Full run: 23 tests, 21 passed, 2 failed (audio bar after reload, keyboard: both explained in R3.5; both pass on rerun). Then one test was added for BUG-068. Final state: **24 tests, 23 pass, 1 fails (BUG-068)** |

**BUG-065, BUG-066 and BUG-067 are fixed.** BUG-067's browser test, with the mock's unplayable clip, now shows "The preview couldn't load one of its files." and Reload preview, which fetches B12 again.

## R3.2 Tests changed on purpose

- **Back to the script** is a button, because it saves first. The header test clicks it and expects the Script editor.
- The stand-in player reports a broken file through `inputProps.onMediaError`, as the shared renderer's video, audio and image elements now do. The message (now a `role="alert"` under the player) and Reload preview are checked through that path. A new test checks that `errorFallback` (drawing errors) says "The preview couldn't be shown."

## R3.3 New tests

Component:
- **Hidden tab panels keep a failed caption fix:**
  - it stays through a tab switch and still counts for the leave warning;
  - while saving keeps failing, Generate stops with the reason;
  - when the server recovers, the next Generate sends the fix from the hidden panel first, then starts;
  - back on the Text tab the fix is there, and the warning clears.
- **Only the shown panel is exposed** to assistive technology (one `tabpanel` role; hidden fields aren't reachable), and every tab's `aria-controls` points at a panel on the page.
- **Back to the script saves first:** while the save is in flight it waits; afterwards it opens the Script editor with the text saved. A save that fails keeps the editor open with "Some changes aren’t saved yet: …" and the text in the box.
- **Thumbnail links kept:** while the project is polled and each answer carries a new thumbnail link, the scene row keeps its first link.
- **A failed slider save is retried:** the value stays on the slider, the audio bar shows the error, and leaving still warns. The next Generate sends the value again before it starts. Another test: leaving the editor after a failed slider save sends it again.
- "For an inspiring Reel" (round-2 test) passes.

Browser:
- **Back to the script, real backend:** text typed a moment before clicking Back is saved, then the Script editor opens.
- **The player does not take focus** (fails: BUG-068).

## R3.4 New bug

### BUG-068 Medium (accessibility): the player takes keyboard focus when it appears and whenever playback starts or stops

- **Test:** `e2e-real/reel-editor.spec.ts`, "the player does not take focus: not when it appears, not when the preview stops by itself". Both soft checks fail. Seen in 3 separate runs, two of them exploratory runs (deleted afterwards).
- **Steps, case 1:** open the editor; before the preview has loaded (slow network, or the timeline answer held back as in the test), put focus on scene 1 in the list, for example with Tab. When the player appears, focus moves to its **Play video** button. The next Tab goes to "Mute sound" instead of scene 2.
- **Steps, case 2:** press Play in the preview (AI-image project), click into **Visual prompt** and keep typing. When the preview reaches its end and stops, focus jumps to **Play video**. What is typed afterwards ("later") never reaches the field, and a space would start the preview again.
- **Expected:** focus stays where the creator put it (WCAG 2.4.3 Focus Order, 3.2.1 On Focus).
- **Actual:** focus moves to the player's Play button.
- **Cause:** Remotion's player controls (`@remotion/player` 4.0.530, `dist/esm/index.mjs` around line 2323) run `playButtonRef.current.focus({preventScroll: true})` in an effect on `[playing, spaceKeyToPlayOrPause]`. That runs on mount and on every play or pause, whenever `spaceKeyToPlayOrPause` is true, which is the default. The editor's `<Player>` doesn't set it. Passing `spaceKeyToPlayOrPause={false}` should stop it; the Play button still works with Enter and Space when it has focus. The earlier keyboard test only passed because the player had loaded before the test focused anything; that test now waits for the player, so it keeps testing keyboard order.

## R3.5 Flaky, skipped, notes

- **Component timeouts under load:** during this round the machine was heavily loaded (other processes; the stack's worker health check was timing out). Full runs of `ReelEditorPage.test.tsx` and `ScriptEditorPage.test.tsx` timed out 1–3 different tests each time ("Test timed out in 5000ms" after 11–15 s of wall time, which means the event loop was starved). All of them pass alone, and the F6 files passed 92 of 92 in a quieter moment. On a normal machine I'd expect a clean run. If CI is slower, raising Vitest's `testTimeout` would help, rather than changing tests.
- **Browser run:**
  - "audio bar … after a reload" couldn't find the audio bar for 15 s after `page.reload()`. An exploratory run showed the region present after a reload, and the test passed on rerun: the dev server was slow under load.
  - The keyboard test failure was BUG-068, case 1. The test now waits for the player before focusing.
- Nothing is skipped. The exploration specs were deleted.

---

# Round 4 (BUG-068 fix: `spaceKeyToPlayOrPause={false}`; Vitest `testTimeout` 15 s)

`npm test`: **622 tests, 622 passed**, so the round-3 load timeouts are gone. Browser tests on a fresh `rff6` stack (archive plus the same uncommitted `generation.py`, `visuals.py` and `renderer/src`; taken down afterwards with `down -v`): "the player does not take focus…" and "keyboard: scenes and inspector tabs work without a mouse", **2 passed**. **BUG-068 is fixed.** No new bugs, and no tests were changed.
