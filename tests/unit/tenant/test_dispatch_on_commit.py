"""``dispatch_on_commit`` — the schema hand-off, pinned.

``TenantTask.apply_async`` reads ``connection.schema_name`` when it runs.
Inside a commit hook that is the WRONG moment: the hook fires after the
request's schema context can unwind, and the connection has usually
snapped back to ``public`` by then. The task either dies on
``DoesNotExist`` or, worse, finds a same-named row in the wrong store.

These tests pin the two halves that matter — the schema is read at
REGISTRATION, and the resulting ``apply_async`` call keeps the exact
shape the existing call sites produce, because a dozen tests across the
suite assert that shape verbatim.
"""

from __future__ import annotations

from unittest import mock
from unittest.mock import Mock

from django.db import connection, transaction
from django.test import TestCase

from tenant.celery import dispatch_on_commit


class DispatchOnCommitTests(TestCase):
    def test_args_only_call_keeps_the_shape_the_call_sites_produce(self):
        task = Mock()

        with self.captureOnCommitCallbacks(execute=True):
            dispatch_on_commit(task, [42])

        # No `kwargs=` key. Tests across the suite assert exactly this,
        # and an empty dict would not be the same call.
        task.apply_async.assert_called_once_with(
            args=[42], headers={"_schema_name": connection.schema_name}
        )

    def test_kwargs_only_call_omits_args(self):
        task = Mock()

        with self.captureOnCommitCallbacks(execute=True):
            dispatch_on_commit(task, kwargs={"product_id": 7})

        task.apply_async.assert_called_once_with(
            kwargs={"product_id": 7},
            headers={"_schema_name": connection.schema_name},
        )

    def test_schema_is_read_at_registration_not_at_commit(self):
        """The whole point of the helper.

        `captureOnCommitCallbacks` cannot express this: the suite has an
        autouse fixture that makes `transaction.on_commit` run its
        callback IMMEDIATELY, so the schema would still be the
        registration-time one however the helper was written, and the
        test would pass while proving nothing. Capturing the callback and
        running it after the connection has moved on is what actually
        separates "read now" from "read later" — and "moved on" is
        exactly what a `schema_context` exit does between an inner block
        and the outer transaction's commit.
        """
        task = Mock()
        captured: list = []
        original = connection.schema_name

        with mock.patch.object(transaction, "on_commit", captured.append):
            connection.schema_name = "tenant_under_test"
            try:
                dispatch_on_commit(task, [1])
            finally:
                connection.schema_name = original

        assert captured, "nothing was registered on commit"
        for callback in captured:
            callback()

        task.apply_async.assert_called_once_with(
            args=[1], headers={"_schema_name": "tenant_under_test"}
        )

    def test_explicit_schema_name_wins(self):
        task = Mock()

        with self.captureOnCommitCallbacks(execute=True):
            dispatch_on_commit(task, [1], schema_name="pinned")

        task.apply_async.assert_called_once_with(
            args=[1], headers={"_schema_name": "pinned"}
        )

    def test_caller_headers_are_kept_and_the_schema_added(self):
        task = Mock()

        with self.captureOnCommitCallbacks(execute=True):
            dispatch_on_commit(task, [1], headers={"x-trace": "abc"})

        task.apply_async.assert_called_once_with(
            args=[1],
            headers={
                "x-trace": "abc",
                "_schema_name": connection.schema_name,
            },
        )

    def test_other_apply_async_options_pass_through(self):
        task = Mock()

        with self.captureOnCommitCallbacks(execute=True):
            dispatch_on_commit(task, [1], countdown=30)

        task.apply_async.assert_called_once_with(
            args=[1],
            headers={"_schema_name": connection.schema_name},
            countdown=30,
        )

    def test_on_commit_is_reached_through_the_module(self):
        """The suite monkeypatches ``transaction.on_commit``.

        Binding it at import would make every such patch a no-op, and the
        tests that rely on it would pass while testing nothing.
        """
        seen = []
        real = transaction.on_commit
        transaction.on_commit = lambda fn, **kw: seen.append(fn)
        try:
            dispatch_on_commit(Mock(), [1])
        finally:
            transaction.on_commit = real

        assert len(seen) == 1, (
            "dispatch_on_commit did not go through transaction.on_commit — "
            "it must not bind the function at import time."
        )


def test_helper_is_importable_without_touching_models():
    """`tenant/celery.py` must stay model-free at import.

    It is imported by signal modules that load during app registry
    population; a model import at module level would make that ordering
    fragile.
    """
    import ast
    import inspect

    import tenant.celery as module

    tree = ast.parse(inspect.getsource(module))
    top_level_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom)
    ]
    offenders = [
        ast.unparse(node)
        for node in top_level_imports
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.endswith(".models")
    ]
    assert not offenders, (
        f"tenant/celery.py imports models at module level: {offenders}"
    )
