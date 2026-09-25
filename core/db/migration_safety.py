"""Classify pending migrations against the release that is still serving.

The Argo CD PreSync hook migrates BEFORE the new image rolls out, so the
previous release keeps querying the new schema until its last pod exits.
``makemigrations --check`` keeps every release's models equal to its
migration state, which makes the state of the migrations already applied
to a schema the model state of the release serving it. A pending
operation is therefore unsafe when its DATABASE effect removes, renames
or tightens a (table, column) that exists in that applied state:

* a table or column disappears (``RemoveField``, ``DeleteModel``,
  ``RenameField``, ``RenameModel``, ``AlterModelTable``, an ``AlterField``
  that changes the column name) — the old ORM selects it by name;
* a column that exists becomes NOT NULL with no ``db_default`` — the old
  code may still write NULL;
* a NOT NULL column without ``db_default`` is added to a table that
  exists — Django drops the migration's default right after ``ADD
  COLUMN``, and the old INSERTs do not name the column.

Operations on tables the applied state does not have are always safe, so
a fresh schema (``tenant_create``, CI, the test database) passes by
construction. ``SeparateDatabaseAndState`` is judged by its
``database_operations`` only. ``RunSQL`` is opaque: SQL that drops,
renames or sets NOT NULL is allowed only in a migration that declares
``contract_of``, and only once every migration it names was applied by an
earlier deploy. ``RunPython`` is not classified.

``accepted_downtime = "<reason>"`` on a migration reports its findings
without blocking. See ``docs/migrations.md``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from django.db import models
from django.db.backends.utils import truncate_name
from django.db.migrations import operations
from django.db.migrations.migration import Migration
from django.db.migrations.operations.base import Operation
from django.db.migrations.operations.fields import FieldOperation
from django.db.migrations.state import ModelState, ProjectState
from django.db.models.fields import NOT_PROVIDED

type Allow = Callable[..., bool]
"""``allow(app_label, model_name=None, **hints)`` — the router's verdict
for the schema being checked, as ``Operation.allow_migrate_model`` asks
it."""

type Key = tuple[str, str | None]
"""``(table, column)``; ``column`` is ``None`` for the table itself."""

type MigrationKey = tuple[str, str]

_SCHEMA_OPERATIONS = (
    operations.AddField,
    operations.RemoveField,
    operations.AlterField,
    operations.RenameField,
    operations.DeleteModel,
    operations.RenameModel,
    operations.AlterModelTable,
)

_DESTRUCTIVE_SQL = re.compile(
    r"\bDROP\s+TABLE\b"
    r"|\bALTER\s+TABLE\b[^;]*?\b(?:"
    r"RENAME\b(?!\s+CONSTRAINT\b)"
    r"|DROP\s+(?!CONSTRAINT\b|DEFAULT\b|NOT\s+NULL\b|EXPRESSION\b|IDENTITY\b)"
    r"|SET\s+NOT\s+NULL\b"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    migration: MigrationKey
    operation: str
    reason: str
    accepted_downtime: str = ""

    @property
    def blocking(self) -> bool:
        return not self.accepted_downtime


def _table(
    app_label: str, model_state: ModelState, max_length: int | None
) -> str:
    # django/db/models/options.py: Options.contribute_to_class
    return model_state.options.get("db_table") or truncate_name(
        f"{app_label}_{model_state.name_lower}", max_length
    )


def _column(name: str, field: models.Field) -> str | None:
    if isinstance(field, models.CompositePrimaryKey):
        return None
    if isinstance(field, models.ForeignObject) and not isinstance(
        field, models.ForeignKey
    ):
        return None
    if field.db_column:
        return field.db_column
    # ForeignKey.get_attname (fields/related.py) needs a bound field.
    return f"{name}_id" if isinstance(field, models.ForeignKey) else name


def _model_keys(
    app_label: str, model_state: ModelState | None, max_length: int | None
) -> dict[Key, models.Field | None]:
    """Every table and column one model owns in the database."""
    if model_state is None:
        return {}
    options = model_state.options
    if not options.get("managed", True) or options.get("proxy"):
        return {}
    table = _table(app_label, model_state, max_length)
    keys: dict[Key, models.Field | None] = {(table, None): None}
    for name, field in model_state.fields.items():
        if field.many_to_many:
            # Only an auto-created join table belongs to this model; an
            # explicit ``through`` model is a model of its own.
            if field.remote_field.through is None:
                # fields/related.py: ManyToManyField._get_m2m_db_table
                join = field.db_table or truncate_name(
                    f"{table}_{name}", max_length
                )
                keys[(join, None)] = field
            continue
        column = _column(name, field)
        if column:
            keys[(table, column)] = field
    return keys


def schema_keys(
    state: ProjectState, allow: Allow, max_length: int | None
) -> frozenset[Key]:
    """The tables and columns the serving release can read."""
    keys: set[Key] = set()
    for (app_label, model_name), model_state in state.models.items():
        if allow(app_label, model_name=model_name):
            keys.update(_model_keys(app_label, model_state, max_length))
    return frozenset(keys)


def _touched_models(operation: Operation) -> tuple[str, ...]:
    if isinstance(operation, FieldOperation):
        return (operation.model_name_lower,)
    if isinstance(operation, operations.RenameModel):
        return (operation.old_name_lower, operation.new_name_lower)
    return (operation.name_lower,)


def _describe(key: Key) -> str:
    table, column = key
    return f"table {table}" if column is None else f"column {table}.{column}"


def _needs_a_value(field: models.Field | None) -> bool:
    return (
        field is not None
        and not field.many_to_many
        and not field.null
        and field.db_default is NOT_PROVIDED
        and not field.primary_key
        and not isinstance(field, models.GeneratedField)
    )


def _schema_operation(
    operation: Operation,
    app_label: str,
    state: ProjectState,
    serving: frozenset[Key],
    allow: Allow,
    max_length: int | None,
) -> list[str]:
    """Apply ``operation`` to ``state``; return why it is unsafe, if it is."""
    names = [
        name
        for name in _touched_models(operation)
        if allow(app_label, model_name=name)
    ]
    before: dict[Key, models.Field | None] = {}
    for name in names:
        before.update(
            _model_keys(
                app_label, state.models.get((app_label, name)), max_length
            )
        )
    operation.state_forwards(app_label, state)
    after: dict[Key, models.Field | None] = {}
    for name in names:
        after.update(
            _model_keys(
                app_label, state.models.get((app_label, name)), max_length
            )
        )

    reasons = []
    gone = {key for key in before.keys() - after.keys() if key in serving}
    gone_tables = {table for table, column in gone if column is None}
    for key in sorted(gone, key=str):
        if key[1] is not None and key[0] in gone_tables:
            continue
        reasons.append(
            f"removes {_describe(key)}, which the serving release still reads"
        )
    for key in sorted(before.keys() & after.keys(), key=str):
        old, new = before[key], after[key]
        if (
            key in serving
            and old is not None
            and old.null
            and _needs_a_value(new)
        ):
            reasons.append(
                f"makes {_describe(key)} NOT NULL without db_default; the "
                "serving release may still write NULL"
            )
    if not isinstance(operation, operations.AddField):
        return reasons  # a rename's new name is not a new column
    for key in sorted(after.keys() - before.keys(), key=str):
        if (key[0], None) in serving and _needs_a_value(after[key]):
            reasons.append(
                f"adds NOT NULL {_describe(key)} without db_default; "
                "the serving release's INSERTs do not name it"
            )
    return reasons


def _sql_text(sql: str | Iterable) -> str:
    """``RunSQL.sql``: a string, or a list of strings / (sql, params)."""
    if isinstance(sql, str):
        return sql
    return ";\n".join(
        str(item[0] if isinstance(item, list | tuple) else item) for item in sql
    )


def _run_sql(
    operation: operations.RunSQL,
    migration: Migration,
    allow: Allow,
    applied: frozenset[MigrationKey],
    pending: frozenset[MigrationKey],
) -> list[str]:
    if not allow(migration.app_label, **operation.hints):
        return []
    if not _DESTRUCTIVE_SQL.search(_sql_text(operation.sql)):
        return []
    if not any(app == migration.app_label for app, _ in applied):
        return []  # a fresh schema: nothing serves it yet
    contract_of: Iterable[MigrationKey] = getattr(migration, "contract_of", ())
    if not contract_of:
        return [
            (
                "RunSQL drops, renames or sets NOT NULL; declare contract_of "
                "naming the migration that took the columns out of state"
            )
        ]
    reasons = []
    for app, name in contract_of:
        target = (app, name)
        if target in pending:
            reasons.append(
                f"contract_of {target[0]}.{target[1]} is pending in this "
                "same deploy; deploy the release that contains it first"
            )
        elif target not in applied:
            reasons.append(
                f"contract_of {target[0]}.{target[1]} is not an applied "
                "migration of this schema"
            )
    return reasons


@dataclass(frozen=True)
class _Walk:
    serving: frozenset[Key]
    allow: Allow
    applied: frozenset[MigrationKey]
    pending: frozenset[MigrationKey]
    max_length: int | None


def _operation_reasons(
    operation: Operation,
    migration: Migration,
    state: ProjectState,
    walk: _Walk,
) -> list[str]:
    """Apply ``operation`` to ``state``; return why it is unsafe."""
    app_label = migration.app_label
    if isinstance(operation, operations.SeparateDatabaseAndState):
        # special.py: database_forwards walks database_operations on a
        # clone of the state; only the state_operations move the state.
        database_state = state.clone()
        reasons = []
        for database_operation in operation.database_operations:
            reasons += _operation_reasons(
                database_operation, migration, database_state, walk
            )
        operation.state_forwards(app_label, state)
        return reasons
    if isinstance(operation, _SCHEMA_OPERATIONS):
        return _schema_operation(
            operation,
            app_label,
            state,
            walk.serving,
            walk.allow,
            walk.max_length,
        )
    reasons = []
    if isinstance(operation, operations.RunSQL):
        reasons = _run_sql(
            operation,
            migration,
            walk.allow,
            walk.applied,
            walk.pending,
        )
    operation.state_forwards(app_label, state)
    return reasons


def classify(
    plan: Iterable[Migration],
    state: ProjectState,
    *,
    allow: Allow,
    applied: frozenset[MigrationKey],
    max_length: int | None,
) -> list[Finding]:
    """Findings for ``plan`` (forwards, in order) on top of ``state``.

    ``state`` is the applied state — the serving release's models. It is
    left untouched; the walk runs on a clone.
    """
    plan = list(plan)
    state = state.clone()
    walk = _Walk(
        serving=schema_keys(state, allow, max_length),
        allow=allow,
        applied=applied,
        pending=frozenset((m.app_label, m.name) for m in plan),
        max_length=max_length,
    )
    findings = []
    for migration in plan:
        accepted = getattr(migration, "accepted_downtime", "")
        for operation in migration.operations:
            description = operation.describe()
            for reason in _operation_reasons(operation, migration, state, walk):
                findings.append(
                    Finding(
                        (migration.app_label, migration.name),
                        description,
                        reason,
                        accepted,
                    )
                )
    return findings
