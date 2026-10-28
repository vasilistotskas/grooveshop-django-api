"""``OrderService.free_shipping_progress``: ONE distance to free shipping.

Staging webside, 2026-10-28, a cart of one 19,99 € item: the promotion
teaser said "add 19,01 € to unlock «free shipping from 39 €»" while the
store's bar said "add 30,01 € for free shipping" (the carrier's 50 €
threshold). Both were read from different places; checkout honours the
cheaper one. The progress is now decided by the rules checkout prices
shipping with (``OrderService.shipping_cost``): a free-shipping promotion
waives every carrier, a carrier's threshold zeroes that carrier, and the
cart is told the cheapest route that holds.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar
from unittest.mock import patch

import pytest
from django.urls import reverse
from djmoney.money import Money
from measurement.measures import Weight
from rest_framework.test import APIClient

from b2b.factories import CustomerGroupFactory
from b2b.services import B2BPricingContext, B2BPricingService
from cart.factories import CartFactory, CartItemFactory
from cart.serializers.cart import CartSerializer
from country.factories import CountryFactory
from country.models import Country
from order.services import FreeShippingProgress, OrderService
from product.factories import ProductCategoryFactory, ProductFactory
from promotion.enum import BenefitType, PromotionTrigger, TargetScope
from promotion.factories import PromotionCodeFactory, PromotionFactory
from promotion.models import CartPromotionCode
from promotion.services import PromotionEngine
from shipping.enum import FreeShippingSource, ShippingKind
from shipping.interfaces import (
    _REGISTRY,
    ShippingCarrierInterface,
    register_provider,
)
from shipping.models import ShippingProvider
from tests.utils.shipping import enable_rate

pytestmark = pytest.mark.django_db


class _SecondCarrier(ShippingCarrierInterface):
    """A second carrier, so two thresholds can compete in one country."""

    code: ClassVar[str] = "second_free_shipping_test"

    def create_shipment(self, order, *, kind, payload=None):
        return None

    def cancel_shipment(self, shipment, *, reason="") -> None:
        return None

    def fetch_label_bytes(self, shipment) -> bytes:
        return b""

    def fetch_tracking_events(self, shipment) -> list[dict[str, Any]]:
        return []

    def shipment_for_order(self, order):
        return None

    def serialize_shipment(self, shipment, *, context):
        return None

    def validate_order_payload(self, *, kind, payload) -> dict[str, list[str]]:
        return {}


@pytest.fixture
def second_carrier():
    register_provider(_SecondCarrier)
    yield _SecondCarrier.code
    _REGISTRY.pop(_SecondCarrier.code, None)
    ShippingProvider.objects.filter(code=_SecondCarrier.code).delete()


@pytest.fixture
def settings_on():
    """Promotions on; wholesale carts take no retail promotion."""
    values = {"PROMOTIONS_ENABLED": True, "B2B_ALLOW_PROMOTIONS": False}
    with patch(
        "extra_settings.models.Setting.get",
        side_effect=lambda key, default=None: values.get(key, default),
    ):
        yield values


@pytest.fixture
def country():
    """The test's country, with the carrier's 50 € threshold."""
    country = CountryFactory()
    enable_rate(country, free_shipping_threshold=Decimal("50.00"))
    return country


def _cart(*lines: tuple[str, int], category=None):
    cart = CartFactory(is_guest=True)
    cart.items.all().delete()
    for price, quantity in lines:
        CartItemFactory(
            cart=cart,
            product=ProductFactory(
                price=Money(Decimal(price), "EUR"),
                discount_percent=Decimal(0),
                vat=None,
                stock=100,
                active=True,
                weight=Weight(kg=1),
                category=category,
                num_images=0,
                num_reviews=0,
            ),
            quantity=quantity,
        )
    return cart


def _free_shipping_promotion(**fields):
    return PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.FREE_SHIPPING,
        benefit_value=Decimal(0),
        **fields,
    )


def _progress(cart, country) -> FreeShippingProgress | None:
    return OrderService.free_shipping_progress(
        cart,
        PromotionEngine.evaluate(cart),
        country_code=country.alpha_2,
    )


def test_a_threshold_alone_is_the_distance_to_it(settings_on, country):
    progress = _progress(_cart(("19.99", 1)), country)

    assert progress == FreeShippingProgress(
        source=FreeShippingSource.THRESHOLD,
        country_code=country.alpha_2,
        remaining_amount=Decimal("30.01"),
        threshold=Decimal("50.00"),
    )


def test_a_cheaper_free_shipping_promotion_wins(settings_on, country):
    """The 2026-10-28 webside cart: 39 € promotion vs a 50 € carrier."""
    _free_shipping_promotion(min_subtotal=Money(Decimal(39), "EUR"))

    progress = _progress(_cart(("19.99", 1)), country)

    assert progress == FreeShippingProgress(
        source=FreeShippingSource.PROMOTION,
        country_code=country.alpha_2,
        remaining_amount=Decimal("19.01"),
        threshold=Decimal("39.00"),
    )


def test_a_dearer_promotion_loses_to_the_threshold(settings_on, country):
    _free_shipping_promotion(min_subtotal=Money(Decimal(60), "EUR"))

    progress = _progress(_cart(("19.99", 1)), country)

    assert progress is not None
    assert progress.source == FreeShippingSource.THRESHOLD
    assert progress.remaining_amount == Decimal("30.01")


def test_a_promotion_out_of_the_carts_categories_is_no_route(
    settings_on, country
):
    promotion = _free_shipping_promotion(
        min_subtotal=Money(Decimal(39), "EUR"),
        target_scope=TargetScope.CATEGORIES,
    )
    promotion.categories.add(ProductCategoryFactory())

    progress = _progress(
        _cart(("19.99", 1), category=ProductCategoryFactory()), country
    )

    assert progress is not None
    assert progress.source == FreeShippingSource.THRESHOLD
    assert progress.remaining_amount == Decimal("30.01")


def test_a_promotion_in_the_carts_category_is_a_route(settings_on, country):
    category = ProductCategoryFactory()
    promotion = _free_shipping_promotion(
        min_subtotal=Money(Decimal(39), "EUR"),
        target_scope=TargetScope.CATEGORIES,
    )
    promotion.categories.add(category)

    progress = _progress(_cart(("19.99", 1), category=category), country)

    assert progress is not None
    assert progress.source == FreeShippingSource.PROMOTION
    assert progress.remaining_amount == Decimal("19.01")


def test_a_first_order_promotion_is_no_route_for_an_anonymous_cart(
    settings_on, country
):
    """Checkout cannot grant it yet, so the cart must not promise it."""
    _free_shipping_promotion(
        min_subtotal=Money(Decimal(39), "EUR"), first_order_only=True
    )

    progress = _progress(_cart(("19.99", 1)), country)

    assert progress is not None
    assert progress.source == FreeShippingSource.THRESHOLD


def test_the_thresholds_are_the_countrys_own(settings_on, country):
    other = CountryFactory()
    enable_rate(other, free_shipping_threshold=Decimal("80.00"))
    cart = _cart(("19.99", 1))

    assert _progress(cart, country).remaining_amount == Decimal("30.01")
    assert _progress(cart, other).remaining_amount == Decimal("60.01")


def test_without_a_country_the_first_shipping_country_is_judged(
    settings_on, country
):
    Country.objects.filter(pk=country.pk).update(sort_order=-1)
    cart = _cart(("19.99", 1))

    progress = OrderService.free_shipping_progress(
        cart, PromotionEngine.evaluate(cart)
    )

    assert progress is not None
    assert progress.country_code == country.alpha_2
    assert progress.remaining_amount == Decimal("30.01")


def test_a_promotion_route_holds_in_a_country_without_a_threshold(
    settings_on,
):
    country = CountryFactory()
    enable_rate(country, free_shipping_threshold=None)
    _free_shipping_promotion(min_subtotal=Money(Decimal(39), "EUR"))

    progress = _progress(_cart(("19.99", 1)), country)

    assert progress is not None
    assert progress.source == FreeShippingSource.PROMOTION
    assert progress.remaining_amount == Decimal("19.01")


@pytest.mark.parametrize(
    ("quantity", "remaining", "threshold"),
    [
        # 2 kg: the capped carrier takes it, and its 30 € is closer.
        (2, Decimal("22.00"), Decimal("30.00")),
        # 3 kg: over its 2 kg cap, so only the 50 € carrier is a route.
        (3, Decimal("38.00"), Decimal("50.00")),
    ],
)
def test_only_a_carrier_that_can_carry_the_cart_counts(
    settings_on, country, second_carrier, quantity, remaining, threshold
):
    """A threshold on a carrier capped below the cart's weight is not a
    route: checkout refuses that carrier for this parcel."""
    enable_rate(
        country,
        provider_code=second_carrier,
        free_shipping_threshold=Decimal("30.00"),
        max_weight_grams=2000,
    )

    progress = _progress(_cart(("4.00", quantity)), country)

    assert progress is not None
    assert progress.remaining_amount == remaining
    assert progress.threshold == threshold


def test_a_carrier_that_does_not_offer_the_kind_is_no_route(
    settings_on, country, second_carrier
):
    enable_rate(
        country,
        provider_code=second_carrier,
        kind=ShippingKind.HOME_DELIVERY,
        free_shipping_threshold=Decimal("30.00"),
    )
    ShippingProvider.objects.filter(code=second_carrier).update(
        supports_home_delivery=False
    )

    progress = _progress(_cart(("19.99", 1)), country)

    assert progress is not None
    assert progress.threshold == Decimal("50.00")


def test_the_threshold_is_judged_after_discounts(settings_on, country):
    PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.FIXED_AMOUNT,
        benefit_value=Decimal(6),
    )

    progress = _progress(_cart(("45.00", 1)), country)

    assert progress is not None
    assert progress.remaining_amount == Decimal("11.00")


def test_a_units_promotion_is_weighed_by_what_it_costs(settings_on, country):
    """Two more 5 € units (10 €) beat 40 € more for the carrier."""
    _free_shipping_promotion(min_quantity=3)

    progress = _progress(_cart(("5.00", 1)), country)

    assert progress == FreeShippingProgress(
        source=FreeShippingSource.PROMOTION,
        country_code=country.alpha_2,
        remaining_quantity=2,
    )


def test_a_units_promotion_dearer_than_the_threshold_loses(
    settings_on, country
):
    _free_shipping_promotion(min_quantity=3)

    progress = _progress(_cart(("40.00", 1)), country)

    assert progress is not None
    assert progress.source == FreeShippingSource.THRESHOLD
    assert progress.remaining_amount == Decimal("10.00")


def test_a_waiving_promotion_makes_it_free(settings_on, country):
    _free_shipping_promotion(min_subtotal=Money(Decimal(39), "EUR"))

    progress = _progress(_cart(("39.00", 1)), country)

    assert progress == FreeShippingProgress(
        source=FreeShippingSource.PROMOTION,
        country_code=country.alpha_2,
        free=True,
    )


def test_a_free_shipping_code_makes_it_free(settings_on, country):
    cart = _cart(("10.00", 1))
    code = PromotionCodeFactory(
        promotion=PromotionFactory(
            trigger=PromotionTrigger.CODE,
            benefit_type=BenefitType.FREE_SHIPPING,
            benefit_value=Decimal(0),
        )
    )
    CartPromotionCode.objects.create(cart=cart, code=code)

    progress = _progress(cart, country)

    assert progress is not None
    assert progress.free is True
    assert progress.source == FreeShippingSource.PROMOTION


def test_reaching_the_threshold_makes_it_free(settings_on, country):
    progress = _progress(_cart(("50.00", 1)), country)

    assert progress == FreeShippingProgress(
        source=FreeShippingSource.THRESHOLD,
        country_code=country.alpha_2,
        free=True,
        threshold=Decimal("50.00"),
    )


def test_no_route_at_all_is_none(settings_on):
    country = CountryFactory()
    enable_rate(country, free_shipping_threshold=None)

    assert _progress(_cart(("19.99", 1)), country) is None


def test_a_wholesale_cart_has_only_the_threshold_on_its_own_prices(
    settings_on, country
):
    """No retail promotion reaches a B2B cart (B2B_ALLOW_PROMOTIONS off),
    so a free-shipping offer is no route; the carrier's threshold is
    judged on the wholesale prices the cart is charged."""
    _free_shipping_promotion(min_subtotal=Money(Decimal(30), "EUR"))
    cart = _cart(("40.00", 1))
    cart._b2b_pricing = B2BPricingContext(
        group=(group := CustomerGroupFactory(discount_percent=Decimal(10))),
        prices=B2BPricingService.resolve_map(
            [item.product for item in cart.items.all()], group
        ),
    )

    progress = _progress(cart, country)

    assert progress == FreeShippingProgress(
        source=FreeShippingSource.THRESHOLD,
        country_code=country.alpha_2,
        remaining_amount=Decimal("14.00"),
        threshold=Decimal("50.00"),
    )


def test_a_wholesale_cart_that_may_take_promotions_gets_the_promotion(
    settings_on, country
):
    settings_on["B2B_ALLOW_PROMOTIONS"] = True
    _free_shipping_promotion(min_subtotal=Money(Decimal(39), "EUR"))
    cart = _cart(("40.00", 1))
    cart._b2b_pricing = B2BPricingContext(
        group=(group := CustomerGroupFactory(discount_percent=Decimal(10))),
        prices=B2BPricingService.resolve_map(
            [item.product for item in cart.items.all()], group
        ),
    )

    progress = _progress(cart, country)

    assert progress is not None
    assert progress.source == FreeShippingSource.PROMOTION
    assert progress.remaining_amount == Decimal("3.00")


def test_the_cart_payload_carries_it_and_flags_the_promotion_teaser(
    settings_on, country
):
    Country.objects.filter(pk=country.pk).update(sort_order=-1)
    promotion = _free_shipping_promotion(min_subtotal=Money(Decimal(39), "EUR"))
    other = PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        benefit_type=BenefitType.FIXED_AMOUNT,
        benefit_value=Decimal(5),
        min_subtotal=Money(Decimal(30), "EUR"),
    )
    cart = _cart(("19.99", 1))

    data = CartSerializer(cart).data

    assert data["free_shipping_progress"] == {
        "free": False,
        "source": "promotion",
        "remainingAmount": Decimal("19.01"),
        "remainingQuantity": None,
        "threshold": Decimal("39.00"),
        "countryCode": country.alpha_2,
    }
    flags = {
        entry["promotionId"]: entry["freeShipping"]
        for entry in data["promotion_near_miss"]
    }
    assert flags == {promotion.id: True, other.id: False}


def test_the_cart_api_answers_in_camel_case(settings_on, country):
    Country.objects.filter(pk=country.pk).update(sort_order=-1)
    cart = _cart(("19.99", 1))
    client = APIClient()

    response = client.get(
        reverse("cart-detail"), headers={"X-Cart-Id": str(cart.uuid)}
    )

    assert response.status_code == 200, response.content
    assert response.json()["freeShippingProgress"] == {
        "free": False,
        "source": "threshold",
        "remainingAmount": 30.01,
        "remainingQuantity": None,
        "threshold": 50.0,
        "countryCode": country.alpha_2,
    }
