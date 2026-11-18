# ReelForge: QA report for F1–F3

| | |
|---|---|
| Date | 2026-11-18 |
| Milestones | F1 setup and design system, F2 sign-in screens, F3 app shell, dashboard and projects library |
| Build tested | `frontend/` working tree on 2026-11-18, dev server at http://localhost:5173, mock API on |
| Browser | Playwright Chromium only |
| Test cases | [TEST_CASES_F1-F3.md](TEST_CASES_F1-F3.md) |
| Plan | [TEST_PLAN.md](TEST_PLAN.md) |

## Verdict: pass with issues

The main flows work: a new user can sign up, verify, log out, log in and reset a password, and the dashboard and library list, filter, search and delete Reels against the mock API. There is no Blocker. Three Major findings should be fixed before F4 builds on them, because all three are in the shared shell and session handling that every later screen uses.

| Severity | Count | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 3 | QA-001 to QA-003 |
| Minor | 10 | QA-004 to QA-013 |
| Polish | 8 | QA-014 to QA-021 |
| Deferred to F9, not counted in the verdict | 4 | QA-022 to QA-025 |
| Questions | 12 | Section 5 |

**"Verified" column**: *Run* means reproduced in the browser. *Read* means concluded from the code only. File and line references are where I believe the cause is; I did not change any code to confirm them.

**Limits of this review**

- I cannot see the Stitch images. Design comparison is against the text of the prompts and the UI review only.
- Chromium only. Edge, Safari and Firefox (PRD section 12) were not tested.
- API failures were injected by wrapping `fetch` in the page, because Playwright cannot intercept requests the mock service worker answers. Real cookies, real CSRF rejection, the real 15-minute expiry, rate limits and lockout do not exist in the mock and were not tested.
- The developer's unit and end-to-end tests were being written during this review. I did not run them.

---

## 1. Findings

### Major

#### QA-001: After being sent to log in, the user lands on Home, not the page they asked for

| | |
|---|---|
| Requirement | PRD A-4 ("after re-login, return to the same project"); the guard's own stated behaviour in `guards.tsx` ("sent to the login page and brought back afterwards") |
| Verified | Run |
| Believed cause | `src/features/auth/guards.tsx:32` and `src/features/auth/LoginPage.tsx:31`, triggered by `src/api/hooks.ts:28` |

Steps:
1. Fresh browser, signed out. Open `http://localhost:5173/app/projects?x=1`.
2. You are redirected to `/login`. Without leaving the page, log in with the demo account.

Expected: `/app/projects?x=1`.
Actual: `/app`.

The login mutation stores the user in the cache, `GuestOnly` re-renders and redirects to `/app`, and that wins over the login page's own `navigate(state.from)`. Today this costs one click. Once the editor exists it breaks A-4 directly: a creator whose session ends mid-edit comes back to the dashboard instead of their project. The same race decides where an unverified user lands after login (`/verify`, from `GuestOnly`, not `/app` from the login page).

#### QA-002: The account menu opens behind the page content, so Log out cannot be clicked

| | |
|---|---|
| Requirement | PRD A2 (log out); Stitch prompt 2 (sidebar tooltips) |
| Verified | Run (element hit-test and screenshots) |
| Believed cause | `src/components/layout/AppShell.tsx:184` (the sticky sidebar forms its own stacking layer with no z-index, so the content column at line 196 paints over anything that sticks out of it: the menu at line 66 and the tooltips at line 26) |

Steps:
1. Log in as the demo account at 1280×800.
2. On Home or on Projects, click the avatar at the bottom of the sidebar.

Expected: a menu with name, email and Log out, on top of the page.
Actual: the menu is drawn underneath the Reel cards. Only its left edge is visible and a click lands on the card behind it.

| Viewport | Home | Projects |
|---|---|---|
| 1280×800 | covered | covered |
| 1440×900 | clickable | clickable |
| 1920×1080 | clickable | clickable |
| 768×1024 | covered | clickable |
| 390×844 | covered | covered |

Whether it is covered depends only on whether a card happens to sit at the bottom-left of the window. Workaround: go to Settings (no cards there) and log out from there. This is the only log-out control in the app. The sidebar tooltips are clipped the same way ("Projects" shows as "Projec" where a card overlaps it). Screenshots: `menu-1280-_app_projects.png`, `tooltip-projects.png`.

#### QA-003: When the session ends, the app stays on screen in a broken state and never returns to log in

| | |
|---|---|
| Requirement | PRD A-4; API design conventions (`401 not_authenticated` = no session); PRD A2 |
| Verified | Run, two ways |
| Believed cause | `src/api/client.ts:56-61` and `src/api/hooks.ts:8-22`: a 401 on any request other than `/me` is treated as an ordinary error; nothing clears the cached user or redirects |

Steps (two tabs):
1. Log in as demo. Open `/app/projects` in tab 1 and tab 2.
2. In tab 1, go to Settings and log out.
3. Look at tab 2. Click a filter. Click Home.

Steps (expired session):
1. Log in as demo, open `/app/projects`.
2. Force `401 token_expired` on `GET /projects` and `401 not_authenticated` on `POST /auth/refresh`. Click a filter.

Expected: tab 2 goes to `/login` (and, with QA-001 fixed, comes back to the same page after logging in).
Actual: the full signed-in shell stays, with the demo user's name, credits and Instagram handle, and "We couldn't load your Reels. Try again". Try again repeats the failure. Placeholder pages look fully signed in. Only a manual reload reaches `/login`. The refresh itself is attempted correctly; it is the failed refresh that leads nowhere.

### Minor

#### QA-004: "Resend email" in the verify banner fails silently

| | |
|---|---|
| Requirement | PRD A-2; API A3 (limited to 3 per hour, so `429` is an expected answer) |
| Verified | Run |
| Believed cause | `src/components/layout/AppShell.tsx:161-176` (only `isSuccess` is handled) |

Steps: sign up, do not verify, open `/app`. Force `429 rate_limited` (or 500, or a dropped connection) on `POST /auth/resend-verification`. Click Resend email in the yellow banner.
Expected: the error message.
Actual: the spinner stops and nothing is shown. The same button on `/verify` does show the message.

#### QA-005: An expired access token is not refreshed for `/auth/resend-verification` or `/auth/logout`

| | |
|---|---|
| Requirement | API design: error `token_expired` ("call `/auth/refresh` and retry"); A3 and A6 are User-level endpoints |
| Verified | Run for resend; Read for logout |
| Believed cause | `src/api/client.ts:56` skips the refresh for every path starting with `/auth/` |

Steps: sign up, stay on `/verify`. Force one `401 token_expired` on `POST /auth/resend-verification`. Click Resend email.
Expected: `POST /auth/refresh`, then the resend is repeated and succeeds.
Actual: no refresh; the raw error text is shown. With the real backend this happens to anyone who leaves the check-your-inbox screen open for more than 15 minutes, which is the normal case for that screen. Only `/auth/refresh` itself (and the public endpoints) need to be excluded.

#### QA-006: A failed log out is shown as a successful one

| | |
|---|---|
| Requirement | PRD A2; API A6 |
| Verified | Run |
| Believed cause | `src/api/hooks.ts:40-43` (`onSettled` clears the user on failure too); `src/components/layout/AppShell.tsx:74` |

Steps: log in as demo, open Settings. Drop the connection on `POST /auth/logout`. Log out. Then reload.
Expected: either an error, or the user really is signed out.
Actual: the login page appears with no message; after a reload the user is back in the app, still signed in. On a shared computer the user believes they logged out. Related: "Wrong email? Start again" on `/verify` (`VerifyEmailPage.tsx:128`) with a failed log out leaves the user on `/verify` in the signed-out state with no explanation.

#### QA-007: Sign up with an existing email does not offer a password reset

| | |
|---|---|
| Requirement | PRD A-1 ("Show 'account exists', offer login or password reset") |
| Verified | Run |
| Believed cause | `src/features/auth/SignupPage.tsx:38-50` |

Steps: sign up with ` DEMO@ReelForge.test `.
Expected: the message plus links to log in and to reset the password.
Actual: "An account with this email already exists." and nothing else in the message. The only link on the card is the standing "Already have an account? Log in" footer; there is no route to `/forgot` from this screen.

#### QA-008: The delete dialog shows the previous Reel's error

| | |
|---|---|
| Requirement | PRD D4 |
| Verified | Run |
| Believed cause | `src/features/projects/ProjectsPage.tsx:144-148`; the mutation from line 39 is never reset when the dialog closes (lines 140, 150) |

Steps: demo, `/app/projects`. Force `500` on `DELETE /projects/:id`. Delete "Why most creators quit": the error appears. Cancel. Remove the forced failure. Open Delete for "Morning dopamine reset".
Expected: a clean confirmation.
Actual: the new dialog opens already showing the old error, before anything was attempted. Screenshot: `delete-stale-error.png`.

#### QA-009: `/verify` shows the signed-out screen to a signed-in user while the account loads, and permanently if loading fails

| | |
|---|---|
| Requirement | PRD A-2 (clear message and Resend) |
| Verified | Run |
| Believed cause | `src/features/auth/VerifyEmailPage.tsx:71, 98, 110-120` (`useMe` data is treated as "no user" while pending or failed); `/verify` has no guard (`src/App.tsx:26`) |

Steps: sign up (unverified). Make `GET /me` take 2 seconds and reload `/verify`. Then force `500` on `GET /me` and reload.
Expected: a loading state, then "We sent a verification link to …" with Resend; on failure an error with retry, as the other pages have.
Actual: for the 2 seconds it reads "Log in to get a new verification link." with a Log in button (by reading, that button leads to `/login`, which sends a signed-in unverified user straight back). With the 500 it stays that way with no error. Screenshot: `verify-reload-early.png`.

#### QA-010: The seed data covers 6 of the 11 project statuses

| | |
|---|---|
| Requirement | Milestones F3 "done when: dashboard and library show mock projects in every status"; status document "recent Reels in every status"; README "7 Reels in different states" |
| Verified | Read (seed), Run (the missing statuses were injected and do render and link correctly) |
| Believed cause | `src/mocks/db.ts:72-80` |

Present: posted, ready, generating, draft, render_failed, rendered. Missing: scripting, needs_attention, rendering, posting, post_failed. The components handle all eleven; only the seed and the claims are short. The dashboard also shows just the five most recent, so it cannot show every status whatever the seed holds.

#### QA-011: One failed background poll replaces the whole list with an error

| | |
|---|---|
| Requirement | PRD flow 8 step 3 (progress shown live) |
| Verified | Run |
| Believed cause | `src/features/dashboard/DashboardPage.tsx:114` and `src/features/projects/ProjectsPage.tsx:91` test `isError` before using the data that is still cached |

Steps: demo dashboard (one Reel is generating, so the page polls). Drop two consecutive `GET /projects` calls.
Expected: the tiles and cards stay; perhaps a small "can't refresh" note.
Actual: tiles and Recent Reels vanish and "We couldn't load your Reels" takes their place until the next poll succeeds a few seconds later. On a weak phone connection the page will flicker between the two.

#### QA-012: The library shows at most 100 Reels

| | |
|---|---|
| Requirement | API design conventions (pagination, `nextCursor`), B2; PRD D1 |
| Verified | Read |
| Believed cause | `src/features/projects/ProjectsPage.tsx:38` (`limit: 100`), `src/api/hooks.ts:61-77` (`nextCursor` never used) |

A creator posting 3–7 Reels a week passes 100 within months. There is no "load more" and no hint that the list is cut. The mock always returns `nextCursor: null`, so this cannot be seen in mock mode.

#### QA-013: The mock API differs from the API design

| | |
|---|---|
| Requirement | API design A1, A3, A6, A10, B2, conventions; Milestones ("a mock API that follows API_DESIGN.md exactly") |
| Verified | Read |
| File | `src/mocks/handlers.ts` |

| # | Endpoint | API design | Mock | Line |
|---|---|---|---|---|
| a | A3 resend | `409` if already verified; no code named, and none in the error-code table fits | `409 already_verified`, a code that is not in the table | 75 |
| b | A1 signup | `name` 1–80; unknown fields rejected with `400` | Name length not checked; unknown fields accepted | 34-40 |
| c | A10 `PATCH /me` | Validated; unknown fields rejected; `onboardingCompleted` true only | No validation at all (an empty name is accepted) | 152-166 |
| d | A6 logout | User level, so `401` without a session | Always `204` | 103-109 |
| e | Conventions | `403 csrf_failed` when the header is missing or wrong | No handler looks at `X-CSRF-Token` | all |
| f | A1, A3, A4, A7 | `429 rate_limited`; A4 `403 account_disabled` | Never returned | |
| g | Conventions | `401 token_expired` | Never returned (the status document says so) | |
| h | B2 | `cursor` paging; a status filter of known values | `cursor` ignored, always `nextCursor: null`; unknown status values silently match nothing | 174-177 |

None of these breaks the screens today. They matter because the real backend is built from the document and the frontend has only ever met the mock: (a) the frontend may start depending on a code the backend will not send, and (e), (f), (g) mean those client paths have never run against anything. Shapes and success status codes for A1–A10, B2, B6 and H1 do match.

### Polish

| Id | Finding | Requirement | Verified | Where |
|---|---|---|---|---|
| QA-014 | Deleting a Reel that another tab already deleted leaves the dialog open with "We couldn't find that Reel." A `404` on delete should count as done and close | PRD D4; API B6 | Run | `ProjectsPage.tsx:46-49` |
| QA-015 | A used verification link shows "This link didn't work" with "Get a new link", which only leads to the Resend screen (two steps, not the Resend button A-2 asks for). A user who is signed in and already verified gets the same failure wording and is then dropped on Home | PRD A-2 | Run | `VerifyEmailPage.tsx:49-66` |
| QA-016 | "Credits reset in 1 days" | PRD D5 | Run | `DashboardPage.tsx:150` |
| QA-017 | The empty dashboard has no "Create your first Reel" button. It has the prompt and three starter ideas instead. The words appear only in the empty library ("Create your first Reel from an idea…"). The Stitch empty-state screen may show something else; I cannot see it | PRD flow 1 step 5 | Run | `DashboardPage.tsx:122-143` |
| QA-018 | `/app/p/:id/render` (Stitch screen 12) has no route. It shows the bare 404 page outside the app shell, not the "not built yet" placeholder the other designed screens get | STITCH_PROMPTS screen table | Run | `App.tsx:32-38` |
| QA-019 | Long text with no spaces overflows: a long Reel title runs out of the delete dialog and is cut off; an 80-character name runs off the page header at phone width | PRD section 12 | Run (`edge-delete-longtitle.png`, `edge-dash-390.png`) | `Dialog.tsx:43`, `AppShell.tsx:147` |
| QA-020 | Cards show a length only when a rendered duration exists. Draft, writing and generating cards show none, though `targetSeconds` is available | PRD D1 ("length"); Stitch prompt 2 | Run | `ReelCard.tsx:63-67` |
| QA-021 | README line 3 and `.env.example` say the mock answers "every `/api/v1` request the way API_DESIGN.md describes". 13 of the 70 endpoints are mocked. Anything else passes through to the dev server, which answers `200` with the HTML page; the client then returns `null` as if it were data. Harmless today, a confusing failure for whoever builds F4 | README | Read | `README.md:3`, `main.tsx:20`, `client.ts:64-65` |

### Deferred to F9 (reported now, not counted in the verdict)

The milestones document puts phone-width layouts and the keyboard and screen-reader pass in F9. The PRD still says the web app should work on a phone browser, so these are listed with the severity they would have if F9 were in scope.

| Id | Would be | Finding | Verified | Where |
|---|---|---|---|---|
| QA-022 | Major | At 390 px wide the dashboard's prompt box collapses to 16 px. Nothing can be typed or seen, and the Create button sticks out of the box. The main action of the product is unusable on a phone. Fine at 768 px | Run (`r390-dash.png`) | `DashboardPage.tsx:81-96` |
| QA-023 | Major | On a touch screen the per-card "More options" button is invisible (it appears only on hover or keyboard focus), so there is no discoverable way to delete a Reel on a phone. Tapping the empty corner does open it | Run, touch emulation (`touch-lib.png`) | `ReelCard.tsx:95-98` |
| QA-024 | Minor | Below 640 px the Instagram status pill is hidden, so Instagram status is not visible anywhere on a phone | Run | `AppShell.tsx:125` |
| QA-025 | Minor | Keyboard and screen-reader basics. (a) Buttons wrapped in links give two tab stops for one action and are invalid HTML: `VerifyEmailPage.tsx:55-59, 115-119`, `ResetPasswordPage.tsx:39-43`, `ProjectsPage.tsx:54-58, 122-126`, `ComingSoon.tsx:17-19, 30-32`. (b) After the delete dialog closes or a Reel is deleted, focus drops to the page body, because the menu item that opened it no longer exists. (c) The account and card menus use `role="menu"` but do not move focus into the menu, ignore arrow keys, and stay open when focus tabs away. (d) The app shell has no `<main>` landmark, the document title is "ReelForge" on every page, and focus is not moved on navigation. (e) Placeholder text is white at 30% on a near-black field, about 2.5:1 by calculation | Run for a–d, Read for e | as listed |

Everything is reachable and operable by keyboard, focus rings are visible, form fields are labelled, errors are announced, and reduced motion is respected.

---

## 2. What passed

**Verified by running**

- F1 "done when": the app opens; `tsc --noEmit` exits 0; a production build succeeds at 342.58 kB JS, 104.78 kB gzipped, as the status document says.
- F2 "done when": sign up → check your inbox → verification link → app; log out; log in; forgot password → reset link → new password → log in.
- Status-document claims sampled and confirmed: reused verification link message; unverified user can open the app and sees the banner; wrong-password message; demo login; mismatched passwords on reset; empty sign-up form shows field messages; new-user dashboard welcome and starter ideas; demo dashboard stats and advancing progress bar; library grid, status filter, empty filter result, delete with confirmation.
- Client conventions: `X-CSRF-Token` taken from the `rf_csrf` cookie (URL-decoded) on every POST and DELETE and not on GET; one refresh and one retry on `401 token_expired` for GET and DELETE; errors read from `error.message`; network failures give a plain message; polling every 2 s while something is in progress.
- Validation: empty, whitespace-only name, bad email, 81-character name, 129-character password, common password (server message lands on the field).
- Emails are trimmed and case-insensitive on sign up, log in and forgot password.
- PRD A-1 on the reset side: the forgot-password screen says the same thing for known and unknown emails. Login gives the same message for a wrong password and an unknown email.
- Forced `429`, `403 account_disabled`, `500` and a dropped connection on login each show a message and leave the form usable.
- `GET /me` failing shows "We couldn't load your account" with a working Try again.
- Route guards: signed-out visits to `/app…` go to `/login`; signed-in visits to `/login`, `/signup`, `/forgot` go into the app.
- Back and forward through verify and reset never return to a used token URL. Reload is sensible on every screen except `/verify` (QA-009).
- Double-click and double-Enter on Log in and Create account send one request. Two human-speed clicks on Delete send one.
- Sidebar: Home, Projects, Settings, and Admin only for an admin. No Music item. None of the invented design labels exist in the source.
- Dashboard: prompt, Ideas, Create, mode and length chips, three stat tiles, five recent Reels plus a New Reel card, counts from `counts`, credits "86 of 100".
- Library: every filter, search (case, whitespace, special characters), combined filter and search, Clear filters, empty states.
- Delete: confirmation names the Reel; Escape, outside click and Cancel close it; the card goes; dashboard counts update and stay updated after reload.
- PRD flow 8 step 2: each of the 11 statuses links to the right step.
- Titles containing HTML are shown as text.
- Phone (390×844) and tablet (768×1024): every sign-in screen, the library, the delete dialog, the placeholders and the 404 page lay out without horizontal scrolling. The dashboard does too apart from QA-022.

**Concluded by reading**

- `src/api/types.ts` matches the User and ProjectSummary objects field for field; the status, mode, caption-style and Instagram-status values match the database design.
- Mock handlers return the documented success codes and shapes for A1–A10, B2, B6, H1 (differences are in QA-013).
- Theme tokens match the Stitch shared style (colours, fonts, glass surface).

**Design comparison (text only)**

Login matches the prompt and the UI review: title, the replaced subtitle, fields, show/hide, right-aligned "Forgot password?", gradient "Log in", "New here? Create an account", three Reel cards with the three captions, logo, "Idea in. Reel out.", no social buttons. Dashboard matches the prompt after the agreed removals. Two small differences a reader of the prompt would notice: the stat tiles sit above Recent Reels (the prompt lists them after), and there are four length chips (the prompt shows one). The bell icon is left out on purpose (status document, open question 4).

---

## 3. Not tested

| What | Why |
|---|---|
| Opening the verification link in a different browser or device | The mock database lives in one browser's storage, so the token does not exist elsewhere. See question 1 |
| Lockout after many failed logins (PRD A-3) | Server behaviour; the client was only checked for showing a `429` |
| Real cookie, CSRF and expiry behaviour | No backend |
| Edge, Safari, Firefox | Only Chromium is installed |
| Pixel comparison with Stitch | Cannot see the designs |

## 4. Out of scope (not defects)

- Onboarding, New Reel, script editor, Reel editor, publish, settings and admin show a "not built yet" placeholder. `/admin` opens for non-admins, but it is only the placeholder.
- New users are not sent to onboarding after verifying (F4).
- The credits pill and the Instagram pill link to Settings tabs that do not exist yet (F8). Credits history (PRD D5) is F8.
- Duplicate project (PRD D3) is P1 and absent.
- The bell icon from the design is left out (status document, open question 4).
- Reel thumbnails are generated gradients (status document, known gaps).

## 5. Questions

| # | Question | Why it matters |
|---|---|---|
| 1 | Does A2 `verify-email` sign the user in? A1 and A4 say "and cookies"; A2 only says `{"user": User}`. The frontend and the mock assume it does: after verifying from a fresh browser the app goes to `/app` | If the backend does not set cookies, a user who verifies on another device lands in the app shell with every request failing (see QA-003) |
| 2 | A3 `resend-verification`: the API design says User level with no body; the technical design's endpoint list (5.5) says it takes `email`. Which is right, and what is the `409` code? | Decides whether a signed-out user with an expired link can get a new one without logging in (PRD A-2), and settles QA-013a |
| 3 | CSRF: do public POSTs (signup, login, forgot, reset, verify) need the header? There is no `rf_csrf` cookie before the first login, so the client sends an empty value. And if the API is on `api.<domain>`, how does the web app read `rf_csrf`? | The client reads it from `document.cookie`, which only works if the cookie is not httpOnly and is set for a domain the app shares |
| 4 | Where is the landing page (PRD flow 1 step 1, technical design route `/`)? No frontend milestone includes it; `/` redirects to the app | Flow 1 cannot start as written |
| 5 | PRD D1 lists 8 statuses with a single "failed"; the database and technical design have 11. The API design never lists the values of `ProjectSummary.status` | The frontend follows the database. The API design should list them |
| 6 | Is `scripting` a draft or in progress? The API design gives it a progress value and polls for it; the library puts it under Drafts and leaves it out of "In progress". The comment in `types.ts:53` also leaves it out | Filter behaviour |
| 7 | The API design (section 5) and the UI review say dashboard credits come from `GET /credits`. The frontend never calls it; it uses `credits` inside `GET /me`, cached for 60 seconds | Fine today. Once actions spend credits the pill can be stale; decide which source is the rule |
| 8 | B2: what order are projects returned in? And for a new user, are `niche` and `defaults.voiceId` empty strings or null? | The mock sorts by `updatedAt` descending and uses empty strings; the types say `string` |
| 9 | A signed-in user who opens a reset link (`/reset?token=…`) is redirected into the app and the link is ignored. Intended? | Someone signed in as one account cannot reset another on the same browser |
| 10 | Unverified users: after login they are sent to `/verify`, but typing `/app` works and shows the banner. Which is intended? | The status document describes the second; the login behaviour comes from the race in QA-001 |
| 11 | Phone width is in F9, but the dashboard prompt is unusable on a phone now (QA-022, QA-023). Wait for F9 or fix as screens are built? | Cost of retrofitting grows with each milestone |
| 12 | Is there a maximum length for a project title? B5 accepts `title` with no limit | Layout (QA-019) and validation |

## 6. Evidence

Scripts and screenshots are in the QA scratch folder `<QA scratch folder, temporary and not in the repository>` (`t1.cjs`–`t4.cjs`, `lib.cjs`, `force.cjs`, `shots\`). The folder is temporary. Run a script with:

```
$env:NODE_PATH = '<workspace>\reelforge\frontend\node_modules'; node t3.cjs
```
