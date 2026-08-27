---
name: feedback-multiagent-pytest-db-contention
description: Concurrent teammates running uv run pytest hit shared Postgres test-db contention (not real failures)
metadata:
  type: feedback
---

When multiple teammate agents run `uv run pytest` concurrently against
the same local Postgres instance (default `-n auto --dist loadfile`,
no `--reuse-db`), each invocation tries to create/drop a `test_postgres`
(or `test_postgres_gwN`) database. Overlapping runs surface as
`django.db.utils.OperationalError: database "test_postgres..." is
being accessed by other users` / `DuplicateDatabase` / connection
timeouts — always at test-DB setup/teardown, never as an assertion
failure (`FAILED`).

**Why:** this is Postgres-instance-level resource contention between
sibling agent processes, not a defect in the code under test.

**How to apply:** before treating a red pytest run as a real bug,
check the traceback is inside `django_db_setup`/`create_test_db` and
grep the output for `FAILED` — if there are zero `FAILED` lines and
only `ERROR ... database ... already exists / being accessed by other
users`, retry once contention clears (or scope to fewer files) rather
than debugging the app code. A run with genuine assertion failures
will show `FAILED test_...` lines regardless of contention.

**Self-collision, not just multi-agent:** the same contention happens
within ONE session if a `Bash` tool call with `run_in_background`
(or one that silently exceeds the default 120s timeout and gets
auto-backgrounded) is still running `pytest` when a second `pytest`
invocation is started — e.g. re-running the same command in the
foreground because the background one "seemed slow". Check for
lingering workers (`Get-CimInstance Win32_Process -Filter
"name='python.exe'" | Where CommandLine -like '*<project>*'` on
Windows) before starting a second run, and stop only ONE at a time —
never fire a second `pytest` while a prior one might still be alive.
If a run was killed mid-migration, Postgres can be left with an
"idle in transaction" zombie session holding locks on `test_postgres_gwN`
(query it via `pg_stat_activity`), which then hangs every subsequent
`CREATE DATABASE`/`DROP DATABASE` attempt indefinitely (not a fast
`OperationalError` — an apparent freeze). Terminate it explicitly
with `pg_terminate_backend(pid)` (a one-off `psycopg` script against
the `postgres` maintenance DB works when no `psql` client is
installed) before retrying.
