"""What checkout charges for a cart, before an order exists.

The value every "free above X" rule is judged on (``threshold_value``),
the shopper's distance to free shipping, the shipping price, the
pay-way fee, and the total they add up to (``quote``). Order creation,
the payment-intent endpoint, ``POST /cart/quote`` and the cart
serializer all take these from ``OrderQuoteService``, so the cart
preview, the quote, the intent amount and the order cannot disagree.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.utils.translation import gettext_lazy as _
from djmoney.money import Money

from order.exceptions import (
    InvalidCouponError,
    InvalidGiftCardError,
    LoyaltyRedemptionError,
    ShippingPriceUnresolvedError,
    ShippingRefusedError,
)
from promotion.enum import BenefitType
from promotion.services import PromotionEngine
from shipping.enum import FreeShippingSource, ShippingRefusal

if TYPE_CHECKING:
    from cart.models import Cart
    from giftcard.services import RedemptionPlan
    from pay_way.models import PayWay
    from promotion.services import CartDiscountResult
    from shipping.services import ShippingCharge

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


@dataclass(frozen=True, slots=True)
class ShippingChoice:
    """The delivery a shopper chose: what ``shipping_cost`` prices.

    ``kind`` may be empty: an order placed without one is a home
    delivery, and is priced as one. ``provider_code`` may be empty for
    a home delivery, whose carrier checkout resolves.
    """

    kind: str | None
    provider_code: str | None
    country_id: str | None
    region_id: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentFee:
    """What a pay way adds to an order.

    ``waived`` when the pay way has a fee and the cart reached its
    free-above threshold; a pay way that simply costs nothing is not
    waived.
    """

    amount: Money
    waived: bool = False


@dataclass(frozen=True, slots=True)
class ExpectedTotal:
    """An order's ``amount_due`` the way a client states it back: whole
    minor units (cents) and the currency, so it is compared exactly."""

    amount: int
    currency: str

    @classmethod
    def of(cls, money: Money) -> ExpectedTotal:
        return cls(
            amount=round(money.amount * 100), currency=str(money.currency)
        )


@dataclass(frozen=True, slots=True)
class BlockedCoupon:
    """An attached coupon order creation refuses the cart for, and why
    (``promotion.enum.CouponRejectionReason``)."""

    code: str
    reason: str


@dataclass(frozen=True, slots=True)
class OrderCharges:
    """The rows an order is created from: those of a quote whose
    delivery and pay way are both chosen."""

    shipping: ShippingCharge
    payment_fee: PaymentFee
    gift_cards: RedemptionPlan
    amount_due: Money


@dataclass(frozen=True, slots=True)
class CheckoutQuote:
    """What checkout charges for a cart, row by row.

    Built by :meth:`OrderQuoteService.quote` alone. ``shipping`` and
    ``payment_fee`` are ``None`` while the shopper has not chosen
    them, and so are the rows that need both: ``grand_total``,
    ``gift_cards`` (planned against what is left to pay) and
    ``amount_due``. A row is never guessed.

    ``grand_total`` is the products after promotions, plus shipping and
    the pay-way fee. ``amount_due`` is what is left of it after the
    loyalty redemption and the gift cards: the amount the order stores
    and the payment is taken for. ``loyalty_points`` are the points
    ``loyalty_discount`` was priced for, 0 when none are redeemed.
    """

    items_subtotal: Money
    promotion: CartDiscountResult
    parcel_items: tuple[tuple[Any, int], ...]
    weight_grams: int
    shipping: ShippingCharge | None
    payment_fee: PaymentFee | None
    loyalty_points: int
    loyalty_discount: Money
    gift_cards: RedemptionPlan | None
    grand_total: Money | None
    amount_due: Money | None

    @property
    def currency(self) -> str:
        return str(self.items_subtotal.currency)

    @property
    def discount_total(self) -> Money:
        return self.promotion.discount_total

    @property
    def free_threshold_value(self) -> Money:
        return OrderQuoteService.threshold_value(
            self.items_subtotal, self.discount_total
        )

    @property
    def gift_card_amount(self) -> Money | None:
        return None if self.gift_cards is None else self.gift_cards.amount

    @property
    def blocking_coupon(self) -> BlockedCoupon | None:
        """The attached coupon order creation refuses this cart for, if
        any: its discount is not in the quote, and no order is created
        until the code is removed."""
        blocking = self.promotion.blocking_rejections
        return BlockedCoupon(*blocking[0]) if blocking else None

    def charges(self) -> OrderCharges:
        """The rows of a quote with nothing left to choose. A caller
        that gave ``quote`` both a delivery and a pay way reads them
        here; for any other quote it is a programming error."""
        if (
            self.shipping is None
            or self.payment_fee is None
            or self.gift_cards is None
            or self.amount_due is None
        ):
            raise ValueError(
                "a quote without a delivery and a pay way has no charges"
            )
        return OrderCharges(
            shipping=self.shipping,
            payment_fee=self.payment_fee,
            gift_cards=self.gift_cards,
            amount_due=self.amount_due,
        )

    @property
    def expected_total(self) -> ExpectedTotal | None:
        """``amount_due`` as order creation compares it."""
        if self.amount_due is None:
            return None
        return ExpectedTotal.of(self.amount_due)


class OrderQuoteService:
    @classmethod
    def quote(
        cls,
        cart: Cart,
        *,
        user,
        email: str = "",
        pay_way: PayWay | None = None,
        shipping: ShippingChoice | None = None,
        loyalty_points: int | None = None,
        gift_card_codes: list[str] | None = None,
        lock: bool = False,
    ) -> CheckoutQuote:
        """What checkout charges for ``cart`` with these choices.

        The one computation of the total: ``POST /cart/quote``, the
        payment-intent amount and both order-creation paths call it, so
        a rule added here reaches all of them. ``cart`` carries its
        wholesale prices already (``B2BPricingService.bind_cart``).

        ``shipping`` and ``pay_way`` are ``None`` while the shopper
        has not chosen; see :class:`CheckoutQuote` for what that leaves
        unset. ``user`` is whose prices, promotions and points apply:
        loyalty points asked for without one are not redeemed.

        ``lock=True`` is order creation, inside its transaction: the
        promotions, the customer's points and the gift cards are locked
        until commit, so what is quoted is what gets redeemed, and a
        refused coupon raises ``InvalidCouponError`` (an order must not
        be created without a discount the shopper was shown). Without
        it nothing is locked and a refused coupon is reported on the
        quote (``blocking_coupon``).

        Raises ``InvalidOrderDataError`` for a delivery that cannot be
        priced (field-scoped, see :meth:`shipping_cost`),
        ``LoyaltyRedemptionError`` and ``InvalidGiftCardError``.
        """
        from b2b.services import B2BService

        promotion = PromotionEngine.evaluate(
            cart, user=user, email=email, lock=lock
        )
        if lock:
            blocking = promotion.blocking_rejections
            if blocking:
                code, reason = blocking[0]
                raise InvalidCouponError(code=code, reason=reason)

        items_subtotal = cart.total_price
        currency = items_subtotal.currency
        parcel_items, weight_grams = cls._parcel(cart, promotion)
        threshold_value = cls.threshold_value(
            items_subtotal, promotion.discount_total
        )

        shipping_charge = None
        if shipping is not None:
            shipping_charge = cls.shipping_cost(
                order_value=threshold_value,
                country_id=shipping.country_id,
                region_id=shipping.region_id,
                shipping_provider_code=shipping.provider_code,
                shipping_kind=shipping.kind,
                weight_grams=weight_grams,
                items=parcel_items,
                free=promotion.free_shipping,
                # Rows are locked: the carrier is not asked. Its live
                # price was resolved before (``resolve_live_shipping``).
                cached_only=lock,
            )
        payment_fee = None
        if pay_way is not None:
            payment_fee = PaymentFee(
                amount=cls.calculate_payment_method_fee(
                    pay_way, threshold_value
                ),
                waived=cls._fee_waived(pay_way, threshold_value),
            )

        # Wholesale carts sit outside the loyalty program unless the
        # merchant opts in. Dropped here, before any total, so the
        # quote, the intent amount, the order and ``redeem_points`` agree.
        if B2BService.suppresses_loyalty(cart):
            loyalty_points = 0
        loyalty_points = cls._redeemable_points(user, loyalty_points)
        # Points pay for goods, never for shipping or a fee: the most
        # they take off is what the goods cost after the promotions
        # (``threshold_value``). ``redeem_points`` at order creation is
        # given the same cap.
        loyalty_discount = Money(
            cls._loyalty_discount(
                user,
                loyalty_points,
                currency,
                max_discount=threshold_value.amount,
                lock=lock,
            ),
            currency,
        )

        grand_total = amount_due = gift_cards = None
        if shipping_charge is not None and payment_fee is not None:
            grand_total = Money(
                threshold_value.amount
                + shipping_charge.price.amount
                + payment_fee.amount.amount,
                currency,
            )
            # Loyalty is a discount; gift cards are payment and settle
            # last, against what is still due.
            due = Money(
                max(grand_total.amount - loyalty_discount.amount, Decimal(0)),
                currency,
            )
            gift_cards = cls._gift_card_plan(gift_card_codes, due, lock=lock)
            amount_due = Money(due.amount - gift_cards.amount.amount, currency)

        return CheckoutQuote(
            items_subtotal=items_subtotal,
            promotion=promotion,
            parcel_items=parcel_items,
            weight_grams=weight_grams,
            shipping=shipping_charge,
            payment_fee=payment_fee,
            loyalty_points=loyalty_points,
            loyalty_discount=loyalty_discount,
            gift_cards=gift_cards,
            grand_total=grand_total,
            amount_due=amount_due,
        )

    @staticmethod
    def _parcel(cart: Cart, promotion: CartDiscountResult):
        """What ships and what it weighs: the cart's lines and the gifts
        the promotions add. Gifts ride in the parcel, so they count for
        the weight bracket, the weight cap and the carrier's parcel size.
        """
        from shipping.utils import compute_total_weight_grams

        parcel_items = (
            *((item.product, item.quantity) for item in cart.items.all()),
            *PromotionEngine.gift_pairs(promotion),
        )
        return parcel_items, compute_total_weight_grams(parcel_items)

    @staticmethod
    def _carrier(
        shipping_kind: str | None,
        shipping_provider_code: str | None,
        country_id: str | None,
        weight_grams: int | None,
    ) -> tuple[str, str | None]:
        """The (kind, carrier code) a delivery is priced as.

        An order created without a kind is a home delivery —
        ``Order.shipping_kind``'s own default, and what
        ``OrderService._resolve_shipping_provider`` assigns the carrier
        for — so it is priced as one too. A home delivery that names no
        carrier takes the active home-delivery carrier for the country,
        by the helper that assigns the order's carrier, so the price
        charged and the carrier assigned always agree.
        """
        from shipping.enum import ShippingKind
        from shipping.services import ShippingService

        shipping_kind = shipping_kind or ShippingKind.HOME_DELIVERY.value
        if (
            not shipping_provider_code
            and shipping_kind == ShippingKind.HOME_DELIVERY.value
        ):
            shipping_provider_code = (
                ShippingService.resolve_home_delivery_provider(
                    country_id, weight_grams
                )
            )
        return shipping_kind, shipping_provider_code

    @classmethod
    def resolve_live_shipping(
        cls, cart: Cart, *, user, email: str = "", shipping: ShippingChoice
    ) -> None:
        """Have the carrier's live price for this checkout resolved now.

        Order creation calls it BEFORE it locks anything. A carrier call
        can take seconds (ACS: a 15 s timeout), and under the locks of
        ``quote(lock=True)`` every checkout that shares the cart, a
        promotion, the customer or a gift card would wait behind it. So
        the price is asked for here, with nothing locked, and the answer
        is cached by the carrier's adapter; the locked quote then prices
        with ``cached_only`` and never reaches the carrier.

        It resolves the price of the parcel the locked quote will weigh
        (the gifts of an unlocked promotion evaluation included), whether
        or not a threshold or a promotion ends up waiving it.
        """
        from shipping.services import ShippingService

        _parcel_items, weight_grams = cls._parcel(
            cart, PromotionEngine.evaluate(cart, user=user, email=email)
        )
        kind, provider_code = cls._carrier(
            shipping.kind,
            shipping.provider_code,
            shipping.country_id,
            weight_grams,
        )
        ShippingService.resolve_live_price(
            provider_code=provider_code,
            kind=kind,
            country_code=shipping.country_id,
            region_id=shipping.region_id,
            weight_grams=weight_grams,
        )

    @staticmethod
    def _redeemable_points(user, loyalty_points: int | None) -> int:
        """The points a redemption is priced for: none without a
        signed-in customer to take them from."""
        if (
            not loyalty_points
            or loyalty_points <= 0
            or user is None
            or not getattr(user, "is_authenticated", False)
        ):
            return 0
        return loyalty_points

    @staticmethod
    def _loyalty_discount(
        user,
        loyalty_points: int,
        currency,
        *,
        max_discount: Decimal,
        lock: bool,
    ) -> Decimal:
        """Price the redemption with the math ``redeem_points`` runs.

        Under ``lock`` the customer's row stays locked until commit, so
        the later ``redeem_points`` of the same transaction cannot find
        a different balance. A redemption that cannot be honoured is an
        error, never dropped: the shopper was shown the discount.
        """
        if not loyalty_points:
            return Decimal(0)

        from django.core.exceptions import ValidationError

        from loyalty.services import LoyaltyService

        try:
            return LoyaltyService.preview_redemption(
                user, loyalty_points, str(currency), max_discount, lock=lock
            )
        except ValidationError as exc:
            raise LoyaltyRedemptionError(
                getattr(exc, "message", None)
                or "; ".join(str(msg) for msg in getattr(exc, "messages", [])),
                code=getattr(exc, "code", None),
            ) from exc

    @staticmethod
    def _gift_card_plan(
        gift_card_codes: list[str] | None, amount_due: Money, *, lock: bool
    ) -> RedemptionPlan:
        """What the cards settle of ``amount_due``. Under ``lock`` the
        card rows stay locked until commit, so the plan quoted is the
        plan ``GiftCardService.record_plan`` writes."""
        from giftcard.services import GiftCardError, GiftCardService

        try:
            return GiftCardService.plan_redemption(
                gift_card_codes or [], amount_due, lock=lock
            )
        except GiftCardError as exc:
            raise InvalidGiftCardError(
                reason=exc.reason, message=str(exc.message)
            ) from exc

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
        cached_only: bool = False,
    ) -> ShippingCharge:
        """The shipping price for a (provider, kind, country) — or 0 when ``free`` —
        and what made it free (``ShippingCharge.waived_by``).

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

        ``cached_only`` is the locked quote of order creation: no
        carrier is asked, and a live price that was not resolved first
        (:meth:`resolve_live_shipping`) raises
        ``ShippingPriceUnresolvedError``.

        ``free=True`` is for a free-shipping promotion: the price is
        forced to 0, but availability and the weight cap are STILL
        checked — a promo must never make an unavailable or
        over-weight option orderable just because it charges nothing.

        Raises :class:`~order.exceptions.ShippingRefusedError`, an
        ``InvalidOrderDataError`` that names which refusal it is (never
        the raw ``shipping.exceptions`` types), so every caller of
        :meth:`quote` — order creation, the create-payment-intent and
        quote endpoints — gets the same field-scoped 400 shape it
        already handles for every other checkout-time rejection.
        """
        from shipping.exceptions import (
            ShippingLivePriceUnresolvedError,
            ShippingParcelTooLargeError,
            ShippingUnavailableError,
            ShippingWeightExceededError,
        )
        from shipping.services import ShippingService

        shipping_kind, shipping_provider_code = cls._carrier(
            shipping_kind, shipping_provider_code, country_id, weight_grams
        )

        try:
            return ShippingService.quote(
                provider_code=shipping_provider_code,
                kind=shipping_kind,
                country_code=country_id,
                region_id=region_id,
                weight_grams=weight_grams,
                items=items,
                order_value=order_value,
                free=free,
                cached_only=cached_only,
            )
        except ShippingLivePriceUnresolvedError as exc:
            raise ShippingPriceUnresolvedError from exc
        except ShippingUnavailableError as exc:
            raise ShippingRefusedError(
                ShippingRefusal.UNAVAILABLE,
                str(
                    _(
                        "The selected shipping method is not available "
                        "for the selected country."
                    )
                ),
            ) from exc
        except ShippingParcelTooLargeError as exc:
            raise ShippingRefusedError(
                ShippingRefusal.PARCEL_TOO_LARGE,
                str(
                    _("The cart is too large for the selected shipping method.")
                ),
            ) from exc
        except ShippingWeightExceededError as exc:
            raise ShippingRefusedError(
                ShippingRefusal.WEIGHT_EXCEEDED,
                str(
                    _("The cart is too heavy for the selected shipping method.")
                ),
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
        if (
            not pay_way
            or not pay_way.cost
            or cls._fee_waived(pay_way, order_value)
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

    @staticmethod
    def _fee_waived(pay_way, order_value: Money) -> bool:
        """Whether ``order_value`` reaches the free-above threshold of a
        pay way that has a fee."""
        # Same currency only, as the fee itself: a threshold in another
        # currency is another sum, not this number.
        return bool(
            pay_way.cost
            and pay_way.free_threshold
            and pay_way.free_threshold.amount > 0
            and pay_way.free_threshold.currency == order_value.currency
            and order_value.amount >= pay_way.free_threshold.amount
        )

    @classmethod
    def shipping_options(
        cls,
        quote: CheckoutQuote,
        *,
        country_id: str,
        region_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Every delivery the store offers to ``country_id``, each at the
        price ``quote``'s cart would be charged for choosing it.

        The rows are ``ShippingService.available_options``, priced with
        what :meth:`shipping_cost` prices an order with: the cart's
        :meth:`threshold_value`, its parcel (gifts included), the region
        and a free-shipping promotion.
        """
        from shipping.services import ShippingService

        return ShippingService.available_options(
            country_code=country_id,
            order_value_amount=quote.free_threshold_value.amount,
            currency=quote.currency,
            weight_grams=quote.weight_grams,
            items=quote.parcel_items,
            region_id=region_id,
            free=quote.promotion.free_shipping,
        )

    @classmethod
    def pay_way_options(
        cls, quote: CheckoutQuote, shipping: ShippingChoice | None
    ) -> list[dict[str, Any]]:
        """Every pay way a shopper is offered for ``shipping`` (all of
        them while no delivery is chosen), each with the fee ``quote``'s
        cart would be charged for choosing it."""
        value = quote.free_threshold_value
        return [
            {
                "id": pay_way.pk,
                "key": pay_way.key,
                "fee": cls.calculate_payment_method_fee(pay_way, value).amount,
                "waived": cls._fee_waived(pay_way, value),
            }
            for pay_way in cls.offered_pay_ways(shipping).order_by(
                "sort_order", "id"
            )
        ]

    @staticmethod
    def offered_pay_ways(shipping: ShippingChoice | None):
        """The pay ways a shopper is offered for ``shipping``: active,
        through a provider the store can charge with, and not excluded
        for the chosen delivery (all of the first two while none is
        chosen). The one rule behind both the list a quote returns and
        the pay way a quote or a payment intent accepts, so a selection
        is accepted exactly when it is listed."""
        from pay_way.models import PayWay
        from pay_way.services import PayWayService

        offered = PayWayService.shopper_facing(PayWay.objects.all())
        if shipping is None:
            return offered
        return PayWayService.filter_for_shipping(
            offered,
            provider_code=shipping.provider_code,
            shipping_kind=shipping.kind,
            country_code=shipping.country_id,
        )
