"""Management profit: net revenue, less what the goods cost, the provider fees
and the expenses.

Every order below pins what the figures read (``OrderFactory`` randomises
status, payment status, pay way and shipping): its lines with price, VAT
rate and cost, the moment it shipped, the ledger rows and the expenses.
Amounts are chosen so the net is a round number.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from djmoney.money import Money

from order.enum.expense import ExpenseCategory
from order.enum.payment_transaction import (
    PaymentFeeSource,
    PaymentTransactionKind,
)
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.expense import ExpenseFactory
from order.factories.order import OrderFactory
from order.factories.payment_transaction import PaymentTransactionFactory
from order.models import Order, PaymentTransaction, StockLog
from order.profit import (
    figures,
    figures_by_group,
    figures_with_prior_year,
    total,
)
from order.turnover import Bucket, GroupBy
from product.factories import ProductFactory
from tests.utils.turnover import snapshot_sale
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

D = Decimal
EUR = "EUR"


def _at(year, month, day=15, hour=12) -> datetime:
    """A moment in UTC; midday is the same calendar day in Athens."""
    return datetime(year, month, day, hour, tzinfo=UTC)


def _sale(lines, *, shipped=None, shipping="0", fee="0", product=None):
    """An order that left the store at ``shipped`` (March 15th by default)
    with ``(unit gross, VAT rate, quantity, unit cost or None)`` lines."""
    order = OrderFactory(
        num_order_items=0,
        status=OrderStatus.DELIVERED,
        payment_status=PaymentStatus.COMPLETED,
        shipping_price=Money(D(shipping), EUR),
        payment_method_fee=Money(D(fee), EUR),
    )
    order.items.all().delete()
    for gross, rate, quantity, cost in lines:
        order.items.create(
            product=product or ProductFactory(vat=VatFactory(value=rate)),
            price=Money(D(gross), EUR),
            quantity=quantity,
            vat_rate=D(rate),
            unit_cost=None if cost is None else Money(D(cost), EUR),
        )
    order.save()
    return snapshot_sale(order, shipped or _at(2026, 3))


def _march(year=2026):
    return figures(date(year, 3, 1), date(year, 3, 31), None)[0]


def _by_month(year=2026):
    return figures(date(year, 1, 1), date(year, 12, 31), Bucket.MONTH)


def _payment(order, kind, amount, when, *, fee=None, charge=None, ref=None):
    row = PaymentTransactionFactory(
        order=order,
        kind=kind,
        amount=Money(D(amount), EUR),
        charge=charge,
        **({"provider_reference": ref} if ref else {}),
    )
    PaymentTransaction.objects.filter(pk=row.pk).update(created_at=when)
    if fee is not None:
        PaymentTransaction.objects.filter(pk=row.pk).update(
            fee=Money(D(fee), EUR),
            fee_source=PaymentFeeSource.STRIPE_BALANCE_TRANSACTION,
        )
    return row


def _charge(order, amount, when, fee=None):
    return _payment(order, PaymentTransactionKind.CHARGE, amount, when, fee=fee)


class TestCostOfGoodsSold:
    def test_it_is_the_cost_of_the_units_that_shipped(self):
        _sale([("124.00", "24", 3, "20.00")])

        assert _march().cogs == D("60.00")

    def test_a_cost_belongs_to_the_month_the_order_shipped(self):
        _sale([("124.00", "24", 1, "20.00")], shipped=_at(2026, 4))

        assert _march().cogs == 0
        assert _by_month()[3].cogs == D("20.00")

    def test_an_order_that_has_not_shipped_costs_nothing_yet(self):
        order = _sale([("124.00", "24", 1, "20.00")])
        Order.objects.filter(pk=order.pk).update(shipped_at=None)

        assert _march().cogs == 0

    def test_a_line_with_no_cost_adds_nothing_and_is_not_covered(self):
        _sale([("124.00", "24", 1, "20.00"), ("62.00", "24", 1, None)])

        row = _march()
        assert row.cogs == D("20.00")
        assert row.goods_costed == D("100.00")
        assert row.goods_uncosted == D("50.00")
        assert row.cost_coverage == D("66.7")

    def test_a_cost_of_zero_is_a_recorded_cost(self):
        _sale([("124.00", "24", 1, "0")])

        row = _march()
        assert row.goods_costed == D("100.00")
        assert row.cost_coverage == D("100.0")

    def test_no_goods_means_no_coverage_to_state(self):
        assert _march().cost_coverage is None

    def test_a_free_gift_still_costs_what_it_cost(self):
        _sale([("124.00", "24", 1, "20.00"), ("0.00", "24", 1, "5.00")])

        assert _march().cogs == D("25.00")


class TestRestockedReturns:
    def _restocked(self, quantity, when):
        order = _sale([("124.00", "24", 3, "20.00")])
        line = order.items.get()
        line.refund(quantity=quantity)
        StockLog.objects.filter(order_item=line).update(created_at=when)
        return order

    def test_a_restocked_unit_gives_its_cost_back_in_the_month_it_returned(
        self,
    ):
        self._restocked(1, _at(2026, 4))

        rows = _by_month()
        assert rows[2].cogs == D("60.00")
        assert rows[3].cogs == D("-20.00")

    def test_over_the_whole_range_the_cost_nets_out(self):
        self._restocked(1, _at(2026, 4))

        [row] = figures(date(2026, 1, 1), date(2026, 12, 31), None)
        assert row.cogs == D("40.00")

    def test_a_return_that_was_not_restocked_keeps_its_cost(self):
        order = _sale([("124.00", "24", 3, "20.00")])
        order.items.update(refunded_quantity=1)

        [row] = figures(date(2026, 1, 1), date(2026, 12, 31), None)
        assert row.cogs == D("60.00")

    def test_a_restock_of_a_line_with_no_cost_reverses_nothing(self):
        order = _sale([("124.00", "24", 3, None)])
        order.items.get().refund(quantity=1)

        [row] = figures(date(2026, 1, 1), date(2026, 12, 31), None)
        assert row.cogs == 0


class TestGrossProfit:
    def test_it_is_the_net_revenue_less_the_cost(self):
        _sale([("124.00", "24", 2, "30.00")], shipping="12.40")

        row = _march()
        # 200.00 goods + 10.00 shipping, net
        assert row.revenue == D("210.00")
        assert row.cogs == D("60.00")
        assert row.gross_profit == D("150.00")
        assert row.gross_margin == D("71.4")

    def test_a_month_with_no_revenue_has_no_margin(self):
        assert _march().gross_margin is None


class TestPaymentFees:
    def test_fees_count_in_the_month_of_the_payment(self):
        order = _sale([("124.00", "24", 1, "20.00")])
        _charge(order, "124.00", _at(2026, 3), fee="2.10")
        _charge(order, "10.00", _at(2026, 4), fee="0.50")

        rows = _by_month()
        assert rows[2].payment_fees == D("2.10")
        assert rows[3].payment_fees == D("0.50")

    def test_a_payment_with_no_reported_fee_adds_nothing_and_lowers_coverage(
        self,
    ):
        order = _sale([("124.00", "24", 1, "20.00")])
        _charge(order, "100.00", _at(2026, 3), fee="2.00")
        _charge(order, "300.00", _at(2026, 3))

        row = _march()
        assert row.payment_fees == D("2.00")
        assert row.online_collected == D("400.00")
        assert row.fee_coverage == D("25.0")

    def test_no_online_payment_means_no_coverage_to_state(self):
        assert _march().fee_coverage is None

    def test_cash_collected_by_a_courier_is_not_an_online_payment(self):
        order = _sale([("124.00", "24", 1, "20.00")])
        _payment(
            order,
            PaymentTransactionKind.COD_COLLECTION,
            "124.00",
            _at(2026, 3),
        )

        assert _march().online_collected == 0

    def test_a_refund_that_carries_a_fee_is_a_cost_too(self):
        order = _sale([("124.00", "24", 1, "20.00")])
        charge = _charge(order, "124.00", _at(2026, 3), fee="2.00")
        _payment(
            order,
            PaymentTransactionKind.REFUND,
            "124.00",
            _at(2026, 3),
            charge=charge,
            fee="0.25",
        )

        assert _march().payment_fees == D("2.25")


class TestExpenses:
    def test_a_carrier_invoice_is_a_delivery_cost(self):
        ExpenseFactory(
            date=date(2026, 3, 10),
            category=ExpenseCategory.SHIPPING,
            amount_net=Money(D("80.00"), EUR),
        )

        row = _march()
        assert row.delivery_costs == D("80.00")
        assert row.other_expenses == 0

    def test_every_other_category_is_an_operating_expense(self):
        ExpenseFactory(
            date=date(2026, 3, 10),
            category=ExpenseCategory.MARKETING,
            amount_net=Money(D("50.00"), EUR),
        )
        ExpenseFactory(
            date=date(2026, 3, 20),
            category=ExpenseCategory.RENT,
            amount_net=Money(D("200.00"), EUR),
        )

        row = _march()
        assert row.delivery_costs == 0
        assert row.other_expenses == D("250.00")
        assert row.operating_costs == {
            ExpenseCategory.MARKETING: D("50.00"),
            ExpenseCategory.RENT: D("200.00"),
        }

    def test_the_vat_on_an_expense_is_never_a_cost(self):
        ExpenseFactory(
            date=date(2026, 3, 10),
            amount_net=Money(D("100.00"), EUR),
            vat_amount=Money(D("24.00"), EUR),
        )

        assert _march().delivery_costs == D("100.00")

    def test_an_expense_belongs_to_the_month_of_its_date(self):
        ExpenseFactory(date=date(2026, 3, 31))
        ExpenseFactory(date=date(2026, 4, 1))

        rows = _by_month()
        assert rows[2].delivery_costs == D("100.00")
        assert rows[3].delivery_costs == D("100.00")

    def test_a_weekly_bucket_lands_an_expense_in_its_monday_week(self):
        ExpenseFactory(date=date(2026, 3, 12))  # a Thursday

        rows = figures(date(2026, 3, 1), date(2026, 3, 31), Bucket.WEEK)
        [week] = [row for row in rows if row.delivery_costs]
        assert week.period == date(2026, 3, 9)

    def test_expenses_outside_the_range_are_left_out(self):
        ExpenseFactory(date=date(2026, 2, 28))

        assert _march().delivery_costs == 0


class TestTheWholeWaterfall:
    def test_operating_profit_is_what_is_left_after_every_cost(self):
        order = _sale([("124.00", "24", 2, "30.00")], shipping="12.40")
        _charge(order, "260.80", _at(2026, 3), fee="4.00")
        ExpenseFactory(
            date=date(2026, 3, 5),
            category=ExpenseCategory.SHIPPING,
            amount_net=Money(D("25.00"), EUR),
        )
        ExpenseFactory(
            date=date(2026, 3, 6),
            category=ExpenseCategory.SOFTWARE,
            amount_net=Money(D("15.00"), EUR),
        )

        row = _march()
        assert row.revenue == D("210.00")
        assert row.gross_profit == D("150.00")
        assert row.contribution == D("121.00")  # 150 - 4 fees - 25 carrier
        assert row.operating_profit == D("106.00")  # 121 - 15


class TestTotals:
    def test_percentages_follow_from_the_sums_not_from_the_rows(self):
        _sale([("124.00", "24", 1, "20.00")], shipped=_at(2026, 3))
        _sale([("124.00", "24", 3, None)], shipped=_at(2026, 4))

        everything = total(_by_month())

        assert everything.goods_costed == D("100.00")
        assert everything.goods_uncosted == D("300.00")
        assert everything.cost_coverage == D("25.0")

    def test_the_categories_are_added_up(self):
        ExpenseFactory(
            date=date(2026, 3, 1),
            category=ExpenseCategory.MARKETING,
            amount_net=Money(D("10.00"), EUR),
        )
        ExpenseFactory(
            date=date(2026, 4, 1),
            category=ExpenseCategory.MARKETING,
            amount_net=Money(D("5.00"), EUR),
        )

        assert total(_by_month()).operating_costs == {
            ExpenseCategory.MARKETING: D("15.00")
        }


class TestPriorYear:
    def test_the_same_range_a_year_earlier_is_keyed_by_its_own_period(self):
        _sale([("124.00", "24", 1, "20.00")], shipped=_at(2025, 3))

        rows, prior = figures_with_prior_year(
            date(2026, 1, 1), date(2026, 12, 31), Bucket.MONTH
        )

        assert prior[date(2025, 3, 1)].cogs == D("20.00")
        assert len(rows) == 12


class TestBreakdown:
    def test_a_product_reports_its_goods_cost_and_profit(self):
        product = ProductFactory(vat=VatFactory(value=D("24")))
        _sale([("124.00", "24", 2, "30.00")], product=product)

        [row] = figures_by_group(
            date(2026, 1, 1), date(2026, 12, 31), None, GroupBy.PRODUCT
        )

        assert row.group == product.pk
        assert row.goods == D("200.00")
        assert row.cogs == D("60.00")
        assert row.gross_profit == D("140.00")
        assert row.gross_margin == D("70.0")
        assert row.units == 2

    def test_an_uncosted_product_states_no_profit_and_no_coverage(self):
        product = ProductFactory(vat=VatFactory(value=D("24")))
        _sale([("124.00", "24", 1, None)], product=product)

        [row] = figures_by_group(
            date(2026, 1, 1), date(2026, 12, 31), None, GroupBy.PRODUCT
        )

        assert row.goods_uncosted == D("100.00")
        assert row.cost_coverage == D("0.0")
        assert row.gross_profit == 0
        assert row.gross_margin is None

    @pytest.mark.parametrize("group_by", list(GroupBy))
    def test_every_breakdown_runs(self, group_by):
        _sale([("124.00", "24", 1, "20.00")])

        rows = figures_by_group(
            date(2026, 1, 1), date(2026, 12, 31), Bucket.MONTH, group_by
        )

        assert sum(row.goods for row in rows) == D("100.00")


class TestQueries:
    def test_the_query_count_does_not_grow_with_the_orders(
        self, django_assert_num_queries
    ):
        _sale([("124.00", "24", 1, "20.00")])
        ExpenseFactory(date=date(2026, 3, 1))
        with django_assert_num_queries(8):
            _by_month()

        for _ in range(6):
            order = _sale([("124.00", "24", 2, "20.00")], shipping="5.00")
            _charge(order, "100.00", _at(2026, 3), fee="1.00")
            order.items.get().refund(quantity=1)
            ExpenseFactory(
                date=date(2026, 4, 1), category=ExpenseCategory.MARKETING
            )

        with django_assert_num_queries(8):
            _by_month()
