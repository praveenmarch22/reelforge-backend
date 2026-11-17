# Stitch prompts: ReelForge

Paste each prompt into Stitch (web app, desktop). Generate the login page first, then the dashboard in the same project so the style carries over.

## Shared style (included in both prompts)

Dark, futuristic, minimal. Near-black background (#07070B) with a subtle deep-violet glow; glassmorphism cards (semi-transparent, blurred, 1px light border); accent gradient electric violet (#7C5CFF) to cyan (#22D3EE); one font family (Inter or Space Grotesk for headings); generous spacing; rounded corners (16–20px); thin line icons; very little text.

## 1. Login page

```
Design a login page for "ReelForge", an AI studio that turns an idea into a ready-to-post Instagram Reel.

Style: dark, futuristic and minimal. Near-black background (#07070B) with a soft deep-violet glow, glassmorphism card (semi-transparent, blurred, thin light border), accent gradient from electric violet #7C5CFF to cyan #22D3EE, Space Grotesk headings and Inter body text, rounded corners, lots of empty space, very little text.

Layout, desktop, two columns:
- Left (55%): a large visual. Three vertical 9:16 phone-shaped video cards floating at slight angles, each showing an abstract cinematic frame with a bold animated-looking caption ("WAIT FOR IT", "3 HABITS", "DID YOU KNOW?"). Behind them a soft gradient orb. Small logo "ReelForge" top left. One short line under the cards: "Idea in. Reel out."
- Right (45%): a centered glass card with:
  - Title "Welcome back"
  - Email field, password field with show/hide eye icon
  - "Forgot password?" link aligned right
  - Full-width gradient button "Log in"
  - Small text "New here? Create an account"
No social login buttons. No long paragraphs, no feature lists.
```

## 2. Dashboard page

```
Design the main dashboard for "ReelForge", in the same style as the login page: dark, futuristic, minimal, near-black background with a subtle violet glow, glassmorphism cards, violet #7C5CFF to cyan #22D3EE gradient accents, Space Grotesk headings, Inter body.

Layout, desktop:
- Slim left sidebar with icons only and tooltips: Home, Projects, Music, Settings; logo mark at top; user avatar at bottom.
- Top bar: greeting "Hey Praveen" on the left; on the right a credits pill "86 credits" with a small circular progress ring, an Instagram status pill "@praveen.fit connected" with a green dot, and a bell icon.
- Hero: one large glass panel with a single prompt input "What's your next Reel about?" with a glowing gradient border, a small "✨ Ideas" button inside the input on the left, and a gradient "Create" button on the right. Under it, three small chips: "Faceless", "Full AI", "30s".
- Section "Recent Reels": a horizontal row of 9:16 vertical cards (5 visible). Each card shows a thumbnail frame, title (one line), length ("0:31") and a status pill: Posted (green), Ready (cyan), Generating (violet with a thin progress bar), Draft (grey), Failed (red). Last card is a dashed "+ New Reel" card.
- Small row of three compact stat tiles: "Reels made 12", "Posted 8", "Credits reset in 19 days".
Keep text minimal, no tables, no long descriptions. Plenty of breathing room.
```

## Generated screens (Stitch project "ReelForge", 2026-11-11)

Stitch project: https://stitch.google.com/projects/9599212476590829822 (design system: "Synthetic Cinema").

| # | Screen | Route | Main APIs |
|---|---|---|---|
| 1 | Login | `/login` | A4 |
| 2 | Sign up | `/signup` | A1 |
| 3 | Check your email | `/verify` | A2, A3 |
| 4 | Forgot password | `/forgot` | A7 |
| 5 | Set new password | `/reset` | A8 |
| 6 | Onboarding (niche, voice, Instagram) | `/onboarding` | A10, D3, F1 |
| 7 | Dashboard | `/app` | A9, B2, H1 |
| 8 | Projects library | `/app/projects` | B2 |
| 9 | New Reel (idea step) | `/app/new` | B1, B3 |
| 10 | Script editor | `/app/p/:id/script` | B4, B5, C1–C6, B10, B11 |
| 11 | Reel editor | `/app/p/:id/edit` | B4, B12, C2–C10, D1–D4 |
| 12 | Render progress | `/app/p/:id/render` | E1, E2 |
| 13 | Publish to Instagram | `/app/p/:id/publish` | F4, F5, F6 |
| 14 | Settings (credits tab shown) | `/app/settings` | A10, A11, F1, F3, H1, H2 |
| 15 | Out of credits modal | (modal) | 402 `insufficient_credits` |
| 16 | Admin console | `/admin` | I1–I13 |
| 17 | Onboarding step 1: niche | `/onboarding` | A10 |
| 18 | Onboarding step 3: connect Instagram | `/onboarding` | F1 |
| 19 | Dashboard, empty state (new user) | `/app` | A9, B2 |
| 20 | Publish success: "Your Reel is live" | `/app/p/:id/publish` | F6 |
| 21 | Instagram connected (dialog) | `/app/settings?instagram=connected` | F2 redirect |
| 22 | Instagram connection error (dialog) | `/app/settings?instagram=error&reason=…` | F2 redirect |

Known differences to tidy during development: Stitch added decorative labels on some screens (for example "PROMPT STUDIO // ENGINE V4.2", GPU and reach figures, a "Trending Audio" strip on the dashboard). Drop them when building; keep layout, colours and components.

## What to check in the results

- Does it feel premium and futuristic without clutter?
- Can you tell at a glance: how to start a Reel, credits left, Instagram status, state of recent Reels?
- Are the vertical 9:16 cards prominent (the product is about Reels)?
