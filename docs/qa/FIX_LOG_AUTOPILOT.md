# Daily autopilot: fix log

Fixes for [QA_REPORT_AUTOPILOT.md](QA_REPORT_AUTOPILOT.md) and [TEST_REPORT_AUTOPILOT.md](TEST_REPORT_AUTOPILOT.md). Design: [AUTOPILOT.md](../AUTOPILOT.md).

## Checked for real (2026-11-29)

The screen was opened against the real backend with a local test account, and "Suggest two weeks" ran on the real language model (Gemini): 14 topics per request, in the rotation's mix. A full real run (voice, visuals, render, publish) has **not** been done; the whole chain is covered with the offline stand-ins.

## QA round 1

| Finding | Fix |
|---|---|
| QAP-001 Major: the rules weren't reliably instructions, and nothing covered pictures | The script model gets the rules as rules of its own, outside the quoted topic; a rewritten script keeps them. Every AI image prompt of an autopilot Reel starts with a no-face rule and has the legend's name replaced |
| QAP-002 Major: Publish could send the uncorrected video | Publishing never confirms an outdated render. If the Reel was edited, L6 answers `409 render_outdated`, the autopilot renders it again and it waits once more |
| QAP-003 Major: a run could stay running for ever | A 4-hour limit per run; a draft with nothing to start fails with a reason; invalid post text is a refusal, not a crash; a run can be stopped (L5) |
| QAP-004: failed starts sorted to the bottom, wrong reason from L8 | A failed start gets its date, and L8 answers with its own reason |
| QAP-006: a waiting Reel published by hand stayed "waiting" | L6 marks it published |
| QAP-007: the hourly limit failed a finished Reel | A finished Reel is never failed for its post text or a publish refusal; it waits for the creator with the reason |
| QAP-008: subject and angle cut mid-word | The rules no longer share the topic's 300 characters |
| QAP-009: L1 built every topic ever | The 30 newest runs are selected in SQL; videos are linked only for waiting Reels |
| QAP-010: first-use race on the settings row | `INSERT … ON CONFLICT DO NOTHING` |
| QAP-011: "Make the next one now" used up the day | Only the scheduled run counts as the day's run |
| QAP-012: weekday in UTC | Kept, and said on the screen ("days in UTC") and in the design |
| QAP-013: lock order | The project, then the topic, everywhere |
| QAP-017 to 020, 022 | Errors for Skip and remove; the real cost (5 to 7 credits); a confirm before fully automatic; the screen refreshes once a minute when idle; "1 topic" |
| QAP-005, 014, 016, 021 | Not fixed: turning the autopilot off doesn't stop a run (use Stop); `privateUntilVerified` isn't shown on runs; the extra indexes; mock-API handlers for the screen |

## Tester round 1

| Bug | Fix |
|---|---|
| BUG-092: two starts at once made two Reels for one topic | The settings row stays locked until the project exists |
| BUG-093: an unexpected fault at the daily start used up the day | Nothing is saved before the project exists, so the next hourly check tries again. A day with a run still under way isn't used up either |
| BUG-094: removing the legend's name garbled other words | Whole words only, the full name first |
| BUG-095: the screen wasn't refreshed after a refusal | It is; a refusal that removes the Reel's card keeps its explanation on the page |
| BUG-096: "0 topics added" | Says nothing new was added |
