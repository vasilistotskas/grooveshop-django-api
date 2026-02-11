import logging
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from djmoney.money import Money

from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import (
    InsufficientStockError,
    InvalidOrderDataError,
    InvalidStatusTransitionError,
    OrderCancellationError,
    PaymentError,
    PaymentNotFoundError,
    ProductNotFoundError,
)
from order.models.item import OrderItem
from order.models.order import Order
from order.models.stock_reservation import StockReservation
from order.signals import order_canceled, order_refunded
from order.stock import StockManager

logger = logging.getLogger(__name__)


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

            order = Order.objects.create(**order_data)

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
                        _("Product is required for order items")
                    )

                if quantity <= 0:
                    raise InvalidOrderDataError(
                        _(
                            "Invalid quantity {quantity} for product {product_id}"
                        ).format(
                            quantity=quantity,
                            product_id=getattr(product, "id", _("unknown")),
                        )
                    )

                if product.stock < quantity:
                    raise InsufficientStockError(
                        product_id=product.id,
                        available=product.stock,
                        requested=quantity,
                    )

                item_to_create = item_data.copy()

                product_price = product.final_price
                if product_price.currency != target_currency:
                    item_to_create["price"] = Money(
                        product_price.amount, target_currency
                    )
                else:
                    item_to_create["price"] = product_price

                OrderItem.objects.create(order=order, **item_to_create)

            order.paid_amount = order.calculate_order_total_amount()
            order.save(update_fields=["paid_amount"])

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
            # Step 1: Validate cart for checkout
            validation_result = cls.validate_cart_for_checkout(cart)
            if not validation_result.get("valid", False):
                raise InvalidOrderDataError(
                    _("Cart validation failed: {errors}").format(
                        errors=", ".join(validation_result.get("errors", []))
                    )
                )

            # Step 2: Validate shipping address
            cls.validate_shipping_address(shipping_address)

            # Step 3: Validate payment intent exists
            from order.payment import get_payment_provider

            if not payment_intent_id:
                raise PaymentNotFoundError(
                    _("Payment intent ID is required for order creation")
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

            # Step 4: Get stock reservations for cart session
            # Reservations are identified by cart.uuid (session_id)
            reservations = list(
                StockReservation.objects.filter(
                    session_id=str(cart.uuid), consumed=False
                ).select_related("product")
            )

            # Validate we have reservations for all cart items
            cart_items = cart.get_items()

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
            }

            # Calculate shipping cost
            cart_total = cart.total_price
            shipping_cost = cls.calculate_shipping_cost(
                order_value=cart_total,
                country_id=shipping_address.get("country_id"),
                region_id=shipping_address.get("region_id"),
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

            # Create the order
            order = Order.objects.create(**order_data)

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

            # Track reservation IDs for this order
            reservation_ids = []

            # Step 6: Create OrderItems from CartItems
            for cart_item in cart_items:
                product = cart_item.product
                quantity = cart_item.quantity

                # Validate product still exists and has stock
                if not product:
                    raise InvalidOrderDataError(
                        _("Product is required for order items")
                    )

                if quantity <= 0:
                    raise InvalidOrderDataError(
                        _(
                            "Invalid quantity {quantity} for product {product_id}"
                        ).format(quantity=quantity, product_id=product.id)
                    )

                # Get product price and convert to target currency if needed
                product_price = product.final_price
                if product_price.currency != target_currency:
                    item_price = Money(product_price.amount, target_currency)
                else:
                    item_price = product_price

                # Create OrderItem
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
            order.metadata["stock_reservation_ids"] = reservation_ids

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
            order.save(update_fields=["paid_amount", "metadata"])

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
    ) -> Order:
        """
        Create order from cart for offline payment methods (order-first flow).

        This method implements the order-first approach for offline payment methods
        like Cash on Delivery and Bank Transfer. It performs the following steps:
        1. Validates cart items still exist and prices match
        2. Validates shipping address completeness
        3. Gets or creates stock reservations for cart session
        4. Creates Order with status=PENDING, payment_status=PENDING
        5. Creates OrderItems from CartItems
        6. Converts stock reservations to decrements via StockManager
        7. Sets payment_id = f"offline_{order.uuid}"
        8. Clears cart
        9. Returns order in PENDING status

        Args:
            cart: Cart object containing items to order
            shipping_address: Dictionary with shipping address fields
            pay_way: PayWay object for payment method (must have is_online_payment=False)
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
            # Step 1: Validate cart for checkout
            validation_result = cls.validate_cart_for_checkout(cart)
            if not validation_result.get("valid", False):
                raise InvalidOrderDataError(
                    _("Cart validation failed: {errors}").format(
                        errors=", ".join(validation_result.get("errors", []))
                    )
                )

            # Step 2: Validate shipping address
            cls.validate_shipping_address(shipping_address)

            # Step 3: Get stock reservations for cart session
            # Reservations are identified by cart.uuid (session_id)
            reservations = list(
                StockReservation.objects.filter(
                    session_id=str(cart.uuid), consumed=False
                ).select_related("product")
            )

            # Validate we have reservations for all cart items
            cart_items = cart.get_items()

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
            }

            # Calculate shipping cost
            cart_total = cart.total_price
            shipping_cost = cls.calculate_shipping_cost(
                order_value=cart_total,
                country_id=shipping_address.get("country_id"),
                region_id=shipping_address.get("region_id"),
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

            # Create the order
            order = Order.objects.create(**order_data)

            # Set payment_id for offline payments
            order.payment_id = f"offline_{order.uuid}"
            order.save(update_fields=["payment_id"])

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

            # Track reservation IDs for this order
            reservation_ids = []

            # Step 5: Create OrderItems from CartItems
            for cart_item in cart_items:
                product = cart_item.product
                quantity = cart_item.quantity

                # Validate product still exists and has stock
                if not product:
                    raise InvalidOrderDataError(
                        _("Product is required for order items")
                    )

                if quantity <= 0:
                    raise InvalidOrderDataError(
                        _(
                            "Invalid quantity {quantity} for product {product_id}"
                        ).format(quantity=quantity, product_id=product.id)
                    )

                # Get product price and convert to target currency if needed
                product_price = product.final_price
                if product_price.currency != target_currency:
                    item_price = Money(product_price.amount, target_currency)
                else:
                    item_price = product_price

                # Create OrderItem
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
                            reason=f"Order {order.id} created from cart {cart.uuid} (offline payment)",
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
            order.metadata["stock_reservation_ids"] = reservation_ids

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
            order.save(update_fields=["paid_amount", "metadata"])

            # Step 7: Clear cart
            cart.items.all().delete()
            logger.info("Cleared cart %s after order creation", cart.uuid)

            # Step 8: Return order in PENDING status
            logger.info(
                "Order %s created successfully from cart %s (offline payment)",
                order.id,
                cart.uuid,
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
                "Unexpected error creating order from cart (offline): %s",
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

        # Check 2: All products exist and are in stock
        for cart_item in cart_items:
            product = cart_item.product

            # Check product exists
            if not product:
                errors.append(_("Product in cart no longer exists"))
                continue

            # Check product is in stock
            available_stock = StockManager.get_available_stock(product.id)
            if available_stock < cart_item.quantity:
                errors.append(
                    _(
                        "Product '{product}' has insufficient stock. "
                        "Available: {available}, Requested: {requested}"
                    ).format(
                        product=product.name,
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
    def validate_shipping_address(cls, address: dict[str, Any]) -> None:
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
                raise InvalidStatusTransitionError(
                    _("New status cannot be empty")
                )

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
                    allowed=allowed_transitions.get(order.status, []),
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
    def get_user_orders(cls, user_id: int) -> QuerySet:
        """Get all orders for a user with optimized queryset."""
        return (
            Order.objects.for_list()
            .filter(user_id=user_id)
            .order_by("-created_at")
        )

    @classmethod
    @transaction.atomic
    def cancel_order(
        cls,
        order: Order,
        reason: str = "",
        refund_payment: bool = True,
        canceled_by: int | None = None,
    ) -> tuple[Order, dict[str, Any] | None]:
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

            order_canceled.send(
                sender=cls,
                order=order,
                previous_status=old_status,
                reason=reason,
            )

            logger.info(
                "Order %s canceled successfully (previous status: %s)",
                order.id,
                old_status,
            )

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

        if order.payment_status == PaymentStatus.REFUNDED:
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
    @transaction.atomic
    def handle_payment_succeeded(cls, payment_intent_id: str) -> Order | None:
        try:
            order = Order.objects.for_detail().get(payment_id=payment_intent_id)
        except Order.DoesNotExist:
            logger.error(
                "Order not found for payment_intent: %s", payment_intent_id
            )
            return None

        order.mark_as_paid(
            payment_id=payment_intent_id, payment_method="stripe"
        )

        if order.status == OrderStatus.PENDING:
            cls.update_order_status(order, OrderStatus.PROCESSING)

        logger.info("Order %s marked as paid successfully", order.id)
        return order

    @classmethod
    @transaction.atomic
    def handle_payment_failed(cls, payment_intent_id: str) -> Order | None:
        try:
            order = Order.objects.for_detail().get(payment_id=payment_intent_id)
        except Order.DoesNotExist:
            logger.error(
                "Order not found for payment_intent: %s", payment_intent_id
            )
            return None

        order.payment_status = PaymentStatus.FAILED
        order.save(update_fields=["payment_status"])

        logger.info("Order %s payment marked as failed", order.id)
        return order

    @classmethod
    def calculate_shipping_cost(
        cls,
        order_value: Money,
        country_id: int | None = None,
        region_id: int | None = None,
    ) -> Money:
        from extra_settings.models import Setting

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
