"""Store settings for the loyalty tests, at the ``Setting`` boundary."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch


@contextmanager
def loyalty_settings(**values) -> Iterator[None]:
    """Answer both ways a store setting is read: ``Setting.get`` for one
    name and ``Setting.objects.filter`` for ``store_settings``' batch.

    ``LOYALTY_ENABLED`` defaults to on; any key not given reads as a missing
    row, so as its declared default.
    """
    values = {"LOYALTY_ENABLED": True, **values}

    def rows(name__in):
        return [
            SimpleNamespace(name=name, value=values[name])
            for name in name__in
            if name in values
        ]

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "extra_settings.models.Setting.get",
                side_effect=lambda key, default=None: values.get(key, default),
            )
        )
        stack.enter_context(
            patch(
                "extra_settings.models.Setting.objects.filter", side_effect=rows
            )
        )
        yield
