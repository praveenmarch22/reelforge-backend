# ReelForge: UI review of the Stitch screens

| | |
|---|---|
| Date | 2026-11-17 |
| Stitch project | https://stitch.google.com/projects/9599212476590829822 |
| Checked against | [PRD](PRD.md), [API design](API_DESIGN.md) |

**Verdict:** the visual direction is approved (palette, glass cards, typography, vertical Reel cards). The screens are good enough to build from. Most problems are invented details Stitch added; these are fixed while building and need no new designs. Four screens or states are missing and should be designed.

How closely each screen was checked: login, dashboard, new Reel, script editor and Reel editor were checked at full size. Onboarding, projects library, render progress, publish, settings, out-of-credits and admin were checked as thumbnails plus Stitch's own description of what it generated. Look at those yourself in Stitch before building them.

## 1. Fix while building (no new design needed)

| Screen | Issue | Fix |
|---|---|---|
| Everywhere | Invented tech labels: "PROMPT STUDIO // ENGINE V4.2", "Optimal GPU 7.4", "Model: ForgeVision v4.2 Pro", audio meter "−3.2 dBFS" | Remove |
| Login | Subtitle "Enter your studio credentials to access your pipeline" is jargon | "Log in to keep creating" or no subtitle |
| Dashboard | Allowance shows "86 of 150 credits", "PRO TIER", "Auto-renews" | 100 credits a month, no plans or tiers in v1 (credits come from `GET /credits`) |
| Dashboard | Render-time estimate, cost line and "ElevenLabs Hyper Real" chip under the prompt; reach and post-rate figures; "Trending Audio & Voice Models" strip | Remove. Keep prompt, Ideas, Create and the Faceless / Full AI / length chips |
| Dashboard and others | Sidebar has a **Music** item but there is no Music page in v1 | Sidebar: Home, Projects, Settings (Admin for admins only) |
| Script editor | Extra scores ("pacing score", "hook score") | Keep only estimated length vs target, word count and credit estimate |
| Reel editor | Two stacked header bars, with both "Export Reel" and "Render Reel" | One header: back, title, status, credits, Preview, Render |
| Reel editor | Mood chips in the inspector (Tense, Authoritative, Urgent, Whisper) don't match our moods | Use the 10 moods from the PRD: calm, joyful, excited, funny, inspiring, triumphant, serious, tense, mysterious, sad |
| Settings | Tabs "Profile & Team" and "API & Security" | v1 is solo with no public API: Profile, Defaults, Instagram, Credits, Security (password) |
| Settings | "+ Add Credits" button; dates show 2024 | No buying credits in v1; dates come from the API |
| Admin | Lists "Runway Gen-3 Backup" | Providers are exactly: Gemini, Groq, ElevenLabs, Edge TTS, Groq Whisper, Pexels, Cloudflare FLUX, Hugging Face video |
| All | Sample handle "@praveen.fit" | Real username from the API |

## 2. Missing screens and states to design

**Status (2026-11-17): all four designed in Stitch** (screens 17–22 in [STITCH_PROMPTS.md](STITCH_PROMPTS.md)). One more invented detail to drop: the publish success screen has a "telemetry" chip (bitrate, render engine).

| # | Screen / state | Why |
|---|---|---|
| 1 | Onboarding step 1 (niche) and step 3 (connect Instagram, with "Business or Creator account needed" note) | Only step 2 (voice) was designed |
| 2 | Dashboard empty state for a first-time user (no Reels yet) | First thing every new creator sees |
| 3 | Instagram connect result: success, and errors (personal account, permission denied, not a tester yet) | PRD flow 2 and edge cases IG-1, IG-11 |
| 4 | Publish success: "Your Reel is live" with link | End of the main flow (PRD flow 6) |

Nice to have, not blocking: a 404/error page, and phone-width layouts (the PRD asks the web app to be usable on a phone browser; this can be done in code from the desktop designs).

## 3. What is good and should be kept

- Colour palette, glass cards and typography: approved.
- Login: two-column layout with floating Reel cards and the "Idea in. Reel out." line.
- Dashboard: one prompt input as the main action; Reel cards with clear status pills, including Generating with progress and Failed.
- Reel editor: scene list with per-scene voice and visual status, failed scene with Retry, "Regenerate all missing", 9:16 player with word-by-word captions, inspector with the four visual sources, bottom audio bar with ducking and caption styles. Matches the PRD closely.
- Script editor: scene cards with mood and Rewrite, sticky summary with length, credits and "Generate Reel".
- Publish: caption with AI write, hashtag chips with the 30 limit, account pill, four-step publishing tracker.
- Out-of-credits modal: exactly the PRD's flow 7, including the "use stock instead" suggestion.
