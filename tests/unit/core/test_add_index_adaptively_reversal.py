"""Reversing `AddIndexAdaptively` must DROP the index, on every backend.

The non-PostgreSQL branch delegated to `RemoveIndex.database_backwards`.
Reversing a *removal* means adding, so Django implements that method
with `schema_editor.add_index()` — the reverse of this operation would
have created the index it is meant to drop, and failed with a duplicate
name wherever it already existed.

The PostgreSQL path was exercised for real against the three tenant
schemas when migration 0053 landed; this is the branch that never runs
here and so was never observed.
"""

from __future__ import annotations

from unittest import mock

from django.db import models

from core.db.migration_operations import AddIndexAdaptively


def _schema_editor(vendor="sqlite"):
    editor = mock.Mock()
    editor.connection.alias = "default"
    editor.connection.vendor = vendor
    editor.connection.in_atomic_block = False
    return editor


def _state():
    state = mock.Mock()
    state.apps.get_model.return_value = mock.Mock()
    return state


def test_reversal_removes_the_index():
    index = models.Index(fields=["created_at"], name="probe_ix")
    operation = AddIndexAdaptively("order", index)
    editor = _schema_editor()

    operation.database_backwards("order", editor, _state(), _state())

    assert not editor.add_index.called, (
        "reversal ADDED the index it was supposed to drop"
    )
    assert editor.remove_index.called, "reversal did not drop the index"
    assert editor.remove_index.call_args.args[1] is index


def test_the_router_guard_still_applies_on_reversal():
    operation = AddIndexAdaptively(
        "order", models.Index(fields=["created_at"], name="probe_ix")
    )
    editor = _schema_editor()

    with mock.patch.object(
        AddIndexAdaptively, "allow_migrate_model", return_value=False
    ):
        operation.database_backwards("order", editor, _state(), _state())

    assert not editor.remove_index.called
    assert not editor.add_index.called
