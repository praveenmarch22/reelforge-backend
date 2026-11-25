# ReelForge: test report for B4 slice 3 (music, Generate, timeline)

| | |
|---|---|
| Date | 2026-11-25 |
| Role | Tester (automated tests only; no product code changed) |
| Build tested | `backend/` working tree, slice 3 uncommitted on top of bb9b04e, Compose stack running |
| How run | `docker compose exec -T api python -m pytest -q` in the `api` container, one full run at a time |
| Checked against | API design B11, B12, D4, Project summary (`durationSeconds`), CR-1; TECHNICAL_DESIGN 7 "As built"; README "Music library"; pipeline strategy "The timeline JSON" |

## 1. Results

| Run | Result |
|---|---|
| Before slice 3 | 1291 passed |
| Full suite with slice 3 and the new tests | **1363 tests: 1361 passed, 2 failed** (7 min 33 s) |
| New files alone | 70 passed, 2 failed (same two) |

Both failures are BUG-044. No older test fails, and no older test needed changing.

## 2. What was added

| File | Tests |
|---|---|
| `api/tests/test_generate_and_timeline.py` | 43 functions, 63 cases: B11, B12, D4, `timeline_version` |
| `api/tests/test_music_library.py` | 9 functions, 14 cases: `flask music-seed` with downloads stubbed, the starter list |

### Covered

- **B11 main flow**:
  - `202 {jobs, creditsReserved, balance}`: one voice and one visual job per scene; the voice price is `voice_credits(total estimated seconds)`, split over the voice jobs; stock is free; `balance` matches `GET /credits`.
  - The project is `generating` with an integer `progress`, and every scene shows `queued`.
  - Music is picked from the project's `musicMood` tracks, not other moods.
  - After the jobs: `ready`, every part `ready`. `project.timeline` is saved with storage keys (`users/…`, `library/music/…`), not links. The listed `durationSeconds` equals B12's.
- **Music picking**:
  - No library → no music, and B12 `music` null.
  - A chosen track is kept.
  - With no track for the mood, an active one is picked (inactive ones never), and the B12 preview shows the same track each time.
- **Plan**:
  - Ready parts are kept.
  - An outdated voice, failed parts and missing parts are planned.
  - Upload scenes get a voice job but no visual job; the run then ends `needs_attention`.
  - `sceneIds` limits the plan to those scenes.
  - Bodies refused with 400: an empty list, a malformed id, a non-list, snake_case, an unknown field.
  - `sceneIds` of another project, or unknown ids, are refused with 400 `sceneIds`.
- **Price**:
  - `_share` keeps whole credits that add up, including ties, zero weights, zero total and no jobs.
  - The total is charged on the Reel's length; each job's ledger matches its share.
  - A failing scene refunds exactly its own share. The run ends `needs_attention` with that scene `failed`. Generate again starts only that voice, at `voice_credits` of that scene; the project is then `ready`, and the ledger matches the balance.
  - 402 with `needed`/`balance` starts nothing: no job rows, statuses unchanged, no music picked. One more credit and it starts.
- **Refusal order**:
  - `rendering`/`posting` → 409, even with providers off and no credits.
  - Script being written → 409.
  - A needed provider off → 503, before 402, and nothing starts.
  - A provider not needed (voice off, only visuals left) doesn't block.
  - Everything left in progress → 409 `already_running`, before 402.
  - Nothing left → 200 `{"jobs": [], "creditsReserved": 0, "balance"}` and a complete project becomes `ready`.
  - An empty Generate closes a run left in `generating`.
  - 404 other user, 401, 403 unverified.
  - Idempotency replay and conflict.
- **During a run**:
  - An edit cancels that scene's voice; the run still closes as `needs_attention`, and Generate then retries just that voice.
  - Cancelling one job, or every job, still closes the run; cancelling every job refunds everything.
  - Deleting a scene: the rest close as `ready`, and B12 and the listed duration agree.
  - Deleting the project refunds everything and leaves no active job.
  - A one-scene Reel closes when its last job ends (the `finishing` fix).
  - Edit and Generate again on a ready Reel plans only the outdated visual and ends `ready`.
- **B12**:
  - Response shape. Placeholders on a fresh draft: empty `src`, no words, `ready: false`, the mood.
  - Start frames add up; length = estimate + 0.3 s (at least 1.5 s); motions rotate; `durationSeconds` = frames / 30.
  - With a voice: length from the voice, words, playable voice and video links.
  - A 0.4 s voice → 45 frames.
  - An outdated voice is left out (no `src`, no words, estimate length). A failed re-take keeps the old voice playing, with the scene not `ready`.
  - Trims, on-screen text, `captionStyle`, `showCaptions`, `voiceVolume`, `transition`, music volume and fades reach the timeline. Ducking off makes `duckedVolume` = `volume`; ducking on uses the setting.
  - Music source: the pick, then the chosen track, then the upload.
  - Over 90 s → `tooLong`.
  - 404 other user, 401.
- **`timeline_version`**:
  - +1 on each of: C2, B5 settings, C5, C1, C4, C3.
  - It also goes up when a voice is saved; it is unchanged by reading B12.
  - The B5 answer carries the new version.
- **D4**:
  - Sorted by title, inactive tracks hidden, exact fields, a link.
  - `mood` filter; empty `mood` means all.
  - Unknown moods (`CALM`, `happy`, a list, 200 characters) → 400 `mood`.
  - 401 signed out; an unverified creator may list.
- **Seed** (requests stubbed):
  - Downloads the URL-encoded title from the base URL, streamed, no redirects, `User-Agent: ReelForge/1.0`.
  - Stores `library/music/<slug>.mp3`. Rows get title, moods, the MP3's duration, the license with the title, the source page, and `is_active`.
  - A second run downloads nothing but updates the moods and reactivates the track. `--force` downloads again, still one row per file. A row whose file is missing is downloaded again.
  - The `flask music-seed` CLI works, with and without `--force`.
  - Refused before any download: `http`, other hosts, look-alike hosts, `user@host`, `www.incompetech.com`.
  - Nothing is stored for a 404, a 302, an MP4 or HTML (`not an MP3`), or more than the cap (30 MB, tested with a lowered cap).
  - An unknown mood stops the seed before downloading.
  - The real starter list: 15 unique titles and keys, valid moods, every mood covered, an https incompetech base URL, `{title}` in the license.

### Not covered (deliberately)

- Real incompetech downloads, and the Celery round trip on the real worker.
- Two workers finishing a project's last two jobs at the same moment. It is serialized by the project lock (read), not exercised.
- Rendering (E1), which will consume the saved timeline in B5.

## 3. Bugs

### BUG-044 (Minor): the listed `durationSeconds` is stale after edits, and null for a Reel made ready without Generate

- **Tests**: `tests/test_generate_and_timeline.py::test_the_listed_duration_follows_the_reel_after_edits` and `::test_a_reel_made_ready_scene_by_scene_has_a_listed_duration`
- **Steps (stale)**: scripted project → B11 → run the jobs (`ready`) → `DELETE /scenes/{first}` → compare `GET /projects` `items[].durationSeconds` with B12's `durationSeconds`.
- **Expected**: the same. The Project summary's `durationSeconds` is the Reel's length, "null until it exists".
- **Actual**: the list still shows the length from before the edit (for example 15.6 against 10.5).
- **Steps (null)**: C7 and C8 on every scene of a scripted project → run the jobs. The project becomes `ready` through `after_edit` → `GET /projects/{id}`.
- **Actual**: `durationSeconds: null`, though B12 gives a length (15.6).
- **Cause (reading)**: `summary_json` reads `project.timeline`, which only `advance_project` writes, at the end of a Generate run. Edits (`after_edit`) and readiness reached outside a Generate run never refresh it. The dashboard shows a wrong or missing length, and anything else that reads `projects.timeline` gets a snapshot older than `timeline_version`.

## 4. Observations (not counted as bugs)

The first is measured in the tests. The other three come from reading the code; no test checks them.

1. **Generate charges more than C7 for a short scene.** Retrying one failed 4-second scene through Generate costs `voice_credits(4) = 2`; C7 on the same scene costs 1 (`voiceSceneShort`). It follows the 2026-11-25 decision as written, but the creator pays more through the "retry" path than through the scene's own button.
2. **A Generate with nothing to do still bumps `timeline_version`.** It calls `after_edit(force=True)`, which counts as an edit. Harmless now; once renders exist (E1), a render made just before would show as outdated.
3. **A Generate that only waits on an upload sets the project to `draft`.** Its only missing part is an upload scene's file, so it answers 200 and moves a `needs_attention` project to `draft`. The upload scene shows `missing`, so the editor still points at it.
4. **A chosen track that is later deactivated is still used.** `_music` reads the chosen track by id without checking `is_active`.

---

## Round 2 (after FIX_LOG_B4_S3.md)

### Results

| Run | Result |
|---|---|
| `test_generate_and_timeline.py` alone | 73 passed |
| Full suite, one run | **1380 tests: 1380 passed, 0 failed** (6 min 49 s) |

BUG-044 is fixed: its two tests pass unchanged. No new bugs. No existing test needed changing. The 17 new cases below all pass.

### New tests (all in `api/tests/test_generate_and_timeline.py`)

| Test | What it checks |
|---|---|
| `test_a_script_rewrite_during_a_run_that_does_not_happen_still_closes_the_run` (cases: script `fails`, script `is cancelled`) | QB4S3-001, step by step: Generate → B8 while the jobs are queued (`scripting`) → run only the voice and visual jobs (still `scripting`) → the script job fails (`[fail]`) or is cancelled (G3). The project ends `ready`, and the listed length equals B12's |
| `test_a_script_rewrite_that_fails_with_jobs_left_keeps_the_run_until_they_end` | The other order: the script fails first, the project goes back to `generating` while its jobs wait, then closes `ready` when they end |
| `test_the_saved_timeline_follows_the_status` | `save_timeline`: saved after Generate. Rebuilt by an edit that keeps the Reel ready (the saved copy has the new on-screen text). Cleared when an edit makes it `draft`; the listed `durationSeconds` is then null. Saved again when the new visual makes it `ready`, with the listed length equal to B12's |
| `test_after_a_narration_edit_the_old_voice_is_not_played_while_a_take_is_queued` / `..._while_a_take_runs` / `test_after_a_narration_edit_and_a_failed_take_the_old_voice_is_not_played` | QB4S3-003: after a narration edit and a new C7, B12 leaves the old voice out while the take is queued, while it runs (read during the provider call) and after it fails: no `src`, no words, estimate length. The new voice plays once ready |
| `test_after_a_mood_change_the_old_voice_is_not_played` | The same for a mood change |
| `test_a_new_take_of_an_unchanged_scene_keeps_playing_the_old_voice` | The hash check doesn't over-trigger: a re-take with nothing changed keeps the old voice and words while it runs |
| `test_a_generate_that_makes_one_voice_costs_what_c7_would` (10, 37, 38, 80 words → 1, 1, 2, 4) | QB4S3-004: one voice in a Generate is charged `scene_voice_credits` |
| `test_an_empty_generate_on_a_ready_reel_changes_nothing` | Status, `timelineVersion` and `updatedAt` unchanged (Tester obs. 2) |
| `test_an_empty_generate_leaves_needs_attention_alone_when_only_an_upload_is_missing` | Status stays `needs_attention` and `timelineVersion` is unchanged (Tester obs. 3) |
| `test_an_empty_generate_turns_a_complete_draft_ready_with_its_timeline` | A complete `draft` with no saved timeline becomes `ready` with a listed length |
| `test_a_deactivated_track_is_replaced_by_the_moods_pick` | A chosen track taken out of the library is replaced in B12 by the mood's pick (Tester obs. 4) |

All four observations from the first report are answered by the fixes and covered above.
