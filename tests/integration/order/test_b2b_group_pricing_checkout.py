"""Group pricing through the offline (COD) order-creation path — the
one with NO payment-amount guard. OrderItem snapshots, order totals and
the metadata audit must all carry the wholesale figures; guests and
non-approved users must be untouched."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

from b2b.factories import (
    BusinessProfileFactory,
    CustomerGroupFactory,
    PriceListItemFactory,
)
from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from order.services import OrderService
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def enable_wholesale():
    def _get(key, default=None):
        return {"B2B_WHOLESALE_ENABLED": True}.get(key, default)

    with patch("b2b.services.Setting.get", side_effect=_get):
        yield


def _shipping_address(country):
    return {
        "first_name": "Maria",
        "last_name": "Papadopoulou",
        "email": "maria@example.com",
        "street": "Ermou",
        "street_number": "1",
        "city": "Athens",
        "zipcode": "10563",
        "country_id": country.alpha_2,
        "phone": "+306900000000",
    }


def _cart_for(user, price="100.00", vat_rate=None, quantity=1):
    cart = CartFactory(user=user) if user else CartFactory(is_guest=True)
    cart.items.all().delete()
    product = ProductFactory(
        stock=10,
        price=Money(Decimal(price), "EUR"),
        discount_percent=Decimal("0"),
        vat=VatFactory(value=Decimal(str(vat_rate))) if vat_rate else None,
        active=True,
    )
    CartItemFactory(cart=cart, product=product, quantity=quantity)
    return cart, product


def _pay_way(**kwargs):
    defaults = {
        "is_online_payment": False,
        "cost": Money(Decimal("0"), "EUR"),
        "free_threshold": Money(Decimal("0"), "EUR"),
    }
    defaults.update(kwargs)
    return PayWayFactory(**defaults)


class TestGroupPricingCheckout:
    def test_order_item_snapshots_wholesale_price(self, enable_wholesale):
        group = CustomerGroupFactory(discount_percent=Decimal("10"))
        profile = BusinessProfileFactory(approved=True, customer_group=group)
        cart, product = _cart_for(profile.user, "100.00", vat_rate="24")
        country = CountryFactory()

        order = OrderService.create_order_from_cart_offline(
            cart=cart,
            shipping_address=_shipping_address(country),
            pay_way=_pay_way(),
            user=profile.user,
        )

        item = order.items.get()
        assert item.price.amount == Decimal("111.60")  # 90 net × 1.24
        assert item.price.amount != product.final_price.amount
        assert order.metadata["b2b_pricing"]["group_name"] == group.name
        assert order.metadata["b2b_pricing"]["discount_percent"] == "10.00"

    def test_fixed_override_reaches_order_item(self, enable_wholesale):
        group = CustomerGroupFactory(discount_percent=Decimal("0"))
        profile = BusinessProfileFactory(approved=True, customer_group=group)
        cart, product = _cart_for(profile.user, "100.00")
        PriceListItemFactory(
            group=group, product=product, net_price=Money("60.00", "EUR")
        )
        country = CountryFactory()

        order = OrderService.create_order_from_cart_offline(
            cart=cart,
            shipping_address=_shipping_address(country),
            pay_way=_pay_way(),
            user=profile.user,
        )

        assert order.items.get().price.amount == Decimal("60.00")

    def test_payment_fee_threshold_uses_wholesale_total(self, enable_wholesale):
        # Fee 5 EUR waived from 95 EUR: retail total (100) clears the
        # threshold but the wholesale total (90) must NOT.
        group = CustomerGroupFactory(discount_percent=Decimal("10"))
        profile = BusinessProfileFactory(approved=True, customer_group=group)
        cart, _product = _cart_for(profile.user, "100.00")
        country = CountryFactory()

        order = OrderService.create_order_from_cart_offline(
            cart=cart,
            shipping_address=_shipping_address(country),
            pay_way=_pay_way(
                cost=Money(Decimal("5.00"), "EUR"),
                free_threshold=Money(Decimal("95.00"), "EUR"),
            ),
            user=profile.user,
        )

        assert order.payment_method_fee.amount == Decimal("5.00")
        assert order.items.get().price.amount == Decimal("90.00")

    def test_guest_checkout_stays_retail(self, enable_wholesale):
        cart, product = _cart_for(None, "100.00", vat_rate="24")
        country = CountryFactory()

        order = OrderService.create_order_from_cart_offline(
            cart=cart,
            shipping_address=_shipping_address(country),
            pay_way=_pay_way(),
            user=None,
        )

        assert order.items.get().price.amount == product.final_price.amount
        assert "b2b_pricing" not in order.metadata

    def test_pending_profile_stays_retail(self, enable_wholesale):
        profile = BusinessProfileFactory(
            customer_group=CustomerGroupFactory(discount_percent=Decimal("10"))
        )  # PENDING
        cart, product = _cart_for(profile.user, "100.00")
        country = CountryFactory()

        order = OrderService.create_order_from_cart_offline(
            cart=cart,
            shipping_address=_shipping_address(country),
            pay_way=_pay_way(),
            user=profile.user,
        )

        assert order.items.get().price.amount == product.final_price.amount
        assert "b2b_pricing" not in order.metadata

    def test_below_group_minimum_is_refused(self, enable_wholesale):
        from djmoney.money import Money as M

        from order.exceptions import InvalidOrderDataError

        group = CustomerGroupFactory(
            discount_percent=Decimal("10"),
            min_order_value=M("500.00", "EUR"),
        )
        profile = BusinessProfileFactory(approved=True, customer_group=group)
        cart, _product = _cart_for(profile.user, "100.00")
        country = CountryFactory()

        # Match the amount, not the wording — the message is localized
        # (Greek when the compiled .mo is present, English in CI).
        with pytest.raises(InvalidOrderDataError, match="500"):
            OrderService.create_order_from_cart_offline(
                cart=cart,
                shipping_address=_shipping_address(country),
                pay_way=_pay_way(),
                user=profile.user,
            )

    def test_feature_off_stays_retail(self):
        group = CustomerGroupFactory(discount_percent=Decimal("10"))
        profile = BusinessProfileFactory(approved=True, customer_group=group)
        cart, product = _cart_for(profile.user, "100.00")
        country = CountryFactory()

        order = OrderService.create_order_from_cart_offline(
            cart=cart,
            shipping_address=_shipping_address(country),
            pay_way=_pay_way(),
            user=profile.user,
        )

        assert order.items.get().price.amount == product.final_price.amount
