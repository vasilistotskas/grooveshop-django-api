"""B2BPricingService arithmetic.

The resolver is the single money authority for wholesale prices — the
cart binding, both order-create paths and the /b2b/prices endpoint all
read it, so these tests pin the rules: fixed override beats percent,
retail markdown never stacks, the retail final price floors the result,
VAT applies to the B2B net, everything rounds 2dp HALF-UP.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from b2b.factories import CustomerGroupFactory, PriceListItemFactory
from b2b.services import B2BPricingService
from product.factories import ProductFactory
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db


def _product(price="100.00", discount="0", vat_rate=None, **kwargs):
    vat = VatFactory(value=Decimal(str(vat_rate))) if vat_rate else None
    return ProductFactory(
        price=Money(Decimal(price), "EUR"),
        discount_percent=Decimal(discount),
        vat=vat,
        stock=100,
        active=True,
        **kwargs,
    )


class TestPercentPricing:
    def test_percent_off_net_with_vat(self):
        product = _product("100.00", vat_rate=24)
        group = CustomerGroupFactory(discount_percent=Decimal("10"))

        resolved = B2BPricingService.resolve(product, group)

        assert resolved.net.amount == Decimal("90.00")
        assert resolved.final.amount == Decimal("111.60")

    def test_zero_percent_group_matches_retail(self):
        product = _product("100.00", vat_rate=24)
        group = CustomerGroupFactory(discount_percent=Decimal("0"))

        resolved = B2BPricingService.resolve(product, group)

        assert resolved.final.amount == product.final_price.amount

    def test_no_vat_final_equals_net(self):
        product = _product("50.00")
        group = CustomerGroupFactory(discount_percent=Decimal("20"))

        resolved = B2BPricingService.resolve(product, group)

        assert resolved.net.amount == Decimal("40.00")
        assert resolved.final.amount == Decimal("40.00")

    def test_quantization_half_up(self):
        # 16.12 × 75% = 12.09; × 1.24 = 14.9916 → 14.99
        product = _product("16.12", vat_rate=24)
        group = CustomerGroupFactory(discount_percent=Decimal("25"))

        resolved = B2BPricingService.resolve(product, group)

        assert resolved.net.amount == Decimal("12.09")
        assert resolved.final.amount == Decimal("14.99")


class TestFixedOverride:
    def test_override_wins_over_percent(self):
        product = _product("100.00", vat_rate=24)
        group = CustomerGroupFactory(discount_percent=Decimal("10"))
        PriceListItemFactory(
            group=group, product=product, net_price=Money("80.00", "EUR")
        )

        resolved = B2BPricingService.resolve_map([product], group)[product.pk]

        assert resolved.net.amount == Decimal("80.00")
        assert resolved.final.amount == Decimal("99.20")

    def test_resolve_map_mixes_overrides_and_percent(self):
        with_override = _product("100.00")
        without_override = _product("100.00")
        group = CustomerGroupFactory(discount_percent=Decimal("10"))
        PriceListItemFactory(
            group=group,
            product=with_override,
            net_price=Money("70.00", "EUR"),
        )

        prices = B2BPricingService.resolve_map(
            [with_override, without_override], group
        )

        assert prices[with_override.pk].net.amount == Decimal("70.00")
        assert prices[without_override.pk].net.amount == Decimal("90.00")


class TestNoStackingAndFloor:
    def test_retail_markdown_not_stacked(self):
        # Product already 20% off retail; the group's 10% applies to the
        # UNDISCOUNTED net (100 → 90, would be 111.60 gross) — never
        # 10% on top of the markdown. The FLOOR then kicks in: retail
        # final is 100+24-20 = 104.00, cheaper than the naive 111.60,
        # so the buyer gets the better one.
        product = _product("100.00", discount="20", vat_rate=24)
        group = CustomerGroupFactory(discount_percent=Decimal("10"))

        resolved = B2BPricingService.resolve(product, group)

        assert resolved.final.amount == product.final_price.amount
        assert resolved.final.amount == Decimal("104.00")
        # Net is re-derived from the floored gross.
        assert resolved.net.amount == Decimal("83.87")

    def test_deep_retail_sale_floors_fixed_override(self):
        product = _product("100.00", discount="50", vat_rate=24)
        group = CustomerGroupFactory(discount_percent=Decimal("0"))
        PriceListItemFactory(
            group=group, product=product, net_price=Money("90.00", "EUR")
        )

        resolved = B2BPricingService.resolve_map([product], group)[product.pk]

        # Retail final = 100 + 24 - 50 = 74.00 < override 90×1.24.
        assert resolved.final.amount == Decimal("74.00")

    def test_wholesale_below_sale_price_wins(self):
        product = _product("100.00", discount="10", vat_rate=24)
        group = CustomerGroupFactory(discount_percent=Decimal("40"))

        resolved = B2BPricingService.resolve(product, group)

        # 60 × 1.24 = 74.40 < retail final 114.00 — no floor.
        assert resolved.net.amount == Decimal("60.00")
        assert resolved.final.amount == Decimal("74.40")
