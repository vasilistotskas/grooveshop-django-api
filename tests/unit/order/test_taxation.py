"""The one VAT rule the PDF invoice and the myDATA payload share.

Shipping and the payment-method fee are incidental costs charged to the
customer, so they are part of the taxable amount of the goods they
accompany (Directive 2006/112/EC art. 78(b); Greek VAT Code, Law
5144/2024, art. 24(4)(a)) and take the goods' rate — apportioned over the
rates when the order mixes them. ``order.taxation`` is where that lives;
these tests pin its arithmetic, the document-level agreement is in
``test_invoice_mydata_agreement.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from order.discounts import proportional_shares
from order.factories.order import OrderFactory
from order.taxation import (
    ChargeKind,
    breakdown_by_rate,
    order_vat_lines,
    split_gross,
)
from product.factories import ProductFactory
from tests.utils.discounts import snapshot_discounts
from vat.constants import MYDATA_SUPPORTED_VAT_RATES
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

D = Decimal


def _order(
    lines, *, shipping="0", fee="0", discount=None, promotion_lines=None
):
    """``lines``: ``(unit gross, VAT rate, quantity)`` per goods line.

    ``promotion_lines`` maps a line's index to the promotion money it
    carries (a scoped promotion); without it the discount is order-wide.

    The rate is stated on each item, as checkout does, so these tests do
    not lean on the product's current VAT.
    """
    order = OrderFactory(
        num_order_items=0,
        shipping_price=Money(D(shipping), "EUR"),
        payment_method_fee=Money(D(fee), "EUR"),
    )
    order.items.all().delete()
    created = [
        order.items.create(
            product=ProductFactory(vat=VatFactory(value=D(str(rate)))),
            price=Money(D(str(gross)), "EUR"),
            quantity=quantity,
            vat_rate=D(str(rate)),
        )
        for gross, rate, quantity in lines
    ]
    if discount is not None:
        order.discount_amount = Money(D(str(discount)), "EUR")
        order.save()
        snapshot_discounts(
            order,
            None
            if promotion_lines is None
            else {
                created[index].pk: D(amount)
                for index, amount in promotion_lines.items()
            },
        )
    return order


def _gross(lines, kind):
    return sum((line.gross for line in lines if line.kind == kind), D(0))


class TestSplitGross:
    def test_each_part_is_rounded_half_up_to_the_cent(self):
        assert split_gross(D("3.50"), D("24")) == (D("2.82"), D("0.68"))
        assert split_gross(D("1.25"), D("24")) == (D("1.01"), D("0.24"))

    def test_a_zero_rate_leaves_the_amount_whole(self):
        assert split_gross(D("5.00"), D("0")) == (D("5.00"), D("0.00"))

    def test_a_half_cent_tie_still_adds_up_to_the_gross(self):
        # 0.13 at 4%: the net is exactly 0.125 and the VAT 0.005. Rounding
        # both up billed 0.14 for 0.13 of goods.
        net, vat = split_gross(D("0.13"), D("4"))

        assert (net, vat) == (D("0.13"), D("0.00"))
        assert net + vat == D("0.13")

    def test_net_plus_vat_is_the_gross_for_every_cent_at_every_rate(self):
        for cents in range(1, 501):
            gross = D(cents) / 100
            for rate in MYDATA_SUPPORTED_VAT_RATES:
                net, vat = split_gross(gross, rate)
                assert net + vat == gross, (gross, rate)
                assert net >= 0
                assert vat >= 0


class TestProportionalShares:
    def test_shares_sum_exactly_to_the_amount(self):
        shares = proportional_shares(
            D("10.00"), {D("24"): D("124.00"), D("13"): D("113.00")}
        )

        assert sum(shares.values()) == D("10.00")
        assert shares == {D("24"): D("5.23"), D("13"): D("4.77")}

    def test_an_amount_smaller_than_the_weights_still_sums_exactly(self):
        shares = proportional_shares(
            D("0.01"),
            {D("24"): D("1.00"), D("13"): D("1.00"), D("6"): D("1.00")},
        )

        assert sum(shares.values()) == D("0.01")


class TestGoodsLines:
    def test_goods_are_split_at_the_rate_snapshotted_on_the_item(self):
        # The product says 24% today; the line was sold at 13%.
        order = _order([])
        order.items.create(
            product=ProductFactory(vat=VatFactory(value=D("24"))),
            price=Money(D("11.30"), "EUR"),
            quantity=1,
            vat_rate=D("13"),
        )

        (line,) = order_vat_lines(order)

        assert line.kind is ChargeKind.GOODS
        assert line.rate == D("13")
        assert (line.net, line.vat) == (D("10.00"), D("1.30"))

    def test_each_line_is_rounded_before_it_is_summed(self):
        """Three 1.00 lines at 24%: rounding the summed bucket would give
        net 2.42 / VAT 0.58, but myDATA validates the sum of the rounded
        lines, 2.43 / 0.57 — and the PDF has to print that sum."""
        order = _order([("1.00", "24", 1)] * 3)

        (row,) = breakdown_by_rate(order_vat_lines(order))

        assert (row["subtotal"], row["vat"], row["gross"]) == (
            "2.43",
            "0.57",
            "3.00",
        )

    def test_every_line_adds_up_to_what_was_charged(self):
        """Two 0.13 lines at 4% plus 0.13 of shipping at the same rate:
        each line's net and VAT are its gross, so the order bills 0.39
        for 0.39, not 0.42."""
        order = _order([("0.13", "4", 1), ("0.13", "4", 1)], shipping="0.13")

        lines = order_vat_lines(order)

        assert [line.gross for line in lines] == [D("0.13")] * 3
        assert sum(line.gross for line in lines) == D("0.39")

    def test_a_line_with_nothing_to_pay_is_left_out(self):
        """myDATA rejects zero-amount lines, and a row of zeros in the
        breakdown says nothing."""
        order = _order([("12.40", "24", 1), ("0.00", "6", 1)])

        lines = order_vat_lines(order)

        assert [line.rate for line in lines] == [D("24")]


class TestAncillaryCharges:
    def test_a_single_rate_order_takes_its_rate_for_every_charge(self):
        order = _order([("12.40", "24", 1)], shipping="3.50", fee="1.25")

        goods, shipping, fee = order_vat_lines(order)

        assert (goods.kind, shipping.kind, fee.kind) == (
            ChargeKind.GOODS,
            ChargeKind.SHIPPING,
            ChargeKind.PAYMENT_FEE,
        )
        assert (shipping.rate, shipping.net, shipping.vat) == (
            D("24"),
            D("2.82"),
            D("0.68"),
        )
        assert (fee.rate, fee.net, fee.vat) == (D("24"), D("1.01"), D("0.24"))

    def test_a_reduced_rate_basket_gets_reduced_rate_shipping(self):
        order = _order([("10.60", "6", 1)], shipping="3.50")

        shipping = order_vat_lines(order)[1]

        assert shipping.rate == D("6")
        assert (shipping.net, shipping.vat) == (D("3.30"), D("0.20"))

    def test_a_zero_rated_basket_gets_zero_rated_shipping(self):
        order = _order([("10.00", "0", 1)], shipping="3.50")

        shipping = order_vat_lines(order)[1]

        assert shipping.rate == D("0")
        assert (shipping.net, shipping.vat) == (D("3.50"), D("0.00"))

    def test_a_mixed_basket_apportions_each_charge_by_goods_value(self):
        # 124.00 of goods at 24% and 113.00 at 13%: 10.00 of shipping
        # splits 5.23 / 4.77 and each share is taxed at its own rate.
        order = _order(
            [("124.00", "24", 1), ("113.00", "13", 1)], shipping="10.00"
        )

        shipping = [
            line
            for line in order_vat_lines(order)
            if line.kind is ChargeKind.SHIPPING
        ]

        assert [(line.rate, line.gross) for line in shipping] == [
            (D("24"), D("5.23")),
            (D("13"), D("4.77")),
        ]
        assert [(line.net, line.vat) for line in shipping] == [
            (D("4.22"), D("1.01")),
            (D("4.22"), D("0.55")),
        ]

    def test_the_shares_of_every_charge_add_up_to_the_charge(self):
        order = _order(
            [("33.33", "24", 3), ("7.77", "13", 2), ("1.01", "6", 7)],
            shipping="4.99",
            fee="1.23",
        )

        lines = order_vat_lines(order)

        assert _gross(lines, ChargeKind.SHIPPING) == D("4.99")
        assert _gross(lines, ChargeKind.PAYMENT_FEE) == D("1.23")

    def test_a_rate_with_no_value_in_the_basket_takes_no_share(self):
        # A free gift at 6% sits next to paid goods at 24%.
        order = _order([("12.40", "24", 1), ("0.00", "6", 1)], shipping="3.50")

        shipping = order_vat_lines(order)[1:]

        assert [(line.rate, line.gross) for line in shipping] == [
            (D("24"), D("3.50"))
        ]

    def test_an_order_level_discount_does_not_move_the_apportionment(self):
        order = _order(
            [("100.00", "24", 1), ("100.00", "6", 1)],
            shipping="10.00",
            discount="50.00",
        )

        lines = order_vat_lines(order)

        assert [
            (line.rate, line.gross)
            for line in lines
            if line.kind is ChargeKind.SHIPPING
        ] == [(D("24"), D("5.00")), (D("6"), D("5.00"))]
        # The discount still reduces the goods themselves.
        assert _gross(lines, ChargeKind.GOODS) == D("150.00")

    def test_a_discount_on_one_rate_moves_the_apportionment_to_the_other(self):
        """Shipping follows the value of the goods CHARGED (Εγκ. 10/1987),
        so a promotion that only took the 24% line down leaves it the
        smaller weight: 50.00 at 24% against 100.00 at 6% is 5.00 / 10.00,
        where the undiscounted 100.00 / 100.00 gave 7.50 each."""
        order = _order(
            [("100.00", "24", 1), ("100.00", "6", 1)],
            shipping="15.00",
            discount="50.00",
            promotion_lines={0: "50.00"},
        )

        lines = order_vat_lines(order)

        assert [
            (line.rate, line.gross)
            for line in lines
            if line.kind is ChargeKind.SHIPPING
        ] == [(D("24"), D("5.00")), (D("6"), D("10.00"))]
        assert [
            (line.rate, line.gross)
            for line in lines
            if line.kind is ChargeKind.GOODS
        ] == [(D("24"), D("50.00")), (D("6"), D("100.00"))]

    def test_a_fully_discounted_rate_takes_no_share_of_the_charge(self):
        order = _order(
            [("10.00", "24", 1), ("30.00", "6", 1)],
            shipping="4.00",
            discount="10.00",
            promotion_lines={0: "10.00"},
        )

        shipping = [
            line
            for line in order_vat_lines(order)
            if line.kind is ChargeKind.SHIPPING
        ]

        assert [(line.rate, line.gross) for line in shipping] == [
            (D("6"), D("4.00"))
        ]

    def test_goods_with_no_value_fall_back_to_units(self):
        """A free sample with paid delivery: nothing to weigh by value,
        so the charge follows the units that travel."""
        order = _order([("0.00", "24", 1), ("0.00", "6", 3)], shipping="4.00")

        lines = order_vat_lines(order)

        assert [(line.rate, line.gross) for line in lines] == [
            (D("24"), D("1.00")),
            (D("6"), D("3.00")),
        ]

    def test_a_charge_with_no_goods_to_follow_is_refused(self):
        order = _order([], shipping="3.50")

        with pytest.raises(ValueError, match="no goods"):
            order_vat_lines(order)

    def test_no_charge_means_no_apportionment_even_without_goods(self):
        assert order_vat_lines(_order([])) == []


class TestBreakdownByRate:
    def test_rows_are_highest_rate_first_with_string_amounts(self):
        order = _order([("12.40", "24", 1), ("10.60", "6", 1)], shipping="3.50")

        rows = breakdown_by_rate(order_vat_lines(order))

        assert [D(row["rate"]) for row in rows] == [D("24"), D("6")]
        assert all(
            isinstance(row[key], str)
            for row in rows
            for key in ("rate", "subtotal", "vat", "gross")
        )
        assert all(
            D(row["subtotal"]) + D(row["vat"]) == D(row["gross"])
            for row in rows
        )
