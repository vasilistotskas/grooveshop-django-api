"""A cancel is ``OrderService.cancel_order``, never a status change.

``POST /order/{id}/update_status`` accepted CANCELED and wrote the label
through ``update_order_status``: the stock stayed held, a paid order kept
the customer's money and no cancellation was recorded. The endpoint now
refuses CANCELED and names the cancel action; the cancel action is the
one that releases stock, refunds and records.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse
from djmoney.money import Money
from rest_framework.test import APIClient

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import InvalidStatusTransitionError
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models import OrderItem, PaymentTransaction
from order.services import OrderService
from order.stock import StockManager
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from tests.utils.staff import stamp_platform_identity
from tests.utils.stripe_refunds import FakeStripeRefunds
from user.factories import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

STOCK = 10
QUANTITY = 3


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def _staff_client() -> APIClient:
    client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
    client.force_authenticate(
        user=stamp_platform_identity(
            UserAccountFactory(is_staff=True, is_superuser=True)
        )
    )
    return client


def _order_holding_stock(**kwargs):
    """An order with one line whose stock really left the shelf."""
    product = ProductFactory(stock=STOCK)
    order = OrderFactory(num_order_items=0, **kwargs)
    OrderItem.objects.create(
        order=order,
        product=product,
        quantity=QUANTITY,
        price=product.price,
        sort_order=1,
    )
    StockManager.decrement_stock(
        product_id=product.id,
        quantity=QUANTITY,
        order_id=order.id,
        reason="test: order created",
    )
    product.refresh_from_db()
    assert product.stock == STOCK - QUANTITY
    return order, product


def _paid_processing_order():
    order, product = _order_holding_stock(
        pay_way=PayWayFactory.create_online_payment(),
        status=OrderStatus.PROCESSING,
        payment_status=PaymentStatus.COMPLETED,
        payment_id="pi_update_status",
        amount_due=_eur("100.00"),
    )
    PaymentTransactionFactory(
        order=order,
        amount=_eur("100.00"),
        provider_reference="pi_update_status",
    )
    return order, product


def _pending_order():
    return _order_holding_stock(
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
    )


def _update_status(order, status):
    return _staff_client().post(
        reverse("order-update-status", kwargs={"pk": order.pk}),
        {"status": status},
        format="json",
    )


def _cancel(order):
    return _staff_client().post(
        reverse("order-cancel", kwargs={"pk": order.pk}),
        {"reason": "staff cancel"},
        format="json",
    )


def _refunds(order) -> list[PaymentTransaction]:
    return list(
        PaymentTransaction.objects.filter(
            order=order, kind=PaymentTransactionKind.REFUND
        )
    )


@pytest.mark.parametrize(
    "make_order",
    [_paid_processing_order, _pending_order],
    ids=["paid-processing", "pending"],
)
class TestUpdateStatusRefusesCanceled:
    def test_the_endpoint_answers_a_coded_400_and_changes_nothing(
        self, make_order
    ):
        order, product = make_order()
        status_before = order.status
        payment_status_before = order.payment_status

        with FakeStripeRefunds() as stripe_api:
            response = _update_status(order, OrderStatus.CANCELED)

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "cancel_through_cancel_action"
        assert body["detail"] == (
            "An order is canceled through its cancel action, which "
            "releases its stock and refunds its payment."
        )
        order.refresh_from_db()
        product.refresh_from_db()
        assert order.status == status_before
        assert order.payment_status == payment_status_before
        assert "cancellation" not in (order.metadata or {})
        assert product.stock == STOCK - QUANTITY
        assert stripe_api.calls == []
        assert _refunds(order) == []

    def test_the_service_refuses_the_transition(self, make_order):
        order, product = make_order()
        status_before = order.status

        with pytest.raises(InvalidStatusTransitionError) as refused:
            OrderService.update_order_status(order, OrderStatus.CANCELED)

        assert OrderStatus.CANCELED not in refused.value.allowed
        order.refresh_from_db()
        product.refresh_from_db()
        assert order.status == status_before
        assert product.stock == STOCK - QUANTITY


class TestTheCancelActionIsTheCancel:
    """What the refusal points staff to does the whole job."""

    def test_a_paid_processing_order_is_restocked_and_refunded(self):
        order, product = _paid_processing_order()

        with FakeStripeRefunds() as stripe_api:
            response = _cancel(order)

        assert response.status_code == 200
        order.refresh_from_db()
        product.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert product.stock == STOCK
        assert stripe_api.amounts == [10000]
        [refund] = _refunds(order)
        assert refund.amount == _eur("100.00")
        assert order.payment_status == PaymentStatus.REFUNDED
        cancellation = order.metadata["cancellation"]
        assert cancellation["reason"] == "staff cancel"
        assert cancellation["previous_status"] == OrderStatus.PROCESSING

    def test_a_pending_order_is_restocked_and_its_payment_settled(self):
        order, product = _pending_order()

        with FakeStripeRefunds() as stripe_api:
            response = _cancel(order)

        assert response.status_code == 200
        order.refresh_from_db()
        product.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert product.stock == STOCK
        assert order.payment_status == PaymentStatus.CANCELED
        assert stripe_api.calls == []
        assert order.metadata["cancellation"]["previous_status"] == (
            OrderStatus.PENDING
        )
