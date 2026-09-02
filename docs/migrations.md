# Migration Safety Rules

## Index additions on large tables

A plain `CREATE INDEX` (what `AddIndex` emits) takes a `SHARE` lock on the
table for the whole build: reads continue, every write blocks until the
index exists. On a large or live table that is an outage, so index additions
there MUST be built concurrently, which PostgreSQL only allows outside a
transaction:

- Set `atomic = False` on the migration.
- Prefer `django.contrib.postgres.operations.AddIndexConcurrently` (and
  `RemoveIndexConcurrently` for the reverse); Django tracks the state and
  writes the reverse for you.
- If raw SQL is unavoidable, use `RunSQL("CREATE INDEX CONCURRENTLY ...",
  reverse_sql="DROP INDEX CONCURRENTLY ...", state_operations=[AddIndex(...)])`
  so the migration state still matches the model.

Plain `AddIndex` stays fine on new or small tables.

## PreSync Argo hook ordering

Migrations land BEFORE the new image rolls. Schema-changing migrations MUST
be backwards-compatible (additive only) OR split across two releases.
