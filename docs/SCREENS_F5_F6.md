# ReelForge: screens for F5 and F6 (New Reel, Script editor, Reel editor)

| | |
|---|---|
| Date | 2026-11-25 |
| Designs | Stitch screens 9, 10 and 11 ([STITCH_PROMPTS.md](STITCH_PROMPTS.md)), read in the Stitch project on 2026-11-25 |
| Checked against | [PRD](PRD.md), [API design](API_DESIGN.md), [UI review](UI_REVIEW.md) |
| Backend | The real local backend (`npm run dev:real`), not the mock |

The layouts, colours and components follow Stitch. What Stitch invented, or what the backend doesn't do, is replaced by the real feature or left out, as listed under each screen. All three screens share the app shell (sidebar, top bar with the credit balance and Instagram status).

## 1. New Reel: `/app/new` (F5)

**Layout (Stitch):** a three-step header (1 Idea, 2 Script, 3 Create, with Idea active), the title "Craft your next Reel" with one line under it, a large prompt box, a row of idea cards, a settings panel, and a footer with the cost and the main button.

| Part | What it does | API |
|---|---|---|
| Prompt box | The topic (up to 300 characters), with a character count. A **Paste my own script** switch turns it into a script box (up to 5000 characters) | B3 `topic` or `script` |
| Get ideas | Asks for 5 ideas for the creator's niche (free) and shows them as cards below. "Use this" fills the prompt box | B1, with polling of G1 |
| Idea cards | The idea's title and hook line. Stitch's percentages, niche scores and "Trending #1" badge are left out | |
| Tone | Educational, Funny, Motivational, Storytelling, Dramatic | B3 `tone` |
| Target length | 15, 30, 60, 90 s | B3 `targetSeconds` |
| Visual style | Faceless or Full AI (Full AI shows "AI images with motion until AI video arrives") | B3 `mode` |
| Narrator | The account's default voice, with a preview button and a "Change" menu listing D3 voices | B3 `voiceId`, D3 |
| Footer | "1 credit" for the script and the balance; **Write script** creates the project and opens the Script editor, which shows writing progress | B3, H1 |

Left out from Stitch: "Algorithmic hook engine", "Viral engine active", "Optimal prompt length", "High viral probability", the sparkline, the "Estimated script generation ~4s" and "scene beat-sheet" lines.

Errors: 402 opens the out-of-credits message; 409 and 503 show the API's message next to the button; field errors show under their field.

## 2. Script editor: `/app/p/:id/script` (F5)

**Layout (Stitch):** a top bar with the breadcrumb (Projects / Script) and the credit and Instagram pills, the three steps with Script active, the title "Script storyboard" (the project's title, editable), a column of scene cards, and a sticky panel on the right.

| Part | What it does | API |
|---|---|---|
| While writing | The project is `scripting`: an indeterminate progress card "Writing your script…" until it is `draft`; a failed script shows the job's reason (G2), **Try again**, and **change the topic** (back to New Reel with the topic filled in). A failed rewrite of the whole script says why and that the scenes are unchanged | B4 polling, B8, G2 |
| Title | Editable inline | B5 `title` |
| Scene card | Number and on-screen label ("Scene 01"), the estimated time range, the narration (editable text area, autosaved), mood (a menu of the 10 moods), on-screen text (editable chip), word count and estimated seconds, a warning when the scene is over 15 s or under 2 s (hook scenes are often 3–5 s, so 5 s would warn on most scripts) | C2 |
| Scene menu | Rewrite (with an instructions box, 1 credit), Duplicate, Delete | C6, C4, C3 |
| Reorder | Drag by the handle, or move up and down from the menu (keyboard) | C5 |
| Add scene | At the end ("Add scene", with "n of 30 scenes") | C1 |
| Right panel | Estimated length against the target (bar), word count, narrator, the credit estimate for generating, **Generate Reel**, and **Rewrite whole script**. Over the target, **Shorten with AI** (PRD S7) writes a new script asked to fit the target | B10, B11, B8 |
| Hooks | "Try another opening" on the first scene: 3 suggested first lines, one click puts it in scene 1 | B9, C2 |

Left out from Stitch: the phone preview in the right panel (nothing is generated yet; the Reel editor has the real preview), "Version history", "Auto-optimize script", "Save draft as template", the pacing score and "Neural voice" labels. Generate opens the Reel editor, which shows the progress.

## 3. Reel editor: `/app/p/:id/edit` (F6)

**Layout (Stitch):** one header, a scene list on the left, the 9:16 player in the middle, an inspector on the right, and an audio bar at the bottom.

| Part | What it does | API |
|---|---|---|
| Header | Back, title, status pill, credits, **Generate missing** (when anything is missing, outdated or failed), and **Render** (disabled until F7/B5, with a note) | B4, B11 |
| Scene list | A thumbnail, number and mood, the first words, voice and visual status dots, a progress ring while generating, a red card with **Retry** on failure. Click selects the scene; drag reorders | B4, C5 |
| Player | The Remotion Player playing the timeline: scenes, captions word by word, music. Placeholders for scenes not made yet; play, scrub, and the current time against the total | B12 |
| Inspector, Visual tab | Source (Stock, AI image, AI video, Upload), visual prompt and stock query (editable), **Regenerate visual** with its cost, **Search stock** (results grid with "from Pixabay" and the author; click to use) | C2, C8, C9, C10 |
| Inspector, Voice tab | Narration (editable), mood, voice (this scene, from D3), **Regenerate voice** with its cost, the CA-4 warning when caption words were fixed | C2, C7, D3 |
| Inspector, Text tab | On-screen text, and the caption words (fix a word's spelling, times kept) | C2 `onScreenText`, `words` |
| Audio bar | Narrator (project voice), music track (D4 list by mood, preview), voice and music volume, ducking on or off, caption style (Bold pop, Clean, Neon), captions on or off | B5, D4 |
| Autosave | Every edit saves at once (or after 1 s of typing); a small "Saved" or "Saving…" note in the header | |

Left out from Stitch: the second header bar, "Export Reel" (Render is the one action), "Aspect 9:16 Ultra HD", "Pacing 142 WPM", "Model: ForgeVision", CFG, seed and "Motion: warp", the "−3.2 dBFS" meter and master limiter, Stitch's moods (Tense, Authoritative, Urgent, Whisper; the 10 PRD moods are used), and **Alternative takes** (the backend keeps one visual per scene; "Search stock" and "Regenerate" are the ways to change it). Upload is shown but disabled until uploads arrive (milestone B7).

Polling: while the project is `generating` or a scene's job is running, the editor refetches the project every 2 seconds (API design 1) and the timeline when a job finishes.

## 4. Shared rules

- Costs are read from `GET /credits`, never hard-coded.
- A 402 anywhere opens the out-of-credits message (the full modal is F8).
- Endpoints the API design marks **idempotent** (B11 Generate, C7, C8) get an `Idempotency-Key` per click. Others (B3, B8, C6) don't accept one; a second click is prevented by the busy button and the server's `409 already_running`.
- Edits save as you type (after 0.8 s) and when a field is left, one save at a time per scene; what you type is never replaced by an older answer. Leaving the page sends unsaved text, closing the tab warns, and Generate or a rewrite waits for unsaved text first.
- The player code is a copy of `backend/renderer/src` (Reel, Scene, Captions, types, util) in `frontend/src/remotion/`; a test fails if the copies differ.
