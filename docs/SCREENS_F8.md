# ReelForge: screens for F8 (settings, credits, admin)

| | |
|---|---|
| Date | 2026-11-28 |
| Designs | Stitch screens 14 (Settings, credits tab), 15 (Out of credits) and 16 (Admin console), with the UI review's corrections: v1 is solo with no public API, there's no buying credits, and dates come from the API. Built from the design system the other screens use. Compare with Stitch when convenient |
| Checked against | [PRD](PRD.md) 5.1, 5.10, 5.11; [API design](API_DESIGN.md) A9–A12, H1–H2, I1–I14; [PUBLISHING_YOUTUBE.md](PUBLISHING_YOUTUBE.md) F1, F3 |
| Backend | The real backend (`npm run dev:real`); the mock API gains what the unit tests need |

## 1. Settings: `/app/settings?tab=…` (screen 14)

Tabs in the address (`profile`, `defaults`, `youtube`, `credits`, `security`), so the top bar's pills can open one directly. The connect results of F2 (`?connected=youtube` / `?connection_error=…`) show the same dialog as the publish page.

| Tab | What it does | API |
|---|---|---|
| Profile | Name; niche (up to three topics, the onboarding chips); email shown, not editable | A9, A10 |
| Defaults | Default narrator (with sample), caption style, mode for new Reels | A10, D3 |
| YouTube | Connected: channel picture and name, "Connected since", **Change channel** (F1) and **Disconnect** (F3, with a confirm). Not connected or needing a reconnect: **Connect YouTube** with the testing note. Instagram: "Coming soon" | F1, F3 |
| Credits | Balance against the monthly allowance (bar), the reset date, what each action costs (from H1), and the history (H2) with "Load more". No buying credits in v1 | H1, H2 |
| Security | Change password (current, new, confirm; other sessions are signed out) and Delete account (password, a confirm with the 7-day purge explained) | A11, A12 |

## 2. Out of credits (screen 15)

Any action answered with `402 insufficient_credits` opens one dialog. It says what was needed against the balance, when credits renew, and links to the Credits tab. The page that made the request also keeps its own inline message.

## 3. Admin console: `/admin` (screen 16)

Only for admins: the sidebar link and the route are hidden from everyone else, and the API refuses them anyway. Tabs:

| Tab | What it does | API |
|---|---|---|
| Overview | Users, Reels, failed jobs today, credits held | I14 |
| Users | Search; a row per user (email, name, balance, Reels, tester/admin/disabled badges). The detail panel has toggles for tester, admin and disabled (with a confirm), the monthly allowance, and **Adjust credits** (amount ± and a required note); also the recent jobs | I1–I4 |
| Jobs | Filters for state, type and provider; failed jobs show their error and **Retry** (free) where allowed | I5, I6 |
| Providers | Each provider's switch, the reason when off (required), and today's calls and error rate | I8, I9 |
| Usage | The last 30 days per day, provider and job type: calls, failures, credits | I7 |
| Music | The library with active switches; **Add track** (title, moods, licence, source, file and its length): uploads the file to the returned link, then switches it on; **Delete** (deactivates if in use) | I10–I13 |

Left out from Stitch: revenue charts, GPU or queue graphs.
