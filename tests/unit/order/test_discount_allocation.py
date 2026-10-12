"""Largest-remainder discount allocation invariants.

The invoice PDF and the AADE myDATA submission both reduce per-line
gross values by the discount each line carries (``OrderItem.
discount_allocation``, snapshotted at checkout); the rounded lines MUST
sum exactly to (items total − discount) or AADE rejects the document
(errors 203 / 207-210).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.apps import apps
from djmoney.money import Money

from order.discount_snapshot import heal_discount_allocation
from order.discounts import (
    allocate_capped,
    discounted_line_gross,
    order_discount_total,
)
from tests.utils.discounts import snapshot_discounts

pytestmark = pytest.mark.django_db

D = Decimal


def _order_with_lines(lines, discount=None, loyalty=None, snapshot=True):
    from djmoney.money import Money as M

    from order.factories.order import OrderFactory
    from product.factories import ProductFactory

    order = OrderFactory(num_order_items=0)
    order.items.all().delete()
    for price, quantity in lines:
        product = ProductFactory(
            price=M(Decimal(str(price)), "EUR"),
            discount_percent=Decimal(0),
            vat=None,
            stock=100,
        )
        order.items.create(
            product=product,
            price=M(Decimal(str(price)), "EUR"),
            quantity=quantity,
        )
    if discount is not None:
        order.discount_amount = Money(Decimal(str(discount)), "EUR")
    if loyalty is not None:
        order.loyalty_discount = Money(Decimal(str(loyalty)), "EUR")
    order.save()
    if snapshot:
        snapshot_discounts(order)
    return order


class TestAllocation:
    def test_no_discount_returns_full_gross(self):
        order = _order_with_lines([(10, 2), (5, 1)])

        allocation = discounted_line_gross(order)

        assert sorted(allocation.values()) == [
            Decimal(5),
            Decimal(20),
        ]

    def test_allocated_lines_sum_exactly_to_discounted_total(self):
        # 3 lines with awkward proportions force rounding remainders.
        order = _order_with_lines(
            [(19.99, 1), (7.77, 2), (3.33, 3)], discount="10.00"
        )
        items_total = Decimal("19.99") + Decimal("15.54") + Decimal("9.99")

        allocation = discounted_line_gross(order)

        assert sum(allocation.values()) == items_total - Decimal("10.00")
        assert all(
            value == value.quantize(Decimal("0.01"))
            for value in allocation.values()
        )

    def test_combines_promotion_and_loyalty_but_not_gift_card(self):
        order = _order_with_lines([(50, 1)], discount="5.00", loyalty="2.50")
        order.gift_card_amount = Money(Decimal("10.00"), "EUR")
        order.save(
            update_fields=["gift_card_amount", "gift_card_amount_currency"]
        )

        assert order_discount_total(order) == Decimal("7.50")
        allocation = discounted_line_gross(order)
        assert sum(allocation.values()) == Decimal("42.50")

    def test_discount_larger_than_items_clamps_to_zero(self):
        order = _order_with_lines([(10, 1), (5, 1)], discount="99.00")

        allocation = discounted_line_gross(order)

        assert sum(allocation.values()) == Decimal("0.00")
        assert all(value >= 0 for value in allocation.values())

    def test_no_line_goes_negative_near_full_discount(self):
        order = _order_with_lines([(0.03, 1), (99.97, 1)], discount="99.99")

        allocation = discounted_line_gross(order)

        assert sum(allocation.values()) == Decimal("0.01")
        assert all(value >= 0 for value in allocation.values())

    def test_a_scoped_discount_leaves_the_other_lines_whole(self):
        order = _order_with_lines(
            [(10, 1), (20, 1)], discount="4.00", snapshot=False
        )
        scoped, other = list(order.items.order_by("price"))
        snapshot_discounts(order, {scoped.pk: D("4.00")})

        allocation = discounted_line_gross(order)

        assert allocation == {scoped.pk: D("6.00"), other.pk: D("20.00")}


class TestAllocateCapped:
    def test_shares_follow_the_weights_and_sum_to_the_amount(self):
        weights = {1: D("19.99"), 2: D("15.54"), 3: D("9.99")}

        shares = allocate_capped(D("10.00"), weights, weights)

        assert sum(shares.values()) == D("10.00")
        assert all(
            share == share.quantize(D("0.01")) for share in shares.values()
        )

    def test_a_line_at_its_cap_hands_the_rest_to_the_others(self):
        weights = {1: D("10.00"), 2: D("10.00")}
        caps = {1: D("1.00"), 2: D("50.00")}

        shares = allocate_capped(D("8.00"), weights, caps)

        assert shares == {1: D("1.00"), 2: D("7.00")}

    def test_more_than_the_caps_hold_takes_only_what_they_hold(self):
        weights = {1: D("10.00"), 2: D("5.00")}

        shares = allocate_capped(D("99.00"), weights, weights)

        assert shares == {1: D("10.00"), 2: D("5.00")}

    def test_a_key_with_no_weight_takes_nothing(self):
        shares = allocate_capped(
            D("5.00"),
            {1: D("0"), 2: D("10.00")},
            {1: D("10.00"), 2: D("10.00")},
        )

        assert shares == {1: D("0"), 2: D("5.00")}


class TestSnapshotMustAddUp:
    """A document built on lines that do not carry the order's discount
    would bill a total the customer never owed, so it refuses."""

    def test_an_order_with_no_snapshot_is_refused(self):
        order = _order_with_lines([(10, 1)], discount="2.00", snapshot=False)

        with pytest.raises(ValueError, match="discount snapshots"):
            discounted_line_gross(order)

    def test_a_staff_added_line_carries_no_discount(self):
        from product.factories import ProductFactory

        order = _order_with_lines([(10, 1)], discount="2.00")
        added = order.items.create(
            product=ProductFactory(vat=None, stock=10),
            price=Money(D("5.00"), "EUR"),
            quantity=1,
        )

        allocation = discounted_line_gross(order)

        assert allocation[added.pk] == D("5.00")
        assert sum(allocation.values()) == D("13.00")


class TestHeal:
    """Orders created before the column, or by the release that was still
    serving the rollout, get the proportional rule their documents were
    issued under; an order that has a snapshot is left alone."""

    def test_an_unsnapshotted_order_gets_the_proportional_allocation(self):
        order = _order_with_lines(
            [(19.99, 1), (7.77, 2), (3.33, 3)],
            discount="6.00",
            loyalty="4.00",
            snapshot=False,
        )

        assert heal_discount_allocation(apps) == 1

        allocation = discounted_line_gross(order)
        assert sum(allocation.values()) == D("45.52") - D("10.00")

    def test_it_matches_the_order_wide_rule_checkout_writes(self):
        healed = _order_with_lines(
            [(19.99, 1), (7.77, 2)], discount="5.00", snapshot=False
        )
        live = _order_with_lines([(19.99, 1), (7.77, 2)], discount="5.00")
        heal_discount_allocation(apps)

        assert sorted(discounted_line_gross(healed).values()) == sorted(
            discounted_line_gross(live).values()
        )

    def test_running_twice_changes_nothing(self):
        order = _order_with_lines(
            [(10, 1), (5, 1)], discount="3.00", snapshot=False
        )
        heal_discount_allocation(apps)
        first = discounted_line_gross(order)

        assert heal_discount_allocation(apps) == 0

        assert discounted_line_gross(order) == first

    def test_a_snapshotted_order_is_never_rewritten(self):
        order = _order_with_lines(
            [(10, 1), (20, 1)], discount="4.00", snapshot=False
        )
        scoped = order.items.order_by("price").first()
        snapshot_discounts(order, {scoped.pk: D("4.00")})
        before = discounted_line_gross(order)

        assert heal_discount_allocation(apps) == 0

        assert discounted_line_gross(order) == before

    def test_an_order_without_a_discount_is_skipped(self):
        _order_with_lines([(10, 1)], snapshot=False)

        assert heal_discount_allocation(apps) == 0

    def test_it_runs_on_the_historical_models_of_the_migration(self):
        """The data migration hands it the registry of its own point in the
        graph, so a column added later cannot break it."""
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor

        order = _order_with_lines(
            [(10, 1), (5, 1)], discount="3.00", snapshot=False
        )
        state = MigrationExecutor(connection).loader.project_state(
            ("order", "0073_backfill_orderitem_discount_allocation")
        )

        assert heal_discount_allocation(state.apps) == 1

        assert sum(discounted_line_gross(order).values()) == D("12.00")

    def test_the_command_heals_inside_a_tenant_and_refuses_public(self, capsys):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from django_tenants.utils import schema_context

        order = _order_with_lines(
            [(10, 1), (5, 1)], discount="3.00", snapshot=False
        )

        with (
            schema_context("public"),
            pytest.raises(CommandError, match="public schema context"),
        ):
            call_command("heal_discount_allocation")
        with schema_context("heal_discount_allocation_tenant"):
            call_command("heal_discount_allocation")

        assert "Orders healed: 1" in capsys.readouterr().out
        assert sum(discounted_line_gross(order).values()) == D("12.00")
