# ReelForge: test report for the daily autopilot

| | |
|---|---|
| Date | 2026-11-28 |
| Role | Tester (automated tests only; no product code changed) |
| Build tested | `backend/` and `frontend/` working trees, the autopilot uncommitted. The product code changed while this round ran (the fixes for [QA_REPORT_AUTOPILOT.md](QA_REPORT_AUTOPILOT.md), QAP-001 to QAP-020; last product edit 20:13 IST). Every number below is from runs made **after** those fixes, and the tests cover the fixed behaviour |
| Kind of tests | Backend: pytest inside the existing `api` container against the test database `reelforge_test` and the offline mock providers (one pytest at a time; nothing else started in the stack). Frontend: unit and component only (Vitest, Testing Library, jsdom) against the fake `fetch` in `src/test/fakeApi.ts`. No browser tests, no dev server, no second Docker stack |
| Checked against | [AUTOPILOT.md](../AUTOPILOT.md) (decisions, flow, data, API group L1–L8, AP-1 to AP-7, the screen); [QA_REPORT_AUTOPILOT.md](QA_REPORT_AUTOPILOT.md); the coordinator's two notes on what the fixes changed |

## 1. Results

| Suite | Command | Result |
|---|---|---|
| Backend, autopilot files | `docker compose exec -T api python -m pytest tests/test_autopilot_full.py tests/test_autopilot.py` | **226 tests: 223 passed, 3 failed.** The 3 failures are new tests that find BUG-092 to BUG-094. The 9 starter tests pass |
| Backend, everything | `docker compose exec -T api python -m pytest` | **2143 tests: 2140 passed, 3 failed** (19 min 1 s). The 3 failures are the same new tests (BUG-092 to BUG-094). Every older test passes |
| Backend lint (test file only) | `ruff check tests/test_autopilot_full.py`, `ruff format --check` | Pass |
| Frontend typecheck | `npm run typecheck` (app and `e2e/`) | Pass |
| Frontend unit and component (all) | `npx vitest run` | **976 tests: 973 passed, 3 failed** (25 files), the same on two full runs. The 3 failures are new tests that find BUG-095 and BUG-096 |
| Frontend autopilot file alone | `npx vitest run src/features/autopilot` | 86 tests: 83 passed, 3 failed, the same on three runs |

Not run, as instructed: Playwright (`npm run test:e2e`).

## 2. What was added

| File | Tests | What |
|---|---|---|
| `backend/api/tests/test_autopilot_full.py` | 217 (with `parametrize`) | Every endpoint L1–L8, the suggestions job, the daily start, each step of a run and its failures, the rules for real people, migration 0003, the beat schedule |
| `frontend/src/features/autopilot/AutopilotPage.test.tsx` | 86 | The screen's states, every control, suggest, add and remove, run now, publish, skip, stop, polling, `localHour`, the nav item |

No product file, mock handler or config was changed. The starter file `tests/test_autopilot.py` was left as it is.

Every test in the new backend file starts from empty tables (an autouse fixture). That matters here: `advance_all` moves every run in the database, so a run left by another test would be advanced too. The starter file does not do this (see "Other observations").

### Covered: backend

**Who may call what (all 8 endpoints)**
- Signed out: `401 not_authenticated`, nothing changed. Without the CSRF header: `403 csrf_failed` on the seven that change something.
- Unverified email: `403 email_not_verified` on L2, L3, L4, L6 and L8, with no topic, job or project made; L1, L5 and L7 are allowed.
- Another creator's topic, a malformed id and an unknown id are `404 not_found` on L5, L6 and L7; nothing is deleted, skipped or uploaded. Each creator sees only their own settings, queue and runs.

**L1**: the defaults (off, approve, 17:00 UTC, the default week, storytelling, 30 s, public); the exact fields of the settings and of a topic; the queue in the order added; runs newest first and at most 30.

**L2**
- Refused with `400` naming the field, and nothing saved: hour −1, 24, text, a fraction; an unknown mode, tone, length or visibility; `enabled` as text or a number; rotation with a key outside "0"–"6", an unknown or wrongly cased sport, `null`, a list or text; an unknown field. A body that is not an object.
- Saved: hours 0 and 23, both modes, every tone, 15/30/60/90 seconds, the three visibilities; a rotation change keeps the other days; `{}` answers the whole state.
- Turning on: `409 not_connected` with no channel or with one that needs reconnecting, and nothing else in that request is saved; allowed once connected; other settings and "off" never need the channel.

**L4, L5, the queue**
- 16 bad topics (sport, empty, spaces, 81 and 161 characters, a line break, a number, `null`, unknown fields) and each missing field. 80 and 160 characters are accepted; text is trimmed.
- The duplicate rule: the same legend and story is refused (`409 not_added`) in any letter case and under another sport; another story, another legend and another creator's queue are fine.
- The limit: the 60th queued topic fits, the 61st is `409 not_added`, finished runs don't count, and one removal makes room.
- L5 removes a topic in any status (queued, a finished run, a waiting one). On a run under way it is the stop: the autopilot takes the Reel no further, nothing is rendered or published, the Reel stays, and the next topic can start at once.

**L3 suggestions**
- A `202` job of type `autopilot_topics`, free (no ledger row, the balance unchanged), `output.added: 14`.
- The mix follows the rotation: 8 football, 4 F1, 2 cricket by default; 14 cricket for an all-cricket week; 10 F1 and 4 football for another.
- "avoid" is sent with every topic the creator has, queued or run, and nobody else's; the prompt carries the mix and the list ("none" when empty).
- Unusable answers (no `topics`, empty, text, `null`, only unknown sports, no legend, no story, not objects) fail the job with `provider_failed` and add nothing; a refusal is `content_refused`.
- A mixed answer keeps the usable topics: sport trimmed and lower-cased, text cut to 80 and 160, unknown sports, empty text and non-objects dropped, repeats (of the queue and inside the answer) added once. A sport outside the creator's rotation is left out. At most 30 from one answer. The queue is never filled past 60. Suggesting again adds nothing still queued. `503` when no language model is on.

**`topic_text` and the rules for real people (after QAP-001, QAP-008)**
- "Subject (sport legend): angle" for the three sports, at most 300 characters, and the whole story is kept at the prompt's limits (60 + 140) and the form's (80 + 160).
- The rules travel as `rules` on the script job, are printed under the prompt's own "Rules:" and not inside the quoted topic; a Reel made by hand gets none; a script written again (B8) for an autopilot Reel keeps them.
- The image prompt of an autopilot Reel loses the legend's name and starts with the no-face rule, checked at the image provider; a Reel made by hand is untouched.

**L8 and one run at a time**
- `409 queue_empty`; the project is made with the settings' tone and length, faceless, costs one script; works with the autopilot off and no channel.
- A second start is `409 not_started` through every step of the run (scripting to rendered); another creator is unaffected; once a Reel waits the next can start, and several can wait (AP-5).
- The day's sport first, else the queue's order. A failed start answers with that topic's own reason and the topic is first in the runs with today's date (QAP-004). A run by hand, started or refused, does not use up the day (QAP-011).

**The daily start (`start_due`)**
- Not before the hour; at the hour; once a day; again the next day; later the same day when the hour was missed (AP-6); hours 0 and 23.
- The rotation picks the day's sport, then the queue's order; the creator's own rotation is used.
- An autopilot that is off, and disabled or deleted accounts, are skipped and nothing is marked; an account enabled again starts at the next check.
- An empty queue marks the day (AP-1): a topic added later waits for tomorrow.
- Yesterday's Reel waiting does not stop today's (AP-5). A run still under way stops a second from starting.
- Every creator who is due is started; one creator's unexpected fault does not stop the others and leaves nothing half-made.
- The two tasks are scheduled (hourly at minute 2, every 2 minutes, `maintenance`) and run the two functions.

**A run, step by step (`advance`)**
- Each status: `scripting`, `generating`, `rendering` and `posting` wait (no status change, no new job); `draft` generates; `ready` renders; `rendered` asks for the post text once, waits for it, then publishes or waits; `posted` is `published`. Exactly one script, render, caption and publish job, and one upload.
- A run costs what the same Reel costs by hand.
- Approve mode waits with the video and the written title, and is then left alone. The mode is read when the Reel is finished.
- AP-4: a refused or failed script fails the topic with the job's reason and the credit comes back; a scene that could not be made (`needs_attention`) and a failed render (`render_failed`, refunded) fail it with their reasons; nothing is retried and the next topic can start.
- The post text: a failed caption job, and a caption job that cannot be started, both proceed with the Reel's own title, in approve and in automatic mode.
- AP-2: no credits at the daily start (topic failed with the reason, the day marked, not repeated every hour, autopilot still on), for Generate and for the render (failed with the reason, nothing started, autopilot still on). The post text and publishing need none.
- AP-3: not connected, `reconnect_required` and the `youtube` switch off each leave the Reel `awaiting_approval` with the reason; Publish is refused the same way until it is put right, then works. A publish that fails (`quota_exceeded`, `upload_failed`, `reconnect_required`), in automatic mode or after an approval, waits with the reason and is not retried by itself.
- The Reel deleted at each of six statuses: failed with "The Reel was deleted", active jobs refunded, the next topic can start. The account disabled or deleted, and a purged project: failed.
- AP-7: the creator fixing a line, generating and rendering by hand is carried on from (one render, no second set of voices); an edit after the render is rendered again; publishing by hand finishes the run as published; a scene switched to "upload" with no file fails the run with "Some scenes need you" and does not count as an edit (QAP-003).
- Another of the creator's Reels rendering: the run waits with no error and goes on afterwards.
- One run's unexpected fault does not stop the others in `advance_all`, costs nothing, and the run goes on once the fault is gone.
- A run that has not finished after 4 hours is failed (3 h 55 min is not), also while publishing; the next topic can start.

**L6 approve and L7 skip**
- Only a waiting topic (`409 not_waiting` for the six other statuses); twice publishes once.
- Publishes the saved title, caption and hashtags with each of the three visibilities; `<` and `>` are stripped from all three; a title of nothing but brackets falls back to the legend's name.
- After the creator edits the Reel (a new voice needed, or only the timeline): `409 render_outdated`, nothing uploaded, the Reel is made again and waits once more, then publishes (QAP-002). Changing only the post text is not an edit.
- A Reel already published by hand becomes `published` (200) with no second copy. Hashtags YouTube would refuse are `409 post_text_invalid`, not a crash, and in automatic mode the Reel waits with that reason. A deleted Reel is 404.
- Skip keeps the Reel, clears the reason, publishes nothing.

**The database**: the checks on both tables; the autopilot goes with the account; a purged Reel leaves the run (`project_id` null). Migration 0003 down to 0002 and up again with autopilot rows and jobs present: the tables go, `autopilot_topics` jobs are deleted and every other job stays, `jobs_type_check` refuses the type after the downgrade and accepts it (and the nine older types) after the upgrade.

### Covered: frontend

- **States**: off and on (with the local time); no channel and a channel that needs reconnecting (the link to Settings); the saved settings on every control; the 24 hours with their UTC labels; "Week (days in UTC)"; an empty queue; the queue list; a waiting Reel (video, title, story, Publish, Skip, Edit the Reel) and its error; no title or video yet; two waiting; the runs list for each status with its date, reason, YouTube link or Open link, Stop only on a running run; the load error.
- **On and off**: `{enabled: true}` and `{enabled: false}`; the `409 not_connected` reason with the switch still off; the button busy while saving (no second request).
- **Settings**: Fully automatic asks first, sends `{mode: "auto"}`, does nothing when declined or already automatic; back to approve without a question; the hour as a number; one day of the rotation only; the three sports; length; visibility; a refused setting shows the field's message and keeps the saved value.
- **Suggest**: the job is polled, the button is busy meanwhile, "3 topics added" and "1 topic added", the queue is read again; a failed job's reason, the plain message when it has none, a refusal to start (no polling), an error cleared by the next try.
- **Add and remove**: both fields needed (spaces don't count, Enter doesn't get round it); the field limits; the trimmed body with the chosen sport, the emptied form; Enter adds; `409 not_added` and a field error are announced (`role="alert"`) and the text stays; Remove sends the DELETE and reads the queue again; a failed removal says so.
- **Run now**: disabled with an empty queue; the cost line; a start moves the topic to the runs; five refusals shown under the button (already running, the topic's own reason, queue empty, unverified, out of credits with the renewal date).
- **Publish, Skip, Stop**: the right topic's endpoint; refusals on the card with the Reel still waiting; Skip and its error; Stop with its confirm (declined sends nothing); removing failed and skipped runs; a failed Stop says so.
- **Polling**: every 5 s while a run is running or publishing; once a minute otherwise (nothing run, a Reel waiting, all ended); slows when the run ends, speeds up when the daily run starts; stops when the page is left.
- **`localHour`**: UTC, India (+5:30), Japan and Arizona, including hours that fall on the next or previous day; 24 different times.
- **The nav item**: Autopilot between Projects and Settings, to `/app/autopilot`, marked current when open.

### Not covered yet

- **Real browsers.** No Playwright: the video player, the layout on a phone, `window.confirm` as the creator sees it.
- **The mock API (MSW)** has no autopilot handlers (QAP-021), so the page cannot be opened in mock mode and there is nothing of it to test there.
- **Rate limits**: the hourly language-model limit at the start of a run and at the post text (limits are off in the default test app). The post-text fallback is tested with the model switched off, which takes the same branch.
- **Real concurrency**: two workers advancing one topic, L6 racing `advance`. One overlap is tested deterministically (BUG-092).
- **Real providers**: whether the models obey the rules for real people; only that the rules and the image rule are sent where they should be.
- **The stock search**: that a `stock_query` naming the legend is not sent to the stock sites (nothing in the code cleans it; see observations).
- **`privateUntilVerified`** on a run (QAP-014), time zones other than UTC for the weekday (QAP-012), L1's cost with hundreds of runs (QAP-009).

## 3. Bugs

| Bug | Severity | Area |
|---|---|---|
| BUG-092 | Medium | Two starts at the same moment make two Reels for one topic and charge two scripts |
| BUG-093 | Low | An unexpected fault at the daily start uses up the day, with nothing shown |
| BUG-094 | Low | Removing the legend's name from an image prompt breaks other words ("Lonthe athlete") |
| BUG-095 | Low | The screen is not refreshed after a refusal that changed the state: the old video keeps its Publish button, a failed topic stays in the queue |
| BUG-096 | Low | "0 topics added. Remove any you don't want." |

### BUG-092 Medium: two starts at the same moment make two Reels for one topic

- **Test:** `backend/api/tests/test_autopilot_full.py`, `test_two_starts_at_the_same_moment_make_one_reel`.
- **Spec:** L8 "one run at a time"; "Nothing is charged differently".
- **Steps:**
  1. One topic in the queue.
  2. `POST /autopilot/run`. While that request is between choosing the topic and creating its project, a second start for the same creator arrives (a second tab, or the hourly task at the moment the button is pressed). The test makes the second start run exactly there.
- **Expected:** one project, one script job, 1 credit; the second start finds the topic taken.
- **Actual:** 2 projects and 2 script jobs for the one topic, 2 credits charged. Both starts report "running". The topic points at the second project; the first is an orphan that nothing advances.
- **Cause:** `start_next` (changed for QAP-011) now commits after choosing the topic (`topic.run_on = today.date(); db.session.commit()`), which lets go of the settings-row lock while the topic is still `queued`. It takes the lock again and creates the project. A second call that gets the lock in between sees no `running` topic and picks the same one. Before the change the lock was held from the check to the commit that set `running`. For the scheduled start the re-check of `last_run_on` closes the gap between two hourly tasks, but not between the task and the button, or two presses.

### BUG-093 Low: an unexpected fault at the daily start uses up the day

- **Test:** `test_autopilot_full.py`, `test_start_due_tries_again_at_the_next_check_after_an_unexpected_fault`.
- **Spec:** AP-6 (a missed start happens at the next hourly check); the comment in `start_next` says the day counts "so a failure isn't repeated every hour", which is about refusals that are recorded on the topic.
- **Steps:**
  1. Autopilot on, one topic queued.
  2. `start_due` runs and something other than a refusal goes wrong while the project is being created (the test raises a `RuntimeError`; in production a database or Redis hiccup).
  3. `start_due` runs again an hour later, the fault gone.
- **Expected:** the Reel starts at the second check. Nothing was made and nothing was recorded the first time.
- **Actual:** nothing starts until tomorrow. The topic is still queued, there is no failed run and no reason anywhere on the screen.
- **Cause:** the same early commit as BUG-092: `last_run_on` is saved before the project is created, so the rollback in `start_due`'s `except` no longer undoes it. Before the fixes the two were one transaction (the QA report lists that as a pass).

### BUG-094 Low: removing the legend's name breaks other words in the image prompt

- **Test:** `test_autopilot_full.py`, `test_removing_the_legends_name_leaves_other_words_whole`.
- **Steps:** an autopilot Reel about "Don Bradman"; a scene's visual prompt is "Don Bradman's bat, a London crowd, an abandoned pavilion".
- **Expected:** the name goes; "a London crowd, an abandoned pavilion" reaches the image model as written.
- **Actual:** "the athlete the athlete's bat, a Lonthe athlete crowd, an abanthe athleteed pavilion".
- **Cause:** `autopilot.image_prompt_for` replaces every part of the name longer than 2 letters with `re.sub(re.escape(name), …, flags=IGNORECASE)` and no word boundary, so "Don" is replaced inside "London" and "abandoned" (likewise "Ian" in "Brazilian", "Ali" in "Italian", "Max" in "maximum"). It also loops over a `set` of the full name and its parts, so "Don Bradman" becomes "the athlete the athlete" or "the athlete" depending on the set's order in that process. Match whole words, and the full name first.

### BUG-095 Low: the screen keeps what the server has just changed after a refusal

- **Tests:** `frontend/src/features/autopilot/AutopilotPage.test.tsx`, "Publish after an edit (409 render_outdated): says a new video is being made and stops offering the old one" and "a start that failed puts the failed topic on the screen without waiting for the next poll".
- **Steps (publish):** a Reel waits; the creator edits it and presses **Publish**. L6 answers `409 render_outdated` and has put the topic back to `running`.
- **Expected:** the message, and the card with the old video and its Publish button goes; the run shows as "Being made".
- **Actual:** the message shows, but the old video, **Publish** and **Skip** stay for up to a minute (the next slow poll). Pressing Publish again answers "This Reel isn't waiting to be published". When the poll does arrive the card is removed and the explanation goes with it.
- **Steps (run now):** one queued topic, no credits, **Make the next one now**. L8 answers `409 not_started` with the reason and has marked the topic failed.
- **Expected:** the queue shows 0 topics and the failed run is listed.
- **Actual:** the topic is still shown as next in the queue, and the button stays enabled, until the next poll.
- **Cause:** `useChange` in `src/api/autopilot.ts` updates the cached state only in `onSuccess`. Both refusals change the state on the server (since the QA fixes), and nothing invalidates the `['autopilot']` query on an error. The slower 60-second poll added for QAP-020 is the only thing that corrects it.

### BUG-096 Low: "0 topics added. Remove any you don't want."

- **Test:** `AutopilotPage.test.tsx`, "nothing new came back: does not invite the creator to remove topics that were not added".
- **Steps:** press **Suggest two weeks** when every suggestion is already queued, or the queue is full (the job succeeds with `added: 0`; the mock does this on every second press).
- **Expected:** a sentence that says nothing new was added.
- **Actual:** "0 topics added. Remove any you don't want.", in the green success style.
- **Cause:** the QAP-022 fix handles 1 ("1 topic added") but not 0; `Remove any you don't want` is appended whatever the number.

### Other observations (not bugs)

- **A run under way at the hour still uses up the day.** `start_next(scheduled=True)` marks `last_run_on` and then finds a run in progress: nothing starts and nothing is shown (`test_start_due_does_not_start_a_second_run_while_one_is_under_way` checks only that no second run starts). The 4-hour limit now ends a stuck run, so this can cost one day, not every day.
- **L5 on a `publishing` run** removes the row but, from the code, does not stop the publish job already queued; the Short still goes to YouTube and the run is gone from the list. The screen offers Stop only on `running` runs, so this needs a direct API call. Not tested.
- **Stop does not cancel the Reel's active jobs** (a voice or render in progress finishes and is charged). The confirm text says "The Reel stays in your library as it is", which fits.
- **Removing a run takes the rules with it.** `is_autopilot` looks for a topic row; after L5 the Reel is no longer an autopilot Reel, so a regenerated script and new AI images have no rules. Read from the code, not tested.
- **`stock_query` is not cleaned** of the legend's name; only the script rule asks the model not to write it. Read from the code.
- **Line breaks in a model's answer join two words**: `job_types.clean` drops control characters before it collapses spaces, so "The turn\nin 1974" is stored as "The turnin 1974". Older than this feature and true of every language-model job.
- **Suggestions repeat finished runs.** The duplicate rule looks at queued topics only; the offline mock returns the same 14 each time, so after a topic has run, the next Suggest queues it again. With a real model the "avoid" list is what prevents it.
- **The screen offers 15, 30 and 60 seconds** (the API takes 90; with 90 saved no chip is selected) and has no control for the tone.
- **The starter file `tests/test_autopilot.py` does not clear the tables** in most of its tests. They pass because each creates its own user, but `advance_all` there also advances runs left by earlier tests.
- **AUTOPILOT.md** is dated 2026-11-29 and still describes the build before the fixes (rules inside the topic, L5 "Remove a queued topic", no `render_outdated`, `post_text_invalid` or the 4-hour limit).

## 4. Flaky or skipped

- None skipped, no `xfail`, no `it.todo`.
- **Frontend**: the whole suite twice and the autopilot file three times, the same 3 failures each time.
- **Backend**: after the fixes the two autopilot files were run three times (twice on their own, once inside the full suite): the same 3 failures each time. The machine was busy: the same 226 tests took 2 minutes on one run and 8 min 40 s on another, with the same result.
- **Discarded backend runs.** The first two runs of the new file were made while the QA fixes were being written: one died with `relation "users" does not exist` (another session's migration test had the shared test database), one failed on `autopilot.RULES`, which the fixes removed. The tests were then brought up to the fixed behaviour; five failures of the first round (the story cut at 140 characters, L8's wrong reason, a failed start listed last, the run stuck on an upload scene, Publish sending the old video) are the QA report's QAP-008, QAP-004, QAP-003 and QAP-002, are fixed, and their tests now pass.
- The migration test downgrades the shared test database to 0002 and upgrades it again inside a `try`/`finally`, as the older migration tests do. A second pytest running at that moment would fail; none was.
