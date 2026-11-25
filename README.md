# ReelForge backend

ReelForge is an AI studio for solo creators: turn an idea into a vertical Reel of up to 90 seconds (script, voiceover, visuals, captions, music) and post it to Instagram from one place.

This repository holds the backend and all project documents. The web app is in [reelforge-frontend](https://github.com/praveenmarch22/reelforge-frontend).

**Where the project stands:** [PROJECT_STATUS.md](PROJECT_STATUS.md). Milestones: [MILESTONES.md](MILESTONES.md).

## What is here

```
├── api/                 Flask API and Celery worker (B1 foundations, B2 accounts)
├── docker-compose.yml   local stack: Postgres, Redis, MinIO, Mailpit, API, worker
├── pipeline/            Python generation prototype: topic → script → voiceover → visuals → timeline
├── renderer/            Remotion prototype: timeline → vertical MP4
├── docs/                PRD, design documents, database schema, QA documents
├── MILESTONES.md
├── PROJECT_STATUS.md
└── PROTOTYPE.md         how to run the command-line prototype
```

## Run the backend locally

You need Docker Desktop. Nothing else has to be installed.

```bash
docker compose up -d --build
```

The first start takes a few minutes. It creates the database from the migrations and the storage bucket by itself.

| What | Address |
|---|---|
| API | http://localhost:5000/api/v1 (try `/api/v1/health`) |
| Test inbox (every email the API sends) | http://localhost:8025 |
| MinIO console (file storage) | http://localhost:9001, user `reelforge-dev`, password `reelforge-dev-secret` (local only) |
| Postgres | `localhost:54320`, database, user and password all `reelforge` (local only) |
| Redis | `localhost:63790` |

Postgres and Redis use unusual ports on your machine so they don't clash with other projects. To change any port, copy `.env.example` to `.env` and edit it.

```bash
docker compose exec api pytest
```

runs the tests. They use their own database, storage bucket and Redis database, so development data is not touched. One thing is shared: the background worker, which a few tests ask to run a harmless "ping" task.

```bash
docker compose logs -f api worker
```

follows the logs, and

```bash
docker compose down
```

stops everything. Add `-v` to also delete the local database and files.

Code changes in `api/` are picked up by the API automatically. The worker needs `docker compose restart worker` after a change to task code.

### Music library

The starter music (15 CC BY 4.0 tracks by Kevin MacLeod, listed in `api/reelforge_api/music_library.json`) is downloaded into storage and the database by a command that is safe to run again:

```bash
docker compose exec api flask music-seed
```

## How the API is organised

```
api/
├── wsgi.py                  entry point for Flask, Gunicorn and Celery
├── reelforge_api/
│   ├── __init__.py          create_app(): settings, logging, errors, database, queue, storage, routes
│   ├── config.py            settings from environment variables; refuses development secrets in production
│   ├── errors.py            one error shape for every response
│   ├── log.py               JSON logs with a request id on every line
│   ├── celery_app.py        Celery wired to the app; the queue names
│   ├── storage.py           S3 / MinIO: presigned upload and download links
│   ├── cli.py               `flask storage-init`, `flask worker-ping`
│   ├── models.py            database tables as Python classes
│   ├── schemas.py           request bodies (validation) and the User object
│   ├── security.py          password hashing, link tokens, common-password list
│   ├── sessions.py          login sessions: the three cookies, refresh rotation, revoking
│   ├── auth.py              @require_user: who is calling, CSRF check
│   ├── ratelimit.py         rate limits in Redis
│   ├── mail.py              the emails and how they are sent
│   ├── accounts.py          creating users, link tokens, deleting accounts
│   ├── routes/              HTTP endpoints: health, auth (A1-A8), me (A9-A12)
│   └── tasks/               background tasks: ping, sending email, daily clean-up
├── migrations/              database migrations; 0001 is the schema in docs/schema.sql
└── tests/
```

Conventions come from [docs/API_DESIGN.md](docs/API_DESIGN.md) (error format, status codes) and [docs/TECHNICAL_DESIGN.md](docs/TECHNICAL_DESIGN.md) (queues, storage, security).

### Accounts

Sign-up, email verification, login, password reset and the profile follow [docs/API_DESIGN.md](docs/API_DESIGN.md) group A. To try them by hand, run the web app against this API or call the endpoints directly; verification and reset emails arrive in the test inbox at http://localhost:8025.

The rate limits from the API design are on locally too (for example 3 sign-ups per hour from one address). To switch them off while experimenting, put `RATE_LIMITS_ENABLED=false` in `.env` and run `docker compose up -d`.

### Database changes

The baseline migration runs an exact copy of `docs/schema.sql`. Do not edit either file. To change the schema, add a migration:

```bash
docker compose exec api flask db revision -m "describe the change"
```

then write the `upgrade` and `downgrade` steps in the new file under `api/migrations/versions/`, and update `docs/DB_DESIGN.md`.

### Updating dependencies

Direct dependencies are pinned in `api/requirements.txt`; every indirect one is pinned in `api/constraints.txt`. To update: change `requirements.txt`, empty `constraints.txt`, rebuild, run the tests, then write the new lock:

```bash
docker compose run --rm --no-deps -T api pip freeze > api/constraints.txt
```

## Documents

| Document | What it covers |
|---|---|
| [docs/PRD.md](docs/PRD.md) | What the product does, user flows, edge cases |
| [docs/TECHNICAL_DESIGN.md](docs/TECHNICAL_DESIGN.md) | Architecture, tech stack, jobs, integrations, AWS |
| [docs/API_DESIGN.md](docs/API_DESIGN.md) | All 70 endpoints |
| [docs/DB_DESIGN.md](docs/DB_DESIGN.md), [docs/schema.sql](docs/schema.sql) | 16 tables, tested on PostgreSQL 16 |
| [docs/PIPELINE_STRATEGY.md](docs/PIPELINE_STRATEGY.md) | How a Reel is generated |
| [docs/STITCH_PROMPTS.md](docs/STITCH_PROMPTS.md), [docs/UI_REVIEW.md](docs/UI_REVIEW.md) | The designed screens and review notes |
| [docs/qa/](docs/qa/) | Test plan, test cases, QA reports and fix logs |
