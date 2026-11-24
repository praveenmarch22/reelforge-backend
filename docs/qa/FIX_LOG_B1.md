# ReelForge: developer response to QA findings, B1 (Backend setup)

| | |
|---|---|
| Date | 2026-11-24 |
| Input | [QA_REPORT_B1.md](QA_REPORT_B1.md) (QA-039 to QA-053) |
| Written by | The developer. The QA report itself is left as QA wrote it. |

Status values: **Fixed**, **Partly fixed**, **Answered** (a document is updated), **Deferred** (with the milestone).

## Major

| Finding | Status | What was done |
|---|---|---|
| QA-039: only the API's own messages were JSON logs with a request id | Fixed | Every request writes one JSON line (method, path without its query string, status, duration, user id; health checks left out). The worker logs the same JSON format, with task arguments and results removed so an email's link never reaches the logs. Gunicorn's plain-text access log is switched off, since the request line replaces it |

## Minor

| Finding | Status | What was done |
|---|---|---|
| QA-040: the production check let weak settings through | Partly fixed | Production now also refuses the development database login, an insecure cookie setting, a non-https web address, a missing mail service and unencrypted SMTP. A web address containing `*` is refused in every environment. Deferred to I2: the length of the storage secret (it depends on the answer to question 6) |
| QA-041: the local stack published a debugger console and its ports to the network | Fixed | All ports are bound to this machine only (127.0.0.1); the in-browser debugger is off |
| QA-042: tests share the development Redis and worker | Partly fixed | The Tester moved the tests' Redis keys to their own database (15). The README no longer claims full isolation. Deferred to B3, when tasks start to matter: a separate queue for tests |
| QA-043: error handlers dropped headers | Fixed | 405 keeps `Allow`; `Retry-After` is kept; `ApiError` can carry headers |
| QA-044: ordinary requests got the upload error codes | Fixed | 413 is `payload_too_large` and 415 is `unsupported_media_type`; the upload codes are kept for upload endpoints. API design updated |
| QA-045: the health check was slow when a dependency hangs | Partly fixed | Database connections give up after 3 s and storage makes one retry instead of two. Deferred to I2 with question 2: a lighter check for the load balancer |
| QA-046: CI gaps | Partly fixed | CI now builds the production image and checks that it refuses to start with development settings. Deferred to I1: mypy, a vulnerability audit of the pins, pinned base images, running Gunicorn in CI |
| QA-047: storage wrapper limits (region, upload size, bucket CORS) | Deferred to B3 | They belong with the upload endpoints |
| QA-048: `flask db migrate` would generate a migration that drops tables | Deferred to B3 | The README already says migrations are written by hand; a guard is added when the remaining models exist |
| QA-049: test gaps | With the Tester | Passed on as part of round 2 |

## Polish

| Finding | Status | What was done |
|---|---|---|
| QA-050: the `default` queue is not in the technical design | Deferred | Document update with B3, when tasks are routed |
| QA-051: `repr(Settings)` showed secrets | Fixed | The secret fields are left out of the printed form |
| QA-052: README inaccuracies | Fixed | Test isolation wording corrected |
| QA-053: no `Access-Control-Max-Age` | Fixed | 10 minutes |

## Questions

| # | Question | Answer |
|---|---|---|
| 1 | Which definition of B1 applies? | MILESTONES.md: the foundations of the API. The renderer service is B5 |
| 2 | Should the load balancer use a lighter check? | **Open, for the product owner at I2** |
| 3 | Add `internal_error`, `method_not_allowed` and the others to the API code table? | Yes, added |
| 4 | Does staging run as `production`? | **Open, for the product owner at I2.** Until then there is no staging environment |
| 5 | Is the schema SQL-first for good? | The baseline stays SQL. Later changes are hand-written migrations, with the models kept in step |
| 6 | Access keys or an instance role on AWS? | Decided at I2; the wrapper will accept empty keys then |
| 7 | Is a public health check with three network calls acceptable? | For now, yes. Revisited with question 2 |
