# ReelForge: developer response to QA and Tester findings, B2 (Accounts)

| | |
|---|---|
| Date | 2026-11-24 |
| Inputs | [QA_REPORT_B2.md](QA_REPORT_B2.md) (QB2-001 to QB2-013), the Tester's report (BUG-021 to BUG-027) |
| Written by | The developer. The QA report itself is left as QA wrote it. |

Status values: **Fixed**, **Answered** (a document is updated), **Deferred** (with the milestone), **Open** (needs the product owner).

## Major

| Finding | Status | What was done |
|---|---|---|
| QB2-001 / BUG-022: two tabs refreshing at the same moment logged the browser out | Fixed | The request that loses the race is refused without clearing cookies, so the winner's new cookies stay |
| QB2-002 / BUG-021: an invalid email on sign-up or forgot-password gave a server error | Fixed | It is a normal validation error with "Enter a valid email address" |

## Minor

| Finding | Status | What was done |
|---|---|---|
| QB2-003 / BUG-023 / BUG-024: a NUL character or half an emoji gave a server error | Fixed | Refused as a validation error in every text field, new passwords and the login email |
| QB2-004: forgot-password answered faster for unknown emails | Fixed | The lookup and the sending moved to a background task; the request does the same work either way |
| QB2-005: the verification link signs in whoever opens it and replaces the current session | **Open** | The code follows API A2 as written. Decision needed: keep, or sign in only when nobody else is signed in |
| QB2-006: a deleted account's email is blocked for 7 days with a misleading message | **Open** | Decision needed: free the email at once, or keep the 7 days (and whether "undo delete" is a feature) |
| QB2-007: database errors logged query values | Fixed | Query values are hidden in errors and logs |
| QB2-008: proxy and cookie-domain settings are not checked in production | Deferred to I2 | Their right values depend on the AWS layout, so the check is added when that is decided |
| QB2-009: the mail connection did not verify the server's certificate | Fixed | It is verified, and encryption is required in production |
| BUG-025: snake_case field names were accepted | Fixed | camelCase only |
| BUG-026: `onboardingCompleted` accepted 1 and 1.0 | Fixed | Only the JSON value `true` |
| BUG-027: refresh tokens were cleaned up after 7 days instead of 30 | Fixed | 30 days, as the database design says |

## Polish

| Finding | Status | What was done |
|---|---|---|
| QB2-010: undocumented 429 and `account_disabled` answers; resend and forgot-password shared a counter | Fixed and answered | Separate counters. The API design now lists these answers and the extra limits |
| QB2-011: an unknown field named `email` got the wrong message | Fixed | |
| QB2-012: emails had no `Date` header | Fixed | `Date` and `Message-ID` are set |
| QB2-013: the "token reuse" warning fired for ordinary ended sessions | Fixed | Only when the exchanged token's replacement is still in use |

## Round 2 (found by the Tester while checking the fixes above)

| Finding | Status | What was done |
|---|---|---|
| BUG-028: a refused production configuration printed part of the mail password in the start-up error | Fixed | Start-up errors no longer print the values they were given |
| BUG-029: a web address written as a pattern (`(app\|www)`, `?`) let look-alike sites call the API with cookies | Fixed | The setting must be one exact address: scheme, host, optional port |
| BUG-030: `Name <a@b.com>` was accepted as an email at sign-up but could not log in | Fixed | Only a plain address is accepted |
| BUG-031: the API design promised `415` for a non-JSON body, the code answered `400` | Fixed | A body in another format is `415 unsupported_media_type`; no body, or JSON that isn't an object, is `400` |
| BUG-032 (round 3): a web address ending in a line break was accepted and then nothing worked | Fixed | Refused at start-up like any other inexact address |
| Running the migrations in the same process switched the app's logging off | Fixed | The migration setup leaves logging alone when the app has already configured it |

## Differences from the mock that the web app will notice

These are frontend changes, to be made when the web app is connected to this API (milestone I1).

| Difference | Plan |
|---|---|
| A wrong current password (A11, A12) is a 401, which the web app's client treats as signed out | The client must only treat `not_authenticated` as signed out. API A11 now says so |
| `403 account_disabled` on `/me` leaves the app on a retry screen | Add a "this account is disabled" screen |
| The demo login does not exist on the real API, and `.test` addresses are refused | A seed command for a demo account with an `example.com` address |
| With a dead reset link and a weak password, the password error comes first | Accepted |
| Everything outside group A returns 404 until B3 | Expected |

## Questions

| # | Question | Answer |
|---|---|---|
| 1 | What should happen when Redis is down? | Requests are let through without rate limits, and a logged-out access token keeps working for at most 15 minutes. Chosen so that a Redis fault does not stop every login; the health check reports it. Revisit at I2 with real monitoring |
| 2 | Should the verification link keep signing in? | **Open** (QB2-005) |
| 3 | Should a deleted account's email be free at once? | **Open** (QB2-006) |
| 4 | Purged "after" or "within" 7 days? | After: the daily task removes accounts deleted more than 7 days ago, so up to 8 days. The technical design is to be corrected with the next edit of that section |
| 5 | Is there a staging environment? | Not yet; decided at I2 (same as B1 question 4) |
| 6 | Should a login have an absolute lifetime? | Not in version 1: an active user stays signed in. Noted as a hardening option |

## For later milestones (from QA)

| Item | Milestone |
|---|---|
| The Instagram callback cannot refresh an expired access token, because the refresh cookie is limited to `/api/v1/auth` | B8: the callback will accept the refresh cookie's path or use its own short-lived state |
| The purge will leave files behind once assets exist | B3: delete stored files before the rows |
