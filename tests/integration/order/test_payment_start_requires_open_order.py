"""A payment may only be started on an order that is still waiting for one.

Cancelling an unpaid order releases its stock and settles its payment
to CANCELED (``Order.settle_unpaid_payment``). Opening a hosted checkout
session for it anyway, or charging an agent's delegated card, takes money
for goods that will never ship — ``record_payment_after_cancel`` then
books the charge and alerts ops for a manual refund. Every endpoint that
starts a payment refuses such an order before the provider is ever
called, and one cancelled while the provider call is in flight is not
reopened.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.lifecycle.service import OrderLifecycleService
from order.models.order import Order
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
    OrderLifecycleService.cancel_order(order, refund_payment=False)
    order.refresh_from_db()
    assert order.status == OrderStatus.CANCELED
    return order


@pytest.mark.parametrize(
    "route",
    [
        "order-create-checkout-session",
        "order-confirm-agent-payment",
    ],
)
@override_settings(AGENT_STRIPE_DELEGATED_ENABLED=True)
def test_a_canceled_order_cannot_start_a_payment(canceled_order, route):
    client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
    client.force_authenticate(user=canceled_order.user)

    # The store is fully configured, so the order is the only reason
    # left to refuse; the provider itself must never be reached.
    with (
        patch(
            "pay_way.services.PayWayService.is_provider_configured",
            return_value=True,
        ),
        patch("order.views.order.get_payment_provider") as provider,
    ):
        response = client.post(
            reverse(route, kwargs={"pk": canceled_order.pk}), {}, format="json"
        )

    assert response.status_code == 400, response.data
    assert response.json()["detail"] == REFUSAL
    provider.assert_not_called()
    canceled_order.refresh_from_db()
    assert canceled_order.payment_status == PaymentStatus.CANCELED
    assert canceled_order.payment_id == "pi_before_cancel"


def test_a_cancel_during_the_provider_call_is_not_overwritten():
    """The provider call runs without the row lock. A cancel landing
    meanwhile wins: the session just opened is ended, never recorded and
    its link never handed out."""
    user = UserAccountFactory(num_addresses=0)
    order = OrderFactory(
        user=user,
        num_order_items=0,
        pay_way=PayWayFactory.create_online_payment(),
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        payment_id="",
    )
    provider = Mock(min_hosted_session=timedelta(0))
    provider.recorded_checkout_session.return_value = None
    provider.live_checkout_session.return_value = None
    provider.checkout_session_params.return_value = {}

    def cancel_then_open(**kwargs):
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.CANCELED,
            payment_status=PaymentStatus.CANCELED,
        )
        return True, {
            "session_id": "cs_opened_meanwhile",
            "checkout_url": "https://checkout.example/cs_opened_meanwhile",
        }

    provider.create_checkout_session.side_effect = cancel_then_open
    client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
    client.force_authenticate(user=user)

    with (
        patch(
            "pay_way.services.PayWayService.is_provider_configured",
            return_value=True,
        ),
        patch("order.views.order.get_payment_provider", return_value=provider),
    ):
        response = client.post(
            reverse("order-create-checkout-session", kwargs={"pk": order.pk}),
            {
                "successUrl": "https://shop.example/ok",
                "cancelUrl": "https://shop.example/no",
            },
            format="json",
        )

    assert response.status_code == 409, response.data
    assert response.json()["code"] == "order_changed_during_payment"
    assert "cs_opened_meanwhile" not in str(response.data)
    provider.discard_checkout_session.assert_called_once_with(
        "cs_opened_meanwhile"
    )
    order.refresh_from_db()
    assert order.status == OrderStatus.CANCELED
    assert order.payment_status == PaymentStatus.CANCELED
    assert order.payment_id == ""
    provider.record_checkout_session.assert_not_called()
