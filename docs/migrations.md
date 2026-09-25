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

## The release still serving reads the new schema

The Argo CD PreSync hook (`prepare-helm/templates/job.yaml` in the
infrastructure repository) runs `migrate_schemas` BEFORE the new image
rolls. Until the last old backend and Celery pod exits, the previous
release queries the migrated schema — from the moment each schema's
migrations commit, through the rest of the hook and the rollout. The
Django ORM names every column in its SELECT and INSERT, so:

- **a column or table that disappears** breaks the old code
  (`RemoveField`, `DeleteModel`, `RenameField`, `RenameModel`,
  `AlterModelTable`, an `AlterField` that changes `db_column`);
- **a NOT NULL column added to an existing table without `db_default`**
  breaks the old INSERTs. `AddField(..., default=...)` is not enough:
  Django sets the default for the backfill and drops it straight after
  `ADD COLUMN`. Only `db_default` stays in the database;
- **an existing column made NOT NULL without `db_default`** breaks old
  writes that still send NULL.

An added nullable column, or one with `db_default`, is safe: the old code
never names it.

### The rule

A migration may only add. Anything that removes, renames or tightens
ships in two releases:

1. **Release N (expand).** The code stops reading and writing the old
   shape. The migration takes it out of Django's STATE only, with
   `SeparateDatabaseAndState(state_operations=[...], database_operations=[])`,
   and first gives every column the new code stops supplying a
   `db_default` or `null=True`, so the new code's INSERTs still work.
   A new NOT NULL column carries `db_default`.
2. **Release N+1 (contract), once N is what production runs.** A
   `RunSQL` does the physical `DROP`, and the migration declares which
   expand migration it finishes:

   ```python
   class Migration(migrations.Migration):
       contract_of = [("page_config", "0025_navigation_drop_json_from_state")]
       operations = [
           migrations.RunSQL(
               "ALTER TABLE page_config_navigationmenu "
               "DROP COLUMN IF EXISTS items",
               reverse_sql="ALTER TABLE page_config_navigationmenu "
               "ADD COLUMN items jsonb NULL",
           ),
       ]
   ```

   `RemoveField` cannot do that step: the field left the state in N.

`page_config` 0025/0026 is the worked precedent (0026 predates
`contract_of`).

### Enforcement: `migration_preflight`

`manage.py migration_preflight` (`core/db/migration_safety.py`) checks
every schema `migrate_schemas` would touch — `public` and every tenant
row — against the migrations already applied to it. `makemigrations
--check` keeps each release's models equal to its migration state, so
that applied state IS the serving release's model state. A pending
operation blocks when its database effect removes, renames or tightens a
(table, column) that exists there. It runs:

- **in CI** (the `Migration Check` job): the database is migrated at the
  base commit, then the head's preflight judges the pending migrations,
  then the head migrates on top;
- **as step 0 of the PreSync hook**, before `migrate_schemas`. A failure
  stops the sync before any DDL runs, and the old pods keep serving.

Because it compares against the database rather than the git history,
it also catches a deploy that skips a release: pinning production from
N-1 straight to N+1 leaves the expand migration pending next to its
contract, and `contract_of` refuses that. Deploy N first.

What it decides:

- tables and columns the applied state does not have are always safe,
  so a fresh schema (`tenant_create`, CI, the test database) passes;
- `SeparateDatabaseAndState` is judged by its `database_operations`;
- operations the router keeps out of a schema are ignored there (a
  TENANT_APPS drop never blocks `public`);
- `RunSQL` that drops, renames or sets NOT NULL needs `contract_of`, and
  every migration it names must have been applied by an EARLIER deploy;
- `RunPython` and column type changes are not classified: review them.

**Escape hatch.** `accepted_downtime = "<reason>"` on a migration prints
its findings as `ACCEPTED` and does not block. Use it only for an
agreed maintenance window; the reason is the record.

### Worked example: the shared SEO columns

v3.77.0 moved `seo_title` / `seo_description` / `seo_keywords` from the
shared rows onto the parler translations in one release (`blog`
0035–0037, `page_config` 0028–0030, `product` 0044–0046). Step 1's
`RenameField seo_title → shared_seo_title` was a real
`ALTER TABLE ... RENAME COLUMN`, so the previous release's
`SELECT ... "seo_title"` failed with `column ... does not exist` for the
length of the rollout; `product` 0046 also dropped
`historicalproduct.seo_*`, which every old Product save writes. The
translated columns were added without `db_default` as well. The
preflight blocks all three.

Parler refuses a translated field with the shared model's field name,
so the shared field has to leave the state name before the translated
one arrives — in STATE only:

- **Release N, 0035:** a real `AlterField` giving each shared column
  `db_default=""` (the new code no longer writes it); then
  `SeparateDatabaseAndState(database_operations=[], state_operations=[
  RenameField("blogpost", "seo_title", "shared_seo_title"),
  AlterField("blogpost", "shared_seo_title",
  CharField(..., db_column="seo_title"))])`, so the column keeps its
  name; then `AddField` of the translated fields with `default=""` and
  `db_default=""`. The `historicalproduct` columns leave the state the
  same way.
- **Release N, 0036:** the copy, unchanged.
- **Release N, 0037:** `SeparateDatabaseAndState(state_operations=[
  RemoveField(... "shared_seo_*")], database_operations=[])`.
- **Release N+1, 0038:** `contract_of = [("blog", "0037_...")]`; a
  `RunSQL` that re-copies whatever the old pods wrote to the shared
  columns during N's rollout into still-empty translations, then
  `ALTER TABLE blog_blogpost DROP COLUMN IF EXISTS seo_title, ...`.

### Rollback

After a contract migration runs, the image can roll back to N, not to
N-1: N-1 still reads the dropped columns.
