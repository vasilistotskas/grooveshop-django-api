"""``Order.estimated_delivery``: fixed at creation from the chosen rate."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
from extra_settings.models import Setting

from country.factories import CountryFactory
from order.factories.order import OrderFactory
from order.serializers.order import OrderDetailSerializer
from order.services import OrderService
from shipping.models import ShippingProvider
from tests.utils.shipping import enable_rate

pytestmark = pytest.mark.django_db

MONDAY_10_ATHENS = datetime(2026, 10, 5, 7, 0, tzinfo=UTC)
MONDAY_1530_ATHENS = datetime(2026, 10, 5, 12, 30, tzinfo=UTC)


def _order_data(country, *, kind="home_delivery", code="flat_rate"):
    data = {
        "country_id": country.alpha_2,
        "shipping_provider_code": code,
        "shipping_kind": kind,
    }
    OrderService._resolve_shipping_provider(data, weight_grams=0)
    return data


def _seed(order_data, now):
    with patch("order.services.timezone.now", return_value=now):
        OrderService._seed_estimated_delivery(order_data)
    return order_data.get("estimated_delivery")


def _country_with_rate(**days):
    country = CountryFactory(num_regions=0)
    rate = enable_rate(country)
    for name, value in days.items():
        setattr(rate, name, value)
    rate.save()
    return country


def test_rate_without_estimate_leaves_the_order_estimate_null():
    country = _country_with_rate()
    assert _seed(_order_data(country), MONDAY_10_ATHENS) is None


def test_estimate_is_dispatch_plus_the_rates_max_days():
    country = _country_with_rate(delivery_days_min=1, delivery_days_max=3)
    assert _seed(_order_data(country), MONDAY_10_ATHENS) == date(2026, 10, 8)


def test_order_after_the_cutoff_slips_a_business_day():
    country = _country_with_rate(delivery_days_min=1, delivery_days_max=3)
    assert _seed(_order_data(country), MONDAY_1530_ATHENS) == date(2026, 10, 9)


def test_cutoff_comes_from_the_setting():
    country = _country_with_rate(delivery_days_min=1, delivery_days_max=3)
    Setting.objects.update_or_create(
        name="DISPATCH_CUTOFF",
        defaults={"value_type": Setting.TYPE_STRING, "value_string": "18:00"},
    )
    assert _seed(_order_data(country), MONDAY_1530_ATHENS) == date(2026, 10, 8)


def test_inactive_rate_is_not_used():
    country = _country_with_rate(delivery_days_min=1, delivery_days_max=3)
    country_rate = country.alpha_2
    from shipping.models import ShippingRate

    data = _order_data(country)
    ShippingRate.objects.filter(country_id=country_rate).update(is_active=False)
    assert _seed(data, MONDAY_10_ATHENS) is None


def test_unresolved_provider_leaves_it_unset():
    country = CountryFactory(num_regions=0)
    assert ShippingProvider.objects.filter(code="nope").count() == 0
    data = {"country_id": country.alpha_2, "shipping_kind": "pickup_point"}
    assert _seed(data, MONDAY_10_ATHENS) is None


def test_detail_serializer_reports_the_stored_date_as_iso():
    order = OrderFactory(estimated_delivery=date(2026, 10, 8))
    details = OrderDetailSerializer(order).data["tracking_details"]
    assert details["estimated_delivery"] == "2026-10-08"


def test_detail_serializer_reports_null_when_unset():
    order = OrderFactory(estimated_delivery=None)
    details = OrderDetailSerializer(order).data["tracking_details"]
    assert details["estimated_delivery"] is None


class TestSetAtCreation:
    """The seeding is wired into order creation, not just unit-callable."""

    def _create(self, *, days):
        from decimal import Decimal

        from djmoney.money import Money

        from cart.factories import CartFactory, CartItemFactory
        from pay_way.enum.settlement import PaySettlement
        from pay_way.factories import PayWayFactory
        from product.factories import ProductFactory

        country = _country_with_rate(**days)
        cart = CartFactory(is_guest=True)
        cart.items.all().delete()
        CartItemFactory(
            cart=cart,
            product=ProductFactory(
                stock=10,
                price=Money(Decimal("10.00"), "EUR"),
                discount_percent=Decimal(0),
                active=True,
            ),
            quantity=1,
        )
        pay_way = PayWayFactory(
            settlement=PaySettlement.COURIER_CASH,
            active=True,
            cost=Money(Decimal(0), "EUR"),
            free_threshold=Money(Decimal(0), "EUR"),
        )
        address = {
            "first_name": "Maria",
            "last_name": "Papadopoulou",
            "email": "maria@example.com",
            "street": "Ermou",
            "street_number": "1",
            "city": "Athens",
            "zipcode": "10563",
            "country_id": country.alpha_2,
            "phone": "+306900000000",
            "shipping_kind": "home_delivery",
        }
        with patch(
            "order.services.timezone.now", return_value=MONDAY_10_ATHENS
        ):
            return OrderService.create_order_from_cart_offline(
                cart=cart, shipping_address=address, pay_way=pay_way
            )

    def test_order_stores_the_estimate(self):
        order = self._create(
            days={"delivery_days_min": 1, "delivery_days_max": 3}
        )
        order.refresh_from_db()
        assert order.estimated_delivery == date(2026, 10, 8)

    def test_order_stores_null_when_the_rate_has_none(self):
        order = self._create(days={})
        order.refresh_from_db()
        assert order.estimated_delivery is None


def test_payment_first_creation_stores_the_estimate():
    """``create_order_from_cart`` (online) seeds it too, not only the
    offline path."""
    from decimal import Decimal

    from djmoney.money import Money

    from cart.factories import CartFactory, CartItemFactory
    from order.enum.status import PaymentStatus
    from pay_way.factories import PayWayFactory
    from product.factories import ProductFactory
    from user.factories import UserAccountFactory

    user = UserAccountFactory()
    cart = CartFactory(user=user)
    cart.items.all().delete()
    CartItemFactory(
        cart=cart,
        product=ProductFactory(
            stock=10,
            price=Money(Decimal("10.00"), "EUR"),
            discount_percent=Decimal(0),
            active=True,
        ),
        quantity=1,
    )
    country = _country_with_rate(delivery_days_min=1, delivery_days_max=3)

    with (
        patch("order.payment.get_payment_provider") as provider,
        patch("order.services.timezone.now", return_value=MONDAY_10_ATHENS),
    ):
        provider.return_value.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {"payment_id": "pi_test_123", "status": "succeeded"},
        )
        order = OrderService.create_order_from_cart(
            cart=cart,
            shipping_address={
                "first_name": "Maria",
                "last_name": "Papadopoulou",
                "email": "maria@example.com",
                "street": "Ermou",
                "street_number": "1",
                "city": "Athens",
                "zipcode": "10563",
                "country_id": country.alpha_2,
                "phone": "+306900000000",
                "shipping_kind": "home_delivery",
            },
            payment_intent_id="pi_test_123",
            pay_way=PayWayFactory(provider_code="stripe"),
            user=user,
        )

    order.refresh_from_db()
    assert order.estimated_delivery == date(2026, 10, 8)
