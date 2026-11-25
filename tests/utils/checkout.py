"""The total an order is created at, asked for the way a client asks.

Order creation requires ``expectedTotal``: the amount due the client
showed the buyer, as ``POST /cart/quote`` returned it. A test that
creates an order quotes it first, with the same inputs, and sends that
total back, as the storefront and the agent gateway do. A test of the
refusal (``total_changed``) sends another total on purpose.
"""

from __future__ import annotations

from typing import Any

from django.urls import reverse

from b2b.services import B2BPricingService
from order.checkout.quote import (
    ExpectedTotal,
    OrderQuoteService,
    ShippingChoice,
)
from shipping.enum import ShippingKind

# The total sent with an order its quote refuses too (a pay way not on
# offer, a gift card guess, points the order cannot take, a body the
# serializer refuses): there is no quoted total to send, and creation
# refuses the order before it compares one.
UNQUOTED = {"amount": 0, "currency": "EUR"}


def quoted_total(
    cart,
    shipping_address: dict[str, Any],
    *,
    pay_way,
    user=None,
    loyalty_points_to_redeem: int | None = None,
    gift_card_codes: list[str] | None = None,
) -> ExpectedTotal:
    """The ``expected_total`` for ``OrderService.create_order_from_cart``
    called with these arguments: the quote of the same order, priced as
    creation prices it (the cart's wholesale prices bound first)."""
    B2BPricingService.bind_cart(cart, user)
    quote = OrderQuoteService.quote(
        cart,
        user=user,
        email=shipping_address.get("email") or "",
        pay_way=pay_way,
        shipping=ShippingChoice(
            kind=shipping_address.get("shipping_kind"),
            provider_code=shipping_address.get("shipping_provider_code"),
            country_id=shipping_address.get("country_id"),
            region_id=shipping_address.get("region_id"),
        ),
        loyalty_points=loyalty_points_to_redeem,
        gift_card_codes=gift_card_codes,
    )
    assert quote.expected_total is not None, "the order is not priced"
    return quote.expected_total


def quoted_body(client, body: dict[str, Any], **request) -> dict[str, Any]:
    """``body`` for ``POST /api/v1/order`` with the ``expectedTotal`` its
    quote returns: ``body`` posted to ``POST /cart/quote`` by the same
    client with the same ``request`` arguments (headers, ``format``).

    Order creation takes a body without a delivery kind as a home
    delivery (``OrderQuoteService.delivery_kind``) while the quote prices
    no delivery without one, so the quote is asked with that kind."""
    request.setdefault("format", "json")
    has_kind = "shippingKind" in body or "shipping_kind" in body
    quoted = {
        **body,
        **({} if has_kind else {"shippingKind": ShippingKind.HOME_DELIVERY}),
    }
    response = client.post(reverse("cart-quote"), quoted, **request)
    assert response.status_code == 200, response.json()
    expected = response.json()["expectedTotal"]
    assert expected is not None, "the order is not priced"
    return {**body, "expectedTotal": expected}


def priced(**creation) -> dict[str, Any]:
    """The arguments of ``OrderService.create_order_from_cart`` with the
    ``expected_total`` a client would send for them (``quoted_total``),
    unless they name one of their own."""
    if "expected_total" in creation:
        return creation
    return {
        **creation,
        "expected_total": quoted_total(
            creation["cart"],
            creation["shipping_address"],
            pay_way=creation["pay_way"],
            user=creation.get("user"),
            loyalty_points_to_redeem=creation.get("loyalty_points_to_redeem"),
            gift_card_codes=creation.get("gift_card_codes"),
        ),
    }
