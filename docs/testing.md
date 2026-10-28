# Test configuration

How the pytest suites are set up and what that means for a test you write
or a run you start. Source: `pyproject.toml` (`[tool.pytest.ini_options]`,
`[tool.coverage]`), `tests/settings.py`, `tests/conftest.py`,
`tests/migration_seed.py`, `tests/cache.py`, `tests_mt/conftest.py`. The
code wins over this page.

Tests in `tests/` with `unit/`, `integration/`, and `utils/` subdirectories.

Settings read while Django sets up live in `tests/settings.py`, selected with `--ds=tests.settings` in `addopts` (the option outranks a `DJANGO_SETTINGS_MODULE` env var, which every container gets from `env_file: .env`). It pins `DISABLE_CACHE`, `ENABLE_DEBUG_TOOLBAR`, `APPEND_SLASH`, `DEBUG` and `DATABASE_BACKUPS_ENABLED` in the environment before `settings.py` loads, so the developer's `.env` cannot make a local run differ from CI, and declares `CACHES` as `tests.cache.WorkerScopedCache` (Redis, `clear()` scoped to its own `KEY_PREFIX`). The cache prefix and the per-run `MEDIA_ROOT` both follow `TEST_NAMESPACE` — the test database name plus the xdist worker — so two sessions on one machine never touch each other's state **as long as they use different `DB_NAME`s**: a second `-n N` run beside a full run must set its own `DB_NAME=…` (or use `-n0`), or it truncates the full run's `test_<DB_NAME>_gwN` databases. pytest-django imports `conftest.py` only after `django.setup()`, so a value assigned there is too late for anything an `AppConfig.ready()` or the cache registry reads. Key `conftest.py` settings:
- MD5 password hasher (faster than default)
- `MEILISEARCH["OFFLINE"] = True`
- `CELERY_TASK_ALWAYS_EAGER = True` with `EAGER_PROPAGATES` — a task body that fails raises into the test
- `transaction.on_commit` runs callbacks at once in non-transactional tests, exactly like Django's autocommit branch: an exception propagates unless the callback was registered `robust=True`. A test that means "this failure does not reach the caller" must say so (`pytest.raises(Retry)` in the task, a patched `apply_async`), not rely on a swallowed error
- **Migration seeds survive every flush** (`tests/migration_seed.py`): when `migrate` builds a test database, every non-empty table is snapshotted into the `test_migration_seed` schema; a `flush` (the end of any `transaction=True` / `TransactionTestCase` test) and a reused database at session start restore it. Every test therefore sees exactly the seeded rows — never a hand-maintained reseed. A test that needs a table without its seed deletes it inside its own transaction (`setUpTestData`, or the test body), never in a session fixture. A database built before this existed needs one `--create-db`. New migrations on a reused database first restore the old snapshot, so leftovers are never snapshotted. `available_apps`, `serialized_rollback` and `reset_sequences` bypass the restore and are refused at collection
- **The test database has the public schema only**: `tests/conftest.py` runs django-tenants' `migrate` with `shared=True`, so the seeded `webside` tenant's schema is not migrated too (it halved the time to build a worker database). No test in `tests/` needs a physical tenant schema; `tests_mt/` provisions its own
- `transaction=True` / `TransactionTestCase` only where real commits are the point (threads on other connections, `select_for_update` contention, `database_sync_to_async`); each one ends with a full-table flush
- `bind_tenant` (connection.tenant; restores `schema_name`) and `bind_tenant_and_schema` (also points `schema_name` at the tenant) — no local copies; `tests.utils.staff.store_tenant` builds a `Tenant` row (no schema: `auto_create_schema` is off for the whole suite)
- `openapi_schema` session fixture: the document `manage.py spectacular` writes, built once
- WebSocket tests drive the ASGI application with `asgiref.testing.ApplicationCommunicator` and raw `websocket.*` events (`tests/unit/asgi/test_websocket_stack.py`). Do not import `channels.testing`: its `__init__` imports `daphne.testing`, and neither daphne nor Twisted is a dependency
- `requires_meilisearch` marker for tests needing a live engine; `tests/utils/meilisearch.py` indexes real documents for them
- Query counts: Django's `CaptureQueriesContext` and pytest-django's `django_assert_num_queries` / `django_assert_max_num_queries`
- Coverage minimum: 70% (`fail_under = 70`, enforced on the combined CI shards), per-test timeout: 600s

On Windows with Docker Desktop a fresh connection to `localhost` can stall 1-30 s (its IPv6 port relay); test durations that are exact multiples of ~30 s mean that, not slow code — run locally with `REDIS_HOST=127.0.0.1`.

**MT (multi-tenant) lane** — `tests_mt/` is a SIBLING of `tests/`, not a subdirectory: `tests/conftest.py` strips multi-tenancy (`DATABASE_ROUTERS = []`, no `TenantMainMiddleware`) to keep the main suite fast, which makes schema-binding bugs (cross-schema FKs, cache-key leaks, seed migrations that stop reaching new tenants) invisible to it. `tests_mt/conftest.py` keeps the real `TenantSyncRouter` and `TenantMainMiddleware`, provisions one tenant schema per session, and runs a handful of high-leverage smoke tests on top. Not collected by a bare `uv run pytest` (`testpaths = ["tests"]` excludes it) — run it explicitly with `uv run pytest tests_mt -n 0` (serial; schema switching is process-global via `connection`).
