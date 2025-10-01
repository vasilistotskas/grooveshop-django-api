import logging
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from djmoney.money import Money

from order.enum.status import OrderStatus, PaymentStatus
from order.models.item import OrderItem
from order.models.order import Order
from order.signals import order_canceled, order_refunded, order_status_changed

logger = logging.getLogger(__name__)


class OrderServiceError(Exception):
    pass


class ProductNotFoundError(OrderServiceError):
    pass


class InsufficientStockError(OrderServiceError):
    pass


class InvalidOrderDataError(OrderServiceError):
    pass


class OrderNotFoundError(OrderServiceError):
    pass


class InvalidStatusTransitionError(OrderServiceError):
    pass


class OrderCancellationError(OrderServiceError):
    pass


class OrderService:
    @classmethod
    def get_order_by_id(cls, order_id: int) -> Order:
        return (
            Order.objects.select_related("user", "pay_way", "country", "region")
            .prefetch_related("items")
            .get(id=order_id)
        )

    @classmethod
    def get_order_by_uuid(cls, uuid: str) -> Order:
        return (
            Order.objects.select_related("user", "pay_way", "country", "region")
            .prefetch_related("items")
            .get(uuid=uuid)
        )

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
                    raise ProductNotFoundError(
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
                        _(
                            "Product {product_name} does not have enough stock. "
                            "Available: {available}, Requested: {requested}"
                        ).format(
                            product_name=product.name or product.id,
                            available=product.stock,
                            requested=quantity,
                        )
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
        ) as e:
            logger.warning(
                "Re-raising %s as ValueError for backward compatibility",
                e.__class__.__name__,
            )
            raise ValueError(str(e)) from e
        except Exception as e:
            logger.error(
                "Unexpected error creating order: %s", e, exc_info=True
            )
            raise InvalidOrderDataError(
                _("Failed to create order: {error}").format(error=str(e))
            ) from e

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
                error_message = _(
                    "Cannot transition from {old_status} to {new_status}. "
                    "Allowed transitions: {allowed_transitions}"
                ).format(
                    old_status=order.status,
                    new_status=new_status,
                    allowed_transitions=allowed_transitions.get(
                        order.status, []
                    ),
                )
                logger.warning(
                    "Invalid status transition for order %s: %s",
                    order.id,
                    error_message,
                )
                raise InvalidStatusTransitionError(error_message)

            old_status = order.status

            order.status = new_status
            order.status_updated_at = timezone.now()
            order.save(update_fields=["status", "status_updated_at"])

            order_status_changed.send(
                sender=cls,
                order=order,
                old_status=old_status,
                new_status=new_status,
            )

            logger.info(
                "Order %s status updated from %s to %s",
                order.id,
                old_status,
                new_status,
            )

            return order

        except InvalidStatusTransitionError as e:
            logger.warning(
                "Re-raising %s as ValueError for backward compatibility",
                e.__class__.__name__,
            )
            raise ValueError(str(e)) from e

    @classmethod
    def get_user_orders(cls, user_id: int) -> QuerySet:
        return (
            Order.objects.filter(user_id=user_id)
            .select_related("pay_way", "country", "region")
            .prefetch_related("items", "items__product")
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
            raise ValueError(error_message)

        try:
            for item in order.items.select_related("product").all():
                product = item.product
                if hasattr(product, "stock"):
                    logger.info(
                        "Restoring stock for product %s: +%s",
                        product.id,
                        item.quantity,
                    )
                    product.stock += item.quantity
                    product.save(update_fields=["stock"])

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
            raise ValueError(
                _("Failed to cancel order: {error}").format(error=str(e))
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
            raise ValueError(_("This order has no payment ID to refund."))

        if not order.is_paid:
            raise ValueError(_("This order has not been paid yet."))

        if order.payment_status == PaymentStatus.REFUNDED:
            raise ValueError(_("This order has already been refunded."))

        if not order.pay_way:
            raise ValueError(_("This order has no payment method configured."))

        if amount and amount.amount > order.paid_amount.amount:
            raise ValueError(
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
            raise ValueError(_("This order has no payment ID."))

        if not order.pay_way:
            raise ValueError(_("This order has no payment method configured."))

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
            order = Order.objects.get(payment_id=payment_intent_id)
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
            order = Order.objects.get(payment_id=payment_intent_id)
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
                country = Country.objects.get(id=country_id)

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
                region = Region.objects.get(id=region_id)

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
