# ReelForge: developer response to QA and Tester findings, F1–F3

| | |
|---|---|
| Date | 2026-11-18 |
| Inputs | [QA_REPORT_F1-F3.md](QA_REPORT_F1-F3.md) (QA-001 to QA-025), the Tester's report (BUG-001 to BUG-006) |
| Written by | The developer. The QA report itself is left as QA wrote it. |

Status values: **Fixed** (changed in the frontend, covered by a test), **Answered** (a document was unclear; the document is updated), **Deferred** (agreed to do later, with the milestone).

## Major

| Finding | Status | What was done |
|---|---|---|
| QA-001 / BUG-002: after login you land on `/app`, not the page you asked for | Fixed | The guest-only guard now redirects to the requested page. Logout does a full page load, so the next login can't inherit the previous user's page |
| QA-002 / BUG-001: account menu opens behind page content, so Log out can't be clicked | Fixed | The sidebar now sits above the page content; this also fixes the clipped tooltips |
| QA-003: when the session ends, the app never returns to login | Fixed | Any request that ends in "not signed in" (after a failed refresh) clears the cached user, and the route guard sends the visitor to login and brings them back afterwards |

## Minor

| Finding | Status | What was done |
|---|---|---|
| QA-004: Resend in the verify banner fails silently | Fixed | The banner shows the error |
| QA-005 / BUG-003: expired session not refreshed for resend and logout | Fixed | Only the six public sign-in endpoints skip the refresh |
| QA-006: failed logout shown as success | Fixed | The user stays signed in and sees "Couldn't log out"; "Start again" on the verify screen behaves the same way |
| QA-007: existing email offers no reset | Fixed | The message links to "Log in" and "reset your password" |
| QA-008: delete dialog shows the previous Reel's error | Fixed | The error is cleared when the dialog opens or is cancelled |
| QA-009: verify screen shows the wrong text while loading, and no error if loading fails | Fixed | Loading and "couldn't load your account" states added |
| QA-010: seed data has 6 of the 11 statuses | Deferred to F5–F7 | The other five statuses come from flows that don't exist yet (script writing, rendering, posting). The cards and pills are unit-tested with all 11. The status document's "every status" claim is corrected |
| QA-011: one failed poll replaces the list with an error | Fixed | The last good list stays on screen; the error shows only when there is nothing to show |
| QA-012: library stops at 100 Reels | Deferred to F9 | Needs "load more" paging |
| QA-013: mock API differs from the API design | Partly fixed | (b) name length now checked; unmocked endpoints now return `501 not_mocked`. (a) `already_verified` added to the API design. Not done, by choice, because the mock is a stand-in and the real backend enforces them: unknown-field rejection, A10 validation, CSRF checks, rate limits, `account_disabled`, `token_expired`, cursors |

## Polish

| Finding | Status | What was done |
|---|---|---|
| QA-014: delete of an already-deleted Reel leaves the dialog open | Fixed | The dialog closes and the list refreshes |
| QA-015 / BUG-004: used verification link needs two steps to reach Resend | Fixed | Resend is on that screen; an already-verified user sees "Your email is already verified" |
| QA-016: "1 days" | Fixed | "1 day", and "Today" for zero |
| QA-017 / BUG-005: no "Create your first Reel" button | Fixed | Added to the empty dashboard |
| QA-018: `/app/p/:id/render` shows a bare 404 | Fixed | Shows the "not built yet" placeholder (F7) |
| QA-019: long text overflows the delete dialog and page header | Fixed | Text wraps in the dialog and truncates in the header |
| QA-020: cards without a rendered duration show no length | Fixed | Shows the target length, for example "~30s" |
| QA-021: README says every request is mocked | Fixed | README corrected; the client also rejects a non-JSON success response |
| BUG-006: mock accepts names over 80 characters | Fixed | |

## Reported as "deferred to F9" by QA

| Finding | Status | What was done |
|---|---|---|
| QA-022: at phone width the dashboard prompt can't be typed in | Fixed now | The input takes its own row on narrow screens |
| QA-023: on touch screens the card menu is invisible | Fixed now | Always visible when the device has no hover |
| QA-024: Instagram pill hidden below 640 px | Deferred to F9 | Part of the phone-layout pass |
| QA-025: keyboard and screen-reader basics | Partly fixed | (a) buttons inside links replaced by a single link styled as a button; (d) the app content is now in a `<main>` landmark and the 404 page has a heading. Deferred to F9: (b) focus return after the dialog, (c) arrow keys in menus, per-page titles, (e) placeholder contrast (raised slightly on the dashboard prompt only) |

## Tester observations

| Observation | Status |
|---|---|
| 404 page has no `h1` | Fixed |
| `/reset` redirects signed-in users away | Fixed: the reset link works signed in or out, and resetting signs the browser out |
| Stale "return to" page after logout | Fixed by the full page load on logout |

## Found by the Tester's second pass (in the fixes themselves)

| Finding | Status | What was done |
|---|---|---|
| BUG-007: after Resend on the bad-link screen, the mock-mode verification link did nothing | Fixed | Each link now starts a fresh confirmation |
| BUG-008: a bad link showed the signed-out screen to a signed-in user while the account was loading (rest of QA-009) | Fixed | A loading state is shown until the account is known |

## QA's questions

| # | Question | Answer |
|---|---|---|
| 1 | Does verify-email set session cookies? | Yes. Added to API A2 |
| 2 | Resend verification: body or not, and the 409 code? | No body; `409 already_verified`. API A3 and the technical design are updated |
| 3 | CSRF on public POSTs; reading the cookie across subdomains | Public endpoints ignore the header; the cookie is set for the parent domain. Added to the API conventions |
| 4 | Landing page at `/` is in no milestone | **Open, for the product owner.** Until decided, `/` goes to the app or login |
| 5 | Status lists differ between PRD, database and API | The API design now lists the 11 statuses and how the web app groups them |
| 6 | Is `scripting` a draft or in progress? | Shown under Drafts in the library, with its progress bar. Recorded in the API design |
| 7 | Credits from `GET /credits` or `/me`? | The pill and tiles use `/me` (already loaded). `GET /credits` is for the settings page, which also needs the cost table (F8) |
| 8 | Sort order; empty strings or null | Newest first; empty strings. Added to the API design |
| 9 | Signed-in user opening a reset link | Now works (see above) |
| 10 | Unverified users: `/verify` after login, but `/app` reachable by URL | **Open, for the product owner.** Current behaviour: they can browse the app with a "verify your email" banner; creating is blocked by the API |
| 11 | Should phone-width problems wait for F9? | The two that made a screen unusable are fixed now; the rest wait |
| 12 | Maximum project title length | **Open.** Proposed: 120 characters, to be added to API B5 |
