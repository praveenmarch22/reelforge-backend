# ReelForge: developer response to QA findings, F6 (Reel editor)

| | |
|---|---|
| Date | 2026-11-26 |
| Inputs | [QA_REPORT_F6.md](QA_REPORT_F6.md) (QF6-001 to QF6-017), [TEST_REPORT_F6.md](TEST_REPORT_F6.md) (BUG-057 to BUG-064) |
| Written by | The developer. The QA and test reports are left as they were written. |

## Major

| Finding | Status | What was done |
|---|---|---|
| QF6-001: a failed save was dropped on switching scene | Fixed | Selecting another scene first saves the current scene's unsaved text; if that fails, the editor stays on the scene and shows why |
| QF6-002: caption-word fixes were never autosaved | Fixed | They save as typed (0.8 s) and on leaving a word, with the same guarantees as every field: in order, kept and retried on failure, sent before Generate, counted for the leave warning |

## Minor and polish

| Finding | Status | What was done |
|---|---|---|
| QF6-003 / BUG-059 / BUG-060: sliders shared one timer; a last move was lost | Fixed | Each slider has its own timer; a move not yet sent is sent when the editor closes, counts for the leave warning and is sent before Generate |
| QF6-004 / BUG-061: no drag reorder in the scene list | Fixed | Drag a scene onto another (C5) |
| QF6-005: no narrator picker in the audio bar | Fixed | Added; it asks first, since changing it means every voice is made again |
| QF6-006: mood change showed "Automatic" over a fixed track | Fixed | The track list is the whole library (this mood's tracks first, then the others), so a chosen track is always named; "Automatic" names the track it would use only when none is chosen |
| QF6-007: a scene's fields could show old text when coming back mid-save | Fixed | Switching scene waits for the scene's saves (QF6-001) |
| QF6-008: every timeline refetch re-downloaded all media | Fixed | A file's earlier link is reused while it has more than 30 minutes left (`links.ts`), so the player keeps what it has |
| QF6-009: a broken or expired file left a black player | Fixed | The player shows "The preview couldn't load one of its files" and a **Reload preview** button that fetches fresh links |
| QF6-010: hidden text made the page scroll | Fixed | The scene row is the positioning parent of its screen-reader text |
| QF6-011: full narration instead of two lines | Fixed | The `block` that overrode the line clamp is removed |
| QF6-012 / BUG-062: tabs and the keyboard | Fixed | One Tab stop; arrow keys, Home and End move between tabs; only the shown panel is referenced |
| BUG-057 / BUG-058 (QF5-020 again): double starts | Fixed | Generate missing, Retry, Regenerate voice and Regenerate visual are busy from the first click |
| BUG-063: the tab reset on scene change | Fixed | The open tab is kept |
| BUG-064 (backend): a failed voice's reason disappeared when its visual finished | Fixed | A part that succeeds clears the scene's error only if the other part hasn't failed (`generation.py`, `visuals.py`) |
| QF6-013: "Saved" under each field, not in the header | Answered | Kept under the field it belongs to; the spec now says so |
| QF6-014: CA-4 uses the browser's confirm | Deferred to F9 | It works and is accessible; a styled dialog comes with the F9 polish |
| QF6-015: a spinner, not a progress ring | Answered | The API gives no per-scene percentage; a spinner says the same |
| QF6-016: the Render note only in a tooltip | Fixed | Visible text next to the button |
| QF6-017: audio-bar wording | Fixed | Track lengths as m:ss, license shown under the track, a "Saving…" note |
| A shared voice-picker id (found while fixing) | Fixed | Each picker has its own id; two can be on the page |

## Questions

| # | Question | Answer |
|---|---|---|
| 1 | Autosave delay | 0.8 s everywhere; the spec is corrected |
| 2 | PRD CA-2 safe zone in the preview | Captions are already drawn inside it by the shared renderer; an on-screen guide for Instagram's buttons is a later improvement |
| 3 | Pixabay link | "Videos from Pixabay" links to pixabay.com |
| 4 | Mood change with a fixed track | The fixed track stays; changing the mood only changes the automatic choice |
| 5 | Accessibility target | WCAG 2.1 AA, audited in F9 |
| 6 | Clip trims | The timeline and renderer support them; a trim control in the editor is a later improvement |

## Intended changes that the tests should follow

- The Render note is visible text ("Rendering to a video comes next"), not a `title`.
- The tabs are one Tab stop with arrow keys.
- Caption words autosave; an emptied word is refused by the API and its message shown under the words (no Save button).
- "Videos from Pixabay": "Pixabay" is a link.
- The music track list is the whole library (`GET /music`, no mood filter), in two groups.
- Changing the narrator in the audio bar asks for confirmation first.

## For F7 and B5 (from QA section 3)

Render enabled only when ready and not too long; the render service must reach storage by an internal address; the mock stock provider needs a tiny playable MP4 for offline renders; `transition` isn't drawn yet; music loop crossfade and loudness (AU-4, AU-7); and the backend's CI should run the same copy check as `sync.test.ts`.

## Round 3 (QA recheck and Tester round 2)

| Finding | Status | What was done |
|---|---|---|
| QF6-009 / BUG-067: the player's error message never appeared | Fixed | Media errors come from the video, audio and image elements, outside React's rendering. The shared `Reel` now takes an optional `onMediaError` (passed to every video, audio and image); only the editor sets it, to show "The preview couldn't load one of its files" and **Reload preview**. Without it (a render), Remotion still fails on a broken file. Renderer and web-app copies updated together |
| QF6-018 / BUG-066: a failed caption fix was lost on a tab switch; Back dropped unsaved text | Fixed | All three tab panels stay mounted (the others are `hidden`), so their saves keep retrying. Back to the script first saves everything; if that fails, the editor stays and shows why. Other in-app links (sidebar) stay with F9's `useBlocker` (QF5-021) |
| QF6-019: thumbnails downloaded again after every edit and poll | Fixed | The scene list keeps each thumbnail's first link for up to 90 minutes |
| BUG-065: "For a inspiring Reel" | Fixed | "an" before a vowel |
| Tester note: a failed slider save was dropped | Fixed | It is kept and sent again on the next Generate, leave or move |

Intended test changes: Back is a button (it saves first); a test stand-in player reports a broken file through `inputProps.onMediaError`, and the message and **Reload preview** appear; `errorFallback` now says "The preview couldn't be shown" for drawing errors.

## Round 4 (Tester round 3)

| Finding | Status | What was done |
|---|---|---|
| BUG-068: the player took keyboard focus when it appeared and when playback stopped | Fixed | `spaceKeyToPlayOrPause={false}` on the editor's player: Remotion's space-key shortcut is what moves focus to its Play button. The button itself still plays and pauses with Space or Enter when focused |
| Unit tests timing out on a loaded machine | Fixed | Vitest's `testTimeout` is 15 s |

QA's Recheck 2 passed. Still deferred to F9: the styled CA-4 dialog (QF6-014) and blocking other in-app links with unsaved text (QF5-021). For B5: keep `onMediaError` out of a render's props, and add a render test with a missing file that expects the render to fail.
