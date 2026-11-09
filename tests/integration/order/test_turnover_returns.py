"""Turnover counts only the goods part of a refund as a return.

A refund pays a duplicate payment back first, and that part is no return of
the sale (it owes no credit document and takes no room on the receipt,
``order.credit_allocation``). Turnover reads the fixed
``CreditAllocation.goods_claimed`` of each refund, so it agrees with the
credit documents, and a past month never moves when a refund is later
reversed. These tests run the real ledger paths that write the allocations.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from order.enum.payment_transaction import PaymentTransactionKind
from order.models import PaymentTransaction
from order.payment_ledger import PaymentLedger
from order.turnover import Bucket, figures
from tests.integration.order.test_mydata_lifecycle import _eur, _order
from tests.utils.turnover import snapshot_sale

pytestmark = pytest.mark.django_db

D = Decimal


def _at(month: int, day: int = 15) -> datetime:
    """Noon on *day* of *month*, 2026."""
    return datetime(2026, month, day, 12, tzinfo=UTC)


def _shipped_order(gross="124.00"):
    """A 24% order of *gross*, paid online, that shipped in March."""
    order = _order(lines=(("24", gross),))
    return snapshot_sale(order, _at(3))


def _move(reference: str, kind: str, when: datetime) -> None:
    """Date the ledger row *reference* of *kind* at *when*."""
    PaymentTransaction.objects.filter(
        provider_reference=reference, kind=kind
    ).update(created_at=when)


def _refund(order, charge, amount: str, reference: str, when: datetime):
    """Book a refund of *amount* on *charge*, dated *when*."""
    PaymentLedger.record_refund(
        order, charge=charge, amount=_eur(amount), reference=reference
    )
    _move(reference, PaymentTransactionKind.REFUND, when)


def _reverse(order, charge, amount: str, reference: str, when: datetime):
    """Book the provider's reversal of a refund, dated *when*."""
    PaymentLedger.record_refund_reversal(
        order, charge=charge, amount=_eur(amount), reference=reference
    )
    _move(reference, PaymentTransactionKind.REFUND_REVERSAL, when)


def _months() -> list:
    """The 2026 turnover figures, one per month."""
    return figures(date(2026, 1, 1), date(2026, 12, 31), Bucket.MONTH)


def _duplicate(order, amount):
    """A second payment of *amount* for *order*: a duplicate."""
    charge, _ = PaymentLedger.record_charge(
        order, amount=_eur(amount), provider="stripe", reference="pi_dup"
    )
    return charge


class TestADuplicatePaymentIsNotAReturn:
    """Giving back a duplicate payment does not undo the sale."""

    def test_a_duplicate_given_back_keeps_the_full_turnover(self):
        order = _shipped_order()
        dup = _duplicate(order, "124.00")

        _refund(order, dup, "124.00", "re_dup", _at(4))

        april = _months()[3]
        assert april.returns_net == D("0")
        assert april.returns_gross == D("0")
        assert april.turnover == D("0")
        assert _months()[2].turnover == D("100.00")

    def test_a_refund_larger_than_the_duplicate_counts_its_goods_only(self):
        order = _shipped_order()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        _duplicate(order, "50.00")

        # 50.00 of the 80.00 gives the duplicate back; 30.00 is goods.
        _refund(order, charge, "80.00", "re_80", _at(4))

        april = _months()[3]
        assert april.returns_gross == D("30.00")
        assert april.returns_net == D("24.19")
        assert april.returns_vat == D("5.81")


class TestAPastMonthNeverMoves:
    """A reversal belongs to its own month; a closed month never moves."""

    def test_a_reversal_is_booked_in_its_own_month(self):
        order = _shipped_order()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        _refund(order, charge, "124.00", "re_all", _at(4))
        before = _months()

        _reverse(order, charge, "124.00", "re_all", _at(5))

        after = _months()
        assert after[3] == before[3]
        assert after[3].returns_net == D("100.00")
        assert after[4].returns_net == D("-100.00")
        assert after[2] == before[2]

    @pytest.mark.parametrize(
        ("reversed_", "goods_back"), [("50.00", "0"), ("60.00", "-10.00")]
    )
    def test_a_reversal_takes_the_duplicate_part_before_the_goods(
        self, reversed_, goods_back
    ):
        order = _shipped_order()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        _duplicate(order, "50.00")
        _refund(order, charge, "80.00", "re_80", _at(4))
        april = _months()[3]

        # 50.00 of the refund was duplicate payment and 30.00 goods: the
        # first 50.00 reversed is duplicate, the rest takes goods back.
        _reverse(order, charge, reversed_, "re_80", _at(5))
        rows = _months()

        assert rows[3] == april
        assert rows[4].returns_gross == D(goods_back)
