# ReelForge: developer response to QA and Tester findings, F4 (Onboarding)

| | |
|---|---|
| Date | 2026-11-24 |
| Inputs | [QA_REPORT_F4.md](QA_REPORT_F4.md) (QA-026 to QA-038), the Tester's report (BUG-010 to BUG-015) |
| Written by | The developer. The QA report itself is left as QA wrote it. |

Status values: **Fixed** (changed in the frontend, covered by a test), **Answered** (a document was unclear; the document is updated), **Deferred** (to do later, with the milestone).

## Major

| Finding | Status | What was done |
|---|---|---|
| QA-026 / BUG-014: the return address after connecting Instagram could lead to another site (`/\evil.example` and similar) | Fixed, and the rule rewritten | The mock now refuses backslashes, control characters, over-long values and anything that resolves off the site; fragments and leftover result parameters are removed. API F1 now states the rule exactly, so the real backend is built to it |

## Minor

| Finding | Status | What was done |
|---|---|---|
| QA-027 / BUG-010: a connect that fails outright during onboarding was silent | Fixed | The failure is carried into step 3 and shown |
| QA-028: a crafted `reason` in the address gave a blank page | Fixed | Unknown reasons are treated as the generic failure |
| QA-029: "Skip setup" could race with a save or connect in flight | Fixed | It waits for requests in flight; topic controls are disabled during a save |
| QA-030 / BUG-012: a false "Couldn't play this sample" when switching quickly | Fixed | An interrupted start is no longer treated as a failure |
| QA-031 / BUG-011: more than three topics could be stored | Fixed | The free text is one topic and cannot contain commas; no repeats; existing values that break the rule are brought within it; a saved niche can be cleared |
| QA-032: the 40-character cut could split an emoji | Fixed | The limit counts whole characters |
| QA-033: Connect could stay spinning after Back from Instagram | Fixed | The request now completes; the busy state is reset when the page is restored |
| BUG-013: an unverified user could open onboarding and mark it done | Fixed | Onboarding redirects unverified users to the app |
| BUG-015: the mock's connect `state` never expired | Fixed | 10 minutes, single use |

## Polish

| Finding | Status | What was done |
|---|---|---|
| QA-034: skipped steps showed a tick | Fixed | A tick only when the choice is saved; skipped steps are announced as skipped |
| QA-035: a failed finish kept its error on other steps | Fixed | Cleared when changing step |
| QA-036: wording differed from the PRD | Fixed | "Skip for now" and "Connected as @username" |

## Looked at early for F9

| Finding | Status | What was done |
|---|---|---|
| QA-037: focus, Enter key, page title | Fixed now | Focus moves to the new step's heading; Enter in "Something else" continues; each step sets the page title |
| QA-038: 320 px overflow, small "Skip setup" target, voice card hit area | Partly fixed | Step names collapse and buttons wrap at narrow widths; larger touch target. Deferred to F9: making the whole voice card select the voice |

## Round 2 (found by the Tester while checking the fixes above)

| Finding | Status | What was done |
|---|---|---|
| BUG-016: `/.//evil.example` and similar still gave an off-site return address, because resolving the path removes `/./` and `/../` | Fixed, and the rule extended | The resolved value is checked again for a leading `//`. API F1 has the extra condition and says the resolved value is the one stored |
| BUG-017: the 40-character cut split emoji made of several code points (flags, skin tones, families) | Fixed | The limit is 40 code points, cut only between visible characters; an emoji that doesn't fit whole is left out. API A10 names the unit |
| BUG-018: with a topic selected, a free text beginning with its name ("Tech reviews") could not be typed | Fixed, behaviour changed | The field is emptied when it loses focus and holds exactly a selected topic's name, not while typing |
| BUG-019: reloading a step moved focus to the heading | Fixed | Focus moves only when the step changes while the page is open |
| BUG-020: a failed finish error survived the browser's Back button | Fixed | Cleared on every change of step, including Back and Forward |

Round 3: the Tester confirmed all five (223 of 223 browser tests) and noted one leftover: typing a topic's name and then selecting that topic left the typed text on screen. Fixed: selecting the topic now empties the field.

## Questions

| # | Question | Answer |
|---|---|---|
| 1 | PRD says "niche and language" | The PRD is corrected: version 1 is English only, so there is no language choice or setting |
| 2 | Should F2 require the signed-in user to match `state`? | Yes. Added to API F2 and to the mock |
| 3 | Is the free text one topic or several; the 40-character limit | One topic, 40 characters, no commas. Added to API A10, including what the server rejects |
| 4 | Unverified users and `/onboarding` | Closed to them (BUG-013). Whether they may browse the rest of the app stays open question 6 in the status document |
| 5 | Instagram profile picture (PRD I2) | **Open for F8.** The API's `instagram` object needs a `profilePictureUrl` field when the Settings screen is built |
| 6 | After onboarding, a user who came from a deep link lands on `/app` | **Deferred to F9.** Minor; onboarding happens once per account |
