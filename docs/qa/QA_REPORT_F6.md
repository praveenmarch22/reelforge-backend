# ReelForge: QA report for F6 (Reel editor with live preview)

| | |
|---|---|
| Date | 2026-11-26 |
| Milestone | F6: Reel editor (`/app/p/:id/edit`) |
| Spec | [SCREENS_F5_F6.md](../SCREENS_F5_F6.md) §3 and §4; API design B4, B5, B10–B12, C2, C7–C10, D3, D4, H1; PRD V1–V9, VI1–VI6, C1–C5, edge cases G-\*, CA-\*, AU-\*; [PIPELINE_STRATEGY.md](../PIPELINE_STRATEGY.md); [FIX_LOG_F5.md](FIX_LOG_F5.md) (autosave rules) |
| Build tested | `frontend/` working tree (F6 uncommitted on top of `bc606b3`); `backend/renderer/src` working tree (uncommitted on top of `459d2c5`) |
| Backend | Isolated copy of `backend` HEAD as compose project `rfqa6` (API 5300, MinIO 9300, Mailpit 8325), every provider `mock`, rate limits off, no `.env`. Presigned links use `S3_PUBLIC_ENDPOINT_URL=http://localhost:${MINIO_PORT}`, so the browser loaded media from the isolated MinIO on 9300. Music library seeded with `flask music-seed` (downloads the 15 CC BY tracks from incompetech.com; no AI or stock provider was called). Vite `--mode real` on 5175 proxying to 5300. All torn down afterwards (containers, volumes, images) |
| Browser | Playwright driving installed Chrome (`channel: 'chrome'`, for H.264), 1440 × 900 and 375 × 812 |

## Verdict: pass with issues

The editor does what screen 11 describes for the main flow. The player plays the timeline, and selecting a scene seeks to it. Scenes that aren't made yet show their mood placeholder. **Generate missing** shows the B10 cost, saves unsaved text first and polls every 2 s. A failed scene gets a red card and **Retry**, and `needs_attention` shows its notice. Regenerate voice and visual show the same costs the server reserves, including the B7 rule for AI video. CA-4 asks before replacing hand-fixed words. Stock search lists results with the author and their source and uses the clicked clip. A 402 shows the shared message.

Two findings lose what the creator typed. A failed save is thrown away silently when the creator selects another scene (QF6-001). Caption-word fixes are never autosaved, so they disappear on any tab or scene switch (QF6-002). Fix both before committing. The rest are Minor or Polish.

| Severity | Count | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 2 | QF6-001, QF6-002 |
| Minor | 10 | QF6-003 to QF6-012 |
| Polish | 5 | QF6-013 to QF6-017 |
| Questions | 6 | Section 4 |

**Verified column:** *Run* means reproduced in the browser against the isolated real backend. *Read* means concluded from the code only. Slow and failing saves were simulated with `page.route`. A zero balance was set directly in the QA database.

**Environment notes**

- The mock stock provider stores a 2,048-byte placeholder as the "clip". No browser can decode it (MediaError code 4), so in this setup stock scenes show a black frame. Real clip playback is therefore not verified here. The developer reports it working with real Pixabay clips. See also F7/B5 note 3.
- `npx tsc --noEmit` reports 4 errors, all in `src/features/editor/ReelEditorPage.test.tsx` (`Array.prototype.at`). That file appeared during the review and is the Tester's work in progress, so it is not counted. Product code type-checks, and so does the renderer (`backend/renderer`, `tsc --noEmit`).
- `src/remotion/sync.test.ts` passes: all 5 files are identical to `backend/renderer/src`.
- The Tester's stack `rff6` and the main stack were not touched.

---

## 1. Findings

### QF6-001 Major: text whose save failed is thrown away, with no message, when another scene is selected

| | |
|---|---|
| Where | `src/features/editor/ReelEditorPage.tsx:163` (`<Inspector key={selected.id}>` unmounts the inspector on every scene switch); `src/features/create/autosave.ts:115-124` (on unmount: one last flush, then `markUnsaved(-1)` even if it failed) |
| Requirement | SCREENS_F5_F6 §4: "what you type is never replaced… closing the tab warns"; FIX_LOG_F5 QF5-017: "A failed save keeps its text unsaved: the card shows the error, the next blur, Generate or rewrite sends it again, and closing the tab still warns" |
| Verified | Run |

**Steps.** Make `PATCH /scenes/:id` answer 500. In scene 1, Text tab, type "Lost text test" into On-screen text. The field shows the error. Click scene 2, then click scene 1 again. Let saves succeed again.

**Expected.** The unsaved text is kept and still shows as not saved. It is sent again later, and closing the tab warns.

**Actual.** No error is shown after the switch. Back on scene 1 the box shows the old server text ("Wake up and hydrate"). The typed text is gone, and `beforeunload` no longer warns (checked: `defaultPrevented` false). The same happens with Narration, Visual prompt and Stock search words, and on leaving the page.

**Why it matters.** In F5 a card only disappeared when its scene was deleted, so dropping its unsaved text on unmount was acceptable. In the editor the inspector unmounts on every scene click. A spotty mobile connection plus one click on the next scene loses the creator's words without a trace.

**Fix.** Keep the per-field autosave state outside the inspector, keyed by scene id and field, in a small store or a context above `Inspector`. Then switching scenes neither unmounts nor discards it, the unsaved count stays up, and the error is still shown when the creator comes back. At minimum, if the final flush fails, keep the field counted as unsaved and show a page-level notice naming the scene ("Scene 1 text isn't saved · Retry").

### QF6-002 Major: caption-word fixes are not autosaved and vanish on any tab or scene switch

| | |
|---|---|
| Where | `src/features/editor/Inspector.tsx:310-343` (`CaptionWords` keeps its draft in local state and saves only on **Save caption fixes**; it is unmounted when the Text tab is left) |
| Requirement | SCREENS_F5_F6 §3 Autosave: "Every edit saves at once (or after 1 s of typing)"; §3 Text tab ("fix a word's spelling, times kept"); §4 "Generate … waits for unsaved text first"; PRD C3 |
| Verified | Run |

**Steps.** Ready project, scene 2, Text tab. Change caption word 1 from "Most" to "Mostt". Click the Voice tab, then the Text tab again. Separately: change it, click scene 3, then click scene 2.

**Expected.** The fix is saved, like every other field in the editor.

**Actual.** Both times the word is back to "Most". Nothing was sent and nothing warned. The fix is also not part of `flushAll`, so Generate does not send it, and closing the tab does not warn.

**Why it matters.** This is the one field in the editor with a manual Save button, and leaving it unsaved costs the creator their work. Caption spelling fixes are exactly the small, fiddly edits people make in bulk.

**Fix.** Save the word list through `useAutosavedField` or the same mechanism: debounce 0.8 s, also save on blur, register a flusher, and count it as unsaved. Keep the "Fixed by hand" note. If the manual button is meant to stay, keep the draft above the tab panel and include it in `flushAll` and the leave warning.

### QF6-003 Minor: moving both volume sliders within half a second drops the first change; a pending slider change is lost on leaving

| | |
|---|---|
| Where | `src/features/editor/AudioBar.tsx:23-35` (one `timer` for both sliders; cleared on unmount, line 24) |
| Requirement | SCREENS_F5_F6 §3 Audio bar (voice and music volume) and Autosave ("Every edit saves at once"); §4 (leaving sends unsaved changes, closing the tab warns); PRD V9 |
| Verified | Run (two sliders); Read (unmount, tab close) |

**Steps.** Focus Voice volume and press ←, then within 0.5 s focus Music volume and press ←.

**Expected.** Two saves: `voiceVolume 0.95` and `musicVolume 0.2`.

**Actual.** Only `{"settings":{"musicVolume":0.2}}` is sent. The server keeps `voiceVolume: 1`, and the voice slider jumps back when the project is next fetched. A slider change made less than 0.5 s before leaving the page is also dropped. It isn't counted as unsaved, so closing the tab doesn't warn.

**Fix.** Use one timer per key, or save `{voiceVolume, musicVolume}` together from the latest local state. Send pending values on unmount and register them with `registerFlusher` and `markUnsaved`.

### QF6-004 Minor: scenes can't be reordered in the editor

| | |
|---|---|
| Where | `src/features/editor/SceneList.tsx` (no drag handle, no move up or down) |
| Requirement | SCREENS_F5_F6 §3 Scene list: "Click selects the scene; drag reorders" (C5) |
| Verified | Read; Run (no handle on screen) |

**Workaround.** Go back to the Script editor and reorder there. That is why this is Minor. The creator does lose the preview while doing it.

**Fix.** Reuse the Script editor's reorder (drag handle plus a keyboard move up or down) and `useReorderScenes`. The optimistic update is already there.

### QF6-005 Minor: the audio bar has no Narrator (project voice)

| | |
|---|---|
| Where | `src/features/editor/AudioBar.tsx` |
| Requirement | SCREENS_F5_F6 §3 Audio bar: "Narrator (project voice), music track …"; API B5 `voiceId` (marks all voices outdated); PRD V1, V4 ("regenerate the voiceover for the whole video") |
| Verified | Run (1440 and 375 screenshots); Read |

The inspector's per-scene "Voice for this take" (C7) is there. The project-wide narrator is not, so changing the whole Reel's voice means going back to the Script editor.

**Fix.** Add `VoicePicker` (label "Narrator") to the audio bar, saving `{voiceId}` through `useUpdateProject`. It already exists in the Script editor. The voices then turn outdated and **Generate missing** appears with its B10 cost.

### QF6-006 Minor: after changing the music mood, the track list says "Automatic: Inspired" while a fixed track keeps playing

| | |
|---|---|
| Where | `src/features/editor/AudioBar.tsx:37-78` |
| Requirement | SCREENS_F5_F6 §3 Audio bar: "music track (D4 list by mood, preview)"; API B5, B11 ("a library track tagged with its `musicMood` is chosen"), B12 (`music` names what plays); PRD V7, V8 |
| Verified | Run |

**Steps.** Generated project (Generate stored `music: {trackId: Inspired}`). In the audio bar, change Music mood from Inspiring to Sad.

**Actual.** The project's music is still `{trackId: Inspired}` and the timeline still plays Inspired. The track select now lists the Sad tracks and shows its first option, **"Automatic: Inspired"**. That is wrong twice: the music is not automatic, and Inspired is not a Sad track. The creator has no way to see that their mood change did nothing to the music. Preview is offered only for the track already chosen, not for the tracks in the list.

**Fix.** Give the chosen track its own option when it isn't in the mood's list (for example "Inspired (chosen)"). Decide what a mood change should do with a fixed track (Question 4). Let any listed track be previewed before it is chosen.

### QF6-007 Minor: a scene's saves are not one at a time; coming back to a scene while its save is in flight shows the old text

| | |
|---|---|
| Where | `src/features/create/autosave.ts:54-133` (one chain per field and per inspector instance); `Inspector.tsx:81-84` |
| Requirement | SCREENS_F5_F6 §4: "one save at a time per scene; what you type is never replaced by an older answer"; FIX_LOG_F5 QF5-001 |
| Verified | Run (switch back); Read (cross-field ordering) |

**Steps (Run).** Delay `PATCH /scenes/:id` by 3 s. In scene 1, set On-screen text to "Version A" and wait 1 s (the save is in flight). Click scene 2, then scene 1.

**Actual.** The box shows the previous server text, not "Version A", until the delayed answer lands. Anything the creator types in that window starts from the old text. In this run the later typing replaced the whole field, so nothing was lost. Edits to part of the text would lose "Version A".

**Read.** Each field has its own save chain, so two fields of one scene can be saving at once. The server locks the row, so the data stays correct. But each answer's `putScene` writes the *whole* scene into the cache. An answer that committed first but arrives last puts an older value of the other field into its box, if that field has nothing pending at that moment. That is the "older answer" case §4 rules out.

**Fix.** One queue per scene id, module-level and keyed by scene, shared by all of that scene's fields and surviving remounts. Patch only the field that was sent into the cache, rather than the whole scene. QF6-001's fix, a store above the inspector, covers the remount part.

### QF6-008 Minor: every timeline refetch makes the player download all media again; thumbnails reload on every poll

| | |
|---|---|
| Where | `src/features/editor/ReelEditorPage.tsx:27-30, 138-150`; `src/api/projects.ts:320-329`; `SceneList.tsx:51,70` |
| Requirement | API design, shared objects: links are "different on every response (key media by `assetId`)"; SCREENS_F5_F6 §3 Player |
| Verified | Run |

**Observed.** One On-screen text edit led to 1 timeline request and 3 new media requests: voice, clip and music, all with new signatures. One caption-style click led to 5. The timeline is fetched again after every autosave, as it should be, because `timelineVersion` goes up with every edit. But B12 signs every link afresh, so each `src` changes and the `<audio>` and `<video>` elements reload. With real files, a 3–9 MB music track and several MB per Pixabay clip, that means repeated downloads on a phone and a hiccup if the creator types while the preview plays. While generating, the 2-s project poll also gives every `thumbnailUrl` a new signature, so the scene list images reload every 2 s.

**Fix.** Keep a client-side map from asset id to the first URL seen and its time, and reuse it until it is about 1 h 45 min old. For this, B12 should carry the `assetId` of each scene's voice and visual (music already has `trackId` or `assetId`). Alternatively, the API could sign links on a fixed time window so they stay identical between calls.

### QF6-009 Minor: expired links and media errors leave a silent or black player with no message

| | |
|---|---|
| Where | `src/api/projects.ts:320-329` (`staleTime` 1 h, no `refetchInterval`); `ReelEditorPage.tsx:138-150` (no Player error handling) |
| Requirement | API B12 ("links valid for 2 hours"); SCREENS_F5_F6 §3 Player |
| Verified | Run (media error behaviour); Read (expiry timing) |

**Run.** With a clip the browser can't decode, the player shows a black frame for that scene and keeps going. The page gets an uncaught error each time ("The browser threw an error while playing the video … Pass an onError() prop to handle the error") and the creator sees nothing.

**Read.** The timeline is fetched again only when an edit or status change alters the version key, or when the window regains focus after the 1-h `staleTime`. If the editor stays open and focused for more than 2 hours without edits (a second monitor, or a long pause), the links expire. Media not yet buffered, or any seek that needs a new range, then gets 403 and fails silently.

**Fix.** Set `refetchInterval` on the timeline to about 90 min. Listen to the Player's `error` event (`playerRef.addEventListener('error', …)`), refetch the timeline once, and show "The preview couldn't load a file · Reload" if that doesn't help. Don't add a swallowing `onError` to the shared renderer files, because a render must still fail on a broken file.

### QF6-010 Minor: on the editor the whole page scrolls, so on a phone the app shell slides away and leaves an empty screen

| | |
|---|---|
| Where | `src/features/editor/SceneList.tsx:91-93` (`<span className="sr-only">` inside the scene button, which has no positioned ancestor inside the scrolling `<main>`) |
| Requirement | SCREENS_F5_F6 §3 Layout (the app shell); phone layout as in F5 (QF5-004) |
| Verified | Run |

**Observed.** At 375 × 812 the document's scroll height is 2,173 px, while `<main>` is the only intended scroller. On the Script editor the same check gives 812 px. The `sr-only` status texts are `position: absolute` and are placed relative to the page, not the scroll area, so they make the page itself taller. A scroll gesture that starts on the sidebar, or that continues past the end of `<main>`, scrolls the whole shell up and leaves a blank screen with only the background (screenshot `s10-375-1`).

**Fix.** Add `relative` to the scene button, or to its `<li>`, so the hidden text stays inside the scroll area. Check the audio bar's `sr-only` labels the same way.

### QF6-011 Minor: the scene list shows the whole narration, not the first words

| | |
|---|---|
| Where | `src/features/editor/SceneList.tsx:86` (`line-clamp-2 block`: `block` overrides the `display: -webkit-box` that `line-clamp-2` needs) |
| Requirement | SCREENS_F5_F6 §3 Scene list: "the first words" |
| Verified | Run |

With 70-word scenes, each card holds all 70 words. At 375 px, four such cards take about 1,400 px between the player and the inspector, so after picking a scene the creator scrolls a long way to edit it. With the Retry button at 1440 px, the text wraps into a narrow column.

**Fix.** Remove `block`, so `line-clamp-2` works.

### QF6-012 Minor: the inspector tabs don't work like tabs from the keyboard

| | |
|---|---|
| Where | `src/features/editor/Inspector.tsx:139-159` |
| Requirement | No project document sets an accessibility level (Question 5). Judged against the WAI-ARIA tabs pattern, which the code claims by using `role="tablist"`/`"tab"` |
| Verified | Run |

Focusing **Visual** and pressing → does nothing. All three tabs have `tabIndex 0`, so Tab visits each one. The unselected tabs' `aria-controls` point at ids that don't exist, because only the active panel is rendered. Screen-reader users hear "tab" but the keys tabs use don't work.

**Fix.** Use a roving `tabIndex` (0 on the selected tab, −1 on the others), handle ←/→/Home/End, and point `aria-controls` only from the selected tab, or render all three panels and hide the inactive ones.

### QF6-013 Polish: "Saved / Saving…" is shown under each field, not in the header

| | |
|---|---|
| Where | `Inspector.tsx:55-65` |
| Requirement | SCREENS_F5_F6 §3 Autosave: "a small 'Saved' or 'Saving…' note in the header" |
| Verified | Run |

Volume, music, caption-style, mood and source changes show no saved state at all. **Fix.** One header note fed by the unsaved counter and the in-flight saves. Keep per-field errors where they are.

### QF6-014 Polish: the CA-4 warning isn't on the Voice tab, and it uses the browser's `confirm`

| | |
|---|---|
| Where | `Inspector.tsx:109-120, 339` |
| Requirement | SCREENS_F5_F6 §3 Voice tab: "the CA-4 warning when caption words were fixed"; PRD CA-4 |
| Verified | Run |

The warning works: the dialog says the new voice replaces the hand-fixed words, the request carries `confirmReplaceWords: true`, and `wordsEdited` is false afterwards. But the note ("Fixed by hand…") is only on the Text tab. The Voice tab says nothing until **Regenerate voice** is clicked, and then a native, unstyled `window.confirm` appears. **Fix.** Show the note above **Regenerate voice** on the Voice tab, and use the app's own confirm dialog.

### QF6-015 Polish: scene-list progress and Retry busy state

| | |
|---|---|
| Where | `SceneList.tsx:71-75, 97-107`; `ReelEditorPage.tsx:131` |
| Requirement | SCREENS_F5_F6 §3 Scene list: "a progress ring while generating" |
| Verified | Run (spinner); Read (shared `retrying`) |

There is a spinner, not a progress ring. Job `progress` is available from B4 `activeJobs`. Every failed card's **Retry** shows a spinner when any one of them is pressed, because they all share `generate.isPending`. **Fix.** Show a ring from the scene's job `progress`, and track the retrying scene id.

### QF6-016 Polish: the Render note is only a tooltip on a disabled button

| | |
|---|---|
| Where | `ReelEditorPage.tsx:107-109` |
| Requirement | SCREENS_F5_F6 §3 Header: "Render (disabled until F7/B5, with a note)" |
| Verified | Run |

A `title` on a disabled button is not shown on touch screens, is not focusable, and many screen readers don't announce it. **Fix.** A short visible note next to it ("Rendering comes next"), or `aria-disabled` with `aria-describedby`.

### QF6-017 Polish: audio-bar details

| | |
|---|---|
| Where | `AudioBar.tsx:75, 97-106`; `ReelEditorPage.tsx:157-159` |
| Requirement | SCREENS_F5_F6 §3 Audio bar, Player |
| Verified | Run |

- The volume sliders show no value, and a screen reader reads "0.95" (no `aria-valuetext` such as "95%").
- Track lengths are rounded to minutes: Fluffing a Duck (67 s) reads "(1 min)", and a 25-s track would read "(0 min)". Use m:ss.
- At 375 px the track select shrinks to "Inspir…".
- "Scenes still being made show a coloured placeholder" is shown whenever the timeline isn't `ready`, including for failed scenes and never-generated drafts, where nothing is "being made". An outdated visual also keeps its old picture, not a placeholder. Suggested wording: "Scenes without a voice or picture yet show a coloured placeholder."

---

## 2. What passed

| Area | Check | Verified |
|---|---|---|
| Route | `/app/p/:id/edit` renders the editor; a `scripting` project redirects to the Script editor; 404 goes to Projects | Run (route); Read (redirects) |
| Layout 1440 | Header (back, title, status pill, credits in the top bar, Generate missing, Render), scene list, 9:16 player, inspector, audio bar | Run |
| Layout 375 | No horizontal overflow; order: player, scene list, inspector, audio bar (but see QF6-010, QF6-011) | Run |
| Player | Plays (0:00 → 0:02 after 3 s); shows current time and total (0:14); captions word by word; caption style switch (Neon) shows in the preview after the refetch | Run |
| Seeking | Selecting scene 3 (start 8.2 s) moves the player to 0:08; scene 1 back to 0:00 | Run |
| Placeholders | Draft: every scene shows its mood gradient; a failed voice has `src: ""` and `durationSeconds: null` in B12 and the timeline still plays | Run |
| Polling | 2 s while `generating`; 2 s while one scene's job runs in a `ready` project (`activeJobs`); stops when done; the timeline is fetched again when statuses change | Run |
| Generate missing | Shown for missing, outdated or failed parts; label equals B10 (2 = 2); sends unsaved text first (PATCH, then POST `/generate`); `Idempotency-Key` per click | Run; Read (key) |
| Failure | Failed voice: red card, **Retry** posts `{"sceneIds":[…]}`; `needs_attention` notice; editing the narration then Generate missing brings the project to `ready` | Run |
| tooLong | 113.2 s timeline shows "over 90 s: shorten it before rendering" | Run |
| Costs (H1, C7, C8) | Voice "1 credit" = job reserved 1; AI image "1 credit" = 1; AI video "1 credit" = 1 (the B7 rule); stock "free". Costs come from `GET /credits` | Run |
| CA-4 | Confirm before replacing hand-fixed words; `confirmReplaceWords: true`; `wordsEdited` false after the new voice | Run |
| Stock search (C9, C10) | Results grid with author and duration; "Videos from …" from each result's `source`; selecting posts `{"stock":"mock:3"}`, the scene's visual changes, the in-use tick moves | Run |
| 402 | "This needs 1 credit and you have 0. Your credits renew on December 1." | Run |
| Leaving | Typing then clicking Back within 0.8 s saves the text | Run |
| Upload, Render | Upload chip disabled with "Uploading your own file comes later."; Render disabled | Run |
| AI video note | "An AI image with slow motion, until AI video clips arrive." | Run |
| Ducking off | B12 sets `duckedVolume` = `volume`; the checkbox saves `settings.ducking` | Read; Run (save) |
| Keyboard | Tab order is logical (back, credits, scenes, player, inspector, audio bar); focus is visible on every control | Run |
| Copies | `sync.test.ts`: the 5 files match `backend/renderer/src` | Run |

**Renderer changes (Read), checked against B5's use:**

- `musicVolume` skips scenes with no voice length. Before, `null × fps` gave a zero-length voice, ducking the music for ±8 frames around each placeholder scene. Correct, and harmless for a render, because E1 refuses timelines that aren't ready.
- Trims: `startFrom = trimStart × fps`. In Remotion 4.0.530, `startFrom` (the older name for `trimBefore`) is counted in source frames and is not scaled by `playbackRate` (`OffthreadVideo` wraps the clip in `<Sequence from={-trimBefore}>`, and the media time is `trimBefore + frame × rate`). So the "fit a short clip" maths works on the trimmed length and stops at `trimEnd`. `trimEnd <= trimStart` can't reach the renderer, because C2 refuses it.
- Type changes are additive and optional (`ready`, `transition`, `trimStart`/`trimEnd`, music `trackId`/`assetId`/`title`/`license`, nullable voice length). The renderer type-checks, and a timeline from before the change still renders.

---

## 3. What F7 and B5 need (from this review)

1. **Render button (F7).** Enable it only when B12 has `ready: true` and `tooLong: false`. Call `flushAll()` first, send an `Idempotency-Key`, and handle `409 not_ready` (mark `details.scenes` in the scene list), `409 too_long`, `409 already_running` and 402. Since `timelineVersion` goes up with every edit, settings included, the render screen should say "this render is older than your edits" after any change.
2. **Media for the render service (B5).** The saved timeline holds storage keys (`links=False`), and `resolveSrc` treats anything that isn't `http(s)` as `staticFile`. B5 must turn the keys into URLs the render container can reach: the internal endpoint (`http://minio:9000`), not the public `localhost` presign. Alternatively, download the files first. Watch the 2-h expiry for queued renders.
3. **Mock clips can't be decoded.** The mock stock provider stores 2,048 placeholder bytes as an `.mp4`. The preview already fails on them (MediaError 4), and `OffthreadVideo` will fail a render. Offline and CI renders in B5 need the mock to store a real, tiny MP4.
4. **`transition`** is in the timeline type and in B12, but `Reel.tsx` doesn't draw it. Before B5, either draw `fade` and `slide` or remove them.
5. **Music.** It is looped with a hard loop. AU-4 (loop with a crossfade), AU-5 (fade-out, done by `fadeOutSeconds`) and AU-7 (normalise to about −14 LUFS) belong to the render. `timeline.music.license` is the credit line F7 adds to the caption.
6. **Copy check.** `sync.test.ts` is skipped wherever the backend isn't checked out next to the frontend, which includes the frontend's own CI. B5 will change `renderer/src`, so run the same comparison in the backend's CI too.
7. **Media errors.** Keep the shared renderer files free of error-swallowing `onError`. Handle preview errors at the Player level (QF6-009).

---

## 4. Questions for the product owner

1. **Autosave delay.** §3 says "after 1 s of typing", §4 says "after 0.8 s". The code uses 0.8 s. Which is right? Correct the other line.
2. **Safe zone (PRD CA-2).** "Show the safe zone in the preview" is not in screen 11's spec and is not built. Is it deferred, and if so to which milestone, or is it part of F6?
3. **Pixabay credit.** API C9 asks for "Videos from Pixabay", which is built (plain text, plus the author on each result and "Clip by <author>" linked to the clip's page). Please check Pixabay's API terms. If they also require a link to Pixabay wherever results are shown, the footer text should become a link.
4. **Music after a mood change.** Generate saves the track it picked as the project's fixed music, so changing the mood afterwards never changes the track. Should a mood change clear a track that Generate picked, so the new mood's track plays, or only change the list (QF6-006)?
5. **Accessibility target.** No document sets one. Earlier reviews and this one judge against WCAG 2.1 AA and the WAI-ARIA patterns. Please confirm that target, or set another.
6. **Clip trims.** C2 accepts `trimStart`/`trimEnd` and the renderer now honours them, but screen 11 has no control for them. Are they intentionally left for later (for example with uploads in B7)?

---

## 5. Out of scope (not defects)

- Upload source disabled (D1/D2 arrive in B7); Render disabled (F7/B5); "Alternative takes" and the other Stitch inventions listed in §3 are left out on purpose.
- The full out-of-credits modal (F8); the inline 402 message is what §4 asks for now.
- The app-shell sidebar staying visible at 375 px belongs to the shell, not to F6.
- `docs/qa/TEST_PLAN.md` still lists F5 and F6 as "out of scope until built". It needs a refresh, but that is not an F6 defect.

---

## Recheck (2026-11-26, after FIX_LOG_F6)

| | |
|---|---|
| Build | `frontend/` working tree (editor/\*, `links.ts`, `VoicePicker` `useId`); backend HEAD plus the uncommitted `generation.py` and `visuals.py` (BUG-064), copied into the archived stack |
| Stack | Same isolated setup: `rfqa6` (API 5300, MinIO 9300, Mailpit 8325), every provider `mock`, music seeded, Vite `--mode real` on 5175, installed Chrome through Playwright. Torn down afterwards |
| Type check | `tsc --noEmit`: 0 errors |

### Verdict: pass with issues

Both Majors are fixed on the path a creator normally takes. A failed save now keeps the creator on the scene with the error showing. Caption fixes save as they are typed and survive tab and scene switches. The player no longer downloads its files again after an autosave. The sliders, the music list, drag reorder, the narrator, the tabs and the phone layout all work.

What is still open:

- The player's error fallback never appears in a real browser (QF6-009, still open).
- A failed save is still lost in two narrower cases: a caption fix on a tab switch, and leaving the page (QF6-018, new).
- The scene-list thumbnails are still downloaded again after each edit (QF6-019).

None of these blocks the main flow. All are Minor or Polish.

### Results per finding

| Finding | Result | Verified |
|---|---|---|
| QF6-001: failed save, then another scene | **Fixed.** I made `PATCH /scenes` answer 500. Clicking scene 2 keeps scene 1 open with the typed text, and the error "Some changes aren't saved yet: boom" appears at the top and under the field. Closing the tab warns. Once saves work again, the next click saves the text and moves on. Leaving the page is still open (QF6-018) | Run |
| QF6-002: caption words | **Fixed while saves work.** "Mostt" survives Voice → Text, and "peoplee" survives scene 3 → scene 2. The server has both, with `wordsEdited: true`. An emptied word shows the API's "Required". A save that fails on a tab switch still loses the fix (QF6-018) | Run |
| QF6-003: sliders | **Fixed.** ← on Voice, then ← on Music within 0.5 s, gives two saves (`voiceVolume 0.95`, `musicVolume 0.2`). An unsent slider move makes closing the tab warn. It is also sent when the creator clicks Back within 0.5 s (server `voiceVolume 0.9`) | Run |
| QF6-004: reorder | **Fixed.** Dragging scene 1 onto scene 3 saves the new order (C5) | Run |
| QF6-005: narrator | **Fixed.** The audio bar's "Narrator" asks first. After OK the project voice changes, all four voices become `outdated` and **Generate missing** appears | Run |
| QF6-006: mood with a fixed track | **Fixed.** After Inspiring → Sad, the select still names "Inspired (4:46)", which is what plays. The list has two groups: "For a sad Reel" (3 tracks) and "Other moods" (12). The license line is shown | Run |
| QF6-007: coming back mid-save | **Fixed.** Switching scene now waits for the scene's saves | Read; Run (QF6-001 steps) |
| QF6-008: re-downloads | **Fixed for the player.** With nothing playing, an On-screen text autosave and a caption-style change requested no voice, picture or music file again. Thumbnails are still requested again (QF6-019) | Run (network log) |
| QF6-009: player error fallback | **Not fixed** (details below) | Run |
| QF6-010: page scroll on a phone | **Fixed.** At 375 × 812 the document is 812 px high. Scrolling over the sidebar, or past the end of `<main>`, doesn't move the shell | Run |
| QF6-011: two-line clamp | **Fixed.** At 375 px the 4-scene list is 361 px high | Run |
| QF6-012: tabs | **Fixed.** There is one Tab stop: only the selected tab has `tabIndex` 0. → and ← (with wrap-around), Home and End move both the selection and the focus. Only the selected tab has `aria-controls` | Run |
| QF6-013, QF6-015 | Answered in FIX_LOG_F6 (the spec now says the note sits under each field; the API gives no per-scene percentage) | — |
| QF6-014 | Deferred to F9 (a styled CA-4 dialog) | — |
| QF6-016: Render note | **Fixed.** "Rendering to a video comes next" is visible text, linked by `aria-describedby` | Run |
| QF6-017: audio bar | **Fixed.** Sliders read "95 percent" (`aria-valuetext`), lengths show as m:ss ("4:46"), and the track's license line is shown | Run |
| BUG-063: tab kept on scene change | Fixed. Text stays selected after picking scene 3 | Run |
| BUG-064: a failed voice's reason | Fixed. A scene whose voice fails (`[voice-fail]`) keeps its `lastError` after its stock or AI-image visual succeeds, and the project is `needs_attention` | Run (API) |
| Questions 1–6 | Answered in FIX_LOG_F6; nothing further | — |

### QF6-009 Minor (still open): the error fallback never appears in a real browser

| | |
|---|---|
| Where | `ReelEditorPage.tsx:191-200` (`errorFallback`) and `209-222` (**Reload preview**) |
| Requirement | API B12 (links valid for 2 hours); SCREENS_F5_F6 §3 Player; FIX_LOG_F6 QF6-009 |
| Verified | Run (installed Chrome and Playwright Chromium) |

**Steps and results.**

1. Generated stock project (the mock clip can't be decoded). Wait 8 s, then play. The page gets the uncaught error "The browser threw an error while playing the video …" (Remotion's MediaError). The preview keeps drawing the captions over a black frame. "The preview couldn't load one of its files" never appears, and there is no **Reload preview** button.
2. AI-image project, with every media request answered 403 "Request has expired", which is what an expired link returns. Reload, then play. The clock runs (0:02 / 0:14) over a black, silent player, with no fallback.

**Cause.** `errorFallback` is the Player's error boundary, and it only catches errors thrown while React renders. Remotion reports media errors from the media element's event handler, outside rendering, so they never reach the boundary. Images and audio that fail to load don't throw at all.

**Fix.** Let the preview handle media errors itself. For example, give the shared `Reel` an optional `onMediaError` prop and pass it to `<OffthreadVideo onError>`, `<Audio onError>` and `<Img onError>`. The editor sets it to show the notice and **Reload preview**. The render service leaves it unset, so a render still fails on a broken file. Cover it with a browser test that serves 403 for media, not with a unit test that throws during render.

### QF6-018 Minor (new, the rest of QF6-001): a failed save is still lost on a tab switch (caption words) and on leaving the page

| | |
|---|---|
| Where | `Inspector.tsx` `CaptionWords`: its `useAutosavedField` lives inside the Text tab and unmounts when the tab changes. `autosave.ts:115-124` is unchanged: on unmount the field stops counting even if its last save failed |
| Requirement | SCREENS_F5_F6 §4; FIX_LOG_F5 QF5-017; QF6-001 ("the same happens … on leaving the page") |
| Verified | Run. The Tester's own test "a caption fix whose save failed is not lost by switching to another tab and back" fails for the same reason |

- **Caption word, tab switch.** With saves failing, change caption word 3 to "neverr" and click Voice straight away. The save on unmount fails and no error is ever shown. While away, closing the tab doesn't warn. Back on Text the word reads "never" again, as on the server.
- **Leaving the page.** With saves failing, type in On-screen text and wait for the error under the field. Click **Back to the script**. The page changes, and the text never reaches the server.

**Fix.**
- Move the caption words' `useAutosavedField` up into `Inspector`, beside the other four fields, so it survives tab switches.
- For in-app navigation, block leaving while `flushAll()` rejects. React Router's `useBlocker` with a "Leave without saving?" prompt does this, like the scene switch already does.

### QF6-019 Polish (the rest of QF6-008): scene-list thumbnails download again after every edit

Each project refetch signs the `thumbnailUrl`s again. Two edits requested the four thumbnails twice (8 requests), and the same happens on every 2-s poll while generating. They are small images, so this is Polish. **Fix.** Pass the thumbnails through the same link reuse as the timeline (`stableLinks`, matched by path).

### Unit tests (the Tester's, not counted here)

`npx vitest run src/remotion src/features/editor`: 79 passed, 5 failed, all in `ReelEditorPage.test.tsx`:

- the caption-fix-after-failed-save test (the same as QF6-018);
- "For a inspiring Reel" instead of "For an inspiring Reel" (the mood group label);
- three timing or ARIA tests: the timeline fetched again after an edit, labelled tabs and panels, the tab kept on scene change. All three behaviours worked in the browser.

`sync.test.ts` passed.

---

## Recheck 2 (2026-11-26, after FIX_LOG_F6 "Round 3")

| | |
|---|---|
| Build | `frontend/` working tree; backend HEAD plus the uncommitted `generation.py`, `visuals.py` and `renderer/src`, copied into the archived stack |
| Stack | Same isolated setup (`rfqa6`, API 5300, MinIO 9300, all providers `mock`, music seeded; Vite `--mode real` on 5175; installed Chrome). Torn down afterwards |
| Static checks | `tsc --noEmit`: 0 errors in `frontend/` and in `backend/renderer`. The 5 copied renderer files are identical to `frontend/src/remotion` |

### Verdict: pass

Every open item from the first recheck is fixed, and I found nothing new. Every check below was run in the browser.

| Item | Result |
|---|---|
| QF6-009 / BUG-067: mock stock clip | **Fixed.** On load the preview shows "The preview couldn't load one of its files" and **Reload preview**. The page reports no uncaught errors (there were some before). After the scenes were switched to AI images and **Reload preview** clicked, the message and the button disappear, and the player plays through to 0:14 / 0:14 |
| QF6-009: every file 403 "Request has expired" | **Fixed.** Pressing play shows the message and **Reload preview**. Nothing shows before play, because nothing loads until then. With the links working again, **Reload preview** fetches fresh links (5 files), the message goes, and the player runs (0:06 after 2.5 s) |
| QF6-018 / BUG-066: failed caption fix, then a tab switch | **Fixed.** I made the save fail, typed "neverr" and switched to Voice at once. Back on Text the word still reads "neverr", with "boom" under the words, and closing the tab warns. The fix is sent again by the next action that saves everything: clicking scene 2 saved "neverr" and moved on. It is not retried on its own, the same rule as F5 (next blur, Generate, rewrite or scene switch) |
| QF6-018: Back with a failed save | **Fixed.** **Back to the script** stays on the editor and shows "Some changes aren't saved yet: boom". With saves working, typing and clicking Back at once saves the text ("Back test 2") and then opens the script |
| QF6-019: thumbnails | **Fixed.** Two On-screen text edits made 0 thumbnail requests and 0 other file requests |

**Does a real render still fail loudly on a broken file? Yes** (concluded from the code; no render was run, because rendering is B5):

- `onMediaError` is optional, and only the editor sets it, in `ReelEditorPage` (`playerProps`). A render gets its props as JSON from the saved timeline, and JSON can't carry a function, so a render never has it.
- When `onError` is undefined, Remotion 4.0.530 keeps its default during a render. `OffthreadVideoForRendering` calls `cancelRender(err)` when a frame can't be fetched or decoded. `Img` retries twice, then calls `cancelRender`. Audio failures surface when the render collects its assets. All three stop the render with an error instead of drawing black or silence.
- For B5: keep `onMediaError` out of the render service's `inputProps`, and add one render test with a missing file that expects the render to fail.

Remaining open items: only QF6-014 (styled CA-4 dialog), deferred to F9, and other in-app links such as the sidebar, which stay with F9's `useBlocker` (QF5-021), as FIX_LOG_F6 says.
