import logging
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, ClassVar, NoReturn
from uuid import UUID

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise
from djmoney.money import Money

from core.store_settings import store_setting
from core.utils.i18n import active_language_code
from order.checkout.attribution import (
    AttributionInput,
)
from order.checkout.attribution_service import OrderAttributionService
from order.checkout.discounts import (
    add_line_discounts,
    spread_by_remaining_value,
)
from order.checkout.quote import (
    CheckoutQuote,
    ExpectedTotal,
    OrderQuoteService,
    ShippingChoice,
)
from order.checkout.resume import (
    FINGERPRINT_KEY,
    checkout_fingerprint,
    unpaid_order_of_cart,
)
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.legal_acceptance import LegalAcceptanceSource
from order.enum.status import (
    SETTLED_PAYMENT_STATUSES,
    OrderStatus,
    PaymentStatus,
)
from order.exceptions import (
    AgentCheckoutOrderedError,
    CartHasUnpaidOrderError,
    CartNotReadyError,
    InsufficientStockError,
    InvalidCouponError,
    InvalidGiftCardError,
    InvalidOrderDataError,
    InvalidStatusTransitionError,
    OrderCartChangedError,
    OrderServiceError,
    OrderTotalChangedError,
    PaymentError,
    ProductNotFoundError,
    ShippingPriceUnresolvedError,
    StockReservationError,
    UnpaidOrderLimitError,
)
from order.inventory.stock import StockManager
from order.lifecycle.service import OrderLifecycleService
from order.models.item import OrderItem
from order.models.legal_acceptance import OrderLegalAcceptance
from order.models.order import (
    AGENT_CHECKOUT_UNIQUE_CONSTRAINT,
    Order,
)
from order.models.stock_reservation import StockReservation
from order.payments.payment import (
    StripePaymentProvider,
)
from order.payments.payment_binding import (
    SUBJECT_KEY,
    PaymentPurpose,
    binding_mismatch,
    own_purpose,
)
from order.payments.payment_ledger import (
    OFFLINE_TRANSFER_PROVIDER,
    PAYMENT_AMOUNT_TOLERANCE,
    PaymentLedger,
)
from order.signals import (
    order_stripe_payment_polled,
    payment_without_order_found,
)
from order.vat_snapshot import write_vat_lines
from page_config.legal_documents import CHECKOUT_CONSENT_SLUGS
from page_config.models import LegalDocumentVersion
from pay_way.enum.settlement import PaySettlement
from promotion.coupons import CouponService
from promotion.engine.redemptions import record_redemptions
from shipping.enum import ShippingKind
from shipping.interfaces import order_detail_prefetches
from tenant.internal import is_internal_caller

logger = logging.getLogger(__name__)

#: The claim that makes the payment-without-order alert once per payment
#: (``OrderService.alert_payment_without_order``). Stripe redelivers an
#: event for three days at most; the claim outlives that.
PAYMENT_WITHOUT_ORDER_CLAIM_KEY = "order:payment_without_order:{payment_id}"
PAYMENT_WITHOUT_ORDER_CLAIM_TTL = 7 * 24 * 60 * 60

# Payment statuses that represent a financially settled (terminal) state.
# A stale or out-of-order webhook event MUST NOT overwrite any of these.
# Stripe/Viva do NOT guarantee delivery order, so e.g. a delayed
# ``payment_failed`` event could arrive after a ``charge.refunded`` event
# that already moved the order to REFUNDED — that must not regress it to FAILED.
# Statuses for which a courier shipment is still meaningful. Anything
# else has either not been paid for yet or is already done with its
# voucher; see handle_payment_succeeded.
_SHIPMENT_DISPATCHABLE_STATUSES: frozenset[str] = frozenset(
    {
        OrderStatus.PENDING,
        OrderStatus.PROCESSING,
    }
)

# What a poll of the provider records for each status it reports
# (``OrderService._apply_polled_payment_status``): an open payment's state
# only. A canceled attempt is a failed one, since the order is still open;
# COMPLETED settles through ``handle_payment_succeeded``; a refunded state is
# the ledger's, so a status missing here is never written by a poll.
_POLLED_STATUS_RECORDED: dict[PaymentStatus, PaymentStatus] = {
    PaymentStatus.PENDING: PaymentStatus.PENDING,
    PaymentStatus.PROCESSING: PaymentStatus.PROCESSING,
    PaymentStatus.FAILED: PaymentStatus.FAILED,
    PaymentStatus.CANCELED: PaymentStatus.FAILED,
}


@dataclass(frozen=True, slots=True)
class PaymentEventOutcome:
    """What a provider payment event did to its order.

    ``applied`` is False when the event changed nothing the customer
    should hear about: a stale event against a settled payment, a repeat
    of one already applied, or a charge on a canceled order (booked for a
    manual refund by ``OrderService.record_payment_after_cancel``).
    """

    order: Order
    previous_payment_status: str
    applied: bool


def _violated_constraint(error: IntegrityError) -> str | None:
    """The constraint a database integrity error names, when the driver
    says (psycopg reports it as ``diag.constraint_name``, which for a
    unique index is the index name)."""
    diagnostics = getattr(error.__cause__, "diag", None)
    return getattr(diagnostics, "constraint_name", None)


def _log_price_drift_if_needed(cart_item, current_price) -> None:
    """Emit a warning when the live product price differs from the price the
    customer saw at add-to-cart time.

    This is observability only — we still charge the live price. Operators can
    monitor warnings to detect runaway price changes between add-to-cart and
    checkout, then decide whether to enforce price-match (block checkout when
    drift exceeds a threshold) or warn-and-confirm (UX hop) as a follow-up.
    """
    frozen = getattr(cart_item, "price_at_add", None)
    if frozen is None or current_price is None:
        return
    try:
        if (
            frozen.amount == current_price.amount
            and frozen.currency == current_price.currency
        ):
            return
    except AttributeError, TypeError:
        return
    logger.warning(
        "Cart price drift at checkout: cart_item=%s product=%s "
        "price_at_add=%s charged=%s (raw price_at_add=%r charged=%r delta=%s)",
        getattr(cart_item, "id", "?"),
        getattr(getattr(cart_item, "product", None), "id", "?"),
        frozen,
        current_price,
        frozen.amount,
        current_price.amount,
        current_price.amount - frozen.amount,
    )


@dataclass(frozen=True, slots=True)
class LegalAcceptance:
    """What the buyer of an order accepted, resolved before it is placed:
    how, and the version of each checkout legal document in force."""

    source: LegalAcceptanceSource
    documents: tuple[LegalDocumentVersion, ...]


LEGAL_DOCUMENT_UNAVAILABLE_DETAIL = _(
    "The store's terms of use or privacy policy cannot be shown right "
    "now, so the order cannot be placed. Please contact the store."
)
"""Why a checkout is refused for ``LegalDocumentUnavailableError``: a
store fault, not the buyer's, said at order creation, before the order
or any payment exists."""


class OrderLegalAcceptanceService:
    """Which legal texts an order was placed under."""

    @staticmethod
    def documents_in_force(
        language_code: str,
    ) -> tuple[LegalDocumentVersion, ...]:
        """The version of each checkout legal document a buyer reads in
        *language_code* now.

        Raises ``LegalDocumentUnavailableError`` when one has no
        published text in that language: the buyer was shown a broken
        link, so nothing can be accepted. Checked at order creation,
        before the order is created and so before any payment.
        """
        return tuple(
            LegalDocumentVersion.objects.in_force(slug, language_code)
            for slug in CHECKOUT_CONSENT_SLUGS
        )

    @classmethod
    def accept(cls, request) -> LegalAcceptance:
        """The acceptance behind *request*, in the language its order is
        placed in (``active_language_code``, as ``Order.language_code``)."""
        return LegalAcceptance(
            source=cls.source_for_request(request),
            documents=cls.documents_in_force(active_language_code()),
        )

    @staticmethod
    def source_for_request(request) -> LegalAcceptanceSource:
        """How the buyer behind *request* accepted the documents.

        The agent gateway proves itself with its shared secret
        (``X-Internal-Gateway``), as for the agent attribution: its
        checkouts link the documents for the agent to present. Every
        other caller is the storefront's checkout consent.
        """
        if is_internal_caller(request.headers.get("X-Internal-Gateway", "")):
            return LegalAcceptanceSource.AGENT_CHECKOUT
        return LegalAcceptanceSource.CHECKOUT_CONSENT

    @staticmethod
    def record(
        order: Order, acceptance: LegalAcceptance
    ) -> list[OrderLegalAcceptance]:
        """Store *acceptance* on *order*, in its creation transaction."""
        return [
            OrderLegalAcceptance.objects.create(
                order=order, document=document, source=acceptance.source
            )
            for document in acceptance.documents
        ]


class OrderService:
    @classmethod
    def _consume_stock_for_order(cls, *, order, cart, cart_items, reservations):
        """Reconcile session reservations against the cart, then decrement
        physical stock exactly once per product.

        The cart is the source of truth for how much stock each product owes.
        For each product the session's reservations are converted to sale
        decrements up to the cart quantity (linking them to the order and
        keeping the audit trail); any surplus reservation is released, and any
        shortfall is decremented directly. This guards against duplicate
        reserve calls (double decrement), missing/expired reservations
        (skipped decrement), a reservation that now overshoots a reduced cart
        quantity, and oversell — none of which the previous
        convert-every-reservation loop caught.

        Must run inside the order-creation ``transaction.atomic`` block,
        after ``_lock_order_products`` has locked every product the order
        touches; ``convert_reservation_to_sale``/``decrement_stock`` then
        re-lock a row this transaction already holds, so an
        insufficient-stock product raises ``InsufficientStockError`` and
        rolls the order back. Products are walked in ascending pk order,
        the order the locks were taken in.

        Returns the list of converted reservation ids (stored on the order for
        parity with the cancel path).
        """
        needed: dict[int, int] = {}
        for cart_item in cart_items:
            needed[cart_item.product_id] = (
                needed.get(cart_item.product_id, 0) + cart_item.quantity
            )

        reservations_by_product: dict[int, list] = {}
        for reservation in reservations:
            reservations_by_product.setdefault(
                reservation.product_id, []
            ).append(reservation)

        converted_ids: list[int] = []

        def _release(reservation):
            try:
                StockManager.release_reservation(reservation.id)
            except StockReservationError as exc:
                # Already consumed/released — nothing physical to undo.
                logger.debug(
                    "Reservation %s not released during checkout: %s",
                    reservation.id,
                    exc,
                )

        for product_id, quantity in sorted(needed.items()):
            covered = 0
            for reservation in reservations_by_product.pop(product_id, []):
                if covered >= quantity:
                    # Surplus hold (e.g. a duplicate reserve call) — free it.
                    _release(reservation)
                elif reservation.is_expired:
                    # An expired hold cannot be converted (convert_reservation_
                    # to_sale rejects expired reservations). Release it and let
                    # the shortfall decrement below take the amount still owed
                    # from physical stock — a lapsed TTL must not fail an
                    # otherwise-fulfillable checkout.
                    _release(reservation)
                elif covered + reservation.quantity <= quantity:
                    # Reservation fits within what is still owed — convert it.
                    StockManager.convert_reservation_to_sale(
                        reservation_id=reservation.id, order_id=order.id
                    )
                    converted_ids.append(reservation.id)
                    covered += reservation.quantity
                else:
                    # Reservation overshoots the remaining need — release it
                    # and let the shortfall decrement below take the exact
                    # amount still owed.
                    _release(reservation)
            if covered < quantity:
                StockManager.decrement_stock(
                    product_id=product_id,
                    quantity=quantity - covered,
                    order_id=order.id,
                    reason=f"Order {order.id} created from cart {cart.uuid}",
                    # Checkout path: don't oversell the shortfall against
                    # inventory other sessions have actively reserved (G0284).
                    respect_reservations=True,
                )

        # Release reservations for products no longer in the cart.
        for _product_id, leftovers in sorted(reservations_by_product.items()):
            for reservation in leftovers:
                _release(reservation)

        return converted_ids

    @classmethod
    def _lock_order_products(cls, *, cart_items, reservations, promo_result):
        """Lock every product the order is about to touch, in pk order.

        Called once, right after the cart lines are read and BEFORE the
        first ``OrderItem`` insert (see ``StockManager.lock_products`` for
        why that position matters). The set is everything the stock
        steps below will lock: the cart's lines, the cart session's
        reservations (a hold on a product no longer in the cart is
        released at checkout) and the free-gift products.
        """
        StockManager.lock_products(
            {item.product_id for item in cart_items}
            | {reservation.product_id for reservation in reservations}
            | {gift.product.id for gift in promo_result.gift_items}
        )

    @staticmethod
    def _shipping_choice(shipping_address: dict[str, Any]) -> ShippingChoice:
        return ShippingChoice.of(shipping_address)

    @classmethod
    def _resolve_live_shipping(
        cls, cart, *, user, shipping_address: dict[str, Any]
    ) -> None:
        """Have the carrier's live price resolved BEFORE any row is locked.

        Both creation paths call it ahead of their transaction. A carrier
        call can take seconds, and inside the transaction it would hold
        the cart row and whatever the quote locks (promotions, the
        customer, gift cards) for that long, which every checkout sharing
        one of them waits behind. The locked quote then reads the answer
        from the carrier adapter's cache and never calls out
        (``OrderQuoteService.resolve_live_shipping``).

        The cart's wholesale prices are bound first, as the locked half
        binds them, so both weigh the same parcel.
        """
        from b2b.services import B2BPricingService

        B2BPricingService.bind_cart(cart, user)
        OrderQuoteService.resolve_live_shipping(
            cart,
            user=user,
            email=shipping_address.get("email") or "",
            shipping=cls._shipping_choice(shipping_address),
        )

    @classmethod
    def _refuse_unpriced(
        cls,
        cart,
        *,
        user,
        pay_way,
        shipping_address: dict[str, Any],
        loyalty_points_to_redeem: int | None,
        gift_card_codes: list[str] | None,
    ) -> NoReturn:
        """Answer a creation whose locked quote could not price the
        delivery (``ShippingPriceUnresolvedError``): the parcel it weighed
        was not the one the live price was resolved for, so the cart
        changed in between, or the cached price expired.

        The transaction has rolled back and nothing is locked. The cart
        is priced again, which may ask the carrier, and the creation is
        refused as a changed total with that quote: the client shows it
        and orders again, now against a resolved price.
        """
        raise OrderTotalChangedError(
            OrderQuoteService.quote(
                cart,
                user=user,
                email=shipping_address.get("email") or "",
                pay_way=pay_way,
                shipping=cls._shipping_choice(shipping_address),
                loyalty_points=loyalty_points_to_redeem,
                gift_card_codes=gift_card_codes,
            )
        )

    @classmethod
    def _quote_under_lock(
        cls,
        cart,
        *,
        user,
        pay_way,
        shipping_address: dict[str, Any],
        loyalty_points_to_redeem: int | None,
        gift_card_codes: list[str] | None,
        expected_total: ExpectedTotal,
        expected_cart_digest: str | None,
    ) -> CheckoutQuote:
        """The total an order is created at, computed once.

        Runs inside the caller's order-create transaction, after the
        cart row is locked and its wholesale prices are bound.
        ``OrderQuoteService.quote(lock=True)`` locks the candidate
        Promotion rows, the customer's row (loyalty) and the gift cards
        until commit, so the usage limits it enforced, the points it
        priced and the card balances it planned cannot be raced, and a
        refused attached coupon raises ``InvalidCouponError`` rather
        than silently charging the undiscounted total.

        ``expected_total`` is the total the client showed the buyer.
        When it is not the amount due computed here, nothing is created:
        ``OrderTotalChangedError`` carries the quote. Prices, VAT,
        promotions, rates and pay-way fees can all change between the
        last render and the order, and this is the one place a change
        is caught for a pay way with no provider amount to compare.

        ``expected_cart_digest`` is the quote's ``cart_digest`` the
        client showed. When it is not this quote's, the order would be
        made of something else than what the buyer saw (a line swapped
        at the same price, another pickup point), and
        ``OrderCartChangedError`` refuses it first, whatever the total.
        """
        quote = OrderQuoteService.quote(
            cart,
            user=user,
            email=shipping_address.get("email") or "",
            pay_way=pay_way,
            shipping=cls._shipping_choice(shipping_address),
            loyalty_points=loyalty_points_to_redeem,
            gift_card_codes=gift_card_codes,
            lock=True,
        )
        if (
            expected_cart_digest is not None
            and expected_cart_digest != quote.cart_digest
        ):
            logger.info(
                "Order refused for cart %s: it changed since its quote",
                cart.uuid,
            )
            raise OrderCartChangedError(quote)
        if expected_total != quote.expected_total:
            logger.info(
                "Order refused for cart %s: the client expected %s, the "
                "total is %s",
                cart.uuid,
                expected_total,
                quote.expected_total,
            )
            raise OrderTotalChangedError(quote)
        return quote

    @staticmethod
    def _require_quoted_total(order: Order, quoted: Money) -> None:
        """Refuse an order whose rows do not add up to its quote.

        The quote is the total the buyer was shown and
        ``expected_total`` was compared with;
        ``Order.calculate_order_total_amount`` is what the order stores
        and is charged for. They are one figure because the order is
        built from the quote's rows. A charge added to one and not the
        other, or a cart line changed between the quote and the line
        lock, must fail here and roll the creation back, not surface as
        a payment that does not match its order.
        """
        if ExpectedTotal.of(order.amount_due) != ExpectedTotal.of(quoted):
            logger.error(
                "Order total %s is not its quote's %s; creation rolled back",
                order.amount_due,
                quoted,
            )
            raise InvalidOrderDataError(
                "the order's rows do not add up to the total it was quoted at"
            )

    @classmethod
    def _inject_promotion_gifts(cls, order, promo_result, target_currency):
        """Add FREE_GIFT entitlements as zero-price order lines.

        Stock is decremented directly (gifts never went through the
        reservation flow). An out-of-stock gift is SKIPPED, never a
        checkout blocker — the paid goods must not be held hostage by
        a promotional freebie; the skip is recorded for ops.
        """
        if not promo_result.gift_items:
            return
        from order.inventory.stock import StockManager

        skipped = []
        for gift in promo_result.gift_items:
            try:
                StockManager.decrement_stock(
                    product_id=gift.product.id,
                    quantity=gift.quantity,
                    order_id=order.id,
                    reason="promotion_gift",
                    # Same rule as the paid-line shortfall below (G0284):
                    # a free gift must not be taken out of stock another
                    # session is actively holding.
                    respect_reservations=True,
                )
            except Exception as exc:
                logger.warning(
                    "Promotion gift %s x%s skipped for order %s "
                    "(stock decrement failed: %s)",
                    gift.product.id,
                    gift.quantity,
                    order.id,
                    exc,
                )
                skipped.append(gift.product.id)
                continue
            OrderItem.objects.create(
                order=order,
                product=gift.product,
                quantity=gift.quantity,
                price=Money(0, target_currency),
                vat_rate=gift.product.vat_percent,
            )
        if skipped:
            order.metadata["promotion_gifts_skipped"] = skipped

    @classmethod
    def _snapshot_line_discounts(
        cls, order_items: dict[int, OrderItem], promo_result, loyalty_discount
    ) -> None:
        """Freeze where the order's discounts landed on its lines.

        Each promotion's money goes to the lines the engine says it
        covers (``CartDiscountResult.line_discounts``, keyed by cart item
        pk); the loyalty redemption is order-wide and is spread over the
        value that is left on every line. The invoice and the myDATA
        payload read these snapshots (``order.checkout.discounts``), so a coupon
        for one category can never lower the taxable value of a line of
        another. The gift-card amount is payment and is not allocated.
        """
        line_discounts = promo_result.line_discounts
        if line_discounts.keys() - order_items.keys():
            raise InvalidOrderDataError(
                str(
                    _(
                        "A promotion discounts a cart line that is not "
                        "part of the order."
                    )
                )
            )
        lines = list(order_items.values())
        add_line_discounts(
            lines,
            {
                order_items[cart_pk].pk: amount
                for cart_pk, amount in line_discounts.items()
            },
        )
        if loyalty_discount.amount > 0:
            add_line_discounts(
                lines,
                spread_by_remaining_value(lines, loyalty_discount.amount),
            )

    @staticmethod
    def order_for_agent_checkout(agent_checkout: UUID) -> Order | None:
        """The order this agent checkout placed, if it placed one.

        The agent gateway names its checkout on order creation, and a
        checkout places one order (``order_agent_checkout_unique``): a
        repeat of its creation, a retry after an answer that never
        arrived or a second call that raced the first, is answered with
        this order. Deleted orders count: the checkout placed it, and
        deleting it does not let the checkout place another.

        Read by ``OrderViewSet.create`` before anything else (the
        first order may have emptied or deleted the cart), and under the
        cart row lock by ``create_order_from_cart``, where two calls of
        one checkout serialise and the second finds the first's committed
        order.
        """
        return (
            Order.objects.all_with_deleted()
            .filter(agent_checkout=agent_checkout)
            .first()
        )

    @staticmethod
    def _enforce_unpaid_order_limit(*, pay_way, user, email: str) -> None:
        """Refuse a new online-payment order when the customer already has
        ``ORDER_MAX_UNPAID_ONLINE_ORDERS`` waiting for their payment.

        Such an order holds its stock until it is paid or auto-canceled
        (``auto_cancel_stuck_pending_orders``), so without a bound anyone
        can lock stock by starting checkouts they never pay. The customer
        is the account, or the email for a guest. Per store: the setting
        is a tenant ``Setting``. Offline methods are not counted (they are
        expected to sit PENDING until the carrier remits). ``0`` turns it
        off. Counted under the cart lock, so two carts of one customer
        racing can overshoot by the number in flight, not without bound.
        """
        if PaySettlement(pay_way.settlement) != PaySettlement.ONLINE:
            return
        limit = int(store_setting("ORDER_MAX_UNPAID_ONLINE_ORDERS"))
        if limit <= 0:
            return
        waiting = Order.objects.holding_stock_for_online_payment()
        if user is not None and user.is_authenticated:
            waiting = waiting.filter(user=user)
        elif email:
            waiting = waiting.filter(user__isnull=True, email__iexact=email)
        else:
            return
        if waiting.count() >= limit:
            raise UnpaidOrderLimitError(limit)

    @classmethod
    def _resume_same_checkout(
        cls,
        cart,
        *,
        user,
        pay_way,
        shipping_address: dict[str, Any],
        loyalty_points_to_redeem: int | None,
        gift_card_codes: list[str] | None,
        expected_total: ExpectedTotal,
        expected_cart_digest: str | None,
    ) -> Order | None:
        """The cart's unpaid online order when this checkout is the one that
        made it (``order.checkout.resume``), else ``None``.

        A shopper who goes back and orders the same thing again would
        otherwise place a second order holding the same stock, with its own
        payment link. The checkout is compared by its fingerprint, which
        needs its quote: priced here, under the cart lock, only when the
        cart has such an order. A quote that refuses (another total, other
        contents) is a changed checkout, refused again by the creation that
        follows, in its own order of checks.
        """
        if PaySettlement(pay_way.settlement) != PaySettlement.ONLINE:
            return None
        candidate = unpaid_order_of_cart(cart, pay_way=pay_way)
        if candidate is None:
            return None
        from b2b.services import B2BPricingService

        B2BPricingService.bind_cart(cart, user)
        try:
            quote = cls._quote_under_lock(
                cart,
                user=user,
                pay_way=pay_way,
                shipping_address=shipping_address,
                loyalty_points_to_redeem=loyalty_points_to_redeem,
                gift_card_codes=gift_card_codes,
                expected_total=expected_total,
                expected_cart_digest=expected_cart_digest,
            )
        except OrderServiceError:
            # Another total, other contents, a coupon no longer valid: not
            # the checkout that made the order. The creation that follows
            # answers it, in its own order of checks.
            return None
        fingerprint = checkout_fingerprint(
            quote,
            pay_way=pay_way,
            shipping_address=shipping_address,
            loyalty_points=loyalty_points_to_redeem,
            gift_card_codes=gift_card_codes,
        )
        if candidate.metadata.get(FINGERPRINT_KEY) != fingerprint:
            return None
        logger.info(
            "Checkout of cart %s resumes its unpaid order %s",
            cart.uuid,
            candidate.id,
        )
        return candidate

    @staticmethod
    def _refuse_agent_order_on_unpaid_cart(
        cart, attribution: AttributionInput | None
    ) -> None:
        """Refuse an agent gateway order from a cart whose earlier order
        still awaits its online payment.

        Such an order keeps its cart until the payment lands
        (``Order.awaits_online_payment``), so the same lines can be
        ordered again. A person does that on purpose ("Back, then order
        again"), and an order without a believed agent protocol is not
        looked at here. Through the gateway it is a platform that lost
        its checkout or sent it twice: a second order for the same goods
        the buyer never asked for, whatever its own pay way.

        The earlier order is any order of this cart that still awaits its
        online payment (``OrderQuerySet.awaiting_online_payment``), an
        agent's or not.

        Called by ``create_order_from_cart`` under the cart row lock, so
        two agent orders racing for one cart are judged one after the
        other and the second finds the first's committed order.
        """
        if attribution is None or not attribution.from_agent:
            return
        waiting_id = (
            Order.objects.awaiting_online_payment()
            .filter(
                metadata__contains={
                    "cart_snapshot": {"cart_uuid": str(cart.uuid)}
                }
            )
            .values_list("id", flat=True)
            .first()
        )
        if waiting_id is None:
            return
        logger.warning(
            "Refused an agent order on cart %s: order %s awaits its "
            "online payment",
            cart.uuid,
            waiting_id,
        )
        raise CartHasUnpaidOrderError

    @classmethod
    def create_order_from_cart(
        cls,
        *,
        cart,
        shipping_address: dict[str, Any],
        pay_way,
        user=None,
        loyalty_points_to_redeem: int | None = None,
        gift_card_codes: list[str] | None = None,
        meta_context: dict[str, Any] | None = None,
        attribution: AttributionInput | None = None,
        legal_acceptance: LegalAcceptance,
        expected_total: ExpectedTotal,
        expected_cart_digest: str | None = None,
        agent_checkout: UUID | None = None,
    ) -> Order:
        """
        Create an order from a cart: the one creation path.

        Every pay way creates the order first. An offline one (COD, bank
        transfer) is collected later; an online one (Viva Wallet,
        Stripe) is created PENDING with no payment, and the shopper pays
        it afterwards on the provider's hosted page. No money moves
        before the locked quote below has run every refusal. It performs
        the following steps:
        0. Locks the cart; returns the order this agent checkout already
           created, if any (idempotent)
        1. Validates cart items still exist and prices match
        2. Validates shipping address completeness
        3. Gets or creates stock reservations for cart session
        4. Creates Order with status=PENDING, payment_status=PENDING
        5. Creates OrderItems from CartItems
        6. Converts stock reservations to decrements via StockManager
        7. Sets payment_id for offline payments (skipped for online providers)
        8. Clears cart
        9. Returns order in PENDING status

        Args:
            cart: Cart object containing items to order
            shipping_address: Dictionary with shipping address fields
            pay_way: PayWay object for payment method
            user: Optional UserAccount (None for guest orders)

        Returns:
            Order: Created order in PENDING status

        Raises:
            InvalidOrderDataError: If validation fails
            InsufficientStockError: If stock unavailable
            OrderTotalChangedError: If ``expected_total`` is not the
                amount due computed under lock
            OrderCartChangedError: If ``expected_cart_digest`` is not the
                cart digest computed under lock
            AgentCheckoutOrderedError: If another call of
                ``agent_checkout`` committed its order first

        Example:
            >>> order = OrderService.create_order_from_cart(
            ...     cart=cart,
            ...     shipping_address={
            ...         'first_name': 'John',
            ...         'last_name': 'Doe',
            ...         'email': 'john@example.com',
            ...         'street': 'Main St',
            ...         'street_number': '123',
            ...         'city': 'Athens',
            ...         'zipcode': '12345',
            ...         'country_id': 'GR',
            ...         'phone': '+30123456789'
            ...     },
            ...     pay_way=pay_way,
            ...     user=user
            ... )
        """
        cls._resolve_live_shipping(
            cart, user=user, shipping_address=shipping_address
        )
        try:
            return cls._create_order_locked(
                cart=cart,
                shipping_address=shipping_address,
                pay_way=pay_way,
                user=user,
                loyalty_points_to_redeem=loyalty_points_to_redeem,
                gift_card_codes=gift_card_codes,
                meta_context=meta_context,
                attribution=attribution,
                legal_acceptance=legal_acceptance,
                expected_total=expected_total,
                expected_cart_digest=expected_cart_digest,
                agent_checkout=agent_checkout,
            )
        except ShippingPriceUnresolvedError:
            cls._refuse_unpriced(
                cart,
                user=user,
                pay_way=pay_way,
                shipping_address=shipping_address,
                loyalty_points_to_redeem=loyalty_points_to_redeem,
                gift_card_codes=gift_card_codes,
            )

    @classmethod
    @transaction.atomic
    def _create_order_locked(
        cls,
        *,
        cart,
        shipping_address: dict[str, Any],
        pay_way,
        user,
        loyalty_points_to_redeem: int | None,
        gift_card_codes: list[str] | None,
        meta_context: dict[str, Any] | None,
        attribution: AttributionInput | None,
        legal_acceptance: LegalAcceptance,
        expected_total: ExpectedTotal,
        expected_cart_digest: str | None,
        agent_checkout: UUID | None,
    ) -> Order:
        """The locked half of ``create_order_from_cart``: one
        transaction, no carrier call (the live price was resolved before
        it opened; one the cache does not hold raises
        ``ShippingPriceUnresolvedError``)."""
        try:
            from cart.models import Cart

            # Lock the Cart row immediately so concurrent checkouts on the
            # same cart are serialised.  Must happen before any reads so
            # validate_cart_for_checkout sees the locked snapshot.
            cart = Cart.objects.select_for_update().get(pk=cart.pk)

            # A repeat of this agent checkout answers with its order,
            # before the refusals below: that order took the stock, and
            # the cart it kept or emptied would refuse the repeat.
            if agent_checkout is not None:
                existing_order = cls.order_for_agent_checkout(agent_checkout)
                if existing_order is not None:
                    return existing_order

            cls._refuse_agent_order_on_unpaid_cart(cart, attribution)

            # The same checkout made again returns the cart's unpaid order
            # and its payment link (#694), before the cart is validated:
            # that order holds the cart's stock, the last units included.
            resumed = cls._resume_same_checkout(
                cart,
                user=user,
                pay_way=pay_way,
                shipping_address=shipping_address,
                loyalty_points_to_redeem=loyalty_points_to_redeem,
                gift_card_codes=gift_card_codes,
                expected_total=expected_total,
                expected_cart_digest=expected_cart_digest,
            )
            if resumed is not None:
                return resumed

            # Step 1: Validate cart for checkout
            validation_result = cls.validate_cart_for_checkout(cart)
            if not validation_result.get("valid", False):
                raise CartNotReadyError.from_validation(validation_result)

            # Step 2: Validate shipping address
            cls.validate_shipping_address(shipping_address, pay_way=pay_way)

            # Step 2.3: An online order holds its stock until it is paid
            # or auto-canceled; bound how many one customer can hold.
            cls._enforce_unpaid_order_limit(
                pay_way=pay_way,
                user=user,
                email=shipping_address.get("email") or "",
            )

            # Step 2.4: Re-bind wholesale pricing on the LOCKED cart —
            # the refetch above produced a fresh instance. Everything
            # below (promotion gating, totals, shipping threshold,
            # payment fee, item price snapshots) must see the same
            # prices the cart preview showed.
            from b2b.services import B2BPricingService

            B2BPricingService.bind_cart(cart, user)

            # Wholesale minimum-order-value gate (standard B2B term).
            # The order exists before any payment, so this refusal is
            # the only one needed.
            unmet_minimum = B2BPricingService.min_order_value_unmet(cart)
            if unmet_minimum is not None:
                raise InvalidOrderDataError(
                    str(
                        _(
                            "The order total is below this wholesale "
                            "tier's minimum of {minimum}."
                        ).format(minimum=unmet_minimum)
                    )
                )

            # Step 2.5: The total, once, under lock (promotions, the
            # customer's points, the gift cards): what the client's
            # expected total is compared with, and what the order below
            # is built from. The payment is opened for this order's own
            # amount afterwards, so no provider amount is compared here.
            quote = cls._quote_under_lock(
                cart,
                user=user,
                pay_way=pay_way,
                shipping_address=shipping_address,
                loyalty_points_to_redeem=loyalty_points_to_redeem,
                gift_card_codes=gift_card_codes,
                expected_total=expected_total,
                expected_cart_digest=expected_cart_digest,
            )
            charges = quote.charges()
            promo_result = quote.promotion

            # Step 3: Get stock reservations for cart session
            # Reservations are identified by cart.uuid (session_id)
            reservations = list(
                StockReservation.objects.filter(
                    session_id=str(cart.uuid), consumed=False
                ).select_related("product")
            )

            # Validate we have reservations for all cart items.
            # Materialize the queryset eagerly: the order_created signal
            # handler schedules ``cart.delete()`` via ``transaction.on_commit``;
            # if anything causes that callback to fire before this loop
            # executes, the lazy queryset would resolve to zero rows.
            # Also lock the CartItem rows within the same transaction to
            # prevent concurrent mutations while we process them.
            # ``of=("self",)``: without it the join also locked every
            # product row, in this cart's line order, and two carts that
            # share products in different line orders deadlocked. The
            # products are locked next, in pk order.
            cart_items = list(
                cart.items.select_for_update(of=("self",)).select_related(
                    "product"
                )
            )
            cls._lock_order_products(
                cart_items=cart_items,
                reservations=reservations,
                promo_result=promo_result,
            )

            # Reservations (active, expired, or duplicated) are reconciled
            # against the cart in Step 6 via _consume_stock_for_order, which
            # releases every hold and decrements physical stock by the cart
            # quantity. Expired holds therefore need no special handling
            # here: a lapsed hold whose stock was taken by another session
            # simply fails the decrement and rolls the whole checkout back.

            # Step 4: Create Order with PENDING status
            # Determine target currency from shipping address or default
            target_currency = settings.DEFAULT_CURRENCY

            # Build order data
            order_data = {
                "user": user if user and user.is_authenticated else None,
                "pay_way": pay_way,
                "payment_id": None,  # Will be set after order creation
                "payment_status": PaymentStatus.PENDING,
                "status": OrderStatus.PENDING,
                "agent_checkout": agent_checkout,
                # Shipping address fields
                "first_name": shipping_address.get("first_name"),
                "last_name": shipping_address.get("last_name"),
                "email": shipping_address.get("email"),
                "street": shipping_address.get("street"),
                "street_number": shipping_address.get("street_number"),
                "address_line_2": shipping_address.get("address_line_2", ""),
                "city": shipping_address.get("city"),
                "zipcode": shipping_address.get("zipcode"),
                "country_id": shipping_address.get("country_id"),
                "region_id": shipping_address.get("region_id"),
                "phone": shipping_address.get("phone"),
                "customer_notes": shipping_address.get("customer_notes", ""),
                # B2B billing identity — empty for retail (Tier A),
                # populated for Τιμολόγιο Πώλησης (Tier B). The
                # serializer already normalised these (stripped
                # EL/GR prefix, uppercased country).
                "billing_vat_id": shipping_address.get("billing_vat_id", ""),
                "billing_country": shipping_address.get("billing_country", ""),
                "billing_company_name": shipping_address.get(
                    "billing_company_name", ""
                ),
                "billing_tax_office": shipping_address.get(
                    "billing_tax_office", ""
                ),
                "billing_activity": shipping_address.get(
                    "billing_activity", ""
                ),
                "billing_street": shipping_address.get("billing_street", ""),
                "billing_street_number": shipping_address.get(
                    "billing_street_number", ""
                ),
                "billing_city": shipping_address.get("billing_city", ""),
                "billing_zipcode": shipping_address.get("billing_zipcode", ""),
                "document_type": (
                    shipping_address.get("document_type")
                    or OrderDocumentTypeEnum.RECEIPT
                ),
            }

            # Shipping, its carrier and the pay-way fee are the quote's:
            # the parcel it weighed (gifts included) is the one the
            # shipment is created for.
            parcel_items = list(quote.parcel_items)
            order_data["shipping_price"] = charges.shipping.price
            order_data["payment_method_fee"] = charges.payment_fee.amount

            cls._assign_shipping_provider(order_data, quote)
            cls._seed_language_code(order_data)
            cls._seed_estimated_delivery(order_data)

            order = Order.objects.create(**order_data)
            OrderAttributionService.record(
                order, attribution or AttributionInput(), cart=cart
            )
            OrderLegalAcceptanceService.record(order, legal_acceptance)

            # Set payment_id for offline settlements only. Providers
            # that settle by hosted redirect get their payment_id from
            # the webhook once the shopper has actually paid.
            if PaySettlement(pay_way.settlement) != PaySettlement.ONLINE:
                order.payment_id = f"offline_{order.uuid}"
                order.save(update_fields=["payment_id"])

            # Provider-agnostic shipment row creation via the registry.
            from shipping.services import ShippingService

            ShippingService.create_shipment_row_for_order(
                order, payload=shipping_address, items=parcel_items
            )

            # Initialize metadata with cart snapshot
            order.metadata = {
                "cart_snapshot": {
                    "cart_id": cart.id,
                    "cart_uuid": str(cart.uuid),
                    "total_items": cart.total_items,
                    "total_price": str(cart.total_price.amount),
                    "currency": str(cart.total_price.currency),
                },
                # Derived, not hardcoded. The ``is_online_payment`` guard
                # further up governs only ``payment_id``; this dict was
                # assigned unconditionally, so every card order recorded
                # itself as offline — 78 of them in production, including
                # Viva charges carrying a real provider payment_id.
                # Nothing reads the key (myDATA's ``_pick_payment_type``
                # works off the invoice's payment_id and pay_way), so it
                # misled no logic — it misled whoever reads order
                # metadata to diagnose a payment.
                # The settlement itself, not a two-way summary of
                # it: "offline" could not tell cash to a courier
                # from a card at a locker terminal, which is the
                # distinction the whole shipping layer turns on.
                "payment_type": pay_way.settlement,
            }
            if PaySettlement(pay_way.settlement) == PaySettlement.ONLINE:
                # What a repeat of this checkout is recognised by
                # (``_resume_same_checkout``); an HMAC, and personal
                # (``Order.PERSONAL_METADATA``).
                order.metadata[FINGERPRINT_KEY] = checkout_fingerprint(
                    quote,
                    pay_way=pay_way,
                    shipping_address=shipping_address,
                    loyalty_points=loyalty_points_to_redeem,
                    gift_card_codes=gift_card_codes,
                )
            # Wholesale audit: which group priced this order. The line
            # prices are already snapshotted on OrderItem rows — this
            # records WHY they differ from retail.
            b2b_context = getattr(cart, "_b2b_pricing", None)
            if b2b_context is not None:
                order.metadata["b2b_pricing"] = {
                    "group_id": b2b_context.group.pk,
                    "group_name": b2b_context.group.name,
                    "discount_percent": str(b2b_context.group.discount_percent),
                }
            sanitised_meta = cls._sanitise_meta_context(meta_context)
            if sanitised_meta:
                order.metadata["meta"] = sanitised_meta

            # Step 5: Create OrderItems from CartItems
            # Cart item pk -> the line written for it: the promotion
            # engine reports each promotion's money per cart item, and
            # the order lines snapshot it below.
            order_items: dict[int, OrderItem] = {}
            for cart_item in cart_items:
                product = cart_item.product
                quantity = cart_item.quantity

                # Validate product still exists and has stock
                if not product:
                    raise InvalidOrderDataError(
                        str(_("Product is required for order items"))
                    )

                if quantity <= 0:
                    raise InvalidOrderDataError(
                        str(
                            _(
                                "Invalid quantity {quantity} for product {product_id}"
                            ).format(
                                quantity=quantity,
                                product_id=product.id,
                            )
                        )
                    )

                # Get the line price — bound-aware: a wholesale cart's
                # items carry their group price — and convert currency
                # if needed
                product_price = cart_item.final_price
                if product_price.currency != target_currency:
                    item_price = Money(product_price.amount, target_currency)
                else:
                    item_price = product_price

                _log_price_drift_if_needed(cart_item, item_price)

                # bulk_create cannot be used here: the post_save signal
                # on OrderItem (handle_order_item_post_save) writes an
                # OrderHistory audit note for each new item and is the
                # canonical creation hook. bulk_create skips all signals.
                # The loop is already inside @transaction.atomic so all
                # inserts land in one DB round-trip on commit.
                order_items[cart_item.pk] = OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=quantity,
                    price=item_price,
                    # The rate the cart backed this price's VAT out of —
                    # frozen on the line so the invoice and myDATA split
                    # ``price`` at it, whatever the catalogue says later.
                    vat_rate=cart_item.vat_percent,
                    recommendation_impression_id=(
                        cart_item.recommendation_impression_id
                    ),
                )

            # Step 6: Reconcile reservations against the cart and decrement
            # physical stock exactly once per product (guards against
            # duplicate/missing/expired reservations double-decrementing,
            # over-selling, or skipping the decrement).
            reservation_ids = cls._consume_stock_for_order(
                order=order,
                cart=cart,
                cart_items=cart_items,
                reservations=reservations,
            )

            # Store reservation IDs in order metadata
            order.metadata["stock_reservation_ids"] = reservation_ids

            # FREE_GIFT entitlements become zero-price lines (stock
            # decremented directly; an out-of-stock gift is skipped,
            # never a blocker).
            cls._inject_promotion_gifts(order, promo_result, target_currency)

            # Step 6.5: Apply loyalty points redemption if requested
            loyalty_discount = Money(0, target_currency)
            if quote.loyalty_points:
                try:
                    from loyalty.services import LoyaltyService

                    # Redeem points and get discount amount
                    discount_amount = LoyaltyService.redeem_points(
                        user=user,
                        points_amount=quote.loyalty_points,
                        currency=str(target_currency),
                        order=order,
                        # The cap the quote priced it under: the goods
                        # after promotions, never shipping or a fee.
                        max_discount=quote.free_threshold_value.amount,
                    )
                    loyalty_discount = Money(discount_amount, target_currency)

                    # Store loyalty redemption in order metadata
                    order.metadata["loyalty_redemption"] = {
                        "points_redeemed": quote.loyalty_points,
                        "discount_amount": str(discount_amount),
                        "currency": str(target_currency),
                    }

                    logger.info(
                        "Applied loyalty discount of %s %s (%s points) to order %s",
                        discount_amount,
                        target_currency,
                        quote.loyalty_points,
                        order.id,
                    )
                except ValidationError:
                    raise
                except Exception as e:
                    logger.exception(
                        "Failed to apply loyalty discount to order %s",
                        order.id,
                    )
                    # Fail loud. No provider amount guards order creation,
                    # so swallowing this charged the shopper the undiscounted
                    # total while the checkout sidebar had shown the
                    # discount and the points stayed unspent: a refused
                    # discount must never charge more than was shown.
                    # Raising rolls
                    # the whole creation back so the shopper retries and
                    # sees a price that matches what they are charged.
                    raise InvalidOrderDataError(
                        str(_("Loyalty redemption failed."))
                    ) from e

            # Step 6.6: Record promotion redemptions (rows + metadata)
            # for the evaluation done under lock at Step 2.5.
            order.discount_amount = record_redemptions(order, promo_result)
            order.loyalty_discount = loyalty_discount
            cls._snapshot_line_discounts(
                order_items, promo_result, loyalty_discount
            )

            # Step 6.7: Record the gift-card plan the quote locked. The
            # cards are payment, not discount: they settled what was
            # still due after the promotion and loyalty deductions.
            from giftcard.services import GiftCardService

            order.gift_card_amount = GiftCardService.record_plan(
                charges.gift_cards, order
            )

            # Persist the deductions so they survive on the order, then
            # let calculate_order_total_amount() be the single authority
            # on what the customer owes. Subtracting here and leaving
            # the order's own total undiscounted is what let every
            # charge site bill the full amount while the points were
            # burnt.
            order.amount_due = order.calculate_order_total_amount()
            cls._require_quoted_total(order, charges.amount_due)
            order.save(
                update_fields=[
                    "discount_amount",
                    "discount_amount_currency",
                    "loyalty_discount",
                    "loyalty_discount_currency",
                    "gift_card_amount",
                    "gift_card_amount_currency",
                    "amount_due",
                    "amount_due_currency",
                    "metadata",
                ]
            )

            # Items, discounts and charges are final: keep what the
            # receipt will bill, so every sales figure reads it
            # (order.vat_snapshot).
            write_vat_lines(order)

            # The coupon's durable record is the redemption row — the
            # cart attachment has served its purpose.
            CouponService.clear_after_order(cart)

            # Step 6.8: Deductions covering the FULL total settle the
            # order right here — no provider ever gets involved (a
            # zero-amount PaymentIntent would be rejected anyway).
            # Gift cards are the usual cause; a 100% promotion or a
            # full loyalty redemption produces the same zero
            # remainder and settles identically. Anything LESS than
            # full coverage on an online pay way leaves the order
            # PENDING for the remainder: the payment is opened later
            # (``create_checkout_session``, or ``confirm_agent_payment``
            # for an agent's delegated card), for every provider alike.
            deductions_present = (
                order.gift_card_amount.amount > 0
                or order.discount_amount.amount > 0
                or order.loyalty_discount.amount > 0
            )
            fully_covered = deductions_present and order.amount_due.amount == 0
            if fully_covered:
                if order.gift_card_amount.amount > 0:
                    settle_id = f"GIFTCARD_{order.uuid}"
                    settle_method = "gift_card"
                else:
                    settle_id = f"DISCOUNT_{order.uuid}"
                    settle_method = "discount"
                order.mark_as_paid(
                    payment_id=settle_id,
                    payment_method=settle_method,
                )

            # Step 7: Clear cart
            # Keep the cart while the shopper still owes a hosted
            # payment — see Order.awaits_online_payment. It clears on
            # ``order_paid`` instead.
            if order.awaits_online_payment and not fully_covered:
                logger.info(
                    "Kept cart %s — order %s awaits online payment",
                    cart.uuid,
                    order.id,
                )
            else:
                cart.items.all().delete()
                logger.info("Cleared cart %s after order creation", cart.uuid)

            # Step 8: Dispatch shipment creation for true offline payments
            # (COD, Bank Transfer) and fully-gift-card-settled orders.
            # An online pay way (Viva Wallet, Stripe) defers dispatch to
            # the payment webhook so the courier voucher only mints after
            # the shopper actually pays.
            if (
                PaySettlement(pay_way.settlement) != PaySettlement.ONLINE
                or fully_covered
            ):
                cls._dispatch_shipment_creation_task(order)

            # Step 9: Return order in PENDING status
            logger.info(
                "Order %s created successfully from cart %s (%s)",
                order.id,
                cart.uuid,
                pay_way.provider_code or "offline",
            )

            return order

        except (
            ProductNotFoundError,
            InsufficientStockError,
            StockReservationError,
            InvalidCouponError,
            InvalidGiftCardError,
            InvalidOrderDataError,
            OrderTotalChangedError,
            OrderCartChangedError,
        ):
            raise
        except Exception as e:
            if (
                isinstance(e, IntegrityError)
                and agent_checkout is not None
                and _violated_constraint(e) == AGENT_CHECKOUT_UNIQUE_CONSTRAINT
            ):
                # Another call of this checkout, on another cart (one
                # cart's calls serialise on its lock), committed first.
                raise AgentCheckoutOrderedError(agent_checkout) from e
            logger.exception(
                "Unexpected error creating order from cart",
            )
            raise InvalidOrderDataError(
                _("Failed to create order: {error}").format(error=str(e))
            ) from e

    @classmethod
    def validate_cart_for_checkout(cls, cart) -> dict[str, Any]:
        """
        Validate cart is ready for checkout.

        Performs comprehensive validation of cart state including:
        - Cart is not empty
        - All products still exist
        - All products are in stock

        Args:
            cart: Cart object to validate

        Returns:
            dict: Validation results with structure:
                {
                    'valid': bool,
                    'errors': list[str],
                    'warnings': list[str],
                    'insufficient_stock': bool,
                }

            ``insufficient_stock`` says whether any error is a stock
            shortfall — the stable fact a client branches on, where
            ``errors`` is text in the request's language.

        Example:
            >>> result = OrderService.validate_cart_for_checkout(cart)
            >>> if not result['valid']:
            ...     print(f"Validation failed: {result['errors']}")
            >>> if result['warnings']:
            ...     print(f"Warnings: {result['warnings']}")
        """
        errors = []
        warnings = []
        insufficient_stock = False

        # Get cart items with optimized prefetching
        cart_items = cart.get_items()

        # Check 1: Cart not empty
        if not cart_items.exists():
            errors.append(_("Cart is empty"))
            return {
                "valid": False,
                "errors": errors,
                "warnings": warnings,
                "insufficient_stock": insufficient_stock,
            }

        # Check 2: All products exist, are active, and are in stock
        for cart_item in cart_items:
            product = cart_item.product

            # Check product exists
            if not product:
                errors.append(_("Product in cart no longer exists"))
                continue

            product_display_name = (
                product.safe_translation_getter("name", any_language=True) or ""
            )

            # Check product is active and not soft-deleted
            if not product.is_purchasable:
                errors.append(
                    _("Product '{product}' is no longer available").format(
                        product=product_display_name
                    )
                )
                continue

            # Check product is in stock (exclude this cart's own
            # reservations so they don't count against itself)
            available_stock = StockManager.get_available_stock(
                product.id,
                exclude_session_id=str(cart.uuid),
            )
            if available_stock < cart_item.quantity:
                insufficient_stock = True
                errors.append(
                    _(
                        "Product '{product}' has insufficient stock. "
                        "Available: {available}, Requested: {requested}"
                    ).format(
                        product=product_display_name,
                        available=available_stock,
                        requested=cart_item.quantity,
                    )
                )

        # Return validation results
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "insufficient_stock": insufficient_stock,
        }

    @classmethod
    def validate_shipping_address(
        cls,
        address: dict[str, Any],
        *,
        pay_way: Any | None = None,
    ) -> None:
        """
        Validate shipping address completeness.

        Validates that all required fields are present and properly formatted.
        Required fields:
        - first_name, last_name
        - email (valid format)
        - street, city, zipcode, and street_number for a home delivery
        - country_id
        - phone

        Args:
            address: Dictionary containing shipping address fields

        Raises:
            ValidationError: With field-specific errors if validation fails

        Example:
            >>> try:
            ...     OrderService.validate_shipping_address({
            ...         'first_name': 'John',
            ...         'last_name': 'Doe',
            ...         'email': 'john@example.com',
            ...         'street': 'Main St',
            ...         'street_number': '123',
            ...         'city': 'Athens',
            ...         'zipcode': '12345',
            ...         'country_id': 1,
            ...         'phone': '+30123456789'
            ...     })
            ... except ValidationError as e:
            ...     print(f"Validation failed: {e.message_dict}")
        """
        from django.core.validators import validate_email

        # Values are a mix: `_( ... )` lazy translations from this
        # method and plain `str` from the shipping provider's validator.
        # `StrOrPromise` is django-stubs-ext's public spelling for that.
        errors: dict[str, list[StrOrPromise]] = {}

        # Required fields
        required_fields = [
            "first_name",
            "last_name",
            "email",
            "street",
            "street_number",
            "city",
            "zipcode",
            "country_id",
            "phone",
        ]

        # A pickup point's catalogue address may hold its number in the
        # street line (``address_of_pickup_point``): only a home delivery
        # needs one of its own.
        if address.get("shipping_kind") == ShippingKind.PICKUP_POINT:
            required_fields.remove("street_number")

        # Check for missing required fields
        for field in required_fields:
            if not address.get(field):
                errors[field] = [_("This field is required")]

        # Carrier-specific validation runs through the registry below
        # (see ``ShippingService.validate_order_payload``) — each
        # provider adapter owns its own field-level rules so this
        # method stays carrier-agnostic.
        validation_payload = dict(address)

        provider_code = address.get("shipping_provider_code")
        kind_value = address.get("shipping_kind")
        if provider_code and kind_value:
            from shipping.exceptions import ShippingProviderNotFoundError
            from shipping.services import ShippingService

            try:
                provider_errors = ShippingService.validate_order_payload(
                    provider_code=provider_code,
                    kind=kind_value,
                    payload=validation_payload,
                )
            except ShippingProviderNotFoundError:
                errors["shipping_provider_code"] = [
                    _("Unknown shipping provider.")
                ]
            else:
                for field, messages in provider_errors.items():
                    errors.setdefault(field, []).extend(messages)

        # Validate email format if provided
        email = address.get("email")
        if email:
            try:
                validate_email(email)
            except ValidationError:
                errors["email"] = [_("Enter a valid email address")]

        # Validate phone format (basic check - not empty and reasonable length)
        phone = address.get("phone")
        if phone and (len(phone) < 8 or len(phone) > 20):
            errors["phone"] = [_("Enter a valid phone number")]

        # Validate country_id is a positive integer or valid string
        country_id = address.get("country_id")
        if country_id is not None:
            # Country ID can be either an integer or a string (alpha_2 code)
            if isinstance(country_id, str):
                # String country codes are valid (e.g., 'US', 'GR')
                if len(country_id) < 2:
                    errors["country_id"] = [
                        _("Country ID must be a valid country code")
                    ]
            else:
                try:
                    country_id = int(country_id)
                    if country_id <= 0:
                        errors["country_id"] = [
                            _("Country ID must be a positive integer")
                        ]
                except ValueError, TypeError:
                    errors["country_id"] = [
                        _("Country ID must be a valid integer or country code")
                    ]

        # Availability gate: an active ShippingRate must exist for the
        # resolved (provider, kind, country). Checked here — without
        # the weight cap, which isn't known until the cart total is
        # computed later in order creation — so an unavailable
        # combination (the common Cyprus case: no rate added yet)
        # surfaces as a field-scoped 400 before any stock or payment
        # work happens, rather than only when ``shipping_cost`` is
        # reached deep inside order creation.
        if not errors.get("shipping_provider_code") and not errors.get(
            "country_id"
        ):
            from shipping.exceptions import (
                ShippingRegionUnavailableError,
                ShippingUnavailableError,
            )
            from shipping.services import ShippingService

            # A pickup point is judged by its country; a home delivery by
            # the zone of its region too.
            region_id = (
                address.get("region_id")
                if kind_value == "home_delivery"
                else None
            )
            resolved_provider_code = provider_code
            if not resolved_provider_code and kind_value == "home_delivery":
                resolved_provider_code = (
                    ShippingService.resolve_home_delivery_provider(
                        address.get("country_id"), region_id=region_id
                    )
                )
            if resolved_provider_code and kind_value:
                try:
                    ShippingService.active_rate(
                        provider_code=resolved_provider_code,
                        kind=kind_value,
                        country_code=address.get("country_id"),
                        region_id=region_id,
                    )
                except ShippingRegionUnavailableError:
                    errors["shipping_provider_code"] = [
                        _(
                            "This shipping method does not deliver to "
                            "the selected region."
                        )
                    ]
                except ShippingUnavailableError:
                    errors["shipping_provider_code"] = [
                        _(
                            "This shipping method is not available for "
                            "the selected country."
                        )
                    ]

        # If there are errors, raise ValidationError
        if errors:
            raise ValidationError(errors)

    @classmethod
    def _apply_polled_payment_status(
        cls, order: Order, payment_status_enum: PaymentStatus
    ) -> None:
        """Record where a POLLED payment attempt stands, unless the order
        is settled.

        Polling reports the provider's view of the payment, which is not
        the order's view of the money. A Stripe PaymentIntent stays
        ``succeeded`` after the charge is refunded, so a settled order
        polled by anyone — including a customer opening the order page,
        which reaches this through the read-only ``payment_status``
        action — would be written back to COMPLETED and lose the refund.

        Same rule the webhook handlers already follow: a settled state is
        final, and the row is re-read under lock so a concurrent webhook
        either lands first or is seen.

        A poll records only an open payment's state
        (``_POLLED_STATUS_RECORDED``). A canceled attempt is a FAILED one:
        CANCELED is the payment state of a closed order
        (``Order.settle_unpaid_payment``), and on an open order it is
        settled, so the sweep never closed the order and its stock stayed
        reserved, a later success was ignored as stale, and a retry was the
        only way out (#697). FAILED keeps the order payable and is swept
        after ``ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES``. A refunded state
        is the ledger's to derive from the refunds it booked, so a poll
        never writes one.
        """
        recorded = _POLLED_STATUS_RECORDED.get(payment_status_enum)
        if recorded is None:
            logger.warning(
                "Not recording polled payment status %s for order %s: "
                "a poll records only an open payment's state",
                payment_status_enum,
                order.id,
            )
            return
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            if locked.payment_status in SETTLED_PAYMENT_STATUSES:
                logger.warning(
                    "Ignoring polled payment status %s for order %s: "
                    "payment_status already settled as %s",
                    payment_status_enum,
                    order.id,
                    locked.payment_status,
                )
                order.payment_status = locked.payment_status
                return
            if recorded == locked.payment_status:
                order.payment_status = locked.payment_status
                return
            logger.info(
                "Updating order %s payment status from %s to %s "
                "(provider reports %s)",
                order.id,
                locked.payment_status,
                recorded,
                payment_status_enum,
            )
            locked.payment_status = recorded
            locked.save(update_fields=["payment_status"])
            order.payment_status = recorded

    @classmethod
    def get_payment_status(
        cls,
        order: Order,
        update_order: bool = True,
    ) -> tuple[PaymentStatus, dict[str, Any]]:
        """Ask the provider where the order's payment stands and record it.

        Returns what the provider reported. Raises
        ``PaymentStatusUnavailableError``, having written nothing, when the
        provider could not be asked: the order keeps its state and the
        caller asks again later.
        """
        if not order.payment_id:
            raise PaymentError(_("This order has no payment ID."))

        if not order.pay_way:
            raise PaymentError(
                _("This order has no payment method configured.")
            )

        from order.payments.payment import get_payment_provider

        provider = get_payment_provider(order.pay_way.provider_code)
        payment_status_enum, status_data = provider.get_payment_status(
            payment_id=order.payment_id
        )

        if update_order and payment_status_enum != order.payment_status:
            if payment_status_enum == PaymentStatus.COMPLETED:
                cls.settle_polled_completion(order, status_data)
            else:
                cls._apply_polled_payment_status(order, payment_status_enum)
        if update_order and payment_status_enum == PaymentStatus.COMPLETED:
            # An order paid already may still be unconfirmed: the sight of
            # its payment confirms it (a no-op otherwise).
            OrderLifecycleService.confirm(order)

        return payment_status_enum, status_data

    @classmethod
    def settle_polled_completion(
        cls, order: Order, status_data: dict[str, Any]
    ) -> None:
        """Settle an order the provider reports as paid, through the same
        handler the webhook uses.

        A poll used to write ``COMPLETED`` straight onto the row: no
        amount check, no ledger charge, no status advance and no
        confirmation, and whatever intent the order carried was believed.
        Now only a Stripe intent that no gift-card purchase holds settles,
        and it does so through ``handle_payment_succeeded`` (amount
        compared with the order, charge booked, status advanced), then
        announces exactly as the webhook does
        (``order_stripe_payment_polled``). Any other provider's
        settlement is its own webhook's.
        """
        from giftcard.models import GiftCardPurchase

        if (
            order.pay_way.provider_code != StripePaymentProvider.code
            or GiftCardPurchase.objects.filter(
                payment_id=order.payment_id
            ).exists()
        ):
            logger.info(
                "Not settling order %s from a poll: its payment is settled "
                "by its provider's webhook",
                order.id,
            )
            return
        amount = status_data.get("amount")
        currency = status_data.get("currency")
        if amount is None or not currency:
            return
        outcome = cls.handle_payment_succeeded(
            order.payment_id,
            captured=Money(Decimal(str(amount)), currency),
        )
        order_stripe_payment_polled.send(
            sender=cls, outcome=outcome, payment_intent_id=order.payment_id
        )
        if outcome is not None:
            order.refresh_from_db()

    @classmethod
    @transaction.atomic
    def add_tracking_info(
        cls,
        order: Order,
        tracking_number: str,
        shipping_carrier: str,
        auto_update_status: bool = True,
    ) -> Order:
        order.add_tracking_info(tracking_number, shipping_carrier)

        if not auto_update_status:
            return order

        # Not "final" — SHIPPED is mid-flight, and the documented
        # terminal set is COMPLETED/CANCELED/REFUNDED. These are the
        # statuses that need no advance when tracking lands: already at
        # or past SHIPPED, or ended somewhere this must not walk out of.
        no_advance_needed = {
            OrderStatus.SHIPPED,
            OrderStatus.DELIVERED,
            OrderStatus.COMPLETED,
            OrderStatus.RETURNED,
            OrderStatus.REFUNDED,
        }

        if order.status in no_advance_needed:
            pass
        elif order.status == OrderStatus.PROCESSING:
            OrderLifecycleService.update_order_status(
                order, OrderStatus.SHIPPED
            )
        elif order.status == OrderStatus.PENDING:
            # Two-hop chain → suppress the intermediate PROCESSING
            # email + WS toast. The customer cares that the order has
            # SHIPPED; getting "your order is being prepared" then
            # "your order is shipped" within the same second is the
            # admin path's only remaining duplicate. The PROCESSING
            # transition still fires the signal + OrderHistory row,
            # only the user-visible dispatches are skipped.
            OrderLifecycleService.suppress_customer_status_notifications(
                order, OrderStatus.PROCESSING.value
            )
            OrderLifecycleService.update_order_status(
                order, OrderStatus.PROCESSING
            )
            OrderLifecycleService.update_order_status(
                order, OrderStatus.SHIPPED
            )
        else:
            try:
                OrderLifecycleService.update_order_status(
                    order, OrderStatus.SHIPPED
                )
            except ValueError, InvalidStatusTransitionError:
                logger.warning(
                    "Could not update order %s to SHIPPED status from %s",
                    order.id,
                    order.status,
                )

        order.refresh_from_db()
        return order

    @staticmethod
    def _assign_shipping_provider(
        order_data: dict[str, Any], quote: CheckoutQuote
    ) -> None:
        """Give the order the delivery its quote priced.

        Sets ``shipping_kind`` and the ``shipping_provider`` row of the
        carrier the quote resolved (``CheckoutQuote.shipping_choice``):
        the one it charged for, a home delivery that named none included,
        so the price and the carrier always agree.
        """
        from shipping.models import ShippingProvider

        choice = quote.shipping_choice
        if choice is None:
            raise ValueError("a quote without a delivery has no carrier")
        code = choice.provider_code
        kind = choice.kind

        if code:
            provider = ShippingProvider.objects.filter(code=code).first()
            if provider is None:
                logger.warning(
                    "Unknown shipping_provider_code=%r — leaving Order "
                    "unlinked.",
                    code,
                )
            else:
                order_data["shipping_provider"] = provider

        order_data["shipping_kind"] = kind

    @staticmethod
    def _seed_estimated_delivery(order_data: dict[str, Any]) -> None:
        """Fix ``Order.estimated_delivery`` from the chosen rate at create.

        Runs after :meth:`_assign_shipping_provider`. Left unset when no
        provider resolved, the rate advertises no estimate, or the
        stored ``DISPATCH_CUTOFF`` is unreadable.
        """
        from shipping.delivery import configured_cutoff, estimated_delivery
        from shipping.zones import destination_rates

        provider = order_data.get("shipping_provider")
        country_id = order_data.get("country_id")
        if provider is None or not country_id:
            return
        # The rate of the zone the order's destination resolves to: the
        # islands' own delivery days, where they have their own rate.
        rate = (
            destination_rates(country_id, order_data.get("region_id"))
            .filter(
                provider=provider,
                kind=order_data["shipping_kind"],
                is_active=True,
            )
            .first()
        )
        try:
            cutoff = configured_cutoff()
        except ValueError:
            # Advisory estimate inside the create-order transaction: a
            # bad stored setting must not lose the order. No date is
            # better than one computed from an invented cutoff.
            logger.exception("Estimated delivery skipped")
            return
        order_data["estimated_delivery"] = estimated_delivery(
            rate, timezone.now(), cutoff
        )

    @staticmethod
    def _seed_language_code(order_data: dict[str, Any]) -> None:
        """Capture the active locale into ``Order.language_code`` at create.

        ``Order.language_code`` exists for the email tasks
        (``send_order_confirmation_email``, ``send_order_status_update_
        email``, etc.) — they activate ``translation.override(get_order_
        language(order))`` before rendering. Without seeding here the
        column defaults to ``settings.LANGUAGE_CODE`` ("el") regardless
        of what locale the request was in, so a German shopper would
        get Greek emails for the rest of their order's lifecycle.

        Pulled from the request language
        ``core.middleware.locale.RequestLanguageMiddleware`` activates
        from the storefront's ``X-Language`` (``active_language_code``),
        so views don't need to thread a ``request`` argument through. A
        stray unknown code never lands in the DB.
        """
        if order_data.get("language_code"):
            return
        order_data["language_code"] = active_language_code()

    # Allow-list for keys we accept on ``order.metadata['meta']``. The
    # storefront proxy can only forward what's here; everything else
    # is dropped silently. Keeps the column from drifting into a free-
    # for-all and protects against a malicious client trying to stuff
    # PII into Meta event logs.
    _META_CONTEXT_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "fbp",
            "fbc",
            "client_user_agent",
            "client_ip_address",
            "event_ids",
            "consent",
        }
    )
    _META_EVENT_ID_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"purchase", "initiate_checkout", "add_payment_info"}
    )

    @classmethod
    def _sanitise_meta_context(
        cls, raw: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Filter the storefront-supplied meta dict down to known keys.

        Returns an empty dict when input is missing or malformed. The
        empty result is special-cased upstream to skip the ``meta``
        field on ``order.metadata`` entirely so it doesn't show up in
        admin as a phantom empty bag.
        """
        if not raw or not isinstance(raw, dict):
            return {}
        out: dict[str, Any] = {}
        for key in cls._META_CONTEXT_KEYS:
            if key not in raw:
                continue
            value = raw[key]
            if key == "event_ids" and isinstance(value, dict):
                event_ids = {
                    sub_key: str(sub_val)
                    for sub_key, sub_val in value.items()
                    if sub_key in cls._META_EVENT_ID_KEYS
                    and isinstance(sub_val, (str, int))
                    and str(sub_val)
                }
                if event_ids:
                    out["event_ids"] = event_ids
                continue
            if key == "consent" and isinstance(value, dict):
                # Only keep boolean fields we understand. ``ads`` is
                # the master gate — without it set to True the CAPI
                # dispatcher refuses to send.
                consent = {"ads": bool(value.get("ads"))}
                out["consent"] = consent
                continue
            if isinstance(value, str) and value.strip():
                # Cap raw strings at a sane length so a malicious
                # client can't bloat order rows.
                out[key] = value.strip()[:512]
        return out

    @classmethod
    def _dispatch_shipment_creation_task(cls, order: Order) -> None:
        """Enqueue the provider's create-shipment Celery task.

        Routes via :class:`shipping.services.ShippingService`, which
        looks up the carrier adapter from ``order.shipping_provider``
        (FK → ``ShippingProvider`` row → registered adapter). Orders
        without a provider attached silently no-op. Each provider's
        task is idempotent on its shipment row, so duplicate
        dispatches under payment-provider retries are harmless.
        """
        from shipping.services import ShippingService

        ShippingService.dispatch_create_shipment_task(order)

    @classmethod
    @transaction.atomic
    def confirm_offline_payment(
        cls,
        order: Order,
        *,
        amount: Money,
        reference: str,
        confirmed_by: str,
        note: str = "",
    ) -> None:
        """Record that the money of a bank-transfer order (``PaySettlement.
        OFFLINE_TRANSFER``) arrived, on a staff member's word.

        An order's ``payment_status`` is never typed in (the admin form
        shows it read-only, since a form save once marked orders paid with
        no charge behind them), so this is the one door for money that is
        settled off the platform. It is narrow on purpose: only a
        bank-transfer order, only while its payment is PENDING, only for
        exactly what the order costs (a short or over payment is for the
        staff member to resolve first), and only with a reference (the
        bank's transaction id), which is also the ledger's idempotency key
        so one transfer cannot pay two orders. Online providers never come
        through here: their payments are recorded by the provider's own
        confirmed event.

        Written together, under the order lock: the ledger CHARGE, the
        payment status and the history entry saying who confirmed what
        and when. A PENDING order then moves to PROCESSING like any paid
        order (which fires ``order_paid``).
        """
        from order.models.history import OrderHistory

        locked = Order.objects.select_for_update().get(pk=order.pk)
        pay_way = locked.pay_way
        if (
            pay_way is None
            or PaySettlement(pay_way.settlement)
            != PaySettlement.OFFLINE_TRANSFER
        ):
            raise PaymentError(
                _(
                    "Only a bank-transfer order can be confirmed paid by "
                    "hand. Online payments are recorded by the payment "
                    "provider."
                )
            )
        if locked.payment_status != PaymentStatus.PENDING:
            raise PaymentError(_("This order is not waiting for a payment."))
        reference = reference.strip()
        if not reference:
            raise PaymentError(
                _("Give the bank's transaction reference for the transfer.")
            )
        if amount.amount != locked.amount_due.amount:
            raise PaymentError(
                _("The order costs {due}; confirm exactly that amount.").format(
                    due=locked.amount_due
                )
            )
        previous = locked.payment_status
        _row, created = PaymentLedger.record_offline_collection(
            locked,
            amount=amount,
            reference=reference,
            description=f"Confirmed by {confirmed_by}: {note}",
        )
        if not created:
            raise PaymentError(
                _("This transfer reference is already recorded.")
            )
        locked.mark_as_paid(payment_method=OFFLINE_TRANSFER_PROVIDER)
        OrderHistory.log_payment_update(
            order=locked,
            previous_value={"payment_status": previous},
            new_value={
                "payment_status": PaymentStatus.COMPLETED,
                "provider": OFFLINE_TRANSFER_PROVIDER,
                "reference": reference,
                "amount": str(amount.amount),
                "confirmed_by": confirmed_by,
                "note": note,
            },
        )
        order.payment_status = locked.payment_status
        order.payment_method = locked.payment_method
        if locked.status == OrderStatus.PENDING:
            OrderLifecycleService.update_order_status(
                locked, OrderStatus.PROCESSING
            )
            order.status = locked.status

    @classmethod
    @transaction.atomic
    def settle_cod_payment(
        cls, order: Order, *, amount: Money, provider: str, reference: str
    ) -> bool:
        """Mark a cash-on-delivery order paid and record the collection.

        What a courier's COD evidence (ACS payout row, ACS "received
        outside" action, BoxNow ``delivered`` event) does to an order, in
        one place. The payment status and the ``COD_COLLECTION`` ledger row
        are one step: written together under the order lock or not at all,
        so a ledger insert that fails leaves the order PENDING and the next
        attempt (a payout retry) starts over. Written separately, a
        failure in between left the order COMPLETED with no ledger row, and
        that retry then skipped it for already being paid.

        The status is read under the lock, not taken from the caller's
        possibly stale *order*, so two callers racing for one order (the
        nightly payout reconcile and the staff action) settle it once.
        Returns ``True`` only for the call that settled it, and the caller
        then runs what follows a payment (``order_paid``, the auto-advance
        to COMPLETED); ``False`` when the order was not PENDING.
        """
        payment_status = (
            Order.objects.select_for_update()
            .values_list("payment_status", flat=True)
            .get(pk=order.pk)
        )
        if payment_status != PaymentStatus.PENDING:
            return False
        order.mark_as_paid(payment_method=provider)
        PaymentLedger.record_cod_collection(
            order, amount=amount, provider=provider, reference=reference
        )
        return True

    @staticmethod
    def is_payment_after_cancel(order: Order) -> bool:
        """True when a confirmed charge lands on a CANCELED order.

        Checked BEFORE the settled-state guard of every payment-success
        handler. A canceled order carries a settled ``payment_status``
        (``Order.save`` settles an unpaid one to CANCELED), so behind that
        guard a genuine charge was dropped with a WARNING — money taken,
        order dead, nobody told. A REFUNDED / PARTIALLY_REFUNDED order is
        excluded: there the money already went back, and a success event
        is a stale redelivery the guard must keep ignoring.
        """
        return (
            order.status == OrderStatus.CANCELED
            and order.payment_status
            not in (
                PaymentStatus.REFUNDED,
                PaymentStatus.PARTIALLY_REFUNDED,
            )
        )

    @classmethod
    def record_payment_after_cancel(
        cls, order: Order, *, payment_id: str, payment_method: str
    ) -> None:
        """Book a charge that arrived for a CANCELED order and page staff.

        The money is recorded as received (``mark_as_paid``), which is
        what lets ``refund_order`` act on the order — it refuses one that
        ``is_paid`` says is unpaid. The order stays CANCELED and no courier
        shipment is dispatched (G0281). ``metadata["payment_after_cancel"]``
        is the reconciliation record, an ERROR log the monitored channel,
        an ops email the prompt, and an ``OrderHistory`` note the trace on
        the order page. Idempotent per ``payment_id``: a redelivered event
        neither re-books nor re-alerts.

        Called with the order row locked by the payment handler.
        """
        from emails.send import send_email
        from order.emails import PaymentAfterCancel
        from order.models.history import OrderHistory

        recorded = (order.metadata or {}).get("payment_after_cancel") or {}
        if recorded.get("payment_id") == payment_id:
            logger.info(
                "Payment %s for CANCELED order %s already recorded",
                payment_id,
                order.id,
            )
            return

        order.mark_as_paid(payment_id=payment_id, payment_method=payment_method)
        if not order.metadata:
            order.metadata = {}
        order.metadata["payment_after_cancel"] = {
            "payment_id": payment_id,
            "payment_method": payment_method,
            "recorded_at": timezone.now().isoformat(),
        }
        order.save(update_fields=["metadata"])
        logger.error(
            "Payment %s (%s) received for CANCELED order %s — manual refund "
            "required; NOT dispatching shipment creation",
            payment_id,
            payment_method,
            order.id,
        )
        OrderHistory.log_note(
            order=order,
            note=(
                f"Payment {payment_id} ({payment_method}) arrived after the "
                "order was canceled. The order stays canceled and nothing "
                "ships — refund the customer."
            ),
        )
        try:
            send_email(
                "ops.payment_after_cancel",
                PaymentAfterCancel(
                    order_id=order.id,
                    payment_id=payment_id,
                    payment_method=payment_method,
                ),
            )
        except Exception:
            logger.exception(
                "Could not send the payment-after-cancel alert for order %s",
                order.id,
            )

    @classmethod
    def flag_payment_mismatch(
        cls,
        order: Order,
        *,
        payment_id: str,
        provider: str,
        observed: object,
        expected: object,
    ) -> None:
        """Record a confirmed charge that does not match the order, and
        page staff. The order is NOT marked paid.

        The money HAS left the customer (the provider confirmed it), so the
        flag also keeps ``auto_cancel_stuck_pending_orders`` from closing
        the order with ``refund_payment=False``; a human reconciles it.
        Called with the order row locked by the payment handler.

        Once per payment: the same payment met again while the order still
        waits for that review (a read-back's next run, a refund of it, a
        redelivery) changes nothing and emails no one. Another payment is
        news, and flags it again. Either way the order's ``metadata`` is
        saved, with what the caller wrote on it.
        """
        from emails.send import send_email
        from order.emails import PaymentMismatch
        from order.models.history import OrderHistory
        from order.models.order import AMOUNT_MISMATCH_FLAG
        from order.payments import payment_hold

        flagged = (order.metadata or {}).get(AMOUNT_MISMATCH_FLAG)
        if (
            isinstance(flagged, dict)
            and str(flagged.get("transaction_id", "")).lower()
            == str(payment_id).lower()
            and payment_hold.awaits_payment_review(order)
        ):
            order.save(update_fields=["metadata"])
            return

        if not order.metadata:
            order.metadata = {}
        order.metadata[AMOUNT_MISMATCH_FLAG] = {
            "transaction_id": str(payment_id),
            "provider": provider,
            "verified_amount": str(observed),
            "expected_amount": str(expected),
            "observed_at": timezone.now().isoformat(),
        }
        order.save(update_fields=["metadata"])
        logger.error(
            "%s payment %s collected %s but order %s is for %s — NOT "
            "marking it paid",
            provider,
            payment_id,
            observed,
            order.id,
            expected,
        )
        OrderHistory.log_note(
            order=order,
            note=(
                f"{provider} payment {payment_id} collected {observed} but "
                f"the order is for {expected}. It was not marked paid: "
                "reconcile it with the customer."
            ),
        )
        try:
            send_email(
                "ops.payment_mismatch",
                PaymentMismatch(
                    order_id=order.id,
                    payment_id=str(payment_id),
                    provider=provider,
                    observed=observed,
                    expected=expected,
                ),
            )
        except Exception:
            logger.exception(
                "Could not send the payment-mismatch alert for order %s",
                order.id,
            )

    @classmethod
    def report_payment_without_order(
        cls,
        payment_id: str,
        *,
        captured: Money,
        metadata: Mapping[str, str] | None,
    ) -> None:
        """Queue the alert about a succeeded intent no order carries.

        Only the store's own payments alert: an intent whose server-set
        metadata binds it to this tenant (``own_purpose``). Any other
        (a Payment Link, a Stripe Invoice, Terminal, a charge made in the
        Stripe dashboard) is logged at WARNING and nothing more.

        The mail is sent by ``order.tasks.alert_payment_without_order_task``,
        queued after the webhook's transaction commits (a rolled-back
        webhook queues nothing) and retried when the mail is. The
        services import no task, so the receiver of
        ``payment_without_order_found`` queues it. Every intent of ours is
        opened for an order that already exists, or for a gift-card
        purchase, so one no order carries is alerted at once.
        """
        if own_purpose(metadata) is None:
            logger.warning(
                "Payment %s is not bound to this store's orders: no alert",
                payment_id,
            )
            return
        payment_without_order_found.send(
            sender=cls,
            payment_id=payment_id,
            captured=captured,
        )

    @classmethod
    def alert_payment_without_order(
        cls, payment_id: str, *, captured: Money
    ) -> None:
        """Page staff once about a confirmed Stripe payment no order
        carries: the customer was charged for nothing that will ship.

        Once per payment: the claim on ``payment_id`` is taken before the
        send (``cache.add`` is atomic), so a repeat of the task alerts
        once. A send that fails gives the claim back and raises, so the
        task's retry sends. Run by the task, never inside a webhook.
        """
        from emails.send import send_email
        from order.emails import PaymentWithoutOrder

        claim = PAYMENT_WITHOUT_ORDER_CLAIM_KEY.format(payment_id=payment_id)
        if not cache.add(claim, 1, PAYMENT_WITHOUT_ORDER_CLAIM_TTL):
            return
        try:
            send_email(
                "ops.payment_without_order",
                PaymentWithoutOrder(
                    payment_id=payment_id,
                    provider=StripePaymentProvider.code,
                    amount=captured,
                ),
            )
        except BaseException:
            try:
                cache.delete(claim)
            except Exception:
                # The send's error is the one the task retries on.
                logger.exception(
                    "Could not release the payment-without-order claim for %s",
                    payment_id,
                )
            raise

    @staticmethod
    def _bound_order_uuid(metadata: Mapping[str, str] | None) -> UUID | None:
        """The order an intent was created for, when its own metadata (set
        by the server, ``order.payments.payment_binding``) binds it to an
        existing order of this store; ``None`` for any other intent."""
        subject = (metadata or {}).get(SUBJECT_KEY)
        if not subject:
            return None
        try:
            bound = UUID(subject)
        except ValueError:
            return None
        if binding_mismatch(
            metadata, purpose=PaymentPurpose.ORDER, subject=bound
        ):
            return None
        return bound

    @classmethod
    @transaction.atomic
    def handle_payment_succeeded(
        cls,
        payment_intent_id: str,
        *,
        captured: Money,
        metadata: Mapping[str, str] | None = None,
    ) -> PaymentEventOutcome | None:
        """Book a succeeded Stripe PaymentIntent.

        *captured* is what Stripe collected (``amount_received``). It goes
        into the payment ledger whatever state the order is in, because
        the money has moved either way; the order's own status then
        follows the guards below.

        The order is the one whose ``payment_id`` is the intent. An intent
        the order does not point at (a hosted Checkout Session's intent,
        whose ``checkout.session.completed`` has not been booked yet, or
        an older payment the order has since replaced) is found by what
        the server wrote on it, *metadata*: it must bind the intent to an
        existing order (``PaymentPurpose.ORDER``) of this store, checked
        by ``binding_mismatch``.
        """
        # Acquire a row lock and hydrate related objects in one query.
        # ``for_detail()`` adds COUNT/SUM annotations which Postgres
        # rejects under FOR UPDATE (aggregate in locked query). We
        # replicate the select_related/prefetch_related chains from
        # for_detail() manually, skipping with_counts() / with_total_amounts().
        from django.db.models import Prefetch

        from order.models.history import OrderHistory
        from order.payments.payment_events import publish_payment_status

        # ``of=("self",)`` restricts the row lock to the Order table.
        # Without it Postgres rejects the query with ``FOR UPDATE cannot
        # be applied to the nullable side of an outer join`` because
        # several of the FKs on Order are nullable (user, pay_way,
        # country, region, shipping_provider) and ``select_related``
        # joins them with LEFT OUTER JOIN.
        orders = (
            Order.objects.select_for_update(of=("self",))
            .select_related(
                "user",
                "pay_way",
                "country",
                "region",
                "shipping_provider",
            )
            .prefetch_related(
                "items__product__translations",
                "items__product__images__translations",
                Prefetch(
                    "history",
                    queryset=OrderHistory.objects.select_related(
                        "user"
                    ).order_by("created_at"),
                ),
                *order_detail_prefetches(),
                "invoice",
            )
        )
        found = orders.filter(payment_id=payment_intent_id).first()
        if found is None and (bound := cls._bound_order_uuid(metadata)):
            found = orders.filter(uuid=bound).first()
        order = found
        if order is None:
            logger.error(
                "Order not found for payment_intent: %s", payment_intent_id
            )
            cls.report_payment_without_order(
                payment_intent_id, captured=captured, metadata=metadata
            )
            return None

        PaymentLedger.record_charge(
            order,
            amount=captured,
            provider=StripePaymentProvider.code,
            reference=payment_intent_id,
        )

        previous = order.payment_status
        if cls.is_payment_after_cancel(order):
            cls.record_payment_after_cancel(
                order, payment_id=payment_intent_id, payment_method="stripe"
            )
            publish_payment_status(order)
            return PaymentEventOutcome(order, previous, applied=False)

        # A second payment on an order another intent already paid: booked
        # above (and flagged by ``PaymentLedger.sync_overcharge`` when it
        # takes the order past what it is for). The order keeps its first
        # ``payment_id`` and is not confirmed again.
        if (
            order.payment_status == PaymentStatus.COMPLETED
            and order.payment_id
            and order.payment_id != payment_intent_id
        ):
            logger.error(
                "PaymentIntent %s is a second payment on order %s, already "
                "paid by %s: recorded in the ledger, order left as it is",
                payment_intent_id,
                order.id,
                order.payment_id,
            )
            return PaymentEventOutcome(order, previous, applied=False)

        # Guard: a stale or out-of-order "payment succeeded" event must not
        # un-refund or un-cancel an order that is already in a settled state.
        # Stripe does NOT guarantee event delivery order.  COMPLETED is
        # allowed through (idempotent — mark_as_paid just writes COMPLETED
        # again, and the PENDING→PROCESSING block below is gated on
        # order.status so no double-shipment dispatch occurs).
        _refund_or_cancel = {
            PaymentStatus.REFUNDED,
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.CANCELED,
        }
        if order.payment_status in _refund_or_cancel:
            logger.warning(
                "Ignoring stale payment_succeeded for order %s: "
                "payment_status already %s",
                order.id,
                order.payment_status,
            )
            return PaymentEventOutcome(order, previous, applied=False)

        expected = order.calculate_order_total_amount()
        if (
            captured.currency != expected.currency
            or abs(captured.amount - expected.amount) > PAYMENT_AMOUNT_TOLERANCE
        ):
            cls.flag_payment_mismatch(
                order,
                payment_id=payment_intent_id,
                provider=StripePaymentProvider.code,
                observed=captured,
                expected=expected,
            )
            publish_payment_status(order)
            return PaymentEventOutcome(order, previous, applied=False)

        order.mark_as_paid(
            payment_id=payment_intent_id, payment_method="stripe"
        )

        if order.status == OrderStatus.PENDING:
            # The Stripe webhook handler dispatches
            # ``send_order_confirmation_email`` immediately after this
            # method returns — that email already conveys "we received
            # your order, processing it now". Suppress this transition's
            # status-update email + WS toast so the customer doesn't get
            # back-to-back messages saying essentially the same thing.
            # The COD path doesn't go through this method, so the
            # PENDING → PROCESSING advance for COD voucher mints
            # (AcsService._advance_pending_order_to_processing) keeps
            # firing its email as before.
            OrderLifecycleService.suppress_customer_status_notifications(
                order, OrderStatus.PROCESSING.value
            )
            OrderLifecycleService.update_order_status(
                order, OrderStatus.PROCESSING
            )

        # Enqueue provider-specific shipment creation after payment.
        # ShippingService.dispatch_create_shipment_task wraps the
        # delay() in transaction.on_commit so the worker only sees the
        # committed row.
        #
        # Only for an order that still has a delivery ahead of it. The
        # CANCELED early-return above exists because minting a courier
        # shipment for a dead order is wrong (G0281), and an order that
        # already SHIPPED, was DELIVERED, RETURNED or REFUNDED is just as
        # done with its voucher. A duplicate provider success event — a
        # different event id for the same intent is not caught by the
        # webhook_processed_{event_id} flag — would otherwise re-enqueue
        # carrier work against a finished order.
        if order.status in _SHIPMENT_DISPATCHABLE_STATUSES:
            cls._dispatch_shipment_creation_task(order)
        else:
            logger.info(
                "Not dispatching shipment creation for order %s: status is "
                "%s, which has no delivery ahead of it",
                order.id,
                order.status,
            )

        publish_payment_status(order)
        logger.info("Order %s marked as paid successfully", order.id)
        return PaymentEventOutcome(
            order, previous, applied=previous != PaymentStatus.COMPLETED
        )

    @classmethod
    @transaction.atomic
    def handle_payment_failed(
        cls, payment_intent_id: str
    ) -> PaymentEventOutcome | None:
        from order.payments.payment_events import publish_payment_status

        # ``of=("self",)`` — see ``handle_payment_succeeded`` for why.
        order = (
            Order.objects.select_for_update(of=("self",))
            .select_related(
                "user",
                "pay_way",
                "country",
                "region",
                "shipping_provider",
            )
            .filter(payment_id=payment_intent_id)
            .first()
        )
        if order is None:
            logger.error(
                "Order not found for payment_intent: %s", payment_intent_id
            )
            return None

        # Guard: a stale or out-of-order "payment failed" event must not
        # overwrite a financially settled state.  Stripe does NOT guarantee
        # event delivery order, so a delayed payment_intent.payment_failed
        # could arrive after charge.refunded already moved the order to
        # REFUNDED.  FAILED is only written from non-settled states.
        previous = order.payment_status
        if previous in SETTLED_PAYMENT_STATUSES:
            logger.warning(
                "Ignoring stale payment_failed for order %s: "
                "payment_status already %s",
                order.id,
                order.payment_status,
            )
            return PaymentEventOutcome(order, previous, applied=False)

        order.payment_status = PaymentStatus.FAILED
        order.save(update_fields=["payment_status"])

        publish_payment_status(order)
        logger.info("Order %s payment marked as failed", order.id)
        return PaymentEventOutcome(
            order, previous, applied=previous != PaymentStatus.FAILED
        )
