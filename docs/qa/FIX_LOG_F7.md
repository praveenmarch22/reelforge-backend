# F7 render and publish screens: fix log

Fixes for [QA_REPORT_F7.md](QA_REPORT_F7.md).

## Decisions made overnight (2026-11-27, product owner asleep; to review)

- After a stopped or failed render attempt, the newest **finished** video stays the one shown, published and linked from the editor. Found against the real backend: the octopus Reel's latest attempt was a stopped test render.
- Onboarding step 3 connects **YouTube** (the Instagram step is removed; B6b rebuilds it on the platform-neutral endpoints).
- The top bar's Instagram pill is replaced by a YouTube channel pill, shown once a channel is connected.
- The render page shows a placeholder while rendering, not the live Remotion preview.
- The Stitch canvas couldn't be opened (the browser window was minimised), so the screens follow the spec and the design system. Compare with Stitch screens 12, 13 and 20–22.

## QA round 1

| Finding | Fix |
|---|---|
| QF7-001 Major: typing the post text marks the render outdated | Backend: a PATCH that only changes `postTitle`, `postCaption` or `hashtags` no longer counts as an edit of the Reel. Test added |
| QF7-002 Major: the editor hides Publish after a failed or stopped attempt | Publish shows whenever a finished video exists (the project's cover belongs to the newest finished render) |
| QF7-003: publish refusals are one line of server text | `not_connected` / `reconnect_required` point to the channel row and refresh it; `already_posted` links to the Short; `render_outdated` shows the confirm tick |
| QF7-004: autosave gaps | Each valid field is saved on its own; a field counts as saved only after the save succeeds; unsent text is sent when the page closes (including the trip to Google); a title over 100 characters isn't sent |
| QF7-005: a failed post on an older render says "continues where it stopped" | Only said for the same render; otherwise "This video is a newer render" and **Publish now** |
| QF7-006: onboarding keeps the result in the address; no channel link | The result is read once and removed from the address; `no_channel` links to creating a channel; reconnect wording added |
| QF7-007: accessibility | Render progress (in steps of 10 %) and its outcome are announced. The tracker announces its step. The tracker and the live view take focus. A disabled Render explains why in visible text |
| QF7-008: render-again buttons don't show the cost | They show "· 2 credits" and ask to confirm with the cost |
| QF7-009: E1 refusals in raw text | `not_ready` names the scene numbers (and selects the first); `too_long` and `too_short` give the length. The backend's `too_short` text no longer says "Instagram" |
| QF7-010: a failed Change shows nothing | The error shows under the channel row |
| QF7-011: polling the whole project | While a render runs, E2 is polled every 3 s for its progress |
| QF7-012: wording while a new render runs | The publish page says a new render is running and the current video can be published now |
| QF7-013: tiles and counters for screen readers | The platform tiles form a radio group; the counters read "n of 100 characters" |
| QF7-014: copy | "Publish again"; Copy link says when copying failed; the Render tooltip's reason fits the status; no failed-state flash before the history loads |
| QF7-015: Write with AI polls after leaving | It stops when the page closes |
| QF7-016: duplicated texts; pasted hashtags | One shared list of connection texts; pasting "a, b c" adds three hashtags |
| Q: keep InstagramStep | Removed with its test, as QA recommended; git history keeps it |

## Tester round 1

| Bug | Fix |
|---|---|
| BUG-080: Try again forgets the chosen visibility | The form starts from the failed post's visibility |
| BUG-081: `already_posted` doesn't link to the Short | After the refusal the project is read fresh, and the link points to that render's published Short |
| BUG-082: the editor shows `already_running` instead of opening the render page | The project is read again after the refusal; if it is rendering (started in another tab), the render page opens |
| BUG-083: a typed hashtag matching a chip is sent twice | The hashtag still in the box is added with the same duplicate check as a chip |
| BUG-084 (mock): the mock connect keeps an earlier result in `returnTo` | The mock removes `connected`, `connection_error`, `platform`, `reason` and `instagram`, as the backend does |
| BUG-085 (polish): the project and the render are both polled while rendering | Kept on purpose: the project poll notices the end and the status change, the render poll (E2, 3 s) keeps the progress current |

After the fixes: 728 frontend unit tests pass, and the type-check is clean.

## QA round 2

QA round 2 verdict: pass with issues (no Blocker or Major). Fixes:

| Finding | Fix |
|---|---|
| QF7-017: says a video can be published while a new render runs (F5 refuses it) | Publish is disabled while rendering; the note says publishing opens again when it's done |
| QF7-018: text typed just before Connect YouTube is lost | Going to Google first waits for the text to be saved. A reload or a closed tab can still lose the last 0.8 s of typing (accepted) |
| QF7-019: the outdated tick stays after a newer render arrives | Derived from the render shown, plus a refusal for that same render |
| QF7-020: "Failed" flashes before the history loads | The heading says "Checking your videos…" until it knows |
| QF7-021: a post failing for the connection leaves the row "connected" | The channel row is refreshed when a post fails with `reconnect_required` |
| QF7-022: `already_posted` may link to an older render's Short | The link is used only for a Short of the render shown |
| QF7-023: the live view is read twice | No status role; it takes focus instead |
| QF7-013 (rest): counters inside the labels | The counters are outside the labels and describe the fields |

Tests updated for the changed behaviour; 728 frontend unit tests pass, type-check and build clean.
