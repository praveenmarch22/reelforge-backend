# ReelForge: developer response to QA findings, B4 slice 2 (Visuals)

| | |
|---|---|
| Date | 2026-11-25 |
| Inputs | [QA_REPORT_B4_S2.md](QA_REPORT_B4_S2.md) (QB4S2-001 to QB4S2-009), [TEST_REPORT_B4_S2.md](TEST_REPORT_B4_S2.md) (BUG-042, BUG-043, observations) |
| Written by | The developer. The QA and test reports are left as they were written. |

Status values: **Fixed**, **Answered** (a document is updated), **Deferred** (with the milestone).

## Minor

| Finding | Status | What was done |
|---|---|---|
| QB4S2-001 / Tester obs. 1: regenerate alternated between two clips | Fixed | A visual remembers the last 30 clips its scene has had (`meta.seen`); regenerate picks one not among them. Checked with real Pixabay: three takes, three different clips |
| QB4S2-002 / Tester obs. 2: a failing stock search failed the job; downloads never tried the next clip | Fixed | A stock provider that is down sends the job to the AI image; a clip that fails to download makes way for the next fitting one (up to three) |
| QB4S2-003 / BUG-043: cancelling a take started with new fields brought the old visual back as ready | Fixed | C8 (with a changed source, prompt or query) and C10 (stock on a non-stock scene) mark the current visual `outdated` before the job starts, so a cancel leaves `outdated` and the project doesn't count it as ready meanwhile |
| QB4S2-004: free fallback images and C8 had no limit | Fixed | Free fallback images: 20 a day per creator, then the job fails with `no_stock_match` (refunded; nothing was charged). C8: 60 an hour per creator, counted only when work starts |
| QB4S2-005: downloads trusted any URL in the provider's answer | Fixed | Only https on the provider's own hosts (`cdn.pixabay.com`, `pixabay.com`), no redirects followed. The thumbnail is read in pieces and stops at 2 MB |

## Polish

| Finding | Status | What was done |
|---|---|---|
| QB4S2-006 / Tester obs. 4: fallback AI images ignored a style change | Fixed | A fallback image being made is dropped (`scene_changed`) if the style or character changed meanwhile, and B5 marks stock scenes that show a fallback image `outdated` |
| QB4S2-007: a clip id the provider rejects was `provider_failed` | Fixed | A non-retryable refusal of the id is `stock_unavailable`. The lookup is not cached: it happens once per choice |
| QB4S2-008 / BUG-042: the mock stock made real HTTP calls for thumbnails | Fixed | Thumbnails (and clips) are only fetched from the provider's own hosts, so the mock's example addresses are never called |
| QB4S2-009: an image's thumbnail was the full image | Fixed | AI images get a 360-pixel JPEG thumbnail (about 20 KB instead of 500–700 KB) |
| Tester obs. 3: C10 accepted `mock:N` when the mock isn't configured | Fixed | Only ids of stock providers that are set up and switched on are accepted (400 otherwise) |
| Tester obs. 5: a blank `q` on C9 is 400 | Answered | Intended: the web app leaves `q` out to search with the scene's words |

## Questions

| # | Question | Answer |
|---|---|---|
| 1 | The User-Agent named a personal GitHub address | Changed to plain `ReelForge/1.0`: providers don't need a contact, and it kept a personal link out of every request |
| 2 | Limit for the free fallback | 20 a day per creator (above); revisit with real usage |
| 3 | 1024×1024 images and 1920×1080 clips need about 1.8× enlargement for 9:16 | Accepted for version 1. A sharper source (portrait FLUX sizes, Pexels' portrait clips) is a later improvement |
| 4 | G-6 says simpler keywords come from the language model | The first two words of the query are used for now; an extra model call per fallback costs time and quota for little gain. Recorded in the technical design |

## Round 2 (QA recheck)

| Finding | Status | What was done |
|---|---|---|
| QB4S2-010: a style change during a stock take on a scene showing a fallback image left the old image ready | Fixed | B5 also cancels visual jobs on scenes showing a fallback image, then marks them `outdated` |
| QB4S2-011 / Tester observation: regenerate gave up after page 1 of results | Fixed | Regenerate looks through up to 3 pages of results (60 Pixabay clips) per query before the AI image |
| QB4S2-012: the free-image count was spent before the image was made; the message blamed the search words when stock was down | Fixed | The count is checked before and increased only after an image is made. When stock was down and the day's free images are used, the job fails with `provider_unavailable` instead of `no_stock_match` |
