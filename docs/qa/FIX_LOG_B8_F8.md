# B8 (admin, clean-up) and F8 (settings, credits, admin console): fix log

Fixes for [QA_REPORT_B8_F8.md](QA_REPORT_B8_F8.md). Built overnight on 2026-11-28 with the product owner away; the decisions below are for review.

## Decisions made without the product owner

- **Scope of "complete":** B8, F8 and I1; B7 (AI video, uploads, Whisper), Instagram (B6b) and deployment come after (chosen by the product owner).
- **I6 retry covers a scene's voice or visual only.** Ideas, hooks and post text are one click for the creator and their result would go nowhere; a render, a publish or a script is restarted by the creator.
- **The audit record is a structured log line** (`admin change`, with the admin's id), not a table.
- **Disabling a user** signs them out but lets running jobs finish.

## QA round 1

| Finding | Fix |
|---|---|
| QB8-001 Blocker: the purge deletes files a duplicated Reel still uses | Before a purge, files that a surviving project's scenes, music or cover still use are handed to that project (`rehome_shared`), so neither the row nor the file goes |
| QB8-002 Major: a failed storage delete still deletes the rows | Rows are deleted only for files that were deleted. An account or project with a file that couldn't be deleted waits for the next run |
| QB8-003 Major: the YouTube switch does nothing | F5 answers `503 provider_unavailable` while YouTube is switched off |
| QB8-004 Major: the error rate is always 0% | Failures are recorded against each provider that failed, and a failed job carries the last provider tried, so the provider filter finds it |
| QB8-005 Major: retry skips the creator's guards | Retry needs that part of the scene to be still failed; it refuses hand-fixed caption words, an edited narration, a busy Reel and a job already retried, and marks the old job as retried |
| QB8-006: retry for disabled or deleted accounts | Refused |
| QB8-007: search strips `_` and `%` | They are matched literally (`autoescape`) |
| QB8-008: an old render loses the ElevenLabs credit after clean-up | Not fixed: it needs the credit lines stored with the render. Noted for later |
| QB8-009: uploaded music isn't checked | Activation refuses an empty file or one over 30 MB. The length is still the browser's reading (admin-only feature) |
| QB8-010: tester has no effect | Switching tester on or off sets the monthly allowance to the tester or normal default |
| QB8-011: thin tests | The Tester's B8 suite |
| QB8-012: audit detail | `update_music` logs what changed |
| QF8-001: no provider filter | Added |
| QF8-002: tabs ignore arrow keys | Settings and Admin tab lists follow the WAI-ARIA pattern (one Tab stop; arrows, Home, End) |
| QF8-003: delete account | The confirm explains the 7-day purge; the password box is no longer filled by password managers |
| QF8-004: out-of-credits dialog | Title fits when some credits remain; suggests switching to free stock clips (PRD flow 7) |
| QF8-005: M4A and size not checked | MP3, WAV and OGG only, checked with the size before sending |
| QF8-006: file box not cleared | Cleared after a successful add. A failed upload still leaves a switched-off track to delete by hand |
| QF8-007: retry gives no feedback | The row shows "Retried" and the button goes |
| QF8-009: channel picture | Shown |
| QF8-011: minus sign | Either minus works; an invalid amount says so; the note is said to be visible to the user |
| QF8-008, QF8-010, QF8-012 | Tests come from the Tester; native confirm and prompt dialogs and the phone layout of the admin tables are left for F9 |

## Tester round 1

| Bug | Fix |
|---|---|
| BUG-086: a visual's small still is never deleted | The clean-up and the purge delete an asset's still (`meta.thumbnailKey`) with it |
| BUG-087: a queued publish still uploads after YouTube is switched off | The job reads the switch when it runs and fails with `provider_unavailable`; a video already uploaded is still checked to its end |
| BUG-088: stats count deleted accounts' Reels and credits | Left out |
| BUG-089: a bad `to` date is reported under `from` | Each date is reported under its own name |
| BUG-090: "Nothing yet." under a history that failed to load | Shown only when the history loaded and is empty |
| BUG-091: API design behind the fixes | Updated |

After the fixes: 1,917 backend tests and 890 frontend unit tests pass.
