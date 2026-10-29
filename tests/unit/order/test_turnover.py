"""Monthly turnover: net of VAT, no shipping, less what went back.

Every order below pins what the figure reads (``OrderFactory`` randomises
status, payment status, pay way and shipping): the lines with their price
and VAT rate, shipping, the payment fee, the payment status and the moment
it was placed. Amounts are chosen so the net is a round number.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from djmoney.money import Money

from giftcard.enum import GiftCardTransactionKind
from giftcard.factories import GiftCardFactory
from giftcard.models import GiftCardTransaction
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.factories.payment_transaction import PaymentTransactionFactory
from order.models import ChargebackDecision, Order, PaymentTransaction
from order.turnover import first_year, turnover_by_month
from product.factories import ProductFactory
from tests.utils.discounts import snapshot_discounts
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

D = Decimal
ONE_LINE = ((D("124.00"), D("24"), 1),)


def _at(year, month, day=15, hour=12, minute=0) -> datetime:
    """A moment in UTC; midday is the same calendar day in Athens."""
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _order(
    lines=ONE_LINE,
    *,
    placed=None,
    payment_status=PaymentStatus.COMPLETED,
    status=OrderStatus.DELIVERED,
    shipping="0",
    fee="0",
    discount=None,
    loyalty=None,
    gift_card=None,
):
    """An order with ``(unit gross, VAT rate, quantity)`` lines."""
    order = OrderFactory(
        num_order_items=0,
        status=status,
        payment_status=payment_status,
        shipping_price=Money(D(shipping), "EUR"),
        payment_method_fee=Money(D(fee), "EUR"),
    )
    order.items.all().delete()
    for gross, rate, quantity in lines:
        order.items.create(
            product=ProductFactory(vat=VatFactory(value=rate)),
            price=Money(gross, "EUR"),
            quantity=quantity,
            vat_rate=rate,
        )
    order.discount_amount = Money(D(discount or 0), "EUR")
    order.loyalty_discount = Money(D(loyalty or 0), "EUR")
    order.gift_card_amount = Money(D(gift_card or 0), "EUR")
    order.save()
    snapshot_discounts(order)
    Order.objects.filter(pk=order.pk).update(created_at=placed or _at(2026, 3))
    return order


def _ledger(order, kind, amount, when, *, charge=None):
    row = PaymentTransactionFactory(
        order=order,
        kind=kind,
        amount=Money(D(amount), "EUR"),
        charge=charge,
    )
    PaymentTransaction.objects.filter(pk=row.pk).update(created_at=when)
    return row


def _refund(order, amount, when):
    charge = _ledger(order, PaymentTransactionKind.CHARGE, "500.00", when)
    return _ledger(
        order, PaymentTransactionKind.REFUND, amount, when, charge=charge
    )


def _month(year, month, rows=None):
    rows = rows or turnover_by_month(year)
    return rows[month - 1]


class TestSales:
    def test_the_net_excludes_vat(self):
        _order([(D("124.00"), D("24"), 1)])

        assert _month(2026, 3).sales == D("100.00")

    def test_every_vat_rate_is_backed_out_at_its_own_rate(self):
        _order(
            [
                (D("124.00"), D("24"), 1),
                (D("56.50"), D("13"), 2),
                (D("10.60"), D("6"), 1),
                (D("5.00"), D("0"), 1),
            ]
        )

        assert _month(2026, 3).sales == D("215.00")

    def test_a_line_is_rounded_to_the_cent_like_the_receipt(self):
        # 1.00 at 24% nets 0.8064, so 0.81; a line of three units is
        # rounded once, as a line: 3.00 / 1.24 = 2.4194, so 2.42.
        _order([(D("1.00"), D("24"), 1)])
        _order([(D("1.00"), D("24"), 3)], placed=_at(2026, 4))

        assert _month(2026, 3).sales == D("0.81")
        assert _month(2026, 4).sales == D("2.42")

    def test_shipping_and_the_payment_fee_are_not_sales(self):
        _order(shipping="12.40", fee="3.10")

        assert _month(2026, 3).sales == D("100.00")

    def test_a_discount_reduces_the_net_on_the_line_it_was_granted_on(self):
        _order([(D("124.00"), D("24"), 1)], discount="24.80")

        assert _month(2026, 3).sales == D("80.00")

    def test_a_loyalty_redemption_reduces_the_net(self):
        _order([(D("124.00"), D("24"), 1)], loyalty="12.40")

        assert _month(2026, 3).sales == D("90.00")

    def test_a_gift_card_is_a_payment_and_does_not_reduce_the_net(self):
        _order([(D("124.00"), D("24"), 1)], gift_card="124.00")

        assert _month(2026, 3).sales == D("100.00")

    def test_the_order_count_is_orders_not_lines(self):
        _order([(D("10.00"), D("0"), 1), (D("20.00"), D("0"), 1)])
        _order([(D("10.00"), D("0"), 1)])

        assert _month(2026, 3).orders == 2


class TestWhichOrdersCount:
    @pytest.mark.parametrize(
        "payment_status",
        [
            PaymentStatus.COMPLETED,
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.REFUNDED,
        ],
    )
    def test_a_collected_payment_is_a_sale(self, payment_status):
        _order(payment_status=payment_status)

        assert _month(2026, 3).sales == D("100.00")
        assert _month(2026, 3).orders == 1

    @pytest.mark.parametrize(
        "payment_status",
        [
            PaymentStatus.PENDING,
            PaymentStatus.PROCESSING,
            PaymentStatus.FAILED,
            PaymentStatus.CANCELED,
        ],
    )
    def test_money_not_collected_is_not_a_sale(self, payment_status):
        _order(payment_status=payment_status, status=OrderStatus.SHIPPED)

        assert _month(2026, 3).sales == D("0")
        assert _month(2026, 3).orders == 0

    def test_a_canceled_order_is_left_out(self):
        _order(
            status=OrderStatus.CANCELED, payment_status=PaymentStatus.CANCELED
        )

        assert _month(2026, 3).sales == D("0")

    def test_a_canceled_order_does_not_reduce_turnover_through_its_credit(
        self,
    ):
        # A canceled unpaid order gave its gift card back; there was no sale
        # to reverse, so that credit is not a return.
        order = _order(
            payment_status=PaymentStatus.CANCELED,
            status=OrderStatus.CANCELED,
            gift_card="50.00",
        )
        self._gift_card_credit(order, "50.00", _at(2026, 3))

        assert _month(2026, 3).returns == D("0")

    @staticmethod
    def _gift_card_credit(order, amount, when):
        row = GiftCardTransaction.objects.create(
            gift_card=GiftCardFactory(),
            kind=GiftCardTransactionKind.REFUND_CREDIT,
            amount=D(amount),
            order=order,
        )
        GiftCardTransaction.objects.filter(pk=row.pk).update(created_at=when)

    def test_another_year_is_not_included(self):
        _order(placed=_at(2025, 3))

        assert sum(m.sales for m in turnover_by_month(2026)) == D("0")
        assert _month(2025, 3, turnover_by_month(2025)).sales == D("100.00")


class TestReturns:
    def test_a_refund_in_a_later_month_belongs_to_that_month(self):
        order = _order(
            placed=_at(2026, 3), payment_status=PaymentStatus.REFUNDED
        )
        _refund(order, "124.00", _at(2026, 5))

        rows = turnover_by_month(2026)
        assert _month(2026, 3, rows).sales == D("100.00")
        assert _month(2026, 3, rows).returns == D("0")
        assert _month(2026, 5, rows).returns == D("100.00")
        assert _month(2026, 5, rows).net == D("-100.00")
        assert _month(2026, 5, rows).sales == D("0")

    def test_a_partial_refund_returns_its_share_of_the_net_goods(self):
        # Receipt gross 134.00 (124.00 goods + 10.00 shipping): a refund of
        # 67.00 is half of it, and half of the 100.00 of net goods.
        order = _order(
            shipping="10.00",
            payment_status=PaymentStatus.PARTIALLY_REFUNDED,
        )
        _refund(order, "67.00", _at(2026, 4))

        assert _month(2026, 4).returns == D("50.00")

    def test_refunding_the_whole_receipt_returns_exactly_the_net_goods(self):
        order = _order(
            [(D("124.00"), D("24"), 1), (D("56.50"), D("13"), 2)],
            shipping="9.90",
            fee="2.00",
            payment_status=PaymentStatus.REFUNDED,
        )
        _refund(order, "124.00", _at(2026, 4))
        _refund(order, "113.00", _at(2026, 4))
        _refund(order, "11.90", _at(2026, 4))

        # 200.00 of net goods, three refunds that add up to the receipt.
        assert _month(2026, 3).sales == D("200.00")
        assert _month(2026, 4).returns == D("200.00")

    def test_a_refund_reversal_gives_the_return_back(self):
        order = _order(payment_status=PaymentStatus.COMPLETED)
        charge = _ledger(
            order, PaymentTransactionKind.CHARGE, "124.00", _at(2026, 3)
        )
        refund = _ledger(
            order,
            PaymentTransactionKind.REFUND,
            "124.00",
            _at(2026, 4),
            charge=charge,
        )
        _ledger(
            order,
            PaymentTransactionKind.REFUND_REVERSAL,
            "124.00",
            _at(2026, 5),
            charge=charge,
        )

        assert refund.pk
        assert _month(2026, 4).returns == D("100.00")
        assert _month(2026, 5).returns == D("-100.00")

    def test_value_returned_onto_a_gift_card_is_a_return(self):
        order = _order(
            gift_card="124.00", payment_status=PaymentStatus.REFUNDED
        )
        TestWhichOrdersCount._gift_card_credit(order, "124.00", _at(2026, 6))

        assert _month(2026, 6).returns == D("100.00")

    def test_a_chargeback_is_a_return_only_when_the_goods_came_back(self):
        returned = _order(payment_status=PaymentStatus.COMPLETED)
        kept = _order(payment_status=PaymentStatus.COMPLETED)
        undecided = _order(payment_status=PaymentStatus.COMPLETED)
        for order, decision in (
            (returned, True),
            (kept, False),
            (undecided, None),
        ):
            charge = _ledger(
                order, PaymentTransactionKind.CHARGE, "124.00", _at(2026, 3)
            )
            row = _ledger(
                order,
                PaymentTransactionKind.CHARGEBACK,
                "124.00",
                _at(2026, 4),
                charge=charge,
            )
            if decision is not None:
                ChargebackDecision.objects.create(
                    chargeback=row,
                    goods_returned=decision,
                    decided_at=_at(2026, 7),
                )

        # In the month of the decision, not of the dispute.
        assert _month(2026, 4).returns == D("0")
        assert _month(2026, 7).returns == D("100.00")

    def test_a_refund_of_an_order_that_was_never_a_sale_is_not_a_return(self):
        order = _order(
            payment_status=PaymentStatus.CANCELED, status=OrderStatus.CANCELED
        )
        _refund(order, "124.00", _at(2026, 4))

        assert _month(2026, 4).returns == D("0")

    def test_an_order_without_a_receipt_value_does_not_divide_by_zero(self):
        order = _order([(D("10.00"), D("0"), 1)], discount="10.00")
        _refund(order, "1.00", _at(2026, 4))

        assert _month(2026, 4).returns == D("0")


class TestTheMonthIsTheStoresMonth:
    """Europe/Athens: UTC+3 in summer, UTC+2 in winter."""

    def test_midnight_athens_starts_the_next_month(self):
        # 2026-03-31 21:00 UTC is 2026-04-01 00:00 EEST.
        _order(placed=_at(2026, 3, 31, 21, 0))
        # One minute earlier is still March 31st in Athens.
        _order(placed=_at(2026, 3, 31, 20, 59))

        assert _month(2026, 3).orders == 1
        assert _month(2026, 4).orders == 1

    def test_the_new_year_starts_at_midnight_in_athens(self):
        # 2025-12-31 22:00 UTC is 2026-01-01 00:00 EET.
        _order(placed=_at(2025, 12, 31, 22, 0))
        _order(placed=_at(2025, 12, 31, 21, 59))

        assert _month(2026, 1).orders == 1
        assert _month(2025, 12, turnover_by_month(2025)).orders == 1

    def test_a_refund_follows_the_same_boundary(self):
        order = _order(payment_status=PaymentStatus.REFUNDED)
        _refund(order, "124.00", _at(2026, 4, 30, 21, 0))

        assert _month(2026, 4).returns == D("0")
        assert _month(2026, 5).returns == D("100.00")


class TestTheYear:
    def test_an_empty_year_is_twelve_zero_months(self):
        rows = turnover_by_month(2031)

        assert [m.month.month for m in rows] == list(range(1, 13))
        assert all(
            m.sales == m.returns == D("0") and m.orders == 0 for m in rows
        )

    def test_the_first_year_is_the_oldest_sale(self):
        assert first_year() is None

        _order(placed=_at(2024, 6))
        _order(placed=_at(2026, 2))
        _order(placed=_at(2022, 1), payment_status=PaymentStatus.PENDING)

        assert first_year() == 2024

    def test_the_query_count_does_not_grow_with_the_orders(
        self, django_assert_num_queries
    ):
        _order()
        with django_assert_num_queries(5):
            turnover_by_month(2026)

        for _ in range(6):
            order = _order(
                shipping="5.00", payment_status=PaymentStatus.REFUNDED
            )
            _refund(order, "20.00", _at(2026, 4))
            TestWhichOrdersCount._gift_card_credit(order, "5.00", _at(2026, 5))

        with django_assert_num_queries(5):
            turnover_by_month(2026)
