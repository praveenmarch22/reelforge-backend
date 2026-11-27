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
