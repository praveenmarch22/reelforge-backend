# ReelForge: QA report for B2 (accounts)

| | |
|---|---|
| Date | 2026-11-24 |
| Milestone | B2 Accounts: auth endpoints A1–A12, email |
| Build tested | `backend/` working tree on 2026-11-24 (B2 not committed), Compose stack running, API at http://localhost:5000/api/v1, test inbox at http://localhost:8025 |
| Checked against | API design (section 2, group A, section 4), PRD (A1–A5, flow 1, edge cases A-1 to A-5), technical design (sign-in session, Security), DB design and `schema.sql`, and the web app's `src/api` and `src/mocks` |
| Scope | Accounts code only. Foundations are in [QA_REPORT_B1.md](QA_REPORT_B1.md) and are not repeated here |

## Verdict: pass with issues

Login, email verification, resend, refresh with rotation and reuse detection, logout, forgot and reset password, profile read and update, change password, delete account and the purge task all behave as the API design says when given well-formed input. All of that was checked by running. No Blocker.

Two Major findings should be fixed before the web app is pointed at this API:

- **QB2-001**: two refreshes at the same moment (two tabs) log the browser out.
- **QB2-002**: an invalid email address on sign-up or forgot-password is answered with `500` instead of `400 validation_error`.

| Severity | Count | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 2 | QB2-001, QB2-002 |
| Minor | 7 | QB2-003 to QB2-009 |
| Polish | 4 | QB2-010 to QB2-013 |
| Questions | 6 | Section 5 |

**"Verified" column**: *Run* means reproduced against the running stack (curl, or Chromium through Playwright where it says "browser"). *Read* means concluded from the code only.

**Limits of this review**

- **A successful sign-up (A1) was not run.** The limit of 3 sign-ups per hour per address was already used up when I started (counter at 5, 55 minutes left), and every caller on this machine shares one address (the Docker gateway). Sign-up validation was run, because it happens before the limit. The four accounts I used (`qb2a…` to `qb2d…@example.com`) were created inside the `api` container with the product's own `accounts.create_user` and `issue_token`. The sign-up success path is therefore Read, plus the one sign-up visible in the API log from the developer's own smoke run.
- Per-email login limit: only the per-address limit could be triggered from one machine. The per-email counter was seen counting in Redis; its 429 is Read.
- "Redis down" was simulated with a second, throwaway app object inside the container pointed at a dead address. Nothing was stopped.
- The purge task was run once by hand in the `api` container after I back-dated `deleted_at` on my own deleted QA account. No other account matched.
- Timing figures are from 3 to 4 samples each; the limits leave no room for more.
- What I left behind: accounts `qb2a`, `qb2c`, `qb2d` in the development database (`qb2b` was deleted and purged), a few dozen emails in the test inbox, and the forgot-password per-address counter at 10 of 10 until about 11:29 UTC.

---

## 1. Findings

### Major

#### QB2-001: Two refreshes at the same moment log the browser out

| | |
|---|---|
| Requirement | API A5 (refresh rotates and returns the user); technical design, sign-in session. The code's own comment at `sessions.py:37-39` says a simultaneous refresh from two tabs "is not theft" |
| Verified | Run (curl, and Chromium with two parallel `fetch` calls from a page on `http://localhost:5173`) |
| Cause | `api/reelforge_api/sessions.py:165-173` raises `_not_authenticated()`, whose default (`sessions.py:50-53`) is to clear all three cookies |

Steps:
1. Log in.
2. Send `POST /auth/refresh` twice at the same moment with the same cookies.

Expected: both callers end up with a working session (that is what the 10-second allowance is for).
Actual: one answers `200` with new cookies. The other waits for the first (row lock), then answers `401 not_authenticated` **with `Set-Cookie` headers that delete `rf_access`, `rf_refresh` and `rf_csrf`**. Because it always finishes second, its deletions land after the winner's new cookies. In the browser run: results `[200, 401]`, cookie jar empty afterwards, next `GET /me` → `401`, next refresh → `401`. The user is logged out in every tab.

The allowance only stops the family from being revoked; it does not keep the session. The web app shares one refresh per tab (`frontend/src/api/client.ts:59-70`), not across tabs, so this happens whenever two tabs notice the expired token together: two tabs polling a project every 2 s, or several tabs waking with the laptop.

Note for the fix: not clearing cookies inside the allowance is the smallest change, but the losing tab still gets a `401` from refresh and the web app then marks the user signed out (`client.ts:77-81`) until the page is reloaded. Both sides need to agree on what the loser receives.

#### QB2-002: An invalid email on sign-up or forgot-password returns 500

| | |
|---|---|
| Requirement | API A1 and A7: `400 validation_error`; section 2: `details.fields` |
| Verified | Run |
| Cause | `api/reelforge_api/schemas.py:171-172`. For `value_error` it reads `item["ctx"]["error"]`. Pydantic's email check reports `value_error` with `ctx = {"reason": …}`, so this is a `KeyError`. The email wording two lines below is never reached |

| Request | Expected | Actual |
|---|---|---|
| `POST /auth/signup` `{"email":"not-an-email", …}` | 400, `fields.email` | 500 `internal_error` |
| `POST /auth/signup` `{"email":"a@b", …}` | 400 | 500 |
| `POST /auth/signup` `{"email":"demo@reelforge.test", …}` | 400 (the library refuses `.test`, `.local` and similar) | 500 |
| `POST /auth/forgot-password` `{"email":""}` | 400 | 500 |
| `POST /auth/forgot-password` `{"email":"not-an-email"}` | 400 | 500 |

Log line: `schemas.py", line 172, in _field_errors … KeyError: 'error'`.

Why Major: it is the basic validation path of two public endpoints, and each occurrence is logged as an unhandled error. The web app checks the address first, which hides most cases, but its check is looser than the server's: `someone@reelforge.test` passes in the browser and then shows "Something went wrong on our side".

### Minor

#### QB2-003: Two more inputs that return 500

| | |
|---|---|
| Requirement | API section 2: validation → `400 validation_error` |
| Verified | Run |

- `POST /auth/login` with a NUL character in the email (`"a\u0000b@example.com"`) → 500. `LoginBody.email` is plain text (`schemas.py:110`) and goes straight into the query (`accounts.py:19-23`); PostgreSQL refuses NUL. No session needed. The attempt is counted by the login limit first.
- `PATCH /me` with a lone surrogate in `name` (`"a\ud800b"`) → 500 at the database write. The same value in a required field (the `token` of A2) is refused with 400, so only the optional fields are affected (`schemas.py:134-136`, and `defaults.voiceId` at `:128`).

#### QB2-004: Forgot-password answers more slowly when the account exists

| | |
|---|---|
| Requirement | API A7 "Always 204, whether or not the email exists"; PRD A-1 "Don't reveal more than that on the reset page" |
| Verified | Run (3 pairs, alternating) |
| Cause | `api/reelforge_api/routes/auth.py:159-163`: the token write, the commit and the queueing of the email happen only for a real account |

| | Response time |
|---|---|
| Unknown email | 44, 25, 28 ms |
| Existing email | 80, 116, 99 ms |

The status code is the same; the time is not. Sign-up already tells a caller whether an address is registered (`409 email_taken`, by design), but only 3 times an hour; this path allows 10 an hour per address without the person receiving anything that looks like a sign-up. Login does not have this problem: unknown email 274–400 ms, wrong password 251–252 ms (Run), thanks to the dummy hash.

With the queue unreachable the gap becomes 6 seconds (see question 1).

#### QB2-005: The verification link is a 24-hour sign-in link that replaces whoever is signed in

| | |
|---|---|
| Requirement | API A2 as written ("opening the link signs the user in on that browser"), against the technical design's Security section. API F2 already guards against the same kind of mix-up for Instagram ("stops one person's approval link from connecting another person's account") |
| Verified | Run (mechanics). The attack itself is reasoning |
| Where | `api/reelforge_api/routes/auth.py:73-90`, `sessions.py:143-147` |

The code does what A2 says, so this is about the rule, not the implementation.

- **Account swap.** Signed in as user A, I posted user B's unused verification token from the same cookie jar: `200`, the browser now holds B's session, A's session is revoked. Someone can sign up, keep their own verification link unopened and send it to another person. That person clicks, lands signed in to the sender's account, and is taken straight to onboarding, whose third step is "connect Instagram". The Instagram account is then connected to an account the sender has the password for.
- **Survives a password reset.** After `POST /auth/reset-password` for user D (all sessions revoked, confirmed), D's unused verification token still returned `200` with a new session. A8 says all sessions are revoked; an outstanding sign-in link is left. `accounts.set_password` (`accounts.py:88-95`) cancels reset tokens only.

Possible rule: the link always verifies, but signs in only when the browser has no session or already has that user's session. See question 2.

#### QB2-006: After deleting an account, its email is refused for 7 days with advice that cannot work

| | |
|---|---|
| Requirement | PRD A-1 ("offer login or password reset"); PRD A5; API A12 |
| Verified | Read for sign-up (limit was used up). Run for the rest: login with the deleted account → `401 invalid_credentials`; forgot-password → `204` and no email arrives |
| Where | `api/reelforge_api/routes/auth.py:39-42` and `:57-63`; `users.email` is unique including deleted rows (`schema.sql`) |

Someone who deletes their account and signs up again with the same address within 7 days gets `409 email_taken`: "An account with this email already exists. Log in or reset your password." Neither works, and nothing says why. DB design decision 7 says the 7 days exist so a delete can be undone, but there is no way to undo it. See question 3.

#### QB2-007: A database error writes the query's values into the log

| | |
|---|---|
| Requirement | Technical design, Security and logging (no secrets or personal data in logs) |
| Verified | Run: the 500 from QB2-003 logged `[parameters: {'email_1': 'a\x00b@example.com'}]` |
| Where | `api/reelforge_api/__init__.py:31` (engine options without `hide_parameters`), `errors.py:78` |

Any unhandled SQLAlchemy error logs the statement's parameters. For a failed write to `users` that is the email and the password hash; for `auth_tokens`, the token hash; for `refresh_tokens`, the IP address. Otherwise the logs are clean: no email addresses, tokens or links in the API or worker logs during this review (Run, searched).

#### QB2-008: Settings added in B2 whose defaults break production without any error

| | |
|---|---|
| Requirement | API section 4 (per-IP limits); section 2 (`rf_csrf` "set for the parent domain"); the production checks in `config.py:64-77` |
| Verified | Read |
| Where | `api/reelforge_api/config.py:44`, `:47` |

- `TRUSTED_PROXY_HOPS` defaults to 0. Behind a load balancer every visitor then has the balancer's address: 3 sign-ups an hour and 5 logins a minute for all users together. This is what local development already shows (my first sign-up attempt was refused because of someone else's).
- `COOKIE_DOMAIN` defaults to empty. With the API on `api.<domain>`, the web app cannot read `rf_csrf`, so every POST, PATCH and DELETE that needs a session fails with `403 csrf_failed`.

Neither is checked by the production validator, and neither appears in `.env.example` or the README. Deployment is I2, but the settings and their validator were added here.

#### QB2-009: SMTP with STARTTLS does not check the mail server's certificate

| | |
|---|---|
| Requirement | Technical design, Security (emails carry sign-in and reset links) |
| Verified | Read; the library behaviour was confirmed in the container (Python 3.12: `starttls()` without a context uses one with `verify_mode=0`, `check_hostname=False`) |
| Where | `api/reelforge_api/mail.py:114-116`; `config.py:58` |

`smtp.starttls()` is called without an SSL context, so the connection is encrypted but the server is not authenticated. `SMTP_STARTTLS` also defaults to off and production does not require it, so with a real mail service the SMTP password and every reset link can go out in clear text. Not reachable locally (Mailpit has no TLS).

### Polish

#### QB2-010: Group A answers that the API design does not list

| | |
|---|---|
| Requirement | API A2, A5, A7, A8, A11, A12, section 4 |
| Verified | Run for A3/A7 and login; Read for the rest |

- `429` from A2 and A8 (30 an hour per address, `routes/auth.py:76`, `:170`), from A7 (10 an hour per address and 3 per email, `:157-158`) although A7 says "Always 204", and from A11 and A12 (5 password checks a minute, `routes/me.py:24`). None of these limits is in section 4.
- `403 account_disabled` from A2 and A5.
- A3 and A7 share one counter per email (`routes/auth.py:100`, `:158`). Run: after three resends, forgot-password for the same address answered `429`. Section 4 reads that way, so this is only to confirm it is intended: anyone can use up another person's three emails an hour.

The limits look sensible; the document should list them so the web app can word them.

#### QB2-011: An unknown field called `email` gets the wrong message

Run. `PATCH /me` with `"email": "x@example.com"` answers `fields.email = "Enter a valid email address"`; every other unknown field gets "This field isn't accepted". Cause: `schemas.py:173` matches on the field name before the kind of error. Someone trying to change their email is told their address is invalid.

#### QB2-012: Emails have no `Date` header

Run (headers read from the test inbox: `From`, `To`, `Subject`, `Mime-Version`, `Content-Type` only; Mailpit added the `Message-Id`). `mail.py:108-113`. A missing `Date` counts against a message with some spam filters. Worth adding before a real mail service is used.

#### QB2-013: The "refresh token reuse" warning also fires for ordinary logouts

Read. `sessions.py:165-172`: any refresh with a token revoked more than 10 seconds ago logs `refresh token reuse`. A browser whose session was ended by "change password" on another device, or by a reset, does exactly that on its next visit. The warning will be mostly noise once it feeds an alarm. The reason for the revocation is not stored, so the two cases cannot be told apart.

---

## 2. Where the real API differs from the mock in ways the web app will notice

Checked by Run unless marked.

| # | Topic | Mock | Real API | Effect on the web app |
|---|---|---|---|---|
| 1 | Link paths | `/verify?token=`, `/reset?token=` | The same, on `WEB_ORIGIN` | None. Matches `App.tsx` routes |
| 2 | Cookies and CSRF across `localhost:5173` → `localhost:5000` | Not involved | Works: CORS allows the origin with credentials, the preflight allows `X-CSRF-Token`, `Content-Type`, `Idempotency-Key` and PATCH/DELETE, `rf_csrf` is readable from the page (browser run). Another origin gets no CORS headers | None, as long as both sides use `localhost`. Opening the app as `127.0.0.1:5173` fails both CORS and cookies. `VITE_API_URL` must be the full `http://localhost:5000/api/v1` and `VITE_USE_MOCKS=false` (Read) |
| 3 | Two tabs refreshing together | Always succeeds | Logs the browser out | QB2-001 |
| 4 | Invalid email | 400 with `fields.email` | 500 | QB2-002. Also: `.test` and `.local` addresses are refused, so the mock's `demo@reelforge.test` cannot be signed up, and no demo account is seeded |
| 5 | Rate limits | Never | `429` on sign-up, login, resend, forgot, verify, reset | The forms show the server's message (Read). In development all browsers on one machine share the 3 sign-ups an hour |
| 6 | Reset with a dead link and a weak password | `410` first | `400` for the password first, `410` after it is fixed | The user corrects the password and is then told the link is dead |
| 7 | A11 and A12 wrong password | Not in the mock | `401 invalid_credentials` (as designed) | `client.ts:75-81` treats any 401 outside the public paths as "signed out" and clears the cached user. The Settings page (F8) would send a user who mistypes their current password to the login screen. Needs a decision before F8 (Read) |
| 8 | Disabled account | Never | `403 account_disabled` on every call; cookies are kept | `useMe` only handles 401, so the app shows "We couldn't load your account. Check your connection" with a retry button, forever (Read) |
| 9 | Logout | Always `204` | Needs a session and the CSRF header; `401` without | None; `useLogout` accepts 401 |
| 10 | `niche` | Stored as sent | Trimmed and rejoined as `A, B, C`; each topic limited to 40 characters, listed topics included | None with the current topic list |
| 11 | User object | | Same fields and types, including `instagram` with nulls when not connected | None |
| 12 | Everything outside group A | Present | `404` until B3 onwards | After login the dashboard's `GET /projects` and `GET /credits` fail. Expected at this milestone; the README's "run the web app against this API" should say so |

---

## 3. The two recorded deviations

**PyJWT instead of Flask-JWT-Extended.** Reasonable. The hand-written part is about 250 lines and gets the dangerous things right (all Run): the algorithm is pinned (`alg: none` and HS512 refused), access and refresh tokens cannot stand in for each other, required claims are enforced, and the CSRF value is inside the signed access token rather than compared cookie-to-header. That last point is stronger than the "double-submit" the technical design names: a value planted in the cookie by another subdomain does not pass. The price is that the team owns the edge cases; QB2-001 is one.

**Mailpit instead of Resend in development.** Reasonable, and better for testing: nothing leaves the machine and the inbox has an API. Production goes through the same SMTP code, which is where QB2-009 and QB2-012 matter.

---

## 4. What passed

Run unless marked.

**Sessions**
- Cookies: `rf_access` HttpOnly, Path `/`, SameSite=Lax; `rf_refresh` HttpOnly, Path `/api/v1/auth`; `rf_csrf` readable, Path `/`; all Max-Age 30 days. Token lifetimes inside: 900 s and 30 days. `Secure` is off on http locally and forced on in production (Read).
- Expired access token → `401 token_expired`, cookies kept. No cookie, garbage, wrong type, wrong algorithm → `401 not_authenticated`.
- Refresh rotates all three cookies and needs no CSRF header. An old refresh token replayed after 11 s → `401`, the whole login revoked, and the newest access token of that login stops working at once.
- Logout: `204`, cookies cleared, the access token and refresh token both dead immediately. Without the CSRF header → `403 csrf_failed`.
- Password reset: both other sessions' access tokens and refresh tokens dead immediately. Change password: this session kept (and can still refresh), the other dead.
- CSRF: missing, wrong, or another session's value → `403 csrf_failed`. GET needs none.
- What a stolen token can do (Read, consistent with the runs): an access token works for at most 15 minutes and carries its own CSRF value; logout, reset, change password, disable and delete cut it off at once while Redis is up. A stolen refresh token works until the owner's browser refreshes next (then both are thrown out), or indefinitely if the owner never returns, since each rotation gives a fresh 30 days.

**Endpoints**
- A2: `200` with session from a browser with no cookies; second use and unknown token → `410`; only the newest of three tokens works.
- A3: `204` three times, then `429` with `Retry-After` and the same number in `details.retryAfterSeconds`; `409 already_verified`; `401` without a session. Three emails arrived with working links.
- A4: email matched without regard to case or surrounding spaces; same `401` and same timing for unknown email and wrong password; sixth attempt in a minute → `429`; deleted account → `401`.
- A7/A8: `204` for known, unknown and deleted addresses; older reset link dead once a newer one is issued; weak and common passwords → `400` with the link still usable; success → `204`; second use → `410`.
- A9/A10: partial update, `onboardingCompleted: false` refused, unknown and nested unknown fields refused with camelCase names, more than three topics, repeats and control characters refused, empty `niche` clears.
- A11/A12: wrong password → `401 invalid_credentials`; delete → `204`, cookies cleared, every session dead, tokens used, login and forgot-password behave as for an unknown address.
- Sign-up validation: common password, wrong types, unknown field (`isAdmin`), non-JSON body → `400`.
- Purge: a back-dated deleted account was removed with its credit, token and session rows; an account deleted minutes ago was left. Both tasks are registered and the scheduler is running in the worker.
- Passwords: argon2id, 64 MiB, 3 passes, 4 lanes. Link tokens are stored hashed.

---

## 5. Questions

1. **What should happen when Redis is down?** Nothing in the documents says. Measured with a throwaway app pointed at a dead address: login and `GET /me` work; rate limits are not applied (7 wrong logins, no `429`); a logged-out access token keeps working (for up to 15 minutes); forgot-password takes 6.0 s and the email is dropped. The code comments show this is deliberate. Is "keep working, unprotected" the agreed choice for login limits and revocation, and should it be written into the technical design? One detail either way: if Redis fails only at the moment of a logout, that session's access token stays usable after Redis is back (`sessions.py:206-215`).
2. **A2 signing in from the link** (QB2-005): keep as written, or sign in only when the browser has no other user's session?
3. **Deleted accounts** (QB2-006): should the address be free again at once, or should sign-up say the account is being deleted? And is "undo within 7 days" (DB design, decision 7) a real feature? Delete already removes the Instagram connection, so an undo could not be complete.
4. **"After 7 days" or "within 7 days"?** API A12 and the DB design say purged after 7 days; the technical design (data retention, Privacy) says removed within 7 days. The task runs daily at 03:10 UTC on accounts deleted more than 7 days ago, so data lives for up to 8 days.
5. **Which environments exist?** `ENV` allows development, test and production. A staging server run as "development" would accept the published development signing key (anyone can then mint a session for any user id; Run locally, where it is expected) and send cookies without `Secure`.
6. **No absolute session lifetime**: each refresh gives another 30 days. Is that intended, or should a login end at some fixed age?

---

## 6. For the milestones that follow

Not defects in B2. Listed because B2 decides them.

- **Instagram callback (B8, API F2)** identifies the user from the session cookie on a top-level navigation. `rf_access` holds a 15-minute token, and `rf_refresh` is not sent outside `/api/v1/auth`, so the callback cannot refresh. A user whose token runs out while they are on Instagram's page comes back to `reason=failed`. Roughly one connect in fifteen if the round trip takes a minute. Needs a rule, for example the web app refreshing just before it leaves.
- **Purge and files (B3)**: `tasks/accounts.py:23` deletes the user row and lets the database cascade. Once assets exist, their rows will be gone before anything has deleted the files. The comment there says so; it needs to become a step that runs first.
- **Cancelling jobs on delete (B4)**: `accounts.py:108-114` sets job rows to `cancelled` with plain SQL. Real cancelling (stopping the task, project status) will have to replace it.
- **Worker logs**: the worker currently logs in Celery's own format, so nothing sensitive appears. If the JSON formatter is applied to it, Celery's task lines carry the task arguments (seen as `data.args` when a task ran in the API container), and for `mail.send` the argument is the whole email including the link. Set the task's argument display before doing that.
