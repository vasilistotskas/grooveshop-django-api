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
