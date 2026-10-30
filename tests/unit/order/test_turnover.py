"""Turnover: net of VAT, shipping and fees included, an order counted when
it left the store, less what went back.

Every order below pins what the figure reads (``OrderFactory`` randomises
status, payment status, pay way and shipping): the lines with their price
and VAT rate, shipping, the payment fee, the payment status and the moment
it shipped. Amounts are chosen so the net is a round number.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
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
from order.turnover import (
    Bucket,
    GroupBy,
    a_year_earlier,
    figures,
    figures_with_prior_year,
    first_year,
    total,
)
from product.factories import ProductFactory
from tests.utils.discounts import snapshot_discounts
from tests.utils.turnover import snapshot_sale
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

D = Decimal
ONE_LINE = ((D("124.00"), D("24"), 1),)
NEVER = object()


def _at(year, month, day=15, hour=12, minute=0) -> datetime:
    """A moment in UTC; midday is the same calendar day in Athens."""
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _order(
    lines=ONE_LINE,
    *,
    shipped=None,
    payment_status=PaymentStatus.COMPLETED,
    status=OrderStatus.DELIVERED,
    shipping="0",
    fee="0",
    discount=None,
    loyalty=None,
    gift_card=None,
    pay_way_key="",
    products=None,
):
    """An order with ``(unit gross, VAT rate, quantity)`` lines that left
    the store at ``shipped`` (March 15th by default; ``NEVER``: it did
    not)."""
    order = OrderFactory(
        num_order_items=0,
        status=status,
        payment_status=payment_status,
        shipping_price=Money(D(shipping), "EUR"),
        payment_method_fee=Money(D(fee), "EUR"),
        pay_way_key=pay_way_key,
    )
    order.items.all().delete()
    for index, (gross, rate, quantity) in enumerate(lines):
        order.items.create(
            product=(products or {}).get(index)
            or ProductFactory(vat=VatFactory(value=rate)),
            price=Money(gross, "EUR"),
            quantity=quantity,
            vat_rate=rate,
        )
    order.discount_amount = Money(D(discount or 0), "EUR")
    order.loyalty_discount = Money(D(loyalty or 0), "EUR")
    order.gift_card_amount = Money(D(gift_card or 0), "EUR")
    order.save()
    snapshot_discounts(order)
    return snapshot_sale(
        order, None if shipped is NEVER else shipped or _at(2026, 3)
    )


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


def _gift_card_credit(order, amount, when):
    row = GiftCardTransaction.objects.create(
        gift_card=GiftCardFactory(),
        kind=GiftCardTransactionKind.REFUND_CREDIT,
        amount=D(amount),
        order=order,
    )
    GiftCardTransaction.objects.filter(pk=row.pk).update(created_at=when)


def _march(year=2026):
    return figures(date(year, 3, 1), date(year, 3, 31), None)[0]


def _by_month(year=2026):
    return figures(date(year, 1, 1), date(year, 12, 31), Bucket.MONTH)


def _in(rows, month):
    return rows[month - 1]


class TestWhatASaleIsWorth:
    def test_goods_net_excludes_vat(self):
        _order([(D("124.00"), D("24"), 1)])

        row = _march()
        assert row.goods == D("100.00")
        assert row.vat == D("24.00")
        assert row.sales_gross == D("124.00")

    def test_every_vat_rate_is_backed_out_at_its_own_rate(self):
        _order(
            [
                (D("124.00"), D("24"), 1),
                (D("56.50"), D("13"), 2),
                (D("10.60"), D("6"), 1),
                (D("5.00"), D("0"), 1),
            ]
        )

        row = _march()
        assert row.goods == D("215.00")
        assert row.sales_gross == D("252.60")

    def test_a_line_is_rounded_to_the_cent_like_the_receipt(self):
        # 1.00 at 24% nets 0.8064, so 0.81; a line of three units is
        # rounded once, as a line: 3.00 / 1.24 = 2.4194, so 2.42.
        _order([(D("1.00"), D("24"), 1)])
        _order([(D("1.00"), D("24"), 3)], shipped=_at(2026, 4))

        rows = _by_month()
        assert _in(rows, 3).goods == D("0.81")
        assert _in(rows, 4).goods == D("2.42")

    def test_shipping_and_the_payment_fee_are_turnover_net_of_their_vat(self):
        _order(shipping="12.40", fee="3.10")

        row = _march()
        assert row.goods == D("100.00")
        assert row.shipping == D("10.00")
        assert row.fees == D("2.50")
        assert row.sales_net == D("112.50")
        assert row.vat == D("27.00")
        assert row.sales_gross == D("139.50")

    def test_a_charge_on_a_mixed_rate_order_follows_the_goods_rates(self):
        # Goods 124.00 at 24% and 113.00 at 13% weigh 124:113. Shipping
        # of 23.70 splits 12.40 / 11.30 and is taxed at its own rate.
        _order(
            [(D("124.00"), D("24"), 1), (D("113.00"), D("13"), 1)],
            shipping="23.70",
        )

        row = _march()
        assert row.shipping == D("20.00")
        assert row.vat == D("24.00") + D("13.00") + D("2.40") + D("1.30")

    def test_a_discount_reduces_the_net_on_the_line_it_was_granted_on(self):
        _order([(D("124.00"), D("24"), 1)], discount="24.80")

        assert _march().goods == D("80.00")

    def test_a_loyalty_redemption_reduces_the_net(self):
        _order([(D("124.00"), D("24"), 1)], loyalty="12.40")

        assert _march().goods == D("90.00")

    def test_a_gift_card_is_a_payment_and_does_not_reduce_the_net(self):
        _order([(D("124.00"), D("24"), 1)], gift_card="124.00")

        assert _march().goods == D("100.00")

    def test_the_figure_is_the_receipts_own_split(self):
        from order.taxation import order_vat_lines

        order = _order(
            [(D("124.00"), D("24"), 1), (D("56.50"), D("13"), 2)],
            shipping="9.90",
            fee="2.00",
            discount="10.00",
        )
        lines = order_vat_lines(order)

        row = _march()
        assert row.sales_net == sum(line.net for line in lines)
        assert row.vat == sum(line.vat for line in lines)

    def test_orders_units_and_the_average_order(self):
        _order([(D("10.00"), D("0"), 2), (D("20.00"), D("0"), 1)])
        _order([(D("10.00"), D("0"), 1)])

        row = _march()
        assert row.orders == 2
        assert row.units == 4
        assert row.average_order_value == D("25.00")

    def test_an_order_that_bills_nothing_is_still_an_order(self):
        order = _order([(D("10.00"), D("0"), 1)], discount="10.00")

        row = _march()
        assert order.vat_lines.count() == 0
        assert row.orders == 1
        assert row.sales_net == D("0")


class TestWhenASaleIsEarned:
    def test_an_order_counts_when_it_left_the_store_not_when_it_was_placed(
        self,
    ):
        order = _order(shipped=_at(2026, 4, 2))
        Order.objects.filter(pk=order.pk).update(created_at=_at(2026, 3, 28))

        rows = _by_month()
        assert _in(rows, 3).goods == D("0")
        assert _in(rows, 4).goods == D("100.00")

    def test_cash_on_delivery_counts_at_shipment_before_it_is_collected(self):
        _order(
            payment_status=PaymentStatus.PENDING,
            status=OrderStatus.SHIPPED,
            shipping="12.40",
        )

        row = _march()
        assert row.goods == D("100.00")
        assert row.orders == 1

    def test_what_is_still_owed_is_reported_apart(self):
        order = _order(
            payment_status=PaymentStatus.PENDING,
            status=OrderStatus.SHIPPED,
            shipping="12.40",
        )
        _order()

        row = _march()
        assert row.awaiting_collection == order.amount_due.amount
        assert row.sales_gross == D("260.40")

    def test_collecting_later_changes_no_past_month(self):
        order = _order(
            payment_status=PaymentStatus.PENDING, status=OrderStatus.SHIPPED
        )
        before = _march()

        Order.objects.filter(pk=order.pk).update(
            payment_status=PaymentStatus.COMPLETED
        )

        after = _march()
        assert before.awaiting_collection == order.amount_due.amount
        assert after.awaiting_collection == D("0")
        assert (
            replace(after, awaiting_collection=before.awaiting_collection)
            == before
        )

    def test_a_paid_order_that_has_not_shipped_is_not_a_sale_yet(self):
        _order(status=OrderStatus.PROCESSING, shipped=NEVER)

        assert _march().orders == 0
        assert _march().goods == D("0")

    def test_an_order_canceled_and_refunded_before_it_shipped_is_neither(self):
        order = _order(
            payment_status=PaymentStatus.REFUNDED,
            status=OrderStatus.CANCELED,
            shipped=NEVER,
        )
        _refund(order, "124.00", _at(2026, 4))

        rows = _by_month()
        assert _in(rows, 3).goods == D("0")
        assert _in(rows, 4).returns_net == D("0")

    def test_a_parcel_refused_is_taken_out_in_the_month_it_came_back(self):
        order = _order(
            payment_status=PaymentStatus.PENDING,
            status=OrderStatus.SHIPPED,
            shipping="12.40",
            fee="3.10",
        )
        order.status = OrderStatus.RETURNED
        order.save()
        Order.objects.filter(pk=order.pk).update(
            status_updated_at=_at(2026, 4, 20)
        )

        rows = _by_month()
        assert order.payment_status == PaymentStatus.CANCELED
        assert _in(rows, 3).sales_net == D("112.50")
        assert _in(rows, 3).returns_net == D("0")
        assert _in(rows, 4).returns_net == D("112.50")
        assert _in(rows, 4).returns_vat == D("27.00")
        assert _in(rows, 4).turnover == D("-112.50")
        assert total(rows).turnover == D("0")
        assert _in(rows, 3).awaiting_collection == D("0")

    def test_another_year_is_not_included(self):
        _order(shipped=_at(2025, 3))

        assert sum(row.goods for row in _by_month()) == D("0")
        assert _in(_by_month(2025), 3).goods == D("100.00")


class TestReturns:
    def test_a_refund_in_a_later_month_belongs_to_that_month(self):
        order = _order(
            shipped=_at(2026, 3), payment_status=PaymentStatus.REFUNDED
        )
        _refund(order, "124.00", _at(2026, 5))

        rows = _by_month()
        assert _in(rows, 3).goods == D("100.00")
        assert _in(rows, 3).returns_net == D("0")
        assert _in(rows, 5).returns_net == D("100.00")
        assert _in(rows, 5).returns_vat == D("24.00")
        assert _in(rows, 5).turnover == D("-100.00")
        assert _in(rows, 5).goods == D("0")

    def test_a_refund_in_a_later_year_is_that_years_return(self):
        order = _order(
            shipped=_at(2025, 12, 20), payment_status=PaymentStatus.REFUNDED
        )
        _refund(order, "124.00", _at(2026, 1, 10))

        assert _in(_by_month(2025), 12).returns_net == D("0")
        assert _in(_by_month(2026), 1).returns_net == D("100.00")

    def test_a_partial_refund_returns_its_share_of_the_receipt(self):
        # Receipt gross 134.00 (124.00 goods + 10.00 shipping): a refund of
        # 67.00 is half of it, so half of the 100.00 + 8.06 net.
        order = _order(
            shipping="10.00",
            payment_status=PaymentStatus.PARTIALLY_REFUNDED,
        )
        _refund(order, "67.00", _at(2026, 4))

        row = _in(_by_month(), 4)
        assert row.returns_gross == D("67.00")
        assert row.returns_net == D("54.03")
        assert row.returns_vat == D("12.97")

    def test_refunding_the_whole_receipt_returns_the_whole_sale(self):
        order = _order(
            [(D("124.00"), D("24"), 1), (D("56.50"), D("13"), 2)],
            shipping="9.90",
            fee="2.00",
            payment_status=PaymentStatus.REFUNDED,
        )
        _refund(order, "124.00", _at(2026, 4))
        _refund(order, "113.00", _at(2026, 4))
        _refund(order, "11.90", _at(2026, 4))

        rows = _by_month()
        sale, back = _in(rows, 3), _in(rows, 4)
        assert back.returns_gross == sale.sales_gross
        assert abs(back.returns_net - sale.sales_net) <= D("0.02")

    def test_a_refund_mixing_card_and_gift_card_adds_up_to_the_receipt(self):
        order = _order(gift_card="24.80", payment_status=PaymentStatus.REFUNDED)
        _refund(order, "99.20", _at(2026, 4))
        _gift_card_credit(order, "24.80", _at(2026, 4))

        row = _in(_by_month(), 4)
        assert row.returns_net == D("100.00")
        assert row.returns_gross == D("124.00")

    def test_a_refund_reversal_gives_the_return_back(self):
        order = _order(payment_status=PaymentStatus.COMPLETED)
        charge = _ledger(
            order, PaymentTransactionKind.CHARGE, "124.00", _at(2026, 3)
        )
        _ledger(
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

        rows = _by_month()
        assert _in(rows, 4).returns_net == D("100.00")
        assert _in(rows, 5).returns_net == D("-100.00")

    def test_value_returned_onto_a_gift_card_is_a_return(self):
        order = _order(
            gift_card="124.00", payment_status=PaymentStatus.REFUNDED
        )
        _gift_card_credit(order, "124.00", _at(2026, 6))

        assert _in(_by_month(), 6).returns_net == D("100.00")

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
        rows = _by_month()
        assert _in(rows, 4).returns_net == D("0")
        assert _in(rows, 7).returns_net == D("100.00")

    def test_a_refund_of_an_order_that_never_shipped_is_not_a_return(self):
        order = _order(
            payment_status=PaymentStatus.REFUNDED,
            status=OrderStatus.CANCELED,
            shipped=NEVER,
        )
        _refund(order, "124.00", _at(2026, 4))

        assert _in(_by_month(), 4).returns_net == D("0")

    def test_a_canceled_unpaid_order_gives_no_return_through_its_credit(self):
        order = _order(
            payment_status=PaymentStatus.CANCELED,
            status=OrderStatus.CANCELED,
            gift_card="50.00",
            shipped=NEVER,
        )
        _gift_card_credit(order, "50.00", _at(2026, 3))

        assert _march().returns_net == D("0")

    def test_an_order_without_a_receipt_value_does_not_divide_by_zero(self):
        order = _order([(D("10.00"), D("0"), 1)], discount="10.00")
        _refund(order, "1.00", _at(2026, 4))

        assert _in(_by_month(), 4).returns_net == D("0")


class TestRemovedOrders:
    def test_a_soft_deleted_order_is_in_no_figure(self):
        sale = _order(shipped=_at(2026, 3))
        _refund(sale, "124.00", _at(2026, 4))
        _order(shipped=_at(2026, 3))
        sale.delete()

        rows = _by_month()
        assert _in(rows, 3).orders == 1
        assert _in(rows, 3).goods == D("100.00")
        assert _in(rows, 4).returns_net == D("0")


class TestBuckets:
    def test_quarters_months_weeks_and_days(self):
        _order(shipped=_at(2026, 2, 10))
        _order(shipped=_at(2026, 5, 4))

        quarters = figures(date(2026, 1, 1), date(2026, 6, 30), Bucket.QUARTER)
        assert [row.period for row in quarters] == [
            date(2026, 1, 1),
            date(2026, 4, 1),
        ]
        assert [row.goods for row in quarters] == [D("100.00")] * 2

        weeks = figures(date(2026, 5, 1), date(2026, 5, 10), Bucket.WEEK)
        assert [row.period for row in weeks] == [
            date(2026, 4, 27),
            date(2026, 5, 4),
        ]
        assert weeks[1].goods == D("100.00")

        days = figures(date(2026, 5, 3), date(2026, 5, 5), Bucket.DAY)
        assert [row.goods for row in days] == [D("0"), D("100.00"), D("0")]

    def test_a_range_without_a_bucket_is_one_row(self):
        _order(shipped=_at(2026, 2, 10))
        _order(shipped=_at(2026, 5, 4))

        [row] = figures(date(2026, 1, 1), date(2026, 12, 31), None)

        assert row.period is None
        assert row.goods == D("200.00")
        assert row.orders == 2

    def test_the_end_day_is_included(self):
        _order(shipped=_at(2026, 3, 31, 20, 0))

        assert _march().orders == 1

    def test_an_empty_bucket_is_zeros_not_a_gap(self):
        rows = _by_month(2031)

        assert [row.period.month for row in rows] == list(range(1, 13))
        assert all(row.sales_net == row.returns_net == D("0") for row in rows)


class TestTheYearBefore:
    def test_the_same_range_a_year_earlier_lines_up_by_day(self):
        _order(shipped=_at(2025, 3))
        _order(shipped=_at(2026, 3))
        _order(shipped=_at(2026, 3, 20))

        rows, prior = figures_with_prior_year(
            date(2026, 3, 1), date(2026, 3, 31), Bucket.MONTH
        )

        assert rows[0].goods == D("200.00")
        assert prior[a_year_earlier(rows[0].period)].goods == D("100.00")

    @pytest.mark.parametrize(
        ("day", "earlier"),
        [
            (date(2026, 3, 1), date(2025, 3, 1)),
            (date(2024, 2, 29), date(2023, 2, 28)),
        ],
    )
    def test_a_leap_day_has_an_earlier_twin(self, day, earlier):
        assert a_year_earlier(day) == earlier


class TestBreakdowns:
    def test_by_vat_rate(self):
        _order(
            [(D("124.00"), D("24"), 1), (D("56.50"), D("13"), 2)],
            shipping="12.40",
        )

        rows = figures(
            date(2026, 3, 1), date(2026, 3, 31), None, GroupBy.VAT_RATE
        )

        by_rate = {row.group: row for row in rows}
        assert by_rate[D("24.0")].sales_net == D("105.23")
        assert by_rate[D("13.0")].goods == D("100.00")
        assert set(by_rate) == {D("24.0"), D("13.0")}

    def test_by_product_counts_goods_and_units_only(self):
        product = ProductFactory(vat=VatFactory(value=D("24")))
        _order(
            [(D("124.00"), D("24"), 2)],
            shipping="12.40",
            products={0: product},
        )

        [row] = figures(
            date(2026, 3, 1), date(2026, 3, 31), None, GroupBy.PRODUCT
        )

        assert row.group == product.pk
        assert row.goods == D("200.00")
        assert row.shipping == D("0")
        assert row.units == 2

    def test_by_category(self):
        product = ProductFactory(vat=VatFactory(value=D("24")))
        _order([(D("124.00"), D("24"), 1)], products={0: product})

        [row] = figures(
            date(2026, 3, 1), date(2026, 3, 31), None, GroupBy.CATEGORY
        )

        assert row.group == product.category_id
        assert row.goods == D("100.00")

    def test_by_payment_method_includes_shipping_and_fees(self):
        _order(pay_way_key="PAY_ON_DELIVERY", shipping="12.40", fee="3.10")
        _order(pay_way_key="CREDIT_CARD")

        rows = figures(
            date(2026, 3, 1), date(2026, 3, 31), None, GroupBy.PAYMENT_METHOD
        )

        assert [row.group for row in rows] == ["PAY_ON_DELIVERY", "CREDIT_CARD"]
        assert rows[0].sales_net == D("112.50")
        assert rows[0].orders == 1

    def test_by_channel_separates_a_missing_attribution(self):
        from order.models import OrderAttribution

        tracked = _order()
        OrderAttribution.objects.create(
            order=tracked, source="instagram", source_type="social"
        )
        _order()

        rows = figures(
            date(2026, 3, 1), date(2026, 3, 31), None, GroupBy.CHANNEL
        )

        assert {row.group for row in rows} == {"instagram", None}

    def test_a_breakdown_has_its_own_row_per_period(self):
        _order(shipped=_at(2026, 3), pay_way_key="CREDIT_CARD")
        _order(shipped=_at(2026, 4), pay_way_key="CREDIT_CARD")

        rows = figures(
            date(2026, 3, 1),
            date(2026, 4, 30),
            Bucket.MONTH,
            GroupBy.PAYMENT_METHOD,
        )

        assert [row.period for row in rows] == [
            date(2026, 3, 1),
            date(2026, 4, 1),
        ]


class TestTheMonthIsTheStoresMonth:
    """Europe/Athens: UTC+3 in summer, UTC+2 in winter."""

    def test_midnight_athens_starts_the_next_month(self):
        # 2026-03-31 21:00 UTC is 2026-04-01 00:00 EEST.
        _order(shipped=_at(2026, 3, 31, 21, 0))
        # One minute earlier is still March 31st in Athens.
        _order(shipped=_at(2026, 3, 31, 20, 59))

        rows = _by_month()
        assert _in(rows, 3).orders == 1
        assert _in(rows, 4).orders == 1

    def test_the_new_year_starts_at_midnight_in_athens(self):
        # 2025-12-31 22:00 UTC is 2026-01-01 00:00 EET.
        _order(shipped=_at(2025, 12, 31, 22, 0))
        _order(shipped=_at(2025, 12, 31, 21, 59))

        assert _in(_by_month(), 1).orders == 1
        assert _in(_by_month(2025), 12).orders == 1

    def test_a_refund_follows_the_same_boundary(self):
        order = _order(payment_status=PaymentStatus.REFUNDED)
        _refund(order, "124.00", _at(2026, 4, 30, 21, 0))

        rows = _by_month()
        assert _in(rows, 4).returns_net == D("0")
        assert _in(rows, 5).returns_net == D("100.00")


class TestTheFirstYear:
    def test_it_is_the_oldest_order_that_left_the_store(self):
        assert first_year() is None

        _order(shipped=_at(2024, 6))
        _order(shipped=_at(2026, 2))
        _order(shipped=NEVER, status=OrderStatus.PROCESSING)

        assert first_year() == 2024


class TestQueries:
    def test_the_query_count_does_not_grow_with_the_orders(
        self, django_assert_num_queries
    ):
        _order()
        with django_assert_num_queries(3):
            _by_month()

        for _ in range(6):
            order = _order(
                shipping="5.00", payment_status=PaymentStatus.REFUNDED
            )
            _refund(order, "20.00", _at(2026, 4))
            _gift_card_credit(order, "5.00", _at(2026, 5))

        with django_assert_num_queries(3):
            _by_month()

    def test_a_breakdown_is_one_query(self, django_assert_num_queries):
        _order()
        with django_assert_num_queries(1):
            figures(
                date(2026, 1, 1),
                date(2026, 12, 31),
                Bucket.MONTH,
                GroupBy.VAT_RATE,
            )
