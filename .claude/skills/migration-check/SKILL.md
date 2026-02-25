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
- Run `uv run pytest tests/unit/ -x --timeout=60` to ensure migrations don't break tests
