"""A repeated checkout resumes its cart's unpaid order (#694).

A shopper who goes back and orders the same thing again from the same
cart would otherwise create a second unpaid online order: it holds the
same stock again and opens its own payment link, and both stay payable
until their holds end. The same checkout returns the order it made.

"The same checkout" is :func:`checkout_fingerprint`: what the order is
made of (the quote's ``cart_digest``: lines, promotions, delivery), what
it costs (``amount_due``), how it is paid (the pay way, the loyalty
points and gift cards redeemed) and where and to whom it goes (every
field of the shipping address). Stored on the order at creation as
``metadata["checkout_fingerprint"]``, with :func:`address_fingerprint`
beside it. Both are HMACs: they reveal nothing of the checkout.

A checkout that spent something is not the same the second time, since the
order spent it: a drained gift card is refused, a single-use coupon is gone
from the cart (another total), and points are there only with a balance that
covers them twice.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from django.db.models import Sum
from django.utils import timezone
from django.utils.crypto import salted_hmac

if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.db.models import QuerySet

    from cart.models import Cart
    from order.checkout.quote import CheckoutQuote
    from order.models.order import Order
    from pay_way.models import PayWay

# The HMAC key salt of :func:`checkout_fingerprint`: its own, so the
# fingerprint is no other HMAC the ``SECRET_KEY`` signs.
CHECKOUT_FINGERPRINT_SALT = "order.checkout.resume.checkout_fingerprint"
FINGERPRINT_KEY = "checkout_fingerprint"

#: What :func:`address_fingerprint` covers beside ``Order.PERSONAL_FIELDS``.
ADDRESS_COLUMNS = ("country_id", "region_id", "document_type")


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
    return _hmac(
        {
            "cart": quote.cart_digest,
            # Never ``str(Money)``: it is written in the active language.
            "amount_due": [
                str(quote.amount_due.amount),
                str(quote.amount_due.currency),
            ],
            "pay_way": pay_way.pk,
            "loyalty_points": loyalty_points or 0,
            "gift_cards": sorted(
                code.strip().upper() for code in gift_card_codes or []
            ),
            "address": {
                key: _plain(value) for key, value in shipping_address.items()
            },
        }
    )


def address_fingerprint(order: Order) -> str:
    """The address an order ships to, as its row holds it.

    A pending order's contact and address can be edited (by its owner, by
    staff), and then the checkout that made it no longer describes it. Read
    from the database: a save normalises what it was given.
    """
    from order.models.order import Order

    row = Order.objects.values(*Order.PERSONAL_FIELDS, *ADDRESS_COLUMNS).get(
        pk=order.pk
    )
    return _hmac({name: _plain(value) for name, value in row.items()})


def unpaid_orders_of_cart(
    cart: Cart, *, pay_way: PayWay | None = None
) -> QuerySet[Order]:
    """The cart's orders that hold their stock for an online payment and
    are not an agent's. With ``pay_way``, those made with it.

    An agent's order is the gateway's: a repeat of it is answered by its
    agent checkout, a second one refused (#692), never handed to another
    caller of the same cart.
    """
    from order.enum.attribution import OrderSourceType
    from order.models.order import Order

    orders = (
        Order.objects.holding_stock_for_online_payment()
        .filter(
            metadata__contains={"cart_snapshot": {"cart_uuid": str(cart.uuid)}},
            metadata__has_key=FINGERPRINT_KEY,
        )
        .exclude(attribution__source_type=OrderSourceType.AGENT)
    )
    if pay_way is not None:
        orders = orders.filter(pay_way=pay_way)
    return orders


def order_to_resume(
    cart: Cart, *, pay_way: PayWay, fingerprint: str
) -> Order | None:
    """The cart's unpaid order this same checkout made, locked; ``None``
    when there is none, its address was edited since, it was canceled, or
    it can no longer be paid (no link it has is payable, and no new link of
    the pay way's provider fits in its payment window).

    One whose payment is being decided (processing, another amount taken,
    or paid while this waited for its lock) is returned whatever its
    window: asking for its link answers that a payment is in progress, and
    a second order would ask the shopper to pay twice.

    Read under the cart row lock, so a checkout sent twice at once is judged
    after the first one committed, and under the order's own, so a cancel
    or a payment that commits meanwhile is seen.
    """
    from order.enum.status import OrderStatus
    from order.models.order import Order
    from order.payments.payment import get_payment_provider_class

    provider = get_payment_provider_class(pay_way.provider_code)
    if provider is None:
        return None
    made_by_it = list(
        unpaid_orders_of_cart(cart, pay_way=pay_way)
        .filter(metadata__contains={FINGERPRINT_KEY: {"checkout": fingerprint}})
        .values_list("pk", flat=True)
    )
    for order in (
        Order.objects.filter(pk__in=made_by_it)
        .order_by("-created_at")
        .select_for_update(of=("self",))
    ):
        if order.status == OrderStatus.CANCELED:
            continue
        if order.metadata[FINGERPRINT_KEY]["address"] != address_fingerprint(
            order
        ):
            continue
        if payment_undecided(order) and not _still_payable(order, provider):
            continue
        return order
    return None


def _still_payable(order: Order, provider) -> bool:
    """A link opened for the order is payable until the expiry its provider
    applied, whatever is left of the hold; without one, a new link must
    still fit (``payment_hold.window_closed``). Read from the order alone,
    under its lock: asking for its link reads the provider."""
    from order.payments import payment_hold

    opened = order.payment_hold_expires_at
    if opened is not None and opened > timezone.now():
        return True
    return not payment_hold.window_closed(order, provider.min_hosted_session)


def payment_undecided(order: Order) -> bool:
    """Whether nothing was taken for the order yet: its payment is pending
    or failed, and no other amount arrived for it."""
    from order.enum.status import PaymentStatus
    from order.models.order import AMOUNT_MISMATCH_FLAG

    return (
        order.payment_status in {PaymentStatus.PENDING, PaymentStatus.FAILED}
        and AMOUNT_MISMATCH_FLAG not in order.metadata
    )


def lines_of_an_unpaid_order(cart: Cart, quantities: Mapping[int, int]) -> bool:
    """Whether one of the cart's unpaid orders holds exactly these units
    (product id -> quantity): a checkout of them is that order's again,
    and resuming it needs no hold, since the order already took them."""
    from order.models.item import OrderItem

    wanted = dict(quantities)
    lines: dict[int, dict[int, int]] = {}
    rows = (
        OrderItem.objects.filter(order__in=unpaid_orders_of_cart(cart))
        .values("order_id", "product_id")
        .annotate(units=Sum("quantity"))
        .values_list("order_id", "product_id", "units")
    )
    for order_id, product_id, units in rows:
        lines.setdefault(order_id, {})[product_id] = units
    return any(order_lines == wanted for order_lines in lines.values())


def _hmac(content: dict[str, Any]) -> str:
    canonical = json.dumps(content, separators=(",", ":"), sort_keys=True)
    return salted_hmac(
        CHECKOUT_FINGERPRINT_SALT, canonical, algorithm="sha256"
    ).hexdigest()


def _plain(value: Any) -> Any:
    """A shipping address value as JSON: a related row by its key, a
    phone number or anything else by its text."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    pk = getattr(value, "pk", None)
    if pk is not None:
        return pk
    return str(value)
