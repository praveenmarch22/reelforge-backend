# ReelForge: test plan

| | |
|---|---|
| Owner | QA |
| Last updated | 2026-11-18 |
| Covers so far | Frontend milestones F1, F2, F3 |
| Related | [PRD](../PRD.md), [API design](../API_DESIGN.md), [UI review](../UI_REVIEW.md), [Stitch prompts](../STITCH_PROMPTS.md), [Milestones](../../MILESTONES.md), [Status](../../PROJECT_STATUS.md) |

This plan is kept current as milestones are added. Test cases and reports are per milestone group: `TEST_CASES_<milestones>.md`, `QA_REPORT_<milestones>.md`.

## 1. Scope

**In scope now**

| Milestone | What is tested |
|---|---|
| F1 | App starts, type-check and build pass, design tokens, UI kit, router, API client conventions, mock API against the API design |
| F2 | Login, sign up, check-your-email / verify, forgot password, set new password, route guards, session refresh |
| F3 | App shell (sidebar, account menu, credits and Instagram pills), dashboard (with empty state), projects library (search, filters, delete) |

**Out of scope until built**: onboarding (F4), New Reel and script editor (F5), Reel editor (F6), render and publish (F7), settings, credits history and admin (F8). These routes show a "not built yet" placeholder on purpose. The backend (track B) does not exist; everything runs against the mock API.

**Deferred by the milestones document to F9**: phone-width layouts, the keyboard and screen-reader pass, 404 and error pages, automated tests. QA still looks at these early and reports what it finds in a separate "deferred" section, so the F9 work starts from a list. They do not count towards a milestone verdict before F9.

## 2. What the product is checked against

1. **PRD**: feature list (section 5), user flows (section 6), edge cases (section 11), non-functional requirements (section 12).
2. **API design**: conventions (cookies, CSRF header, error format, 401 refresh, polling, pagination), each endpoint's status codes, error codes and response shapes, the shared objects.
3. **UI review and Stitch prompts**: copy, elements that must be present, agreed removals. QA cannot see the Stitch images, so this is a text comparison against the prompts and the review notes.
4. **Milestones and status documents**: each milestone's "done when" line, and a sample of the status document's "what was verified" claims.

Every finding cites the requirement it breaks. Ambiguities and contradictions between documents are reported as questions, not defects.

## 3. Approach

| Type | How | When |
|---|---|---|
| Document review | Read the code against the documents: endpoints, types, error handling, unreachable states | Every milestone |
| Exploratory, real browser | Playwright scripts (CommonJS, run with the frontend's own Playwright) driving Chromium against the dev server. Each check uses a fresh browser context, so the mock database starts from its seed | Every milestone |
| Negative and failure testing | Forced API failures (500, 401 `token_expired`, 429, 403, dropped connection, slow response) | Every milestone |
| Responsive | 390×844 (phone), 768×1024 (tablet), 1280×800, 1440×900, 1920×1080; full-page screenshots looked at by eye plus an automatic check for horizontal overflow | Every milestone; verdict-relevant from F9 |
| Keyboard and accessibility basics | Tab order, focus visibility, focus after dialogs and menus, labels, landmarks, reduced motion, contrast of small text (calculated) | Every milestone; verdict-relevant from F9 |
| Multi-tab and history | Two tabs in one context, back/forward, reload on every screen | Every milestone with auth or editing |

### How failures are simulated

The mock API is an MSW service worker. Playwright's `page.route` does not see requests a service worker answers, so failures are injected one level above it: an init script wraps `window.fetch` and, for chosen `/api/v1` paths, returns a forced response, throws a network error, or adds a delay. From the app's point of view this is the same as the server answering that way. It does not test the service worker or a real network.

### What is automated and what is manual

| | State |
|---|---|
| QA exploratory scripts | Scripted but not a regression suite. They live in the QA scratch folder and print observations for a person to judge |
| Unit and component tests (Vitest), end-to-end tests (Playwright, `frontend/e2e`) | Being written by the developer side; owned there, not by QA. QA does not run or rely on them yet. Once stable, the cases in the test-case document marked "candidate for automation" should be covered there |
| Visual comparison with Stitch | Manual, by the product owner; QA covers only the text |

## 4. Environments

| Item | Value |
|---|---|
| App | `frontend/` dev server, http://localhost:5173, mock mode on |
| Browser | Playwright Chromium (the one installed with the frontend) |
| OS | Windows 11, Node 18 |
| Data | Mock database in the browser's localStorage, re-seeded per context. Demo account from `frontend/src/mocks/db.ts`; new accounts created by signing up |

**Known limits of this environment**

- Chromium only. The PRD also names Edge, Safari and Firefox; none of those were tested.
- No real backend: cookies, real CSRF checks, real token expiry after 15 minutes, rate limits, email delivery and lockout cannot be exercised. Where it matters they are simulated and marked as such.
- The mock database belongs to one browser profile, so "open the email link on another device" cannot succeed in mock mode.

## 5. Entry and exit criteria

**Entry (QA starts a milestone)**

- The status document marks the milestone done.
- The dev server starts and the type-check passes.

**Exit (milestone passes)**

- The milestone's "done when" line is met, verified by running.
- No open Blocker or Major finding.
- Every Minor finding is either fixed or accepted in writing by the product owner.
- Open questions that change behaviour are answered and the documents updated.

**Verdicts**: *pass* (exit criteria met), *pass with issues* (main flows work, no Blocker, Major or Minor findings open), *fail* (a Blocker, or the "done when" line is not met).

## 6. Severity

| Level | Meaning |
|---|---|
| Blocker | A main flow cannot be completed, or data is lost |
| Major | A specified behaviour is wrong or missing, with no easy workaround |
| Minor | Wrong but with a workaround, or an edge case |
| Polish | Wording, spacing, visual differences from the design |

## 7. Risks to watch in later milestones

- The mock API and the API design drifting apart; the real backend will be built from the document, so each milestone re-checks the mock handlers it adds.
- Session handling: several F1–F3 findings are about what happens when the session ends. The editor (F6) depends on this for PRD A-4 (autosave, then return to the same project).
- Phone width: the dashboard prompt is already unusable at 390 px. Screens in F5–F7 are denser.
- Credits: the balance currently comes from `GET /me` only and is cached; once actions spend credits (F5) it must stay correct.
