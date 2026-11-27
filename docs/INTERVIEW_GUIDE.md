# ReelForge: how to explain it, and interview questions with answers

Use this with [ARCHITECTURE.md](ARCHITECTURE.md). Read the answers, then say them in your own words; an interviewer notices a memorised answer. Everything here is true of the code in these two repositories, so you can always open the file and show it.

**Be honest about how it was built.** You designed and directed the product and built it with an AI coding assistant (Claude Code): you made the product decisions, reviewed the designs, ran a Tester and a QA review on every milestone, and set up the accounts and keys. Say that plainly if asked. What an interviewer then wants to know is whether you *understand* it. This guide is for that.

## 1. The 30-second version

> "ReelForge is a SaaS that turns a topic into a finished vertical video. It writes a script with an LLM, makes the voiceover, finds stock clips or generates images, adds music and word-by-word captions, shows a live preview you can edit, renders an MP4 and publishes it as a YouTube Short. It's a React front end, a Flask API, Celery workers on Redis for all the slow AI work, PostgreSQL, S3 storage and a separate Node render service, all running in Docker."

## 2. The 2-minute version (add these four points)

1. **Everything slow is a background job.** The API answers in milliseconds: it creates a job row, reserves credits and returns 202. Workers do the AI calls. The browser polls for progress.
2. **Every AI provider is replaceable.** Each has an interface, a fallback (Gemini then Groq, ElevenLabs then Edge TTS) and an offline mock, so the 1,700 tests run with no keys and no cost.
3. **One timeline JSON drives both the preview and the render,** using the same React components (Remotion), so the preview is exact.
4. **Credits work like a bank ledger:** reserved before the work under a row lock, refunded automatically on failure.

## 3. Numbers worth remembering

| | |
|---|---|
| API endpoints | About 65, in 11 groups |
| Database tables | 17 (PostgreSQL 16) |
| Automated tests | About 1,700 backend, 730 frontend unit, 200 browser |
| Containers locally | 9 (Postgres, Redis, storage, mailbox, API, 3 workers, render service) |
| A real Reel | 5 scenes generated in about 26 seconds for 2 credits |
| Render | 29 s of video took about 17 minutes on a laptop (software rendering) |
| External services | Gemini, Groq, ElevenLabs, Edge TTS, Pixabay, Cloudflare Workers AI, YouTube Data API |

## 4. Questions and answers

### Architecture

**Why not just call the AI services inside the HTTP request?**
They take seconds to minutes and fail often. A request that waits ties up a server process, hits proxy timeouts and can't show progress or be retried. With jobs, the API stays fast, failures are isolated, and work survives a browser refresh.

**Why Celery and Redis? Why not a simpler queue or a cloud queue?**
Celery gives per-queue workers, time limits, retries and a scheduler out of the box, and Redis was already needed for rate limits. I use separate queues: general work, `render` (one at a time) and `publish`, so a 30-minute render never blocks a 5-second script.

**What happens if a worker crashes in the middle of a job?**
Tasks are acknowledged late, so Redis delivers the task again. The job first checks it is still `queued`; a job already `running` when its worker died is found by a sweeper task that fails jobs past their time limit and refunds the credits. Nothing stays stuck, and the creator never loses credits.

**How do you avoid doing a job twice?**
Three layers. The click carries an idempotency key, so a retried request returns the first answer. A partial unique index allows one active job per scene and type. And the worker locks the row and checks the state before starting.

**Why is the render service a separate Node service?**
Remotion is a React library, so rendering runs in Node with headless Chrome and FFmpeg. That image is 1.5 GB and CPU-heavy. Keeping it separate keeps the Python image small, lets rendering scale on its own, and a Chrome crash can't affect the API. The worker starts a render over HTTP and polls it.

**Why polling and not WebSockets?**
Polling every 2 seconds is simple, works through any proxy and recovers by itself after a network drop. For a single-creator tool the load is tiny. At scale I'd move to server-sent events.

**Is it a monolith or microservices?**
A modular monolith (one Flask codebase for the API and the workers) plus one separate service, the renderer, because it has a different runtime and resource profile. I split where there was a real reason, not by default.

### Data and consistency

**How do you stop two requests from spending the same credits?**
Reserving credits locks the user's credit account row (`SELECT … FOR UPDATE`), checks the balance, writes the ledger row and updates the balance in one transaction. The second request waits for the lock and then sees the new balance. An early version checked the balance without the lock; the QA review caught the race, and a test now covers it.

**What is the ledger for, if you store the balance?**
The balance is a cache; the ledger is the truth and the audit trail. Every change has a row with its kind (grant, reserve, refund, adjust) and the job it belongs to. A database constraint allows one reserve and one refund per job, so a bug can't refund twice.

**How do you handle a user editing a scene while its voice is being generated?**
The job records what it was made from (the narration and mood). When the result arrives, the worker locks the scene and compares. If the text changed, the result is discarded, the file deleted and the credit refunded; the scene shows as needing a new voice.

**How do you prevent deadlocks?**
Every code path takes locks in the same order: the project, then its scenes and jobs. Web requests and workers both follow it. A deadlock found by testing in one path was fixed by making it follow that order.

**Why PostgreSQL?**
Relational data with real constraints: foreign keys, check constraints on every status column, partial unique indexes ("one active job per scene"). Those constraints caught bugs that code review missed. JSONB holds the flexible parts (the timeline, job input and output).

### AI and providers

**What if Gemini is down or retires a model?**
The LLM interface has an ordered list of providers. A failure moves to the next one (Groq). An unusable answer (wrong JSON shape) is asked again up to twice, then the next provider. An admin switch can turn a provider off without a deploy. This happened for real: two models were retired during the build, and the fix was a one-line setting.

**How do you make an LLM return something you can rely on?**
I ask for JSON with an exact shape, validate every field (lengths, counts, allowed moods), and treat an invalid answer as a retryable failure. User text is quoted inside the prompt and the output is cleaned of control characters. The model's answer is never trusted as code or markup.

**How are the word-by-word captions timed?**
ElevenLabs returns character timestamps with the audio; they're turned into word timings and matched back to the script's own spelling. The fallback voice (Edge TTS) gives word boundaries too. The renderer highlights the word whose time range contains the current frame.

**How do you test code that calls paid APIs?**
Every provider has a mock that answers offline in the real shape. Tests of the real clients stub the HTTP layer and assert the exact requests. No test ever calls a real provider, so CI is free and repeatable.

### Security

**How does login work?**
A 15-minute access token and a 30-day rotating refresh token, both in HttpOnly cookies. Reusing an old refresh token revokes the whole session family (it means a token was stolen). Because cookies are sent automatically, every change also needs a CSRF header that only our own pages can produce.

**Why cookies and not a token in localStorage?**
JavaScript can read localStorage, so any XSS bug leaks the token. HttpOnly cookies can't be read by scripts. The cost is CSRF, which the header check handles.

**How did you secure the YouTube connection?**
OAuth with a random `state` stored in Redis for 10 minutes, used once and tied to the signed-in user, so one person's approval link can't connect another's account. The return address must be a path on our own site (no open redirect). The upload permission is checked after consent. Tokens are encrypted at rest with a key from the environment and revoked on disconnect.

**How do users' files stay private?**
The bucket is private. The API hands out presigned links that expire, and only for files the user owns. The render service gets links only for the files of one render.

### Front end

**How does the editor's live preview work?**
The API builds a timeline JSON from the scenes as they are now. The Remotion Player plays it in the browser with the same React components the renderer uses. A test compares the two copies of those components file by file, so they can't drift.

**How does autosave avoid losing text?**
Each field saves a short time after typing stops. Saves for one field run in order; a failed save stays "unsaved" and is retried; leaving the page or pressing Generate first waits for every pending save. A newer edit is never overwritten by an older response.

**What state management did you use?**
TanStack Query for server state (caching, polling, invalidation); plain React state for forms. No global store was needed: almost all state is server data.

### Testing and quality

**How did you keep quality up while building fast?**
Decide, document, design, then code. Every milestone was reviewed twice before it was committed: a Tester role writing automated tests against the spec, and a QA role reading the code against the documents. Each produced a report; every finding was fixed or explicitly deferred with a reason. The reports and fix logs are in the repository.

**Tell me about a bug you're glad you caught.**
- *The credit race:* two parallel requests could both pass the balance check. Fixed with a row lock.
- *Retry duplicates:* "Try again" after an interrupted YouTube upload would have uploaded a second copy. Now a retry continues the same upload session, on the same channel only.
- *Post text marked the video outdated:* autosaving the YouTube title counted as editing the Reel. Found in review before a user saw it.

**What does CI do?**
On every push: build the Docker stack, lint and format checks, the full test suites, a check that the migration matches the documented schema, and a check that the production image refuses to start with development secrets.

### Docker and deployment

**Walk me through your Docker Compose file.**
Nine services. Postgres, Redis and MinIO have health checks, and the API waits for them. The API runs migrations and creates the bucket on start. Three workers run the same image with different queues and concurrency. The renderer is its own image with Chrome's libraries and more shared memory. Settings come from environment variables; secrets from a `.env` file that is never committed.

**How would you deploy and scale this?**
The API is stateless, so it scales horizontally behind a load balancer. Workers scale per queue: add general workers for more users, render instances for more videos. Postgres, Redis and S3 move to managed services. On Kubernetes each of these is a Deployment; the workers scale on queue length; migrations run as a Job; secrets come from a Secret store.

**What's the bottleneck?**
Rendering: it's CPU-bound, about one frame a second on a laptop. That's why it has its own queue and service, and why the next step is Remotion Lambda (rendering chunks in parallel).

### Product and judgement

**Why YouTube before Instagram?**
YouTube lets the server upload the file directly, so it works from a local machine. Instagram needs a public video URL and an app review by Meta. I made publishing platform-neutral in the API and data model, shipped YouTube, and left Instagram as an addition to the same endpoints.

**What would you do differently?**
Start with the render speed problem earlier (pre-crop stock clips to portrait; they're decoded at full landscape size). Use server-sent events instead of polling. Give the queue its own Redis.

**What did you learn?**
That most of a real system is the failure paths: retries, refunds, cancelled jobs, half-finished uploads. And that writing the design down first made every later decision faster.

## 5. A demo script (5 minutes)

1. Log in. Show the dashboard and the credits.
2. **New Reel:** type a topic, pick a tone and length. Show the job progress while the script is written.
3. **Script editor:** edit a scene (autosave), rewrite one with AI, reorder by drag.
4. **Generate:** show voices and visuals arriving scene by scene.
5. **Editor:** play the preview; change a stock clip; fix a caption word.
6. **Render** (show a finished one; it's slow) and **Publish:** AI title and description, then the live Short.
7. **Admin:** the provider switches and the usage table.
8. Open the repository: `docs/ARCHITECTURE.md`, a QA report, the CI run.

## 6. Résumé lines

- Built **ReelForge**, an AI short-video SaaS (React, TypeScript, Flask, Celery, PostgreSQL, Redis, S3, Docker): topic → script → voice → visuals → rendered video → YouTube, with a live in-browser preview.
- Designed a **job engine** with credit reservation, automatic refunds, idempotent requests and provider fallback across 7 external AI and media APIs.
- Implemented **OAuth 2.0 publishing to YouTube** with resumable uploads, encrypted tokens and duplicate-safe retries.
- Ran a documented **test and QA process**: about 2,600 automated tests in CI (GitHub Actions) across unit, API and browser levels.
