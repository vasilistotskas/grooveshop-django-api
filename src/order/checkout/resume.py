"""A repeated checkout resumes its cart's unpaid order (#694).

A shopper who goes back and orders the same thing again from the same
cart would otherwise create a second unpaid online order: it holds the
same stock again and opens its own payment link, and both stay payable
until their holds end. The rule is that a cart has at most one live
unpaid online order, and the same checkout returns it.

"The same checkout" is :func:`checkout_fingerprint`: what the order is
made of (the quote's ``cart_digest``: lines, promotions, delivery), what
it costs (``amount_due``), how it is paid (the pay way, the loyalty
points and gift cards redeemed) and where and to whom it goes (every
field of the shipping address). Stored on the order at creation as
``metadata["checkout_fingerprint"]``, an HMAC: it reveals nothing of the
checkout.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from django.utils.crypto import salted_hmac

if TYPE_CHECKING:
    from cart.models import Cart
    from order.checkout.quote import CheckoutQuote
    from order.models.order import Order
    from pay_way.models import PayWay

# The HMAC key salt of :func:`checkout_fingerprint`: its own, so the
# fingerprint is no other HMAC the ``SECRET_KEY`` signs.
CHECKOUT_FINGERPRINT_SALT = "order.checkout.resume.checkout_fingerprint"
FINGERPRINT_KEY = "checkout_fingerprint"


def checkout_fingerprint(
    quote: CheckoutQuote,
    *,
    pay_way: PayWay,
    shipping_address: dict[str, Any],
    loyalty_points: int | None,
    gift_card_codes: list[str] | None,
) -> str:
    """One opaque value for a checkout: equal for the same checkout made
    again, different when anything the shopper chose changed."""
    canonical = json.dumps(
        {
            "cart": quote.cart_digest,
            "amount_due": str(quote.amount_due),
            "pay_way": pay_way.pk,
            "loyalty_points": loyalty_points or 0,
            "gift_cards": sorted(
                code.strip().upper() for code in gift_card_codes or []
            ),
            "address": {
                key: _plain(value) for key, value in shipping_address.items()
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return salted_hmac(
        CHECKOUT_FINGERPRINT_SALT, canonical, algorithm="sha256"
    ).hexdigest()


def unpaid_order_of_cart(cart: Cart, *, pay_way: PayWay) -> Order | None:
    """The cart's live unpaid online order with this pay way, when it can
    still be paid: it holds its stock for the payment, and a link the pay
    way's provider can open still fits in its payment window. ``None``
    otherwise. Whether it was made by the same checkout is for the caller
    to tell, by its ``metadata[FINGERPRINT_KEY]``.

    Read under the cart row lock both creation paths take, so a checkout
    sent twice at once is judged after the first one committed.
    """
    from order.enum.attribution import OrderSourceType
    from order.models.order import Order
    from order.payments import payment_hold
    from order.payments.payment import get_payment_provider_class

    provider = get_payment_provider_class(pay_way.provider_code)
    if provider is None:
        return None
    order = (
        Order.objects.holding_stock_for_online_payment()
        .filter(
            pay_way=pay_way,
            metadata__contains={"cart_snapshot": {"cart_uuid": str(cart.uuid)}},
            metadata__has_key=FINGERPRINT_KEY,
        )
        # An agent's order is the gateway's: a repeat of it is answered by
        # its agent checkout, a second one refused (#692), never handed to
        # another caller of the same cart.
        .exclude(attribution__source_type=OrderSourceType.AGENT)
        .order_by("-created_at")
        .first()
    )
    if order is None or payment_hold.window_closed(
        order, provider.min_hosted_session
    ):
        return None
    return order


def _plain(value: Any) -> Any:
    """A shipping address value as JSON: a related row by its key, a
    phone number or anything else by its text."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    pk = getattr(value, "pk", None)
    if pk is not None:
        return pk
    return str(value)
