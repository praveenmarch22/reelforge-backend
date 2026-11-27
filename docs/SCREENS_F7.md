# ReelForge: screens for F7 (render and publish)

| | |
|---|---|
| Date | 2026-11-27 |
| Designs | Stitch screens 12 (Render progress), 13 (Publish), 20 (success) and 21–22 (connect results), adapted for platform-neutral publishing with YouTube first (decision 2026-11-26: adapt in the spec, no new Stitch screens). **To check:** the Stitch canvas couldn't be opened overnight (the browser window was minimised), so this spec follows the UI review's notes on those screens and the design system the built screens already use. Compare with Stitch when the product owner is back |
| Checked against | [PRD](PRD.md) 5.7, 5.8; [PUBLISHING_YOUTUBE.md](PUBLISHING_YOUTUBE.md); [API design](API_DESIGN.md) E1–E3; [UI review](UI_REVIEW.md) |
| Backend | The real local backend (`npm run dev:real`); the mock API gets the same endpoints for unit tests |

All screens share the app shell. The top bar's Instagram pill becomes a **YouTube** pill: the channel name when connected, "Reconnect YouTube" when the connection needs it, else nothing.

## 1. Starting a render (Reel editor, F6 header)

- **Render** in the editor header is enabled when the Reel is `ready`, `rendered`, `render_failed`, `posted` or `post_failed`.
- It saves unsaved edits first, then asks to confirm: "Render this Reel? 2 credits, refunded if it fails".
- It then calls E1 with an Idempotency-Key and opens the render page.
- E1's refusals show next to the button:
  - `not_ready` names the scenes and selects the first one;
  - `too_long` and `too_short` give the length;
  - `already_running` opens the render page;
  - 402 shows the credit message.
- While `rendering`, the button reads **Rendering…** and opens the render page.

## 2. Render progress: `/app/p/:id/render` (screen 12)

**Layout:** the steps header (Idea, Script, Create, **Publish**), then two columns: the 9:16 video on the left, a status panel on the right.

| Part | What it does | API |
|---|---|---|
| While queued/running | The status panel shows the state ("Waiting for the renderer" / "Rendering"), a progress bar with the percentage, and the note "This can take a while on the free server; you can leave this page, the render continues". The left side shows a placeholder ("Your video is being made"). Polls E2 every 3 s | E2 |
| Cancel | Stops the render (G3 on the render's job), credits refunded | G3 |
| Done | The left side plays the MP4 (the render's `video.url`, poster `thumbnailUrl`). The panel shows "Your video is ready", its length and size, **Download** (`downloadUrl`), and **Publish** (opens the publish page) | E2 |
| Outdated | When `outdated`, a note "You edited the Reel after this render" with **Render again** | E1 |
| Failed | The reason (`error.message`), "Credits refunded", and **Try again** | E1 |
| History | Earlier renders (E3) as a small list: time, state, length, Download for finished ones | E3 |
| No render yet | Goes back to the editor with a note to press Render | |
| After a stopped or failed attempt | The newest **finished** video stays the one shown (with Publish and Download); the attempt's outcome shows under it with **Render again**. The publish page and the editor's Publish button use that video too | E3 |

Left out from Stitch, per the UI review: render-engine telemetry (bitrate, GPU, "engine" labels).

## 3. Publish: `/app/p/:id/publish` (screen 13)

**Layout:** the steps header with Publish active; the video on the left (the latest finished render); the post form on the right.

| Part | What it does | API |
|---|---|---|
| Platform | Two tiles: **YouTube Shorts** (selected) and **Instagram Reels** ("Coming soon", disabled) | |
| Account | YouTube connected: the channel picture and name, and "Change" (connects again). Not connected: **Connect YouTube**, which explains first that only test accounts can connect while ReelForge is in testing, then goes to Google (F1 with `returnTo` = this page). Needs reconnecting: a warning and **Reconnect YouTube** | A9 `connections`, F1 |
| Title | Required, up to 100 characters, with a counter; starts from the project's `postTitle`, else its title | F5 `title` |
| Description | Up to 2,200 characters, with a counter | F5 `caption` |
| Hashtags | Chips; type and press Enter or comma; × removes; up to 30; `#` is added in front | F5 `hashtags` |
| Write with AI | Writes the title, description and hashtags (free), replacing what's there after a confirm if anything was typed | F4, polling G1 |
| Credits | A read-only note: "Credits for the music and voice are added at the end of the description" | |
| Visibility | Public (default), Unlisted, Private. A note: "YouTube may keep it private until ReelForge is verified" | F5 `visibility` |
| Outdated render | If the render is older than the Reel: "You edited the Reel after this render", with **Render again** or **Publish this version** (`confirmOutdated`) | F5 |
| Publish now | F5 with an Idempotency-Key. `< >` in the title or description is refused before sending. The API's errors show at the button: `not_connected` and `reconnect_required` point to the account row, and `already_posted` links to the Short | F5 |
| Unsaved text | The title, description and hashtags are saved to the project as they are typed (autosave), so leaving and coming back keeps them | B5 `postTitle`, `postCaption`, `hashtags` |

**Publishing tracker** (replaces the form once F5 answers): four steps (Queued, Uploading, Processing, Published) with the post's progress. Polls F6 every 3 s. Failed: the reason and **Try again**, which continues the earlier upload (the backend does this).

## 4. "Your Short is live" (screen 20)

- **Content:**
  - the title "Your Short is live";
  - the video's title;
  - **Open on YouTube** (the post's `url`, new tab) and **Copy link**.
- **When YouTube kept it private** (`privateUntilVerified`): "YouTube kept it private until ReelForge is verified; you can change this in YouTube Studio".
- **Next steps:** **Create another Reel** and **Back to the dashboard**.
- **Coming back later:** a posted Reel opens this view from the publish page, with **Publish again** for a new render.
- **Left out:** the "telemetry" chip.

## 5. Connect results (screens 21–22)

Google sends the browser back with `?connected=youtube` or `?connection_error=youtube&reason=…`. Any page that gets these (publish, onboarding, settings) shows a dialog, then removes them from the address:

- `connected`: "YouTube connected", with the channel name.
- `denied`: "You didn't allow ReelForge to upload. Try again and allow both permissions."
- `missing_permission`: "ReelForge needs permission to upload videos. Try again and keep both boxes ticked."
- `no_channel`: "This Google account has no YouTube channel yet." With a link to create one (youtube.com).
- `failed` or anything else: "Connecting YouTube didn't work. Please try again."

## 6. Elsewhere

- **Dashboard and projects library:** the cards show the render's cover (`thumbnailUrl`) when there is one. The status pills already cover `rendering`, `rendered`, `posting`, `posted` and `post_failed`.
- **Onboarding step 3:** connects **YouTube** (F1) instead of the mock Instagram, and keeps "Skip".
- **Settings:** stays F8. Its YouTube row (connect / disconnect F3) comes with F8.
