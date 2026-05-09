"""Integration tests for Stripe webhook tenant re-entry (FIX 1).

Verifies that the ``@with_tenant_schema_from_event`` decorator correctly
routes webhook processing into the right tenant schema — or falls back to
the public schema when the tenant metadata is absent or invalid.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from order.signals._tenant import (
    _tenant_schema_from_event,
    with_tenant_schema_from_event,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(metadata=None, event_id="evt_test"):
    """Build a minimal mock dj-stripe Event with the given metadata dict."""
    event = MagicMock()
    event.id = event_id
    event.data = {
        "object": {
            "id": "pi_test_123",
            "metadata": metadata or {},
        }
    }
    return event


# ---------------------------------------------------------------------------
# Unit tests for _tenant_schema_from_event
# ---------------------------------------------------------------------------


class TestTenantSchemaFromEvent:
    def test_extracts_schema_from_payment_intent_metadata(self):
        event = _make_event(metadata={"tenant_schema": "webside"})
        assert _tenant_schema_from_event(event) == "webside"

    def test_falls_back_to_public_when_no_metadata(self):
        from django_tenants.utils import get_public_schema_name

        event = _make_event(metadata={})
        assert _tenant_schema_from_event(event) == get_public_schema_name()

    def test_falls_back_to_public_on_malformed_event(self):
        from django_tenants.utils import get_public_schema_name

        event = MagicMock()
        event.id = "evt_bad"
        # data is not a dict-like object
        del event.data
        assert _tenant_schema_from_event(event) == get_public_schema_name()

    def test_extracts_schema_from_nested_payment_intent(self):
        """charge.refunded / charge.dispute.created embed the PI as a dict."""
        event = MagicMock()
        event.id = "evt_charge"
        event.data = {
            "object": {
                "id": "ch_test",
                "payment_intent": {
                    "id": "pi_test",
                    "metadata": {"tenant_schema": "tenant_b"},
                },
            }
        }
        assert _tenant_schema_from_event(event) == "tenant_b"

    def test_strips_whitespace_from_schema_name(self):
        event = _make_event(metadata={"tenant_schema": "  webside  "})
        assert _tenant_schema_from_event(event) == "webside"


# ---------------------------------------------------------------------------
# Integration tests for @with_tenant_schema_from_event
# ---------------------------------------------------------------------------


class TestWithTenantSchemaFromEvent:
    """Tests for the decorator that wraps handlers in schema_context."""

    def test_no_event_kwarg_calls_func_directly(self):
        """When no event is passed the decorator is transparent."""
        called_with = {}

        @with_tenant_schema_from_event
        def _handler(sender, **kwargs):
            called_with.update(kwargs)

        _handler(sender=None, foo="bar")
        assert called_with == {"foo": "bar"}

    @pytest.mark.django_db
    def test_handler_runs_in_public_schema_when_no_tenant_schema(self):
        """Event without tenant_schema metadata → handler runs in public schema."""
        from django.db import connection

        executed_in = {}

        @with_tenant_schema_from_event
        def _handler(sender, **kwargs):
            executed_in["schema"] = connection.schema_name

        event = _make_event(metadata={})  # no tenant_schema
        _handler(sender=None, event=event)
        assert executed_in["schema"] == "public"

    @pytest.mark.django_db
    def test_unknown_schema_logs_warning_and_does_not_crash(self, caplog):
        """Event referencing a non-existent tenant logs a warning and returns
        None instead of crashing (which would cause Stripe to redeliver)."""
        import logging

        event = _make_event(
            metadata={"tenant_schema": "nonexistent_schema_xyz_99"}
        )

        handler_was_called = {}

        @with_tenant_schema_from_event
        def _handler(sender, **kwargs):
            handler_was_called["yes"] = True

        with caplog.at_level(logging.WARNING, logger="order.signals._tenant"):
            result = _handler(sender=None, event=event)

        assert result is None
        assert not handler_was_called
        assert any(
            "unknown" in r.message or "nonexistent" in r.message
            for r in caplog.records
        )

    @pytest.mark.django_db
    def test_handler_receives_correct_event_kwarg(self):
        """The event kwarg is passed through to the inner function."""
        received = {}

        @with_tenant_schema_from_event
        def _handler(sender, **kwargs):
            received["event"] = kwargs.get("event")

        event = _make_event(metadata={})
        _handler(sender=None, event=event)
        assert received["event"] is event
