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

GrooveShop deploys via an Argo CD PreSync hook (`backend-prepare-job.yaml`) that runs `migrate` **before** the new image rolls. Old pods are still serving traffic when the new schema lands. Destructive migrations break those old pods.

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
| `AddField` (with `default=` or `null=True`) | SAFE | Old code ignores the new column |
| `AddField` (NOT NULL, no default) | UNSAFE | Migration will fail on a non-empty table |
| `CreateModel` | SAFE | Old code doesn't reference the table |
| `AddIndex` / `AddConstraint` | USUALLY SAFE | Watch for long lock on large tables; consider `CONCURRENTLY` |
| `RemoveField` | UNSAFE | Old pods still SELECT/INSERT the column → 500s |
| `DeleteModel` | UNSAFE | Old pods still query the table → 500s |
| `RenameField` | UNSAFE | Both old name (old pods) and new name (new pods) need to exist simultaneously |
| `RenameModel` | UNSAFE | Same reason as RenameField |
| `AlterField` (type widening, e.g. `CharField(50)` → `CharField(100)`) | SAFE | Old code can still read |
| `AlterField` (type narrowing or incompatible change) | UNSAFE | Truncates/breaks old writes |
| `AlterField` (`null=True` → `null=False` without default) | UNSAFE | Old pods may insert NULL → IntegrityError |
| `AlterField` (`null=False` → `null=True`) | SAFE | More permissive |
| `AlterUniqueTogether` / `AlterIndexTogether` | SAFE-ISH | Watch for duplicate-data violations |
| `RunPython` | DEPENDS | Read the callable; classify what it does to data |
| `RunSQL` | DEPENDS | Read the SQL; same logic |

### 4. Report findings

For each UNSAFE op, output:
- File and operation (e.g. `product/migrations/0042_remove_product_legacy_sku.py: RemoveField`)
- Why it's unsafe (which queries from old code break)
- The two-release split that fixes it

### 5. Prescribe the fix

For destructive ops, the canonical fix is the **two-release pattern**:

**Release 1 (safe):**
- Add the new column / new table / new field name
- Keep the old column writable
- Update code to dual-write (write both old and new) and read from new with fallback to old
- Deploy. Old pods + new pods both work.

**Release 2 (cleanup):**
- Remove dual-write code, read only from new
- Remove the old column / table / field name in a follow-up migration
- Deploy. By now no code references the old thing.

For `RenameField` specifically, the correct sequence is:
1. Release 1: `AddField(new_name)` + RunPython copy + dual-write
2. Release 2: `RemoveField(old_name)` once all reads/writes target `new_name`

### 6. Check for long-running operations on hot tables

```bash
grep -l "AddIndex\|AddConstraint" <migration_path>
```

If the table is large (`Order`, `Product`, `OrderItem`), warn: the PreSync Job has `activeDeadlineSeconds=600` — a long `CREATE INDEX` may exceed it. Suggest `AddIndexConcurrently` or splitting.

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
- The PreSync hook deploy model is documented in the project memory (`Backend Init Steps Run as Argo PreSync Hook` entry, added 2026-04-06).
- If the user explicitly accepts downtime for a release, UNSAFE migrations become acceptable — note that as the user's call.
