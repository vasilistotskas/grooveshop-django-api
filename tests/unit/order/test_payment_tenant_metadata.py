"""Tests that StripePaymentProvider tags every Stripe call with the
current tenant's schema so revenue can be attributed per tenant when
multiple tenants share one platform Stripe account.

``Tenant.stripe_connect_account_id`` is also echoed into the metadata
when present — the hook is the groundwork for future Stripe Connect
support where charges are routed to the tenant's own account.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import pytest
from django.conf import settings
from django.db import connection
from djmoney.money import Money

from order.payment import StripePaymentProvider


@pytest.fixture
def bind_tenant(monkeypatch):
    """Mimic ``django_tenants.TenantMainMiddleware.set_tenant``.

    The real middleware sets both ``connection.tenant`` and
    ``connection.schema_name``; the payment code reads the latter.
    Binding only the tenant leaves ``schema_name`` on its prior value
    ("public" in the test DB), which would mask real bugs.
    """
    original_schema = getattr(connection, "schema_name", "public")

    def _bind(t):
        if t is None:
            monkeypatch.setattr(connection, "tenant", None, raising=False)
            monkeypatch.setattr(
                connection, "schema_name", "public", raising=False
            )
            return
        monkeypatch.setattr(connection, "tenant", t, raising=False)
        monkeypatch.setattr(
            connection,
            "schema_name",
            getattr(t, "schema_name", "public"),
            raising=False,
        )

    yield _bind
    monkeypatch.setattr(
        connection, "schema_name", original_schema, raising=False
    )


@pytest.fixture
def amount():
    return Money(amount=Decimal("100.00"), currency=settings.DEFAULT_CURRENCY)


def _fake_tenant(schema_name: str = "webside", connect_id: str | None = None):
    return SimpleNamespace(
        schema_name=schema_name,
        stripe_connect_account_id=connect_id or "",
    )


def _stub_stripe_payment_intent():
    pi = mock.Mock()
    pi.id = "pi_test"
    pi.status = "succeeded"
    pi.client_secret = "secret"
    pi.next_action = None
    return pi


class TestProcessPaymentMetadata:
    @mock.patch("order.payment.PaymentIntent.sync_from_stripe_data")
    @mock.patch("order.payment.stripe.PaymentIntent.create")
    def test_includes_tenant_schema_in_metadata(
        self, mock_create, _mock_sync, amount, bind_tenant
    ):
        bind_tenant(_fake_tenant(schema_name="webside"))
        mock_create.return_value = _stub_stripe_payment_intent()

        provider = StripePaymentProvider()
        provider.process_payment(amount, order_id="order-1")

        mock_create.assert_called_once()
        metadata = mock_create.call_args.kwargs["metadata"]
        assert metadata["tenant_schema"] == "webside"
        assert metadata["order_id"] == "order-1"

    @mock.patch("order.payment.PaymentIntent.sync_from_stripe_data")
    @mock.patch("order.payment.stripe.PaymentIntent.create")
    def test_includes_stripe_connect_id_when_present(
        self, mock_create, _mock_sync, amount, bind_tenant
    ):
        bind_tenant(_fake_tenant(schema_name="tenant-b", connect_id="acct_123"))
        mock_create.return_value = _stub_stripe_payment_intent()

        provider = StripePaymentProvider()
        provider.process_payment(amount, order_id="order-2")

        metadata = mock_create.call_args.kwargs["metadata"]
        assert metadata["tenant_stripe_account"] == "acct_123"

    @mock.patch("order.payment.PaymentIntent.sync_from_stripe_data")
    @mock.patch("order.payment.stripe.PaymentIntent.create")
    def test_omits_stripe_connect_id_when_blank(
        self, mock_create, _mock_sync, amount, bind_tenant
    ):
        bind_tenant(_fake_tenant(schema_name="webside", connect_id=""))
        mock_create.return_value = _stub_stripe_payment_intent()

        provider = StripePaymentProvider()
        provider.process_payment(amount, order_id="order-3")

        metadata = mock_create.call_args.kwargs["metadata"]
        assert "tenant_stripe_account" not in metadata

    @mock.patch("order.payment.PaymentIntent.sync_from_stripe_data")
    @mock.patch("order.payment.stripe.PaymentIntent.create")
    def test_defaults_to_public_when_no_tenant(
        self, mock_create, _mock_sync, amount, bind_tenant
    ):
        # When Celery / tests run outside a tenant request the schema
        # falls back to "public" so finance still has a value to key on.
        bind_tenant(None)
        mock_create.return_value = _stub_stripe_payment_intent()

        provider = StripePaymentProvider()
        provider.process_payment(amount, order_id="order-4")

        metadata = mock_create.call_args.kwargs["metadata"]
        assert metadata["tenant_schema"] == "public"


class TestRefundMetadata:
    @mock.patch("order.payment.Refund.sync_from_stripe_data")
    @mock.patch("order.payment.stripe.Refund.create")
    def test_includes_tenant_schema(self, mock_refund, _mock_sync, bind_tenant):
        bind_tenant(_fake_tenant(schema_name="tenant-c"))
        mock_refund.return_value = mock.Mock(id="re_1", status="succeeded")

        provider = StripePaymentProvider()
        provider.refund_payment("pi_some")

        refund_kwargs = mock_refund.call_args.kwargs
        assert refund_kwargs["metadata"]["tenant_schema"] == "tenant-c"


class TestCheckoutSessionMetadata:
    @mock.patch("order.payment.stripe.checkout.Session.create")
    def test_session_and_payment_intent_both_tagged(
        self, mock_session, amount, bind_tenant
    ):
        bind_tenant(_fake_tenant(schema_name="webside"))
        mock_session.return_value = mock.Mock(
            id="cs_1", url="https://stripe/cs_1"
        )

        provider = StripePaymentProvider()
        provider.create_checkout_session(
            amount=amount,
            order_id="order-5",
            success_url="https://webside.gr/ok",
            cancel_url="https://webside.gr/cancel",
        )

        kwargs = mock_session.call_args.kwargs
        assert kwargs["metadata"]["tenant_schema"] == "webside"
        assert (
            kwargs["payment_intent_data"]["metadata"]["tenant_schema"]
            == "webside"
        )
