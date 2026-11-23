"""The engine's value types: what an evaluation returns, and the cart
line a simulation builds."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from djmoney.money import Money

from promotion.enum import BenefitType, CouponRejectionReason
from promotion.models import Promotion, PromotionCode

if TYPE_CHECKING:
    from collections.abc import Iterable

    from product.models.product import Product

TWO_PLACES = Decimal("0.01")

# Key of a simulated cart line (``SimulatedLine``) that is not a row yet.
NEW_LINE_PK = -1


@dataclass
class AppliedPromotion:
    """A monetary promotion that applies, and where its money lands.

    ``line_amounts`` (cart item pk -> amount) is filled by
    ``stacking.clamp``: the part of ``amount`` each cart line the
    promotion covers carries, to the cent, summing exactly to ``amount``.
    Entries not yet clamped (the pre-stacking ones) leave it empty.
    """

    promotion: Promotion
    code: PromotionCode | None
    amount: Money
    line_amounts: dict[int, Decimal] = field(default_factory=dict)


@dataclass
class GiftEntitlement:
    """A FREE_GIFT promotion's reward: ``quantity`` units of
    ``product`` added to the order at price 0."""

    promotion: Promotion
    product: Product
    quantity: int


@dataclass
class NearMissEntry:
    """An automatic promotion the cart ALMOST qualifies for.

    Exactly one distance is set: ``remaining_amount`` when only the
    minimum subtotal is missing, ``remaining_quantity`` when only units
    are — a minimum quantity, the rest of a buy-X-get-Y group, or, with
    ``reward_products`` set, units of one of those reward products (a
    reward-pool offer whose buy side is already met). Powers the
    storefront's "add X more to unlock" teaser. It is emitted only for a
    cart that already holds something the promotion counts, and only
    once ``PromotionEngine._teaser_holds`` has confirmed that doing what
    it says would actually get the promotion granted."""

    promotion: Promotion
    remaining_amount: Decimal | None = None
    remaining_quantity: int | None = None
    reward_products: list = field(default_factory=list)


@dataclass
class SimulatedLine:
    """A cart line as it would be after the shopper follows a teaser.

    Carries exactly what the engine reads from a ``CartItem`` —
    ``pk``, ``product``, ``product_id``, ``quantity``, ``final_price``,
    ``total_price`` — so the real classification can run over a cart
    that does not exist without writing anything. A line that is not in
    the cart yet takes ``NEW_LINE_PK``, which no row can have."""

    pk: int
    product: object
    quantity: int
    final_price: Money

    @property
    def product_id(self) -> int:
        return self.product.pk

    @property
    def total_price(self) -> Money:
        return Money(
            self.quantity * self.final_price.amount,
            self.final_price.currency,
        )


@dataclass(frozen=True, slots=True)
class CouponVerdict:
    """What one attached coupon code does for the cart: the money it
    takes off, the free shipping or gift it earns, or ``rejection``, the
    reason the engine refuses it now (None while it applies). A code
    that would change nothing is refused, so exactly one holds."""

    code: str
    amount: Decimal
    free_shipping: bool
    gift: bool
    rejection: str | None


@dataclass
class CartDiscountResult:
    applied: list[AppliedPromotion] = field(default_factory=list)
    free_shipping: bool = False
    gift_items: list[GiftEntitlement] = field(default_factory=list)
    near_miss: list[NearMissEntry] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    # The codes attached to the cart as this evaluation read them, the
    # ones it decided on; none when it read none (promotions off, an
    # empty cart).
    codes: list[str] = field(default_factory=list)
    # FREE_SHIPPING / FREE_GIFT promotions that applied — recorded as
    # zero-amount redemption rows so their usage limits are enforced
    # exactly like monetary promotions.
    non_monetary: list[tuple[Promotion, PromotionCode | None]] = field(
        default_factory=list
    )

    @property
    def discount_total(self) -> Money:
        currency = settings.DEFAULT_CURRENCY
        total = sum((entry.amount.amount for entry in self.applied), Decimal(0))
        return Money(total, currency)

    @property
    def line_discounts(self) -> dict[int, Decimal]:
        """Cart item pk -> the promotion money its line carries.

        Sums the applied promotions' ``line_amounts``, so the lines add up
        to ``discount_total`` exactly. This, not a proportional spread
        over the whole cart, is what the VAT of a discounted cart is
        derived from (``Cart.vat_after_discount``) and what checkout
        freezes on the order lines (``OrderItem.discount_allocation``).
        """
        lines: dict[int, Decimal] = defaultdict(Decimal)
        for entry in self.applied:
            for pk, amount in entry.line_amounts.items():
                lines[pk] += amount
        return dict(lines)

    @property
    def blocking_rejections(self) -> list[tuple[str, str]]:
        """Rejections that must abort order creation.

        Three reasons are not blocking, because dropping the code
        changes nothing the cart preview showed:

        - combination_disallowed: the code lost the stacking comparison
          to a better automatic promotion (or the automatic ones already
          use up what it would give), so the shopper is charged no more
          than the code alone would give.
        - not_applicable: nothing left in the cart is in the code's
          scope (the item it covered was removed after applying), so it
          discounted nothing.
        - gift_unavailable: the code's gift ran out after it was
          applied. A freebie must not hold the paid goods hostage, the
          same rule ``_inject_promotion_gifts`` follows.
        """
        non_blocking = {
            str(CouponRejectionReason.COMBINATION_DISALLOWED),
            str(CouponRejectionReason.NOT_APPLICABLE),
            str(CouponRejectionReason.GIFT_UNAVAILABLE),
        }
        return [
            (code, reason)
            for code, reason in self.rejected
            if reason not in non_blocking
        ]

    def coupon_verdicts(
        self, codes: Iterable[str]
    ) -> tuple[CouponVerdict, ...]:
        """The verdict on each of ``codes``, the codes attached to the
        evaluated cart, in their order. A caller with no read of its own
        passes ``self.codes``: the codes this evaluation decided on.

        A coupon row that only knows its own money cannot tell "free
        shipping" from "a better offer applies" (demo store: SHIPFREE
        read "a better offer applies" while it was waiving shipping),
        nor say why a code that stopped applying is idle. The evaluation
        already decided all of it; this hands its verdict over.
        """
        rejections: dict[str, str] = {}
        for code, reason in self.rejected:
            rejections.setdefault(code, reason)
        benefits = {
            code.code: promotion.benefit_type
            for promotion, code in self.non_monetary
            if code is not None
        }
        return tuple(
            CouponVerdict(
                code=code,
                amount=sum(
                    (
                        entry.amount.amount
                        for entry in self.applied
                        if entry.code is not None and entry.code.code == code
                    ),
                    Decimal(0),
                ),
                free_shipping=benefits.get(code) == BenefitType.FREE_SHIPPING,
                gift=benefits.get(code) == BenefitType.FREE_GIFT,
                rejection=rejections.get(code),
            )
            for code in codes
        )


def quantize(amount: Decimal) -> Decimal:
    return amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
