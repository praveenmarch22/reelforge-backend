# ReelForge: QA report for B4 slice 3 (music, Generate Reel, timeline)

| | |
|---|---|
| Date | 2026-11-25 |
| Milestone | B4 slice 3: API D4 (music library), B11 (Generate Reel), B12 (timeline), `advance_project`, `flask music-seed` |
| Build tested | `backend/` working tree on top of bb9b04e (slice 3 uncommitted), Compose stack running |
| Checked against | PRD 5 (V7, CR1–CR4), 6 (flows 3 and 5), 11 (G-5, G-12, G-13, G-14), the scene length rule in section 7; API design B10–B12, D4, C2, C3, B5, B8, G3; technical design 7 ("As built"), DB design 5 (project statuses); PIPELINE_STRATEGY timeline; `renderer/src/types.ts` and `Scene.tsx` |
| Not in scope | Rendering (B5), posting and the caption credit (B6), uploads (B7), clean-up (B8) |

## Verdict: fail

The main flow works:
- **Generate starts everything at once:** one voice job and one visual job per scene, side by side.
- **Credits:** it reserves exactly the B10 estimate, with the voice price on the total length split over the voice jobs (the shares add up). With too few credits it answers `402` and starts nothing (CR-1).
- **Music:** it picks a mood-matched track.
- **Closing the run:** the run ends `ready` with a saved timeline that uses storage keys. One failed voice gives `needs_attention` and refunds only that voice's share, and a second Generate fixes it. The run is also closed correctly when its last job is cancelled, when that job's scene is deleted, or when a scene edit or a voice change cancels it.
- **B12 and D4:** both return what the API design says.

It fails on one Major finding:

- **QB4S3-001 (Major):** if the script is regenerated (B8) while a Reel is generating, and that script job then fails or is cancelled, the project goes back to `generating` with no jobs left. Edits don't release it. Only pressing Generate again does, which the creator has no reason to do.

| Severity | Count | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 1 | QB4S3-001 |
| Minor | 2 | QB4S3-002, QB4S3-003 |
| Polish | 2 | QB4S3-004, QB4S3-005 |
| Questions | 3 | Section 3 |

**How verified.** *Run*: I ran a script inside the `api` container that drives the real app through its test client. It used an isolated database (`reelforge_qa6_test`), Redis database 9 and bucket `reelforge-qa6`, all removed afterwards. The voice, stock and image providers were the mocks, and all outbound HTTP was refused by stubs. Music rows were inserted directly, without downloads. Jobs ran through the real `jobs.run()`. To make one job "the last one", the script ran the other jobs first. *Read*: concluded from the code. No real provider or incompetech call was made. The Tester's database and tests were not touched.

---

## 1. Findings

### QB4S3-001 Major: a script regenerated during Generate can leave the project `generating` for good

| | |
|---|---|
| Requirement | API B11 ("`generating` until its last voice or visual job ends, then `ready` … or `needs_attention`"); DB design 5; PRD G-13 (progress shown on return) |
| Where | `routes/projects.regenerate_script` (B8) is allowed while `generating` and stores `previousStatus: "generating"`. `reel.advance_project` does nothing while the status is `scripting`. `Script.on_stop` puts `previousStatus` back |
| Verified | Run, both ways: the script job cancelled (G3), and the script job failing (mock `[fail]`) |

**Steps**: Generate (8 jobs), then B8 while they are queued. The voice and visual jobs finish while the project is `scripting`, so their `advance_project` calls return without closing the run. The script job then fails or is cancelled, and the project is back to `generating` with **0 active jobs**. An edit (C2) leaves it `generating`, because `after_edit` returns early for that status. Only another `POST /generate` (it answers `200` and closes the run) brings it back.

**Why it matters**: the dashboard and editor show a Reel that is generating forever. The web app will most likely disable Generate while generating, so the only way out is hidden. A script failure is common: the free model is busy, or the content is refused.

**Suggested fix**: in `Script.on_stop`, when restoring `generating`, call `advance_project` (or restore `draft`/`needs_attention` worked out from the scenes). Alternatively, refuse B8 while `generating` with `409 already_running`, as for rendering and posting. Also consider having `jobs.fail_stuck` close any `generating` project that has no active voice or visual job.

### QB4S3-002 Minor: the saved `project.timeline` goes stale, or is never saved, so summary lengths and renders can be wrong

| | |
|---|---|
| Requirement | API B11 ("then `ready` (the timeline is saved)"), B12 (`timelineVersion` goes up with every edit); ProjectSummary `durationSeconds` |
| Where | `advance_project` is the only place that writes `project.timeline`. `after_edit` bumps `timeline_version` and can set `ready`, but never rebuilds or clears the saved timeline |
| Verified | Run |

- After a successful run, editing scene 1's on-screen text left the project `ready`, while the saved timeline kept the old text ("Here is something surprising" against the live "Changed overlay"). Its `durationInFrames`, which feeds the dashboard's `durationSeconds`, also stays at the old length after narration changes.
- When a run closes because its last busy scene is deleted, `after_edit` makes the project `ready`. Nothing is saved (`timeline` stayed `null`), so `durationSeconds` is `null` for a ready Reel.
- The same happens for projects made `ready` by C7/C8 one scene at a time, and by Generate's `200` "nothing left to make" path.

**Why it matters**: B5 must not render `project.timeline` as it stands. The summary length on the dashboard is wrong or missing.

**Suggested fix**: decide what the saved timeline means. Either rebuild it (keys, `links=False`) whenever `after_edit` leaves the project `ready`, and clear it otherwise. Or drop it, and have B5 build the timeline at render time and store it with the render, as technical design 7 already implies. In both cases, work out the summary `durationSeconds` from the scenes when nothing is saved.

### QB4S3-003 Minor: during a new take after a narration change, the timeline plays the old voice and old captions

| | |
|---|---|
| Requirement | API B12 ("an outdated voice is left out, since it says the old words") |
| Where | `timeline.usable_voice` drops the voice only while `voice_status == "outdated"`. Once C7 or Generate starts a new take, the status is `queued`/`running` (later `failed` if it fails), and the old voice and its `words` come back |
| Verified | Run |

**Steps**: edit scene 1's narration (voice `outdated`), then C7. B12 now gives scene 1 the old voice `src` and the old words ("Here is something surprising about 3 morning habits…") under the new narration. If the take fails, the scene stays `failed` with the old voice playing.

**Suggested fix**: decide "usable" by content, not by status: keep the voice only when its asset's `input_hash` matches the scene's current narration, mood and voice. Or record in the job input that the take started from `outdated`, and treat the scene as outdated until it succeeds.

### QB4S3-004 Polish: the voice share split means some failed scenes refund nothing, and redoing one scene through Generate costs more than C7

**Verified**: Run.
- Four short scenes cost 2 credits, split `[1, 1, 0, 0]`. If scenes 3 and 4 fail, nothing is refunded, although B11 says "a failed scene refunds its own part".
- Generate for a single failed scene reserves 2 credits (2 per started 30 s, minimum 2). C7 for the same scene costs 1.

Both follow the agreed rule, but the second one rewards the creator for avoiding Generate. Fix: either change the wording in B11, or let Generate use C7's price (`scene_voice_credits`) when it makes a single voice. See Q1.

### QB4S3-005 Polish: the timeline differs from the renderer's types

**Verified**: Read (`renderer/src/types.ts`, `Scene.tsx`).
- `voice.durationSeconds` can be `null` (a scene without a voice), but the type says `number`.
- `visual.trimStart`/`trimEnd`, top-level `transition` and each scene's `ready` are not in the type, and the renderer ignores them.
  - The ignored trims matter: a creator's trims (C2) change nothing in the preview or the render.

The renderer is B5's, so this is a note for B5 rather than a defect in this slice. Update `types.ts`, and make `Scene.tsx` honour `trimStart`/`trimEnd` and `transition` there.

---

## 2. What passed

| Check | Verified |
|---|---|
| D4: tracks with `title`, `durationSeconds`, `moods`, `license`, `sourceUrl`, `previewUrl`; `mood` filter; unknown mood → `400` | Run |
| B11: `202`; one voice and one visual job per scene; `creditsReserved` equals the B10 estimate; balance returned; project `generating`; a mood-matched track chosen | Run |
| Voice shares add up exactly to `voice_credits(total)` | Run |
| Second Generate while busy → `409 already_running`; replay with the same `Idempotency-Key` → the same jobs | Run |
| `402`: nothing started, no jobs, scene statuses and music unchanged (all or nothing) | Run |
| A run closes `ready` with the saved timeline using storage keys (`users/…`, `library/music/…`), and the summary `durationSeconds` matches it | Run |
| One voice failing → `needs_attention`, refunded by its share; Generate again redoes only that scene and closes `ready` | Run |
| Closing under races: the last job cancelled (G3) → `needs_attention`; the last busy scene deleted (C3) → closed. A narration edit (C2) or voice change (B5) cancelling the last voice jobs → closed, as `draft` because the scenes are now `outdated`. In every case the worker locks the project before the job, so two jobs finishing together close the run once | Run |
| An upload scene without a file → `needs_attention` | Run |
| Generate on a complete Reel → `200` with no jobs; `sceneIds` not of the project → `400` | Run |
| B12: `timelineVersion`, `timeline`, `durationSeconds`, `tooLong`, `ready`; presigned links; `startFrame`s contiguous and adding up to `durationInFrames`; scenes at least 1.5 s | Run |
| `timelineVersion` goes up on a settings change and on scene edits | Run |
| Seed command: https on `incompetech.com` only, no redirects, 30 MB cap, MP3 check, one commit per track (safe to run again); moods checked against the 10 moods; license string per track | Read |

## 3. Questions for the product owner

1. **Pricing a single scene through Generate** (QB4S3-004). Should Generate for one scene cost what C7 costs (1 credit up to 15 s)?
2. **Scene length cap.** PRD section 7 says a scene lasts "at most 15 seconds". The timeline makes the scene as long as its voice (plus 0.3 s), whatever that length, and narration can run to 1 500 characters. Should the narration be limited so a scene's voice stays under 15 s, or does the 15-second rule no longer apply?
3. **Credit for the music outside Instagram.** CC BY 4.0 needs the credit wherever the Reel is shared. B6 puts it in the Instagram caption. A downloaded MP4 posted elsewhere carries no credit. Should the download screen (F7) show the credit line to copy, or should a short end card be added?

## 4. For B5 (render), B6 (posting) and F6 (editor)

- **B5:**
  - Build the timeline at render time from the scenes (QB4S3-002), with storage keys turned into links the render service can fetch. `resolveSrc` treats anything that isn't `http(s)` as a file inside `renderer/public`.
  - Store the timeline and `timelineVersion` with the render. A render is outdated when the project's `timelineVersion` is higher.
  - Refuse when `tooLong` is true.
  - Honour `trimStart`/`trimEnd` and `transition`, or leave them out of the timeline (QB4S3-005).
- **B6:**
  - The credit line is in `music_tracks.license` for the project's `music_track_id`.
  - Uploaded music (`music_asset_id`) has no license field yet.
  - Stock clips have `author`/`pageUrl` in the asset's `meta`. Pixabay doesn't require credit, but it can be offered.
- **F6:**
  - Use B12 for the Player. Key media by asset, since links change on every call.
  - Show the placeholder for empty `src`.
  - Show `tooLong`.
  - Show `needs_attention` scenes with Retry, which is Generate again.
  - While `generating`, keep Generate reachable, or at least offer it when the project has no active jobs: this is the workaround for QB4S3-001.

## 5. Seed command notes (no findings)

`flask music-seed` never deactivates a track removed from `music_library.json`. Retiring a track needs a manual `is_active = false`. If one download fails, the run stops there. Tracks already stored are kept, and running the command again continues from that point.

---

## 6. Recheck (2026-11-25, after FIX_LOG_B4_S3.md)

**Build**: working tree with the fixes (uncommitted). **Method**: as before, on fresh isolated resources: database `reelforge_qa7_test`, Redis database 8 and bucket `reelforge-qa7`, all removed afterwards. Mock providers only, with outbound HTTP refused. Music rows were inserted directly. The SQL statements of a request were counted with an engine event. 19 checks, all passed.

### Verdict: pass

QB4S3-001 to 004 are fixed, and QB4S3-005 is deferred to B5 as agreed. The answers to the three questions are recorded in the fix log. The regression checks found nothing above Polish, and one small note for B6 (QB4S3-006).

### Status of the findings

| Id | Status | How verified |
|---|---|---|
| QB4S3-001 | **Fixed.** A script rewrite during a run that is then cancelled, or that fails (mock `[fail]`), closes the run as `ready` with its timeline. If the voice and visual jobs are still active when the script job ends, the project stays `generating` and closes when they finish | Run |
| QB4S3-002 | **Fixed.** On a ready Reel, an edit rewrites the saved timeline (the new on-screen text is in it). A narration edit makes the project `draft`, clears the timeline and sets the list's `durationSeconds` to `null`. Making the Reel ready again with C7 saves it again. A run closed by deleting its last busy scene saves a timeline with the remaining scenes | Run |
| QB4S3-003 | **Fixed.** A new take after a narration edit plays no voice and no captions, and nor does a failed take. A take with the same narration keeps the old voice meanwhile. A voice made with C7's own `voiceId` is still used (the hash uses the voice in the asset's `meta`) | Run |
| QB4S3-004 | **Fixed / answered.** A Generate that makes one voice reserves 1 credit, as C7 would. Zero shares are accepted by the product owner | Run |
| QB4S3-005 | Deferred to B5 (render service and types) | — |

### Regression checks requested

| Area | Result | Verified |
|---|---|---|
| `save_timeline` inside `after_edit`: flush side effects | Reorder, duplicate, delete, and add-then-delete on a ready Reel all succeed. Scene positions use a deferred constraint, so the early flush is safe. The saved timeline lists the scenes in the new order; adding a scene makes the project `draft` and clears it, and deleting it makes the project `ready` again with a timeline | Run |
| `save_timeline` cost | One C2 edit on a ready 8-scene Reel: 23 SQL statements, the same count as the same edit on that project when it isn't ready. Building the timeline uses relationships already loaded by the status check, and makes no storage calls (`links=False`) | Run |
| Input-hash rule in `timeline.py` | See QB4S3-003. A voice with no hash (future uploads, B7) is kept; an `outdated` status always drops it | Run + Read |
| Script `on_stop` → `advance_project` | See QB4S3-001. It runs under the project lock in both the G3 path and the worker path (`jobs.lock` / `_lock`) | Run + Read |
| Empty Generate | On a ready Reel: `200`, `timelineVersion` unchanged, still `ready`. Waiting only on an upload: stays `needs_attention`. On a complete `draft`: becomes `ready` with a saved timeline | Run |

### New finding

#### QB4S3-006 Polish: a deactivated music track stays on the project while the preview uses another one

After a track is deactivated, the project's `music` still shows `{"trackId": <deactivated track>}`, which no longer appears in D4. Meanwhile the timeline plays the mood's pick (here "Calm Song"). The editor can't name the track it plays. If B6 builds the caption credit from `project.music_track_id`, it would credit the wrong track. Verified: Run. Fix: in B6 (and in the editor), take the credited track from the timeline's music, or clear or replace `music_track_id` when the chosen track is inactive.

### Counts after the recheck

| Severity | Open | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 0 | |
| Minor | 0 | |
| Polish | 2 | QB4S3-005 (deferred to B5), QB4S3-006 |
| Fixed | 4 | QB4S3-001 to QB4S3-004 |
