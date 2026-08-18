"""Tests for agent-delegated Stripe payments (SharedPaymentToken).

Two layers:

- ``StripePaymentProvider.confirm_delegated_payment``: charges an
  agent-granted ``spt_…`` token via a server-side confirmed
  PaymentIntent (``payment_method_data[shared_payment_granted_token]``).
- ``POST /order/{id}/confirm_agent_payment?uuid=``: the flag-gated
  endpoint the agent gateway calls for ACP/UCP tokenized completion.
  ``AGENT_STRIPE_DELEGATED_ENABLED`` is off by default (pending Stripe
  Agentic Commerce enrollment) and must yield a clean 400.
"""

from __future__ import annotations

from decimal import Decimal
from unittest import mock

import stripe
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APITestCase

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.payment import StripePaymentProvider
from pay_way.factories import PayWayFactory

SPT = "spt_test_123"


class ConfirmDelegatedPaymentProviderTestCase(TestCase):
    def setUp(self):
        # These tests run outside any tenant context (public schema),
        # where stripe_credentials() has no fallback at all — provide a
        # stand-in tenant key so StripePaymentProvider() constructs.
        patcher = mock.patch(
            "tenant.credentials.stripe_credentials",
            return_value={
                "secret_key": "sk_test_dummy_tenant_key",
                "publishable_key": "pk_test_dummy_tenant_key",
                "live_mode": False,
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @mock.patch("order.payment.stripe.PaymentIntent.create")
    def test_charges_token_with_confirmed_payment_intent(
        self, mock_create
    ) -> None:
        mock_pi = mock.Mock()
        mock_pi.id = "pi_agent_123"
        mock_pi.status = "succeeded"
        mock_create.return_value = mock_pi

        with mock.patch("order.payment.PaymentIntent.sync_from_stripe_data"):
            provider = StripePaymentProvider()
            success, data = provider.confirm_delegated_payment(
                amount=Money(
                    amount=Decimal("47.50"),
                    currency=settings.DEFAULT_CURRENCY,
                ),
                order_id="42",
                order_uuid="0e35c7e5-8a5d-4f6e-9be1-2be929e14a41",
                token=SPT,
            )

        self.assertTrue(success)
        self.assertEqual(data["payment_id"], "pi_agent_123")
        self.assertEqual(data["status"], PaymentStatus.COMPLETED)

        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs["amount"], 4750)
        self.assertTrue(kwargs["confirm"])
        self.assertEqual(
            kwargs["payment_method_data"],
            {"shared_payment_granted_token": SPT},
        )
        self.assertEqual(
            kwargs["idempotency_key"],
            "agent_spt_0e35c7e5-8a5d-4f6e-9be1-2be929e14a41",
        )

    @mock.patch("order.payment.stripe.PaymentIntent.create")
    def test_stripe_error_returns_failure(self, mock_create) -> None:
        mock_create.side_effect = stripe.StripeError("token expired")
        provider = StripePaymentProvider()
        success, data = provider.confirm_delegated_payment(
            amount=Money(
                amount=Decimal("10.00"), currency=settings.DEFAULT_CURRENCY
            ),
            order_id="42",
            order_uuid="0e35c7e5-8a5d-4f6e-9be1-2be929e14a41",
            token=SPT,
        )
        self.assertFalse(success)
        self.assertIn("error", data)


class ConfirmAgentPaymentEndpointTestCase(APITestCase):
    def setUp(self):
        # These tests run outside any tenant context (public schema),
        # where stripe_credentials() has no fallback at all —
        # PayWayService.is_provider_configured("stripe") needs a
        # stand-in tenant key so the endpoint doesn't 400 before
        # reaching the behaviour under test.
        patcher = mock.patch(
            "tenant.credentials.stripe_credentials",
            return_value={
                "secret_key": "sk_test_dummy_tenant_key",
                "publishable_key": "pk_test_dummy_tenant_key",
                "live_mode": False,
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _guest_stripe_order(self) -> Order:
        pay_way = PayWayFactory.create_online_payment(provider_code="stripe")
        order = OrderFactory(
            user=None,
            pay_way=pay_way,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            paid_amount=Money(
                amount=Decimal("0.00"), currency=settings.DEFAULT_CURRENCY
            ),
        )
        return Order.objects.get(pk=order.pk)

    def _url(self, order: Order) -> str:
        return (
            reverse("order-confirm-agent-payment", kwargs={"pk": order.pk})
            + f"?uuid={order.uuid}"
        )

    def test_disabled_flag_returns_clean_400(self) -> None:
        # AGENT_STRIPE_DELEGATED_ENABLED defaults to False.
        order = self._guest_stripe_order()
        response = self.client.post(
            self._url(order), {"sharedPaymentToken": SPT}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not enabled", response.data["detail"])

    @override_settings(AGENT_STRIPE_DELEGATED_ENABLED=True)
    @mock.patch("order.views.order.get_payment_provider")
    def test_successful_charge_marks_order_paid(self, mock_get) -> None:
        order = self._guest_stripe_order()
        mock_get.return_value.confirm_delegated_payment.return_value = (
            True,
            {
                "payment_id": "pi_agent_123",
                "status": PaymentStatus.COMPLETED,
                "amount": "47.50",
                "currency": str(settings.DEFAULT_CURRENCY),
                "provider": "stripe",
            },
        )
        response = self.client.post(
            self._url(order), {"sharedPaymentToken": SPT}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # response.data holds pre-render (snake_case) keys; the camelCase
        # conversion happens in the renderer.
        self.assertEqual(response.data["payment_id"], "pi_agent_123")

        row = (
            Order.objects.filter(pk=order.pk)
            .values("payment_status", "payment_id", "payment_method")
            .first()
        )
        self.assertEqual(row["payment_status"], PaymentStatus.COMPLETED)
        self.assertEqual(row["payment_id"], "pi_agent_123")
        self.assertEqual(row["payment_method"], "stripe_agent_spt")

        token_kwarg = (
            mock_get.return_value.confirm_delegated_payment.call_args.kwargs
        )
        self.assertEqual(token_kwarg["token"], SPT)
        self.assertEqual(token_kwarg["order_uuid"], str(order.uuid))

    @override_settings(AGENT_STRIPE_DELEGATED_ENABLED=True)
    def test_non_stripe_pay_way_rejected(self) -> None:
        pay_way = PayWayFactory.create_offline_payment(
            provider_code="cash_on_delivery", requires_confirmation=False
        )
        order = OrderFactory(
            user=None,
            pay_way=pay_way,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        response = self.client.post(
            self._url(order), {"sharedPaymentToken": SPT}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Message is localized (real i18n returns Greek) — the provider
        # name survives translation.
        self.assertIn("Stripe", response.data["detail"])

    @override_settings(AGENT_STRIPE_DELEGATED_ENABLED=True)
    def test_already_paid_rejected(self) -> None:
        order = self._guest_stripe_order()
        order.mark_as_paid(payment_id="pi_prior")
        response = self.client.post(
            self._url(order), {"sharedPaymentToken": SPT}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Localized message — assert the rejection shape, and that the
        # prior payment id was not overwritten.
        self.assertIn("detail", response.data)
        row = Order.objects.filter(pk=order.pk).values("payment_id").first()
        self.assertEqual(row["payment_id"], "pi_prior")

    @override_settings(AGENT_STRIPE_DELEGATED_ENABLED=True)
    @mock.patch("order.views.order.get_payment_provider")
    def test_provider_failure_leaves_order_unpaid(self, mock_get) -> None:
        order = self._guest_stripe_order()
        mock_get.return_value.confirm_delegated_payment.return_value = (
            False,
            {"error": "token expired", "stripe_error": True},
        )
        response = self.client.post(
            self._url(order), {"sharedPaymentToken": SPT}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        row = Order.objects.filter(pk=order.pk).values("payment_status").first()
        self.assertEqual(row["payment_status"], PaymentStatus.PENDING)

    @override_settings(AGENT_STRIPE_DELEGATED_ENABLED=True)
    def test_guest_without_uuid_is_denied(self) -> None:
        order = self._guest_stripe_order()
        url = reverse("order-confirm-agent-payment", kwargs={"pk": order.pk})
        response = self.client.post(
            url, {"sharedPaymentToken": SPT}, format="json"
        )
        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )

    @override_settings(AGENT_STRIPE_DELEGATED_ENABLED=True)
    def test_missing_token_is_a_validation_error(self) -> None:
        order = self._guest_stripe_order()
        response = self.client.post(self._url(order), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
