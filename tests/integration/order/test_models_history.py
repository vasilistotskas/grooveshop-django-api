from unittest import TestCase

import pytest
from django.conf import settings
from djmoney.money import Money

from order.enum.status import OrderStatus
from order.factories.item import OrderItemFactory
from order.factories.order import OrderFactory
from order.models.history import OrderHistory, OrderItemHistory
from product.factories.product import ProductFactory


@pytest.mark.django_db
class OrderHistoryTestCase(TestCase):
    def setUp(self):
        self.order = OrderFactory(num_order_items=0)
        self.user = None
        self.request = None

    def test_log_status_change(self):
        result = OrderHistory.log_status_change(
            order=self.order,
            previous_status=OrderStatus.PENDING,
            new_status=OrderStatus.PROCESSING,
            user=self.user,
            request=self.request,
        )

        self.assertIsInstance(result, OrderHistory)
        self.assertEqual(result.order, self.order)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "STATUS")
        self.assertEqual(result.previous_value, {"status": OrderStatus.PENDING})
        self.assertEqual(result.new_value, {"status": OrderStatus.PROCESSING})
        self.assertIsNone(result.ip_address)
        self.assertEqual(result.user_agent, "")
        self.assertEqual(
            result.description,
            f"Status changed from {OrderStatus.PENDING} to {OrderStatus.PROCESSING}",
        )

    def test_log_payment_update(self):
        previous_value = {
            "amount_due": {"amount": 0, "currency": settings.DEFAULT_CURRENCY}
        }
        new_value = {
            "amount_due": {
                "amount": 100,
                "currency": settings.DEFAULT_CURRENCY,
            }
        }

        result = OrderHistory.log_payment_update(
            order=self.order,
            previous_value=previous_value,
            new_value=new_value,
            user=self.user,
            request=self.request,
        )

        self.assertIsInstance(result, OrderHistory)
        self.assertEqual(result.order, self.order)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "PAYMENT")
        self.assertEqual(result.previous_value, previous_value)
        self.assertEqual(result.new_value, new_value)
        self.assertIsNone(result.ip_address)
        self.assertEqual(result.user_agent, "")
        self.assertEqual(result.description, "Payment information updated")

    def test_log_payment_update_with_money_object(self):
        previous_value = {
            "amount_due": Money("0.00", settings.DEFAULT_CURRENCY)
        }
        new_value = {"amount_due": Money("100.00", settings.DEFAULT_CURRENCY)}

        result = OrderHistory.log_payment_update(
            order=self.order,
            previous_value=previous_value,
            new_value=new_value,
            user=self.user,
        )

        self.assertIsInstance(result, OrderHistory)
        self.assertEqual(result.order, self.order)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "PAYMENT")
        self.assertEqual(
            result.previous_value["amount_due"],
            str(Money("0.00", settings.DEFAULT_CURRENCY)),
        )
        self.assertEqual(
            result.new_value["amount_due"],
            str(Money("100.00", settings.DEFAULT_CURRENCY)),
        )
        self.assertEqual(result.description, "Payment information updated")

    def test_log_shipping_update(self):
        previous_value = {"tracking_number": None, "carrier": None}
        new_value = {"tracking_number": "TRACK123", "carrier": "FedEx"}

        result = OrderHistory.log_shipping_update(
            order=self.order,
            previous_value=previous_value,
            new_value=new_value,
            user=self.user,
            request=self.request,
        )

        self.assertIsInstance(result, OrderHistory)
        self.assertEqual(result.order, self.order)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "SHIPPING")
        self.assertEqual(result.previous_value, previous_value)
        self.assertEqual(result.new_value, new_value)
        self.assertIsNone(result.ip_address)
        self.assertEqual(result.user_agent, "")
        self.assertEqual(result.description, "Shipping information updated")

    def test_log_note(self):
        note = "This is a test note"

        result = OrderHistory.log_note(
            order=self.order, note=note, user=self.user, request=self.request
        )

        self.assertIsInstance(result, OrderHistory)
        self.assertEqual(result.order, self.order)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "NOTE")
        self.assertEqual(result.new_value, {"note": note})
        self.assertIsNone(result.ip_address)
        self.assertEqual(result.user_agent, "")
        self.assertEqual(result.description, "Note added to order")

    def test_log_refund(self):
        refund_data = {
            "amount": {"amount": 50, "currency": settings.DEFAULT_CURRENCY},
            "reason": "Customer request",
            "transaction_id": "refund_123",
        }

        result = OrderHistory.log_refund(
            order=self.order,
            refund_data=refund_data,
            user=self.user,
            request=self.request,
        )

        self.assertIsInstance(result, OrderHistory)
        self.assertEqual(result.order, self.order)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "REFUND")
        self.assertEqual(result.new_value, refund_data)
        self.assertIsNone(result.ip_address)
        self.assertEqual(result.user_agent, "")
        self.assertEqual(
            result.description, f"Refund processed for {refund_data['amount']}"
        )


@pytest.mark.django_db
class OrderItemHistoryTestCase(TestCase):
    def setUp(self):
        self.order_item = OrderItemFactory(
            order=OrderFactory(num_order_items=0),
            product=ProductFactory(num_images=0, num_reviews=0),
            price=Money("50.00", settings.DEFAULT_CURRENCY),
            quantity=2,
        )
        self.user = None

    def test_log_quantity_change(self):
        result = OrderItemHistory.log_quantity_change(
            order_item=self.order_item,
            previous_quantity=1,
            new_quantity=2,
            user=self.user,
            reason="Customer request",
        )

        self.assertIsInstance(result, OrderItemHistory)
        self.assertEqual(result.order_item, self.order_item)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "QUANTITY")
        self.assertEqual(result.previous_value, {"quantity": 1})
        self.assertEqual(result.new_value, {"quantity": 2})
        self.assertEqual(
            result.description,
            "Quantity changed from 1 to 2. Reason: Customer request",
        )

    def test_log_price_update(self):
        previous_price = Money("50.00", settings.DEFAULT_CURRENCY)
        new_price = Money("45.00", settings.DEFAULT_CURRENCY)

        result = OrderItemHistory.log_price_update(
            order_item=self.order_item,
            previous_price=previous_price,
            new_price=new_price,
            user=self.user,
            reason="Price adjustment",
        )

        self.assertIsInstance(result, OrderItemHistory)
        self.assertEqual(result.order_item, self.order_item)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "PRICE")
        self.assertEqual(
            result.previous_value["price"], float(previous_price.amount)
        )
        self.assertEqual(
            result.previous_value["currency"], str(previous_price.currency)
        )
        self.assertEqual(result.new_value["price"], float(new_price.amount))
        self.assertEqual(result.new_value["currency"], str(new_price.currency))
        self.assertIn("Price updated from", result.description)
        self.assertIn("Price adjustment", result.description)

    def test_log_refund(self):
        result = OrderItemHistory.log_refund(
            order_item=self.order_item,
            refund_quantity=1,
            user=self.user,
            reason="Damaged item",
        )

        self.assertIsInstance(result, OrderItemHistory)
        self.assertEqual(result.order_item, self.order_item)
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.change_type, "REFUND")
        self.assertEqual(
            result.new_value,
            {
                "refund_quantity": 1,
                "refund_amount": 50.0,
                "currency": settings.DEFAULT_CURRENCY,
            },
        )
        self.assertIn("Refund processed for 1 items", result.description)
        self.assertIn("Damaged item", result.description)


EDGE_SECRET = "s3cret-edge-token"
VISITOR = "203.0.113.9"
TRAEFIK_PEER = "198.51.100.7"

_RECORDERS = [
    pytest.param(
        lambda order, request: OrderHistory.log_status_change(
            order=order,
            previous_status=OrderStatus.PENDING,
            new_status=OrderStatus.PROCESSING,
            request=request,
        ),
        id="status",
    ),
    pytest.param(
        lambda order, request: OrderHistory.log_note(
            order=order, note="n", request=request
        ),
        id="note",
    ),
]


@pytest.mark.django_db
@pytest.mark.parametrize("record", _RECORDERS)
class TestOrderHistoryRecordsTheVisitorAddress:
    """The history row used to store ``REMOTE_ADDR``: behind Traefik, the
    proxy. ``tests/unit/core/test_client_ip_only_through_the_helper.py``
    keeps every call site (all five recorders) on ``client_ip_or_peer``."""

    def test_a_request_through_the_edge_records_the_visitor(
        self, record, settings, rf
    ):
        settings.ORIGIN_VERIFY_SECRET = EDGE_SECRET
        request = rf.get(
            "/",
            REMOTE_ADDR="10.42.1.0",
            HTTP_X_ORIGIN_VERIFY=EDGE_SECRET,
            HTTP_CF_CONNECTING_IP=VISITOR,
            HTTP_X_FORWARDED_FOR=f"{VISITOR}, {TRAEFIK_PEER}",
        )
        row = record(OrderFactory(num_order_items=0), request)
        assert row.ip_address == VISITOR

    def test_without_the_proof_it_records_the_peer_not_a_chosen_header(
        self, record, settings, rf
    ):
        settings.ORIGIN_VERIFY_SECRET = EDGE_SECRET
        request = rf.get(
            "/",
            REMOTE_ADDR="10.42.1.0",
            HTTP_CF_CONNECTING_IP="1.2.3.4",
            HTTP_X_REAL_IP="1.2.3.4",
            HTTP_X_FORWARDED_FOR=f"1.2.3.4, {TRAEFIK_PEER}",
        )
        row = record(OrderFactory(num_order_items=0), request)
        assert row.ip_address == TRAEFIK_PEER

    def test_a_forwarded_hop_that_is_not_an_address_does_not_break_the_save(
        self, record, settings, rf
    ):
        settings.ORIGIN_VERIFY_SECRET = EDGE_SECRET
        request = rf.get(
            "/", REMOTE_ADDR="10.42.1.0", HTTP_X_FORWARDED_FOR="garbage"
        )
        row = record(OrderFactory(num_order_items=0), request)
        assert row.ip_address == "10.42.1.0"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "hop", ["fe80::1%eth0", "::ffff:203.0.113.9", "2001:db8::1"]
)
def test_an_ipv6_hop_saves_on_the_history_row(hop, rf):
    """``ipaddress.ip_address`` accepts a zone id; the field stores the
    address without it. Neither may make the history save raise."""
    request = rf.get("/", REMOTE_ADDR="10.42.1.0", HTTP_X_FORWARDED_FOR=hop)
    row = OrderHistory.log_note(
        order=OrderFactory(num_order_items=0), note="n", request=request
    )
    row.refresh_from_db()
    assert row.ip_address == hop.split("%")[0]
