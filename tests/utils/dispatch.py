"""Assertions on a task published through ``tenant.celery.dispatch_on_commit``.

The helper publishes with ``task.apply_async(args=..., kwargs=...,
headers={"_schema_name": ...})``, so a test that patched ``task.delay`` no
longer sees the call. Patch ``apply_async`` and assert with these.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from unittest import mock


def assert_published_once(
    published: mock.Mock,
    args: Sequence[Any] | None = None,
    kwargs: Mapping[str, Any] | None = None,
) -> None:
    """The patched ``apply_async`` ran once, with these task arguments."""
    published.assert_called_once()
    call = published.call_args.kwargs
    if args is not None:
        assert call["args"] == list(args)
    if kwargs is not None:
        assert call["kwargs"] == dict(kwargs)
    assert "_schema_name" in call["headers"]
