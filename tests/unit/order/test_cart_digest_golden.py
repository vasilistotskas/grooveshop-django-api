"""The cart digest's canonical form, pinned.

An order is refused ``cart_changed`` when the digest it carries is not
the one its cart gives now, so a canonical form that changes between two
releases refuses every checkout quoted before the deploy. The golden
value below moves only when that is meant: a change to the form fails
here first.
"""

from __future__ import annotations

from decimal import Decimal

from django.test import override_settings
from djmoney.money import Money

from order.checkout.quote import OrderQuoteService, QuoteLine, ShippingChoice
from product.models.product import Product
from promotion.engine.results import CartDiscountResult

GOLDEN_SECRET_KEY = "cart-digest-golden-key"
GOLDEN_DIGEST = (
    "381db96cafa0040c73d400cc8dc46958663e14a2daba0d0aa47b28e4850c5f44"
)

DELIVERY = ShippingChoice(
    kind="pickup_point",
    provider_code="boxnow",
    country_id="GR",
    region_id="I",
    location=(("boxnow_locker_id", "25"),),
)


def _line(item_id: int, product_id: int, quantity: int, unit: str):
    return QuoteLine(
        item_id=item_id,
        product=Product(id=product_id),
        quantity=quantity,
        unit_price=Money(Decimal(unit), "EUR"),
        promotion_discount=Decimal(0),
    )


LINES = (_line(11, 7, 2, "19.90"), _line(12, 9, 1, "5.00"))


def _digest(lines) -> str:
    return OrderQuoteService.cart_digest(
        lines, CartDiscountResult(), shipping=DELIVERY, weight_grams=1200
    )


@override_settings(SECRET_KEY=GOLDEN_SECRET_KEY)
def test_the_canonical_form_is_pinned():
    assert _digest(LINES) == GOLDEN_DIGEST


@override_settings(SECRET_KEY=GOLDEN_SECRET_KEY)
def test_the_order_of_the_lines_does_not_matter():
    assert _digest(tuple(reversed(LINES))) == GOLDEN_DIGEST


@override_settings(SECRET_KEY=GOLDEN_SECRET_KEY)
def test_a_line_left_out_of_the_quote_changes_it():
    assert _digest(LINES[:1]) != GOLDEN_DIGEST
