# ReelForge: QA report for B3 (projects, scripts, scenes, jobs, credits)

| | |
|---|---|
| Date | 2026-11-25 |
| Milestone | B3 Projects, scripts and jobs: API B1–B10, C1–C6, G1–G3, H1–H2, the language models, credit reservation, refunds and monthly reset |
| Build tested | `backend/` working tree on 2026-11-25 (B3 not committed), Compose stack running, API at http://localhost:5000/api/v1, a real Celery worker, `LLM_PROVIDERS=mock` |
| Checked against | API design (sections 2, 4, 5, groups B, C, G, H, shared objects, error table), PRD (5.2, 5.6 E2/E5, 5.10, flows 3, 4, 7, 8, edge cases S-1 to S-7, G-1 to G-5, G-11 to G-14, CR-1 to CR-4), technical design (7, 8, 9.0, language model, 10, 13), DB design (projects, scenes, jobs, credit tables, section 5, queries 6.1 to 6.3), `schema.sql`, the prototype `pipeline/reelforge/` (prompts, LLM providers, errors), and the web app's `src/api`, `src/mocks` and the Stitch notes for screens 9 and 10 |
| Scope | B3 code only. B1 and B2 findings are in their own reports and are not repeated. B11, B12 and C7 to C10 moved to B4 and are not reviewed |

## Verdict: fail

The endpoints do what the API design says when one request runs at a time: projects, scripts (written and pasted), scene add, edit, delete, duplicate and reorder, rewrite, hooks, ideas, estimate, job polling and cancel, the ledger, refunds on every failure path, the hourly language-model limit and the monthly reset all worked when run. Ownership is checked on every endpoint.

It fails on the money and on concurrency:

- **QB3-001 (Blocker)**: two requests at the same moment can both spend the same credits. With 3 credits, 10 parallel "create project" requests were all charged against a balance that never went below 0: 7 were accepted. This is exactly what CR-3 forbids, and B11 will reserve whole batches through the same code.
- **QB3-002 (Major)**: cancelling a running job (or deleting its project) does not stop its result. The job ends `succeeded`, its output is saved, and its credits are refunded as well.
- **QB3-003 (Major)**: starting work that is already running answers with an HTML `500` instead of `409 already_running`. With a real language model (several seconds per call) a second click on **Rewrite** or **Write script** is enough.

QB3-001 and QB3-002 have the same root cause: a row lock that returns the copy of the row the session already holds instead of the locked, current one (details under each finding).

| Severity | Count | Ids |
|---|---|---|
| Blocker | 1 | QB3-001 |
| Major | 2 | QB3-002, QB3-003 |
| Minor | 9 | QB3-004 to QB3-012 |
| Polish | 2 | QB3-013, QB3-014 |
| Questions | 7 | Section 5 |

**"Verified" column**: *Run* means reproduced against the running stack (curl, or a script run inside the `api` container that calls the product's own functions). *Read* means concluded from the code only.

**Limits of this review**

- The mock language model answers instantly, so "cancel while running" and "delete while running" cannot be timed through the API. QB3-002 was reproduced inside the `api` container by calling the real `jobs.run()` and, from a second thread with its own session, the real `jobs.cancel()` while the job's work step was in progress. Queued jobs for the duplicate-job and delete tests were created the same way (`jobs.new_job` + `credits.reserve`, not dispatched), then used through the API.
- No real Gemini or Groq key exists, so retries, fallbacks, refusals from real providers and timeouts are Read. The mock's `[fail]`, `[refuse]` and `[invalid]` paths were Run.
- To test the status model on a generated project, I set one of my own projects to `rendered` with all scenes `ready` directly in the database (no assets exist yet).
- What I left behind: users `qab3a`, `qab3b` (verified) and `qab3u` (unverified) `@example.com` with a dozen projects; on `qab3b` an `adjust` ledger row "QA set balance" and one monthly reset run (its `resetsAt` is back to 2026-12-01); its ledger total and balance disagree by 4 because of QB3-001. The language-model limit for `qab3b` is used up until about 05:00 UTC and nearly used up for `qab3a`.

---

## 1. Findings

### Blocker

#### QB3-001: Two requests at the same moment can spend the same credits

| | |
|---|---|
| Requirement | PRD CR-3 and Flow 7; technical design 8 (reserve under a row lock); DB design 6.1; `balance` is the cached sum of the ledger (technical design 4) |
| Verified | Run |
| Cause | `models.py:47`: `User.credit_account` is `lazy="joined"`, so the account row is loaded together with the user when the request is authenticated. `credits.lock_account()` (`credits.py:35-38`) then runs `SELECT … FOR UPDATE`, which waits for the lock correctly but returns the object already in the session **without refreshing it** (SQLAlchemy only overwrites loaded attributes with `populate_existing`). `credits.reserve()` (`credits.py:71`) then writes `balance = <stale value> − cost` as an absolute value |

Steps:
1. Give a user a balance of 3.
2. Send 10 `POST /projects {"topic": …}` requests in parallel.

Expected: 3 accepted (`202`), 7 refused with `402 insufficient_credits`; balance 0; ledger total equals balance.
Actual: **7 accepted**, 3 refused. Seven `reserve −1` ledger rows, balance 0, ledger total −4 (balance 0 only because each request wrote "what I saw minus 1"). Seven scripts were written for 3 credits.

The same stale write can also destroy credits: a refund made with SQL (`credits.py:93-97`, for example a job failing in the worker) between the moment a request loaded the user and the moment it reserves is overwritten by the reserve. The monthly reset (`credits.py:110-146`) loads the account fresh and is not affected.

This must be fixed before B11, which reserves a whole batch (CR-1) through the same `reserve()`. A test that runs parallel requests against real Postgres would have caught it; the manual job dispatch used by the tests does not exercise it.

### Major

#### QB3-002: Cancelling a running job does not stop its result; it is saved and refunded

| | |
|---|---|
| Requirement | API G3 (`cancelled`, credits refunded), B6 and PRD G-14 (deleting a project cancels its jobs), B8 ("if it fails, the project returns to its previous status with its scenes untouched"); technical design 7, rule 4 ("tasks check this before saving output"); DB design 5 (`jobs.state`: `cancelled` is final) |
| Verified | Run (inside the `api` container, real `jobs.run` and `jobs.cancel`, see Limits) |
| Cause | `jobs.py:313-336`. `run()` commits the job as `running`; reading `job.type` (line 320) and `job.input` in the work step reloads the row into the session. After the work, `_lock(job_id)` (`jobs.py:304-305`) selects `FOR UPDATE` but gets back the same in-session object with `state == "running"` from before the cancel, so the "cancelled meanwhile" check on line 337 never sees the cancellation |

Steps (as run): reserve 1 credit for a job; start `jobs.run()`; while its work step runs, cancel it from a second session (`jobs.cancel` + commit), exactly what `POST /jobs/:id/cancel` does.

Expected: the job stays `cancelled`, its result is thrown away, 1 credit refunded.
Actual: the job ends `succeeded` with its output saved, **and** the refund row exists (`creditsRefunded: true`, balance back up by 1). A job went `cancelled → succeeded`.

What this means per job type (Read, same code path):
- **Script (B3/B8)**: cancelling a script, or regenerating and then cancelling, still replaces every scene; `on_stop` has already put the status back, then `apply` sets it to `draft`. The creator gets the new script free and loses the old one they meant to keep.
- **Project deleted while its script is written**: scenes are written into the deleted project. Invisible to the user, but the job reads `succeeded` with a refund.
- **Rewrite (C6)**: the scene text changes after the creator cancelled.
- B4 voice and visual jobs will inherit this.

The worker run also has no path for the job row having disappeared between steps other than `None`, which works today only because the row is deleted (see QB3-008).

#### QB3-003: Starting work that is already running returns an HTML 500, not 409 already_running

| | |
|---|---|
| Requirement | API error table (`409 already_running`), B8 (`409 already_running`), PRD G-12 ("ignore duplicates"); API section 2 (JSON error format) |
| Verified | Run |
| Cause | `jobs.py:251-266`: the job is added to the session **before** `begin_nested()`. When the partial unique index rejects it, the savepoint is rolled back but the job object stays pending in the session. The `ApiError(409)` is raised correctly, but the after-request log hook (`log.py:85`, reading `user.id`) triggers an autoflush that inserts the same job again outside any savepoint; that fails and leaves the session unusable (`PendingRollbackError`), and because this happens in `after_request`, Werkzeug answers with its own HTML page |

| Request | Expected | Actual |
|---|---|---|
| `POST /scenes/:id/rewrite` while a rewrite of that scene is queued | `409 already_running` (JSON) | `500`, HTML "Internal Server Error" |
| `POST /projects/:id/script/regenerate` while the project's script job is queued | `409 already_running` | `500`, HTML |
| Three `POST …/script/regenerate` in parallel | one `202`, two `409` | one `202`, two `500` |

Nothing is charged (balance unchanged) and the first job runs normally, but each attempt uses one of the 20 hourly language-model calls. B8 does not check `status == "scripting"` before trying (`routes/projects.py:334-341`), so every B8 click while a script is being written goes down this path, not only races. C6 has the same exposure through the per-scene index. B11 and C7/C8 in B4 will rely on the same `new_job`.

### Minor

#### QB3-004: "Write script" after a failed first attempt is not a retry of the first attempt

| | |
|---|---|
| Requirement | API B3 ("If writing fails … the project stays, as a draft with no scenes, and **Write script** (B8) tries again"; a pasted script is split "so the creator's words never change"); PRD S2 |
| Verified | Run (B8 on a pasted-script project) and Read (the failed-first path uses the same code) |
| Cause | `routes/projects.py:65-78`: only the first job (`first=True`) uses the pasted script; B8 sends `topic = source_script[:3000]`. `job_types.py:309-312`: only the first job sets the title and music mood |

Run: a project created from the pasted script "My own words stay. Exactly as I wrote them." had that as its single scene. After B8 its scenes were "Here is something surprising about My own words stay. Exactly as I wrote them..", "Most people never notice it…", and so on: the model wrote a new script using the creator's text as a topic.

Consequences for a project whose first attempt failed (a refusal, provider down, unusable answer):
- pasted script: the retry replaces the creator's words with AI-written ones;
- any project: the retry never sets the title or music mood, so it stays "Untitled Reel" with mood `calm` (Read).

Whether B8 on a pasted-script project should ever rewrite the words in normal use is question 2.

#### QB3-005: Choosing music in B5 always fails validation

| | |
|---|---|
| Requirement | API B5 (`music`: `{"trackId"}`, `{"assetId"}` or null); API section 2 (`details.fields` names the field) |
| Verified | Run, and inside the container with the schema alone |
| Cause | `project_schemas.py:142`: `MusicTrackChoice | MusicAssetChoice` inside the strict body. Validating the union tries each member strictly, where a UUID must be a `UUID` object, not a string; `Field(strict=False)` on `Id` is not applied there. `MusicTrackChoice` alone accepts the same input |

`PATCH /projects/:id {"music": {"trackId": "3f1c2a4e-9b7d-4c1e-8a2b-1234567890ab"}}` → `400` with
`{"music.MusicAssetChoice.assetId": "Required", "music.MusicAssetChoice.trackId": "This field isn't accepted", "music.MusicTrackChoice.trackId": "That value isn't valid"}`. Same for `assetId`. `"music": null` works.

No tracks or uploads exist until B4, so nobody can hit it yet; it becomes Major the day the music library lands. The error keys contain Python class names, which the web app cannot map to a field.

#### QB3-006: Reordering scenes does not move a generated project back to ready

| | |
|---|---|
| Requirement | DB design 5 ("editing a ready / rendered / posted project → back to ready"); technical design 7 status diagram; PRD 7.5 |
| Verified | Run |
| Cause | `routes/scenes.py:369-385` does not call `projects.after_edit()`; every other edit endpoint does |

A project in `rendered` with every scene ready: `POST /projects/:id/scenes/reorder` → `200`, status still `rendered`. An on-screen-text edit straight after → `ready`, as designed. A reorder changes the video, so the render is outdated; the web app would offer the old render for publishing.

#### QB3-007: Scene edits do not change the project's updatedAt

| | |
|---|---|
| Requirement | API shared objects ("Lists are sorted by `updatedAt`, newest first"); PRD flow 8 and 5.9 (recent Reels on the dashboard) |
| Verified | Run |
| Cause | `updated_at` is kept by a trigger on `projects` only (`schema.sql:334`); C1 to C6 write `scenes` and touch the project row only when its status changes |

`PATCH /scenes/:id` (narration) and `POST /projects/:id/scenes` on a draft: the project's `updatedAt` stayed `03:59:13Z`, and the dashboard order did not change. A creator who spends an hour in the script editor sees that Reel listed by the time of its last status change. The same applies to the rewrite result.

#### QB3-008: Deleting a scene deletes its jobs, so a polled job disappears and the ledger loses its links

| | |
|---|---|
| Requirement | Technical design 7, rule 4 ("deleting a project or scene marks its jobs `cancelled`"); API G1, H2 (`jobId`, `projectId`) |
| Verified | Run |
| Cause | `schema.sql`: `jobs.scene_id … ON DELETE CASCADE`, and `credit_ledger.job_id … ON DELETE SET NULL`. `cancel_active()` marks the jobs cancelled and refunds them, then the scene delete removes the job rows in the same transaction |

After B8 replaced the scenes, the earlier rewrite jobs of those scenes were gone: their eight ledger rows ("Scene rewrite", "Refund: Scene rewrite") now have `jobId: null` and `projectId: null`. A client polling a rewrite job whose scene was just deleted gets `404`, never `cancelled`. In B4 every voice and visual job is per scene, so a regenerated script or a deleted scene will erase the generation history the admin pages (I5, I7) are meant to show. Fixing it means a schema migration (`SET NULL` instead of `CASCADE`, and checking that the active-job indexes still behave).

#### QB3-009: Gaps in how the worker turns provider trouble into job errors

| | |
|---|---|
| Requirement | PRD G-1 (retry, then the fallback provider), G-11 (time out → failed, refund, retry offered), S-1; technical design 7, rule 3 |
| Verified | Read |

- **Time budget**: Gemini's HTTP timeout is 60 s with 3 attempts and 2 s + 4 s back-off (`providers/base.py:180, 207-220`), about 186 s, before Groq is tried; the Celery soft limit is 240 s (`tasks/jobs.py:399`). A slow Gemini leaves Groq about 54 s, so the fallback rarely gets a fair chance.
- **Soft time limit**: `SoftTimeLimitExceeded` is an ordinary `Exception`, so `jobs.run` (`jobs.py:330-334`) records it as `internal_error` ("Something went wrong on our side") and logs a stack trace, instead of `timed_out`.
- **Non-JSON answer**: `http(...).json()` in `providers/llm.py:288, 320-325` raises `requests.JSONDecodeError`, not `ProviderError`, so a proxy error page from Gemini skips the Groq fallback and ends as `internal_error`.
- **Errors in the apply step**: `apply` only handles `JobFailed` (`jobs.py:340-345`). Any other error there (database error, the row gone) escapes the task and leaves the job `running` until `fail_stuck` picks it up 6 or more minutes later.
- **Refusal detection**: any 4xx whose body contains "policy" or "blocked" (`base.py:202`) is treated as a content refusal, which stops the fallback and tells the creator to reword. A configuration error such as an access block on the key would be shown as "refused by the AI's content rules".

#### QB3-010: The prompt version is not stored, and provider usage is not recorded per call

| | |
|---|---|
| Requirement | Technical design 9, language model ("the job stores which prompt version was used"); technical design 13 ("every provider call logs provider, latency, outcome and estimated cost") |
| Verified | Read (`PROMPT_VERSION` is defined in `prompts.py:12` and used nowhere; the module's docstring says it is stored) |

`record_usage()` (`jobs.py:426-447`) counts one row per job, not per call; a failed job is recorded under provider `none`, so the admin cannot see which provider is failing; nothing records latency or `cost_estimate_usd`. The admin usage page (I7, B8) is built on this table.

#### QB3-011: Creator text can close the "treat as material" markers in prompts

| | |
|---|---|
| Requirement | Technical design 10 (content safety); prompts docstring ("Creator text is placed between clear markers") |
| Verified | Run (input accepted and placed in the prompt unchanged); effect on a real model Read |

`_quoted()` (`prompts.py:20-21`) wraps text in `<<<` … `>>>` without escaping. A pasted script with a line `>>>`, then "IGNORE ALL PREVIOUS INSTRUCTIONS…", was accepted and sent as is. Single-line fields (topic, style, character, instructions) can contain `>>>` too. Because the creator only affects their own output and the answer is still checked and cleaned, the practical risk is weakening the safety instruction, not data exposure. Escaping or stripping the marker sequences in creator text is enough.

#### QB3-012: The migration guard deferred to B3 is not there

| | |
|---|---|
| Requirement | FIX_LOG_B1 QA-048 ("a guard is added when the remaining models exist", deferred to B3) |
| Verified | Read (`api/migrations/env.py` has only the "no changes" filter) |

B3 maps most tables, but `renders`, `posts`, `idempotency_keys` and `usage_daily` are still unmapped, and `music_tracks` and `provider_switches` are only partly mapped (for example `music_tracks.s3_key`, `provider_switches.updated_at`), so `flask db migrate` would now generate drops for those tables and columns. Either add the guard or move QA-048 explicitly to a later milestone.

### Polish

#### QB3-013: Error details the web app cannot use well

| | |
|---|---|
| Requirement | API section 2 (`details.fields` names the field) |
| Verified | Run |

- `POST /projects` with both or neither of `topic` and `script` → `fields.body`, not `topic` or `script`.
- `POST /projects/:id/scenes {"position": 0}` → "That value isn't valid" (the rule is "1 or more").
- The `music` union errors (QB3-005).
- `invalid_output` exists in `jobs.MESSAGES` but is never used: an unusable model answer ends as `provider_failed` (Run with `[invalid]`).

#### QB3-014: Documents not brought in line with the B3 scope

| | |
|---|---|
| Requirement | MILESTONES, PROJECT_STATUS, API design |
| Verified | Read |

- MILESTONES: B8 still lists "credit reset", which is now in B3. Uploads appear in both B4 ("D group") and B7 ("uploads"). The rest of the move (B11, B12, C7 to C10 to B4) is sensible: those are exactly the endpoints that need voice, visual and timeline code, and F5 can be built on the mock meanwhile.
- PROJECT_STATUS still says "upload limits and a migration guard (B3)"; upload limits now belong to B4 with D1.
- Technical design 7 queue table: no `default` queue (QA-050 was deferred to B3 "when tasks are routed"); the `llm` row lists "rewrite, caption" but not hooks.
- API design: job error codes (`provider_failed`, `provider_unavailable`, `content_refused`, `scene_changed`, `timed_out`, `queue_unavailable`, `internal_error`) are not listed anywhere, but the web app needs them (section 2, item 3).

---

## 2. What the web app will need for F5 (New Reel, script editor) that the API does not give, or gives differently

From PRD flows 3 and 4, Stitch screens 9 and 10, UI review section 3, and `frontend/src/api`, `src/mocks`. Run unless marked.

| # | Topic | What F5 needs | What the API does now |
|---|---|---|---|
| 1 | Generate button | Script editor "Generate Reel" (F5 "done when … sent to generation") | B11 is in B4. Against the real API F5 can stop at the estimate (B10) |
| 2 | Voice picker in the settings step | The voice list (D3), and a 400 for an unknown voice | D3 is in B4; any `voiceId` up to 100 characters is accepted (`"zzz"` was) |
| 3 | Job error messages | A list of `job.error.code` values to decide which show **Try again** and which ask for rewording | Codes are in `jobs.MESSAGES` only (QB3-014). The `message` text is written for creators and can be shown as is |
| 4 | New error codes | Handling for `409 already_running` (also for every scene edit while the script is being written), `409 no_script` (hooks), `409 job_finished` (cancel), `503 provider_unavailable` (B1, B3, B8, B9, C6), `429` shared across ideas, scripts, rewrites and hooks (20 an hour, so "More ideas" eats into script writing) | All present; the mock has none of them yet. Until QB3-003 is fixed a duplicate click gives an HTML 500 |
| 5 | Script progress | A progress bar while scripting | `progress` is 0 (queued), 10 (running), then the project is `draft`. Use an indeterminate indicator |
| 6 | Project while scripting | Something to show in the header | `title` is "Untitled Reel", `scenes` is empty, `topic` is `""` for a pasted script. After a failed first script: `draft`, no scenes, title stays "Untitled Reel" even after a successful **Write script** (QB3-004) |
| 7 | Live length and word count (S7) and the over-target warning (S2, S3) | Per-scene and total length, word count, target | `estimatedSeconds` per scene and per project, `wordCount` per project only (count per scene in the app), `targetSeconds`. The warning thresholds are the app's to apply (none returned) |
| 8 | Credit estimate in the sticky summary | Updated after every change | C1, C2 and B5 return `estimate`; C3 (`204`), C4 and C5 do not, so refetch B10 after delete, duplicate and reorder |
| 9 | Credit costs for the buttons (CR2) | Cost of Write script, Rewrite, Ideas, Hooks | `GET /credits` `costs` has `script` only; rewrite costs the same as `script` and ideas and hooks are free, which the app has to know |
| 10 | Rewrite | Result and conflicts | `job.output` = `{narration, onScreenText}`. If the creator edits the scene while the rewrite runs, the job fails with `scene_changed` (refunded). Lock or mark the scene while a rewrite is active |
| 11 | Hooks | "3 alternative opening lines" and choosing one | `output.hooks` has 1 to 3 items (the server accepts fewer). Choosing one is a `PATCH /scenes/:id` of the first scene, which outdates its voice |
| 12 | Polling | One poll for the editor | B4 `activeJobs` includes script, rewrite and hooks jobs, with `creditsReserved` and `creditsRefunded` |
| 13 | "Edited …" time and dashboard order | Updated by script edits | Not updated by scene edits (QB3-007) |
| 14 | Field errors on the New Reel form | `fields.topic` / `fields.script` | `fields.body` for the topic-or-script rule (QB3-013) |
| 15 | Moods and tones | The 10 moods and 5 tones for chips | Not served by the API; the app hard-codes them from the API design (fine, but keep the lists in step) |
| 16 | Full AI style lock (flow 4, step 3) | Keyframe generation | Not in B3 (B7) |
| 17 | Mock vs real | The mock implements only B2, B6 and H1 of these groups; its `GET /projects` never paginates and its 404 text is "We couldn't find that Reel." (real: "We couldn't find that.") | F5 should add B1, B3 to B10, C1 to C6 and G1 to G3 to the mock following the API design, including the error codes in row 4 |

---

## 3. Recorded deviations

None claimed by the developer beyond the documented scope move. The API design changes in this milestone (B3 pasted-script rule and failure rule, B8 kept fields, new 409 codes, `wordCount`, scene `estimatedSeconds`, hooks in the shared limit) all match the code, except where findings above say otherwise (QB3-004).

---

## 4. What passed

Run unless marked.

**Projects (B1 to B10)**
- B1: `202` with a job; output has the requested number of ideas with title, hook and angle; free; `count` 11 → `400`; missing `niche` and line breaks → `400`; unverified user → `403 email_not_verified`.
- B3: topic and pasted script both work; status `scripting` then `draft`; 1 credit reserved and kept on success; settings default from the profile. A pasted script is split at sentence ends and paragraph breaks with the words unchanged (extra spaces, tabs and `\r\n` normalised); the model only adds title, on-screen text, prompts and moods. A refused topic → job `failed` with `content_refused`, credit refunded, project `draft` with no scenes. Both or neither of topic and script, `targetSeconds` 45, unknown fields → `400`.
- B2: keyset pagination over 7 projects with `limit=3` returned every project once in `updatedAt` order; `status` list, unknown status, `q` (with `%` escaped), bad `limit` and bad `cursor` → as designed; `counts` ignore the filters.
- B4 to B7: full Project shape as in the API design; B5 partial update of title, caption, hashtags (cleaned, de-duplicated, spaces refused), settings (partial merge, unknown key refused), music mood, `null` title refused; mood change → voice `outdated` and a ready project → `draft`; B6 → `204`, its queued script job `cancelled` and refunded; B7 Read.
- B8: replaces all scenes, keeps the creator's title and edited caption; `[fail]` → job failed, refunded, scenes and status untouched.
- B9: three hooks; no scenes → `409 no_script`; while scripting → `409 already_running`.
- B10: voice 2 credits per started 30 s per missing or outdated scene, stock 0, music line; matches the API example shape.

**Scenes (C1 to C6)**
- C1 at position 1 shifts the others; past the end goes last; position 0, empty narration, unknown field → `400`. C4 inserts right after the original. C3 renumbers 1..n. C5 reverses correctly; a missing, repeated or foreign id → `400`. Positions were 1..n with no gaps after every step.
- C6: success updates the scene and charges 1 credit; `[fail]` → `provider_failed`, `[refuse]` → `content_refused`, `[invalid]` → asked again, then failed; each refunded.
- Every scene edit, rewrite and hooks request is refused with `409 already_running` while the script is being written (Read for C1 to C5, Run for B9).

**Jobs and credits (G, H)**
- G1, G2 (`active=true`, cursor, `active=maybe` → `400`), G3: queued job → `cancelled`, `creditsRefunded: true`, balance up by 1; a second cancel → `409 job_finished`.
- H1 matches the API example; H2 newest first with working cursors.
- Refunds: one per job (unique index plus `ON CONFLICT DO NOTHING`); ledger total equals balance for the user that had no parallel requests.
- Monthly reset: a due account went from 0 to its allowance with a `grant` row, and `resetsAt` moved to the next month start from the reset date. The beat schedule has the hourly reset and the 5-minute stuck-job task (Read).
- Duplicate task delivery: `run()` takes a job only while it is `queued`, under a lock (Read).

**Cross-cutting**
- Ownership: 15 endpoints called by another user on someone else's project, scene and job → `404` every time; malformed ids → `404`.
- Rate limit: the 21st language-model request in an hour → `429` with `Retry-After` and `details.retryAfterSeconds`.
- Non-JSON body → `415`; no body on B8 accepted.
- No creator text in logs: request lines carry ids only; job failures log the code; provider errors keep the provider's status and at most 300 characters of its answer and are not logged (Read). Creator prompts are kept on the job's `input`, as technical design 10 asks, and are not returned by G1.
- `LLM_PROVIDERS` refuses unknown names and refuses `mock` in production (Read).
- `GET /projects` uses 4 queries regardless of page size (Read).

---

## 5. Questions

1. **Monthly reset while credits are reserved**: a job reserved before the reset and refunded after it adds its credits on top of the new allowance (`credits.py:113-115`), so a little does carry over. CR-4 says unused credits don't carry over. Accept this, or refund into the old month (refund then reset)?
2. **B8 on a pasted-script project**: should **Write script** re-split the creator's own text with fresh annotations, or write a new script from it (current behaviour)? The answer decides QB3-004's fix.
3. **"An edited caption is kept" (B8)**: the code keeps any non-empty caption and hashtags, including the AI ones from the first script, so a regenerated script never gets a matching caption. Is that intended, or should only a caption the creator edited be kept (which needs a flag)?
4. **Title edited while the first script is being written**: the first script then overwrites it (`job_types.py:309-311`). Should the creator's title win?
5. **Should a refused attempt count against the 20-an-hour limit?** Requests answered `402`, `409` or (today) `500` all use one of the 20.
6. **B7 while the source is still scripting** produces an empty `draft` copy. Refuse with `409 already_running`, or allow?
7. **Hooks**: B9 says 3; the server accepts 1 or 2 if that is all the model returns. Ask again instead?

---

## 6. For B4

Not defects in B3. Listed because B3 decides them.

- **Fix QB3-001 first.** B11 reserves whole batches through `credits.reserve()`; parallel `generate` calls would overspend.
- **Batches and duplicates**: `new_job` raises `409` on the first duplicate, which aborts the whole request. B11 says `409` only when *everything* is already running, so a batch must skip scenes with an active job instead. The QB3-003 fix must keep that possible.
- **One task for every job type**: `jobs.run` has fixed limits (240 s soft, 300 s hard) and `task_routes` pins it to the `llm` queue. Voice (2 min) and AI video (12 min) need per-type limits (technical design 7, rule 3), passed per call. `QUEUED_LIMIT` (30 min) will fail queued AI video clips when the `ai_video` queue (concurrency 1) has a backlog of ten clips; G-2 says to queue rather than fail.
- **N+1 on the polled project**: `scene_json` reads `voice_asset` and `visual_asset` lazily, two queries per scene once assets exist, on a response polled every 2 s per open editor (up to 60 extra queries for 30 scenes). Load them with the scenes.
- **Status while generating**: `after_edit` does nothing in `generating`; `advance_project` must recompute it. Edits in the middle of generation are a stated design goal (technical design 7).
- **Job history** (QB3-008): decide the `scenes`/`jobs` foreign key before voice and visual jobs exist.
- **Account deletion** (A12, `accounts.py:113-119`) still cancels jobs with plain SQL: no task revoke, no `on_stop`. Use `jobs.cancel_active` now that it exists.
- **Purge**: `purge_deleted` now deletes projects too; with assets, the rows go before the files (as noted in the B2 report).
- **Locks**: the rewrite result locks the scene while the edit endpoints lock the project. It is safe today; voice and visual results in B4 should lock the project like everything else, so their status updates cannot interleave with edits.
