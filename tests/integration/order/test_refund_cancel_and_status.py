"""What a cancel refunds, what it says when it cannot, and when an order may
be marked REFUNDED.

* A cancel refunds everything still refundable, so an order a provider's
  dashboard already refunded in part (PARTIALLY_REFUNDED) gets the rest
  back, not nothing.
* A refund refused because a dispute is open waits for the dispute: the
  order's history says so, and so does the admin.
* REFUNDED says the customer was paid back, so it is refused while the
  ledger still holds refundable money.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from djmoney.money import Money

from order.admin import OrderAdmin, OrderItemAdmin, OrderItemInline
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import RefundOutstandingError
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models.history import OrderHistory
from order.models.item import OrderItem
from order.models.order import Order
from order.payment_ledger import PaymentLedger
from order.services import OrderService
from pay_way.factories import PayWayFactory
from tests.utils.stripe_refunds import FakeStripeRefunds
from user.factories import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def _paid_order(captured="100.00", **kwargs):
    kwargs.setdefault("pay_way", PayWayFactory.create_online_payment())
    kwargs.setdefault("status", OrderStatus.PROCESSING)
    kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
    kwargs.setdefault("payment_id", "pi_cancel")
    kwargs.setdefault("amount_due", _eur(captured))
    order = OrderFactory(num_order_items=0, **kwargs)
    charge = PaymentTransactionFactory(
        order=order, amount=_eur(captured), provider_reference="pi_cancel"
    )
    return order, charge


def _notes(order) -> list[str]:
    return [
        row.new_value["note"]
        for row in OrderHistory.objects.filter(order=order, change_type="NOTE")
    ]


class TestACancelRefundsWhatIsStillRefundable:
    def test_an_order_refunded_in_part_gets_the_rest_back(self):
        order, charge = _paid_order()
        PaymentTransactionFactory(
            order=order,
            charge=charge,
            kind=PaymentTransactionKind.REFUND,
            amount=_eur("20.00"),
            provider_reference="re_dashboard",
        )
        Order.objects.filter(pk=order.pk).update(
            payment_status=PaymentStatus.PARTIALLY_REFUNDED
        )
        order.refresh_from_db()

        with FakeStripeRefunds() as stripe_api:
            canceled, refund_info = OrderService.cancel_order(
                order, reason="changed mind", customer_initiated=False
            )

        assert canceled.status == OrderStatus.CANCELED
        assert refund_info["refunded"] is True
        assert stripe_api.amounts == [8000]
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED

    def test_nothing_is_asked_of_the_provider_when_nothing_is_left(self):
        order, charge = _paid_order()
        PaymentTransactionFactory(
            order=order,
            charge=charge,
            kind=PaymentTransactionKind.CHARGEBACK,
            amount=_eur("100.00"),
            provider_reference="dp_lost",
        )

        with FakeStripeRefunds() as stripe_api:
            _canceled, refund_info = OrderService.cancel_order(
                order, customer_initiated=False
            )

        assert refund_info is None or refund_info["refunded"] is False
        assert stripe_api.calls == []


class TestADisputeHoldsTheRefundOfACancel:
    def _disputed(self):
        order, _charge = _paid_order()
        order.metadata = {
            "disputed": True,
            "dispute_id": "dp_open",
            "dispute_status": "needs_response",
        }
        order.save(update_fields=["metadata"])
        return order

    def test_the_history_says_a_dispute_is_why(self):
        order = self._disputed()

        with FakeStripeRefunds() as stripe_api:
            canceled, refund_info = OrderService.cancel_order(
                order, reason="fraud", customer_initiated=False
            )

        assert canceled.status == OrderStatus.CANCELED
        assert refund_info["refunded"] is False
        assert refund_info["dispute_open"] is True
        assert stripe_api.calls == []
        [note] = [n for n in _notes(order) if "not refunded" in n]
        assert "dispute is open" in note
        assert "waits until the dispute closes" in note

    def test_the_admin_says_the_refund_waits_for_the_dispute(self):
        order = self._disputed()
        admin = OrderAdmin(Order, AdminSite())
        request = RequestFactory().post("/admin/order/order/")
        request.user = UserAccountFactory(is_staff=True, is_superuser=True)
        request.session = {}
        request._messages = FallbackStorage(request)

        with FakeStripeRefunds():
            admin.mark_as_canceled(request, Order.objects.filter(pk=order.pk))

        messages = [str(m) for m in get_messages(request)]
        assert any("a dispute is open on its payment" in m for m in messages)
        assert not any("payment was not refunded" in m for m in messages)


class TestMarkingAnOrderRefunded:
    def test_it_is_refused_while_money_can_still_be_refunded(self):
        order, _charge = _paid_order(status=OrderStatus.RETURNED)

        with pytest.raises(RefundOutstandingError) as excinfo:
            OrderService.update_order_status(order, OrderStatus.REFUNDED)

        assert excinfo.value.refundable == Decimal("100.00")
        order.refresh_from_db()
        assert order.status == OrderStatus.RETURNED

    def test_it_is_allowed_once_the_payment_is_refunded(self):
        order, _charge = _paid_order(status=OrderStatus.RETURNED)
        with FakeStripeRefunds():
            OrderService.refund_order(order)

        OrderService.update_order_status(order, OrderStatus.REFUNDED)

        order.refresh_from_db()
        assert order.status == OrderStatus.REFUNDED

    def test_it_is_allowed_when_nothing_was_ever_collected(self):
        """A refused parcel that came back: no money moved, nothing to
        refund, so closing it as refunded is only a status."""
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.RETURNED,
            payment_status=PaymentStatus.CANCELED,
        )

        OrderService.update_order_status(order, OrderStatus.REFUNDED)

        order.refresh_from_db()
        assert order.status == OrderStatus.REFUNDED

    def test_the_admin_action_says_which_payment_to_refund_first(self):
        order, _charge = _paid_order(status=OrderStatus.RETURNED)
        admin = OrderAdmin(Order, AdminSite())
        request = RequestFactory().post("/admin/order/order/")
        request.user = UserAccountFactory(is_staff=True, is_superuser=True)
        request.session = {}
        request._messages = FallbackStorage(request)

        admin.mark_as_refunded(request, Order.objects.filter(pk=order.pk))

        [message] = [str(m) for m in get_messages(request)]
        assert "refund its payment first" in message
        assert "100.00" in message
        order.refresh_from_db()
        assert order.status == OrderStatus.RETURNED


class TestAnItemLineSaysRestockedNotRefunded:
    """``OrderItem.refund`` puts units back on the shelf and moves no money,
    so the admin must not say the customer was refunded."""

    @pytest.fixture
    def item(self):
        order = OrderFactory(num_order_items=1)
        return order.items.get()

    def test_the_item_list_label(self, item):
        admin = OrderItemAdmin(OrderItem, AdminSite())

        item.is_refunded, item.refunded_quantity = True, item.quantity
        assert str(admin.refund_status_display(item)) == (
            "All units back in stock"
        )
        item.is_refunded, item.refunded_quantity = False, 1
        assert "Back in stock (1/" in str(admin.refund_status_display(item))
        assert "efund" not in str(admin.refund_status_display(item))

    def test_the_inline_label(self, item):
        inline = OrderItemInline(Order, AdminSite())

        item.is_refunded, item.refunded_quantity = True, item.quantity
        assert str(inline.refund_status(item)) == "Back in stock"
        item.is_refunded, item.refunded_quantity = False, 1
        assert str(inline.refund_status(item)) == "Partly back in stock"


class TestAPaidOrderTheLedgerNeverRecorded:
    """Settled while the previous release served, say: the order is paid
    through Stripe but its capture is not on the ledger, so ``refundable``
    is 0. ``refund_order`` reads the capture from the provider once, so the
    money is still refundable, and the button and the REFUNDED guard must
    say so without calling the provider."""

    def _unrecorded(self, **kwargs):
        kwargs.setdefault("pay_way", PayWayFactory.create_online_payment())
        kwargs.setdefault("payment_id", "pi_unrecorded")
        return OrderFactory(
            num_order_items=0,
            status=OrderStatus.RETURNED,
            payment_status=PaymentStatus.COMPLETED,
            amount_due=_eur("50.00"),
            **kwargs,
        )

    def test_the_refund_action_is_offered_without_asking_the_provider(self):
        order = self._unrecorded()
        admin = OrderAdmin(Order, AdminSite())
        request = RequestFactory().post("/admin/order/order/")
        request.user = UserAccountFactory(is_staff=True, is_superuser=True)

        with patch("order.payment.get_payment_provider") as provider:
            offered = admin.has_refund_permission(request, object_id=order.pk)

        assert offered is True
        provider.assert_not_called()

    def test_it_cannot_be_marked_refunded_while_the_money_is_held(self):
        order = self._unrecorded()

        with pytest.raises(RefundOutstandingError) as excinfo:
            OrderService.update_order_status(order, OrderStatus.REFUNDED)

        assert excinfo.value.refundable == 0

    def test_the_admin_says_to_refund_it_first(self):
        order = self._unrecorded()
        admin = OrderAdmin(Order, AdminSite())
        request = RequestFactory().post("/admin/order/order/")
        request.user = UserAccountFactory(is_staff=True, is_superuser=True)
        request.session = {}
        request._messages = FallbackStorage(request)

        admin.mark_as_refunded(request, Order.objects.filter(pk=order.pk))

        [message] = [str(m) for m in get_messages(request)]
        assert message == (
            f"Order {order.pk} skipped: refund its payment first."
        )

    def test_an_order_paid_without_a_provider_is_not_open(self):
        """Paid by gift cards and points, or cash: there is no provider to
        read a capture from, so closing it as REFUNDED is only a status."""
        order = self._unrecorded(pay_way=None, payment_id="")

        assert PaymentLedger.refund_open(order) is False
        OrderService.update_order_status(order, OrderStatus.REFUNDED)

        order.refresh_from_db()
        assert order.status == OrderStatus.REFUNDED


class TestMarkingRefundedOverTheApi:
    """The admin reports the refusal; the endpoint answered a logged 500."""

    @staticmethod
    def _post(order):
        from django.urls import reverse
        from rest_framework.test import APIClient

        from tests.utils.staff import stamp_platform_identity

        client = APIClient(HTTP_ACCEPT_LANGUAGE="en")
        client.force_authenticate(
            user=stamp_platform_identity(
                UserAccountFactory(is_staff=True, is_superuser=True)
            )
        )
        return client.post(
            reverse("order-update-status", kwargs={"pk": order.pk}),
            {"status": OrderStatus.REFUNDED},
            format="json",
        )

    def test_it_is_a_coded_409_naming_what_is_left(self):
        order, _charge = _paid_order(status=OrderStatus.RETURNED)

        response = self._post(order)

        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "refund_outstanding"
        assert "100.00" in body["detail"]
        order.refresh_from_db()
        assert order.status == OrderStatus.RETURNED

    def test_it_succeeds_once_the_payment_is_refunded(self):
        order, _charge = _paid_order(status=OrderStatus.RETURNED)
        with FakeStripeRefunds():
            OrderService.refund_order(order)

        assert self._post(order).status_code == 200
