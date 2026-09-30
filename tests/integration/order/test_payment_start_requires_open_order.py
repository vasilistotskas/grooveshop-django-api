"""A payment may only be started on an order that is still waiting for one.

Cancelling an unpaid order releases its stock and settles its payment
to CANCELED (``Order.settle_unpaid_payment``). Minting a PaymentIntent
or a hosted checkout session for it anyway takes money for goods that
will never ship — ``record_payment_after_cancel`` then books the charge
and alerts ops for a manual refund. Every endpoint that starts a
payment refuses such an order before the provider is ever called, and
one cancelled while the provider call is in flight is not reopened.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from order.services import OrderService
from pay_way.factories import PayWayFactory
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

REFUSAL = "This order is no longer open for payment."


@pytest.fixture
def canceled_order():
    user = UserAccountFactory(num_addresses=0)
    order = OrderFactory(
        user=user,
        num_order_items=0,
        pay_way=PayWayFactory.create_online_payment(),
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        payment_id="pi_before_cancel",
    )
    OrderService.cancel_order(order, refund_payment=False)
    order.refresh_from_db()
    assert order.status == OrderStatus.CANCELED
    return order


@pytest.mark.parametrize(
    "route",
    [
        "order-create-payment-intent",
        "order-create-checkout-session",
        "order-retry-payment",
        "order-confirm-agent-payment",
    ],
)
@override_settings(AGENT_STRIPE_DELEGATED_ENABLED=True)
def test_a_canceled_order_cannot_start_a_payment(canceled_order, route):
    client = APIClient()
    client.force_authenticate(user=canceled_order.user)

    # The store is fully configured, so the order is the only reason
    # left to refuse; the provider itself must never be reached.
    with (
        patch(
            "pay_way.services.PayWayService.is_provider_configured",
            return_value=True,
        ),
        patch(
            "pay_way.services.PayWayService.get_provider_for_pay_way"
        ) as provider,
    ):
        response = client.post(
            reverse(route, kwargs={"pk": canceled_order.pk}), {}, format="json"
        )

    assert response.status_code == 400, response.data
    assert response.data["detail"] == REFUSAL
    provider.assert_not_called()
    canceled_order.refresh_from_db()
    assert canceled_order.payment_status == PaymentStatus.CANCELED
    assert canceled_order.payment_id == "pi_before_cancel"


def test_a_cancel_during_the_provider_call_is_not_overwritten():
    """The provider call runs without the row lock. A cancel landing
    meanwhile wins, and the intent just opened is never handed out."""
    user = UserAccountFactory(num_addresses=0)
    order = OrderFactory(
        user=user,
        num_order_items=0,
        pay_way=PayWayFactory.create_online_payment(),
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        payment_id="",
    )
    provider = Mock()

    def cancel_then_open(**kwargs):
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.CANCELED,
            payment_status=PaymentStatus.CANCELED,
        )
        return True, {
            "payment_id": "pi_opened_meanwhile",
            "status": PaymentStatus.PENDING,
            "client_secret": "pi_opened_meanwhile_secret",
        }

    provider.process_payment.side_effect = cancel_then_open
    client = APIClient()
    client.force_authenticate(user=user)

    with (
        patch(
            "pay_way.services.PayWayService.is_provider_configured",
            return_value=True,
        ),
        patch(
            "pay_way.services.PayWayService.get_provider_for_pay_way",
            return_value=provider,
        ),
    ):
        response = client.post(
            reverse("order-create-payment-intent", kwargs={"pk": order.pk}),
            {},
            format="json",
        )

    assert response.status_code == 409, response.data
    assert "pi_opened_meanwhile_secret" not in str(response.data)
    order.refresh_from_db()
    assert order.status == OrderStatus.CANCELED
    assert order.payment_status == PaymentStatus.CANCELED
    assert order.payment_id == ""
