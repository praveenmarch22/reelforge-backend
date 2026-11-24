# ReelForge: QA report for B1 (backend setup)

| | |
|---|---|
| Date | 2026-11-24 |
| Milestone | B1 Backend setup: Flask app, PostgreSQL (schema.sql), Redis, Celery, MinIO, Docker Compose |
| Build reviewed | `backend/` at commit `8f0da83` (B1 is `9a013e6` plus the CI fix `70a2cdc`). All file and line references are to that commit |
| Environment | The running Docker Compose stack (`backend-*` containers), API at http://localhost:5000; `curl`, `pytest` and short Python probes run inside the `api` container |
| Reviewed against | [MILESTONES.md](../../MILESTONES.md), [TECHNICAL_DESIGN.md](../TECHNICAL_DESIGN.md) (TD), [API_DESIGN.md](../API_DESIGN.md) sections 2 and J, [DB_DESIGN.md](../DB_DESIGN.md), [schema.sql](../schema.sql) |
| Plan | [TEST_PLAN.md](TEST_PLAN.md) |

## Verdict: pass with issues

The foundation does what the milestone asks: the stack was up with every container healthy, `GET /api/v1/health` answers in the documented shape, the baseline migration is byte-for-byte `docs/schema.sql` and the database holds the 16 tables at revision `0001`, a task makes the round trip through the worker, presigned links work against MinIO, the container runs as a non-root user, errors use the one documented shape and do not leak internals, and the 39 tests pass.

There is no Blocker. There is one Major finding: "JSON logs with a request id on every line" is true only for lines the API's own code writes. The worker, and the API under the image's production command (Gunicorn), write plain text, and no line at all is written for an ordinary request. The Minor findings are mostly places where the foundation is safe only as long as it is configured correctly (production checks, the health check when a dependency hangs, the local stack's open ports), plus gaps in CI and in the tests.

| Severity | Count | Ids |
|---|---|---|
| Blocker | 0 | |
| Major | 1 | QA-039 |
| Minor | 10 | QA-040 to QA-049 |
| Polish | 4 | QA-050 to QA-053 |
| Questions | 7 | Section 6 |

**"Verified" column**: *Run* means reproduced against the running stack or with a probe inside the `api` container. *Read* means concluded from the code only. I changed no product code.

**Limits of this review**

- **The working tree changed during the review.** From about 15:52 IST, B2 work appeared in `api/` (new modules, changed `config.py`, `errors.py`, `requirements.txt`, `docker-compose.yml`), and at about 15:57 the `api` and `worker` containers were recreated from a new image by someone else. Checks before that ran on a tree identical to the commit: the `curl` checks, the 39-test run, the logs, the container user, `pip freeze`, the database state. For the later probes (config, CORS, error mapping, health timing, Gunicorn) I extracted the committed `api/` folder with `git archive` into `/tmp` inside the container and ran against that copy, so they exercise B1 code; the second batch (Gunicorn, logging configuration) ran in the rebuilt image, whose installed packages may differ from B1's. The copy was deleted afterwards. I did not review the B2 files; where I happened to see that B2 work already touches a finding, the finding says so.
- I did not stop any service. Behaviour with a dependency down was tested by pointing a throwaway app object, inside the container, at an address that refuses connections and at one that never answers.
- I did not start the stack from nothing (`docker compose up -d --build` on a clean machine) and did not see a CI run; the workflow was read.
- AWS (S3, RDS, a load balancer) was not available. Findings about it are *Read*.
- `MILESTONES.md` has no "done when" line for B1 (question 1). I used TD section 15 row 1: "`docker compose up` runs everything; health checks pass".
- No separate test-case file was written for B1; the checks are listed in section 3.

---

## 1. Findings

### Major

#### QA-039: Logs are JSON with a request id only for the API's own messages; the worker and the production server write plain text, and a normal request writes nothing

| | |
|---|---|
| Requirement | TD section 13 ("Structured JSON logs with `request_id`, `user_id`, `project_id`, `job_id`"); API J ("the same id is on every log line for that request"); README ("JSON logs with a request id on every line") |
| Verified | Run |
| Believed cause | `api/reelforge_api/log.py:35-43` configures only the Flask process's root logger; `api/Dockerfile:26` (`--access-logfile -`, Gunicorn's own format); `docker-compose.yml:92` (Celery takes over the root logger when the worker starts, replacing the JSON handler) |

What was seen:

| Process | Output |
|---|---|
| Worker (`docker compose logs worker`) | `[2026-11-24 10:19:36,095: INFO/MainProcess] Task system.ping[f54d…] received`: Celery's text format on every line, no JSON |
| API under the image's own command (Gunicorn, started for a few seconds on an internal port from the committed code) | `127.0.0.1 - - [24/Nov/2026:10:28:17 +0000] "GET /api/v1/health?token=secret123 HTTP/1.1" 200 72 "-" "Python-urllib/3.12"` and `[…] [240] [INFO] Starting gunicorn 23.0.0`: text, no request id, and the full query string |
| API in the local stack (`flask run`) | Start-up lines are plain text; the only JSON lines are Werkzeug's warning. Eleven requests, including a 404 and a 405, produced no line at all (`log.py:43` silences the request log) |

Expected: one JSON object per line from every process, and at least one line per request that carries the id returned in `X-Request-ID`.
Actual: the id returned to the client can be matched to a log line only when the request hit the 500 handler or a failed health probe. The worker, where all slow work will run, has no structured logs, so the `job_id` and `project_id` fields the design asks for have nowhere to go. Nothing passes the request id to a task either.

Also: the access line under Gunicorn records query strings. API F2 (`GET /instagram/callback?code=…&state=…`) will put Instagram's one-time code in that log unless the line is replaced.

### Minor

#### QA-040: The production safety check covers two secrets and is skipped unless `ENV=production` is set

| | |
|---|---|
| Requirement | TD section 10 (Secrets; "CORS limited to the web app's domain"); API section 2 (cookie sessions, so the allowed origin is a security boundary); README ("refuses development secrets in production") |
| Verified | Run (probe inside the container) |
| Believed cause | `api/reelforge_api/config.py:18` (default `development`), `:38-45` |

| Probe | Result |
|---|---|
| `Settings(env="production", secret_key=<40 chars>, s3_secret_key="x")` with nothing else set | Accepted, with `database_url` = `…reelforge:reelforge@localhost:5432…`, `s3_endpoint_url` = `http://localhost:9000`, `s3_access_key` = `reelforge-dev`, `web_origin` = `http://localhost:5173`, and a one-character storage secret |
| The same with `web_origin="*"` | Accepted |
| An app with `WEB_ORIGIN=*`, request with `Origin: https://evil.example` | `Access-Control-Allow-Origin: https://evil.example` with `Access-Control-Allow-Credentials: true`: any site may call the API with the user's cookies |
| `ENV` not set at all | `development`: no check runs, and the published `dev-only-secret-change-me` signs whatever B2 signs with `SECRET_KEY` |
| `ENV=staging` | Refused (only `development`, `test`, `production` exist). TD section 12 has a staging environment on AWS; see question 4 |

Expected: a deployment that forgets a setting fails to start. Actual: it starts with development values, except for the two checked secrets.

Note: `S3_ENDPOINT_URL` has to be set to an empty string on AWS (`config.py:25-26`); if it is left out the API looks for storage on `localhost:9000`. The B2 work in the tree already adds an `https://` check on `WEB_ORIGIN` in production, which also closes the `*` case there.

#### QA-041: The local stack publishes the debugger console, an unauthenticated Redis, Postgres and MinIO on every network interface

| | |
|---|---|
| Requirement | TD section 10 (Secrets); `docker-compose.yml:7` ("for local development only") |
| Verified | Run (`docker compose ps` shows `0.0.0.0:5000`, `:54320`, `:63790`, `:9000-9001`; `GET http://localhost:5000/console` returns `200` "Console // Werkzeug Debugger") |
| Believed cause | `docker-compose.yml:31,44,62-63,75` (ports without `127.0.0.1:`), `:73` (`--debug`) |

On a shared or public network, other machines can reach Redis with no password (the Celery broker: anything written there is run by the worker), Postgres and MinIO with passwords that are published in a public repository, and the Werkzeug console page (protected by a PIN and a host check only). Nothing here is production, but the developer's machine is. Binding the ports to `127.0.0.1` keeps every README step working.

#### QA-042: Tests share the development Redis, and a test that queues a task has it run by the development worker against the development database

| | |
|---|---|
| Requirement | README ("runs the tests (in their own database and bucket, never touching development data)"); TD section 14 (integration tests: API + database + Celery) |
| Verified | Run for the path (the worker log shows `system.ping` received and executed by `backend-worker-1` during the test run); Read for the consequence |
| Believed cause | `api/tests/conftest.py:23-32` (overrides the database and bucket, not `redis_url`); `docker-compose.yml:15,89-92` (one Redis, one worker, which always uses `DATABASE_URL`) |

`test_a_task_makes_the_round_trip_through_the_worker` passes because `ping` only runs `SELECT 1`. The first B2 task that reads a row (send the verification email for user X) will look for it in `reelforge`, not `reelforge_test`, and fail or act on development data. Rate-limit counters written by B2 tests will land in the same Redis the running API uses, so a test run can lock the developer out of the local app, and leftovers from one run can fail the next.

#### QA-043: Error responses drop the headers the error carries, and `ApiError` cannot set any

| | |
|---|---|
| Requirement | API section 2 ("`429 rate_limited` with `Retry-After`") |
| Verified | Run |
| Believed cause | `api/reelforge_api/errors.py:20,28-32,53-57` |

- `POST /api/v1/health` → `405` with no `Allow` header (Run against the live API).
- A route raising Werkzeug's `TooManyRequests(retry_after=30)` → `429 rate_limited` with no `Retry-After` (Run, probe).
- `ApiError(status, code, message, details)` has no way to add a header, so B2's rate limits cannot answer as documented through the one error path.

The B2 work in the tree already adds `headers` to `ApiError`; the handler for Flask's own errors (`_http_error`) still discards them.

#### QA-044: A JSON request with the wrong or missing `Content-Type`, or a body over 1 MB, is answered with the upload error codes

| | |
|---|---|
| Requirement | API section 2, error codes: `400 validation_error` for a bad body; `413 file_too_large` and `415 unsupported_file_type` are upload errors (API D1) |
| Verified | Run (probe route that calls `request.get_json()`) |
| Believed cause | `api/reelforge_api/errors.py:42-43` |

| Request | Answer |
|---|---|
| `POST` with a body and no `Content-Type: application/json` | `415 unsupported_file_type`, "That file type isn't supported." |
| `POST` with 1 MB of JSON | `413 file_too_large`, "That file is too large." |
| `POST` with malformed JSON | `400 validation_error`, "The request could not be understood." (no `details.fields`) |

A person who pastes a long script, or a client that forgets the header on login, is told about a file. Every B2 endpoint that reads a body inherits this.

#### QA-045: When a dependency stops answering (instead of refusing), the health check takes 10 to 33 seconds

| | |
|---|---|
| Requirement | API J1 ("If a dependency is down: `503` … Used by the load balancer") |
| Verified | Run (throwaway app inside the container; nothing was stopped) |
| Believed cause | `api/reelforge_api/__init__.py:28-29` (no connect timeout for PostgreSQL), `api/reelforge_api/storage.py:34-36` (3 s connect × retries), `api/reelforge_api/routes/system.py:29-33` (checks run one after another) |

| Dependency state | Status | Body | Time |
|---|---|---|---|
| Database refuses the connection | 503 | `db: down`, others `ok` | 0.3 s |
| Redis refuses | 503 | `redis: down` | 0.1 s |
| Storage refuses | 503 | `storage: down` | 2.1 s |
| Bucket missing | 503 | `storage: down` | 0.0 s |
| Redis does not answer | 503 | `redis: down` | 2.1 s |
| Storage does not answer | 503 | `storage: down` | 10.7 s |
| Database does not answer | 503 | `db: down` | 21.1 s |
| None of the three answers | 503 | all `down` | 33.3 s |

The body and status are right in every case, and no detail leaks. The times are the problem: "does not answer" is what a wrong security group or a network fault looks like on AWS. A load balancer gives a health check a few seconds, so it never sees the body; the image runs two Gunicorn workers with the default 30-second limit (`Dockerfile:26`), so two probes in flight occupy the whole API, and in the last row the worker is killed before it can answer.

#### QA-046: CI does not check several things the design or the image depend on

| | |
|---|---|
| Requirement | TD section 12, CI/CD ("lint (ruff, eslint), type-check (mypy, tsc), tests"); TD section 2 ("Lint, type-check, test, build images") |
| Verified | Read (`.github/workflows/ci.yml`, `api/Dockerfile`, `api/requirements-dev.txt`) |

- **No type-check.** mypy is not in `requirements-dev.txt` and there is no step for it.
- **The production image is never built.** `Dockerfile:15` defaults to `requirements-dev.txt`, so an image built without `--build-arg REQUIREMENTS=requirements.txt` ships pytest and ruff; the variant without them is not built anywhere.
- **The image's own command is never run.** Compose replaces it with `flask run --debug` (`docker-compose.yml:71-73`), so `gunicorn … wsgi:app` (`Dockerfile:26`) is untested. It does start and serve (Run, see QA-039).
- **No check for known vulnerabilities in the pins.** The direct pins date from late 2024. From memory, not confirmed with a scanner: Flask-Cors 5.0.0 has published advisories fixed in 6.0.0, and Flask 3.1.0 one fixed in 3.1.1. A `pip-audit` step would settle it.
- **`constraints.txt` is not checked against what is installed.** Today they match exactly (Run: `pip freeze` equals the file, `pip check` clean).
- `postgres:16`, `redis:7-alpine` and `python:3.12-slim` are moving tags, while MinIO and every Python package are pinned.

#### QA-047: The storage wrapper will not create a bucket outside `us-east-1` and cannot limit the size of an upload

| | |
|---|---|
| Requirement | TD section 10 ("Presigned PUT limited by content type and size"; "S3 … CORS limited to the web app's domain"); `cli.py:18` ("local MinIO, or a new AWS environment") |
| Verified | Read. The MinIO behaviour is covered by the passing tests |
| Believed cause | `api/reelforge_api/storage.py:58` (`create_bucket` without a location), `:85-91` (`presigned_put` signs the content type only) |

- In any AWS region other than `us-east-1`, `create_bucket` needs `CreateBucketConfiguration`; without it S3 refuses, so `flask storage-init` fails there.
- `presigned_put` has no size parameter. A link made for a 2 MB voice file accepts a 5 GB upload for 15 minutes. The uploads endpoint is a later milestone, but it can only ask for what the wrapper offers.
- `storage-init` sets no CORS rule or public-access block on the bucket. MinIO allows browser requests from any origin by default; S3 allows none, so browser uploads that work locally will fail on AWS until a rule exists.

#### QA-048: `flask db migrate` would write a migration that drops every table

| | |
|---|---|
| Requirement | TD section 2 ("SQLAlchemy 2.0, Alembic (Flask-Migrate)"); README, "Database changes" |
| Verified | Read (not run: it would write a file into `api/migrations/versions/`) |
| Believed cause | `api/migrations/env.py:48-51,99-104` (compares the database with the app's models; B1 has none), `api/reelforge_api/extensions.py:8-12` |

The schema is built from SQL, and the README tells the developer to use `flask db revision`. The usual Flask-Migrate command, `flask db migrate`, compares the database with the models; with no models it proposes dropping all 16 tables, and once B2 has models for some tables it will propose dropping the rest and "fixing" every difference between a model and the SQL (indexes with `WHERE`, `citext`, checks). Nothing warns about this. See question 5.

#### QA-049: Gaps and weak spots in the tests

| | |
|---|---|
| Requirement | TD section 14; the B1 scope items the tests are meant to hold in place |
| Verified | Read, except where marked |

- **The size-limit test cannot fail.** `tests/test_errors.py:64-66` posts 1 MB to a route that does not exist and accepts `404` or `413`; it gets `404` because nothing reads the body. Remove `MAX_CONTENT_LENGTH` and it still passes. The limit itself works (Run: `413` on a route that reads the body).
- **Nothing tests that a real request's log line carries the request id, or that the 500 handler logs the exception.** `test_log_lines_are_json_with_extra_fields` formats a record by hand.
- **Running a migration switches the app's logging off for the rest of the process.** `migrations/env.py:14` calls `fileConfig`, which replaces the JSON handler and disables existing loggers (Run: after it, the root formatter is the plain one and `reelforge_api.errors` is `disabled`). `conftest.py:43` runs `upgrade()` in the test process, so any later test that looks at log output will see nothing.
- **`conftest.py:40` drops the `public` schema of whatever `TEST_DATABASE_URL` points to**, with no check that it is a test database.
- **Not tested at all**: the `storage-init` and `worker-ping` commands; an app created with `env="production"`; CORS preflight (`OPTIONS`) with `X-CSRF-Token` and `Idempotency-Key`; a `db: down` health answer (Redis and storage are covered).
- Health and the dependency timings in QA-045 are tested only with patched functions that fail at once.

### Polish

#### QA-050: A `default` queue exists that the technical design does not list, and the test that says it checks the design agrees with the code

| | |
|---|---|
| Requirement | TD section 7 (seven queues: `llm`, `voice`, `visuals`, `ai_video`, `render`, `publish`, `maintenance`) |
| Verified | Run (worker log lists eight queues) |
| Believed cause | `api/reelforge_api/celery_app.py:10`, `api/reelforge_api/__init__.py:36`, `api/tests/test_worker_and_config.py:21` (`test_queues_match_the_technical_design` expects eight) |

A catch-all queue is reasonable; the design document is the thing to update. Until then a task with no route lands on `default` without anyone noticing.

#### QA-051: Printing the settings object shows every secret

| | |
|---|---|
| Requirement | TD section 10 (Secrets) |
| Verified | Run (`repr(Settings(...))` contains the database password) |
| Believed cause | `api/reelforge_api/config.py:20,22,33` (plain `str`, not pydantic's `SecretStr`) |

The validation error raised by the production check does not show them (Run). The risk is a later `log.info("settings: %s", settings)` or an error tracker capturing local variables.

#### QA-052: README details that do not hold

| | |
|---|---|
| Requirement | README accuracy |
| Verified | Read, except the first row |

| README says | What happens |
|---|---|
| Tests run "in their own database and bucket, never touching development data" | True for Postgres and MinIO; not for Redis or the worker (QA-042, Run) |
| `docker compose up -d --build`, then `docker compose exec api pytest` | Without `--wait` (which CI uses) the second command can run before migrations and the worker are ready on a first start |
| `docker compose run … pip freeze > api/constraints.txt` (shown as `bash`) | On this project's Windows machine the natural shell is PowerShell, where `>` may write the file in an encoding pip cannot read. Not run |
| "Conventions come from API_DESIGN.md (error format, status codes)" | The codes `internal_error` and `method_not_allowed`, and the fallback `error`, are not in that document (question 3) |

`MILESTONES.md` still describes Track B as "plan to be agreed", and TD section 3 still lists `api/` and `docker-compose.yml` as planned.

#### QA-053: Preflight answers carry no `Access-Control-Max-Age`

| | |
|---|---|
| Requirement | API section 2 (every POST, PATCH and DELETE sends `X-CSRF-Token`, so each one is preflighted when the web app and the API are on different origins) |
| Verified | Run (`OPTIONS /api/v1/health` with the web app's origin: methods, the three requested headers and credentials are allowed; no max-age) |
| Believed cause | `api/reelforge_api/__init__.py:57-62` |

Browsers then cache the preflight for about five seconds, so the editor's autosave and most other writes cost two round trips each.

---

## 2. For B2 (not findings)

Things the foundation leaves open that Accounts will meet first.

| Topic | State in B1 |
|---|---|
| Client address for per-IP rate limits | Nothing reads `X-Forwarded-For`; behind a load balancer every request would count as one IP. The B2 tree is adding a setting for this |
| One Redis database for the queue, task results and (soon) rate-limit counters | No key prefix or separate database, and tests use it too (QA-042) |
| One `SECRET_KEY` | Sessions, the Instagram `state` and the token encryption key (TD section 10) should not all be the same value; settings for the others do not exist yet |
| Request id in tasks | Not passed from the request to the task (QA-039) |
| Email | No mail service in the B1 compose file; TD names Resend for development |
| Cookies locally | Web app on `localhost:5173`, API on `localhost:5000`: same site, different origin, so cookies work only with credentials on and the CORS setup above; `Secure` cookies over plain http are browser-dependent |
| Errors with `details.fields` | `ApiError` supports `details`; Flask's own 400 has none (QA-044) |

## 3. What passed

**Verified by running** (on the committed code; see Limits)

| Area | Result |
|---|---|
| Stack | `backend-api-1`, `-worker-1`, `-postgres-1`, `-redis-1` healthy, `-minio-1` up (it has no health check, by design) |
| Tests | `docker compose exec -T api pytest -q`: 39 passed in 3.6 s |
| Health | `200` `{"status":"ok","db":"ok","redis":"ok","storage":"ok","version":"0.1.0"}`, exactly API J1. `503`, `"status":"degraded"` and the right dependency `down` for each of db, Redis and storage, with nothing but the word `down` in the body (QA-045 table) |
| Error shape | 404 inside and outside `/api/v1`, 405, 400, 413, 429 and an unhandled exception all return `{"error": {"code", "message"}}`; the 500 body is the fixed sentence and does not contain the exception text |
| Request id | Present on 200, 404, 405 and 500; a plain caller id is kept; one with a space is replaced |
| CORS | The web app's origin is allowed with credentials; `https://evil.example` gets no CORS headers; preflight allows `X-CSRF-Token`, `Idempotency-Key`, `Content-Type`; `X-Request-ID` and `Retry-After` are exposed; error responses carry the headers too |
| Request size | A 1 MB JSON body is refused with 413 on a route that reads it |
| Database | `flask db current` → `0001 (head)`; 16 tables plus `alembic_version` |
| Schema | `diff docs/schema.sql api/migrations/versions/0001_baseline.sql`: identical |
| Container | Runs as `uid=1000(app)` |
| Dependencies | `pip freeze` equals `constraints.txt`; `pip check` clean |
| Worker | Listens on the eight queues, has `system.ping` registered, and answered the test's task |
| Production command | Gunicorn starts from the committed code and serves health and a 404 |
| Production secrets | `env="production"` refuses the development `SECRET_KEY`, a short one and the development storage secret; the error text shows no values |

**Concluded by reading**

- `.gitignore` and `api/.dockerignore` keep `.env` out of the repository and the image; `.env.example` holds no secrets; `.gitattributes` forces LF so the CI `diff` and the container mounts work from a Windows checkout.
- The baseline migration sends the SQL file unchanged to the driver and its downgrade drops the 16 tables and the function; the downgrade and re-upgrade are tested.
- Tasks run inside the app context; late acknowledgement and a prefetch of one are set, so a crashed worker does not lose a task.
- Redis client timeouts are 2 s; storage and presign clients are separate so links are signed for the address a browser can reach.
- CI: checkout, schema `diff`, `docker compose up -d --build --wait`, ruff check, ruff format check, pytest, logs on failure, `down -v`; read-only token permissions.
- Following the README's "Run the backend locally" steps against the files: the commands, addresses, ports, credentials and the file tree match `docker-compose.yml`, `.env.example` and `api/`, apart from QA-052.

## 4. Not tested

| What | Why |
|---|---|
| A first start from an empty machine, and `docker compose down -v` | Not allowed to stop or remove containers or volumes |
| Really stopping Postgres, Redis or MinIO under the running API | Same; simulated instead (QA-045) |
| A CI run on GitHub | Workflow read only |
| Amazon S3, RDS, a load balancer in front of the health check | No AWS environment |
| `flask db migrate`, `flask db revision`, `flask storage-init --wait` against a stopped MinIO | Would write files or need a stopped service |
| The schema against DB_DESIGN.md line by line | The B1 requirement is that the migration equals `schema.sql`, which holds; the schema itself was reviewed in P3 |
| Vulnerability scan of the pins | No scanner in the image |
| The B2 files now in the working tree | Not part of B1 |

## 5. Out of scope (not defects)

- No render service in Compose: TD section 15 row 1 lists it, `MILESTONES.md` puts rendering in B5 (question 1).
- No Celery beat service, task routes, time limits or per-queue concurrency (TD section 7): they arrive with the tasks (B3 to B8). One worker with two processes serves all queues for now.
- Sentry, CloudWatch, image build and deploy steps in CI: TD milestone 12 and I2.
- Auth, CSRF checking, rate limits, request validation with Pydantic: B2.
- MinIO runs as root in Compose and has no health check: stated and explained in `docker-compose.yml`.

## 6. Questions

| # | Question | Why it matters |
|---|---|---|
| 1 | `MILESTONES.md` gives B1 a scope but no "done when", and calls Track B provisional. TD section 15 row 1 says "`docker compose up` runs everything (… renderer); health checks pass". Which is the definition, and should the renderer line move to B5 in the technical design? | The exit criterion in the test plan needs a "done when" line |
| 2 | J1 makes the load balancer's check fail when storage or Redis is down. Should the load balancer use a lighter check (process up, database reachable) and keep the full one for monitoring? | With the documented behaviour a storage blip takes login and every other endpoint out of service; it also decides how QA-045 is fixed |
| 3 | The handlers answer `500 internal_error`, `405 method_not_allowed` and, for any other status, the code `error`. None is in the API design's code table. Add them? | The web app's error handling is written from that table |
| 4 | TD section 12 has a staging environment on AWS; settings allow only `development`, `test`, `production`. Does staging run as `production`? | Decides whether the production checks (QA-040) protect staging, which is on the internet with real Instagram tokens |
| 5 | Is the schema SQL-first for good (models follow the SQL, migrations written by hand), or will models become the source and `flask db migrate` be used? | Settles QA-048 and how B2 adds its models |
| 6 | On AWS, will the API use access keys from Secrets Manager or the instance's role? The wrapper always passes `S3_ACCESS_KEY` and `S3_SECRET_KEY` and the production check requires a non-default secret | A role cannot be used as written |
| 7 | `/health` is public and makes three network calls per request with no caching. Acceptable, or should it be limited or cached for a second or two? | It is the cheapest way to load the database and storage from outside |

## 7. Evidence

Probe scripts and their output are in the QA scratch folder (temporary, not in the repository): `probe1.py` (settings, CORS with `*`, error mapping, headers), `probe2.py` and `probe2.out` (health timings), `probe3.py` (logging after `fileConfig`). They are run from a copy of the committed `api/` folder inside the container:

```
git archive HEAD api | docker compose exec -T api sh -c "mkdir -p /tmp/qa-b1 && tar -x -C /tmp/qa-b1"
docker compose exec -T -w /tmp/qa-b1/api api python - < probe1.py
```
