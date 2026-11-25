# ReelForge: test report for B4 slice 1 (voiceovers)

| | |
|---|---|
| Date | 2026-11-25 |
| Role | Tester (automated tests only; no product code changed) |
| Build tested | `backend/` working tree (slice 1 uncommitted on top of HEAD), Compose stack running |
| How run | `docker compose exec -T api python -m pytest -q` inside the `api` container (test database `reelforge_test`, Redis db 15, bucket `reelforge-test`) |
| Checked against | API design C7, D3, Scene and Job objects, Idempotency row, error table (`words_edited`), B5, C2, C3, C4; technical design "Voice (ElevenLabs, fallback Edge TTS)" and 7; DB design 5; PRD V1-V3, CA-4, G-12, G-15 |

## 1. Results

| Run | Result |
|---|---|
| Before this slice | 938 passed |
| Full suite with the new tests | **1059 tests: 1052 passed, 7 failed**, the same in two clean full runs (6 min 28 s, then 4 min 42 s after the flakiness fix). All 7 failures are the bugs below; none is in an older test |
| New files, run on their own, repeated | same results on every run (2 earlier runs of the first 116 tests, 1 more after the last additions) |

The 7 failing tests:

| Test | Bug |
|---|---|
| `tests/test_voiceovers.py::test_changing_the_project_voice_mid_job_leaves_the_scene_outdated` | BUG-036 |
| `tests/test_voiceovers.py::test_cancelling_after_an_edit_does_not_bring_back_a_stale_voice_as_ready[narration]` | BUG-037 |
| `tests/test_voiceovers.py::test_cancelling_after_an_edit_does_not_bring_back_a_stale_voice_as_ready[mood]` | BUG-037 |
| `tests/test_voiceovers.py::test_cancelling_after_an_edit_does_not_bring_back_a_stale_voice_as_ready[project voice]` | BUG-037 |
| `tests/test_voiceovers.py::test_a_ready_project_goes_back_to_draft_when_a_voice_fails` | BUG-038 |
| `tests/test_voice_providers.py::test_the_tasks_soft_time_limit_stops_an_edge_voice_as_timed_out` | BUG-039 |
| `tests/test_voiceovers.py::test_a_scene_with_no_earlier_voice_shows_outdated_after_scene_changed` | BUG-040 |

## 2. What was added

| File | What |
|---|---|
| `api/tests/voice_support.py` | Helpers (C7 call with optional `Idempotency-Key`, scene lookup, storage listing, presigned-link download, a valid in-memory MP3 for stubs) and the autouse guard `no_real_voice_services`: any `requests` call, `requests.Session` call or `edge_tts.Communicate` fails the test unless the test installs its own stub. A test checks the guard itself |
| `api/tests/test_voiceovers.py` (44 test functions, 66 cases) | C7 and the voice job with the mock voice |
| `api/tests/test_voice_providers.py` (27 test functions, 55 cases) | ElevenLabs and Edge with HTTP / edge-tts stubbed, the helper functions, D3, settings |
| `api/tests/projects_support.py` | `unsigned(scene)`: a scene with its file links cut to the address (see section 5) |
| `api/tests/test_scenes_and_scripts.py`, `api/tests/test_projects_manage.py` | Two existing duplicate tests compare scenes through `unsigned()` (flakiness fix, section 5) |

### Covered

- **C7 main flow**: `202 {"job"}` with the Job shape; scene `queued` → `running` (read while the provider works) → `ready`; `voice` with `assetId`, `url`, `durationSeconds`; the link downloads the stored audio; `words` match the narration, in order, inside the audio; asset row (kind, provider, content type, key `users/{u}/projects/{p}/voice/{job}.wav`, size, `input_hash` = `voice_input_hash`, `meta.voiceId`, `meta.estimatedTimings`); job output `{"voice": {...}, "estimatedTimings"}`; ledger note "Voiceover"; a second take replaces the asset and marks the old one replaced.
- **Credits**: 2 per started 30 s at 2.5 words/s (1, 74, 75, 76, 150, 151 words → 2, 2, 2, 4, 4, 6); refund on failure, on cancel while queued and while running; ledger stays the sum of the balance.
- **Failure and cancel**: failed job → scene `failed` with `lastError` = job message, no asset, no file; fixing the text clears it. Cancel while queued restores `missing` or `ready`; cancel while the provider works drops the result and deletes the stored file. Stuck voice job (7 min running) → `timed_out`, refunded, scene `failed`.
- **CA-4**: `409 words_edited` without `confirmReplaceWords: true` (also with `false`, with only `voiceId`), nothing charged, the fixed words kept; with confirmation the new words replace them and `wordsEdited` is false.
- **Voice choice**: request `voiceId` > project > account default > first catalogue voice; the project keeps its own voice after a scene-only choice; stored ids no longer in the catalogue fall back to the next choice, then to the default; every catalogue voice accepted.
- **Refusals**: invalid `voiceId` (made-up, wrong case, 101 chars, number), non-boolean `confirmReplaceWords`, snake_case and unknown fields → 400, nothing charged; unverified → 403 `email_not_verified`; signed out → 401; other user's, unknown, malformed and deleted-project scene → 404; while scripting → 409 `already_running`; second click → 409 `already_running` (other scenes not blocked, free again afterwards); 402 with `needed`/`balance`, no job, status unchanged; 503 with the mock switched off by the admin, and with `VOICE_PROVIDERS=elevenlabs` and no key; documented order 503 → 409 → 402; provider switched off after the click → job `provider_unavailable`, refunded.
- **Idempotency-Key**: same key and body → same answer, one job, one charge, also after the job finished; same key, other body → 409 `idempotency_conflict`; a refused request (402) can be retried with its key; invalid keys (empty, space, 101 chars, non-ASCII, `/`, `;`) → 400 naming `Idempotency-Key`; valid keys (1 and 100 chars, `-_:.`, UUID); keys are per creator; the same key sent while the first request is still being handled → 409 `already_running`.
- **Edits while the voice is made**: narration or mood edit → `scene_changed`, refunded, file deleted, earlier voice kept as `outdated`; on-screen text and prompt edits let it finish; deleting the scene → cancelled, `sceneId: null`, file deleted; deleting the project → cancelled, file deleted; deleting a scene with a queued voice; script regeneration cancels the voice of a replaced scene.
- **Regressions**: a duplicated scene shares the voice file and link; replacing the original's voice keeps the shared file, replacing the copy's releases it; duplicating a scene whose voice is queued gives the copy `missing`; a new take on a ready project keeps it ready.
- **ElevenLabs (stubbed)**: URL with the voice id, `xi-api-key` header, `output_format`, timeout, body with `model_id` and mood voice settings (excited, calm, sad); v3 model gets the emotion tag (not for moods without one) and the tag never reaches the captions; duration read from the MP3; asset meta (`model`, `characters`); missing or impossible character timings → estimated timings.
- **Fallback to Edge (stubbed)**: `401 quota_exceeded` → one ElevenLabs call, then the paired Edge voice with the mood's rate and pitch, word timings from WordBoundary, `provider: edge`; 500, 429, connection error, broken base64, non-MP3 audio, HTML answer → 3 tries then Edge; no key → Edge without any HTTP call; admin switch-off of ElevenLabs → Edge only; both failing → `provider_failed`, refunded, no key in the job; Edge without audio fails; a safety refusal → `content_refused`, Edge not tried; Edge without boundaries → estimated timings; mock `[no-timings]` → `estimatedTimings: true` on job and asset.
- **Helpers**: `words_from_alignment` (spaces, line breaks, tags dropped), `estimate_words` (fills the audio, longer words longer, empty text), `mp3_duration` on unreadable audio → retryable `ProviderError`.
- **D3**: 8 items in catalogue order, default first, exact fields, `provider: elevenlabs`, `previewUrl ""` without a key and no network call; needs a login (401 `not_authenticated`) but not a verified email; every listed id accepted by A10 defaults and B3; with a key (stubbed) previews only for catalogue voices and https links, fetched once, cached in Redis for 24 h with the key header; ElevenLabs failing in five ways → 200 with empty previews.
- **Settings**: unknown or empty `VOICE_PROVIDERS` refused in every environment; mock refused in production (also mixed and with spaces); real providers accepted in production in any order; mock allowed in development and test; defaults `elevenlabs,edge` and `eleven_multilingual_v2`; the ElevenLabs key not in `repr`/`str`.

### Not covered (deliberately)

- Real ElevenLabs and Edge calls (forbidden here: real keys with a small free quota). Real Edge WordBoundary text (with or without punctuation) and real ElevenLabs quota answers are therefore assumed, not observed.
- The Celery round trip of a voice job on the real worker (would use the development worker and possibly real keys). The `voice` queue is checked in the existing queue test and in `docker-compose.yml` by reading.
- Redis being unreachable during C7 (the idempotency layer then raises); not specified.
- Caption words fixed while a voice is being made (they are replaced without the CA-4 warning). The expected behaviour is not specified; listed as a question for QA/PM, not as a bug.
- B11 Generate, B12 timeline and slice 3 hash reuse, B7 Whisper (G-15 transcription), clean-up of replaced files (B8).

## 3. Bugs

### BUG-036 (Major): changing the project's voice while a scene's voice is being made leaves that scene `ready` with the old voice

- **Test**: `tests/test_voiceovers.py::test_changing_the_project_voice_mid_job_leaves_the_scene_outdated`
- **Steps**: 1. Scripted project, voice not chosen in the C7 body. 2. `POST /scenes/{id}/voice/regenerate`. 3. While the job's provider call is running (or while it is queued), `PATCH /projects/{id}` with another catalogue `voiceId`. 4. Let the job finish. 5. `GET /projects/{id}`.
- **Expected**: B5 "Changing `voiceId` marks all voices `outdated`": the scene is `outdated` (or the job is dropped as `scene_changed`).
- **Actual**: scene `voiceStatus: "ready"`, asset `meta.voiceId` is the old voice, project `voiceId` is the new one.
- **Cause (reading)**: `mark_voice_outdated` ignores `queued`/`running`, and `VoiceJob.apply` compares only narration and mood with the job input, not the voice. Slice 3's Generate skips `ready` scenes, so the Reel would mix two voices. Same as QA's QB4-001.

### BUG-037 (Minor): cancelling a voice after editing the scene brings a stale voice back as `ready`

- **Test**: `tests/test_voiceovers.py::test_cancelling_after_an_edit_does_not_bring_back_a_stale_voice_as_ready` (3 cases: narration, mood, project voice)
- **Steps**: 1. Scene with a ready voice. 2. C7 → job queued (scene `queued`). 3. `PATCH /scenes/{id}` with new narration (or mood), or `PATCH /projects/{id}` with a new `voiceId`; answer 200. 4. `POST /jobs/{job}/cancel`. 5. `GET /projects/{id}`.
- **Expected**: `outdated`: the kept voice was made for the old narration / mood / voice (C2, B5, DB design 5).
- **Actual**: `ready`.
- **Cause (reading)**: `VoiceJob.on_stop` restores `input.previousStatus` ("ready") on cancel without checking whether narration, mood or voice changed since the job started; the edit itself could not mark it because the status was `queued`.

### BUG-038 (Minor): a ready project stays `ready` after one of its voices fails

- **Test**: `tests/test_voiceovers.py::test_a_ready_project_goes_back_to_draft_when_a_voice_fails`
- **Steps**: 1. Project with status `ready`, every scene voice and visual `ready`. 2. C7 on one scene. 3. The job fails (here: the admin switches the voice provider off before it runs → `provider_unavailable`). 4. `GET /projects/{id}`.
- **Expected**: project `draft` (DB design 5: ready only while every scene's voice and visual is ready; "edit ... back to ready, or draft if something is now missing").
- **Actual**: scene `failed`, project still `ready`.
- **Cause (reading)**: `start_voice_job` and `VoiceJob.on_stop` change `voice_status` without calling `after_edit(project)`; only `apply` (success) does. Same as QA's QB4-003.

### BUG-039 (Minor): Celery's soft time limit inside the Edge provider is retried as a network error instead of failing the job as `timed_out`

- **Test**: `tests/test_voice_providers.py::test_the_tasks_soft_time_limit_stops_an_edge_voice_as_timed_out`
- **Steps**: voice providers `elevenlabs,edge`, no ElevenLabs key; edge-tts stub raises `celery.exceptions.SoftTimeLimitExceeded` from its stream; C7; run the job.
- **Expected**: job `failed` with `timed_out` after one call (technical design 7 rule 3; `jobs.run` handles `SoftTimeLimitExceeded` as `timed_out`).
- **Actual**: Edge is called 3 times, the job fails as `provider_failed`. In the real worker the retries continue after the soft limit until the hard limit (300 s) kills the task, leaving the job `running` until the stuck-job sweep.
- **Cause (reading)**: `EdgeVoice.synthesize` has `except Exception` that turns everything, including `SoftTimeLimitExceeded`, into a retryable `ProviderError`. Same as QA's QB4-002.

### BUG-040 (Polish / docs): a dropped first voice leaves the scene `missing`, the API design says `outdated`

- **Test**: `tests/test_voiceovers.py::test_a_scene_with_no_earlier_voice_shows_outdated_after_scene_changed`
- **Steps**: scene with no voice yet; C7; edit the narration while the voice is being made; let the job finish.
- **Expected (as written)**: API design, Job object: `scene_changed` "the voice then shows as `outdated`"; C7: "the scene shows `outdated`".
- **Actual**: `missing` (with an earlier voice it is `outdated`, which is tested and passes).
- **Reading**: the code's `missing` matches DB design 5 better (there is no voice to be out of date). Suggest fixing the API design text ("`outdated`, or `missing` if the scene had no voice yet") and then changing this test's expectation. Same as QA's QB4-010.

## 4. Review of the developer's test edits

The replacements of made-up voice ids in `test_accounts_me.py`, `test_projects_create.py` and `test_projects_manage.py` are correct and needed: ids are now checked against the catalogue, and each replacement is a real catalogue id. Two remarks, no disagreement:

- The ids are written out as literals; importing them (`voices.CATALOGUE[n].id`) would follow the "import shared values" rule and survive a catalogue change.
- Two older tests now pass for a different reason than they were written for: `test_accounts_unusual_text.py` (`"voice-{odd}"`) and `test_projects_manage.py` (`"v" * 101`) are refused because the id is unknown, not because of the unusual character or the length. Still valid as "refused" tests, but they no longer exercise those checks. If the unusual-text check matters for `voiceId`, base it on a catalogue id (for example `DEFAULT_VOICE.id + odd`).

## 5. Flaky tests found and fixed (test code only)

Adding the presigned `url` to the Scene object made two existing tests flaky: they compare a scene read at one moment with the same scene read later, and a presigned link carries the second it was signed in (`X-Amz-Date`), so the links differ when a second boundary passes between the two reads. Measured: 2 failures in 15 runs of the pair.

- `tests/test_scenes_and_scripts.py::test_duplicating_a_scene_places_the_copy_right_after_it`
- `tests/test_projects_manage.py::test_duplicating_copies_script_and_settings_and_reuses_files_for_free`

Fix: both compare through `projects_support.unsigned()`, which keeps the link's address (bucket and key, so still "the same file") and drops the signature. After the fix: 12 runs of both, plus the new cancel test that uses the same helper, all passed. Nothing else was skipped or marked as expected to fail.

## 6. Questions (not counted as bugs)

1. Caption words fixed by hand while a voice is already being made are replaced by that voice without the CA-4 warning (the confirmation was checked before the fix existed). Should the job keep them, fail as `scene_changed`, or is this acceptable?
2. D3 asks ElevenLabs again on every request while ElevenLabs is failing (only successes are cached), each call waiting up to 10 s. The API design says "asked for once a day"; a short negative cache would match it better (QA's QB4-006).
3. DB design 3.15 keeps idempotency keys in the `idempotency_keys` table per user; the code keeps them in Redis per user, method and path. Behaviour matches the API design; the DB design (and the unused table) should say which is meant.

---

## Round 2 (after the fixes in FIX_LOG_B4_S1.md)

### Results

| Run | Result |
|---|---|
| Full suite, run 1 (one run at a time) | **1082 tests: 1080 passed, 2 failed** (5 min 36 s) |
| Full suite, run 2 | identical: 1080 passed, 2 failed (5 min 00 s) |

Both failures are the new BUG-041 (two cases of one test). BUG-036 to BUG-039 are fixed and their tests were updated or now pass; BUG-040 follows the answered documentation.

### Tests changed because behaviour changed on purpose

| Change in the fix log | Test change |
|---|---|
| 1. Pricing | `test_a_voice_costs_two_credits...` replaced by `test_a_scene_voice_costs_1_credit_up_to_15_seconds_else_2_per_started_30` (1, 37, 38, 74, 75, 76, 150, 151 words → 1, 1, 2, 2, 2, 4, 4, 6). `VOICE_COST` is now `COSTS["voiceSceneShort"]` (every other test scene is 15 s or less). `test_projects_manage.py::test_the_estimate_counts_what_is_missing_or_outdated` computes the voice item with `voice_credits(total seconds)` and checks it is 4 (per scene it would have been 8) |
| 2. C2 cancels the voice | The `scene_changed` mid-job tests became `test_editing_narration_or_mood_while_queued_cancels_and_refunds_the_voice_at_once` (4 cases) and `test_editing_narration_while_the_voice_is_being_made_cancels_it_and_deletes_its_file` (2 cases): job `cancelled`, refunded at the edit, `outdated` or `missing`, file deleted, a new take works straight away |
| 3. B5 cancels voice jobs (BUG-036) | `test_changing_the_project_voice_mid_job_cancels_the_voice_and_refunds_it` (queued/running × with/without an earlier voice); the next take uses the new voice |
| 3. BUG-037 | `test_a_stale_voice_never_comes_back_as_ready_after_an_edit` (narration, mood, project voice): `outdated` at the edit, later cancel → `409 job_finished` |
| 4. BUG-040 answered | `test_a_first_voice_dropped_as_scene_changed_leaves_the_scene_missing` expects `missing`; it now reaches the apply-time safety net through a C6 rewrite that lands first |
| 5. Voice limits | `test_a_stuck_voice_job_fails_and_is_refunded_after_8_minutes`: 7.5 min → not stuck, 8.5 min → `timed_out` |
| 6. Project status (BUG-038) | `test_a_ready_project_stays_ready_during_a_new_take_and_after_unless_it_fails` (succeeded, failed, cancelled): ready while queued; `draft` only after a failure |

### New tests

- **Scene `voice` object**: `provider`, `voiceId`, `estimatedTimings` (main flow and `[no-timings]`).
- **GET /credits** lists `voiceSceneShort: 1` and equals the `COSTS` table.
- **Celery limits**: with `job_dispatch=celery` and `apply_async` replaced (nothing reaches the worker), a voice job goes to queue `voice` with `soft_time_limit=360`, `time_limit=420`.
- **C2 words while the voice is queued or running** → `409 already_running`, the new voice's words kept, allowed again afterwards.
- **Not over-triggering**: the same narration and mood sent again, the same project voice chosen again, on-screen text and prompt edits all leave the voice job alone; B5 cancels only voice jobs (a rewrite job stays queued and succeeds).
- **Safety net**: a C6 rewrite landing before a queued voice runs gives `scene_changed`, refunded, file deleted, `missing` (no earlier voice) or `outdated` with the earlier voice kept.
- **Captions with Edge** (stubbed): narration punctuation kept when the word counts match, the provider's words kept when they differ; `match_narration` as a unit.
- **D3**: a failed lookup is remembered for at most 300 s (one ElevenLabs call over two lists, in five failure kinds); after that the samples are asked for again and a success is kept for a day.
- BUG-039's test now passes unchanged (Edge re-raises the soft time limit → `timed_out`, one call).

### New bug

#### BUG-041 (Polish): an ElevenLabs answer without a voice list is kept as "no samples" for a day

- **Test**: `tests/test_voice_providers.py::test_an_answer_without_a_voice_list_counts_as_a_failed_lookup` (`[body0]` `{"voices": "nope"}`, `[body1]` `{"detail": "unexpected"}`)
- **Steps**: app with an ElevenLabs key (HTTP stubbed); `GET https://api.elevenlabs.io/v1/voices` answers 200 with a body that has no list under `voices`; `GET /api/v1/voices`; read the TTL of Redis key `voices:previews`.
- **Expected**: treated like the other failed lookups (QB4-006 fix): `previewUrl` empty, remembered for at most 300 s, asked again afterwards.
- **Actual**: `previewUrl` empty, but `{}` is cached as a successful lookup for 86 400 s, so the voice picker has no samples for a day after one odd answer.
- **Cause (reading)**: `voices._previews` iterates `data.get("voices", [])` without checking it is a list (a string iterates as characters, a missing key gives `[]`), so no exception is raised and the empty result goes down the success path with `PREVIEWS_SECONDS`.

### Observations (not counted as bugs)

- The QB4-005 fix covers C2 only. A C6 rewrite that lands while the scene's voice is queued does not cancel it: the voice still runs (and spends provider quota) before the apply-time check drops it as `scene_changed`, refunded. Behaviour is correct for the creator; only the provider call is wasted. The fix log describes the C2 path only.
- The `idempotency_keys` table answer and the literal voice ids in older tests are left as they are (fix log: Tester's judgement). I kept the literals: they are correct catalogue ids, and changing them would not change what those tests check.
- The deadlock fix (QB4-009, lock order) is not tested directly. The existing concurrency tests (cancel, edit and delete from another thread while the worker is busy) all pass with the new order.

---

## Round 3 (after the "Round 3" fixes in FIX_LOG_B4_S1.md)

### Results

| Run | Result |
|---|---|
| New voice files alone | 152 passed |
| Full suite, one run | **1090 tests: 1090 passed, 0 failed** (5 min 31 s) |

BUG-041 is fixed: `test_an_answer_without_a_voice_list_counts_as_a_failed_lookup` passes without changes. No new bugs.

### Tests changed because behaviour changed on purpose

The two rewrite tests from round 2 (`test_a_first_voice_dropped_as_scene_changed_leaves_the_scene_missing`, `test_a_rewrite_landing_on_a_scene_with_a_voice_leaves_it_outdated`) are replaced:

- `test_a_rewrite_that_changes_the_narration_cancels_the_scenes_voice` (with and without an earlier voice): the rewrite lands first and the voice job is then `cancelled` and refunded. The scene is `outdated` (earlier voice kept) or `missing`. No new file is stored.
- `test_a_narration_changed_behind_the_apis_back_is_caught_when_the_voice_is_saved` (with and without an earlier voice): the apply-time safety net, reached through a direct database update of the narration while the provider works. Result: `failed scene_changed`, refunded, `outdated` or `missing`, and the made file deleted.

### New tests

- **QB4-014 (project status)**:
  - QA's repro: ready project, C7 on scene 1, edit scene 2's on-screen text, the take succeeds. The project stays `ready` during and after.
  - A failed take sends the project to `draft`; a successful retry brings it back to `ready`.
  - A draft (scripted, never ready) becomes `ready` when its last missing voice is made.
  - A scene getting its first voice does not count as ready while queued; only a redone scene with an earlier ready voice does.
- **QB4-015 (lock order)**:
  - `test_a_lock_error_when_saving_fails_the_voice_and_deletes_its_file`: the worker's second lock raises (simulated deadlock victim). The job fails `internal_error`, is refunded, the scene is `failed`, and no asset or file remains.
  - `test_deleting_the_account_while_a_voice_is_being_made_drops_it`: A12 during the provider call answers 204. The job is gone or `cancelled`, and no asset or file remains.
  - G3 cancel during the provider call was already covered (`test_a_voice_cancelled_while_it_is_being_made_is_dropped_and_its_file_deleted`) and passes with the new lock order.

### Not covered

- A real lock-order deadlock between two database sessions. The tests check what happens when a lock fails, not that the new order prevents a deadlock.
