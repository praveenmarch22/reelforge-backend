# ReelForge: test cases for F4 (onboarding)

| | |
|---|---|
| Last run | 2026-11-23, Chromium, dev server with the mock API |
| Report | [QA_REPORT_F4.md](QA_REPORT_F4.md) |
| Plan | [TEST_PLAN.md](TEST_PLAN.md) |

**How to read this**

- "New user" means an account created in the same browser and verified, with onboarding not yet done. "Demo" means the demo account in `frontend/src/mocks/db.ts` (onboarding already done).
- Each case starts in a fresh browser context (empty storage) at 1280×800 unless it says otherwise.
- "Forced" means the API answer is simulated by the fetch wrapper described in the test plan. "Held" means the real answer is delivered late.
- "Profile" means what `GET /me` returns after the step.
- Result: **Pass**, **Fail** (with the finding id), **Read** (checked by reading code only), **N/T** (not testable in this environment).
- Cases marked ⚙ are good candidates for the automated suite.

## Gate: shown once, and the F1–F3 flows with the gate in place

| ID | Requirement | Steps | Expected | Result |
|---|---|---|---|---|
| TC-F4-001 ⚙ | Milestone F4; PRD flow 1 steps 3–4 | Sign up through the form, open the verification link | Lands on `/onboarding`, step 1 "What do you post about?" | Pass |
| TC-F4-002 ⚙ | Milestone F4 | New user opens `/`, `/app`, `/app/projects`, `/app/new`, `/app/settings`, `/admin`, `/app/p/x/edit`, `/login`, `/signup`, `/verify` | Each ends on `/onboarding` | Pass |
| TC-F4-003 ⚙ | Milestone F4 ("once") | Finish onboarding, then open `/onboarding` and `/onboarding?step=2`; press browser Back; log out and log in again | Always `/app`; onboarding never shown again | Pass |
| TC-F4-004 ⚙ | Milestone F4 | Demo logs in; then opens `/onboarding` | `/app` both times | Pass |
| TC-F4-005 | Status document, open question 6 (decided) | New unverified user opens `/app` | Stays in the app with the verify banner; not sent to onboarding | Pass |
| TC-F4-006 | Milestone F4; status document, open question 6 | New unverified user opens `/onboarding?step=3` by URL, clicks Connect Instagram, then Skip setup | Not specified. Observed: the page opens; Connect shows "Verify your email before connecting Instagram."; Skip setup marks onboarding done, so it is not shown after verifying | Question 4 |
| TC-F4-007 | PRD A-2 (F2 regression) | New user (verified, onboarding not done) opens the used verification link again, clicks "Go to ReelForge" | "Your email is already verified", then `/onboarding` | Pass |
| TC-F4-008 ⚙ | PRD A-4 (F2 regression, QA-001) | User who finished onboarding, signed out, opens `/app/projects?x=1`, logs in | `/app/projects?x=1` | Pass |
| TC-F4-009 | PRD A-4 | New user (onboarding not done), signed out, opens `/app/projects?x=1`, logs in, clicks Skip setup | Onboarding first; afterwards not specified. Observed: `/app`, the requested page is forgotten | Pass (question 6) |
| TC-F4-010 | F2 regression | Force `500` on `GET /me`, open `/onboarding?step=2`; click Try again | "We couldn't load your account" with Try again; then step 2 | Pass |
| TC-F4-011 | Milestone F1 | `npx tsc --noEmit` in `frontend/` | Exits 0 | Pass |

## Step navigation, reload, history

| ID | Requirement | Steps | Expected | Result |
|---|---|---|---|---|
| TC-F4-020 ⚙ | PRD flow 1 step 4 | Walk steps 1 → 2 → 3 with Continue; read the stepper at each step | Current step marked (`aria-current="step"`), earlier steps ticked | Pass |
| TC-F4-021 ⚙ | — | Reload on step 1 (nothing saved), step 2, step 3 | Same step after reload; unsaved choices are gone, saved ones are shown | Pass |
| TC-F4-022 | — | Save step 1, on step 2 press browser Back, then Forward | Step 1 with the saved topics; step 2 again. No broken screen | Pass (an unsaved voice choice is not kept across Back/Forward) |
| TC-F4-023 ⚙ | PRD flow 1 step 4 ("all skippable") | New user opens `/onboarding?step=3` directly, clicks "I'll do this later" | Step 3 opens; finishing works; profile has no niche or voice | Pass with note QA-034 (steps 1 and 2 are shown as done) |
| TC-F4-024 | — | Open `/onboarding?step=9`, `?step=abc`, `?step=0`, `?step=`, `?step=2&step=3` | Step 1 for unknown values; first value wins | Pass |
| TC-F4-025 | — | Step 2 and step 3: click Back | Previous step, with what was saved | Pass |

## Skipping, and what is saved

| ID | Requirement | Steps | Expected | Result |
|---|---|---|---|---|
| TC-F4-030 ⚙ | PRD flow 1 step 4; API A10 | Step 1: pick Fitness, type "gardening", click **Skip for now**. Step 2: pick a voice, **Skip for now**. Read the profile and the requests | No `PATCH /me` sent; niche and voice still empty. Going Back shows nothing selected | Pass |
| TC-F4-031 ⚙ | Milestone F4 ("can skip it") | Then "I'll do this later" on step 3 | One `PATCH /me {"onboardingCompleted":true}`; `/app`; niche and voice empty | Pass |
| TC-F4-032 ⚙ | Milestone F4 | Step 1: pick Fitness, click **Skip setup** | Only `onboardingCompleted` is sent; `/app`; niche empty; `/onboarding` no longer opens | Pass |
| TC-F4-033 ⚙ | API A10 | Save Travel on step 1, then **Skip setup** on step 2 | Niche stays "Travel"; onboarding done | Pass |
| TC-F4-034 ⚙ | API A10 | Step 1: Fitness, Finance, "gardening", Continue. Step 2: pick Adam, Continue. Read the profile after each | `niche` = "Fitness, Finance, gardening"; `defaults.voiceId` = Adam's id; caption style and mode unchanged | Pass |
| TC-F4-035 | Milestone F4 | Step 1: click Continue with a save held for 2.5 s, then click **Skip setup** before it answers | Ends on `/app` and stays there | **Fail QA-029** |
| TC-F4-036 | Milestone F4 | Step 3: click Connect Instagram with the answer delayed 2.5 s, then **Skip setup** | Ends on `/app` and stays there | **Fail QA-029** |

## Step 1: niche

| ID | Requirement | Steps | Expected | Result |
|---|---|---|---|---|
| TC-F4-040 ⚙ | API A10 (up to three topics) | Pick three topics | Other topics and the text field are disabled; "3 of 3 chosen"; deselecting one frees them | Pass |
| TC-F4-041 ⚙ | — | Nothing chosen; only spaces typed | Continue disabled | Pass |
| TC-F4-042 ⚙ | API A10 | Type "   home  cooking   ", Continue | Saved trimmed ("home  cooking") | Pass |
| TC-F4-043 ⚙ | API A10 (up to three topics, comma-separated) | Type "knitting, pottery, chess, origami", Continue | At most three topics stored, or the commas refused | **Fail QA-031** (counted as one; four topics stored) |
| TC-F4-044 ⚙ | API A10 (120 characters) | Paste 100 characters into the field | Cut to a length that keeps the whole value within 120 | Pass (cut to 40) |
| TC-F4-045 ⚙ | API A10 | Paste 39 letters followed by an emoji, Continue | Valid text is sent | **Fail QA-032** (half an emoji is sent) |
| TC-F4-046 | — | Type "🌱 plants 🌿"; type `<b>bold</b> & "quotes"` | Saved and shown again exactly as typed | Pass |
| TC-F4-047 | — | Type "Fitness" in the field (chip not chosen), Continue, come Back | Saved as "Fitness"; shown as the Fitness chip | Pass |
| TC-F4-048 | API A10 | Choose the Fitness chip and also type "Fitness", Continue, come Back | One topic stored | **Fail QA-031** ("Fitness, Fitness"; "2 of 3 chosen" with one chip lit) |
| TC-F4-049 | API A10 | Profile niche set (through the API) to "Fitness, Finance, Tech, Food"; open step 1 | The screen copes: no "4 of 3", and does not save four again | **Fail QA-031** |
| TC-F4-050 | API A10 | Profile niche set to 120 characters of free text; open step 1; press Backspace once | Text is kept, or the user is told it will be shortened | **Fail QA-031** (silently cut to 40) |
| TC-F4-051 | — | Profile niche "fitness", "FITNESS,finance", "a, b, c, d", " , ,", "Productivity"; open step 1 | No crash; value shown somewhere sensible | Pass (all go to the text field; see QA-031 for the count) |
| TC-F4-052 | PRD flow 1 step 4 | Save Fitness, come Back, deselect it | The niche can be cleared | **Fail QA-031** (Continue is disabled at 0; the old value stays) |
| TC-F4-053 ⚙ | API error format | Force `500`, then a dropped connection, on the step 1 save | Message shown, choices kept, button usable, retry works | Pass |
| TC-F4-054 | API error format | Force `400 validation_error` with `details.fields.niche` | The field message is shown | Pass with note (only the general "Please check the form." is shown; see QA-032) |
| TC-F4-055 | — | Step 1 save delayed 2.5 s: read the controls; change the selection while saving | Loading state; the change is either blocked or saved | Pass with note (Continue and Skip for now are disabled; the topic buttons stay live and a change made during the save is dropped; see QA-029) |

## Step 2: voice

| ID | Requirement | Steps | Expected | Result |
|---|---|---|---|---|
| TC-F4-060 ⚙ | API D3 | Open step 2 | Six voices from `GET /voices`, each with name, style, gender, a play button and a radio; Continue disabled until one is chosen | Pass |
| TC-F4-061 ⚙ | PRD flow 1 step 4.2 ("with previews") | Click Play on a voice; wait for the sample to end; click again | Label changes to "Stop … sample", bars light up; resets when the sample ends; plays again | Pass |
| TC-F4-062 | PRD flow 1 step 4.2 | Click Play on all six voices one after another | Only the last one is playing; earlier ones are paused; no error | Pass |
| TC-F4-063 ⚙ | PRD flow 1 step 4.2 | Double-click a play button; or click Play on one voice and at once on another | No error message, because nothing failed | **Fail QA-030** ("Couldn't play this sample.") |
| TC-F4-064 ⚙ | — | While a sample plays, leave the step by Skip for now, Back, browser Back, Continue, Skip setup | Sound stops every time | Pass |
| TC-F4-065 | — | Force a broken sample and an empty `previewUrl` | "Couldn't play this sample." under that voice | Pass |
| TC-F4-066 ⚙ | API D3 | Force `500` on `GET /voices` | Error with Try again; Skip for now and Back still work; Try again reloads | Pass (shown after one automatic retry, about 1.5 s; spinner during Try again) |
| TC-F4-067 | — | `GET /voices` delayed 3 s; click Skip for now during it | Spinner, then step 3 | Pass |
| TC-F4-068 ⚙ | API A10 | Force `500` on the voice save | Message, selection kept, retry works | Pass |
| TC-F4-069 | — | Profile `defaults.voiceId` set to an id not in the list; open step 2 | Not specified. Observed: nothing is selected but Continue is enabled and would save the old id again | Pass (observation, report section 2) |
| TC-F4-070 | Status document (decided) | Listen to the samples | Generated tones in mock mode | Not a defect |

## Step 3: connect Instagram

| ID | Requirement | Steps | Expected | Result |
|---|---|---|---|---|
| TC-F4-080 ⚙ | PRD flow 2 step 2; UI review section 2 item 1 | Open step 3; click "How to switch" | Note that a Business or Creator account is needed; steps to switch for free | Pass |
| TC-F4-081 ⚙ | API F1 | Click Connect Instagram; read the request | `GET /instagram/connect?returnTo=%2Fonboarding%3Fstep%3D3`; the browser goes to the returned `authorizeUrl` | Pass |
| TC-F4-082 ⚙ | PRD flow 2 step 5; API F2 | Approve as a Creator account with username "@asha.creates" | Back on `/onboarding?step=3&instagram=connected`; "Instagram connected", "@asha.creates"; Finish; profile shows connected | Pass |
| TC-F4-083 ⚙ | API F2 | Approve as a Business account | Same as above | Pass |
| TC-F4-084 ⚙ | PRD IG-1; flow 2 step 5 | Approve with a personal account | Reason and how to switch; nothing stored; Try again and "I'll do this later" | Pass |
| TC-F4-085 ⚙ | PRD flow 2 step 5 | Deny the permissions | Reason; nothing stored; Try again | Pass |
| TC-F4-086 ⚙ | PRD IG-11 | Account not added as a tester | Clear message; nothing stored | Pass |
| TC-F4-087 | API F2 | Open `/onboarding?step=3&instagram=error&reason=banana`, and with no `reason` | General "Something went wrong while connecting" | Pass |
| TC-F4-088 ⚙ | API F2 | Open `/onboarding?step=3&instagram=error&reason=__proto__` | General message, as for any unknown reason | **Fail QA-028** (blank page; `toString`, `constructor` and `hasOwnProperty` give an error box with no reason in it) |
| TC-F4-089 | — | Open `/onboarding?step=3&instagram=connected` while not connected; open the error URL while connected | No false "connected"; no error once connected | Pass |
| TC-F4-090 | — | After an error: reload; then Back and forward with the page's own buttons | Error survives reload; is gone after moving between steps | Pass |
| TC-F4-091 ⚙ | API F2; PRD flow 2 step 5 | New user: open `/mock/instagram` with no `state`, an empty one, a wrong one, and one already used; approve | The user is told the connection failed | **Fail QA-027** (lands on step 1 with no message) |
| TC-F4-092 | API F2 | Demo (onboarding done): wrong `state`, approve | `/app/settings?instagram=error&reason=failed` | Pass (Settings is the F8 placeholder) |
| TC-F4-093 | API F1, F2 | Start a connect in tab 1; open the same approval link in tab 2 and approve there | Tab 2 lands on step 3, connected | Pass |
| TC-F4-094 | API F2; PRD flow 2 step 5 | Tabs 1 and 2 both click Connect; approve in tab 1 (the older `state`) | The user is told the connection failed, or it succeeds | **Fail QA-027** (step 1, no message, not connected) |
| TC-F4-095 | API F2 | User A starts a connect. A logs out; user B logs in on the same browser, opens A's approval link and approves | Not specified | Question 2 (A's account is connected to the Instagram account B approved; B sees no result) |
| TC-F4-096 | Route guards | Signed out, open `/mock/instagram?state=abc` | `/login` | Pass |
| TC-F4-097 ⚙ | API F1 | Force `500`, a dropped connection and `403 email_not_verified` on `GET /instagram/connect` | Message shown; button usable again | Pass |
| TC-F4-098 ⚙ | — | `GET /instagram/connect` delayed 3 s | Connect shows loading; Back and "I'll do this later" disabled | Pass (Skip setup stays enabled: QA-029) |
| TC-F4-099 | — | Click Connect, then press browser Back on Instagram's page so the app page is restored exactly as it was left | Connect button usable again | **Fail QA-033** (Read; the back/forward cache cannot be used in this environment) |
| TC-F4-100 ⚙ | Milestone F4 | Force `500`, then a dropped connection, on finishing ("I'll do this later", Skip setup) | Message shown; still on onboarding; retry works | Pass with note QA-035 |
| TC-F4-101 | PRD flow 1 step 4.3; flow 2 step 5 | Compare the wording on step 3 with the PRD | "Skip for now"; "Connected as @username" | Fail QA-036 (polish) |
| TC-F4-102 | API F1 (state valid 10 minutes) | Wait more than 10 minutes on the approval screen | Told the connection failed | N/T (the mock `state` never expires; the landing would be the one in TC-F4-091) |

## `returnTo` (API F1) through the mock API

Each value was sent straight to `GET /instagram/connect?returnTo=…`, the approval page was opened and "Deny" clicked. Other sites were answered by the test, so nothing left the machine.

| ID | Requirement | `returnTo` | Expected | Result |
|---|---|---|---|---|
| TC-F4-110 ⚙ | API F1 | (none) | Default `/app/settings` | Pass |
| TC-F4-111 ⚙ | API F1 | `/onboarding?step=3` | Used; result added with `&` | Pass |
| TC-F4-112 ⚙ | API F1 | `//evil.example`, `https://evil.example`, `\\evil.example`, `javascript:alert(1)`, ` /app` (leading space) | Ignored | Pass |
| TC-F4-113 ⚙ | API F1 ("a path inside the web app") | `/\evil.example`, `/\/evil.example`, `/` + tab + `/evil.example`, `/` + line feed + `/evil.example` | Ignored | **Fail QA-026** (the browser ends on `evil.example`) |
| TC-F4-114 | API F1, F2 | `/app#frag` | Result reaches the page | Fail, part of QA-026 (the result is added after the `#` and is lost) |
| TC-F4-115 | API F1, F2 | `/onboarding?step=3&instagram=connected` | One `instagram` value in the final address | Fail, part of QA-026 (two values; the page showed no result) |
| TC-F4-116 | API F1 | 20,000 characters | Refused or cut | Fail, part of QA-026 (stored and redirected to as is) |
| TC-F4-117 | API F1 | `/app/settings/../../login` | Stays inside the app | Pass |

## Double activation

| ID | Requirement | Steps | Expected | Result |
|---|---|---|---|---|
| TC-F4-120 | — | Click Continue, "I'll do this later", Skip setup and Connect twice, 80 ms apart, with the answer delayed | One request each | Pass |
| TC-F4-121 | — | The same with two clicks and no gap between them (a synthetic double-click) | One request each | Pass with note (two identical requests each; harmless: the saves are repeatable and the newer connect `state` is the one used) |

## Session

| ID | Requirement | Steps | Expected | Result |
|---|---|---|---|---|
| TC-F4-130 ⚙ | PRD A-4; F1–F3 fix QA-003 | New user on step 1 with a topic picked. Log out in a second tab. Click Continue | `/login`; after logging in, back on `/onboarding` | Pass (the unsaved pick is gone) |
| TC-F4-131 | Same | Same on step 2 (entering it loads the voices) and on step 3 (Connect) | `/login`; after login back on the same step | Pass (step 2 checked through the login; step 3 checked as far as the login page, then TC-F4-132) |
| TC-F4-132 | Same | From that login page, log in as Demo instead | `/app` | Pass |

## Production build

| ID | Requirement | Steps | Expected | Result |
|---|---|---|---|---|
| TC-F4-140 | Milestones (mocks off is one setting) | Build with mocks off into the QA scratch folder; search the bundle for `mock/instagram`, "Instagram (mock)", the mock database key, the demo password | None present | Pass (0 matches; one 365 kB script, 110 kB gzipped) |
| TC-F4-141 | Same | Read `src/lib/env.ts`, `src/App.tsx`, `src/main.tsx` | The mock route and the mock database are only reachable when `USE_MOCKS` is true | Read: Pass |

## Looked at early (F9: phone width, keyboard, screen reader)

| ID | Requirement | Steps | Expected | Result |
|---|---|---|---|---|
| TC-F4-150 | PRD section 12 | All three steps, the error and connected states at 390×844; screenshots looked at | Usable, no horizontal scroll | Pass (notes in QA-038) |
| TC-F4-151 | PRD section 12 | Same at 768×1024 and 844×390 | Usable, no horizontal scroll | Pass |
| TC-F4-152 | PRD section 12 | Same at 320×568 | Usable | Fail QA-038 (step 3 scrolls sideways) |
| TC-F4-153 | PRD section 12 | Touch emulation (iPhone 13): tap topics, a voice name, a play button, finish | All work by touch; playing does not change the selection | Pass |
| TC-F4-154 | Milestone F9 | Keyboard only through all three steps, the Instagram round trip and Finish | Everything reachable; visible focus; Space and Enter work; arrow keys move between voices | Pass (notes in QA-037) |
| TC-F4-155 | Milestone F9 | Where is focus after Continue, Back or Skip for now changes the step? | On the new step's heading or first control | Fail QA-037 (focus is dropped to the page) |
| TC-F4-156 | Milestone F9 | Read the roles and names: stepper, topic buttons, voices, play buttons, errors | Stepper is a labelled list with the current step marked; topics are toggle buttons in a labelled group; voices are radios named with name, style and gender; play buttons are named per voice; errors are alerts | Pass |
| TC-F4-157 | Milestone F9 | Reduced motion on: load the voices slowly, play a sample | No animation; playing is still visible (icon and colour) | Pass |
