import logging
from decimal import Decimal
from typing import Any, ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet, Sum
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from djmoney.money import Money

from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import (
    SETTLED_PAYMENT_STATUSES,
    OrderStatus,
    PaymentStatus,
)
from order.exceptions import (
    InsufficientStockError,
    InvalidCouponError,
    InvalidGiftCardError,
    InvalidOrderDataError,
    InvalidStatusTransitionError,
    OrderCancellationError,
    PaymentAmountMismatchError,
    PaymentCurrencyMismatchError,
    PaymentError,
    PaymentNotFoundError,
    PaymentVerificationError,
    ProductNotFoundError,
    StockReservationError,
)
from order.models.item import OrderItem
from order.models.order import Order
from order.models.stock_log import StockLog
from order.models.stock_reservation import StockReservation
from order.signals import order_refunded
from order.stock import StockManager
from promotion.services import CouponService, PromotionEngine

logger = logging.getLogger(__name__)

# Payment statuses that represent a financially settled (terminal) state.
# A stale or out-of-order webhook event MUST NOT overwrite any of these.
# Stripe/Viva do NOT guarantee delivery order, so e.g. a delayed
# ``payment_failed`` event could arrive after a ``charge.refunded`` event
# that already moved the order to REFUNDED — that must not regress it to FAILED.
# Statuses for which a courier shipment is still meaningful. Anything
# else has either not been paid for yet or is already done with its
# voucher; see handle_payment_succeeded.
# Online providers that settle by redirecting the shopper off-site: the
# order is created BEFORE any money moves, so a partial deduction is not
# the client error it is for the intent-first providers.
_REDIRECT_PROVIDER_CODES: frozenset[str] = frozenset({"viva_wallet"})

_SHIPMENT_DISPATCHABLE_STATUSES: frozenset[str] = frozenset(
    {
        OrderStatus.PENDING,
        OrderStatus.PROCESSING,
    }
)

__all__ = ["OrderService"]


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


class OrderService:
    @classmethod
    def get_order_by_id(cls, order_id: int) -> Order:
        """Get order by ID with optimized queryset."""
        return Order.objects.for_detail().get(id=order_id)

    @classmethod
    def get_order_by_uuid(cls, uuid: str) -> Order:
        """Get order by UUID with optimized queryset."""
        return Order.objects.for_detail().get(uuid=uuid)

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

        Must run inside the order-creation ``transaction.atomic`` block;
        ``convert_reservation_to_sale``/``decrement_stock`` lock each product
        row, so an insufficient-stock product raises ``InsufficientStockError``
        and rolls the order back.

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

        for product_id, quantity in needed.items():
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
        for leftovers in reservations_by_product.values():
            for reservation in leftovers:
                _release(reservation)

        return converted_ids

    @classmethod
    def _evaluate_promotions(cls, cart, *, user, email: str):
        """Locked promotion evaluation for order creation.

        Runs inside the caller's order-create transaction and takes
        ``select_for_update`` on the candidate Promotion rows so the
        usage-limit counts hold until commit. A refused attached
        coupon raises ``InvalidCouponError`` — the offline providers
        (COD/Viva) have no amount guard, so silently dropping the
        discount would charge the shopper more than the sidebar showed.
        """
        result = PromotionEngine.evaluate(
            cart, user=user, email=email, lock=True
        )
        blocking = result.blocking_rejections
        if blocking:
            code, reason = blocking[0]
            raise InvalidCouponError(code=code, reason=reason)
        return result

    @classmethod
    def _redeem_gift_cards(cls, gift_card_codes, order) -> Money:
        """Order-first path: locked plan + record against what is still
        due after promotions and loyalty. Returns the settled amount."""
        if not gift_card_codes:
            return Money(0, settings.DEFAULT_CURRENCY)
        from giftcard.services import (
            GiftCardError,
            GiftCardService,
        )

        amount_due = order.calculate_order_total_amount()
        try:
            return GiftCardService.redeem(gift_card_codes, order, amount_due)
        except GiftCardError as exc:
            raise InvalidGiftCardError(
                reason=exc.reason, message=str(exc.message)
            ) from exc

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
        from order.stock import StockManager

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
            )
        if skipped:
            order.metadata["promotion_gifts_skipped"] = skipped

    @classmethod
    def _plan_loyalty_locked(
        cls, user, loyalty_points_to_redeem, currency, max_discount
    ) -> Decimal:
        """Payment-first path: price the loyalty redemption under the
        user-row lock so the PaymentIntent verification and the later
        ``redeem_points`` (same transaction, same lock) cannot drift.

        Returns Decimal("0") when no redemption was requested. A
        redemption that cannot be honoured aborts order creation with
        a typed error — the intent was created for the discounted
        amount, so silently dropping the discount would make the
        capture disagree with ``paid_amount``.
        """
        if (
            not loyalty_points_to_redeem
            or loyalty_points_to_redeem <= 0
            or user is None
            or not getattr(user, "is_authenticated", False)
        ):
            return Decimal(0)

        from loyalty.services import LoyaltyService

        try:
            return LoyaltyService.preview_redemption(
                user,
                loyalty_points_to_redeem,
                str(currency),
                max_discount,
                lock=True,
            )
        except ValidationError as exc:
            message = getattr(exc, "message", None) or "; ".join(
                str(msg) for msg in getattr(exc, "messages", [])
            )
            raise InvalidOrderDataError(
                _("Loyalty redemption cannot be applied: {reason}").format(
                    reason=message
                )
            ) from exc

    @classmethod
    def _plan_gift_cards_locked(cls, gift_card_codes, amount_due: Money):
        """Payment-first path: lock the cards and compute the plan the
        PaymentIntent verification AND the later recording both use —
        one plan, no drift between the verified and redeemed amounts."""
        from giftcard.services import (
            GiftCardError,
            GiftCardService,
        )

        try:
            return GiftCardService.plan_redemption(
                gift_card_codes or [], amount_due, lock=True
            )
        except GiftCardError as exc:
            raise InvalidGiftCardError(
                reason=exc.reason, message=str(exc.message)
            ) from exc

    @classmethod
    @transaction.atomic
    def create_order_from_cart(
        cls,
        cart,
        shipping_address: dict[str, Any],
        payment_intent_id: str,
        pay_way,
        user=None,
        loyalty_points_to_redeem: int | None = None,
        gift_card_codes: list[str] | None = None,
        meta_context: dict[str, Any] | None = None,
    ) -> Order:
        """
        Create order from cart after payment confirmation (payment-first flow).

        This method implements the payment-first approach where payment is confirmed
        before order creation. It performs the following steps:
        1. Validates cart items still exist and prices match
        2. Validates shipping address completeness
        3. Validates payment intent exists and is confirmed
        4. Gets stock reservations for cart session
        5. Creates Order with payment_id field populated
        6. Creates OrderItems from CartItems
        7. Converts stock reservations to decrements via StockManager
        8. Clears cart
        9. Returns order in PENDING status (webhook will move to PROCESSING)

        Args:
            cart: Cart object containing items to order
            shipping_address: Dictionary with shipping address fields
            payment_intent_id: Stripe payment intent ID (must be confirmed)
            pay_way: PayWay object for payment method
            user: Optional UserAccount (None for guest orders)

        Returns:
            Order: Created order in PENDING status

        Raises:
            InvalidOrderDataError: If validation fails
            InsufficientStockError: If stock unavailable
            PaymentNotFoundError: If payment_intent_id invalid

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
            ...         'country_id': 1,
            ...         'phone': '+30123456789'
            ...     },
            ...     payment_intent_id='pi_123abc',
            ...     pay_way=pay_way,
            ...     user=user
            ... )
        """
        try:
            from cart.models import Cart

            # Lock the Cart row immediately so concurrent checkouts on the
            # same cart are serialised.  Must happen before any reads so
            # validate_cart_for_checkout sees the locked snapshot.
            cart = Cart.objects.select_for_update().get(pk=cart.pk)

            # Step 1: Validate cart for checkout
            validation_result = cls.validate_cart_for_checkout(cart)
            if not validation_result.get("valid", False):
                raise InvalidOrderDataError(
                    _("Cart validation failed: {errors}").format(
                        errors=", ".join(validation_result.get("errors", []))
                    )
                )

            # Step 2: Validate shipping address
            cls.validate_shipping_address(shipping_address, pay_way=pay_way)

            # Step 2.4: Re-bind wholesale pricing on the LOCKED cart —
            # the refetch above produced a fresh instance. Everything
            # below (promotion gating, totals, shipping threshold,
            # payment fee, item price snapshots, PaymentIntent amount
            # verification) must see the same prices the cart preview
            # showed. Kept in lockstep with the offline path.
            from b2b.services import (
                B2BPricingService,
                B2BService,
            )

            B2BPricingService.bind_cart(cart, user)

            # Wholesale carts sit outside the loyalty program unless the
            # merchant opts in. Dropped HERE, before any pricing math,
            # so the verified PaymentIntent amount, the order total and
            # ``redeem_points`` all agree — dropping it later would let
            # the capture disagree with ``paid_amount``.
            if B2BService.suppresses_loyalty(cart):
                loyalty_points_to_redeem = 0

            # Wholesale minimum-order-value gate (standard B2B term).
            # Checked here AND at payment-intent creation so an online
            # payment can never be captured for an order this would
            # reject.
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

            # Step 2.5: Evaluate promotions under lock. The Promotion
            # rows stay locked until commit, so the usage-limit counts
            # the engine enforced cannot be raced by a concurrent
            # checkout. A refused attached coupon aborts with a typed
            # error rather than silently charging the undiscounted
            # total (COD/Viva have no amount guard to catch it).
            promo_result = cls._evaluate_promotions(
                cart, user=user, email=shipping_address.get("email") or ""
            )
            promo_discount = promo_result.discount_total

            # Step 3: Validate payment intent exists
            from order.payment import get_payment_provider

            if not payment_intent_id:
                raise InvalidOrderDataError(
                    str(_("Payment intent ID is required for order creation"))
                )

            # Get payment provider and verify payment intent exists
            provider = get_payment_provider(pay_way.provider_code)
            payment_status, payment_data = provider.get_payment_status(
                payment_intent_id
            )

            # Verify payment intent exists and is in a valid state
            # We accept PENDING status - Stripe webhooks will update order after confirmation
            # This is the standard Stripe payment flow
            if payment_status not in [
                PaymentStatus.PENDING,
                PaymentStatus.PROCESSING,
                PaymentStatus.COMPLETED,
            ]:
                raise PaymentVerificationError(
                    payment_intent_id,
                    _("payment intent is in an invalid state: {status}").format(
                        status=payment_status
                    ),
                )

            # Verify the provider amount matches the server-calculated total.
            # The Stripe PaymentIntent's amount (in cents) must equal the
            # cart total + shipping + payment fee to prevent a tampered client
            # submitting a PI created for a lower amount.
            # payment_data["amount"] is in euros (stripe_pi.amount / 100).
            # We calculate the expected total using the same logic as Step 5.
            from shipping.utils import compute_total_weight_grams

            _cart_total = cart.total_price
            _cart_weight_grams = compute_total_weight_grams(
                (item.product, item.quantity) for item in cart.items.all()
            ) + PromotionEngine.gift_weight_grams(promo_result)
            if promo_result.free_shipping:
                _shipping_cost = Money(0, _cart_total.currency)
            else:
                _shipping_cost = cls.calculate_shipping_cost(
                    order_value=_cart_total,
                    country_id=shipping_address.get("country_id"),
                    region_id=shipping_address.get("region_id"),
                    shipping_provider_code=shipping_address.get(
                        "shipping_provider_code"
                    ),
                    shipping_kind=shipping_address.get("shipping_kind"),
                    weight_grams=_cart_weight_grams,
                )
            # Pay-way fee on the DISCOUNTED subtotal — mirrors the
            # create-payment-intent endpoint exactly.
            _order_subtotal = Money(
                _cart_total.amount
                - promo_discount.amount
                + _shipping_cost.amount,
                _cart_total.currency,
            )
            _payment_fee = cls.calculate_payment_method_fee(
                pay_way=pay_way,
                order_value=_order_subtotal,
            )
            _expected_total = (
                _cart_total.amount
                - promo_discount.amount
                + _shipping_cost.amount
                + _payment_fee.amount
            )
            # Loyalty is a discount, so it reduces what the provider
            # charges — priced here under the user-row lock with the
            # SAME math ``redeem_points`` runs after the order exists,
            # so the verified intent amount and ``paid_amount`` cannot
            # drift. (Historically the intent ignored loyalty and
            # Stripe captured the undiscounted total while the points
            # were still burnt.)
            loyalty_preview = cls._plan_loyalty_locked(
                user,
                loyalty_points_to_redeem,
                _cart_total.currency,
                max_discount=_cart_total.amount,
            )
            _expected_total = max(_expected_total - loyalty_preview, Decimal(0))
            # Gift cards settle part of the total LAST (payment, not
            # discount) — the plan locks the card rows until commit so
            # the amount verified here is exactly what gets redeemed
            # after the order row exists.
            gift_plan = cls._plan_gift_cards_locked(
                gift_card_codes,
                Money(_expected_total, _cart_total.currency),
            )
            _expected_total -= gift_plan.amount.amount
            calculated_total_cents = int(round(_expected_total * 100))
            # The Stripe provider returns amount already divided by 100
            # (see payment.py StripePaymentProvider.get_payment_status) and
            # also returns a currency code. Some providers/tests return a
            # stripped payload without amount/currency — only enforce the
            # check when the provider actually supplied those fields.
            expected_currency = settings.DEFAULT_CURRENCY.lower()

            if "amount" in payment_data and payment_data["amount"] is not None:
                provider_amount_cents = int(round(payment_data["amount"] * 100))
                if provider_amount_cents != calculated_total_cents:
                    # Log the full input set that produced the mismatch so
                    # root-causing doesn't require re-running the checkout
                    # under a debugger. The (provider, kind, weight,
                    # country, region, cart_total, shipping_cost,
                    # payment_fee) tuple is exactly what was fed into
                    # ``calculate_shipping_cost`` + ``calculate_payment_
                    # method_fee`` above — if any of these differ from
                    # what the create-payment-intent step saw, the
                    # mismatch is upstream of this point.
                    logger.warning(
                        "Payment amount mismatch for intent %s: "
                        "provider=%d cents, calculated=%d cents",
                        payment_intent_id,
                        provider_amount_cents,
                        calculated_total_cents,
                        extra={
                            "payment_intent_id": payment_intent_id,
                            "cart_uuid": str(cart.uuid),
                            "provider_amount_cents": provider_amount_cents,
                            "calculated_amount_cents": calculated_total_cents,
                            "cart_total_amount": str(_cart_total.amount),
                            "shipping_cost_amount": str(_shipping_cost.amount),
                            "payment_fee_amount": str(_payment_fee.amount),
                            "promotion_discount_amount": str(
                                promo_discount.amount
                            ),
                            "promotion_free_shipping": (
                                promo_result.free_shipping
                            ),
                            "loyalty_preview_amount": str(loyalty_preview),
                            "gift_plan_amount": str(gift_plan.amount.amount),
                            "shipping_provider_code": shipping_address.get(
                                "shipping_provider_code"
                            ),
                            "shipping_kind": shipping_address.get(
                                "shipping_kind"
                            ),
                            "cart_weight_grams": _cart_weight_grams,
                            "country_id": shipping_address.get("country_id"),
                            "region_id": shipping_address.get("region_id"),
                            "pay_way_code": pay_way.provider_code,
                        },
                    )
                    raise PaymentAmountMismatchError(
                        provider_amount_cents=provider_amount_cents,
                        calculated_amount_cents=calculated_total_cents,
                    )

            if payment_data.get("currency"):
                provider_currency = payment_data["currency"].lower()
                if provider_currency not in {
                    "eur",
                    expected_currency,
                }:
                    logger.warning(
                        "Payment currency mismatch for intent %s: "
                        "provider='%s', expected='%s'",
                        payment_intent_id,
                        provider_currency,
                        expected_currency,
                    )
                    raise PaymentCurrencyMismatchError(
                        provider_currency=provider_currency,
                        expected_currency=expected_currency,
                    )

            # Step 4: Get stock reservations for cart session
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
            cart_items = list(
                cart.items.select_for_update().select_related("product")
            )

            # Reservations (active, expired, or duplicated) are reconciled
            # against the cart in Step 7 via _consume_stock_for_order, which
            # releases every hold and decrements physical stock by the cart
            # quantity. Expired holds therefore need no special handling here:
            # a lapsed hold whose stock was taken by another session simply
            # fails the decrement below and rolls the whole checkout back.

            # Step 5: Create Order with payment_id field populated
            # Determine target currency from shipping address or default
            target_currency = settings.DEFAULT_CURRENCY

            # Build order data
            order_data = {
                "user": user if user and user.is_authenticated else None,
                "pay_way": pay_way,
                "payment_id": payment_intent_id,
                "payment_status": payment_status,
                "status": OrderStatus.PENDING,
                # Shipping address fields
                "first_name": shipping_address.get("first_name"),
                "last_name": shipping_address.get("last_name"),
                "email": shipping_address.get("email"),
                "street": shipping_address.get("street"),
                "street_number": shipping_address.get("street_number"),
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

            # Calculate shipping cost — pass cart weight so ACS live
            # quotes match the weight-banded tariff bracket the voucher
            # mint will charge (no surprise upcharge after order create).
            from shipping.utils import compute_total_weight_grams

            cart_total = cart.total_price
            cart_weight_grams = compute_total_weight_grams(
                (ci.product, ci.quantity) for ci in cart_items
            ) + PromotionEngine.gift_weight_grams(promo_result)
            if promo_result.free_shipping:
                shipping_cost = Money(0, cart_total.currency)
            else:
                shipping_cost = cls.calculate_shipping_cost(
                    order_value=cart_total,
                    country_id=shipping_address.get("country_id"),
                    region_id=shipping_address.get("region_id"),
                    shipping_provider_code=shipping_address.get(
                        "shipping_provider_code"
                    ),
                    shipping_kind=shipping_address.get("shipping_kind"),
                    weight_grams=cart_weight_grams,
                )
            order_data["shipping_price"] = shipping_cost

            # Calculate payment method fee
            # Note: Payment fee is calculated on the DISCOUNTED items
            # total + shipping — the same figure the payment-intent
            # endpoint and the verification step above used.
            order_subtotal = Money(
                cart_total.amount
                - promo_discount.amount
                + shipping_cost.amount,
                cart_total.currency,
            )
            payment_fee = cls.calculate_payment_method_fee(
                pay_way=pay_way,
                order_value=order_subtotal,
            )
            order_data["payment_method_fee"] = payment_fee

            # Resolve provider FK + kind, then create the order.
            order_data.setdefault(
                "shipping_provider_code",
                shipping_address.get("shipping_provider_code"),
            )
            order_data.setdefault(
                "shipping_kind", shipping_address.get("shipping_kind")
            )
            cls._resolve_shipping_provider(order_data)
            cls._seed_language_code(order_data)
            order = Order.objects.create(**order_data)

            # Reuse the already-locked/materialised cart_items list rather
            # than issuing a second SELECT on the same rows.
            cart_items_pairs = [(ci.product, ci.quantity) for ci in cart_items]

            # Provider-agnostic shipment row creation. The carrier
            # adapter reads its own keys out of ``shipping_address``
            # (boxnow_locker_id, acs_station_external_id, etc.) and
            # filters on ``shipping_kind`` itself.
            from shipping.services import ShippingService

            ShippingService.create_shipment_row_for_order(
                order, payload=shipping_address, items=cart_items_pairs
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
            }
            # Meta Pixel context forwarded by the storefront proxy.
            # Persisted alongside cart_snapshot so the CAPI dispatcher
            # can build a UserData payload with the same fbp/fbc the
            # browser pixel saw.
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

            # Step 6: Create OrderItems from CartItems
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
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=quantity,
                    price=item_price,
                )

            # Step 7: Reconcile reservations against the cart and decrement
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

            # Step 7.5: Apply loyalty points redemption if requested
            loyalty_discount = Money(0, target_currency)
            if (
                loyalty_points_to_redeem
                and loyalty_points_to_redeem > 0
                and user
            ):
                try:
                    from loyalty.services import LoyaltyService

                    # Cap discount to products total (excluding shipping/fees)
                    items_total_amount = order.total_price_items.amount

                    # Redeem points and get discount amount
                    discount_amount = LoyaltyService.redeem_points(
                        user=user,
                        points_amount=loyalty_points_to_redeem,
                        currency=str(target_currency),
                        order=order,
                        max_discount=items_total_amount,
                    )
                    loyalty_discount = Money(discount_amount, target_currency)

                    # Store loyalty redemption in order metadata
                    order.metadata["loyalty_redemption"] = {
                        "points_redeemed": loyalty_points_to_redeem,
                        "discount_amount": str(discount_amount),
                        "currency": str(target_currency),
                    }

                    logger.info(
                        "Applied loyalty discount of %s %s (%s points) to order %s",
                        discount_amount,
                        target_currency,
                        loyalty_points_to_redeem,
                        order.id,
                    )
                except ValidationError:
                    raise
                except Exception as e:
                    logger.error(
                        "Failed to apply loyalty discount to order %s: %s",
                        order.id,
                        e,
                        exc_info=True,
                    )
                    # The PaymentIntent was verified WITH the loyalty
                    # discount (Step 2.6) — silently dropping it here
                    # would make the captured amount disagree with
                    # ``paid_amount``. The user-row lock taken during
                    # verification makes this branch unreachable in
                    # practice; if it ever fires, fail loud so the
                    # transaction (and its idempotency mark) rolls
                    # back and the checkout retries cleanly.
                    raise InvalidOrderDataError(
                        str(
                            _(
                                "Loyalty redemption failed after payment "
                                "verification."
                            )
                        )
                    ) from e

            # Step 7.6: Record promotion redemptions (rows + metadata)
            # for the evaluation done under lock at Step 2.5, and the
            # gift-card plan locked during payment verification.
            from giftcard.services import GiftCardService

            order.discount_amount = PromotionEngine.record(order, promo_result)
            order.loyalty_discount = loyalty_discount
            order.gift_card_amount = GiftCardService.record_plan(
                gift_plan, order
            )

            # Persist the deductions so they survive on the order, then
            # let calculate_order_total_amount() be the single authority
            # on what the customer owes. Subtracting here and leaving
            # the order's own total undiscounted is what let every
            # charge site bill the full amount while the points were
            # burnt.
            order.paid_amount = order.calculate_order_total_amount()
            order.save(
                update_fields=[
                    "discount_amount",
                    "discount_amount_currency",
                    "loyalty_discount",
                    "loyalty_discount_currency",
                    "gift_card_amount",
                    "gift_card_amount_currency",
                    "paid_amount",
                    "paid_amount_currency",
                    "metadata",
                ]
            )

            # The coupon's durable record is the redemption row — the
            # cart attachment has served its purpose.
            CouponService.clear_after_order(cart)

            # Step 8: Clear cart
            # Keep the cart while the shopper still owes a hosted
            # payment — see Order.awaits_online_payment. It clears on
            # ``order_paid`` instead.
            if order.awaits_online_payment:
                logger.info(
                    "Kept cart %s — order %s awaits online payment",
                    cart.uuid,
                    order.id,
                )
            else:
                cart.items.all().delete()
                logger.info("Cleared cart %s after order creation", cart.uuid)

            # Step 9: Return order in PENDING status
            # Note: Webhook will move order to PROCESSING when payment is confirmed
            logger.info(
                "Order %s created successfully from cart %s with payment %s",
                order.id,
                cart.uuid,
                payment_intent_id,
            )

            return order

        except (
            ProductNotFoundError,
            InsufficientStockError,
            InvalidCouponError,
            InvalidGiftCardError,
            InvalidOrderDataError,
            PaymentNotFoundError,
            PaymentVerificationError,
            PaymentAmountMismatchError,
            PaymentCurrencyMismatchError,
        ):
            raise
        except Exception as e:
            logger.error(
                "Unexpected error creating order from cart: %s",
                e,
                exc_info=True,
            )
            raise InvalidOrderDataError(
                _("Failed to create order: {error}").format(error=str(e))
            ) from e

    @classmethod
    @transaction.atomic
    def create_order_from_cart_offline(
        cls,
        cart,
        shipping_address: dict[str, Any],
        pay_way,
        user=None,
        loyalty_points_to_redeem: int | None = None,
        gift_card_codes: list[str] | None = None,
        meta_context: dict[str, Any] | None = None,
    ) -> Order:
        """
        Create order from cart using the order-first flow.

        Used for offline payments (COD, Bank Transfer) and redirect-based
        online providers (Viva Wallet). It performs the following steps:
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

        References:
            - Design Section "Order Service"
            - Dual-Flow Payment Architecture

        Example:
            >>> order = OrderService.create_order_from_cart_offline(
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
        try:
            from cart.models import Cart

            # Lock the Cart row immediately so concurrent checkouts on the
            # same cart are serialised.  Must happen before any reads so
            # validate_cart_for_checkout sees the locked snapshot.
            cart = Cart.objects.select_for_update().get(pk=cart.pk)

            # Step 1: Validate cart for checkout
            validation_result = cls.validate_cart_for_checkout(cart)
            if not validation_result.get("valid", False):
                raise InvalidOrderDataError(
                    _("Cart validation failed: {errors}").format(
                        errors=", ".join(validation_result.get("errors", []))
                    )
                )

            # Step 2: Validate shipping address
            cls.validate_shipping_address(shipping_address, pay_way=pay_way)

            # Step 2.4: Re-bind wholesale pricing on the LOCKED cart
            # (see the payment-first path for the rationale).
            from b2b.services import (
                B2BPricingService,
                B2BService,
            )

            B2BPricingService.bind_cart(cart, user)

            # Wholesale carts sit outside the loyalty program unless the
            # merchant opts in (see the payment-first path).
            if B2BService.suppresses_loyalty(cart):
                loyalty_points_to_redeem = 0

            # Wholesale minimum-order-value gate (standard B2B term).
            # Checked here AND at payment-intent creation so an online
            # payment can never be captured for an order this would
            # reject.
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

            # Step 2.5: Evaluate promotions under lock (see the
            # payment-first path for the race/typed-error rationale).
            promo_result = cls._evaluate_promotions(
                cart, user=user, email=shipping_address.get("email") or ""
            )
            promo_discount = promo_result.discount_total

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
            cart_items = list(
                cart.items.select_for_update().select_related("product")
            )

            # Reservations (active, expired, or duplicated) are reconciled
            # against the cart in Step 7 via _consume_stock_for_order. Expired
            # holds need no special handling here (see the payment-first path).

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
                # Shipping address fields
                "first_name": shipping_address.get("first_name"),
                "last_name": shipping_address.get("last_name"),
                "email": shipping_address.get("email"),
                "street": shipping_address.get("street"),
                "street_number": shipping_address.get("street_number"),
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

            # Calculate shipping cost — pass cart weight so ACS live
            # quotes match the weight-banded tariff bracket the voucher
            # mint will charge.
            from shipping.utils import compute_total_weight_grams

            cart_total = cart.total_price
            cart_weight_grams = compute_total_weight_grams(
                (ci.product, ci.quantity) for ci in cart_items
            ) + PromotionEngine.gift_weight_grams(promo_result)
            if promo_result.free_shipping:
                shipping_cost = Money(0, cart_total.currency)
            else:
                shipping_cost = cls.calculate_shipping_cost(
                    order_value=cart_total,
                    country_id=shipping_address.get("country_id"),
                    region_id=shipping_address.get("region_id"),
                    shipping_provider_code=shipping_address.get(
                        "shipping_provider_code"
                    ),
                    shipping_kind=shipping_address.get("shipping_kind"),
                    weight_grams=cart_weight_grams,
                )
            order_data["shipping_price"] = shipping_cost

            # Calculate payment method fee
            # Note: Payment fee is calculated on the DISCOUNTED items
            # total + shipping — mirrors the payment-first path.
            order_subtotal = Money(
                cart_total.amount
                - promo_discount.amount
                + shipping_cost.amount,
                cart_total.currency,
            )
            payment_fee = cls.calculate_payment_method_fee(
                pay_way=pay_way,
                order_value=order_subtotal,
            )
            order_data["payment_method_fee"] = payment_fee

            order_data.setdefault(
                "shipping_provider_code",
                shipping_address.get("shipping_provider_code"),
            )
            order_data.setdefault(
                "shipping_kind", shipping_address.get("shipping_kind")
            )
            cls._resolve_shipping_provider(order_data)
            cls._seed_language_code(order_data)

            order = Order.objects.create(**order_data)

            # Set payment_id for offline payments only.
            # Online redirect providers (Viva Wallet) get payment_id
            # from the webhook after payment completes.
            if not pay_way.is_online_payment:
                order.payment_id = f"offline_{order.uuid}"
                order.save(update_fields=["payment_id"])

            # Reuse the already-locked/materialised cart_items list rather
            # than issuing a second SELECT on the same rows.
            cart_items_pairs = [(ci.product, ci.quantity) for ci in cart_items]

            # Provider-agnostic shipment row creation via the registry.
            from shipping.services import ShippingService

            ShippingService.create_shipment_row_for_order(
                order, payload=shipping_address, items=cart_items_pairs
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
                "payment_type": "offline",
            }
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
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=quantity,
                    price=item_price,
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
            if (
                loyalty_points_to_redeem
                and loyalty_points_to_redeem > 0
                and user
            ):
                try:
                    from loyalty.services import LoyaltyService

                    # Cap discount to products total (excluding shipping/fees)
                    items_total_amount = order.total_price_items.amount

                    # Redeem points and get discount amount
                    discount_amount = LoyaltyService.redeem_points(
                        user=user,
                        points_amount=loyalty_points_to_redeem,
                        currency=str(target_currency),
                        order=order,
                        max_discount=items_total_amount,
                    )
                    loyalty_discount = Money(discount_amount, target_currency)

                    # Store loyalty redemption in order metadata
                    order.metadata["loyalty_redemption"] = {
                        "points_redeemed": loyalty_points_to_redeem,
                        "discount_amount": str(discount_amount),
                        "currency": str(target_currency),
                    }

                    logger.info(
                        "Applied loyalty discount of %s %s (%s points) to order %s",
                        discount_amount,
                        target_currency,
                        loyalty_points_to_redeem,
                        order.id,
                    )
                except ValidationError:
                    raise
                except Exception as e:
                    logger.error(
                        "Failed to apply loyalty discount to order %s: %s",
                        order.id,
                        e,
                        exc_info=True,
                    )
                    # Fail loud, like the payment-first path. The offline
                    # providers (COD, Viva) have no amount guard, so
                    # swallowing this charged the shopper the undiscounted
                    # total while the checkout sidebar had shown the
                    # discount and the points stayed unspent — the exact
                    # outcome _evaluate_promotions' docstring says a
                    # refused discount must never produce. Raising rolls
                    # the whole creation back so the shopper retries and
                    # sees a price that matches what they are charged.
                    raise InvalidOrderDataError(
                        str(_("Loyalty redemption failed."))
                    ) from e

            # Step 6.6: Record promotion redemptions (rows + metadata)
            # for the evaluation done under lock at Step 2.5.
            order.discount_amount = PromotionEngine.record(order, promo_result)
            order.loyalty_discount = loyalty_discount

            # Step 6.7: Redeem gift cards LAST — they are payment, not
            # discount, so they settle whatever is still due after the
            # promotion and loyalty deductions.
            order.gift_card_amount = cls._redeem_gift_cards(
                gift_card_codes, order
            )

            # Persist the deductions so they survive on the order, then
            # let calculate_order_total_amount() be the single authority
            # on what the customer owes. Subtracting here and leaving
            # the order's own total undiscounted is what let every
            # charge site bill the full amount while the points were
            # burnt.
            order.paid_amount = order.calculate_order_total_amount()
            order.save(
                update_fields=[
                    "discount_amount",
                    "discount_amount_currency",
                    "loyalty_discount",
                    "loyalty_discount_currency",
                    "gift_card_amount",
                    "gift_card_amount_currency",
                    "paid_amount",
                    "paid_amount_currency",
                    "metadata",
                ]
            )

            # The coupon's durable record is the redemption row — the
            # cart attachment has served its purpose.
            CouponService.clear_after_order(cart)

            # Step 6.8: Deductions covering the FULL total settle the
            # order right here — no provider ever gets involved (a
            # zero-amount PaymentIntent would be rejected anyway).
            # Gift cards are the usual cause; a 100% promotion or a
            # full loyalty redemption produces the same zero
            # remainder and settles identically. Anything LESS than
            # full coverage on a non-redirect online pay way is a
            # client error: this path only runs without a payment
            # intent, so there is nothing to charge the remainder
            # with.
            deductions_present = (
                order.gift_card_amount.amount > 0
                or order.discount_amount.amount > 0
                or order.loyalty_discount.amount > 0
            )
            fully_covered = deductions_present and order.paid_amount.amount == 0
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
            elif (
                pay_way.is_online_payment
                and pay_way.provider_code not in _REDIRECT_PROVIDER_CODES
            ):
                if gift_card_codes:
                    raise InvalidGiftCardError(
                        reason="gift_card_insufficient",
                        message=(
                            "Gift cards do not cover the order total; "
                            "an online payment intent is required for "
                            "the remainder."
                        ),
                    )
                message = str(
                    _(
                        "A payment intent is required — the applied "
                        "discounts do not cover the order total."
                    )
                )
                raise InvalidOrderDataError(
                    message,
                    field_errors={"payment_intent_id": [message]},
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
            # Online providers that route through this method (Viva
            # Wallet) defer dispatch to the payment webhook so the
            # courier voucher only mints after the shopper actually pays.
            if not pay_way.is_online_payment or fully_covered:
                cls._dispatch_shipment_creation_task(order)

            # Step 9: Return order in PENDING status
            logger.info(
                "Order %s created successfully from cart %s (order-first, %s)",
                order.id,
                cart.uuid,
                pay_way.provider_code or "offline",
            )

            return order

        except (
            ProductNotFoundError,
            InsufficientStockError,
            InvalidCouponError,
            InvalidGiftCardError,
            InvalidOrderDataError,
        ):
            raise
        except Exception as e:
            logger.error(
                "Unexpected error creating order from cart (order-first): %s",
                e,
                exc_info=True,
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
                    'warnings': list[str]
                }

        Example:
            >>> result = OrderService.validate_cart_for_checkout(cart)
            >>> if not result['valid']:
            ...     print(f"Validation failed: {result['errors']}")
            >>> if result['warnings']:
            ...     print(f"Warnings: {result['warnings']}")
        """
        errors = []
        warnings = []

        # Get cart items with optimized prefetching
        cart_items = cart.get_items()

        # Check 1: Cart not empty
        if not cart_items.exists():
            errors.append(_("Cart is empty"))
            return {
                "valid": False,
                "errors": errors,
                "warnings": warnings,
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

            # Check product is active
            if product.active is False:
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
        - street, street_number, city, zipcode
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

        errors = {}

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

        # If there are errors, raise ValidationError
        if errors:
            raise ValidationError(errors)

    @classmethod
    @transaction.atomic
    def update_order_status(
        cls,
        order: Order,
        new_status: str,
        *,
        silent_for_customer: bool = False,
    ) -> Order:
        """Validate + apply a status transition.

        ``silent_for_customer=True`` pre-stamps the suppression flags
        (see ``_suppress_customer_status_notifications``) so the
        customer email/WS toast for THIS transition is skipped while
        history, signals and internal state still flow. Used by chained
        transitions — e.g. the carrier bridge that walks PROCESSING →
        SHIPPED → RETURNED must not tell the customer "your order is on
        the way" moments before the return notification.
        """
        if not new_status:
            raise ValueError("New status cannot be empty")

        # Lock + re-read the CURRENT status so concurrent transitions
        # serialize and validate against the committed row, not the
        # caller's stale snapshot (G0285). Sync the caller's object to
        # the committed status so callers that read ``order`` after this
        # (rather than the returned instance) stay consistent.
        current_status = (
            Order.objects.select_for_update()
            .values_list("status", flat=True)
            .get(pk=order.pk)
        )
        order.status = current_status

        if order.status == new_status:
            logger.info("Order %s status is already %s", order.id, new_status)
            return order

        allowed_transitions = {
            OrderStatus.PENDING: [
                OrderStatus.PROCESSING,
                OrderStatus.CANCELED,
            ],
            OrderStatus.PROCESSING: [
                OrderStatus.SHIPPED,
                OrderStatus.CANCELED,
            ],
            OrderStatus.SHIPPED: [
                OrderStatus.DELIVERED,
                OrderStatus.RETURNED,
            ],
            OrderStatus.DELIVERED: [
                OrderStatus.COMPLETED,
                OrderStatus.RETURNED,
            ],
            OrderStatus.CANCELED: [],
            OrderStatus.COMPLETED: [],
            OrderStatus.RETURNED: [OrderStatus.REFUNDED],
            OrderStatus.REFUNDED: [],
        }

        if new_status not in allowed_transitions.get(order.status, []):
            logger.warning(
                "Invalid status transition for order %s: from %s to %s",
                order.id,
                order.status,
                new_status,
            )
            raise InvalidStatusTransitionError(
                current_status=order.status,
                new_status=new_status,
                allowed=[
                    str(s) for s in allowed_transitions.get(order.status, [])
                ],
            )

        old_status = order.status

        if silent_for_customer:
            cls._suppress_customer_status_notifications(order, new_status)

        order.status = new_status
        order.status_updated_at = timezone.now()
        order.save(update_fields=["status", "status_updated_at"])

        # Note: order_status_changed signal is sent automatically by
        # handle_order_post_save when the status changes.

        logger.info(
            "Order %s status updated from %s to %s",
            order.id,
            old_status,
            new_status,
        )

        return order

    @classmethod
    def _suppress_customer_status_notifications(
        cls, order: Order, new_status: str
    ) -> None:
        """Pre-stamp metadata so the next ``new_status`` transition
        skips the customer email + WS toast.

        Used by chained transitions (DELIVERED → COMPLETED auto-advance,
        admin tracking promotion that hops PENDING → PROCESSING → SHIPPED,
        online payment succeeded firing PENDING → PROCESSING right
        before the order_received confirmation email lands) where the
        chained-into status would arrive at the customer's inbox /
        notification bell within ~ms of the previous one and feel like
        spam.

        Internal state still flows: ``order_status_changed`` signal
        fires, OrderHistory logs the transition, the post-save handler
        runs. Only the user-visible ``send_order_status_update_email``
        + ``notify_order_status_changed_live`` dispatches are skipped.

        ``_status_update_reservation_key`` is the same key the email
        task uses to dedupe — pre-stamping it makes the task short-
        circuit. The matching ``suppress_status_ws_<status>`` flag
        is read by ``handle_order_status_changed`` for the live-
        notification dispatch.
        """
        from order.tasks import _status_update_reservation_key

        email_flag = _status_update_reservation_key(order.id, new_status)
        ws_flag = f"suppress_status_ws_{new_status}"
        if not order.metadata:
            order.metadata = {}
        order.metadata[email_flag] = True
        order.metadata[ws_flag] = True
        order.save(update_fields=["metadata"])

    @classmethod
    def maybe_advance_to_completed(
        cls, order: Order, *, silent_for_customer: bool = False
    ) -> Order:
        """Auto-advance ``order`` from DELIVERED to COMPLETED when paid.

        The canonical state-machine table allows DELIVERED → COMPLETED,
        but nothing in the wild was actually invoking it: online orders
        ended at DELIVERED, COD orders ended at DELIVERED + payment_
        status=PENDING (until the reconcile pass flips them). Without
        this helper, "completed" was an admin-only manual flip.

        Triggers from two call-sites:
        * Carrier event handlers (ACS poll, BoxNow webhook) right after
          they advance to DELIVERED for an already-paid online order.
          Pass ``silent_for_customer=True`` here — the customer just
          got the DELIVERED email + toast and a COMPLETED message ~ms
          later would feel like a duplicate.
        * COD reconcile, after flipping payment_status to COMPLETED.
          Also passes ``silent_for_customer=True`` — the customer paid
          the courier in person and already received the DELIVERED
          notification; the reconcile is internal bookkeeping
          (site-owner decision 2026-07-11: it never emails customers).

        Idempotent and silent when the order is not eligible — a
        non-paid order or one already past DELIVERED no-ops.

        Status fields are read with ``values_list`` rather than
        ``refresh_from_db(fields=...)``: the latter leaves other
        columns deferred, and ``Order.__init__`` lazy-loads them when
        it snapshots ``_original_tracking_number`` etc., which
        recurses through the manager.
        """
        row = (
            Order.objects.filter(pk=order.pk)
            .values("status", "payment_status")
            .first()
        )
        if not row:
            return order
        if row["status"] != OrderStatus.DELIVERED:
            return order
        if row["payment_status"] != PaymentStatus.COMPLETED:
            return order
        order.status = row["status"]
        order.payment_status = row["payment_status"]
        if silent_for_customer:
            cls._suppress_customer_status_notifications(
                order, OrderStatus.COMPLETED.value
            )
        try:
            return cls.update_order_status(order, OrderStatus.COMPLETED)
        except InvalidStatusTransitionError as exc:
            logger.warning(
                "Order %s DELIVERED -> COMPLETED auto-advance rejected by "
                "state machine: %s",
                order.id,
                exc,
            )
            return order

    @classmethod
    def get_user_orders(cls, user_id: int) -> QuerySet:
        """Get all orders for a user with optimized queryset."""
        return (
            Order.objects.for_list()
            .filter(user_id=user_id)
            .order_by("-created_at")
        )

    @classmethod
    @transaction.atomic
    def reorder_to_cart(cls, order: Order, user) -> dict[str, Any]:
        """Add each item from a past order back into the user's active cart.

        Items with insufficient stock or inactive products are recorded in
        `skipped_items` rather than rejecting the whole reorder. Quantities
        are capped at current stock.
        """
        from cart.models import Cart, CartItem

        cart, _created = Cart.objects.get_or_create(user=user)

        added: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for item in order.items.select_related("product").all():
            product = item.product
            requested = item.quantity

            if not getattr(product, "active", True):
                skipped.append(
                    {
                        "product_id": product.id,
                        "requested_quantity": requested,
                        "added_quantity": 0,
                        "reason": "inactive",
                    }
                )
                continue

            available = getattr(product, "stock", 0) or 0
            if available <= 0:
                skipped.append(
                    {
                        "product_id": product.id,
                        "requested_quantity": requested,
                        "added_quantity": 0,
                        "reason": "out_of_stock",
                    }
                )
                continue

            to_add = min(requested, available)

            existing = CartItem.objects.filter(
                cart=cart, product=product
            ).first()
            if existing:
                existing.quantity += to_add
                existing.save(update_fields=["quantity"])
            else:
                CartItem.objects.create(
                    cart=cart, product=product, quantity=to_add
                )

            entry = {
                "product_id": product.id,
                "requested_quantity": requested,
                "added_quantity": to_add,
                "reason": "partial" if to_add < requested else "",
            }
            if to_add < requested:
                skipped.append(entry)
            added.append(entry)

        return {
            "cart_id": cart.id,
            "added_items": added,
            "skipped_items": skipped,
        }

    @classmethod
    @transaction.atomic
    def cancel_order(
        cls,
        order: Order,
        reason: str = "",
        refund_payment: bool = True,
        canceled_by: int | None = None,
    ) -> tuple[Order, dict[str, Any] | None]:
        # Lock the order row to prevent concurrent cancellation requests
        # from both restoring stock
        order = Order.objects.select_for_update().get(id=order.id)

        if not order.can_be_canceled:
            error_message = _(
                "Order in status {status} cannot be canceled. "
                "Only orders in PENDING or PROCESSING status can be canceled."
            ).format(status=order.status)
            logger.warning(
                "Cannot cancel order %s: %s", order.id, error_message
            )
            raise OrderCancellationError(
                order_id=order.id, reason=error_message
            )

        try:
            # Release stock reservations if they exist
            reservation_ids = (
                order.metadata.get("stock_reservation_ids", [])
                if order.metadata
                else []
            )
            for reservation_id in reservation_ids:
                try:
                    StockManager.release_reservation(reservation_id)
                    logger.info(
                        "Released reservation %s for canceled order %s",
                        reservation_id,
                        order.id,
                    )
                except StockReservationError as e:
                    # The periodic cleanup task runs every 5 min and
                    # flips expired reservations to consumed=True. On
                    # stale cancels (e.g. auto_cancel_stuck_pending_orders
                    # on 24h-old PENDING orders) this is the normal
                    # happy path, not an error — log at DEBUG.
                    if "already consumed" in str(e):
                        logger.debug(
                            "Reservation %s already consumed for order %s (expected for stale cancels)",
                            reservation_id,
                            order.id,
                        )
                    else:
                        logger.warning(
                            "Failed to release reservation %s: %s",
                            reservation_id,
                            e,
                        )
                except Exception as e:
                    logger.warning(
                        "Failed to release reservation %s: %s",
                        reservation_id,
                        e,
                    )
                    # Continue with other reservations even if one fails

            # Restore the stock this order is actually HOLDING, which is
            # not the same as the quantities it lists. Only two operations
            # move physical stock — decrement_stock and
            # convert_reservation_to_sale, both logged as DECREMENT against
            # the order — while an OrderItem created any other way (the
            # standalone OrderItem admin, a repair script) never decremented
            # anything: handle_order_item_post_save deliberately skips it to
            # avoid double-counting the checkout path. Restoring per listed
            # item therefore invented stock that never left the shelf.
            #
            # Summing the order's own physical movements gets every case
            # right: nothing taken restores nothing, a partially restored
            # order restores only the remainder, and cancelling twice is a
            # no-op because the first restore's INCREMENT rows net it out.
            # RESERVE/RELEASE rows are excluded — a reservation is a logical
            # hold and leaves physical stock untouched (stock_before ==
            # stock_after).
            outstanding = (
                StockLog.objects.filter(
                    order_id=order.id,
                    operation_type__in=(
                        StockLog.OPERATION_DECREMENT,
                        StockLog.OPERATION_INCREMENT,
                    ),
                )
                .values("product_id")
                .annotate(net=Sum("quantity_delta"))
            )
            for row in outstanding:
                # ``net`` is a Sum over a non-null column, and every row
                # here IS a group, so it is always an int.
                quantity = -row["net"]
                if quantity <= 0:
                    continue
                product_id = row["product_id"]
                try:
                    StockManager.increment_stock(
                        product_id=product_id,
                        quantity=quantity,
                        order_id=order.id,
                        reason=f"Order {order.id} canceled: {reason}",
                    )
                    logger.info(
                        "Restored stock for product %s: +%s (order %s canceled)",
                        product_id,
                        quantity,
                        order.id,
                    )
                except Exception as e:
                    logger.error(
                        "Failed to restore stock for product %s: %s",
                        product_id,
                        e,
                        exc_info=True,
                    )
                    # Continue with other products even if one fails

            old_status = order.status
            order.status = OrderStatus.CANCELED
            order.status_updated_at = timezone.now()

            if not order.metadata:
                order.metadata = {}

            order.metadata["cancellation"] = {
                "reason": reason,
                "canceled_at": timezone.now().isoformat(),
                "canceled_by": canceled_by,
                "previous_status": old_status,
            }

            order.save(
                update_fields=["status", "status_updated_at", "metadata"]
            )

            # order_canceled signal is dispatched by
            # handle_order_status_changed via the post_save chain.
            # Do not send it manually here to avoid double-firing.

            logger.info(
                "Order %s canceled successfully (previous status: %s)",
                order.id,
                old_status,
            )

            # Cascade to the courier voucher synchronously here so the
            # existing programmatic API contract (cancel-order callers
            # see ``metadata.cancellation.shipment_cancel`` populated
            # before this method returns) is preserved. The signal-side
            # cascade in ``handle_order_canceled`` covers paths that
            # bypass this method (admin form save) — it short-circuits
            # when ``shipment_cancel`` is already on the metadata, so
            # we don't double-fire from this entry point.
            cls.cancel_attached_shipment(order, reason)

            refund_info = None
            if (
                refund_payment
                and order.is_paid
                and order.payment_id
                and order.pay_way
            ):
                try:
                    success, refund_response = cls.refund_order(
                        order=order,
                        amount=None,
                        reason=f"Order canceled: {reason}"
                        if reason
                        else "Order canceled",
                        refunded_by=canceled_by,
                    )

                    if success:
                        refund_info = {
                            "refunded": True,
                            "refund_id": refund_response.get("refund_id"),
                            "message": "Payment refunded successfully",
                        }
                    else:
                        # ``refund_response["error"]`` carries raw provider
                        # exception text (see order/payment.py) — log it,
                        # never return it (CodeQL py/stack-trace-exposure).
                        logger.error(
                            "Refund failed for canceled order %s: %s",
                            order.id,
                            refund_response.get("error"),
                        )
                        refund_info = {
                            "refunded": False,
                            "error": str(
                                _("The refund could not be processed.")
                            ),
                            "message": "Order canceled but refund failed",
                        }
                except Exception as refund_error:
                    logger.error(
                        "Error processing refund for canceled order %s: %s",
                        order.id,
                        refund_error,
                        exc_info=True,
                    )
                    refund_info = {
                        "refunded": False,
                        "error": str(_("The refund could not be processed.")),
                        "message": "Order canceled but refund failed",
                    }

            return order, refund_info

        except Exception as e:
            logger.error(
                "Error canceling order %s: %s", order.id, e, exc_info=True
            )
            raise OrderCancellationError(
                order_id=order.id,
                reason=_("Failed to cancel order: {error}").format(
                    error=str(e)
                ),
            ) from e

    @classmethod
    @transaction.atomic
    def refund_order(
        cls,
        order: Order,
        amount: Money | None = None,
        reason: str = "",
        refunded_by: int | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        # Lock the row and re-read the CURRENT payment_status so a refund
        # another request already committed is detected here — otherwise two
        # concurrent refunds both pass the stale in-memory guard and issue
        # duplicate provider refunds (G0280). We keep mutating the caller's
        # ``order`` object below so callers still observe the result.
        current_payment_status = (
            Order.objects.select_for_update()
            .values_list("payment_status", flat=True)
            .get(pk=order.pk)
        )
        if current_payment_status in (
            PaymentStatus.REFUNDED,
            PaymentStatus.PARTIALLY_REFUNDED,
        ):
            raise PaymentError(_("This order has already been refunded."))

        if not order.payment_id:
            raise PaymentError(_("This order has no payment ID to refund."))

        if not order.is_paid:
            raise PaymentError(_("This order has not been paid yet."))

        if order.payment_status in (
            PaymentStatus.REFUNDED,
            PaymentStatus.PARTIALLY_REFUNDED,
        ):
            raise PaymentError(_("This order has already been refunded."))

        if not order.pay_way:
            raise PaymentError(
                _("This order has no payment method configured.")
            )

        if amount and amount.amount > order.paid_amount.amount:
            raise PaymentError(
                _(
                    "Refund amount ({refund}) cannot exceed paid amount ({paid})."
                ).format(
                    refund=amount.amount,
                    paid=order.paid_amount.amount,
                )
            )

        from order.payment import get_payment_provider

        provider = get_payment_provider(order.pay_way.provider_code)
        success, refund_response = provider.refund_payment(
            payment_id=order.payment_id, amount=amount
        )

        if not success:
            return False, refund_response

        order.payment_status = refund_response.get(
            "status", PaymentStatus.REFUNDED
        )

        if not order.metadata:
            order.metadata = {}

        if "refunds" not in order.metadata:
            order.metadata["refunds"] = []

        order.metadata["refunds"].append(
            {
                "refund_id": refund_response.get("refund_id"),
                "amount": str(amount.amount) if amount else "full",
                "currency": str(amount.currency)
                if amount
                else str(order.total_price.currency),
                "reason": reason,
                "refunded_at": timezone.now().isoformat(),
                "refunded_by": refunded_by,
            }
        )

        order.save(update_fields=["payment_status", "metadata"])

        order_refunded.send(
            sender=cls, order=order, amount=amount, reason=reason
        )

        logger.info("Order %s refunded successfully", order.id)

        return True, refund_response

    @classmethod
    def _apply_polled_payment_status(
        cls, order: Order, payment_status_enum: PaymentStatus
    ) -> None:
        """Write a POLLED provider status, unless the order is settled.

        Polling reports the provider's view of the payment, which is not
        the order's view of the money. A Stripe PaymentIntent stays
        ``succeeded`` after the charge is refunded, so a settled order
        polled by anyone — including a customer opening the order page,
        which reaches this through the read-only ``payment_status``
        action — would be written back to COMPLETED and lose the refund.

        Same rule the webhook handlers already follow: a settled state is
        final, and the row is re-read under lock so a concurrent webhook
        either lands first or is seen.
        """
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
            if payment_status_enum == locked.payment_status:
                order.payment_status = locked.payment_status
                return
            logger.info(
                "Updating order %s payment status from %s to %s",
                order.id,
                locked.payment_status,
                payment_status_enum,
            )
            locked.payment_status = payment_status_enum
            locked.save(update_fields=["payment_status"])
            order.payment_status = payment_status_enum

    @classmethod
    def get_payment_status(
        cls,
        order: Order,
        update_order: bool = True,
    ) -> tuple[PaymentStatus, dict[str, Any]]:
        if not order.payment_id:
            raise PaymentError(_("This order has no payment ID."))

        if not order.pay_way:
            raise PaymentError(
                _("This order has no payment method configured.")
            )

        from order.payment import get_payment_provider

        provider = get_payment_provider(order.pay_way.provider_code)
        payment_status_enum, status_data = provider.get_payment_status(
            payment_id=order.payment_id
        )

        if update_order and payment_status_enum != order.payment_status:
            cls._apply_polled_payment_status(order, payment_status_enum)

        return payment_status_enum, status_data

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
            cls.update_order_status(order, OrderStatus.SHIPPED)
        elif order.status == OrderStatus.PENDING:
            # Two-hop chain → suppress the intermediate PROCESSING
            # email + WS toast. The customer cares that the order has
            # SHIPPED; getting "your order is being prepared" then
            # "your order is shipped" within the same second is the
            # admin path's only remaining duplicate. The PROCESSING
            # transition still fires the signal + OrderHistory row,
            # only the user-visible dispatches are skipped.
            cls._suppress_customer_status_notifications(
                order, OrderStatus.PROCESSING.value
            )
            cls.update_order_status(order, OrderStatus.PROCESSING)
            cls.update_order_status(order, OrderStatus.SHIPPED)
        else:
            try:
                cls.update_order_status(order, OrderStatus.SHIPPED)
            except ValueError, InvalidStatusTransitionError:
                logger.warning(
                    "Could not update order %s to SHIPPED status from %s",
                    order.id,
                    order.status,
                )

        order.refresh_from_db()
        return order

    @classmethod
    def _resolve_shipping_provider(cls, order_data: dict[str, Any]) -> None:
        """Resolve ``shipping_provider_code`` → ``shipping_provider`` FK.

        Mutates ``order_data`` in place: removes ``shipping_provider_code``
        and replaces it with the resolved ``shipping_provider`` (a
        ``ShippingProvider`` instance). Defaults ``shipping_kind`` to
        ``home_delivery`` when not supplied.

        Dispatch is registry-driven from the explicit
        ``(shipping_provider_code, shipping_kind)`` pair only.
        ``home_delivery`` orders without an explicit code auto-route
        to whichever active provider advertises
        ``supports_home_delivery=True`` — adding a new courier (ELTA,
        Speedex …) is then a one-row admin change.
        """
        from shipping.models import ShippingProvider

        code = order_data.pop("shipping_provider_code", None) or None
        kind = order_data.get("shipping_kind") or "home_delivery"

        # Dynamic-routing fallback: a plain ``home_delivery`` request
        # auto-routes to the active home-delivery carrier. Shared with
        # ``calculate_shipping_cost`` so the cost we charge and the
        # carrier the order is assigned to always agree.
        if not code and kind == "home_delivery":
            code = cls._resolve_active_home_delivery_code()

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

    @classmethod
    def _resolve_active_home_delivery_code(cls) -> str | None:
        """Return the active home-delivery carrier's code, or None.

        Lower ``ShippingProvider.priority`` wins the tie. Adding a new
        courier (ELTA, Speedex …) is then a one-row Django admin
        change — no order-flow code touched.

        Shared between :meth:`calculate_shipping_cost` (pricing) and
        :meth:`_resolve_shipping_provider` (FK assignment) so both
        callers route ``home_delivery`` through the SAME carrier;
        otherwise the order would be charged against one carrier's
        threshold and served by another, producing the kind of silent
        UI/charge mismatch that motivated this helper.
        """
        from shipping.models import ShippingProvider

        picked = (
            ShippingProvider.objects.filter(
                is_active=True, supports_home_delivery=True
            )
            .order_by("priority", "code")
            .first()
        )
        return picked.code if picked is not None else None

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

        Pulled from ``django.utils.translation.get_language`` (set
        by ``LocaleMiddleware`` from the i18n cookie + Accept-Language
        header) so views don't need to thread a ``request`` argument
        through. Validated against ``settings.LANGUAGES`` so a stray
        unknown code never lands in the DB.
        """
        if order_data.get("language_code"):
            return
        candidate = (get_language() or "").split("-")[0].strip().lower()
        valid = {code for code, _name in settings.LANGUAGES}
        order_data["language_code"] = (
            candidate if candidate in valid else settings.LANGUAGE_CODE
        )

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
    def cancel_attached_shipment(cls, order: Order, reason: str) -> None:
        """Best-effort: cancel the courier voucher when the order is canceled.

        Called from ``handle_order_canceled`` so the cascade fires for
        every code path that produces ``order.status = CANCELED`` —
        including admin form saves that go straight to ``Order.save()``
        without touching :meth:`OrderService.cancel_order`. Verified
        against prod order 60 on 2026-05-16: the admin status dropdown
        was set to CANCELED via the change form, leaving voucher
        9771614856 alive at ACS because the cascade lived only inside
        ``cancel_order``.

        Routed through ``ShippingService.cancel_shipment`` so each
        carrier enforces its own cancellability rules. Common
        rejections (ACS voucher already in a pickup list, BoxNow
        parcel already accepted at a locker) are recorded on
        ``order.metadata['cancellation']['shipment_cancel']`` so the
        admin can see why the cascade didn't reach the courier and
        coordinate the in-transit return out of band.

        Never raises — order cancellation must be allowed to complete
        even when the courier-side cancel fails.
        """
        from shipping.services import ShippingService

        # Idempotency: short-circuit when the cascade has already run for
        # this order. The two entry points (programmatic ``cancel_order``
        # explicit call AND the ``order_canceled`` signal-side safety
        # net) can otherwise double-fire — with the test
        # ``on_commit``-immediate fixture, the signal cascade lands
        # synchronously during the first ``order.save()`` inside
        # ``cancel_order``, then the explicit call inside the same
        # method would run again.
        existing_cancellation = (order.metadata or {}).get("cancellation") or {}
        if "shipment_cancel" in existing_cancellation:
            return

        logger.info(
            "Cascading order cancel to carrier voucher | order=%s reason=%r",
            order.id,
            reason,
        )

        info: dict[str, Any] = {}
        try:
            dispatched = ShippingService.cancel_shipment(order, reason=reason)
            info = {
                "attempted": True,
                "dispatched": dispatched,
            }
        except Exception as exc:  # pragma: no cover - logged below
            info = {
                "attempted": True,
                "dispatched": False,
                "error": str(exc),
            }
            logger.warning(
                "Order %s canceled, but courier voucher cancel failed: %s",
                order.id,
                exc,
                exc_info=True,
            )

        if not order.metadata:
            order.metadata = {}
        cancellation = order.metadata.setdefault("cancellation", {})
        cancellation["shipment_cancel"] = info

        # ``.update(...)`` bypasses ``Order.save()`` and its post_save
        # signal cascade — important because this method runs INSIDE
        # the ``order_canceled`` signal handler, which itself fires
        # from inside ``Order.save()``'s post_save chain. A nested
        # ``order.save()`` would see a stale ``_original_status``
        # (refreshed only AFTER the outer ``super().save()`` returns)
        # and re-fire ``order_status_changed``, causing infinite
        # recursion or duplicate-signal failures (verified in CI on
        # 2026-05-16).
        Order.objects.filter(pk=order.pk).update(metadata=order.metadata)

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
    def handle_payment_succeeded(cls, payment_intent_id: str) -> Order | None:
        # Acquire a row lock and hydrate related objects in one query.
        # ``for_detail()`` adds COUNT/SUM annotations which Postgres
        # rejects under FOR UPDATE (aggregate in locked query). We
        # replicate the select_related/prefetch_related chains from
        # for_detail() manually, skipping with_counts() / with_total_amounts().
        from django.db.models import Prefetch

        from order.models.history import OrderHistory
        from order.payment_events import publish_payment_status

        # ``of=("self",)`` restricts the row lock to the Order table.
        # Without it Postgres rejects the query with ``FOR UPDATE cannot
        # be applied to the nullable side of an outer join`` because
        # several of the FKs on Order are nullable (user, pay_way,
        # country, region, shipping_provider) and ``select_related``
        # joins them with LEFT OUTER JOIN.
        order = (
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
                "boxnow_shipment",
                "acs_shipment",
                "acs_shipment__events",
                "acs_shipment__station_destination",
                "invoice",
            )
            .filter(payment_id=payment_intent_id)
            .first()
        )
        if order is None:
            logger.error(
                "Order not found for payment_intent: %s", payment_intent_id
            )
            return None

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
            return order

        order.mark_as_paid(
            payment_id=payment_intent_id, payment_method="stripe"
        )

        if order.status == OrderStatus.CANCELED:
            # Payment landed for an order that was already CANCELED (the
            # customer cancelled before the webhook, or the two raced).
            # Marking the money received above is intentional bookkeeping,
            # but we must NOT mint a courier shipment for a cancelled order
            # (G0281). Record the receipt for reconciliation and alert staff
            # (ERROR is the monitored channel) so a manual refund is issued.
            if not order.metadata:
                order.metadata = {}
            order.metadata["payment_after_cancel"] = {
                "payment_id": payment_intent_id,
                "recorded_at": timezone.now().isoformat(),
            }
            order.save(update_fields=["metadata"])
            logger.error(
                "Payment %s received for CANCELED order %s — manual refund "
                "required; NOT dispatching shipment creation",
                payment_intent_id,
                order.id,
            )
            publish_payment_status(order)
            return order

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
            cls._suppress_customer_status_notifications(
                order, OrderStatus.PROCESSING.value
            )
            cls.update_order_status(order, OrderStatus.PROCESSING)

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
        return order

    @classmethod
    @transaction.atomic
    def handle_payment_failed(cls, payment_intent_id: str) -> Order | None:
        from order.payment_events import publish_payment_status

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
        if order.payment_status in SETTLED_PAYMENT_STATUSES:
            logger.warning(
                "Ignoring stale payment_failed for order %s: "
                "payment_status already %s",
                order.id,
                order.payment_status,
            )
            return order

        order.payment_status = PaymentStatus.FAILED
        order.save(update_fields=["payment_status"])

        publish_payment_status(order)
        logger.info("Order %s payment marked as failed", order.id)
        return order

    @classmethod
    def calculate_shipping_cost(
        cls,
        order_value: Money,
        country_id: int | None = None,
        region_id: int | None = None,
        shipping_provider_code: str | None = None,
        shipping_kind: str | None = None,
        weight_grams: int | None = None,
    ) -> Money:
        from extra_settings.models import Setting

        # Auto-resolve ``home_delivery`` to the active home-delivery
        # provider's code when the caller didn't supply one — mirrors
        # what ``_resolve_shipping_provider`` does for the order FK
        # (same helper, single source of truth) so the cost calc and
        # the assigned carrier always agree.
        #
        # Without this, a ``home_delivery`` cart with no explicit
        # provider_code (the storefront sends ``null`` per
        # ``shared/shipping/index.ts::carrierForMethod`` — home
        # delivery is provider-agnostic at the form level) would fall
        # through to the generic ``FREE_SHIPPING_THRESHOLD`` /
        # ``CHECKOUT_SHIPPING_PRICE`` pair below, charging a flat
        # rate against the wrong threshold. Meanwhile
        # ``_resolve_shipping_provider`` would still set the order's
        # FK to ACS, so the order DB row would look ACS-served but
        # would have generic pricing — a silent disagreement between
        # the carrier the customer was promised and the price they
        # paid.
        if not shipping_provider_code and shipping_kind == "home_delivery":
            shipping_provider_code = cls._resolve_active_home_delivery_code()

        # When we have a (provider, kind) pair, dispatch through the
        # registry so each provider owns its own pricing rules. The
        # adapter has full control over flat rate, dynamic quotes,
        # free-shipping thresholds, and per-country/region overrides.
        if shipping_provider_code and shipping_kind:
            from shipping.services import ShippingService

            quote = ShippingService.calculate_shipping_cost(
                provider_code=shipping_provider_code,
                kind=shipping_kind,
                order_value_amount=float(order_value.amount),
                currency=str(order_value.currency),
                country_id=str(country_id) if country_id else None,
                region_id=str(region_id) if region_id else None,
                weight_grams=weight_grams,
            )
            if quote is not None:
                amount, currency = quote
                return Money(amount, currency)

        # Generic fallback for the rare case with no active
        # home-delivery carrier (a deploy mid-config) — the platform's
        # flat-rate price.
        base_shipping_cost = Setting.get(
            "CHECKOUT_SHIPPING_PRICE", default=3.00
        )
        free_shipping_threshold = Setting.get(
            "FREE_SHIPPING_THRESHOLD", default=50.00
        )

        if order_value.amount >= float(free_shipping_threshold):
            return Money(0, order_value.currency)

        return Money(float(base_shipping_cost), order_value.currency)

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
            order_value: Total order value (items + shipping)

        Returns:
            Money: Payment method fee (0 if free threshold is met)

        Example:
            >>> pay_way = PayWay.objects.get(id=1)
            >>> order_value = Money(45.00, 'EUR')
            >>> fee = OrderService.calculate_payment_method_fee(pay_way, order_value)
            >>> # Returns Money(3.50, 'EUR') if pay_way.cost = 3.50 and threshold not met
        """
        if not pay_way or not pay_way.cost:
            return Money(0, order_value.currency)

        # Check if order value meets free threshold
        if pay_way.free_threshold and pay_way.free_threshold.amount > 0:
            if order_value.amount >= pay_way.free_threshold.amount:
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
