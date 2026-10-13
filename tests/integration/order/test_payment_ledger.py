"""The payment ledger: ``PaymentTransaction`` and ``PaymentLedger``.

What a provider moved for an order is recorded once, append-only, keyed on
the provider's own id; captured, refunded and refundable are sums over it
and ``payment_status`` after a refund is derived from them in one place.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from djmoney.money import Money

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models.order import Order
from order.models.payment_transaction import PaymentTransaction
from order.payment_ledger import (
    MANUAL_REFUND_PROVIDER,
    PaymentLedger,
    is_cash_collection,
)

pytestmark = pytest.mark.django_db

CHARGE = PaymentTransactionKind.CHARGE
REFUND = PaymentTransactionKind.REFUND


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def _order(**kwargs):
    kwargs.setdefault("status", OrderStatus.DELIVERED)
    kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
    return OrderFactory(num_order_items=0, **kwargs)


def _charge(order, amount="50.00", reference=None, provider="stripe"):
    extra = {"provider_reference": reference} if reference else {}
    return PaymentTransactionFactory(
        order=order, amount=_eur(amount), provider=provider, **extra
    )


class TestTheRowsAreConstrainedByTheDatabase:
    def test_a_provider_reference_is_recorded_once_per_kind(self):
        order = _order()
        _charge(order, reference="pi_1")

        with pytest.raises(IntegrityError), transaction.atomic():
            _charge(order, reference="pi_1")

    def test_the_same_reference_may_exist_as_another_kind_or_provider(self):
        order = _order()
        charge = _charge(order, reference="ref_1")

        PaymentTransactionFactory(
            order=order,
            charge=charge,
            kind=REFUND,
            amount=_eur("5.00"),
            provider_reference="ref_1",
        )
        _charge(order, reference="ref_1", provider="viva_wallet")

        assert PaymentTransaction.objects.filter(order=order).count() == 3

    @pytest.mark.parametrize("amount", ["0.00", "-1.00"])
    def test_an_amount_is_positive(self, amount):
        with pytest.raises(IntegrityError), transaction.atomic():
            _charge(_order(), amount=amount)

    def test_a_refund_must_name_the_charge_it_reverses(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            PaymentTransactionFactory(
                order=_order(), kind=REFUND, provider_reference="re_orphan"
            )

    def test_a_charge_names_no_charge(self):
        order = _order()
        charge = _charge(order)

        with pytest.raises(IntegrityError), transaction.atomic():
            PaymentTransactionFactory(
                order=order, charge=charge, provider_reference="pi_nested"
            )

    def test_an_order_with_a_ledger_cannot_be_deleted_for_real(self):
        order = _order()
        _charge(order)

        with pytest.raises(ProtectedError), transaction.atomic():
            Order.objects.all_with_deleted().filter(pk=order.pk).hard_delete()


class TestTheSums:
    def test_captured_refunded_and_refundable(self):
        order = _order()
        first = _charge(order, "30.00", "pi_a")
        _charge(order, "20.00", "pi_b")
        PaymentTransactionFactory(
            order=order,
            charge=first,
            kind=REFUND,
            amount=_eur("12.50"),
            provider_reference="re_a",
        )

        assert PaymentLedger.captured(order) == Decimal("50.00")
        assert PaymentLedger.refunded(order) == Decimal("12.50")
        assert PaymentLedger.refundable(order) == Decimal("37.50")
        assert PaymentLedger.refunded_on(first) == Decimal("12.50")

    def test_an_order_with_no_rows_has_nothing(self):
        order = _order()

        assert PaymentLedger.captured(order) == 0
        assert PaymentLedger.refunded(order) == 0
        assert PaymentLedger.refundable(order) == 0

    def test_another_orders_rows_do_not_count(self):
        order = _order()
        _charge(_order(), "99.00")

        assert PaymentLedger.captured(order) == 0

    def test_a_cash_collection_is_received_money_the_merchant_refunds(self):
        order = _order()
        PaymentLedger.record_cod_collection(
            order,
            amount=_eur("40.00"),
            provider="acs_cod",
            reference="VOUCHER-1",
        )

        assert PaymentLedger.captured(order) == Decimal("40.00")
        assert PaymentLedger.refundable(order) == Decimal("40.00")
        [(collection, share)] = PaymentLedger.plan_refund(order, None)
        assert is_cash_collection(collection)
        assert share == _eur("40.00")

    def test_a_cash_refund_is_recorded_against_the_collection(self):
        order = _order()
        PaymentLedger.record_cod_collection(
            order,
            amount=_eur("40.00"),
            provider="acs_cod",
            reference="VOUCHER-1",
        )
        [(collection, _share)] = PaymentLedger.plan_refund(order, None)

        row, created = PaymentLedger.record_cod_refund(
            order,
            collection=collection,
            amount=_eur("15.00"),
            reference="TRX-1",
            description="partial",
        )

        assert created
        assert row.charge == collection
        assert row.provider == MANUAL_REFUND_PROVIDER
        assert PaymentLedger.refundable(order) == Decimal("25.00")
        assert PaymentLedger.has_cod_refund(order, collection, "TRX-1")
        assert not PaymentLedger.has_cod_refund(order, collection, "TRX-2")
        assert (
            PaymentLedger.refund_status(order)
            == PaymentStatus.PARTIALLY_REFUNDED
        )

    def test_a_payment_with_nothing_left_is_not_listed(self):
        order = _order()
        spent = _charge(order, "10.00", "pi_spent")
        _charge(order, "10.00", "pi_open")
        PaymentLedger.record_refund(
            order, charge=spent, amount=_eur("10.00"), reference="re_spent"
        )

        assert [
            b.charge.provider_reference for b in PaymentLedger.balances(order)
        ] == ["pi_open"]


class TestPaymentStatusIsDerivedFromTheSums:
    def test_part_of_the_payment_back_is_partially_refunded(self):
        order = _order()
        charge = _charge(order, "50.00")

        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("15.00"), reference="re_1"
        )

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED
        assert PaymentLedger.refund_status(order) == (
            PaymentStatus.PARTIALLY_REFUNDED
        )

    def test_refunds_adding_up_to_the_payment_are_refunded(self):
        order = _order()
        charge = _charge(order, "50.00")

        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("15.00"), reference="re_1"
        )
        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("35.00"), reference="re_2"
        )

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED

    def test_an_order_paid_twice_is_refunded_only_when_both_are_back(self):
        order = _order()
        first = _charge(order, "50.00", "pi_first")
        second = _charge(order, "30.00", "pi_second")

        PaymentLedger.record_refund(
            order, charge=first, amount=_eur("50.00"), reference="re_1"
        )
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED

        PaymentLedger.record_refund(
            order, charge=second, amount=_eur("30.00"), reference="re_2"
        )
        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED

    def test_a_refund_beyond_the_payment_still_reads_refunded(self):
        """The provider's own record wins: the ledger shows the excess
        instead of refusing to know about it."""
        order = _order()
        charge = _charge(order, "50.00")

        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("60.00"), reference="re_over"
        )

        order.refresh_from_db()
        assert order.payment_status == PaymentStatus.REFUNDED

    def test_a_repeated_refund_changes_nothing(self):
        order = _order()
        charge = _charge(order, "50.00")
        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("15.00"), reference="re_1"
        )

        row, created = PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("15.00"), reference="re_1"
        )

        assert created is False
        assert row.provider_reference == "re_1"
        assert PaymentLedger.refunded(order) == Decimal("15.00")

    def test_the_reason_is_kept_with_the_refund(self):
        order = _order()
        charge = _charge(order, "50.00")

        row, _created = PaymentLedger.record_refund(
            order,
            charge=charge,
            amount=_eur("5.00"),
            reference="re_1",
            description="Damaged in transit",
        )

        assert row.description == "Damaged in transit"
        assert row.charge == charge
        assert row.provider == charge.provider


class TestRecording:
    def test_a_charge_is_recorded_once(self):
        order = _order()

        first, created_first = PaymentLedger.record_charge(
            order, amount=_eur("50.00"), provider="stripe", reference="pi_1"
        )
        second, created_second = PaymentLedger.record_charge(
            order, amount=_eur("50.00"), provider="stripe", reference="pi_1"
        )

        assert (created_first, created_second) == (True, False)
        assert first.pk == second.pk

    def test_a_payment_that_belongs_to_another_order_is_not_taken_over(
        self, caplog
    ):
        mine, theirs = _order(), _order()
        PaymentLedger.record_charge(
            theirs, amount=_eur("50.00"), provider="stripe", reference="pi_x"
        )

        with caplog.at_level(logging.ERROR, logger="order.payment_ledger"):
            row, created = PaymentLedger.record_charge(
                mine, amount=_eur("50.00"), provider="stripe", reference="pi_x"
            )

        assert created is False
        assert row.order_id == theirs.pk
        assert PaymentLedger.captured(mine) == 0
        assert "already recorded for order" in caplog.text

    def test_a_cash_collection_is_recorded_once_and_a_zero_one_not_at_all(self):
        order = _order()

        for _ in range(2):
            PaymentLedger.record_cod_collection(
                order,
                amount=_eur("40.00"),
                provider="acs_cod",
                reference="V1",
            )
        PaymentLedger.record_cod_collection(
            order, amount=_eur("0.00"), provider="acs_cod", reference="V2"
        )

        [collection] = order.payment_transactions.all()
        assert collection.kind == PaymentTransactionKind.COD_COLLECTION
        assert collection.provider_reference == "V1"

    def test_the_refund_number_counts_the_refunds_of_the_order(self):
        order = _order()
        charge = _charge(order)
        assert PaymentLedger.next_refund_number(order) == 1

        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("5.00"), reference="re_1"
        )
        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("5.00"), reference="re_2"
        )

        assert PaymentLedger.next_refund_number(order) == 3
        assert PaymentLedger.next_refund_number(_order()) == 1


class TestPlanningARefund:
    def test_everything_left_when_no_amount_is_given(self):
        order = _order()
        _charge(order, "30.00", "pi_a")
        _charge(order, "20.00", "pi_b")

        plan = PaymentLedger.plan_refund(order, None)

        assert [(c.provider_reference, m.amount) for c, m in plan] == [
            ("pi_a", Decimal("30.00")),
            ("pi_b", Decimal("20.00")),
        ]

    def test_a_refund_within_one_payment_goes_against_that_payment(self):
        order = _order()
        _charge(order, "30.00", "pi_a")
        _charge(order, "20.00", "pi_b")

        plan = PaymentLedger.plan_refund(order, _eur("12.00"))

        assert [(c.provider_reference, m.amount) for c, m in plan] == [
            ("pi_a", Decimal("12.00"))
        ]

    def test_a_larger_refund_is_split_across_payments_oldest_first(self):
        order = _order()
        _charge(order, "30.00", "pi_a")
        _charge(order, "20.00", "pi_b")

        plan = PaymentLedger.plan_refund(order, _eur("40.00"))

        assert [(c.provider_reference, m.amount) for c, m in plan] == [
            ("pi_a", Decimal("30.00")),
            ("pi_b", Decimal("10.00")),
        ]

    def test_what_is_already_back_is_not_planned_again(self):
        order = _order()
        first = _charge(order, "30.00", "pi_a")
        _charge(order, "20.00", "pi_b")
        PaymentLedger.record_refund(
            order, charge=first, amount=_eur("25.00"), reference="re_1"
        )

        plan = PaymentLedger.plan_refund(order, _eur("10.00"))

        assert [(c.provider_reference, m.amount) for c, m in plan] == [
            ("pi_a", Decimal("5.00")),
            ("pi_b", Decimal("5.00")),
        ]
