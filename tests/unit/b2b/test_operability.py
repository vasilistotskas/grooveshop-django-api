"""Merchant-operability pieces: minimum order value + bulk price import."""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from b2b.factories import CustomerGroupFactory, PriceListItemFactory
from b2b.models import PriceListItem
from b2b.services import B2BPricingContext, B2BPricingService, B2BService
from cart.factories import CartFactory, CartItemFactory
from product.factories import ProductFactory

pytestmark = pytest.mark.django_db


def _bound_cart(price="100.00", quantity=1, **group_kwargs):
    group = CustomerGroupFactory(discount_percent=Decimal(10), **group_kwargs)
    cart = CartFactory(is_guest=True)
    cart.items.all().delete()
    product = ProductFactory(
        price=Money(Decimal(price), "EUR"),
        discount_percent=Decimal(0),
        vat=None,
        stock=100,
        active=True,
    )
    CartItemFactory(cart=cart, product=product, quantity=quantity)
    cart._b2b_pricing = B2BPricingContext(
        group=group,
        prices=B2BPricingService.resolve_map([product], group),
    )
    return cart, group


class TestMinOrderValue:
    def test_unmet_below_minimum(self):
        cart, group = _bound_cart(
            "100.00", min_order_value=Money("500.00", "EUR")
        )

        unmet = B2BPricingService.min_order_value_unmet(cart)

        assert unmet is not None
        assert unmet.amount == Decimal("500.00")

    def test_met_at_or_above_minimum(self):
        # 6 × 90.00 = 540.00 ≥ 500.00
        cart, _group = _bound_cart(
            "100.00", quantity=6, min_order_value=Money("500.00", "EUR")
        )

        assert B2BPricingService.min_order_value_unmet(cart) is None

    def test_zero_minimum_disables_the_check(self):
        cart, _group = _bound_cart("1.00")

        assert B2BPricingService.min_order_value_unmet(cart) is None

    def test_retail_cart_never_gated(self):
        cart = CartFactory(is_guest=True)

        assert B2BPricingService.min_order_value_unmet(cart) is None


class TestImportPriceLines:
    def test_creates_updates_and_reports_errors(self):
        group = CustomerGroupFactory()
        existing_product = ProductFactory(sku="SKU-EXISTING", stock=1)
        new_product = ProductFactory(sku="SKU-NEW", stock=1)
        PriceListItemFactory(
            group=group,
            product=existing_product,
            net_price=Money("99.00", "EUR"),
        )

        summary = B2BService.import_price_lines(
            group,
            "\n".join(
                [
                    "SKU-EXISTING;12.50",
                    "SKU-NEW,7,90",  # comma separator + decimal comma
                    "",  # blank lines skipped
                    "SKU-MISSING;5.00",
                    "SKU-NEW;not-a-price",
                    "garbage-line",
                ]
            ),
        )

        assert summary["created"] == 1
        assert summary["updated"] == 1
        assert len(summary["errors"]) == 3
        assert PriceListItem.objects.get(
            group=group, product=existing_product
        ).net_price == Money("12.50", "EUR")
        assert PriceListItem.objects.get(
            group=group, product=new_product
        ).net_price == Money("7.90", "EUR")

    def test_negative_price_rejected(self):
        group = CustomerGroupFactory()
        ProductFactory(sku="SKU-NEG", stock=1)

        summary = B2BService.import_price_lines(group, "SKU-NEG;-5.00")

        assert summary["created"] == 0
        assert len(summary["errors"]) == 1
        assert not PriceListItem.objects.filter(group=group).exists()
