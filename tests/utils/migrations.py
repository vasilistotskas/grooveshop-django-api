"""Drive the real ``MigrationExecutor`` to a migration, in reasonable time.

Migration tests migrate the test database, which is built at the latest
migration, back to the state they test and forward again. Django unapplies
migrations in ``MigrationExecutor._migrate_all_backwards``: it walks the
full plan (every app's chain, ordered from the leaf nodes sorted by app
label), renders the project state at the first migration it has to
unapply, and from there mutates that rendered state through EVERY applied
migration up to the last one it unapplies. Each of those mutations
re-renders the models it touches (about 0.4 s apiece, measured
2026-11-19).

So the cost of one rollback is set by how far apart, in the full plan, the
migrations it unapplies are, not by how many there are. On 2026-11-19
``agent.0001`` depended on ``order.0106``, so the ``agent`` chain pulled
``order`` up to 0106 near the front of the plan, while a newer order
migration that no other app depends on landed after some 200 migrations of
other apps. Rolling
order back from such a leaf to 0089 then took about 50 s instead of 2 s,
and the order migration tests pushed a CI shard past its timeout.

``migrate`` unapplies in steps instead: one step per contiguous run of the
full plan (runs are split wherever an applied migration that stays applied
sits between two that are unapplied), newest run first. Each step renders
once, and nothing outside the migrations under test is mutated while
rendered. Forward migrations are left to Django: ``_migrate_all_forwards``
renders once and applies only what it runs.
"""

from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

from core.db.migrate_command import settle_deferred_constraints

type MigrationKey = tuple[str, str | None]


def _settle(action: str, migration=None, fake: bool = False) -> None:
    # A test migrates inside its ``TestCase`` transaction, as a store
    # provisioned in one does: settle each migration's queued checks the
    # way ``migrate_schemas``' command does.
    settle_deferred_constraints(connection, action, fake=fake)


def _executor() -> MigrationExecutor:
    return MigrationExecutor(connection, progress_callback=_settle)


def migrate(*targets: MigrationKey) -> MigrationLoader:
    """Migrate the default database to ``targets``; return a loader that
    reads the result (its ``applied_migrations`` are current)."""
    executor = _executor()
    plan = executor.migration_plan(list(targets))
    if plan and all(backwards for _, backwards in plan):
        for step in _earlier_steps(executor, plan):
            executor.migrate(step)
            executor = _executor()
    executor.migrate(list(targets))
    executor.loader.build_graph()
    return executor.loader


def migrate_to(*targets: MigrationKey):
    """Migrate the default database to ``targets``; return the historical
    apps at that state, as a ``RunPython`` there would receive them."""
    return migrate(*targets).project_state(list(targets)).apps


def _earlier_steps(
    executor: MigrationExecutor, plan
) -> list[list[MigrationKey]]:
    """The targets that unapply every run of ``plan`` but the earliest,
    newest run first. The caller's own targets then unapply the earliest
    run, which is the only one left."""
    graph = executor.loader.graph
    applied = set(executor.loader.applied_migrations)
    to_unapply = {
        (migration.app_label, migration.name) for migration, _ in plan
    }
    full_plan = executor.migration_plan(graph.leaf_nodes(), clean_start=True)

    runs: list[list[tuple[str, str]]] = [[]]
    for migration, _ in full_plan:
        key = (migration.app_label, migration.name)
        if key in to_unapply:
            runs[-1].append(key)
        elif key in applied and runs[-1]:
            runs.append([])
    runs = [run for run in runs if run]

    steps = []
    for run in reversed(runs[1:]):
        earliest: dict[str, str] = {}
        for app_label, name in run:
            earliest.setdefault(app_label, name)
        steps.append(
            [
                (app_label, _parent_in_app(graph, app_label, name))
                for app_label, name in earliest.items()
            ]
        )
    return steps


def _parent_in_app(graph, app_label: str, name: str) -> str | None:
    """The migration of ``app_label`` that ``name`` follows; ``None`` for
    its first migration (migrating to ``(app_label, None)`` unapplies it
    all)."""
    parents = [
        parent.key[1]
        for parent in graph.node_map[(app_label, name)].parents
        if parent.key[0] == app_label
    ]
    return parents[0] if parents else None
