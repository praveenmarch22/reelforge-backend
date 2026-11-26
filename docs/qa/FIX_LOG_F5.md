# ReelForge: developer response to QA findings, F5 (New Reel and Script editor)

| | |
|---|---|
| Date | 2026-11-26 |
| Inputs | [QA_REPORT_F5.md](QA_REPORT_F5.md) (QF5-001 to QF5-016), [TEST_REPORT_F5.md](TEST_REPORT_F5.md) (BUG-045 to BUG-052) |
| Written by | The developer. The QA and test reports are left as they were written. |

## Blocker and major

| Finding | Status | What was done |
|---|---|---|
| QF5-001 / BUG-045: text typed while a save was in flight came back older, then was lost | Fixed | Each scene's edits are sent one at a time, in order. The server's text replaces the box only when no newer edit is waiting (an edit counter, not a single "dirty" flag) |
| QF5-002: leaving within 0.8 s of typing dropped the edit; Generate and Rewrite could start before a save landed | Fixed | Unsaved text is sent when a card goes away; closing or reloading the tab warns while anything is unsaved; Generate, a scene rewrite and Rewrite whole script first wait for every unsaved edit (`autosave.ts`) |
| QF5-003 / BUG-050: a failed script showed no reason; a failed whole-script rewrite showed nothing | Fixed | The latest script job's reason (G2) is shown. A first script that failed offers **Try again** and **change the topic** (New Reel with the topic filled in). A failed rewrite this visit says why and that the scenes are unchanged |
| QF5-004: scene cards overflowed at 375 px | Fixed | The card header wraps, the on-screen text field and rewrite box shrink, and the main column can't grow past the screen (`minmax(0,1fr)`) |
| QF5-005 / BUG-047: field errors other than topic and script were swallowed | Fixed | Every field error is listed under the form with the field's name |
| BUG-046: ideas from the dashboard (`ideas=1`) stayed "Thinking…" in development | Fixed | The ideas live in the screen's own state, so React's double mount in development keeps them |

## Minor and polish

| Finding | Status | What was done |
|---|---|---|
| QF5-006 / BUG-049: no warning under 5 s | Answered | The spec now says under 2 s: a hook scene is usually 3–5 s, so a 5 s warning would fire on most scripts. The Reel-level 90 s limit is shown in the summary |
| QF5-007: no credits and Instagram pills | Fixed | A top bar with both pills on New Reel and the Script editor |
| QF5-008: out-of-credits messages differed; date hard-coded; "1 credits" | Fixed | One message everywhere (`errors.ts`), with the renewal date from the 402's `resetsAt` and correct plurals |
| QF5-009 / BUG-051: scene menu and the keyboard | Fixed | Escape closes it and focus returns to its button; focus starts on the first available item; moves, duplicates and deletes are announced |
| QF5-010: two `main` landmarks | Fixed | The storyboard is a `section` |
| QF5-011: job polling every 1 s, never stopping, giving up on one network error | Fixed | Every 2 s; stops when asked; up to 3 connection errors in a row are retried |
| QF5-012: ideas request over the endpoint's limits | Fixed | Niche cut to 120 and keywords to 200 characters |
| QF5-013: the title reset while being typed | Fixed | The server's title replaces it only while the field isn't focused |
| QF5-014: line breaks in the topic | Fixed | Turned into spaces as you type |
| QF5-015: breadcrumb differs from the spec | Answered | The spec now says "Projects / Script" |
| QF5-016: ideas loading and "Saved" not announced | Fixed | Both are live status messages |
| BUG-048: `?mode=banana` accepted | Fixed | Anything but `faceless` or `full_ai` is ignored |

## Questions

| # | Question | Answer |
|---|---|---|
| 1 / BUG-052 | Which actions send an `Idempotency-Key`? | Only endpoints the API design marks idempotent (B11, C7, C8). B3, B8 and C6 don't accept one; the busy button and the server's `409 already_running` stop double starts. The spec is corrected; the three tests that expect a key should follow it |
| 2 | "Shorten with AI" (PRD) | Added: shown when the script is over its target; it writes a new script asked to fit the target (B8 with an instruction) |
| 3 | A refused first script can't change its topic | **Change the topic** goes back to New Reel with the topic, mode and length filled in |

## Intended changes that the existing tests should follow

- Out-of-credits wording: "This needs 1 credit and you have 0. Your credits renew on 1 December." (date from `resetsAt`).
- The scene rewrite box is labelled "How should scene N change?".
- The scene menu focuses its first enabled item when it opens.
- The short-scene warning is under 2 s ("Under 2 seconds: it may flash by too fast to follow.").
- No `Idempotency-Key` on B3, B8 or C6.

## Round 3 (QA recheck and Tester round 2)

| Finding | Status | What was done |
|---|---|---|
| QF5-017 / BUG-053: a failed save was treated as saved | Fixed | A failed save keeps its text unsaved: the card shows the error (never "Saved"), the next blur, Generate or rewrite sends it again, and closing the tab still warns. `flushAll` rejects while anything is unsaved, so Generate and the rewrites stop with "Some changes aren’t saved yet…". A card that disappears stops counting once its last save ends |
| QF5-018 / BUG-054: a quickly failing whole-script rewrite said nothing | Fixed | The page waits on the job that B8 returned (G1) and shows its reason; it no longer depends on seeing the `scripting` status. Every refresh also reloads the jobs list |
| QF5-011: the stop signal was never passed | Fixed | Waits started by a click (ideas, hooks, scene rewrite, whole-script rewrite) stop when the page is left. The automatic ideas request from the dashboard isn't tied to leaving, because React's double mount in development would stop it |
| QF5-019: New Reel overflowed at 375 px | Fixed | Its grids have an explicit shrinkable column; measured: nothing past 375 px |
| QF5-020: Generate could be clicked twice while unsaved text was sent | Fixed | It is busy from the first moment of the click |
| BUG-055: a second press of the scene menu button didn't close it | Fixed | Focus moving to the menu's own button no longer closes it first |
| BUG-056: with one scene the menu didn't take focus | Fixed | Focus starts on the first usable item (Duplicate) |
| Question 4: PRD S-3, a whole script under 5 s | Fixed | The summary warns "Very short: under 5 seconds" |

Intended test change: the whole-script rewrite failure is now read from `GET /jobs/:id` for the job B8 returned, not from the jobs list.

## Recheck 2

| Finding | Status | What was done |
|---|---|---|
| QF5-021: text whose save failed is dropped without asking when leaving through an in-app link | Deferred to F9 | Blocking in-app navigation needs React Router's data router (`useBlocker`); the app uses `BrowserRouter`. Moving to the data router is part of F9 (hardening). Closing or reloading the tab already warns, and the scene shows its error |
| QF5-022: a refused save said "Check your connection" | Fixed | Generate and the rewrites stop with "Some changes aren’t saved yet: " followed by the scene's own reason: the API's message for a refused save, or "Can't reach ReelForge. Check your connection and try again." when the server can't be reached |
