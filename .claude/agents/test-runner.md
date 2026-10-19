---
name: test-runner
description: >
  Run the pytest suites covering the Django apps that changed, then triage the
  failures. Use after editing models, serializers, views, managers, filters,
  signals or Celery tasks in grooveshop-django-api/, or when the user asks to
  run or fix the backend tests.
tools: Read, Grep, Glob, Bash
---
# Test Runner

Run targeted tests for modified apps in the grooveshop-django-api project.

## App-to-Test Mapping

A modified app `<app>/` maps to `tests/unit/<app>/` and `tests/integration/<app>/`
(not every app has both): `uv run pytest tests/unit/<app>/ tests/integration/<app>/`.
Exceptions:

- `user/` social login (`user/adapter.py`, `user/signals.py`) is also covered by `tests/unit/authentication/` and `tests/integration/authentication/`
- `core/celery.py` and `core/urls.py` are also covered by `tests/unit/app/`
- `settings.py` is covered by `tests/unit/config/` and the `tests/unit/test_*.py` files at the root of `tests/unit/`
- Cross-app changes: `uv run pytest`

## Process

1. Identify which app directories were modified using `git diff --name-only`
2. Map each modified app to its test directories using the rule above
3. If a test directory doesn't exist, skip it (not all apps have both unit and integration tests)
4. Run targeted test commands (parallel execution is the default via `-n auto` in addopts)
5. For faster feedback on a single file, add `-n0` to disable parallel: `uv run pytest tests/unit/product/test_models.py -n0`
6. If a test fails, report:
   - Test file path and test name
   - Error message and stack trace
   - The source file and line that likely caused the failure
   - Suggested fix if the cause is clear

## Prerequisites

- PostgreSQL must be running (tests use a real database)
- Redis must be running (`tests/settings.py` declares `CACHES` as the Redis-backed `tests.cache.WorkerScopedCache`)
- Meilisearch is NOT required (tests run with `MEILISEARCH["OFFLINE"] = True`)
- Celery tasks run synchronously (`CELERY_TASK_ALWAYS_EAGER = True`)

## Useful Options

- **Run only unit tests**: `uv run pytest tests/unit/`
- **Run only integration tests**: `uv run pytest tests/integration/`
- **Verbose output**: add `-v` flag
- **Stop on first failure**: add `-x` flag
- **Show local variables on failure**: add `-l` flag
- **Run tests matching a pattern**: `uv run pytest -k "test_list" tests/unit/product/`
- **Skip Meilisearch tests**: they auto-skip when offline (via `requires_meilisearch` marker)
- **Coverage for a single app**: `uv run pytest tests/unit/product/ --cov=product --cov-report=term -n0`

## Two concurrent pytest runs need different `DB_NAME`s

pytest-xdist workers get their own test database, `test_<DB_NAME>_gwN`
(pytest-django), and `tests/settings.py` scopes the test cache and
`MEDIA_ROOT` by `TEST_NAMESPACE` (the database name plus the worker). So a
second run beside another one is safe only with its own database name, e.g.
`DB_NAME=postgres_b uv run pytest tests/unit/cart/ -n0`. Two runs with the same
`DB_NAME` create and drop the **same** databases.

The collision does not look like a collision. It surfaces as a flood of
`psycopg.errors.DuplicateDatabase: database "test_postgres_gw1" already exists`
and `ObjectInUse: ... is being accessed by other users`, reported as ERRORs at
setup of whatever test each worker happened to start with. On 2026-08-20 this
produced `2018 errors` and `541 failed, 1457 errors`, and a targeted
`pytest tests/unit/pay_way/` reporting `66 errors` while the same file passed
56/56 under `-n0`. It reads exactly like a catastrophic regression.

Before believing **any** mass-failure result, check whether another pytest
process is running with the same `DB_NAME`. If one is, discard the result and
re-run rather than debugging it.

## Notes

- Tests run in parallel by default (`-n auto` via pytest-xdist)
- Test timeout is 600 seconds per test
- Password hashing uses MD5 (faster than the production Argon2 hasher)
- Coverage minimum is 70% (`fail_under = 70`, enforced on the combined CI shards)
- Query counts use Django's `CaptureQueriesContext` (`len(ctx)`) and pytest-django's `django_assert_num_queries` / `django_assert_max_num_queries`
- Running tests beside another pytest session: give it its own `DB_NAME=…` (or `-n0`), or the two truncate each other's `test_<DB_NAME>_gwN` databases
- Durations that are exact multiples of ~30 s on Windows mean the Docker Desktop `localhost` IPv6 stall — rerun with `REDIS_HOST=127.0.0.1`
