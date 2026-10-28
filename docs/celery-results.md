# Celery task results

What the result backend stores, where, and what to ask it. Source:
`settings.py` (the `CELERY_*` result settings), `core/result_backend.py`
(`TenantDatabaseBackend`), `core/admin.py` (`TaskResultAdmin`),
`tests/settings.py`. The code wins over this page.

**Only task FAILURES are stored.** `CELERY_TASK_IGNORE_RESULT` is on, so a
successful task writes nothing; `CELERY_TASK_STORE_ERRORS_EVEN_IF_IGNORED`
keeps failures and retries, which land in `django_celery_results` in the
**public schema only** (the app is in SHARED_APPS, deliberately not
TENANT_APPS) and are surfaced by the platform dashboard and its Task
Results page. The result backend is `core.result_backend.TenantDatabaseBackend`
(not an environment setting — Celery prefers a `CELERY_RESULT_BACKEND` env var
over the Django setting, so `tests/settings.py` pins it): it writes the
`_schema_name` header a task was published with into `TaskResult.meta`, so
every failure row says which store it ran for, hard time limits included. The
Task Results admin (`core.admin.TaskResultAdmin`, platform-only) shows a Store
column and a Store filter built on it. Consequences: adding a `chord()` or `group()` — which do
need results — means setting `ignore_result=False` on that task, not
flipping the global; and "did the nightly task run?" is answered by
`PeriodicTask.last_run_at` or `MonitoredTask`'s log line, never by that
table. The reasoning is spelled out where the settings are defined.
