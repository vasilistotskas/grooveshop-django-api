"""Integration tests for Stripe webhook tenant re-entry.

Verifies that ``@with_tenant_schema_from_event`` routes webhook
processing into the schema the event ARRIVED ON, and that
``metadata.tenant_schema`` — which the sending merchant controls — can
never redirect an event into another tenant's data.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from order.signals._tenant import (
    _claimed_schema_from_event,
    _resolve_event_schema,
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
# Unit tests for _claimed_schema_from_event / _resolve_event_schema
# ---------------------------------------------------------------------------


class TestClaimedSchemaFromEvent:
    def test_reads_claim_from_payment_intent_metadata(self):
        event = _make_event(metadata={"tenant_schema": "webside"})
        assert _claimed_schema_from_event(event) == "webside"

    def test_returns_empty_when_no_metadata(self):
        event = _make_event(metadata={})
        assert _claimed_schema_from_event(event) == ""

    def test_returns_empty_on_malformed_event(self):
        event = MagicMock()
        event.id = "evt_bad"
        # data is not a dict-like object
        del event.data
        assert _claimed_schema_from_event(event) == ""

    @pytest.mark.django_db
    def test_resolve_falls_back_to_claim_in_public_schema(self):
        """No host to trust in the public schema, so the claim is used.

        A request that arrived on a tenant's own webhook endpoint never
        resolves to public, so this path cannot cross tenants.
        """
        event = _make_event(metadata={"tenant_schema": "webside"})
        assert _resolve_event_schema(event) == "webside"

    @pytest.mark.django_db
    def test_resolve_returns_public_when_nothing_claimed(self):
        from django_tenants.utils import get_public_schema_name

        event = _make_event(metadata={})
        assert _resolve_event_schema(event) == get_public_schema_name()

    def test_reads_claim_from_nested_payment_intent(self):
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
        assert _claimed_schema_from_event(event) == "tenant_b"

    def test_strips_whitespace_from_schema_name(self):
        event = _make_event(metadata={"tenant_schema": "  webside  "})
        assert _claimed_schema_from_event(event) == "webside"


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


# ---------------------------------------------------------------------------
# Security regression: metadata must never redirect an event across tenants
# ---------------------------------------------------------------------------


class TestMetadataCannotCrossTenants:
    """``metadata.tenant_schema`` is set by whoever created the Stripe
    object. For a merchant with their own Stripe account that is the
    merchant. Selecting the schema from it let one merchant mark another
    merchant's orders paid: create a cheap Checkout Session in your own
    account stamped with a rival's schema and order id, pay it, and the
    event arrives validly signed by YOUR endpoint secret.

    The schema the event ARRIVED on is the one fact the sender cannot
    influence, so it wins whenever it is a real tenant.
    """

    @staticmethod
    def _on_tenant_host(monkeypatch, schema="tenant_b"):
        from django.db import connection

        monkeypatch.setattr(connection, "schema_name", schema, raising=False)

    @pytest.mark.django_db
    def test_mismatched_claim_is_refused(self, monkeypatch, caplog):
        import logging

        self._on_tenant_host(monkeypatch)
        event = _make_event(metadata={"tenant_schema": "webside"})

        with caplog.at_level(logging.ERROR, logger="order.signals._tenant"):
            assert _resolve_event_schema(event) is None

        assert any("claims" in r.message for r in caplog.records)

    @pytest.mark.django_db
    def test_matching_claim_is_allowed(self, monkeypatch):
        self._on_tenant_host(monkeypatch)
        event = _make_event(metadata={"tenant_schema": "tenant_b"})
        assert _resolve_event_schema(event) == "tenant_b"

    @pytest.mark.django_db
    def test_absent_claim_uses_the_host_schema(self, monkeypatch):
        """Dashboard-initiated refunds and disputes carry no metadata."""
        self._on_tenant_host(monkeypatch)
        event = _make_event(metadata={})
        assert _resolve_event_schema(event) == "tenant_b"

    @pytest.mark.django_db
    def test_decorator_skips_the_handler_on_mismatch(self, monkeypatch):
        self._on_tenant_host(monkeypatch)
        handler_ran = {}

        @with_tenant_schema_from_event
        def _handler(sender, **kwargs):
            handler_ran["yes"] = True

        event = _make_event(metadata={"tenant_schema": "webside"})
        assert _handler(sender=None, event=event) is None
        assert not handler_ran
