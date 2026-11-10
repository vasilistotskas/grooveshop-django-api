"""A BoxNow ``canceled`` parcel event is about the parcel, not the order.

BoxNow defines the state as "Parcel has been canceled by the sender"
(Webhook-Based Parcel Tracking Guide v1.4.6, p. 2; API Manual v7.2,
§6.5.3, p. 49). The sender is the store, so the event reports a decision
the store already made: a staff member replacing the parcel (a wrong
locker, a re-issue), or the cancel of the order itself, which already ran
``OrderService.cancel_order``. The event therefore never cancels the
order (#600). It marks the shipment canceled, leaves the order, its stock
and its payment alone, and an order still open behind a canceled parcel
needs follow-up: staff re-issue the parcel or cancel the order.

Both ways the event arrives are covered: the webhook
(``apply_webhook_event``) and the tracking poll (``sync_shipment_state``).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.models import OrderItem, PaymentTransaction
from order.models.order import Order
from order.services import OrderService
from order.stock import StockManager
from product.factories import ProductFactory
from shipping.models import ShippingProvider
from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.factories import BoxNowShipmentFactory
from shipping_boxnow.models import BoxNowParcelEvent, BoxNowShipment
from shipping_boxnow.services import BoxNowService

pytestmark = pytest.mark.django_db

STOCK = 10
QUANTITY = 3


def _by_webhook(shipment: BoxNowShipment) -> None:
    now = timezone.now().isoformat()
    BoxNowService.apply_webhook_event(
        {
            "id": str(uuid.uuid4()),
            "time": now,
            "data": {
                "parcelId": shipment.parcel_id,
                "parcelState": "canceled",
                "event": "canceled",
                "time": now,
                "orderNumber": str(shipment.order_id),
            },
        }
    )


def _by_poll(shipment: BoxNowShipment) -> None:
    client = MagicMock()
    client.return_value.get_parcel_info.return_value = {
        "data": [
            {
                "id": shipment.parcel_id,
                "state": "canceled",
                "events": [
                    {
                        "type": "canceled",
                        "createTime": timezone.now().isoformat(),
                    }
                ],
            }
        ]
    }
    with patch("shipping_boxnow.services.BoxNowClient", client):
        BoxNowService.sync_shipment_state(shipment)


DELIVERIES: list[Callable[[BoxNowShipment], None]] = [_by_webhook, _by_poll]


@pytest.fixture(params=DELIVERIES, ids=["webhook", "poll"])
def deliver(request) -> Callable[[BoxNowShipment], None]:
    return request.param


def _paid_order_holding_stock(status: OrderStatus = OrderStatus.PROCESSING):
    """A paid order whose stock really left the shelf, with a NEW parcel."""
    product = ProductFactory(stock=STOCK)
    order = OrderFactory(
        status=status,
        payment_status=PaymentStatus.COMPLETED,
        shipping_provider=ShippingProvider.objects.get(code="boxnow"),
        num_order_items=0,
    )
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
    shipment = BoxNowShipmentFactory(order=order, with_parcel=True)
    return order, product, shipment


def _needs_follow_up(order: Order) -> bool:
    return Order.objects.needing_follow_up().filter(pk=order.pk).exists()


class TestAnOpenOrder:
    def test_keeps_its_status_stock_and_payment(self, deliver):
        order, product, shipment = _paid_order_holding_stock()

        deliver(shipment)

        order.refresh_from_db()
        product.refresh_from_db()
        assert order.status == OrderStatus.PROCESSING
        assert product.stock == STOCK - QUANTITY
        assert order.payment_status == PaymentStatus.COMPLETED
        assert "cancellation" not in (order.metadata or {})
        assert not PaymentTransaction.objects.filter(
            order=order, kind=PaymentTransactionKind.REFUND
        ).exists()

    def test_records_the_parcel_as_canceled(self, deliver):
        _order, _product, shipment = _paid_order_holding_stock()

        deliver(shipment)

        shipment.refresh_from_db()
        assert shipment.parcel_state == BoxNowParcelState.CANCELED
        assert BoxNowParcelEvent.objects.filter(
            shipment=shipment, event_type=BoxNowParcelState.CANCELED
        ).exists()

    def test_needs_follow_up_until_staff_decide(self, deliver):
        # Placed a moment ago: only the canceled parcel flags it, never
        # the days the shopper has waited.
        order, _product, shipment = _paid_order_holding_stock()
        assert not _needs_follow_up(order)

        deliver(shipment)

        assert _needs_follow_up(order)

    def test_a_reissued_parcel_clears_the_flag(self, deliver):
        order, _product, shipment = _paid_order_holding_stock()
        deliver(shipment)

        BoxNowService.reset_shipment_for_reissue(shipment)

        assert not _needs_follow_up(order)

    def test_a_customer_cancel_afterwards_still_goes_through(self, deliver):
        """The race #600 named: BoxNow's event for the parcel the customer
        cancel just canceled landed first, canceled the order without a
        refund, and the customer's own cancel then failed its status
        check. Now the event leaves the order to the customer's cancel."""
        order, product, shipment = _paid_order_holding_stock()
        order.payment_status = PaymentStatus.PENDING
        order.save(update_fields=["payment_status"])
        deliver(shipment)

        with patch("shipping_boxnow.services.BoxNowClient") as client:
            OrderService.cancel_order(
                order, reason="changed my mind", customer_initiated=True
            )

        # The parcel is already canceled: BoxNow is not asked again.
        client.return_value.cancel_parcel.assert_not_called()
        order.refresh_from_db()
        product.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert product.stock == STOCK
        assert not _needs_follow_up(order)


class TestAnOrderAlreadyCanceled:
    def test_the_event_changes_nothing(self, deliver):
        order, product, shipment = _paid_order_holding_stock()
        with patch("shipping_boxnow.services.BoxNowClient"):
            OrderService.cancel_order(
                order,
                reason="staff cancel",
                refund_payment=False,
                customer_initiated=False,
            )
        order.refresh_from_db()
        cancellation = order.metadata["cancellation"]

        deliver(shipment)

        order.refresh_from_db()
        product.refresh_from_db()
        assert order.status == OrderStatus.CANCELED
        assert order.metadata["cancellation"] == cancellation
        assert product.stock == STOCK
        assert not _needs_follow_up(order)


class TestAShippedOrder:
    def test_is_left_alone(self, deliver):
        order, product, shipment = _paid_order_holding_stock(
            status=OrderStatus.SHIPPED
        )

        deliver(shipment)

        order.refresh_from_db()
        product.refresh_from_db()
        assert order.status == OrderStatus.SHIPPED
        assert product.stock == STOCK - QUANTITY
        assert "cancellation" not in (order.metadata or {})
        assert not _needs_follow_up(order)
