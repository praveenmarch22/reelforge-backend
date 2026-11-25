# ReelForge: developer response to QA findings, B4 slice 1 (Voiceovers)

| | |
|---|---|
| Date | 2026-11-25 |
| Inputs | [QA_REPORT_B4_S1.md](QA_REPORT_B4_S1.md) (QB4-001 to QB4-013), [TEST_REPORT_B4_S1.md](TEST_REPORT_B4_S1.md) (BUG-036 to BUG-040) |
| Written by | The developer. The QA and test reports are left as they were written. |

Status values: **Fixed**, **Answered** (a document is updated), **Deferred** (with the milestone).

## Major

| Finding | Status | What was done |
|---|---|---|
| QB4-001 / BUG-036: changing the project's voice while a scene's voice was being made left that scene `ready` in the old voice | Fixed | B5 now cancels and refunds the project's active voice jobs before it marks the voices outdated. The scene ends `outdated` |

## Minor

| Finding | Status | What was done |
|---|---|---|
| QB4-002 / BUG-039: Edge swallowed the job's soft time limit and retried; the worst case overran the limit | Fixed | Edge re-raises the time limit, so the job ends `timed_out` at once. Voice jobs get their own Celery limits (6 minutes soft, 7 hard; stuck after 8), enough for three ElevenLabs tries, then three Edge tries with waits |
| QB4-003 / BUG-038: a failed voice left a `ready` project `ready` | Fixed | A voice job that fails or is cancelled works out the project's status again. A ready project stays ready while a new take is made (its old voice still plays), and goes back to draft only if the take fails |
| BUG-037: cancelling a job after an edit brought a stale voice back as `ready` | Fixed | Edits now cancel the job themselves (below), and a cancel that finds the narration or mood changed sets `outdated` |
| QB4-004: caption words fixed while a voice was being made were silently replaced | Fixed | A `words` edit is refused with `409 already_running` while the scene's voice is queued or running |
| QB4-005: editing narration or mood left a doomed voice job queued, spending quota | Fixed | C2 cancels and refunds the scene's active voice job when narration or mood change. The apply-time check stays as a safety net |
| QB4-006: D3 waited on ElevenLabs on every request while it was down | Fixed | A failed lookup is remembered for 5 minutes; the list is served without samples meanwhile |
| QB4-007: punctuation in captions differed between ElevenLabs and Edge | Fixed | When the word counts match, caption words take their text from the narration as written; only the timings come from the provider |
| QB4-008: the Scene's `voice` lacked provider, voice and estimated-timings flag | Fixed | `voice` now has `provider`, `voiceId` and `estimatedTimings` |
| QB4-009: the worker and web requests locked rows in opposite orders (possible deadlock) | Fixed | The worker now locks the job's project before the job, the same order requests use. This also covers B3's rewrite job |

## Polish

| Finding | Status | What was done |
|---|---|---|
| QB4-010 / BUG-040: a dropped first voice shows `missing`, the doc said `outdated` | Answered | The code is right (there is no older voice); the API design now says `outdated`, or `missing` if the scene never had a voice. The test's expectation should follow |
| QB4-011: the key error message left out `:` and `.` | Fixed | |
| QB4-012: out-of-date text in the technical design and API C7 | Fixed | Storage layout, settings list, time limits and "the request's `voiceId`" |
| QB4-013: compose defaulted to `edge` only | Fixed | Defaults to `elevenlabs,edge`; without a key ElevenLabs is skipped |

## Tester's other notes

| Note | Answer |
|---|---|
| Idempotency keys in Redis vs the `idempotency_keys` table | Redis is used (the 24-hour expiry is free). DB design 3.15 now says the table is unused |
| Voice ids written as literals in tests; two older tests now refused for being unknown rather than for odd characters or length | Left to the Tester's judgement |

## Product decisions (asked of the product owner on 2026-11-25)

| # | Question | Answer |
|---|---|---|
| 3 | ElevenLabs' free plan requires crediting it when audio is published | When a posted Reel used an ElevenLabs voice, the post caption gets a short "Voice: elevenlabs.io" line, added when posting (milestone B6) |
| 4 | Per-scene voice charges add up (8 scenes = 16 credits) | Generate charges 2 per started 30 s of all its voices together (a 60 s Reel costs 4). Redoing one scene costs 1 credit up to 15 s, else 2 per started 30 s. `GET /credits` lists `voiceSceneShort: 1`; the estimate (B10) uses the total |

## Developer answers to QA's other questions

| # | Question | Answer |
|---|---|---|
| 1 | Should a scene remember the voice chosen in C7? | No, for version 1. The choice applies to that take; the project's voice is the Reel's voice (one narrator), and changing it redoes every scene |
| 2 | D3 samples are ElevenLabs' even when Edge will be used; no samples without a key | Accepted. Production always has the key; the paired Edge voice is the same gender and accent |
| 5 | A failed regeneration shows `failed` although the old voice still plays | Kept: `failed` with `lastError` tells the creator the new take didn't work; the old file stays and plays until a new one succeeds |
| 6 | Should a project's voice be fixed at creation? | Kept as designed: `""` means "my default voice", resolved when the voice is made |

## Round 3 (QA recheck and Tester round 2)

| Finding | Status | What was done |
|---|---|---|
| QB4-014: a project that went to draft during or after a new take never got back to ready | Fixed | A draft counts as generated once any scene has a file, so it is worked out again after each edit and voice. A scene getting a new take counts as ready while its earlier voice was ready |
| QB4-015: G3 cancel and account deletion locked the job before the project, the opposite of the worker | Fixed | G3 locks the project, then the job; account deletion locks the creator's projects before cancelling their jobs. The worker's second lock is inside the error handling, so a deadlock victim fails the job (refunded) instead of leaving it running |
| QB4-016: estimate examples still used the per-scene voice price | Fixed | API design B10/B11 and technical design 5.4 |
| BUG-041: an ElevenLabs answer without a voice list was cached as a success for a day | Fixed | It counts as a failed lookup (5 minutes) |
| Tester's note: a scene rewrite (C6) landing while a voice is queued didn't stop it | Fixed | The rewrite cancels and refunds the scene's voice job when it changes the narration. The apply-time `scene_changed` check stays as a safety net, now reachable only by a direct change |
