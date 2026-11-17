"""What checkout charges on top of the products, before an order exists.

The value every "free above X" rule is judged on (``threshold_value``),
the shopper's distance to free shipping, the shipping price and the
pay-way fee. Order creation, the payment-intent endpoint and the cart
serializer all take these from ``OrderQuoteService``, so the cart
preview, the intent amount and the order cannot disagree.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.utils.translation import gettext_lazy as _
from djmoney.money import Money

from order.exceptions import InvalidOrderDataError
from promotion.enum import BenefitType
from promotion.services import PromotionEngine
from shipping.enum import FreeShippingSource

if TYPE_CHECKING:
    from cart.models import Cart
    from promotion.services import CartDiscountResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FreeShippingProgress:
    """How far a cart is from free shipping, by its cheapest route.

    ``free`` once checkout would charge 0 for shipping; otherwise
    exactly one of ``remaining_amount`` (products still missing, in the
    store's currency) and ``remaining_quantity`` (units of the
    promotion's products still missing) is set. ``threshold`` is the
    value the route is reached at — the rate's threshold, or the
    promotion's minimum subtotal — and ``None`` for a units route or a
    promotion that waives shipping outright. ``country_code`` is the
    country the rates were judged for.
    """

    source: FreeShippingSource
    country_code: str | None
    free: bool = False
    remaining_amount: Decimal | None = None
    remaining_quantity: int | None = None
    threshold: Decimal | None = None


class OrderQuoteService:
    @staticmethod
    def threshold_value(cart_total: Money, discount_total: Money) -> Money:
        """The value every "free above X" rule is judged on: the products
        after promotion and coupon discounts, excluding shipping and the
        pay-way fee.

        Free shipping (``ShippingRate.free_shipping_threshold``) and a free
        payment fee (``PayWay.free_threshold``) both compare against THIS,
        so "free above 40" means the same thing to the shopper for each
        and the cart preview and order creation cannot disagree. Every
        caller of :meth:`shipping_cost` and
        :meth:`calculate_payment_method_fee` takes it from here. Loyalty
        points and gift cards are payment, not discounts, and do not move
        it.
        """
        return Money(
            max(cart_total.amount - discount_total.amount, Decimal(0)),
            cart_total.currency,
        )

    @classmethod
    def free_shipping_progress(
        cls,
        cart: Cart,
        promotion_result: CartDiscountResult,
        *,
        country_code: str | None = None,
    ) -> FreeShippingProgress | None:
        """How far ``cart`` is from free shipping, by its cheapest route.

        Decided by the two rules :meth:`shipping_cost` charges with, so
        the line the cart shows cannot promise what checkout will not
        give:

        * a live free-shipping promotion (``promotion_result.
          free_shipping``) waives every carrier's price, and one the
          cart is short of is a teaser in ``promotion_result.near_miss``
          that the engine has already checked would really be granted
          (``PromotionEngine._teaser_holds``: scope, first order, usage
          limits, the wholesale gate). Its distance is the teaser's;
        * a rate's ``free_shipping_threshold`` zeroes that carrier and
          kind once :meth:`threshold_value` reaches it. Only rates that
          could take this cart count
          (``ShippingService.free_threshold_rates``: the checkout gate,
          the weight cap, the parcel size).

        The rates are judged for ``country_code``, or for the store's
        first shipping country, the one checkout preselects; a promotion
        holds in every country. Of the routes left, the one that costs
        the shopper least wins, a promotion on a tie since it does not
        depend on the carrier; a units teaser costs what the engine's
        own simulation adds (``PromotionEngine.teaser_cost``).

        A threshold's distance is measured on :meth:`threshold_value`,
        the products after discounts, as checkout judges it; a
        promotion's on the products before them, as the engine judges
        its minimum. ``None`` when the cart has no route to free
        shipping at all.
        """
        from shipping.services import ShippingService
        from shipping.utils import compute_total_weight_grams

        country = (country_code or "").upper() or (
            ShippingService.initial_country_code()
        )
        if promotion_result.free_shipping:
            return FreeShippingProgress(
                source=FreeShippingSource.PROMOTION,
                country_code=country,
                free=True,
            )

        cart_items = list(cart.items.all())
        pairs = [
            (item.product, item.quantity) for item in cart_items
        ] + PromotionEngine.gift_pairs(promotion_result)
        value = cls.threshold_value(
            cart.total_price, promotion_result.discount_total
        ).amount

        # (cost to the shopper, promotion first on a tie, the route)
        routes: list[tuple[Decimal, int, FreeShippingProgress]] = []
        if country is not None:
            rates = ShippingService.free_threshold_rates(
                country,
                weight_grams=compute_total_weight_grams(pairs),
                items=pairs,
            )
            for rate in rates:
                threshold = rate.free_shipping_threshold.amount
                if value >= threshold:
                    return FreeShippingProgress(
                        source=FreeShippingSource.THRESHOLD,
                        country_code=country,
                        free=True,
                        threshold=threshold,
                    )
                remaining = threshold - value
                routes.append(
                    (
                        remaining,
                        1,
                        FreeShippingProgress(
                            source=FreeShippingSource.THRESHOLD,
                            country_code=country,
                            remaining_amount=remaining,
                            threshold=threshold,
                        ),
                    )
                )

        for entry in promotion_result.near_miss:
            promotion = entry.promotion
            if promotion.benefit_type != BenefitType.FREE_SHIPPING:
                continue
            if entry.remaining_amount is not None:
                routes.append(
                    (
                        entry.remaining_amount,
                        0,
                        FreeShippingProgress(
                            source=FreeShippingSource.PROMOTION,
                            country_code=country,
                            remaining_amount=entry.remaining_amount,
                            threshold=promotion.min_subtotal.amount,
                        ),
                    )
                )
                continue
            cost = PromotionEngine.teaser_cost(entry, cart_items)
            if cost is None:
                continue
            routes.append(
                (
                    cost,
                    0,
                    FreeShippingProgress(
                        source=FreeShippingSource.PROMOTION,
                        country_code=country,
                        remaining_quantity=entry.remaining_quantity,
                    ),
                )
            )

        if not routes:
            return None
        return min(routes, key=lambda route: (route[0], route[1]))[2]

    @classmethod
    def shipping_cost(
        cls,
        order_value: Money,
        country_id: str | None = None,
        region_id: str | None = None,
        shipping_provider_code: str | None = None,
        shipping_kind: str | None = None,
        weight_grams: int | None = None,
        *,
        items: Iterable[tuple[Any, int]] | None = None,
        free: bool = False,
    ) -> Money:
        """The shipping price for a (provider, kind, country) — or 0 when ``free``.

        ``items`` are the cart's ``(product, quantity)`` pairs, checked
        against the carrier's parcel sizes next to the weight cap.

        ``order_value`` is :meth:`threshold_value`, never the gross cart
        total: the free-shipping threshold is judged on the products after
        discounts.

        ``ShippingRate`` is the only source of a shipping price now —
        replaces ``calculate_shipping_cost`` and the generic
        ``CHECKOUT_SHIPPING_PRICE`` / ``FREE_SHIPPING_THRESHOLD``
        fallback it used to fall through to for an unmatched carrier.
        A combination with no active rate is a genuine error, not a
        gap to paper over with a platform-wide flat price.

        ``free=True`` is for a free-shipping promotion: the price is
        forced to 0, but availability and the weight cap are STILL
        checked — a promo must never make an unavailable or
        over-weight option orderable just because it charges nothing.

        Raises :class:`~order.exceptions.InvalidOrderDataError` (never
        the raw ``shipping.exceptions`` types) so every caller — order
        creation, the create-payment-intent endpoint — gets the same
        field-scoped 400 shape it already handles for every other
        checkout-time rejection.
        """
        from shipping.enum import ShippingKind
        from shipping.exceptions import (
            ShippingParcelTooLargeError,
            ShippingUnavailableError,
            ShippingWeightExceededError,
        )
        from shipping.services import ShippingService

        # An order created without a kind is a home delivery —
        # ``Order.shipping_kind``'s own default, and what
        # ``OrderService._resolve_shipping_provider`` assigns the carrier
        # for — so it is priced as one too.
        shipping_kind = shipping_kind or ShippingKind.HOME_DELIVERY.value

        # Auto-resolve ``home_delivery`` to the active home-delivery
        # provider's code when the caller didn't supply one — mirrors
        # what ``OrderService._resolve_shipping_provider`` does for the
        # order FK (same helper, single source of truth) so the cost calc and
        # the assigned carrier always agree. Without this, a
        # ``home_delivery`` cart with no explicit provider_code (the
        # storefront sends ``null`` per
        # ``shared/shipping/index.ts::carrierForMethod`` — home
        # delivery is provider-agnostic at the form level) would never
        # resolve a rate, and ``OrderService._resolve_shipping_provider``
        # would still set the order's FK to a real carrier — a silent
        # disagreement between the carrier the customer was promised
        # and the price they paid.
        if not shipping_provider_code and shipping_kind == "home_delivery":
            shipping_provider_code = (
                ShippingService.resolve_home_delivery_provider(
                    country_id, weight_grams
                )
            )

        try:
            if free:
                ShippingService.assert_available(
                    provider_code=shipping_provider_code,
                    kind=shipping_kind,
                    country_code=country_id,
                    weight_grams=weight_grams,
                    items=items,
                )
                return Money(0, order_value.currency)

            return ShippingService.quote(
                provider_code=shipping_provider_code,
                kind=shipping_kind,
                country_code=country_id,
                region_id=region_id,
                weight_grams=weight_grams,
                items=items,
                order_value=order_value,
            )
        except ShippingUnavailableError as exc:
            raise InvalidOrderDataError(
                str(
                    _(
                        "The selected shipping method is not available "
                        "for the selected country."
                    )
                ),
                field_errors={
                    "shipping_provider_code": [
                        str(
                            _(
                                "The selected shipping method is not "
                                "available for the selected country."
                            )
                        )
                    ]
                },
            ) from exc
        except ShippingParcelTooLargeError as exc:
            raise InvalidOrderDataError(
                str(
                    _("The cart is too large for the selected shipping method.")
                ),
                field_errors={
                    "shipping_provider_code": [
                        str(
                            _(
                                "The cart is too large for the selected "
                                "shipping method."
                            )
                        )
                    ]
                },
            ) from exc
        except ShippingWeightExceededError as exc:
            raise InvalidOrderDataError(
                str(
                    _("The cart is too heavy for the selected shipping method.")
                ),
                field_errors={
                    "shipping_provider_code": [
                        str(
                            _(
                                "The cart is too heavy for the selected "
                                "shipping method."
                            )
                        )
                    ]
                },
            ) from exc

    @classmethod
    def calculate_payment_method_fee(
        cls,
        pay_way,
        order_value: Money,
    ) -> Money:
        """
        Calculate payment method fee based on PayWay configuration.

        Args:
            pay_way: PayWay instance
            order_value: :meth:`threshold_value`, the products after
                discounts (never including shipping or this fee)

        Returns:
            Money: Payment method fee (0 if free threshold is met)

        Example:
            >>> pay_way = PayWay.objects.get(id=1)
            >>> order_value = Money(45.00, 'EUR')
            >>> fee = OrderQuoteService.calculate_payment_method_fee(
            ...     pay_way, order_value
            ... )
            >>> # Returns Money(3.50, 'EUR') if pay_way.cost = 3.50 and threshold not met
        """
        if not pay_way or not pay_way.cost:
            return Money(0, order_value.currency)

        # Check if order value meets free threshold
        if (pay_way.free_threshold and pay_way.free_threshold.amount > 0) and (
            order_value.amount >= pay_way.free_threshold.amount
        ):
            return Money(0, order_value.currency)

        # Same currency only. Re-labelling would turn a fee configured
        # in one currency into the same NUMBER in another — charging a
        # different sum rather than converting it.
        if pay_way.cost.currency != order_value.currency:
            logger.error(
                "Pay-way %s fee is in %s but the order is in %s — charging "
                "no fee rather than re-labelling the amount",
                pay_way.pk,
                pay_way.cost.currency,
                order_value.currency,
            )
            return Money(0, order_value.currency)

        return Money(pay_way.cost.amount, order_value.currency)
