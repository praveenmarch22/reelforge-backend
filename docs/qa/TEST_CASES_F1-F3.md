# ReelForge: test cases for F1–F3

| | |
|---|---|
| Last run | 2026-11-18, Chromium, dev server with the mock API |
| Report | [QA_REPORT_F1-F3.md](QA_REPORT_F1-F3.md) |
| Plan | [TEST_PLAN.md](TEST_PLAN.md) |

**How to read this**

- "Demo" means the demo account in `frontend/src/mocks/db.ts`. "New user" means an account made through sign up in the same browser.
- Each case starts in a fresh browser context (empty storage) at 1280×800 unless it says otherwise.
- "Forced" means the API answer is simulated by the fetch wrapper described in the test plan.
- Result: **Pass**, **Fail** (with the finding id), **Read** (checked by reading code only), **N/T** (not testable in this environment).
- Cases marked ⚙ are good candidates for the automated suite.

## F1: setup, API client, mock API

| ID | Requirement | Steps | Expected | Result |
|---|---|---|---|---|
| TC-F1-001 | Milestone F1 | Run `npm run typecheck` in `frontend/` | Exits 0 | Pass |
| TC-F1-002 | Milestone F1 | Run a production build | Succeeds; status document says 342 kB JS, 105 kB gzipped | Pass (342.58 kB / 104.78 kB; built into the QA scratch folder) |
| TC-F1-003 | Milestone F1 | Open http://localhost:5173 signed out | App loads, lands on `/login`, no console errors other than the expected 401 from `GET /me` | Pass |
| TC-F1-004 ⚙ | API conventions (Auth) | Set a cookie `rf_csrf=abc%2B123`, log in, record request headers | Every POST/PATCH/DELETE sends `X-CSRF-Token: abc+123`; GETs send none; requests use `credentials: include` | Pass |
| TC-F1-005 ⚙ | API conventions, error `token_expired` | Demo on `/app/projects`. Force one `401 token_expired` on `GET /projects`, click the Posted filter | Client calls `POST /auth/refresh`, repeats the request, list shows, no error | Pass |
| TC-F1-006 ⚙ | API conventions, `token_expired` | Force one `401 token_expired` on `DELETE /projects/:id`, delete a Reel | Refresh, retry, Reel deleted | Pass |
| TC-F1-007 ⚙ | API conventions, `token_expired`; A3 | New unverified user on `/verify`. Force one `401 token_expired` on `POST /auth/resend-verification`, click Resend email | Refresh, retry, "Sent" | **Fail QA-005** |
| TC-F1-008 ⚙ | API conventions, `not_authenticated`; PRD A-4 | Demo on `/app/projects`. Force `401 token_expired` on `GET /projects` and `401` on `/auth/refresh`, click a filter | User is taken to `/login` and can return to the page | **Fail QA-003** |
| TC-F1-009 | API error format | Force `500` with a JSON error body on `POST /auth/login` | The message from `error.message` is shown; the button is usable again | Pass |
| TC-F1-010 | API error format | Drop the connection on `POST /auth/login` | "Can't reach ReelForge…" shown; button usable again | Pass |
| TC-F1-011 | API shared objects | Compare `src/api/types.ts` with User and ProjectSummary in the API design, and enums with the database design | Same fields, names and types | Read: Pass (see questions 5 and 8 for values the API design does not pin down) |
| TC-F1-012 | API A1–A10, B2, B6, H1 | Compare each mock handler's status codes, error codes and bodies with the API design | Match | Read: **Fail QA-013** (differences listed there) |
| TC-F1-013 | UI review section 1; Stitch shared style | Search the source for the invented labels (ENGINE V4.2, GPU, PRO TIER, Auto-renews, Trending Audio, Music, the jargon login subtitle) | None present | Pass |
| TC-F1-014 | Stitch shared style | Check theme tokens: background #07070B, violet #7C5CFF to cyan #22D3EE, Space Grotesk headings, Inter body, glass cards | Present in `tailwind.config.js` / `index.css` | Read: Pass |
| TC-F1-015 | README | Compare README claims with behaviour | Accurate | **Fail QA-021** |

## F2: sign-in screens

| ID | Requirement | Steps | Expected | Result |
|---|---|---|---|---|
| TC-F2-001 ⚙ | PRD A1, flow 1 steps 2–3; API A1 | Sign up with name, email `  New.User@Example.COM ` and a valid password | `/verify` "Check your inbox" naming `new.user@example.com`; Resend button | Pass |
| TC-F2-002 ⚙ | PRD A1; API A2 | From TC-F2-001 open the verification link | Lands on `/app`, no verify banner, 100 credits | Pass |
| TC-F2-003 ⚙ | PRD A-2 | Open the same verification link again | Clear message and a way to resend | Pass with note QA-015 |
| TC-F2-004 | PRD A-2 | Sign out, open the used link | Clear message; path to a new link | Pass (needs log in first; see question 2) |
| TC-F2-005 | PRD A-2; API A3 | On `/verify` (unverified) click Resend email | "Sent" notice; a new link | Pass |
| TC-F2-006 | PRD A-2; API A3 (3 per hour) | Force `429 rate_limited` on resend, on `/verify` | Error message shown | Pass |
| TC-F2-007 ⚙ | PRD A-2; API A3 | Unverified user on `/app`. Force `429` on resend, click Resend email in the banner | Error message shown | **Fail QA-004** |
| TC-F2-008 | PRD A-2 | Unverified user on `/verify`; make `GET /me` slow (2 s) and reload; then force `500` on `GET /me` and reload | Loading state, then the same screen; on failure an error with retry | **Fail QA-009** |
| TC-F2-009 ⚙ | PRD A-1; API A1 `409 email_taken` | Sign up with ` DEMO@ReelForge.test ` | "Account exists" and an offer of log in or password reset | **Fail QA-007** (message shown, no reset offer) |
| TC-F2-010 ⚙ | API A1 validation | Submit sign up empty; then name of spaces, email `a@b`, password `password`; then 81-character name; then 129-character password | Field messages; focus on first bad field; server's "too common" shown on the password field | Pass |
| TC-F2-011 ⚙ | PRD A2; API A4 | Log in as Demo | `/app` | Pass |
| TC-F2-012 ⚙ | API A4 `invalid_credentials` | Wrong password; unknown email | Same message for both | Pass |
| TC-F2-013 | PRD A-3; API A4 `429`, `403 account_disabled` | Force each on login | Server message shown, form usable | Pass (lockout itself is server side: N/T) |
| TC-F2-014 | PRD A2 | Log in with different letter case and trailing space in the email | Logs in | Pass |
| TC-F2-015 ⚙ | PRD A2; API A6 | Account menu → Log out | `/login`; `/app` now redirects to `/login` | Pass on pages where the menu is reachable; **Fail QA-002** on Home and Projects at 1280×800 |
| TC-F2-016 | PRD A2; API A6 | Drop the connection on `POST /auth/logout`, log out, reload | User is told, or is really signed out | **Fail QA-006** |
| TC-F2-017 ⚙ | PRD A2, A-1; API A7 | Forgot password with an unknown email, then a known one | Same "If an account exists…" text for both | Pass |
| TC-F2-018 ⚙ | PRD A2; API A8 | Open reset link; mismatched passwords; `password`; then a valid pair | "Passwords don't match."; "That password is too common."; then `/login` with "Password updated…" | Pass |
| TC-F2-019 ⚙ | API A8 `410 token_invalid` | Use the same reset link again | "expired or already used" with "Get a new link" | Pass |
| TC-F2-020 | API A8 | Old password, then new password, after a reset | Old refused, new accepted | Pass |
| TC-F2-021 | — | Open `/reset` with no token | "The reset link is missing or incomplete" and "Get a new link" | Pass |
| TC-F2-022 ⚙ | Milestone F2 route guards | Signed out, open `/app`, `/app/projects`, `/app/settings` | Redirect to `/login` | Pass |
| TC-F2-023 ⚙ | PRD A-4; guard's stated behaviour | Signed out, open `/app/projects?x=1`, log in on the page you were sent to | Back on `/app/projects?x=1` | **Fail QA-001** |
| TC-F2-024 | Milestone F2 route guards | Signed in, open `/login`, `/signup`, `/forgot` | Redirect into the app | Pass (see questions 9 and 10 for `/reset` and unverified users) |
| TC-F2-025 | Milestone F2 | Force `500` / dropped connection on `GET /me`, load `/app` and `/login` | "We couldn't load your account" with Try again; Try again works | Pass |
| TC-F2-026 | — | Browser back after verifying; back after resetting the password; forward again | No return to a used token URL; no broken screen | Pass |
| TC-F2-027 | — | Reload on `/login`, `/signup`, `/verify`, `/forgot` (after sending), `/reset?token=…` | Sensible state each time | Pass, except `/verify` (QA-009) |
| TC-F2-028 ⚙ | — | Double-click Log in; double-click Create account; press Enter twice on sign up | One request each | Pass |
| TC-F2-029 | PRD A-2 | Open a verification link in a second, fresh browser context | Verified and signed in | N/T in mock mode (mock database is per browser); see question 1 |
| TC-F2-030 | PRD A2 | Two tabs signed in. Log out in tab 1. In tab 2 wait, click a filter, go Home | Tab 2 goes to `/login` | **Fail QA-003** |
| TC-F2-031 | Stitch prompt 1; UI review section 1 and 3 | Compare login copy and elements with the prompt | "Welcome back", "Log in to keep creating.", email, password with show/hide, "Forgot password?" on the right, gradient "Log in", "New here? Create an account", three Reel cards (WAIT FOR IT, 3 HABITS, DID YOU KNOW?), logo, "Idea in. Reel out.", no social buttons | Pass (text comparison) |
| TC-F2-032 | PRD section 12 | Each sign-in screen at 390×844 and 768×1024 | Usable, no horizontal scroll | Pass |
| TC-F2-033 | Milestone F9 (early look) | Keyboard only: tab through login, submit with Enter, show/hide password | Logical order, visible focus, labels | Pass |

## F3: app shell, dashboard, projects library

| ID | Requirement | Steps | Expected | Result |
|---|---|---|---|---|
| TC-F3-001 ⚙ | UI review section 1 (sidebar) | Demo: read the sidebar items. New user: same | Home, Projects, Settings; Admin only for admins; no Music | Pass |
| TC-F3-002 | Stitch prompt 2 | Hover each sidebar icon | Tooltip with the label, readable | Fail, folded into QA-002 (tooltip partly hidden behind page content) |
| TC-F3-003 | PRD A2 | Open the account menu on Home and on Projects at 1280×800, 1440×900, 1920×1080, 768×1024, 390×844 | Menu fully visible and clickable | **Fail QA-002** |
| TC-F3-004 ⚙ | PRD D5, 5.10; UI review section 1 | Demo: read the credits pill and the "Credits reset in" tile | 86 credits; 86 of 100; days from `resetsAt`; no tiers or plans | Pass (see question 7 on the data source) |
| TC-F3-005 | PRD 5.10 | Set a new user's reset date to 20 hours from now | "1 day" | **Fail QA-016** ("1 days") |
| TC-F3-006 | UI review section 1 ("real username from the API") | Demo: Instagram pill. New user: pill. User with status `expired` | `@username` with green dot; "Connect Instagram"; "Reconnect Instagram" | Pass |
| TC-F3-007 ⚙ | PRD flow 1 step 5; UI review section 2 item 2 | New verified user opens `/app` | Welcome, empty state, a "Create your first Reel" button | Pass with note QA-017 (starter ideas and prompt, no such button) |
| TC-F3-008 | Unverified user | New unverified user opens `/app` | Banner "Verify your email…" with Resend and Open | Pass |
| TC-F3-009 ⚙ | PRD D1; Stitch prompt 2 | Demo dashboard | Prompt with Ideas and Create; mode and length chips; three stat tiles; Recent Reels with 5 cards and a "New Reel" card; each card has thumbnail, title, mode, length, status pill | Pass with note QA-020 (no length on cards without a rendered duration) |
| TC-F3-010 ⚙ | PRD flow 8 step 3; API B2 polling | Demo dashboard, watch the Generating card for 7 s and count `GET /projects` calls | Progress rises; a call about every 2 s; polling stops when nothing is in progress | Pass |
| TC-F3-011 | PRD flow 8 step 3 | While data is on screen, drop two `GET /projects` calls | Data stays on screen (perhaps with a small warning) | **Fail QA-011** |
| TC-F3-012 | — | Force `500` on `GET /projects`; open dashboard and library; press Try again after removing the failure | Error with Try again; recovers | Pass |
| TC-F3-013 | Stitch prompt 2 | Type a topic, pick Full AI and 90s, press Enter; press Create with nothing typed; press Ideas | Goes to `/app/new` with the choices in the URL (placeholder screen until F5) | Pass |
| TC-F3-014 ⚙ | PRD flow 8 step 2 | Read where each card links, for all 11 statuses | scripting/draft → script; generating/needs_attention/ready/rendering/render_failed → editor; rendered/posting/post_failed/posted → result screen | Pass (statuses missing from the seed were injected into the mock database) |
| TC-F3-015 | Milestone F3 "done when" | Demo: dashboard and library show mock projects in every status | All 11 statuses present | **Fail QA-010** (6 of 11 in the seed) |
| TC-F3-016 ⚙ | PRD D2; API B2 `status` | Library: click each filter | All 7; Drafts 1; In progress 1; Ready 2; Posted 2; Failed 1 | Pass (see question 6 on `scripting`) |
| TC-F3-017 ⚙ | PRD D2; API B2 `q` | Search `TOKYO`; spaces only; `%&#?=<b>`; `zzzz`; then Clear filters; then filter + search together | 1; all; none with "No Reels match"; none; all back and box emptied; 1 | Pass |
| TC-F3-018 | — | Library for a user with no Reels | "No Reels yet" and a New Reel button | Pass |
| TC-F3-019 ⚙ | PRD D4; API B6 | Delete a Reel: open the card menu, Delete, confirm | Confirmation names the Reel; card disappears; dashboard counts drop; still gone after reload | Pass |
| TC-F3-020 | PRD D4 | In the dialog: Escape; click outside; Cancel | Closes without deleting | Pass |
| TC-F3-021 ⚙ | PRD D4 | Force `500` on delete for Reel A, Cancel, open delete for Reel B | A's error shown for A only; B's dialog is clean | **Fail QA-008** |
| TC-F3-022 | PRD D4; API B6 `404` | Two tabs, same Reel's dialog open in both; delete in tab 1, then tab 2 | Tab 2 closes the dialog; the Reel is gone | Fail (Polish) QA-014 |
| TC-F3-023 | PRD D4 | Click Delete twice 40, 120 and 250 ms apart | One request | Pass |
| TC-F3-024 | API conventions (pagination) | Account with more than 100 Reels | All reachable | Read: **Fail QA-012** |
| TC-F3-025 | — | Reel with a 200-character title without spaces; title with HTML characters; user with an 80-character name | No overflow; text is escaped | Escaping: Pass. Overflow: Fail (Polish) QA-019 |
| TC-F3-026 | Stitch screen list | Open `/app/new`, `/app/p/x/script`, `/app/p/x/edit`, `/app/p/x/render`, `/app/p/x/publish`, `/app/settings`, `/admin`, `/onboarding`, `/nope` | Placeholder with the milestone number for designed screens; 404 for unknown | Pass, except `/app/p/x/render` (Polish QA-018) |
| TC-F3-027 | PRD section 12 (phone width); Milestone F9 | Dashboard and library at 390×844, mouse and touch | Prompt usable; card actions reachable; status visible | **Fail QA-022, QA-023, QA-024** (deferred to F9) |
| TC-F3-028 | PRD section 12 | Dashboard and library at 768×1024 | Usable, no horizontal scroll | Pass, except the account menu on Home (QA-002) |
| TC-F3-029 | Milestone F9 (early look) | Keyboard only: reach every control; delete a Reel; open and close the account menu; log out | All possible; focus visible and sensibly placed | Possible: Pass. Focus handling and duplicate tab stops: QA-025 (deferred to F9) |
| TC-F3-030 | Milestone F9 (early look) | Browser set to reduced motion | Floating cards, pulses and hover lifts stop | Pass |
