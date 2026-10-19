"""A bank-transfer order can be confirmed paid by a store owner.

``payment_status`` and ``payment_id`` became read-only in the order admin
(a form save once marked orders paid with no charge behind them), and
nothing replaced the form for money that is settled off the platform: a
bank-transfer order could not be marked paid at all. The "Confirm payment
received" action is that door. It is narrow so the protection holds: only
a bank-transfer order still waiting for its payment, for exactly what it
costs, with the bank's reference; online providers never come through it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from djmoney.money import Money

from order.admin import OrderAdmin
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentError
from order.factories import OrderFactory
from order.models.history import OrderHistory
from order.models.order import Order
from order.payment_ledger import PaymentLedger
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from user.factories import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def _transfer_order(**kwargs):
    kwargs.setdefault(
        "pay_way",
        PayWayFactory(
            settlement=PaySettlement.OFFLINE_TRANSFER,
            active=True,
            cost=_eur("0"),
            free_threshold=_eur("0"),
        ),
    )
    kwargs.setdefault("status", OrderStatus.PENDING)
    kwargs.setdefault("payment_status", PaymentStatus.PENDING)
    kwargs.setdefault("payment_id", "")
    kwargs.setdefault("amount_due", _eur("80.00"))
    return OrderFactory(num_order_items=0, **kwargs)


@pytest.fixture
def staff_user():
    return UserAccountFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def order_admin():
    return OrderAdmin(Order, AdminSite())


def _post(user, data):
    request = RequestFactory().post(
        "/admin/order/order/", {"_form_submitted": "true", **data}
    )
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _confirm(
    admin, user, order, *, amount="80.00", reference="BANK-1", note=""
):
    request = _post(
        user, {"amount": amount, "reference": reference, "note": note}
    )
    admin.confirm_offline_payment(request, object_id=order.pk)
    return request


def _messages(request) -> list[str]:
    return [str(m) for m in get_messages(request)]


class TestConfirmingATransfer:
    def test_the_order_is_paid_and_moves_on(self, order_admin, staff_user):
        order = _transfer_order()

        request = _confirm(order_admin, staff_user, order, note="IRIS")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert order.status == OrderStatus.PROCESSING
        assert order.payment_method == "bank_transfer"
        assert _messages(request) == [f"Order {order.pk} is marked paid."]

    def test_the_ledger_holds_the_transfer(self, order_admin, staff_user):
        order = _transfer_order()

        _confirm(order_admin, staff_user, order)

        [row] = order.payment_transactions.all()
        assert row.kind == PaymentTransactionKind.OFFLINE_COLLECTION
        assert row.provider == "bank_transfer"
        assert row.provider_reference == "BANK-1"
        assert row.amount == _eur("80.00")
        assert staff_user.email in row.description
        assert PaymentLedger.refundable(order) == Decimal("80.00")

    def test_who_when_and_how_much_are_on_the_order(
        self, order_admin, staff_user
    ):
        order = _transfer_order()

        _confirm(order_admin, staff_user, order, note="IRIS")

        entry = OrderHistory.objects.filter(
            order=order, change_type="PAYMENT"
        ).latest("id")
        assert entry.new_value["confirmed_by"] == staff_user.email
        assert entry.new_value["amount"] == "80.00"
        assert entry.new_value["reference"] == "BANK-1"
        assert entry.created_at is not None
        assert LogEntry.objects.filter(
            user=staff_user, object_id=str(order.pk)
        ).exists()

    def test_an_already_processing_order_is_just_marked_paid(
        self, order_admin, staff_user
    ):
        order = _transfer_order(status=OrderStatus.PROCESSING)

        _confirm(order_admin, staff_user, order)

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.COMPLETED
        assert order.status == OrderStatus.PROCESSING

    def test_it_can_be_refunded_by_hand_afterwards(
        self, order_admin, staff_user
    ):
        order = _transfer_order(status=OrderStatus.DELIVERED)
        _confirm(order_admin, staff_user, order)

        result = OrderService.refund_order(
            order, amount=_eur("30.00"), repayment_reference="IBAN-REFUND-1"
        )

        assert result.complete is True
        assert result.refunded == _eur("30.00")
        assert PaymentLedger.refundable(order) == Decimal("50.00")


class TestWhatItRefuses:
    def test_a_wrong_amount(self, order_admin, staff_user):
        order = _transfer_order()

        request = _confirm(order_admin, staff_user, order, amount="79.00")

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING
        assert not order.payment_transactions.exists()
        assert "confirm exactly that amount" in _messages(request)[0]

    def test_a_missing_reference(self):
        order = _transfer_order()

        with pytest.raises(PaymentError, match="transaction reference"):
            OrderService.confirm_offline_payment(
                order,
                amount=_eur("80.00"),
                reference="  ",
                confirmed_by="staff@example.com",
            )

    def test_the_same_reference_for_a_second_order(
        self, order_admin, staff_user
    ):
        first, second = _transfer_order(), _transfer_order()
        _confirm(order_admin, staff_user, first)

        request = _confirm(order_admin, staff_user, second)

        second.refresh_from_db()
        assert second.payment_status == PaymentStatus.PENDING
        assert "already recorded" in _messages(request)[0]

    def test_an_order_that_is_not_waiting_for_a_payment(
        self, order_admin, staff_user
    ):
        order = _transfer_order()
        _confirm(order_admin, staff_user, order)

        # The action is not offered again, and the service refuses too.
        assert not order_admin.has_confirm_payment_permission(
            _post(staff_user, {}), object_id=order.pk
        )
        with pytest.raises(PaymentError, match="not waiting for a payment"):
            OrderService.confirm_offline_payment(
                order,
                amount=_eur("80.00"),
                reference="BANK-2",
                confirmed_by=staff_user.email,
            )
        assert order.payment_transactions.count() == 1

    def test_a_canceled_order(self, order_admin, staff_user):
        order = _transfer_order()
        OrderService.cancel_order(order, customer_initiated=False)

        assert not order_admin.has_confirm_payment_permission(
            _post(staff_user, {}), object_id=order.pk
        )
        with pytest.raises(PaymentError, match="not waiting for a payment"):
            OrderService.confirm_offline_payment(
                order,
                amount=_eur("80.00"),
                reference="BANK-1",
                confirmed_by=staff_user.email,
            )

    @pytest.mark.parametrize(
        "settlement",
        [
            PaySettlement.ONLINE,
            PaySettlement.COURIER_CASH,
            PaySettlement.CARRIER_TERMINAL,
        ],
    )
    def test_any_other_settlement_the_protection_for_online_holds(
        self, settlement
    ):
        order = _transfer_order(
            pay_way=PayWayFactory(
                settlement=settlement,
                active=True,
                cost=_eur("0"),
                free_threshold=_eur("0"),
            )
        )

        with pytest.raises(PaymentError, match="bank-transfer order"):
            OrderService.confirm_offline_payment(
                order,
                amount=_eur("80.00"),
                reference="BANK-1",
                confirmed_by="staff@example.com",
            )

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PENDING

    def test_the_payment_fields_stay_read_only_in_the_form(self, order_admin):
        assert "payment_status" in order_admin.readonly_fields
        assert "payment_id" in order_admin.readonly_fields


class TestWhereTheActionIsOffered:
    def test_a_waiting_bank_transfer_order(self, order_admin, staff_user):
        order = _transfer_order()

        assert order_admin.has_confirm_payment_permission(
            _post(staff_user, {}), object_id=order.pk
        )
        assert "confirm_offline_payment" in order_admin.actions_detail

    def test_not_a_paid_one_or_an_online_one(self, order_admin, staff_user):
        paid = _transfer_order(payment_status=PaymentStatus.COMPLETED)
        online = _transfer_order(pay_way=PayWayFactory.create_online_payment())

        for order in (paid, online):
            assert not order_admin.has_confirm_payment_permission(
                _post(staff_user, {}), object_id=order.pk
            )

    def test_a_forged_request_for_another_kind_of_order_is_refused(
        self, order_admin, staff_user
    ):
        online = _transfer_order(pay_way=PayWayFactory.create_online_payment())

        with pytest.raises(PermissionDenied):
            order_admin.confirm_offline_payment(
                _post(
                    staff_user,
                    {"amount": "80.00", "reference": "X", "note": ""},
                ),
                object_id=online.pk,
            )

        online.refresh_from_db()
        assert online.payment_status == PaymentStatus.PENDING
