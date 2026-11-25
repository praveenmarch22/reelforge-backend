# ReelForge: developer response to QA findings, B3 (Projects, scripts and jobs)

| | |
|---|---|
| Date | 2026-11-25 |
| Inputs | [QA_REPORT_B3.md](QA_REPORT_B3.md) (QB3-001 to QB3-014), the Tester's report (BUG-034, BUG-035) |
| Written by | The developer. The QA report itself is left as QA wrote it. |

Status values: **Fixed**, **Answered** (a document is updated), **Deferred** (with the milestone).

## Blocker and major

| Finding | Status | What was done |
|---|---|---|
| QB3-001: two requests at the same moment could spend the same credits | Fixed | Locking the credit account now reads the row as it is after the lock, not an older copy loaded with the user, and the balance is changed in SQL (`balance - cost`). Checked by hand: 10 parallel requests with 3 credits, 3 accepted, balance 0 |
| QB3-002: a job cancelled while running still finished and saved its result | Fixed | Same cause: the job is re-read after the lock, so a cancel is seen and the result dropped |
| QB3-003: starting work already running gave a server error | Fixed | The job is added inside the savepoint, so a duplicate is cleanly refused with `409 already_running` |

## Minor

| Finding | Status | What was done |
|---|---|---|
| QB3-004: "Write script" after a failed first attempt was not a real retry | Fixed | Until a script has been written once, it runs as the first attempt: a pasted script is prepared again and the AI names the project |
| QB3-005: choosing music was always rejected | Fixed | One `{trackId}` or `{assetId}` choice, errors under `fields.music` |
| QB3-006: reordering didn't update the project's status | Fixed | Reorder counts as an edit |
| QB3-007: scene edits didn't move the project up the dashboard | Fixed | Every edit updates `updatedAt` |
| QB3-008: deleting a scene deleted its jobs | Fixed | The jobs are kept without their scene, so polling and the credit history keep working |
| QB3-009: provider trouble handling | Fixed | 30-second calls so both models fit the job's time limit; a time-out is `timed_out`; an error while saving fails the job instead of leaving it running; a non-JSON answer is retried and falls back; only a 400 naming the content filter is a refusal. Deferred to B8: usage counted per call with timings |
| QB3-010: prompt version not stored | Fixed | Stored on every language-model job. Per-call usage: deferred to B8 (admin usage page) |
| QB3-011: creator text could close the prompt's quote markers | Fixed | The markers are broken up inside creator text |
| QB3-012: guard against generated migrations | Fixed | `flask db migrate` is refused with a message pointing to hand-written migrations |

## Polish

| Finding | Status | What was done |
|---|---|---|
| QB3-013: hard-to-use error details | Fixed | Topic-or-script errors on `fields.topic` / `fields.script`; plain messages for number, list and id errors; the unused code removed |
| QB3-014: documents out of step | Fixed | Milestones (B4, B7, B8 rows), the `default` queue, and job error codes in the API design's Job section |

## What F5 needs (from QA's list)

| Item | Plan |
|---|---|
| Generate (B11) and the voice list (D3) | B4. Until then F5 stops at the estimate. Voice ids are checked against the list in B4 |
| Error codes to handle | Documented: `already_running` (also scene edits while scripting), `no_script`, `job_finished`, `provider_unavailable`, the shared 20-per-hour limit, job error codes |
| Script progress only 0, 10, done | The web app shows an indeterminate indicator while writing |
| Estimate after delete, duplicate, reorder | Duplicate and reorder now return it; after delete the app fetches B10 |
| Costs for rewrite, ideas, hooks | Added to `GET /credits` |
| Word count per scene, warnings | The app counts per scene and applies the over-target and too-short warnings |
| Moods and tones not served | The app keeps the fixed lists (they are in the API design) |
| The web app's mock | Brought up to these endpoints when F5 is built |

## Tester (423 new or changed tests, 938 in total)

| Finding | Status | What was done |
|---|---|---|
| BUG-034: deleting a scene with a generated file, or regenerating such a project, crashed | Fixed | The query that finds files still in use was built wrongly |
| BUG-035: `null` for style, character, voice or settings was silently ignored | Fixed | Refused with the field named, like the other fields; only `music` may be null |

## Questions

| # | Question | Answer |
|---|---|---|
| 1 | A job reserved before the monthly reset and refunded after it adds its credits to the new month | Accepted: the creator paid for work they didn't get, so they get it back; the amount is small |
| 2 | Write script on a pasted-script project | Before any script succeeded: the pasted text is prepared again (QB3-004). After that: a new script is written from it, which is what "regenerate" means |
| 3 | Any non-empty caption is kept on regenerate | Kept as is for version 1: the creator can regenerate the caption on the posting screen (F4, milestone B6) |
| 4 | A title typed while the first script is written was overwritten | Fixed: the creator's title wins |
| 5 | Refused requests used up the hourly limit | Fixed: only requests that start work count |
| 6 | Copying a project while its script is written | Refused with `409 already_running` |
| 7 | Hooks: 1 or 2 accepted when the model gives fewer than 3 | Accepted; the web app shows what it gets. Asking again would cost time for little gain |

## From QA's notes for B4, done now

- Account deletion cancels running work the normal way (tasks revoked, results dropped).
- A scene's voice and visual files are loaded together with the scenes, not one query per scene.
