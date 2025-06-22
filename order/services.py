import logging
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from djmoney.money import Money

from order.enum.status import OrderStatus
from order.models.item import OrderItem
from order.models.order import Order
from order.signals import order_canceled, order_status_changed

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
    def get_order_by_id(cls, order_id: int) -> Order | None:
        order = (
            Order.objects.select_related("user", "pay_way", "country", "region")
            .prefetch_related("items")
            .get(id=order_id)
        )

        return order

    @classmethod
    def get_order_by_uuid(cls, uuid: str) -> Order | None:
        order = (
            Order.objects.select_related("user", "pay_way", "country", "region")
            .prefetch_related("items")
            .get(uuid=uuid)
        )

        return order

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
                            "Product {product_name} does not have enough stock. Available: {available}, Requested: {requested}"
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
                f"Order {order.id} created successfully with {len(items_data)} items"
            )

            return order

        except (
            ProductNotFoundError,
            InsufficientStockError,
            InvalidOrderDataError,
        ) as e:
            logger.warning(
                f"Re-raising {e.__class__.__name__} as ValueError for backward compatibility"
            )
            raise ValueError(str(e)) from e
        except Exception as e:
            logger.error(
                f"Unexpected error creating order: {e!s}", exc_info=True
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
                logger.info(f"Order {order.id} status is already {new_status}")
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
                    "Cannot transition from {old_status} to {new_status}. Allowed transitions: {allowed_transitions}"
                ).format(
                    old_status=order.status,
                    new_status=new_status,
                    allowed_transitions=allowed_transitions.get(
                        order.status, []
                    ),
                )
                logger.warning(
                    f"Invalid status transition for order {order.id}: {error_message}"
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
                f"Order {order.id} status updated from {old_status} to {new_status}"
            )

            return order

        except InvalidStatusTransitionError as e:
            logger.warning(
                f"Re-raising {e.__class__.__name__} as ValueError for backward compatibility"
            )
            raise ValueError(str(e)) from e

    @classmethod
    def get_user_orders(cls, user_id: int) -> QuerySet:
        user_orders = (
            Order.objects.filter(user_id=user_id)
            .select_related("pay_way", "country", "region")
            .prefetch_related("items", "items__product")
            .order_by("-created_at")
        )

        return user_orders

    @classmethod
    @transaction.atomic
    def cancel_order(cls, order: Order) -> Order:
        if not order.can_be_canceled:
            error_message = _(
                "Order in status {status} cannot be canceled. Only orders in PENDING or PROCESSING status can be canceled."
            ).format(status=order.status)
            logger.warning(f"Cannot cancel order {order.id}: {error_message}")
            raise ValueError(error_message)

        try:
            for item in order.items.select_related("product").all():
                product = item.product
                if hasattr(product, "stock"):
                    logger.info(
                        f"Restoring stock for product {product.id}: +{item.quantity}"
                    )
                    product.stock += item.quantity
                    product.save(update_fields=["stock"])

            old_status = order.status
            order.status = OrderStatus.CANCELED
            order.status_updated_at = timezone.now()
            order.save(update_fields=["status", "status_updated_at"])

            order_canceled.send(
                sender=cls, order=order, previous_status=old_status
            )

            logger.info(
                f"Order {order.id} canceled successfully (previous status: {old_status})"
            )

            return order

        except Exception as e:
            logger.error(
                f"Error canceling order {order.id}: {e!s}", exc_info=True
            )
            raise ValueError(
                _("Failed to cancel order: {error}").format(error=str(e))
            ) from e

    @classmethod
    def calculate_shipping_cost(
        cls,
        order_value: Money,
        country_id: int | None = None,
        region_id: int | None = None,
    ) -> Money:
        from extra_settings.models import Setting  # noqa: PLC0415

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
            from country.models import Country  # noqa: PLC0415

            try:
                country = Country.objects.get(id=country_id)

                if (
                    hasattr(country, "shipping_multiplier")
                    and country.shipping_multiplier
                ):
                    shipping_cost *= country.shipping_multiplier

            except (ImportError, Country.DoesNotExist) as e:
                logger.warning(
                    f"Country with ID {country_id} not found or country module not available: {e!s}"
                )

        if region_id:
            from region.models import Region  # noqa: PLC0415

            try:
                region = Region.objects.get(id=region_id)

                if (
                    hasattr(region, "shipping_adjustment")
                    and region.shipping_adjustment
                ):
                    shipping_cost += region.shipping_adjustment

            except (ImportError, Region.DoesNotExist) as e:
                logger.warning(
                    f"Region with ID {region_id} not found or region module not available: {e!s}"
                )

        return Money(shipping_cost, order_value.currency)
