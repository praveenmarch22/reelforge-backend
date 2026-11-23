# ReelForge: QA report for F4 (onboarding)

| | |
|---|---|
| Date | 2026-11-23 |
| Milestone | F4 Onboarding: niche, voice (with previews), connect Instagram; skippable |
| Build tested | `frontend/` working tree on 2026-11-23 (F4 not yet committed), dev server at http://localhost:5173, mock API on |
| Browser | Playwright Chromium only |
| Test cases | [TEST_CASES_F4.md](TEST_CASES_F4.md) |
| Plan | [TEST_PLAN.md](TEST_PLAN.md) |

## Verdict: pass with issues

The milestone's "done when" line is met, checked by running: a new user who verifies their email is sent to onboarding, every step and the whole setup can be skipped, skipping saves nothing it should not, the chosen niche and voice reach the profile, and once finished or skipped the setup never appears again. The Instagram round trip works for both account types and for the three documented failures. The F1–F3 flows still work with the gate in place.

There is no Blocker. There is one Major finding, and it is in the rule for `returnTo`, not in what a user can do today: the rule as written in the API design and as built in the mock lets a crafted value send the browser to another site. Nothing is exposed now, because only the mock implements it and the web app always sends a fixed value, but the backend will be built from that sentence, so it should be corrected before then.

| Severity | Count | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 1 | QA-026 |
| Minor | 7 | QA-027 to QA-033 |
| Polish | 3 | QA-034 to QA-036 |
| Looked at early for F9, not counted in the verdict | 2 | QA-037, QA-038 |
| Questions | 6 | Section 5 |

**"Verified" column**: *Run* means reproduced in the browser. *Read* means concluded from the code only. File and line references are where I believe the cause is; I did not change any code to confirm them.

**Limits of this review**

- I cannot see the Stitch images. Screens 6, 17 and 18 have no written prompt in the Stitch document, only a row in its table, so the design comparison is against the PRD wording and the one line in the UI review ("Business or Creator account needed" note).
- Chromium only.
- Instagram is the stand-in page at `/mock/instagram`. Instagram's real screens, the real callback, the 10-minute life of `state` and token storage were not tested.
- API failures and delays were injected by wrapping `fetch` in the page.
- The status document still lists F4 as "Not started". The plan's entry criterion (status says done) was not met; I reviewed the working tree as asked.
- The Tester's suite was running against the same dev server. I did not run or read its results.

---

## 1. Findings

### Major

#### QA-026: The `returnTo` rule lets a crafted value send the browser to another site

| | |
|---|---|
| Requirement | API F1: `returnTo` "must be a path inside the web app starting with a single `/`, otherwise it is ignored" |
| Verified | Run (against the mock API; other sites were answered by the test, nothing left the machine) |
| Believed cause | `src/mocks/db.ts:220-221` (`safeReturnTo`), and the wording of API F1 itself |

Steps:
1. As a new verified user, call `GET /api/v1/instagram/connect?returnTo=` with the value URL-encoded.
2. Open the returned `authorizeUrl` and choose any answer.

| `returnTo` sent | Stored by the mock | Browser ends on |
|---|---|---|
| `//evil.example`, `https://evil.example`, `\\evil.example`, `javascript:alert(1)` | `/app/settings` (ignored) | the app |
| `/\evil.example` | as sent | `http://evil.example/?instagram=…` |
| `/\/evil.example` | as sent | `http://evil.example/?instagram=…` |
| `/` + tab + `/evil.example` | as sent | `http://evil.example/?instagram=…` |
| `/` + line feed + `/evil.example` | as sent | `http://evil.example/?instagram=…` |

Expected: all of these are ignored and the default is used.
Actual: the last four pass the check (`startsWith('/') && !startsWith('//')`) and the browser leaves the app. Browsers read `\` as `/` in web addresses and drop tabs and line breaks, so each of these is `//evil.example` by the time it is followed. All four also satisfy the sentence in the API design ("starting with a single `/`"), so the document needs the stricter rule, not only the mock.

Why Major although nothing is exposed today: the web app only ever sends the fixed `/onboarding?step=3` (`src/features/onboarding/InstagramStep.tsx:113`), and the mock is not in production builds (section 2). But API F2 is a public address that redirects to whatever `returnTo` the `state` carries, and the real backend will be written from the F1 sentence. Someone who starts a connect with their own `returnTo` could then hand out a link on the product's own domain that ends on their site.

Smaller gaps in the same rule, also Run:

- **Fragment**: `returnTo=/app#frag` gives `/app#frag?instagram=error&reason=denied`; the result is inside the fragment and the page never sees it (`src/mocks/db.ts:240`).
- **Result already present**: `returnTo=/onboarding?step=3&instagram=connected` gives two `instagram` values; the page reads the first.
- **Length**: a 20,000-character `returnTo` is stored and redirected to unchanged.

### Minor

#### QA-027: A failed connect with a missing, wrong, used or replaced `state` drops a user who is still in onboarding on step 1 with no message

| | |
|---|---|
| Requirement | API F2 ("if `state` is missing or invalid it redirects to `/app/settings?instagram=error&reason=failed`"); PRD flow 2 step 5 ("show the reason and how to fix it") |
| Verified | Run |
| Believed cause | `src/features/auth/guards.tsx:33` (the gate redirects to a bare `/onboarding` and discards the query string), together with the fixed fallback address in API F2 (`src/mocks/db.ts:237`) |

Steps (two-tab version, which needs no hand-made address):
1. New verified user. Open `/onboarding?step=3` in two tabs.
2. Click Connect Instagram in tab 1, then in tab 2. The second start replaces the first `state`.
3. In tab 1 choose "Allow (Creator account)".

Expected: the user is told the connection did not work and can try again.
Actual: tab 1 goes to `/app/settings?instagram=error&reason=failed`, the gate sends it on to `/onboarding`, and the user is looking at step 1 "What do you post about?" with no message and no connection. The same landing happens with no `state`, an empty or wrong one, one already used, and when F1 is called without `returnTo`.

With the real backend the everyday trigger is the 10-minute life of `state`: anyone who takes longer than that on Instagram's login screen (two-factor codes, a forgotten password) comes back to step 1 with no explanation. For a user who has finished onboarding the same redirect correctly reaches Settings (the F8 placeholder today).

#### QA-028: `/onboarding?step=3&instagram=error&reason=__proto__` shows a blank page

| | |
|---|---|
| Requirement | API F2 (the four `reason` values); an unknown reason is meant to fall back to the general message, as it does for `reason=banana` |
| Verified | Run |
| Believed cause | `src/features/onboarding/InstagramStep.tsx:17` (`value in REASONS` is also true for names every object inherits), rendered at line 69 |

Steps: as a new verified user, open that address.

Expected: "Something went wrong while connecting. Please try again."
Actual: React throws "Objects are not valid as a React child" and the whole page is blank; there is no error screen to recover from. `reason=toString`, `constructor` and `hasOwnProperty` do not crash but show "We couldn't connect your account" with no reason under it.

Only a hand-made address does this; the API's own values are fine.

#### QA-029: "Skip setup" does not wait for a request that is still in flight, and the late answer undoes it

| | |
|---|---|
| Requirement | Milestone F4 ("new users see onboarding once and can skip it") |
| Verified | Run (answers held or delayed by 2.5 s) |
| Believed cause | `src/features/onboarding/OnboardingPage.tsx:86` (the link is disabled only while its own request runs), `src/api/hooks.ts:87` (any `PATCH /me` answer overwrites the cached user, whatever order it arrives in), `src/api/hooks.ts:110` (the connect answer navigates even after the user has left) |

(a) Steps: step 1, pick Fitness, click Continue; before the save answers, click Skip setup.

Expected: the dashboard, and it stays.
Actual: the dashboard appears, then about two seconds later the user is back on onboarding step 1. The server has `onboardingCompleted: true`; the late answer to the niche save (made before the skip) replaced the cached user with an older copy, and the gate acted on it. A reload puts it right.

(b) Steps: step 3, click Connect Instagram; before it answers, click Skip setup.

Expected: the dashboard, and it stays.
Actual: the dashboard appears, then the browser is taken to Instagram's approval page.

Both need a slow answer and a quick second click. On step 1 the topic buttons and the text field also stay live during a save, and a change made then is silently not saved (Run).

#### QA-030: Stopping or switching a voice sample quickly shows "Couldn't play this sample." although nothing failed

| | |
|---|---|
| Requirement | PRD flow 1 step 4.2 ("pick a default voice (with previews)"); API D3 |
| Verified | Run |
| Believed cause | `src/features/onboarding/VoiceStep.tsx:46-49` (every rejection of `play()` is treated as a failure, including the one the browser raises when the sample is paused before it has started) |

Steps: on step 2, double-click a play button. Or click Play on one voice and, before it starts, Play on another.

Expected: the first sample simply stops.
Actual: "Couldn't play this sample." appears in red under the first voice and stays until another sample is started.

In mock mode the samples are embedded in the page and start almost at once, so it takes a double-click. Real samples are fetched over the network; the gap between clicking Play and sound starting will be much longer, and a user flicking through six voices will see the error often. A sample that really is broken shows the same message, correctly (Run).

#### QA-031: Step 1 does not hold to "up to three topics" for free text or for an existing value that already breaks the rule

| | |
|---|---|
| Requirement | API A10 ("one text value of up to 120 characters; the web app stores up to three topics in it, separated by commas") |
| Verified | Run |
| Believed cause | `src/features/onboarding/NicheStep.tsx:43-57` (`splitNiche`, `joinNiche`), `:65` (the count), `:113` (the cut to 40), `:132` (Continue disabled at 0) |

| # | Steps | Expected | Actual |
|---|---|---|---|
| a | Type "knitting, pottery, chess, origami" in "Something else", Continue | At most three topics stored | Counted as "1 of 3"; four topics stored. The count would still allow two chips on top (that part by reading) |
| b | Choose the Fitness chip and also type "Fitness" | One topic | "Fitness, Fitness" stored; on return one chip is lit and the count says "2 of 3" |
| c | Profile already holds "Fitness, Finance, Tech, Food" (set through the API); open step 1 | The screen copes | Four chips lit, "4 of 3 chosen", Continue enabled and saves all four again |
| d | Profile already holds 120 characters of free text; open step 1 and press Backspace once | Kept, or the user is told | Silently cut from 120 to 40 characters |
| e | Save Fitness, press Back, deselect it | The niche can be cleared | Continue is disabled at "0 of 3"; "Skip for now" keeps "Fitness". There is no way to clear it until Settings exists |

(c) and (d) cannot be reached through the app today, because onboarding is the only screen that writes the niche; they will be once Settings (F8) or another client does. A value in other letter case ("fitness") is treated as free text, which is harmless. See question 3 for the parts the documents leave open.

#### QA-032: The 40-character cut can split an emoji, and the broken half is sent to the API

| | |
|---|---|
| Requirement | API A10 (`niche` is text) |
| Verified | Run |
| Believed cause | `src/features/onboarding/NicheStep.tsx:113` (`slice(0, 40)` counts UTF-16 units, not characters) |

Steps: paste 39 letters followed by 🌱 into "Something else", Continue.

Expected: the emoji is kept whole or left out.
Actual: the field holds 39 letters and half of the emoji, and the request body is `{"niche":"aaa…a\ud83c"}`. The mock stores it. A real JSON parser or PostgreSQL is likely to refuse it, and the user would then see "Please check the form." with no hint which part is wrong, because the step shows only the general message of a `400` and not `details.fields.niche` (Run, with a forced `400`).

#### QA-033: After pressing Back on Instagram's page, the Connect button can stay stuck in its loading state

| | |
|---|---|
| Requirement | PRD flow 2 steps 1–3; flow 1 step 4 (step 3 stays skippable) |
| Verified | Read. I tried to reproduce it with Chromium's back/forward cache switched on, but the dev server's live-reload connection and the mock service worker make the page ineligible for that cache, so here Back reloads the page and the button is fine |
| Believed cause | `src/api/hooks.ts:110-112` (after `location.assign` the request is kept pending for ever, on the assumption the page is gone), `src/features/onboarding/InstagramStep.tsx:101,110` (Back and "I'll do this later" are disabled while it is pending) |

Reasoning: in a production build neither of those two blockers exists. When a user clicks Connect, changes their mind on Instagram's page and presses the browser's Back button, current Chrome, Safari and Firefox normally restore the previous page exactly as it was left, with its JavaScript state. The connect request is still "pending", so Connect shows a spinner and Back and "I'll do this later" are disabled, with nothing that will ever end it. "Skip setup" in the header still works, and a reload clears it. Worth one check on the first production-like build.

### Polish

#### QA-034: Skipped steps are shown as done

| | |
|---|---|
| Requirement | PRD flow 1 step 4 (steps are skippable; skipping is not completing) |
| Verified | Run |
| Believed cause | `src/features/onboarding/OnboardingPage.tsx:24` (`done = n < current`) |

A new user who opens `/onboarding?step=3`, or who uses "Skip for now" twice, sees Niche and Voice with a tick, and a screen reader hears "Niche (done)", "Voice (done)", although nothing was chosen or saved.

#### QA-035: A failed finish leaves its error on the other steps

| | |
|---|---|
| Requirement | API error format (errors shown where they happen) |
| Verified | Run |
| Believed cause | `src/features/onboarding/OnboardingPage.tsx:60,96` (the message is cleared only by the next attempt to finish) |

Steps: on step 3, with the connection dropped, click "I'll do this later" or Skip setup; then click Back.
Actual: "Can't reach ReelForge…" is still shown above step 2, and above step 1 after another Back.

#### QA-036: Wording on step 3 differs from the PRD

| | |
|---|---|
| Requirement | PRD flow 1 step 4.3 ("Connect Instagram, or **Skip for now**"); PRD flow 2 step 5 ("show **Connected as @username**") |
| Verified | Run |
| Believed cause | `src/features/onboarding/InstagramStep.tsx:54,60,111` |

The skip button on step 3 reads "I'll do this later" (steps 1 and 2 say "Skip for now"), and the success state reads "Instagram connected" with "@username" in a pill below. Both are clear; they are listed because the PRD puts its wording in bold. If screen 18 in Stitch uses the built wording, the PRD is the one to change.

### Looked at early for F9 (not counted in the verdict)

#### QA-037: Keyboard and screen-reader notes

| | |
|---|---|
| Requirement | Milestone F9 (keyboard and screen-reader pass) |
| Verified | Run |

All three steps, the Instagram round trip and Finish can be done with the keyboard alone; focus is visible everywhere, including on the hidden radio inputs (the ring is drawn on the label); arrow keys move between voices; roles and names are sound (test case TC-F4-156). Three things for the F9 list:

- **Focus is lost when the step changes.** After Continue, Back or Skip for now the button that had focus is gone and focus falls to the page (`document.body`). A keyboard user starts again from "Skip setup"; a screen-reader user is not told the step changed. Nothing moves focus in `src/features/onboarding/OnboardingPage.tsx:66` (`goTo`).
- **Enter in "Something else" does nothing.** The field is not in a form (`NicheStep.tsx:110`), so the user has to tab past "Skip for now" to reach Continue.
- **The page title is "ReelForge" on every step** (per-page titles were already deferred to F9 in the F1–F3 fix log).

#### QA-038: Phone-width notes

| | |
|---|---|
| Requirement | PRD section 12 ("usable on phone width") |
| Verified | Run (screenshots looked at for 390×844, 320×568, 768×1024, 844×390; touch emulation with an iPhone 13 profile) |

At 390×844, 768×1024 and 844×390 every step, the error state and the connected state fit with no sideways scroll, and everything works by touch. Notes:

- **At 320 px wide step 3 scrolls sideways** (page 344 px wide): the stepper is wider than the card and its first tick is cut off, and "Connect Instagram" sticks out of the card on the right. Steps 1 and 2 overflow by one pixel.
- **"Skip setup" is a 65×20 px touch target** at phone width; "How to switch" is 88×20.
- **Only the name row of a voice card selects it.** Tapping the upper part of the card, beside the play button and the bars, does nothing, although the whole card is drawn as the choice (`VoiceStep.tsx:109-133`).
- On step 3 at 390 px the buttons wrap into two rows, with Back alone on the first.

---

## 2. What passed

**Verified by running** (cases in [TEST_CASES_F4.md](TEST_CASES_F4.md))

| Area | Result |
|---|---|
| Shown once | A new verified user is sent to `/onboarding` from every app route, from `/`, and from the login, sign-up and verify pages. After finishing or skipping, `/onboarding` redirects to `/app`, also after browser Back and after logging out and in again. The demo account never sees it |
| Skipping | "Skip for now" on steps 1 and 2 sends no request, even with choices made; those choices are gone on return. "I'll do this later" and "Skip setup" send only `{"onboardingCompleted": true}`. A niche saved earlier survives a later Skip setup |
| Saving | `PATCH /me {"niche": "Fitness, Finance, gardening"}` and `{"defaults": {"voiceId": …}}`; `GET /me` shows both afterwards, with caption style and mode untouched. Free text is trimmed; emoji and `<b>` text come back exactly as typed |
| Step limits | Three topics lock the rest and the text field; Continue is disabled with nothing chosen, with only spaces, and on step 2 until a voice is picked |
| History | Reload keeps the step on all three. Back and Forward move between steps and show what was saved. Unknown `step` values open step 1. `/onboarding?step=3` opens directly for a new user |
| Voices | Six voices from `GET /voices`; one sample plays at a time; the control resets when the sample ends; leaving the step by any of five routes stops the sound; a really broken sample shows its message |
| Instagram | Request is `GET /instagram/connect?returnTo=%2Fonboarding%3Fstep%3D3`. Creator and Business end on step 3 with "@username" and Finish. Personal account, denied and not-a-tester each show their reason, store nothing, and offer Try again; personal account also shows how to switch (PRD IG-1, IG-11, flow 2). Unknown `reason` values show the general message. A stale error address does nothing once connected. A connect started in one tab can be finished in another |
| Failures | `500` and a dropped connection on each of the step-1 save, the voice save, finishing, `GET /voices` and `GET /instagram/connect` show a message, keep the user's choices and leave the buttons usable; retry works. `403 email_not_verified` shows the API's message |
| Double clicks | Two clicks 80 ms apart on Continue, "I'll do this later", Skip setup and Connect send one request. Two clicks with no gap at all (synthetic) send two identical requests; harmless, since the saves are repeatable and the newer connect is the one used |
| Session | Logging out in another tab, then Continue (step 1), entering step 2, or Connect (step 3): the user goes to the login page and returns to the same step after logging in |
| Regression F1–F3 | Sign up → verify → onboarding; a used verification link; unverified user stays in the app with the banner; demo login; return to the requested page after login for a user who has finished onboarding; `GET /me` failure screen with Try again |
| Production build | Built with mocks off into the QA scratch folder: no trace of `/mock/instagram`, the mock page, the mock database key or the demo password in the bundle |
| Type-check | `tsc --noEmit` passes |

**Concluded by reading**

- `src/lib/env.ts`: in a production build `USE_MOCKS` is true only when `VITE_USE_MOCKS` is exactly `"true"`. `src/App.tsx:18,37` registers `/mock/instagram` only then, and `finishInstagramConnect` is imported only by that page. The build check above agrees.
- The mock `GET /voices` returns the D3 fields; the mock F1 checks login and verified email, applies the default `returnTo` and stores it with the `state`; the stand-in for F2 follows the documented result parameters and fallback address. Not built into the mock: the 10-minute expiry of `state`.
- `Voice` and `InstagramErrorReason` in `src/api/types.ts` match D3 and F2.
- Reduced motion: the global rule in `src/index.css` switches off the bar animation and the spinner; playing is still shown by icon and colour (also Run).

**Observations with no requirement behind them** (not findings)

- If a different user logs in on a second tab, the first tab's Continue saves its niche into that other user's profile (Run: the demo account's niche became "Fitness"). Any page that outlives its session behaves like this; noted because Settings will have more such forms.
- A saved `defaults.voiceId` that is no longer in the voice list shows no selection, but Continue is enabled and saves the old id again (Run).
- An empty voice list shows an empty area with no message (Run, forced).
- A `200` from F1 without `authorizeUrl` sends the browser to `/undefined` and the 404 page (Run, forced).
- After connecting in one tab, another tab already on step 3 keeps offering "Connect Instagram" until it is reloaded (Run).

---

## 3. Not tested

| What | Why |
|---|---|
| Instagram's real approval screens, the real F2 callback, token storage | No backend; the stand-in page plays both parts |
| `state` expiring after 10 minutes | The mock never expires it. The landing would be the one in QA-027 |
| The stuck Connect button (QA-033) | Needs the browser's back/forward cache, which the dev server and the mock service worker switch off |
| Real voice samples: loading time, formats, failures | Mock samples are generated tones embedded in the page |
| Server-side validation of `niche` (120 characters) | By decision the mock does not validate A10 (F1–F3 fix log, QA-013) |
| Edge, Safari, Firefox | Only Chromium is installed |
| Pixel comparison with Stitch screens 6, 17, 18 | Cannot see the designs |
| The Tester's automated suite | Owned by the Tester |

## 4. Out of scope (not defects)

- Unverified users are not sent to onboarding (status document, open question 6).
- Voice samples in mock mode are tones, not speech.
- The Instagram result dialogs (design screens 21, 22) belong to F7/F8; onboarding shows the result inline.
- Disconnecting Instagram and the profile picture (PRD I2) belong to Settings (F8).
- Default language, caption style and mode (PRD A4) are not asked in onboarding; Settings (F8). See question 1 for language.
- Settings and the other later screens are placeholders.

## 5. Questions

| # | Question | Why it matters |
|---|---|---|
| 1 | PRD flow 1 step 4.1 says "Pick a niche **and language**", and A4 lists a default language. PRD section 16 (Decisions) says English only in version 1, and neither the `User` object nor A10 has a language field. Step 1 asks for the niche only. Is that the decision, and should the PRD line change? | Otherwise step 1 is missing half of what the flow describes |
| 2 | API F2 is public and trusts the user inside `state`. In the mock, user A started a connect, user B then logged in on the same browser, opened A's approval link and approved: A's account was connected to the Instagram account B approved, and B landed on step 3 with `instagram=connected` in the address but nothing connected and no message. Should F2 also require that the signed-in user is the one in `state`, and what should the other user be shown? | With a real backend the same thing links one person's Instagram to another person's ReelForge account if an approval link is passed on |
| 3 | A10 says the web app stores up to three topics separated by commas. Is the free-text box one topic (so commas should be refused or replaced), or may it hold several (so it should count them)? Is its 40-character limit agreed (it is not in the documents)? And what should the screen do with an existing value that already breaks the rule? | Settles QA-031 |
| 4 | An unverified user can open `/onboarding` by typing the address, save a niche and voice, and use Skip setup; onboarding is then marked done and never shown after they verify. Connect answers "Verify your email before connecting Instagram." Should `/onboarding` be closed to unverified users? | Belongs with open question 6 in the status document |
| 5 | PRD I2 asks for the connected account's username **and profile picture**. `InstagramStatus` in the API design has no picture field, and the onboarding success state shows the username only | Needed before the Settings Instagram tab (F8), and decides whether step 3 should show a picture |
| 6 | A user who has not finished onboarding and logs in from a link to an app page (for example `/app/projects?x=1`) goes through onboarding and then lands on `/app`; the page they asked for is forgotten. Intended? | PRD A-4 says "return to the same project". Today such a user cannot have projects, so nothing is lost; it starts to matter if onboarding is ever shown to existing users again |

## 6. Evidence

Scripts, their output and screenshots are in the QA scratch folder (temporary, not in the repository): `lib.cjs` (helpers and the fetch wrapper), `t01-main.cjs`, `t02-skip-nav-niche.cjs`, `t03-failures.cjs`, `t03b-double.cjs`, `t04-instagram.cjs`, `t04b-reasons.cjs`, `t05-voice-session-regression.cjs`, `t05b-sample-error.cjs`, `t06-responsive-keyboard.cjs`, `t07-bfcache.cjs`, the `out-*.txt` files and `shots/`. Run a script from that folder with the frontend's Playwright on `NODE_PATH`, as described in the QA role file:

```
node t04-instagram.cjs
```

A scenario name prefix can be given to run one scenario, for example `node t04-instagram.cjs G`.
