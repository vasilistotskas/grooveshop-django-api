"""The staff "Refund (amount, reason)" action on an order.

Before it, a return could only be refunded in the PSP dashboard and
relied on a webhook: the admin's "Mark as refunded" moved the order's
status and nothing else. The action goes through
``OrderService.refund_order``, so it is exactly the in-app refund (ledger,
idempotency key, derived status) with a dialog in front of it.

A dialog action is driven with real POST data, not a stub form: Unfold's
``@action(dialog=...)`` wrapper builds its own form from ``request.POST``
and only runs the body once that form validates.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.test import Client, RequestFactory
from django.urls import reverse
from djmoney.money import Money

from order.admin import OrderAdmin, RefundOrderForm
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models.order import Order
from pay_way.factories import PayWayFactory
from tests.utils.stripe_refunds import FakeStripeRefunds
from user.factories import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


@pytest.fixture
def staff_user():
    return UserAccountFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def order_admin():
    return OrderAdmin(Order, AdminSite())


def _post(staff_user, data):
    # ``_form_submitted`` is BaseDialogForm's required hidden field: the
    # rendered dialog posts it, and without it the wrapper treats the
    # request as "open the dialog" and never runs the action.
    request = RequestFactory().post(
        "/admin/order/order/", {"_form_submitted": "true", **data}
    )
    request.user = staff_user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _messages(request) -> list[str]:
    return [str(m) for m in get_messages(request)]


def _delivered_order(captured="100.00", **kwargs):
    kwargs.setdefault("pay_way", PayWayFactory.create_online_payment())
    kwargs.setdefault("status", OrderStatus.DELIVERED)
    kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
    kwargs.setdefault("payment_id", "pi_admin")
    kwargs.setdefault("paid_amount", Money(Decimal(captured), "EUR"))
    order = OrderFactory(num_order_items=0, **kwargs)
    PaymentTransactionFactory(
        order=order,
        amount=Money(Decimal(captured), "EUR"),
        provider_reference="pi_admin",
    )
    return order


def _refunds(order):
    return order.payment_transactions.filter(kind=PaymentTransactionKind.REFUND)


class TestRefundAction:
    def test_it_is_a_detail_action(self, order_admin):
        assert "refund_payment" in order_admin.actions_detail

    def test_a_refund_goes_through_refund_order_and_is_recorded(
        self, order_admin, staff_user
    ):
        order = _delivered_order("100.00")
        request = _post(
            staff_user, {"amount": "30.00", "reason": "One item came back"}
        )

        with FakeStripeRefunds() as stripe_api:
            response = order_admin.refund_payment(request, object_id=order.pk)

        # Unfold dialogs navigate via HX-Redirect, not a 302.
        assert response.headers["HX-Redirect"].endswith(
            f"/order/order/{order.pk}/change/"
        )
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        [refund] = _refunds(order)
        assert refund.amount == Money(Decimal("30.00"), "EUR")
        assert refund.description == "One item came back"
        assert stripe_api.amounts == [3000]
        assert stripe_api.keys == [f"order-{order.pk}-refund-1"]
        assert _messages(request) == [f"Order {order.pk} refunded €30.00."]

    def test_who_refunded_is_in_the_admin_log(self, order_admin, staff_user):
        order = _delivered_order("100.00")
        request = _post(staff_user, {"amount": "30.00", "reason": "Return"})

        with FakeStripeRefunds():
            order_admin.refund_payment(request, object_id=order.pk)

        entry = LogEntry.objects.get(object_id=str(order.pk))
        assert entry.user_id == staff_user.pk
        assert "30.00" in entry.change_message
        assert "Return" in entry.change_message

    def test_the_rest_can_be_refunded_by_a_second_action(
        self, order_admin, staff_user
    ):
        order = _delivered_order("100.00")

        with FakeStripeRefunds() as stripe_api:
            for amount in ("30.00", "70.00"):
                order_admin.refund_payment(
                    _post(staff_user, {"amount": amount, "reason": "Return"}),
                    object_id=order.pk,
                )

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED
        assert stripe_api.amounts == [3000, 7000]
        assert _refunds(order).count() == 2

    def test_more_than_is_left_is_refused_with_the_reason(
        self, order_admin, staff_user
    ):
        order = _delivered_order("100.00")
        request = _post(staff_user, {"amount": "100.01", "reason": "Return"})

        with FakeStripeRefunds() as stripe_api:
            response = order_admin.refund_payment(request, object_id=order.pk)

        assert "HX-Redirect" in response.headers
        assert _messages(request) == [
            (
                "Refund amount (100.01) cannot exceed the refundable amount "
                "(100.00)."
            )
        ]
        assert stripe_api.calls == []
        assert not _refunds(order).exists()

    def test_an_order_no_provider_was_paid_through_is_refused_with_the_reason(
        self, order_admin, staff_user
    ):
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
            payment_id="OFFLINE_1",
            pay_way=None,
        )
        request = _post(staff_user, {"amount": "10.00", "reason": "Return"})

        with FakeStripeRefunds() as stripe_api:
            order_admin.refund_payment(request, object_id=order.pk)

        assert _messages(request) == [
            (
                "This order was not paid through a payment provider, so "
                "there is nothing to refund here."
            )
        ]
        assert stripe_api.calls == []

    def test_a_refund_the_provider_declined_says_where_to_look(
        self, order_admin, staff_user
    ):
        order = _delivered_order("100.00")
        request = _post(staff_user, {"amount": "10.00", "reason": "Return"})

        with FakeStripeRefunds(status="failed"):
            order_admin.refund_payment(request, object_id=order.pk)

        assert _messages(request) == [
            f"Order {order.pk} was not refunded; see the order history."
        ]
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert not _refunds(order).exists()

    @pytest.mark.parametrize(
        "data",
        [
            {"reason": "no amount"},
            {"amount": "10.00"},
            {"amount": "10.00", "reason": ""},
            {"amount": "0.00", "reason": "zero"},
            {"amount": "-5.00", "reason": "negative"},
        ],
        ids=[
            "no-amount",
            "no-reason",
            "blank-reason",
            "zero-amount",
            "negative-amount",
        ],
    )
    def test_an_incomplete_dialog_refunds_nothing(
        self, order_admin, staff_user, data
    ):
        """An empty or invalid dialog must not send money anywhere."""
        order = _delivered_order("100.00")

        with FakeStripeRefunds() as stripe_api:
            order_admin.refund_payment(
                _post(staff_user, data), object_id=order.pk
            )

        assert stripe_api.calls == []
        assert not _refunds(order).exists()


class TestTheDialogOffersTheRemainder:
    def test_the_amount_defaults_to_what_can_still_be_refunded(
        self, staff_user
    ):
        order = _delivered_order("100.00")
        PaymentTransactionFactory(
            order=order,
            charge=order.payment_transactions.get(),
            kind=PaymentTransactionKind.REFUND,
            amount=Money(Decimal("35.00"), "EUR"),
            provider_reference="re_earlier",
        )

        form = RefundOrderForm(_post(staff_user, {}), order.pk)

        assert form.fields["amount"].initial == Decimal("65.00")
        assert "65.00" in str(form.fields["amount"].help_text)

    def test_nothing_is_offered_for_an_order_with_no_recorded_payment(
        self, staff_user
    ):
        order = OrderFactory(num_order_items=0)

        form = RefundOrderForm(_post(staff_user, {}), order.pk)

        assert form.fields["amount"].initial is None


class TestWhereTheActionIsOffered:
    @pytest.mark.parametrize(
        "order_status",
        [
            OrderStatus.DELIVERED,
            OrderStatus.COMPLETED,
            OrderStatus.RETURNED,
        ],
    )
    def test_staff_handling_a_return_can_refund(
        self, order_admin, staff_user, order_status
    ):
        order = _delivered_order(status=order_status)

        assert order_admin.has_refund_permission(
            _post(staff_user, {}), object_id=order.pk
        )

    @pytest.mark.parametrize(
        "order_status",
        [
            OrderStatus.PENDING,
            OrderStatus.PROCESSING,
            OrderStatus.SHIPPED,
            OrderStatus.CANCELED,
            OrderStatus.REFUNDED,
        ],
    )
    def test_any_other_status_is_not_offered_it(
        self, order_admin, staff_user, order_status
    ):
        order = _delivered_order(status=order_status)

        assert not order_admin.has_refund_permission(
            _post(staff_user, {}), object_id=order.pk
        )

    def test_a_forged_request_for_such_an_order_is_refused(
        self, order_admin, staff_user
    ):
        order = _delivered_order(status=OrderStatus.PROCESSING)

        with FakeStripeRefunds() as stripe_api, pytest.raises(PermissionDenied):
            order_admin.refund_payment(
                _post(staff_user, {"amount": "10.00", "reason": "x"}),
                object_id=order.pk,
            )

        assert stripe_api.calls == []


class TestTheOrderPage:
    """What staff see on the order's change page."""

    @pytest.fixture
    def client(self):
        client = Client()
        client.force_login(
            UserAccountFactory(admin=True),
            backend="tenant.auth_backends.PlatformStaffBackend",
        )
        return client

    def _page(self, client, order) -> str:
        response = client.get(
            reverse("admin:order_order_change", args=[order.pk])
        )
        assert response.status_code == 200
        return response.content.decode()

    def test_the_ledger_is_listed_read_only(self, client):
        order = _delivered_order("100.00")
        PaymentTransactionFactory(
            order=order,
            charge=order.payment_transactions.get(),
            kind=PaymentTransactionKind.REFUND,
            amount=Money(Decimal("12.50"), "EUR"),
            provider_reference="re_on_the_page",
            description="Damaged in transit",
        )

        html = self._page(client, order)

        assert "re_on_the_page" in html
        assert "pi_admin" in html
        assert "Damaged in transit" in html
        # Append-only: no way to add, change or delete a row from here.
        assert "payment_transactions-TOTAL_FORMS" in html
        assert 'name="payment_transactions-0-amount"' not in html

    def test_the_refund_action_is_offered_for_a_delivered_order(self, client):
        order = _delivered_order("100.00", status=OrderStatus.DELIVERED)

        assert "refund_payment" in self._page(client, order)

    def test_it_is_not_offered_before_delivery(self, client):
        order = _delivered_order("100.00", status=OrderStatus.PROCESSING)

        assert "refund_payment" not in self._page(client, order)
