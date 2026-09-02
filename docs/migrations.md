# Migration Safety Rules

## Index additions on large tables

GIN/BTree index additions on large tables MUST use `atomic = False` +
`RunSQL("CREATE INDEX CONCURRENTLY ...")` to avoid holding ACCESS EXCLUSIVE
locks. Raw `AddIndex` is only safe on new/small tables.

## PreSync Argo hook ordering

Migrations land BEFORE the new image rolls. Schema-changing migrations MUST
be backwards-compatible (additive only) OR split across two releases.
