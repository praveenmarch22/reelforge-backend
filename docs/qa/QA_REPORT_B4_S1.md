# ReelForge: QA report for B4 slice 1 (voiceovers)

| | |
|---|---|
| Date | 2026-11-25 |
| Milestone | B4 slice 1: API C7 (one scene's voice) and D3 (voice list), ElevenLabs with Edge TTS as backup, word timings, mood-driven delivery (PRD V2, V3), the `voice` job and queue, the `Idempotency-Key` header |
| Build tested | `backend/` working tree on 2026-11-25 (slice 1 uncommitted on top of HEAD), Compose stack running |
| Checked against | PRD 5 (V1–V4, C1, C3, CR1–CR4), 6 (flow 5), 11 (S-7, G-1 to G-5, G-11 to G-15, CA-4); API design (conventions, error table, Scene and Job objects, B5, C2, C3, C7, D3, G3); technical design 7–9 and 11; DB design (scenes, assets, jobs); the prototype `pipeline/reelforge/providers/voice.py`; the web app's `src/mocks` and `src/api` |
| Not in scope | B11 Generate and B12 timeline (slice 3), visuals C8–C10 (slice 2), Whisper / G-15 transcription (B7), clean-up of replaced or orphaned files (B8) |

## Verdict: fail

C7 and D3 do what the API design says in the normal flow: the job reserves the right credits, the scene goes `queued` → `running` → `ready` with a signed `url`, word timings and the job's `provider`, a duplicate click gets `409 already_running`, `words_edited` is enforced (CA-4), unknown voices are refused, failures and cancellations refund, and the audio file of a dropped job is deleted from storage. The idempotency key is scoped per user and path, replays the first answer and refuses a different body. Provider fallback works: an ElevenLabs `401 quota_exceeded` goes straight to the next provider without retries, and no key appears in error messages.

It fails on one Major finding:

- **QB4-001 (Major)**: if the creator changes the project's voice (B5) while a scene's voice is queued or being made, that scene ends `ready` with the **old** voice. B5 says changing the voice marks all voices outdated. Nothing marks this scene as outdated, so slice 3's Generate would skip it and the Reel would mix two voices.

| Severity | Count | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 1 | QB4-001 |
| Minor | 8 | QB4-002 to QB4-009 |
| Polish | 4 | QB4-010 to QB4-013 |
| Questions | 6 | Section 4 |

**"Verified" column**: *Run* means I reproduced it. I ran a script inside the `api` container that drives the real Flask app through its test client, with the mock voice provider, an isolated database (`reelforge_qa_test`), Redis database 14 and a bucket `reelforge-qa`. Jobs ran through the real `jobs.run()`. To cancel, edit or delete "while running", the script called the API from inside the job's work step. *Run (stub)* means the real provider classes were called with `requests` / `edge_tts` replaced by stubs, so nothing left the machine. *Read* means I concluded it from the code only.

**Limits**: no real ElevenLabs or Edge call was made (as instructed), so real latency, real timing quality and real quota responses are not tested. The two real voices already in the development database (one ElevenLabs, one Edge, made by the developer) were inspected read-only. The QA database, bucket and Redis database were deleted afterwards. I did not touch the Tester's `reelforge_test` database or the test files.

---

## 1. Findings

### QB4-001 Major: changing the project's voice while a scene's voice is running leaves that scene `ready` with the old voice

| | |
|---|---|
| Requirement | API B5 ("Changing `voiceId` marks all voices `outdated`"); PRD flow 5 step 2 ("Change the voice … All scenes regenerate"); PRD S-7 |
| Where | `routes/projects.py` B5 → `projects.mark_voice_outdated` (only changes `ready`/`failed`); `generation.VoiceJob.apply` (checks narration and mood only); `VoiceJob.on_stop` (restores `previousStatus`) |
| Verified | Run |

**Steps**: scripted project, project voice `""`. `POST /scenes/s1/voice/regenerate {}` → 202. `PATCH /projects/:id {"voiceId": "JBFqnCBsd6RMkjVDRZzb"}` → 200. Run the job.

**Expected**: s1 ends up `outdated` (or its job is dropped with `scene_changed`), like every other scene.
**Actual**: right after B5 the statuses are `['queued', 'missing', 'missing', 'missing']`. After the job: s1 is `ready`, and its asset's `meta.voiceId` is `TX3LPaxmHKxFdv7VOQHJ` (Liam, the old default), while the project's `voiceId` is George. Cancelling instead of finishing has the same effect: `on_stop` restores `previousStatus` (`ready`) without checking whether the voice changed.

**Why it matters**: the scene shows as up to date, so neither the creator nor slice 3's Generate (which only regenerates `missing`/`outdated`) will redo it. The rendered Reel has two different narrators. The same gap applies to the account's default voice (A10) for projects whose `voiceId` is `""`; see Q6.

**Suggested fix**: record in the job input whether the voice came from the request (`voiceId` in C7) or from the project/account. In `apply`, when it came from the project, compare it with the voice resolved now and fail with `scene_changed` if they differ (the scene then shows `outdated`). Or have B5 cancel and refund the project's active voice jobs and mark those scenes `outdated`. In `on_stop`, never restore `ready` when the voice the scene should have has changed.

---

### QB4-002 Minor: the Edge provider swallows Celery's soft time limit, and the worst-case retry time is longer than that limit

| | |
|---|---|
| Requirement | Technical design 8, task rule 3 (time limits per queue); PRD G-1, G-11; API Job `timed_out` |
| Where | `providers/voice.py` `EdgeVoice.synthesize` (`except Exception`); `ELEVEN_TIMEOUT = 40`, `EDGE_TIMEOUT = 30`; `providers/base.with_retry` (3 attempts, `Retry-After` up to 60 s); `tasks/jobs.py` (`soft_time_limit=240`, `time_limit=300`) |
| Verified | Run (stub) for the swallowing; Read for the time budget |

**What happens**: `SoftTimeLimitExceeded` is a subclass of `Exception`. When it arrives inside Edge's `asyncio.run`, the `except Exception` turns it into `ProviderError("Edge TTS failed: SoftTimeLimitExceeded", retryable=True)`, which `with_retry` retries. I reproduced this with a stubbed `edge_tts.Communicate`. The budget: ElevenLabs 3 × 40 s plus about 6 s of back-off, then Edge 3 × 30 s plus about 6 s, comes to about 224 s before the S3 upload (up to about 20 s with its retries). With two ElevenLabs 429s carrying `Retry-After: 60`, it is about 340 s. Once a run passes 240 s inside Edge, the soft limit is lost, the hard limit kills the process at 300 s, and the job and its scene stay `running` with credits reserved until `fail_stuck` runs. `fail_stuck` acts 6 minutes after the start and runs every 5 minutes, so the scene can stay `running` for up to about 11 minutes.

Each ElevenLabs timeout that is retried can also be billed: ElevenLabs counts characters for a request the client gave up on. On a 10 000-character free plan, three timeouts on a 1 500-character narration use almost half the month.

**Suggested fix**: in `EdgeVoice`, catch `SoftTimeLimitExceeded` before `Exception` and re-raise it. Give `work()` a deadline (for example 200 s from the start). `with_retry` and `with_fallback` stop starting new attempts when the time left is shorter than the provider's timeout, and cap `Retry-After` at the time left. Consider not retrying ElevenLabs on a read timeout and going straight to Edge. Set the voice task's limits explicitly instead of inheriting the LLM values. The comment in `tasks/jobs.py` still says "Language-model jobs".

---

### QB4-003 Minor: a failed or cancelled voice job does not recalculate the project's status

| | |
|---|---|
| Requirement | DB design 5 and `projects.after_edit` (a generated project is `ready` only when every scene's voice and visual are `ready`); PRD G-5 |
| Where | `generation.VoiceJob.on_stop`; `jobs.fail` / `jobs.cancel` |
| Verified | Run |

**Steps**: project `ready`, every scene `ready` (visual statuses set directly in the QA database). Run C7 on scene 1 with a narration the mock fails on.

**Actual**: project status `ready` while scene 1 is `failed` (`['failed', 'ready', 'ready', 'ready']`). C7 itself also doesn't recalculate: while the scene is `queued`, the project still says `ready`. Only a successful `apply` calls `after_edit`.

**Why it matters**: the dashboard and, later, the render button (E1, G-5) read the project status, which now disagrees with its scenes.

**Suggested fix**: call `after_edit(project)` (or a status-only version that does not touch `updated_at`) from `on_stop` and when C7 starts. Also see Q5 about whether a failed *re*generation should show `failed` at all when the scene still has a good voice.

---

### QB4-004 Minor: a caption fix made while the voice is being made is silently thrown away

| | |
|---|---|
| Requirement | PRD CA-4 ("Warn that manual caption edits for that scene will be replaced"), C3 |
| Where | `routes/scenes.update_scene` (`words` accepted while a voice job is active); `VoiceJob.apply` (`words_edited = False`, words replaced) |
| Verified | Run |

**Steps**: scene with a voice, `wordsEdited: false`. C7 → 202. While the job runs, `PATCH /scenes/:id {"words": [...]}` → 200, `wordsEdited: true`. The job finishes.

**Actual**: the job succeeded, the hand-edited words were replaced and `wordsEdited` is `false`. The creator was never warned, because the C7 check ran before the edit.

**Suggested fix**: either refuse caption-word edits while the scene's voice job is active (`409 already_running`, which the editor can explain), or save `wordsEdited` in the job input. Then in `apply`, if it has become true since the job started, drop the result with `scene_changed`.

---

### QB4-005 Minor: editing narration or mood while the voice is queued leaves it `queued`/`running`, and the provider is still paid for

| | |
|---|---|
| Requirement | PRD S-7 ("Mark affected scenes' audio as outdated"); G-12 |
| Where | `projects.mark_voice_outdated` (ignores `queued`/`running`); `VoiceJob.work` (does not re-check the scene before calling the provider) |
| Verified | Run |

**Actual**: after `PATCH narration` on a scene whose voice is queued, the scene still shows `queued`. The job then runs in full, calling ElevenLabs and spending quota, and only `apply` notices and drops it (`scene_changed`, refunded, file deleted). Until then, C7 for that scene answers `409 already_running`, so the creator has to wait for work that is already known to be useless. The API design describes the end result correctly. The problem is the wait and the wasted quota on a 10-minute-a-month plan.

**Suggested fix**: when C2 or a rewrite (C6) changes narration or mood, cancel the scene's active voice job (refund) and mark it `outdated`/`missing` at once. At least compare narration and mood in `work()` before calling the provider.

---

### QB4-006 Minor: D3 calls ElevenLabs on every request while ElevenLabs is failing

| | |
|---|---|
| Requirement | API D3 ("A curated list, cached"; `previewUrl` "asked for once a day") |
| Where | `voices._previews` |
| Verified | Read |

Only a successful answer is cached. With a key set and ElevenLabs unreachable, or the key revoked, every `GET /voices` makes a new call to `api.elevenlabs.io/v1/voices` inside the request, with `timeout=10` (connect and read each, so up to about 20 s). Onboarding and the editor's voice picker would hang that long each time.

**Suggested fix**: cache the failure too (for example `{}` for 10 minutes), or refresh the previews in a scheduled task and only read Redis in the request.

---

### QB4-007 Minor: caption words keep punctuation with ElevenLabs but lose it with Edge

| | |
|---|---|
| Requirement | PRD C1 (captions synced word by word), C3; technical design 9.0 ("the Reel still gets the same kind of voice") |
| Where | `providers/voice.py`: `words_from_alignment` keeps characters as spoken in the text; Edge `WordBoundary` text has no punctuation |
| Verified | Run (read-only look at the two real voices in the development database) |

ElevenLabs gave `"us."`, `"arms."`. Edge gave `"arms"` and `"brains"` for the narration "…inside their arms, not their brains.". The same Reel's captions change style when a scene falls back to Edge, which can happen in the middle of a project once the monthly quota runs out. It also affects what the creator sees and edits in C2 `words`.

**Suggested fix**: match Edge's words back to the narration's own words in order and take the text from the narration, keeping punctuation. Or decide on one style (with or without punctuation) and apply it to both providers.

---

### QB4-008 Minor: the Scene object does not say which voice, which provider, or whether the timings are estimated

| | |
|---|---|
| Requirement | Technical design 9.0 table (fallback timings are "Estimated word timings (captions only roughly in sync), with a warning"); API C7 ("the job's `provider` says which") |
| Where | `projects.scene_json` → `media()` returns `assetId`, `url`, `durationSeconds` only |
| Verified | Run |

`estimatedTimings`, the provider and the voice used are stored in the asset's `meta` and appear only in the voice job's `output`. After a reload, or for a voice made before the page was opened, the editor (F6) cannot warn that captions are rough. It also cannot show that a scene was voiced by Edge instead of the chosen ElevenLabs voice, or which voice a scene regenerated with C7 `voiceId` is using.

**Suggested fix**: add `provider`, `voiceId` and `estimatedTimings` to the Scene's `voice` object and to the API design.

---

### QB4-009 Minor: the worker's save step and the scene requests lock rows in opposite orders

| | |
|---|---|
| Requirement | Technical design 7 (jobs save their result in one transaction); same pattern as B3's rewrite |
| Where | `jobs.run` → `VoiceJob.apply`: locks the job, then the scene, then updates the project (`after_edit`). C2/C3/C7 lock the project, then the scene; C3 then locks the scene's jobs (`cancel_active`) |
| Verified | Read |

A C3 delete or C2 edit of the same scene at the moment a voice is being saved can deadlock. Postgres then aborts one side. If it aborts the worker, the paid-for voice is dropped as `internal_error` (refunded, file deleted). If it aborts the request, the creator gets a `500`. The window is a few milliseconds per job. It becomes more likely in slice 3, when Generate saves many scenes while the creator is editing.

**Suggested fix**: in the save step, lock the project row first (`SELECT … FOR UPDATE` on the project), then the job, then the scene. Requests already lock in that order. Apply the same change to `RewriteScene`.

---

### QB4-010 Polish: a dropped first voice leaves the scene `missing`, not `outdated` as documented

| | |
|---|---|
| Requirement | API C7 ("Editing the scene's narration or mood while its voice is being made drops that voice … and the scene shows `outdated`") |
| Verified | Run |

A scene that never had a voice ends up `missing` (the code: `"outdated" if scene.voice_asset_id else "missing"`). The behaviour is reasonable. The document should say "`outdated`, or `missing` if the scene had no voice before".

### QB4-011 Polish: the Idempotency-Key error message lists fewer characters than are allowed

`idempotency.py` accepts `:` and `.` (as the API design says), but the error says "Use up to 100 letters, digits, dashes or underscores". Verified: Run. Fix: add "colons or full stops", or drop them from the rule and the document.

### QB4-012 Polish: technical design and API design text not brought up to date

Verified: Read.
- Technical design 11 (storage layout): still `voice/<scene_id>-<hash>.mp3` and `library/voices/<voice_id>-preview.mp3`. The code stores `voice/<job_id>.mp3` (section 9 says so) and serves ElevenLabs' own preview links.
- Technical design 9.0 provider table: lists `ELEVENLABS_VOICE_ID`, which no longer exists. The voice comes from D3.
- Technical design 8, task rule 3: "voice 2 minutes". The code uses 240 s soft, 300 s hard and a 5-minute stuck limit.
- API C7: "The voice comes from the scene's `voiceId`". Scenes have no `voiceId`; it means the request body's. See Q1.

### QB4-013 Polish: different default provider order in Compose and in the settings

`config.py` and `.env.example` default to `elevenlabs,edge`, but `docker-compose.yml` defaults to `edge`. Someone who adds only `ELEVENLABS_API_KEY` to `.env` gets Edge voices with no sign that ElevenLabs is unused, apart from the job's `provider`. The Compose comment explains this. A startup log line ("voice providers: edge; ElevenLabs key present but not listed") would make it visible. Verified: Read.

---

## 2. What passed

| Check | Verified |
|---|---|
| D3 `200`, 8 voices in the documented shape, first is the default; `previewUrl` `""` without a key | Run |
| C7 refuses an unknown `voiceId` (`400`, field `voiceId`) and unknown fields | Run |
| C7 `202` with Job; scene `queued` → `running` during work → `ready` with `voice.url` (signed, 2 h, public MinIO address in Compose), `durationSeconds`, `words`; job `provider` set; `output.voice` and `estimatedTimings` | Run |
| `voiceId` in C7 is used for that voice only; the project's `voiceId` is unchanged; asset `meta.voiceId` and `input_hash` saved | Run |
| Credits: 2 reserved for a short scene, 4 for an 80-word scene (32 s); refunded on provider failure, `scene_changed` and cancel; `402` leaves the scene's status untouched | Run |
| Second C7 while the first is active → `409 already_running` (G-12) | Run |
| CA-4: `409 words_edited` without confirmation; with `confirmReplaceWords: true` the new voice replaces the words and clears `wordsEdited` | Run |
| Edit narration or mood while the voice is queued → job `failed`/`scene_changed` with the voice message, refunded, scene `outdated`, the file deleted from S3, hand-fixed words kept | Run |
| Cancel (G3) mid-run → `cancelled`, refunded, previous status restored, file deleted | Run |
| Delete the scene mid-run → job `cancelled`, file deleted | Run |
| Provider failure → `failed`/`provider_failed`, refunded, scene `failed` with `lastError` | Run |
| Unverified user → `403`; another user's scene → `404` | Run |
| Idempotency: same key and body → the same `202` and the same job id; different body → `409 idempotency_conflict`; the same key from another user is not replayed (`404`); invalid key → `400` | Run |
| ElevenLabs `401 quota_exceeded` → one call, no retry, served by the next provider; a `400` that is not a safety refusal also falls back; the error text holds the status and body, never the key | Run (stub) |
| v3 emotion tags are removed from caption words | Run (stub) |
| Mood maps for ElevenLabs (all 10 moods) and Edge match the prototype | Read |
| Worker listens on the `voice` queue; `QUEUES` routes `voice` there | Read |
| Real voices made earlier: ElevenLabs and Edge durations agree with file size and bitrate; word timings are in order and within the audio | Run (read-only database) |
| No API key in worker or API logs since the last restart | Run |
| Production check refuses `VOICE_PROVIDERS` with `mock` | Read |

## 3. Notes for the web app (F5/F6, and I1 when it connects to the real API)

- `voice.url` is signed again on every response, so the string changes from one second to the next. Use `assetId` to decide whether to reload an `<audio>` element. Otherwise polling every 2 s restarts playback.
- `previewUrl` can be `""` (no key, or ElevenLabs unreachable). The onboarding voice step currently does `new Audio(voice.previewUrl)`. Hide or disable the play button when it is empty.
- The mock's 6 voices (`src/mocks/db.ts`, Rachel, Adam, …) have different ids from the backend's 8. Only `EXAVITQu4vr4xnSDxMaL` exists in both. Against the real API, any other mock id is refused with `400` ("That voice isn't available"). Update the mock to the backend list. The development database already holds invalid stored ids (`v1` as a user default, `zzz` as a project voice). These are only used as fallbacks, but the UI will show no voice selected for them.
- C7 needs a confirm dialog for `409 words_edited` that resends with `confirmReplaceWords: true`, and a fresh `Idempotency-Key` per click (the client already supports the header).
- `voiceStatus` has 6 values; `queued` and `running` should show progress, and `failed` should show `lastError` with Retry.
- Job error `scene_changed` now has a voice-specific message.

## 4. Questions for the product owner

1. **Per-scene voice.** C7's `voiceId` is used for one take and not remembered. The next regenerate of that scene, or Generate after an edit, goes back to the project's voice. Should a scene keep its own voice choice (a `voiceId` on the scene)? The API design's "the scene's `voiceId`" suggests it should.
2. **Previews versus the voice actually used.** D3 always says `provider: "elevenlabs"` and plays ElevenLabs' sample. With the Compose default (`VOICE_PROVIDERS=edge`), or after the monthly quota runs out, the creator hears one voice in the preview and gets a different (Edge) voice in the Reel. With no key there are no previews at all, so PRD V1 ("with a preview of each") is not met. Should D3 say which provider is active and offer Edge samples?
3. **ElevenLabs free plan terms.** The decision log notes "non-commercial". The free plan also requires crediting ElevenLabs when the audio is published. Reels go to Instagram. Is an attribution line needed in the post caption, or is that accepted for testing only?
4. **Voice credits per scene (slice 3).** C7 charges at least 2 credits per scene. If Generate charges per scene the same way, a 60-second Reel with 8 scenes costs 16 credits for voice alone. The PRD prices "per 30 seconds of audio" (about 4 for 60 s) and says a whole faceless 60-second Reel costs about 10–15. Should Generate charge on the total narration length?
5. **A failed regeneration of a scene that still has a good voice.** It now shows `failed` (and, per G-5, would block rendering) although the old voice is still attached and usable. Keep `failed`, or go back to `ready` and show the error separately?
6. **Projects with no voice of their own.** Projects with `voiceId: ""` follow the account's default voice when each voice job starts. Changing the default in Settings then changes the voice of later takes in existing projects. Should a project's voice be fixed when it is created?

## 5. For slice 3 (Generate and timeline) to keep in mind

- **Reuse by `input_hash`.** The hash covers narration, mood and voice id, not the provider or model. An Edge fallback take would be reused even after ElevenLabs is available again. Decide whether reuse should skip assets with `provider = edge` or `estimatedTimings`.
- **Fix QB4-001 and QB4-003 before Generate.** Generate skips `ready` scenes and relies on project status.
- **Credits:** see Q4. Generate must reserve the whole batch under the account lock (QB3-001 pattern), and `402` must name the total.
- **Throughput.** The Compose worker has concurrency 2 across all queues. One slow voice job can hold a slot for up to about 4 minutes (QB4-002). ElevenLabs' free plan allows few concurrent requests, so expect 429s when 8 voice jobs start together, and keep the deadline handling from QB4-002 in mind.
- **Scene length.** PRD says a scene lasts as long as its voice plus 0.3 s, at most 15 s. Narration can be up to 1 500 characters (80 words already gave 32 s in this run). The timeline needs a rule for voices longer than 15 s.
- **Presigned URLs in the timeline** must outlive a render (2 h now). Renders that wait in a queue may need fresh links when they start.
- **Lock order (QB4-009)** matters more when many scenes are saved at once.
- **Orphaned files.** Files are left behind when the worker is killed after the upload, or when deleting fails in `discard`. They have no asset row, so the B8 clean-up needs to list storage, not just assets marked replaced. Duplicated projects (B5 duplicate) share asset keys under the original project's prefix. Deleting the original must not delete files the copy uses (G-14).
- **Project status while generating.** `after_edit` returns early for `generating`, so whatever finishes the batch must set `ready`, `needs_attention` or `draft` itself.

---

## 6. Recheck (2026-11-25, after FIX_LOG_B4_S1.md)

**Build**: working tree with the fixes (uncommitted). **Method**: as in the first pass, with fresh isolated resources: database `reelforge_qa2_test`, Redis database 13 and bucket `reelforge-qa2`, all removed afterwards. Mock voice provider only, and no real provider was called. To force the lock-order case, two threads were used: one plays the worker (the real `jobs._lock`), the other sends the real G3 request.

### Verdict of the recheck: fail

All 13 original findings are fixed or answered, and the fixes hold up when run. Two problems came in with the fixes:
- **QB4-014 (Major):** a project drops to `draft` during a new take and cannot get back to `ready`.
- **QB4-015 (Minor):** the new status recalculation in `on_stop` brings back a deadlock, this time between G3 cancel and the worker.

### Status of the original findings

| Id | Status | How verified |
|---|---|---|
| QB4-001 | **Fixed.** B5 cancels and refunds the project's active voice jobs. A scene with no earlier voice ends `missing`; a `ready` scene that was mid-take ends `outdated`; the balance is restored | Run |
| QB4-002 | **Fixed.** Edge re-raises `SoftTimeLimitExceeded` (stubbed). Voice jobs are queued with 360 s soft / 420 s hard limits, and `TIME_LIMITS["voice"]` is 7 min, so the stuck limit (8 min) comes after the hard kill. The worst case with two 60 s `Retry-After` waits (about 436 s) now ends cleanly as `timed_out` | Run (stub) + Read |
| QB4-003 | **Fixed, but see QB4-014.** A failed take moves a `ready` project to `draft` | Run |
| QB4-004 | **Fixed.** C2 `words` gets `409 already_running` (with its own message) while the voice is queued or running. Other edits (on-screen text) are still accepted and the job is kept | Run |
| QB4-005 | **Fixed.** A narration change cancels and refunds the job at once, the scene shows `outdated`, and C7 is accepted again straight away. Re-sending the same narration and mood does not cancel | Run |
| QB4-006 | **Fixed.** With ElevenLabs down, three list requests made one call; the failure is cached for 300 s | Run (stub) |
| QB4-007 | **Fixed** when the word counts match (Edge-style `arms` + narration `arms,` gives `arms,`). If the counts differ (for example a standalone dash, or numbers Edge splits), the provider's text is kept, so that scene can still differ. Acceptable | Run (stub) + Read |
| QB4-008 | **Fixed.** `voice` now has `provider`, `voiceId`, `estimatedTimings` | Run |
| QB4-009 | **Fixed** for the worker against C2, C3, B5, B6 and B8: `_lock` takes the project first. But see QB4-015 | Read + Run |
| QB4-010 | **Answered.** The API design now says `outdated`, or `missing` if the scene never had a voice | Read |
| QB4-011 | **Fixed.** The message now lists colons and dots | Run |
| QB4-012 | **Fixed.** Storage layout, settings list, time limits and "the request's `voiceId`" are updated | Read |
| QB4-013 | **Fixed.** Compose defaults to `elevenlabs,edge` | Read |

Product-owner decisions checked:
- C7 on a short scene reserves 1 credit.
- `GET /credits` lists `voiceSceneShort: 1`.
- B10 charges voices once on their total length: 3 short scenes cost 2 credits (Run).

### Regression checks requested

| Flow (cancel → `on_stop` → `after_edit`) | Result | Verified |
|---|---|---|
| C3 delete a scene whose voice is queued | `204`; job cancelled; project status worked out from the remaining scenes (`ready`) | Run |
| B8 script regenerate with a voice job queued | `202`; no error; C7 refused while scripting (`409`); project ends `draft` with the new scenes `missing`. The old voice job, run first, succeeded on the old scene and is detached (`sceneId: null`) | Run |
| B6 project delete with a voice job queued | `204`, refunded, job cancelled | Run |
| A12 account delete while the voice is running | `204`; the job ends `cancelled`, its result is dropped | Run |
| New project-row lock in `jobs._lock` with G3 | **Deadlock:** see QB4-015 | Run |
| A ready project stays ready during a new take | Only while nothing else changes: see QB4-014 | Run |

### New findings

#### QB4-014 Major: a project that drops to `draft` during or after a take never gets back to `ready`

| | |
|---|---|
| Requirement | API C7 ("it stays `ready` while a new take is being made"); DB design 5 (editing a ready project goes back to `ready`, or `draft` if something is now missing) |
| Where | `projects.after_edit`: it returns early unless the status is one of `_GENERATED`, and `draft` is not one of them. During a take the scene is `queued`/`running`, so the "complete" check fails |
| Verified | Run |

**Steps** (project `ready`, every scene's voice and visual `ready`):
1. C7 on scene 1. The project is still `ready`, which is correct.
2. Edit scene 2's on-screen text (or reorder, duplicate, or any other edit that calls `after_edit`). The project becomes `draft`, because scene 1 is `queued`.
3. Let the take succeed. Every scene is `ready`, but the project stays **`draft`**. A later cancel of another take does not restore it either.

A failed take (the QB4-003 fix) does the same: the project goes to `draft`, and retrying until it succeeds leaves it in `draft`.

**Why it matters**: the dashboard shows a finished Reel as a draft. Render (E1) and slice 3's Generate depend on the project status, and no action available today brings the project back to `ready`.

**Suggested fix**:
- In `after_edit`, count a scene whose voice is `queued`/`running` but still has its old file as complete (the old voice still plays, as the fix log says).
- Let `after_edit` also move a `draft` project to `ready` when every scene is `ready` and the project has been generated before (for example, any scene has a voice file).

#### QB4-015 Minor: G3 cancel (and account delete) now deadlock with the worker

| | |
|---|---|
| Requirement | Technical design 7; the purpose of QB4-009 |
| Where | `routes/jobs_credits.cancel_job` locks the job, then `cancel` → `VoiceJob.on_stop` → `after_edit` updates the project row. The worker's `jobs._lock` now locks the project, then the job. `accounts.soft_delete` → `cancel_active(user_id=…)` has the same job → project order |
| Verified | Run (3 of 3 attempts with the timing forced: `G3` answered `500`, log "deadlock detected") |

Before the fixes this pair was consistent, because voice `on_stop` touched only the scene. Now the creator's **Cancel** can fail with a `500` if it lands in the few milliseconds when the worker is starting or saving that job. If Postgres picks the worker as the victim instead, the second `_lock` in `jobs.run` (the save step) is outside the `try`. The task then errors out, and the job stays `running`, with credits reserved and the file left behind, until the stuck limit (8 minutes).

**Suggested fix**:
- In G3, read the job's `project_id`, lock the project, then lock the job (the same order as `_lock`). Do the same in account delete (lock the user's projects in id order before `cancel_active`).
- Also put the save-step `_lock` in `jobs.run` inside the `try`, so a database error there fails the job (refund and `discard`) instead of leaving it `running`.

#### QB4-016 Polish: estimate examples still use the old per-scene voice price

API design B10 and technical design 5.4 still show `{"type": "voice", "count": 6, "credits": 12}`. Under the new rule (2 per started 30 s of all voices together), 6 typical scenes cost 2–4 credits. Verified: Read.

### Counts after the recheck

| Severity | Open | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 1 | QB4-014 |
| Minor | 1 | QB4-015 |
| Polish | 1 | QB4-016 |
| Fixed or answered | 13 | QB4-001 to QB4-013 |

---

## 7. Recheck 2 (2026-11-25, after "Round 3" in FIX_LOG_B4_S1.md)

**Build**: working tree with the round 3 fixes (uncommitted). **Method**: the same as before, on fresh isolated resources: database `reelforge_qa3_test`, Redis database 12 and bucket `reelforge-qa3`, all removed afterwards. Only the mock voice provider was used, and no real provider was called. For the lock-order checks, one thread plays the worker: it locks the project, waits, then calls the real `jobs._lock`. A second thread sends the real request in that gap.

### Verdict: pass

QB4-014, QB4-015 and QB4-016 are fixed. The new `after_edit` did not mark any project `ready` that should not be, in every case I tried. There are no new findings. All 20 checks passed.

### Status

| Id | Status | Verified |
|---|---|---|
| QB4-014 | **Fixed.** See the table below | Run |
| QB4-015 | **Fixed.** With the timing forced, G3 cancel worked 3 of 3 times (`200`, the worker got its lock afterwards), and account delete returned `204` with the job `cancelled`, with no deadlock. The worker's save-step `_lock` is now inside error handling that fails the job, refunds it and calls `discard` | Run + Read |
| QB4-016 | **Fixed.** API design B10 and technical design 5.4 show `"count": 6, "credits": 4` | Read |
| BUG-041 | **Fixed.** An answer with no `voices` list now counts as a failed lookup (5-minute cache) | Read |
| Tester's rewrite note | **Fixed.** A rewrite that lands while the voice is queued cancels and refunds the voice job. The scene ends `outdated` and the project `draft` | Run |

### The new `after_edit`: could it mark projects `ready` wrongly?

Unless stated otherwise, each case starts from a `ready` project with every scene's voice and visual `ready`.

| Case | Result |
|---|---|
| New take on scene 1, then an unrelated edit to scene 2 | Stays `ready`; still `ready` when the take succeeds |
| New take cancelled | `ready` |
| Failed take, then a successful retry | `draft`, then back to `ready` |
| Take started from `outdated` (after a mood edit), then an unrelated edit | `draft` (not counted as ready); `ready` once the take succeeds |
| Take started from `failed` (old file still attached), then an unrelated edit | `draft`; `ready` once the take succeeds |
| Duplicate the project while one of its scenes is mid-take | The copy is `draft`, and that scene is `outdated` in the copy |
| Duplicate a `ready` project | The copy is `ready` (all files reused), as in B3 |
| A `draft` with one `outdated` voice file and everything else ready, then an unrelated edit | Stays `draft` |
| A freshly scripted draft (no files), edited | Stays `draft` |
| A draft whose voices are all `ready` but visuals missing | Stays `draft` |
| B5 voice change during a take (QB4-001 regression) | The job is cancelled; every scene `outdated`; project `draft` |

I also read the code for edits that make a voice out of date without cancelling a take that started from `ready`. That would let a stale "redoing" scene count as ready. I found none: narration and mood (C2), rewrite (C6), voice change (B5) and script regenerate (B8) all cancel the voice job first. Every request path that locks a job now locks its project first.

**Remaining note (not a defect):** a project is now `ready` whenever every scene is `ready`, even if Generate (B11) was never run. This holds once C7 (and, in slice 2, C8) have filled every scene one by one. That matches DB design 5, and slice 3 should keep it in mind.

### Counts after Recheck 2

| Severity | Open |
|---|---|
| Blocker | 0 |
| Major | 0 |
| Minor | 0 |
| Polish | 0 |

All 16 findings (QB4-001 to QB4-016) are fixed or answered. The answered product-owner questions are in FIX_LOG_B4_S1.md.
