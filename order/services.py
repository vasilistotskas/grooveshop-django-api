import logging
from typing import Any, ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.translation import get_language, gettext_lazy as _
from djmoney.money import Money

from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import (
    InsufficientStockError,
    InvalidOrderDataError,
    InvalidStatusTransitionError,
    OrderCancellationError,
    PaymentAmountMismatchError,
    PaymentCurrencyMismatchError,
    PaymentError,
    PaymentNotFoundError,
    ProductNotFoundError,
    StockReservationError,
)
from order.models.item import OrderItem
from order.models.order import Order
from order.models.stock_reservation import StockReservation
from order.signals import order_refunded
from order.stock import StockManager

# Re-export under the legacy private name so existing tests keep
# passing without churn. The canonical home is ``shipping.utils``;
# new call sites should import from there.
from shipping.utils import (
    compute_total_weight_grams as _compute_total_weight_grams,
)

logger = logging.getLogger(__name__)

__all__ = ["OrderService", "_compute_total_weight_grams"]


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
    except (AttributeError, TypeError):
        return
    logger.warning(
        "Cart price drift at checkout: cart_item=%s product=%s price_at_add=%s charged=%s",
        getattr(cart_item, "id", "?"),
        getattr(getattr(cart_item, "product", None), "id", "?"),
        frozen,
        current_price,
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
    @transaction.atomic
    def create_order(
        cls,
        order_data: dict[str, Any],
        items_data: list[dict[str, Any]],
        user=None,
    ) -> Order:
        try:
            if user and user.is_authenticated:
                order_data["user"] = user

            # Pop provider-specific fields before Order.objects.create —
            # they are not Order columns. The full dict is then handed
            # to the registered carrier adapter so each provider reads
            # its own keys.
            shipment_payload = cls._extract_shipment_payload(order_data)
            cls._resolve_shipping_provider(order_data)
            cls._seed_language_code(order_data)

            order = Order.objects.create(**order_data)

            items_pairs = [
                (item.get("product"), item.get("quantity", 0))
                for item in items_data
            ]

            # One generic dispatch — provider's create_shipment_row
            # handles its own filtering on shipping_method / kind.
            from shipping.services import ShippingService

            ShippingService.create_shipment_row_for_order(
                order, payload=shipment_payload, items=items_pairs
            )

            target_currency = (
                order.shipping_price.currency
                if order.shipping_price
                else settings.DEFAULT_CURRENCY
            )

            for item_data in items_data:
                product = item_data.get("product")
                quantity = item_data.get("quantity", 0)

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
                                product_id=getattr(product, "id", _("unknown")),
                            )
                        )
                    )

                # Atomic stock decrement via StockManager (uses
                # select_for_update() to prevent race conditions).
                StockManager.decrement_stock(
                    product_id=product.id,
                    quantity=quantity,
                    order_id=order.id,
                    reason="order_created",
                )

                item_to_create = item_data.copy()

                product_price = product.final_price
                if product_price.currency != target_currency:
                    item_to_create["price"] = Money(
                        product_price.amount, target_currency
                    )
                else:
                    item_to_create["price"] = product_price

                # bulk_create cannot be used here: post_save on OrderItem
                # writes an OrderHistory audit note per item. Already
                # inside @transaction.atomic so all inserts commit together.
                OrderItem.objects.create(order=order, **item_to_create)

            order.paid_amount = order.calculate_order_total_amount()
            order.save(update_fields=["paid_amount", "paid_amount_currency"])

            logger.info(
                "Order %s created successfully with %s items",
                order.id,
                len(items_data),
            )

            return order

        except (
            ProductNotFoundError,
            InsufficientStockError,
            InvalidOrderDataError,
        ):
            raise
        except Exception as e:
            logger.error(
                "Unexpected error creating order: %s", e, exc_info=True
            )
            raise InvalidOrderDataError(
                _("Failed to create order: {error}").format(error=str(e))
            ) from e

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
            from cart.models import Cart  # noqa: PLC0415

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

            # Step 3: Validate payment intent exists
            from order.payment import get_payment_provider

            if not payment_intent_id:
                raise PaymentNotFoundError(
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
                raise PaymentNotFoundError(
                    _(
                        "Payment intent {payment_id} is in invalid state. Status: {status}"
                    ).format(
                        payment_id=payment_intent_id, status=payment_status
                    )
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
            )
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
            _order_subtotal = Money(
                _cart_total.amount + _shipping_cost.amount,
                _cart_total.currency,
            )
            _payment_fee = cls.calculate_payment_method_fee(
                pay_way=pay_way,
                order_value=_order_subtotal,
            )
            _expected_total = (
                _cart_total.amount + _shipping_cost.amount + _payment_fee.amount
            )
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
                    logger.warning(
                        "Payment amount mismatch for intent %s: "
                        "provider=%d cents, calculated=%d cents",
                        payment_intent_id,
                        provider_amount_cents,
                        calculated_total_cents,
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

            # Check if any reservations have expired
            expired_reservations = [r for r in reservations if r.is_expired]
            active_reservations = [r for r in reservations if not r.is_expired]

            if expired_reservations:
                logger.warning(
                    "Found %d expired reservations for cart %s. Recreating them.",
                    len(expired_reservations),
                    cart.uuid,
                )
                # Release expired reservations
                for expired_res in expired_reservations:
                    try:
                        StockManager.release_reservation(expired_res.id)
                    except Exception as e:
                        logger.warning(
                            "Failed to release expired reservation %s: %s",
                            expired_res.id,
                            e,
                        )

                # Recreate reservations for cart items
                active_reservations = []
                for cart_item in cart_items:
                    try:
                        new_reservation = StockManager.reserve_stock(
                            product_id=cart_item.product.id,
                            quantity=cart_item.quantity,
                            session_id=str(cart.uuid),
                            user_id=cart.user.id if cart.user else None,
                        )
                        active_reservations.append(new_reservation)
                        logger.info(
                            "Created new reservation %s for product %s",
                            new_reservation.id,
                            cart_item.product.id,
                        )
                    except InsufficientStockError as e:
                        logger.error(
                            "Insufficient stock for product %s: %s",
                            cart_item.product.id,
                            e,
                        )
                        raise

                reservations = active_reservations

            if not reservations and cart_items:
                logger.warning(
                    "No stock reservations found for cart %s. "
                    "This may indicate the reservation expired or was never created.",
                    cart.uuid,
                )
                # We'll proceed without reservations and use direct stock decrement
                # This handles cases where reservations expired or weren't created

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
            )
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
            # Note: Payment fee is calculated on items total + shipping
            order_subtotal = Money(
                cart_total.amount + shipping_cost.amount,
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
            sanitised_meta = cls._sanitise_meta_context(meta_context)
            if sanitised_meta:
                order.metadata["meta"] = sanitised_meta

            # Track reservation IDs for this order
            reservation_ids = []

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

                # Get product price and convert to target currency if needed
                product_price = product.final_price
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

            # Step 7: Convert stock reservations to decrements via StockManager
            if reservations:
                # We have reservations - convert them to stock decrements
                for reservation in reservations:
                    try:
                        StockManager.convert_reservation_to_sale(
                            reservation_id=reservation.id, order_id=order.id
                        )
                        reservation_ids.append(reservation.id)
                        logger.info(
                            "Converted reservation %s to sale for order %s",
                            reservation.id,
                            order.id,
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to convert reservation %s: %s",
                            reservation.id,
                            e,
                            exc_info=True,
                        )
                        # Rollback will happen due to @transaction.atomic
                        raise
            else:
                # No reservations - use direct stock decrement
                # This handles cases where reservations expired or weren't created
                logger.info(
                    "No reservations found for cart %s, using direct stock decrement",
                    cart.uuid,
                )
                for order_item in order.items.select_related("product").all():
                    try:
                        StockManager.decrement_stock(
                            product_id=order_item.product.id,
                            quantity=order_item.quantity,
                            order_id=order.id,
                            reason=f"Order {order.id} created from cart {cart.uuid}",
                        )
                        logger.info(
                            "Decremented stock for product %s by %s units",
                            order_item.product.id,
                            order_item.quantity,
                        )
                    except InsufficientStockError as e:
                        logger.error(
                            "Insufficient stock for product %s: %s",
                            order_item.product.id,
                            e,
                        )
                        # Rollback will happen due to @transaction.atomic
                        raise

            # Store reservation IDs in order metadata
            order.metadata["stock_reservation_ids"] = reservation_ids  # type: ignore[invalid-assignment]  # ty: ignore[invalid-assignment]

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
                    # Don't fail the order creation, just log the error
                    # The points won't be redeemed if this fails

            # Calculate and set paid amount (subtract loyalty discount)
            order_total = order.calculate_order_total_amount()
            order.paid_amount = Money(
                max(0, order_total.amount - loyalty_discount.amount),
                order_total.currency,
            )
            order.save(
                update_fields=[
                    "paid_amount",
                    "paid_amount_currency",
                    "metadata",
                ]
            )

            # Step 8: Clear cart
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
            InvalidOrderDataError,
            PaymentNotFoundError,
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
            from cart.models import Cart  # noqa: PLC0415

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

            # Check if any reservations have expired
            expired_reservations = [r for r in reservations if r.is_expired]
            active_reservations = [r for r in reservations if not r.is_expired]

            if expired_reservations:
                logger.warning(
                    "Found %d expired reservations for cart %s. Recreating them.",
                    len(expired_reservations),
                    cart.uuid,
                )
                # Release expired reservations
                for expired_res in expired_reservations:
                    try:
                        StockManager.release_reservation(expired_res.id)
                    except Exception as e:
                        logger.warning(
                            "Failed to release expired reservation %s: %s",
                            expired_res.id,
                            e,
                        )

                # Recreate reservations for cart items
                active_reservations = []
                for cart_item in cart_items:
                    try:
                        new_reservation = StockManager.reserve_stock(
                            product_id=cart_item.product.id,
                            quantity=cart_item.quantity,
                            session_id=str(cart.uuid),
                            user_id=cart.user.id if cart.user else None,
                        )
                        active_reservations.append(new_reservation)
                        logger.info(
                            "Created new reservation %s for product %s",
                            new_reservation.id,
                            cart_item.product.id,
                        )
                    except InsufficientStockError as e:
                        logger.error(
                            "Insufficient stock for product %s: %s",
                            cart_item.product.id,
                            e,
                        )
                        raise

                reservations = active_reservations

            if not reservations and cart_items:
                logger.warning(
                    "No stock reservations found for cart %s. "
                    "This may indicate the reservation expired or was never created.",
                    cart.uuid,
                )
                # We'll proceed without reservations and use direct stock decrement

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
            )
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
            # Note: Payment fee is calculated on items total + shipping
            order_subtotal = Money(
                cart_total.amount + shipping_cost.amount,
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
            sanitised_meta = cls._sanitise_meta_context(meta_context)
            if sanitised_meta:
                order.metadata["meta"] = sanitised_meta

            # Track reservation IDs for this order
            reservation_ids = []

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

                # Get product price and convert to target currency if needed
                product_price = product.final_price
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

            # Step 6: Convert stock reservations to decrements via StockManager
            if reservations:
                # We have reservations - convert them to stock decrements
                for reservation in reservations:
                    try:
                        StockManager.convert_reservation_to_sale(
                            reservation_id=reservation.id, order_id=order.id
                        )
                        reservation_ids.append(reservation.id)
                        logger.info(
                            "Converted reservation %s to sale for order %s",
                            reservation.id,
                            order.id,
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to convert reservation %s: %s",
                            reservation.id,
                            e,
                            exc_info=True,
                        )
                        # Rollback will happen due to @transaction.atomic
                        raise
            else:
                # No reservations - use direct stock decrement
                logger.info(
                    "No reservations found for cart %s, using direct stock decrement",
                    cart.uuid,
                )
                for order_item in order.items.select_related("product").all():
                    try:
                        StockManager.decrement_stock(
                            product_id=order_item.product.id,
                            quantity=order_item.quantity,
                            order_id=order.id,
                            reason=f"Order {order.id} created from cart {cart.uuid}",
                        )
                        logger.info(
                            "Decremented stock for product %s by %s units",
                            order_item.product.id,
                            order_item.quantity,
                        )
                    except InsufficientStockError as e:
                        logger.error(
                            "Insufficient stock for product %s: %s",
                            order_item.product.id,
                            e,
                        )
                        # Rollback will happen due to @transaction.atomic
                        raise

            # Store reservation IDs in order metadata
            order.metadata["stock_reservation_ids"] = reservation_ids  # type: ignore[invalid-assignment]  # ty: ignore[invalid-assignment]

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
                    # Don't fail the order creation, just log the error
                    # The points won't be redeemed if this fails

            # Calculate and set paid amount (subtract loyalty discount)
            order_total = order.calculate_order_total_amount()
            order.paid_amount = Money(
                max(0, order_total.amount - loyalty_discount.amount),
                order_total.currency,
            )
            order.save(
                update_fields=[
                    "paid_amount",
                    "paid_amount_currency",
                    "metadata",
                ]
            )

            # Step 7: Clear cart
            cart.items.all().delete()
            logger.info("Cleared cart %s after order creation", cart.uuid)

            # Step 8: Dispatch shipment creation for true offline payments
            # (COD, Bank Transfer). Online providers that route through
            # this method (Viva Wallet) defer dispatch to the payment
            # webhook so the courier voucher only mints after the
            # shopper actually pays.
            if not pay_way.is_online_payment:
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
                except (ValueError, TypeError):
                    errors["country_id"] = [
                        _("Country ID must be a valid integer or country code")
                    ]

        # If there are errors, raise ValidationError
        if errors:
            raise ValidationError(errors)

    @classmethod
    @transaction.atomic
    def update_order_status(cls, order: Order, new_status: str) -> Order:
        try:
            if not new_status:
                raise ValueError("New status cannot be empty")

            if order.status == new_status:
                logger.info(
                    "Order %s status is already %s", order.id, new_status
                )
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
                        str(s)
                        for s in allowed_transitions.get(order.status, [])
                    ],
                )

            old_status = order.status

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

        except InvalidStatusTransitionError:
            raise

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
          Leave ``silent_for_customer=False`` (default) — DELIVERED
          fired hours/days earlier, so a fresh "thanks for your loyalty
          points" message is welcome, not redundant.

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

            # Restore stock for order items using StockManager
            for item in order.items.select_related("product").all():
                product = item.product
                if hasattr(product, "stock"):
                    try:
                        StockManager.increment_stock(
                            product_id=product.id,
                            quantity=item.quantity,
                            order_id=order.id,
                            reason=f"Order {order.id} canceled: {reason}",
                        )
                        logger.info(
                            "Restored stock for product %s: +%s (order %s canceled)",
                            product.id,
                            item.quantity,
                            order.id,
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to restore stock for product %s: %s",
                            product.id,
                            e,
                            exc_info=True,
                        )
                        # Continue with other items even if one fails

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
                        refund_info = {
                            "refunded": False,
                            "error": refund_response.get("error"),
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
                        "error": str(refund_error),
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
            logger.info(
                "Updating order %s payment status from %s to %s",
                order.id,
                order.payment_status,
                payment_status_enum,
            )
            order.payment_status = payment_status_enum
            order.save(update_fields=["payment_status"])

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

        final_statuses = {
            OrderStatus.DELIVERED,
            OrderStatus.COMPLETED,
            OrderStatus.RETURNED,
            OrderStatus.REFUNDED,
            OrderStatus.SHIPPED,
        }

        if order.status in final_statuses:
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
            except (ValueError, InvalidStatusTransitionError):
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
        # auto-routes to whichever active provider advertises
        # ``supports_home_delivery=True``.  Lower ``priority`` wins
        # the tie.  Adding a new courier (ELTA, Speedex …) is then a
        # one-row Django admin change — no order-flow code touched.
        if not code and kind == "home_delivery":
            picked = (
                ShippingProvider.objects.filter(
                    is_active=True, supports_home_delivery=True
                )
                .order_by("priority", "code")
                .first()
            )
            if picked is not None:
                code = picked.code

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
    def _extract_shipment_payload(
        cls, order_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Pop carrier-specific keys off ``order_data`` and return them.

        Mutates ``order_data`` in place — the popped keys would otherwise
        cause ``Order.objects.create(**order_data)`` to crash with an
        unexpected-kwarg ``TypeError``.  The returned dict is handed to
        the carrier registry so each adapter reads what it needs.

        The key list is the union of every registered carrier's
        ``payload_keys`` ClassVar, so adding a new carrier (ELTA,
        Speedex …) means writing one new adapter file with its
        ``payload_keys`` declared — no edit to ``order/services.py``.
        """
        from shipping.interfaces import all_payload_keys

        return {
            key: order_data.pop(key)
            for key in all_payload_keys()
            if key in order_data
        }

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
        from order.payment_events import publish_payment_status

        # Acquire a row lock and hydrate related objects in one query.
        # ``for_detail()`` adds COUNT/SUM annotations which Postgres
        # rejects under FOR UPDATE (aggregate in locked query). We
        # replicate the select_related/prefetch_related chains from
        # for_detail() manually, skipping with_counts() / with_total_amounts().
        from django.db.models import Prefetch

        from order.models.history import OrderHistory

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
            cls._suppress_customer_status_notifications(
                order, OrderStatus.PROCESSING.value
            )
            cls.update_order_status(order, OrderStatus.PROCESSING)

        # Enqueue provider-specific shipment creation after payment.
        # ShippingService.dispatch_create_shipment_task wraps the
        # delay() in transaction.on_commit so the worker only sees the
        # committed row.
        cls._dispatch_shipment_creation_task(order)

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

        # When the request carries a (provider, kind) pair, dispatch
        # through the registry so each provider owns its own pricing
        # rules. The provider's adapter has full control over flat
        # rate, dynamic quotes, free-shipping thresholds, and per-
        # country/region overrides — keeping per-carrier logic out of
        # this generic dispatcher.
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

        # Generic fallback for orders without a courier adapter — the
        # platform's flat-rate home-delivery price, country/region
        # overrides applied below.
        base_shipping_cost = Setting.get(
            "CHECKOUT_SHIPPING_PRICE", default=3.00
        )
        free_shipping_threshold = Setting.get(
            "FREE_SHIPPING_THRESHOLD", default=50.00
        )

        if order_value.amount >= float(free_shipping_threshold):
            return Money(0, order_value.currency)

        shipping_cost = float(base_shipping_cost)

        if country_id:
            from country.models import Country

            try:
                # Country uses alpha_2 as primary key, not id
                country = Country.objects.get(alpha_2=country_id)

                if (
                    hasattr(country, "shipping_multiplier")
                    and country.shipping_multiplier
                ):
                    shipping_cost *= country.shipping_multiplier

            except (ImportError, Country.DoesNotExist) as e:
                logger.warning(
                    "Country with ID %s not found or country module not available: %s",
                    country_id,
                    e,
                )

        if region_id:
            from region.models import Region

            try:
                # Region uses alpha as primary key, not id
                region = Region.objects.get(alpha=region_id)

                if (
                    hasattr(region, "shipping_adjustment")
                    and region.shipping_adjustment
                ):
                    shipping_cost += region.shipping_adjustment

            except (ImportError, Region.DoesNotExist) as e:
                logger.warning(
                    "Region with ID %s not found or region module not available: %s",
                    region_id,
                    e,
                )

        return Money(shipping_cost, order_value.currency)

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

        # Return payment method cost in order currency
        return Money(pay_way.cost.amount, order_value.currency)
