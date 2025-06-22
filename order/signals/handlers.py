import logging
from typing import Any

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus
from order.models.history import OrderHistory, OrderItemHistory
from order.models.item import OrderItem
from order.models.order import Order
from order.notifications import (
    send_order_canceled_notification,
    send_order_delivered_notification,
    send_order_shipped_notification,
)
from order.services import OrderService
from order.signals import (
    order_canceled,
    order_completed,
    order_created,
    order_delivered,
    order_paid,
    order_refunded,
    order_returned,
    order_shipped,
    order_status_changed,
)
from order.tasks import (
    generate_order_invoice,
    send_order_confirmation_email,
    send_order_status_update_email,
    send_shipping_notification_email,
)

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Order)
def handle_order_post_save(
    sender: type[Order], instance: Order, created: bool, **kwargs: Any
) -> None:
    if created:
        order_created.send(sender=sender, order=instance)
        logger.debug(f"Sent order_created signal for new order {instance.id}")
        return

    if (
        hasattr(instance, "_previous_status")
        and instance._previous_status != instance.status
    ):
        order_status_changed.send(
            sender=sender,
            order=instance,
            old_status=instance._previous_status,
            new_status=instance.status,
        )
        logger.debug(
            f"Sent order_status_changed signal for order {instance.id} "
            f"({instance._previous_status} -> {instance.status})"
        )


@receiver(order_created)
def handle_order_created(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    send_order_confirmation_email.delay(order.id)

    OrderHistory.log_note(
        order=order,
        note="Order created",
    )


@receiver(order_status_changed)
def handle_order_status_changed(
    sender: type[Order],
    order: Order,
    old_status: str,
    new_status: str | None = None,
    **kwargs: Any,
) -> None:
    if new_status is None:
        new_status = order.status

    OrderHistory.log_status_change(
        order=order, previous_status=old_status, new_status=new_status
    )

    send_order_status_update_email.delay(order.id, new_status)

    if new_status == OrderStatus.SHIPPED.value:
        order_shipped.send(sender=sender, order=order)

    elif new_status == OrderStatus.DELIVERED.value:
        order_delivered.send(sender=sender, order=order)

    elif new_status == OrderStatus.CANCELED.value:
        order_canceled.send(sender=sender, order=order)

    elif new_status == OrderStatus.COMPLETED.value:
        order_completed.send(sender=sender, order=order)

    elif (
        new_status == OrderStatus.PROCESSING.value
        and order.is_paid
        and not hasattr(order, "_paid_signal_sent")
    ):
        order_paid.send(sender=sender, order=order)
        order._paid_signal_sent = True
        logger.debug(f"Sent order_paid signal for order {order.id}")

    logger.info(
        f"Order {order.id} status changed from {old_status} to {new_status}"
    )


@receiver(pre_save, sender=Order)
def handle_order_pre_save(
    sender: type[Order], instance: Order, **kwargs: Any
) -> None:
    try:
        if instance.pk:
            instance._previous_status = Order.objects.get(pk=instance.pk).status
        else:
            instance._previous_status = None
    except Order.DoesNotExist:
        instance._previous_status = None


@receiver(pre_save, sender=OrderItem)
def handle_order_item_pre_save(
    sender: Any, instance: Any, **kwargs: Any
) -> None:
    if instance.pk:
        try:
            original = sender.objects.get(pk=instance.pk)

            instance._original_quantity = original.quantity
            instance._original_price = original.price
            instance._original_is_refunded = original.is_refunded
            instance._original_refunded_quantity = original.refunded_quantity

        except sender.DoesNotExist:
            instance._original_quantity = 0
            instance._original_price = None
        except Exception as e:
            logger.error(
                f"Error in handle_order_item_pre_save: {e}", exc_info=True
            )
    else:
        instance._original_quantity = 0
        instance._original_price = None


@receiver(post_save, sender=OrderItem)
def handle_order_item_post_save(
    sender: type[OrderItem], instance: OrderItem, created: bool, **kwargs: Any
) -> None:
    if created:
        product = instance.product
        product.stock = max(0, product.stock - instance.quantity)
        product.save(update_fields=["stock"])

        try:
            order = instance.order

            OrderHistory.log_note(
                order=order,
                note=f"Item {instance.product.name if instance.product else 'Unknown'} added to order",
            )
            logger.debug(
                f"Order item {instance.id} created for order {order.id}"
            )
        except Exception as e:
            logger.error(
                f"Error handling order item creation: {e}", exc_info=True
            )
    elif (
        hasattr(instance, "_original_quantity")
        and instance._original_quantity != instance.quantity
    ):
        product = instance.product
        stock_difference = instance._original_quantity - instance.quantity
        new_stock = product.stock + stock_difference
        product.stock = max(0, new_stock)
        product.save(update_fields=["stock"])

        OrderItemHistory.log_quantity_change(
            order_item=instance,
            previous_quantity=instance._original_quantity,
            new_quantity=instance.quantity,
        )

        try:
            OrderHistory.log_note(
                order=instance.order,
                note=f"Item {instance.product.name if instance.product else 'Unknown'} quantity updated from {instance._original_quantity} to {instance.quantity}",
            )
        except Exception as e:
            logger.error(
                f"Error logging order history for quantity change: {e}",
                exc_info=True,
            )

    if (
        hasattr(instance, "_original_price")
        and instance._original_price
        and instance._original_price != instance.price
    ):
        OrderItemHistory.log_price_update(
            order_item=instance,
            previous_price=instance._original_price,
            new_price=instance.price,
        )

    if (
        hasattr(instance, "_original_is_refunded")
        and instance.is_refunded != instance._original_is_refunded
    ):
        try:
            OrderHistory.log_note(
                order=instance.order,
                note=(
                    f"Item {instance.product.name if instance.product else 'Unknown'} "
                    f"marked as {'refunded' if instance.is_refunded else 'not refunded'}"
                ),
            )
        except Exception as e:
            logger.error(
                f"Error logging order history for refund change: {e}",
                exc_info=True,
            )


@receiver(order_shipped)
def handle_order_shipped(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    if order.status != OrderStatus.SHIPPED.value:
        logger.info(f"Updating order {order.id} status to shipped")
        OrderService.update_order_status(order, OrderStatus.SHIPPED)

    OrderHistory.log_shipping_update(
        order=order,
        previous_value={"status": OrderStatus.PENDING.value},
        new_value={"status": OrderStatus.SHIPPED.value},
    )

    send_order_shipped_notification(order)

    task = send_shipping_notification_email.delay(order.id)
    logger.info(
        f"Order {order.id} shipment notification email queued (task_id: {task.id})"
    )


@receiver(order_delivered)
def handle_order_delivered(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    send_order_delivered_notification(order)

    OrderHistory.log_shipping_update(
        order=order,
        previous_value={"status": OrderStatus.SHIPPED.value},
        new_value={"status": OrderStatus.DELIVERED.value},
    )


@receiver(order_canceled)
def handle_order_canceled(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    previous_status = kwargs.get("previous_status")

    try:
        cancellation_reason = kwargs.get("reason")
        if cancellation_reason:
            OrderHistory.log_note(
                order=order,
                note=f"Order canceled. Reason: {cancellation_reason}",
            )

        send_order_canceled_notification(order)

        logger.info(
            f"Order {order.id} canceled (previous status: {previous_status})"
        )

    except Exception as e:
        logger.error(
            f"Error handling order_canceled signal: {e}", exc_info=True
        )


@receiver(order_completed)
def handle_order_completed(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    try:
        if order.document_type == OrderDocumentTypeEnum.INVOICE.value:
            task = generate_order_invoice.delay(order.id)
            logger.info(
                f"Invoice generation queued for order {order.id} (task_id: {task.id})"
            )

        OrderHistory.log_note(
            order=order,
            note="Order completed",
        )

        logger.info(f"Order {order.id} marked as completed")

    except Exception as e:
        logger.error(
            f"Error handling order_completed signal: {e}", exc_info=True
        )


@receiver(order_refunded)
def handle_order_refunded(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    try:
        refund_amount = kwargs.get("amount")
        refund_reason = kwargs.get("reason", "")

        OrderHistory.log_refund(
            order=order,
            refund_data={
                "amount": str(refund_amount)
                if refund_amount
                else "Full order amount",
                "reason": refund_reason or "Not specified",
            },
        )

        logger.info(f"Order {order.id} refunded")

    except Exception as e:
        logger.error(
            f"Error handling order_refunded signal: {e}", exc_info=True
        )


@receiver(order_returned)
def handle_order_returned(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    try:
        return_reason = kwargs.get("reason", "")
        return_items = kwargs.get("items", [])

        items_text = ""
        if return_items:
            items_text = "Items: " + ", ".join(
                f"{item.get('product_name', 'Unknown')} (qty: {item.get('quantity', 0)})"
                for item in return_items
            )

        OrderHistory.log_note(
            order=order,
            note=(
                f"Order returned. "
                f"{items_text}. "
                f"Reason: {return_reason or 'Not specified'}"
            ),
        )

        logger.info(f"Order {order.id} returned")

    except Exception as e:
        logger.error(
            f"Error handling order_returned signal: {e}", exc_info=True
        )
