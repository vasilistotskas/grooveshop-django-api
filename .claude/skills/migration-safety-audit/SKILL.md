---
name: migration-safety-audit
description: >
  Audit Django migrations for backwards-compatibility under the Argo CD PreSync
  hook deploy model (schema lands BEFORE new code rolls out). Use after
  generating a migration, before committing one, when reviewing a PR that
  touches `*/migrations/*.py`, or when the user says "is this migration safe",
  "audit migration", "check migration safety", "will this break prod".
---

# Migration Safety Audit

GrooveShop deploys via an Argo CD PreSync hook (the `prepare-helm` chart in grooveshop-infrastructure) that runs `migrate` **before** the new image rolls. Old pods are still serving traffic when the new schema lands. Destructive migrations break those old pods.

This skill audits the latest migration (or a named one) for that hazard and prescribes the fix.

## Steps

### 1. Identify the migration to audit

If the user named one, use that path. Otherwise find the most recent migration across all apps:

```bash
ls -t */migrations/0*.py 2>/dev/null | head -5
```

### 2. Read the migration file

Read it with the `Read` tool. Look at the `operations = [...]` list.

### 3. Classify each operation

| Operation | Verdict | Reason |
|-----------|---------|--------|
| `AddField` (with `db_default=` or `null=True`) | SAFE | Old code ignores the new column |
| `AddField` (NOT NULL with only `default=`) on an existing table | UNSAFE | Django drops the default right after `ADD COLUMN`; old INSERTs omit the column → IntegrityError |
| `CreateModel` | SAFE | Old code doesn't reference the table |
| `AddIndex` / `AddConstraint` | USUALLY SAFE | Watch for long lock on large tables; consider `CONCURRENTLY` |
| `RemoveField` | UNSAFE | Old pods still SELECT/INSERT the column → 500s |
| `DeleteModel` | UNSAFE | Old pods still query the table → 500s |
| `RenameField` | UNSAFE | Both old name (old pods) and new name (new pods) need to exist simultaneously |
| `RenameModel` | UNSAFE | Same reason as RenameField |
| `AlterField` (type widening, e.g. `CharField(50)` → `CharField(100)`) | SAFE | Old code can still read |
| `AlterField` (type narrowing or incompatible change) | UNSAFE | Truncates/breaks old writes |
| `AlterField` (`null=True` → `null=False` without `db_default`) | UNSAFE | Old pods may insert NULL → IntegrityError |
| `AlterField` (`null=False` → `null=True`) | SAFE | More permissive |
| `AlterUniqueTogether` / `AlterIndexTogether` | SAFE-ISH | Watch for duplicate-data violations |
| `RunPython` | DEPENDS | Read the callable; classify what it does to data |
| `RunSQL` | DEPENDS | Read the SQL; same logic. A drop / rename / SET NOT NULL needs `contract_of` |
| `SeparateDatabaseAndState` | JUDGE `database_operations` | `state_operations` never touch the database |

Then run the automated gate against a database that is still at the
previous release (not yet migrated). It checks every schema and names
each blocking operation:

```bash
uv run python manage.py migration_preflight
```

### 4. Report findings

For each UNSAFE op, output:
- File and operation (e.g. `product/migrations/0042_remove_product_legacy_sku.py: RemoveField`)
- Why it's unsafe (which queries from old code break)
- The two-release split that fixes it

### 5. Prescribe the fix

**Removing a field or model (expand / contract):**
- Release N: the code stops reading and writing it. The migration gives
  the column a `db_default` or `null=True` (the new code stops supplying
  it), then takes the field out of STATE only:
  `SeparateDatabaseAndState(state_operations=[RemoveField(...)], database_operations=[])`.
- Release N+1, once N is what production runs: a `RunSQL` drops the
  column in a migration that declares
  `contract_of = [("<app>", "<release N migration>")]`. `RemoveField`
  cannot — the field already left the state. A deploy that skips N is
  refused by `migration_preflight`.

**Replacing a shape (rename, move, type change):**
- Release N: `AddField` the new field (`null=True` or `db_default=`) +
  RunPython copy; the code dual-writes and reads new with fallback.
- Release N+1: the code uses only the new field; the old one leaves the
  state as above.
- Release N+2: the `RunSQL` drop with `contract_of`.
- When only the Python name changes, a state-only `RenameField` plus
  `AlterField(db_column=<old column>)` touches no column and is safe in
  one release.

`docs/migrations.md` has the full rule and the worked SEO example.

### 6. Check for long-running operations on hot tables

```bash
grep -l "AddIndex\|AddConstraint" <migration_path>
```

If the table is large (`Order`, `Product`, `OrderItem`), warn: the PreSync Job has `activeDeadlineSeconds=1800` for the whole hook, every schema included — a long `CREATE INDEX` may exceed it, and it blocks writes while it runs. Suggest `AddIndexConcurrently` or splitting.

### 7. Verify no `--fake` is needed

If the user is renaming an app or moving a model:
```bash
uv run python manage.py migrate --plan | head -20
```
Flag any migrations that would require `--fake` to apply cleanly in a fresh environment.

## Output Format

```
Migration: <path>
Verdict: SAFE | UNSAFE | NEEDS REVIEW

Findings:
  [UNSAFE] line N: <Operation> — <reason>
    Old-code break: <which queries fail>
    Fix: <two-release split or specific remediation>

  [SAFE]   line M: <Operation> — <why it's fine>

Recommendation:
  <single sentence: ship as-is | split into two releases | rewrite as additive>
```

## Notes

- Never edit the migration file yourself — the user owns the model design decision; this skill only reports.
- The PreSync hook deploy model and the rule are in `docs/migrations.md`.
- If the user explicitly accepts downtime for a release, UNSAFE migrations become acceptable — note that as the user's call; the migration then declares `accepted_downtime = "<reason>"`, which the preflight reports without blocking.
