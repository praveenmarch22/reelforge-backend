# ReelForge: developer response to QA findings, B4 slice 3 (Music, Generate, timeline)

| | |
|---|---|
| Date | 2026-11-25 |
| Inputs | [QA_REPORT_B4_S3.md](QA_REPORT_B4_S3.md) (QB4S3-001 to QB4S3-005), [TEST_REPORT_B4_S3.md](TEST_REPORT_B4_S3.md) (BUG-044, observations) |
| Written by | The developer. The QA and test reports are left as they were written. |

Status values: **Fixed**, **Answered** (a document is updated), **Deferred** (with the milestone).

## Major

| Finding | Status | What was done |
|---|---|---|
| QB4S3-001: a project could stay `generating` forever after a script rewrite during a run | Fixed | When a failed or cancelled script job puts `generating` back, it closes the run if no voice or visual job is left (`ready` or `needs_attention`). A Generate with nothing to do also closes a run whose jobs have all ended |

## Minor

| Finding | Status | What was done |
|---|---|---|
| QB4S3-002 / BUG-044: the saved timeline (and the list's `durationSeconds`) went stale after edits, and was empty when a Reel became ready without Generate | Fixed | Every time a project's status is worked out (any edit, any finished voice or visual), the saved timeline is rebuilt when the Reel is `ready` and cleared when it isn't. The list's length is the ready Reel's length. B5 builds the timeline it renders at render time from the same code |
| QB4S3-003: while a new take ran after a narration edit, or after it failed, the timeline played the old voice and captions | Fixed | A voice is used only if it was made from the scene's current narration and mood (its saved input hash matches); otherwise the scene's length is the estimate and it has no voice or captions yet |

## Polish

| Finding | Status | What was done |
|---|---|---|
| QB4S3-004 / Tester obs. 1: Generate for one scene cost 2, C7 cost 1; some voices get a 0-credit share | Fixed / Answered | A Generate that makes one voice charges what C7 would. Zero shares are accepted: the whole Reel's voice price is what is charged, so a short scene in a batch can cost nothing and refund nothing |
| QB4S3-005: the timeline doesn't match the renderer's types (nullable voice length, extra fields) | Deferred to B5 | The render service is updated with the timeline in B5: nullable `durationSeconds`, trims, `transition` and placeholders |
| Tester obs. 2: a Generate with nothing to do raised `timelineVersion` | Fixed | It changes nothing unless a draft turns out complete (then it becomes ready) |
| Tester obs. 3: a Generate waiting only on an upload moved `needs_attention` to `draft` | Fixed | Same change: the status is left alone |
| Tester obs. 4: a deactivated track stayed in use | Fixed | The preview and renders use the mood's pick instead |

## Questions

| # | Question | Answer |
|---|---|---|
| 1 | Should Generate for one scene cost what C7 costs? | Yes (above) |
| 2 | The PRD's 15-second scene cap isn't enforced | It guides the script writer (the prompt asks for scenes of about 6 s); the creator's own edits aren't capped, the editor warns about long scenes (F5), and the 90-second limit on the whole Reel is enforced at render (`tooLong`) |
| 3 | CC BY credit wherever the Reel goes, not only the Instagram caption | The credit lines (music, and ElevenLabs on the free plan) are shown with the finished video on the render screen and the download, ready to copy, as well as added to the Instagram caption (B5/B6) |

## Recheck

| Finding | Status | What was done |
|---|---|---|
| QB4S3-006: with its track taken out of the library, the project names a track the timeline doesn't play | Fixed | The timeline's `music` names what plays (`trackId`, `title`, `license`, or `assetId` for an upload); the editor and the B6 caption credit use it |
