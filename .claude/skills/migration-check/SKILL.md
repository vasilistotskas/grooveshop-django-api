---
name: migration-check
description: >
  Check for missing or conflicting Django migrations. Use when models are modified,
  after scaffolding new endpoints, or before committing. Runs makemigrations --check
  and validates migration graph consistency. Also use when the user says things like
  "check migrations", "are migrations up to date", "migration status", or after any
  model field changes.
---

# Migration Check

Validate that Django migrations are consistent with model definitions.

## Steps

1. **Check for missing migrations**:
   ```bash
   uv run python manage.py makemigrations --check --dry-run
   ```
   - Exit code 0 = all migrations are up to date
   - Exit code 1 = there are model changes without migrations

2. **If missing migrations are detected**, show which apps are affected:
   ```bash
   uv run python manage.py makemigrations --dry-run
   ```
   - Show the user the proposed migration names and changes
   - Ask if they want to generate the migrations

3. **Generate migrations** (if user approves):
   ```bash
   uv run python manage.py makemigrations
   ```

4. **Check for migration conflicts** (merge conflicts from branches):
   ```bash
   uv run python manage.py showmigrations --plan | grep -E "^\[ \]|UNMIGRATED"
   ```

5. **Validate migration graph** (can all migrations apply cleanly):
   ```bash
   uv run python manage.py migrate --check
   ```
   - If this fails, there may be circular dependencies or broken migrations

6. **Deploy safety** (before applying the new migrations locally — the
   check judges what is still pending against what is applied):
   ```bash
   uv run python manage.py migration_preflight
   ```
   - Exit 1 names each operation that would break the release still
     serving (a drop, a rename, a NOT NULL column without `db_default`)
     and the schemas it hits. CI and the PreSync hook run the same
     command, so a failure here fails the deploy too. Fix per
     `docs/migrations.md`, or run the `migration-safety-audit` skill.

## Common Issues and Fixes

| Issue | Cause | Fix |
|-------|-------|-----|
| Missing migration | Model field added/changed without makemigrations | Run `uv run python manage.py makemigrations` |
| Conflicting migrations | Two branches added migrations to same app | Run `uv run python manage.py makemigrations --merge` |
| Dependency error | Migration references non-existent migration | Check `dependencies` list in the migration file |
| RunPython error | Data migration has a bug | Fix the `RunPython` callable in the migration |

## After Generating Migrations

- Review the generated migration file to ensure it matches expectations
- Verify no data loss operations (field removal, type changes) without explicit confirmation
- A new NOT NULL column on an existing table needs `db_default`, not just `default` — the old release's INSERTs do not name it
- Run `uv run pytest tests/unit/ -x --timeout=60` to ensure migrations don't break tests
