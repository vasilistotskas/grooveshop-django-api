"""Loyalty configuration for tests that drive ``LoyaltyService`` directly."""

from __future__ import annotations

from unittest.mock import _patch, patch


def loyalty_settings(**values) -> _patch:
    """Patch ``Setting.get`` as ``loyalty.services`` reads it.

    ``LOYALTY_ENABLED`` defaults to on; any key not given falls back to
    the caller's ``default``, as ``Setting.get`` does for a missing row.
    Use it as a context manager or decorator, like any ``patch``.
    """
    values = {"LOYALTY_ENABLED": True, **values}
    return patch(
        "loyalty.services.Setting.get",
        side_effect=lambda key, default=None: values.get(key, default),
    )
