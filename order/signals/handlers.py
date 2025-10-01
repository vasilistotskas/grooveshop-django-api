import logging
from typing import Any

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from djstripe.event_handlers import djstripe_receiver
from djstripe.models import Event

from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus, PaymentStatus
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
    """Handle order post-save signal."""
    if created:
        order_created.send(sender=sender, order=instance)
        logger.debug("Sent order_created signal for new order %s", instance.id)
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
            "Sent order_status_changed signal for order %s (%s -> %s)",
            instance.id,
            instance._previous_status,
            instance.status,
        )


@receiver(order_created)
def handle_order_created(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Handle order creation."""
    send_order_confirmation_email.delay(order.id)
    OrderHistory.log_note(order=order, note="Order created")


@receiver(order_status_changed)
def handle_order_status_changed(
    sender: type[Order],
    order: Order,
    old_status: str,
    new_status: str | None = None,
    **kwargs: Any,
) -> None:
    """Handle order status change."""
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
        logger.debug("Sent order_paid signal for order %s", order.id)

    logger.info(
        "Order %s status changed from %s to %s",
        order.id,
        old_status,
        new_status,
    )


@receiver(pre_save, sender=Order)
def handle_order_pre_save(
    sender: type[Order], instance: Order, **kwargs: Any
) -> None:
    """Store previous order status before save."""
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
    """Store previous order item values before save."""
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
                "Error in handle_order_item_pre_save: %s", e, exc_info=True
            )
    else:
        instance._original_quantity = 0
        instance._original_price = None


@receiver(post_save, sender=OrderItem)
def handle_order_item_post_save(
    sender: type[OrderItem], instance: OrderItem, created: bool, **kwargs: Any
) -> None:
    """Handle order item changes and update stock."""
    if created:
        product = instance.product
        product.stock = max(0, product.stock - instance.quantity)
        product.save(update_fields=["stock"])

        try:
            order = instance.order

            OrderHistory.log_note(
                order=order,
                note=f"Item {instance.product.safe_translation_getter('name', any_language=True) if instance.product else 'Unknown'} added to order",
            )
            logger.debug(
                "Order item %s created for order %s", instance.id, order.id
            )
        except Exception as e:
            logger.error(
                "Error handling order item creation: %s", e, exc_info=True
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
                note=f"Item {instance.product.safe_translation_getter('name', any_language=True) if instance.product else 'Unknown'} quantity updated from {instance._original_quantity} to {instance.quantity}",
            )
        except Exception as e:
            logger.error(
                "Error logging order history for quantity change: %s",
                e,
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
                    f"Item {instance.product.safe_translation_getter('name', any_language=True) if instance.product else 'Unknown'} "
                    f"marked as {'refunded' if instance.is_refunded else 'not refunded'}"
                ),
            )
        except Exception as e:
            logger.error(
                "Error logging order history for refund change: %s",
                e,
                exc_info=True,
            )


@receiver(order_shipped)
def handle_order_shipped(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Handle order shipped signal."""
    if order.status != OrderStatus.SHIPPED.value:
        logger.info("Updating order %s status to shipped", order.id)
        OrderService.update_order_status(order, OrderStatus.SHIPPED)

    OrderHistory.log_shipping_update(
        order=order,
        previous_value={"status": OrderStatus.PENDING.value},
        new_value={"status": OrderStatus.SHIPPED.value},
    )

    send_order_shipped_notification(order)

    task = send_shipping_notification_email.delay(order.id)
    logger.info(
        "Order %s shipment notification email queued (task_id: %s)",
        order.id,
        task.id,
    )


@receiver(order_delivered)
def handle_order_delivered(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Handle order delivered signal."""
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
    """Handle order canceled signal."""
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
            "Order %s canceled (previous status: %s)", order.id, previous_status
        )

    except Exception as e:
        logger.error(
            "Error handling order_canceled signal: %s", e, exc_info=True
        )


@receiver(order_completed)
def handle_order_completed(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Handle order completed signal."""
    try:
        if order.document_type == OrderDocumentTypeEnum.INVOICE.value:
            task = generate_order_invoice.delay(order.id)
            logger.info(
                "Invoice generation queued for order %s (task_id: %s)",
                order.id,
                task.id,
            )

        OrderHistory.log_note(order=order, note="Order completed")

        logger.info("Order %s marked as completed", order.id)

    except Exception as e:
        logger.error(
            "Error handling order_completed signal: %s", e, exc_info=True
        )


@receiver(order_refunded)
def handle_order_refunded(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Handle order refunded signal."""
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

        logger.info("Order %s refunded", order.id)

    except Exception as e:
        logger.error(
            "Error handling order_refunded signal: %s", e, exc_info=True
        )


@receiver(order_returned)
def handle_order_returned(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Handle order returned signal."""
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

        logger.info("Order %s returned", order.id)

    except Exception as e:
        logger.error(
            "Error handling order_returned signal: %s", e, exc_info=True
        )


@djstripe_receiver("payment_intent.succeeded")
def handle_stripe_payment_succeeded(sender, **kwargs):
    """Handle Stripe payment success webhook."""
    logger.debug("Processing payment_intent.succeeded webhook")

    try:
        event: Event = kwargs.get("event")
        payment_intent_id = event.data["object"]["id"]

        logger.info("Stripe payment succeeded: %s", payment_intent_id)

        order = OrderService.handle_payment_succeeded(payment_intent_id)

        if order:
            OrderHistory.log_payment_update(
                order=order,
                previous_value={"payment_status": "pending"},
                new_value={
                    "payment_status": "completed",
                    "payment_id": payment_intent_id,
                },
            )

    except Exception as e:
        logger.error(
            "Error handling payment_intent.succeeded: %s", e, exc_info=True
        )


@djstripe_receiver("payment_intent.payment_failed")
def handle_stripe_payment_failed(sender, **kwargs):
    """Handle Stripe payment failure webhook."""
    logger.debug("Processing payment_intent.payment_failed webhook")

    try:
        event: Event = kwargs.get("event")
        payment_intent_id = event.data["object"]["id"]

        logger.info("Stripe payment failed: %s", payment_intent_id)

        order = OrderService.handle_payment_failed(payment_intent_id)

        if order:
            OrderHistory.log_payment_update(
                order=order,
                previous_value={"payment_status": "pending"},
                new_value={
                    "payment_status": "failed",
                    "payment_id": payment_intent_id,
                },
            )

    except Exception as e:
        logger.error(
            "Error handling payment_intent.payment_failed: %s", e, exc_info=True
        )


@djstripe_receiver("payment_intent.requires_action")
def handle_stripe_payment_requires_action(sender, **kwargs):
    """Handle Stripe payment requiring action webhook."""
    logger.debug("Processing payment_intent.requires_action webhook")

    try:
        event: Event = kwargs.get("event")
        payment_intent_id = event.data["object"]["id"]

        logger.info("Stripe payment requires action: %s", payment_intent_id)

        try:
            order = Order.objects.get(payment_id=payment_intent_id)
        except Order.DoesNotExist:
            logger.error(
                "Order not found for payment_intent: %s", payment_intent_id
            )
            return

        order.payment_status = PaymentStatus.PENDING
        order.save(update_fields=["payment_status"])

        OrderHistory.log_note(
            order=order,
            note=f"Payment requires additional action (3D Secure, etc.) - Payment ID: {payment_intent_id}",
        )

    except Exception as e:
        logger.error(
            "Error handling payment_intent.requires_action: %s",
            e,
            exc_info=True,
        )


@djstripe_receiver("charge.dispute.created")
def handle_stripe_dispute_created(sender, **kwargs):
    """Handle Stripe dispute creation webhook."""
    logger.debug("Processing charge.dispute.created webhook")

    try:
        event: Event = kwargs.get("event")
        dispute_data = event.data["object"]
        charge_id = dispute_data["charge"]

        logger.warning("Stripe dispute created for charge: %s", charge_id)

    except Exception as e:
        logger.error(
            "Error handling charge.dispute.created: %s", e, exc_info=True
        )


@djstripe_receiver("checkout.session.completed")
def handle_stripe_checkout_completed(sender, **kwargs):
    """Handle Stripe checkout session completion webhook."""
    logger.debug("Processing checkout.session.completed webhook")

    try:
        event: Event = kwargs.get("event")
        session_data = event.data["object"]
        session_id = session_data["id"]
        payment_intent_id = session_data.get("payment_intent")
        payment_status = session_data.get("payment_status")

        logger.info("Checkout session completed: %s", session_id)

        order_id = session_data.get("metadata", {}).get("order_id")

        if not order_id:
            logger.warning("No order_id in session metadata: %s", session_id)
            return

        try:
            order = Order.objects.get(id=order_id)
        except Order.DoesNotExist:
            logger.error(
                "Order %s not found for session %s", order_id, session_id
            )
            return

        if payment_status == "paid" and payment_intent_id:
            order.mark_as_paid(
                payment_id=payment_intent_id, payment_method="stripe"
            )

            if order.status == OrderStatus.PENDING:
                OrderService.update_order_status(order, OrderStatus.PROCESSING)

            if not order.metadata:
                order.metadata = {}
            order.metadata["stripe_checkout_session_id"] = session_id
            order.metadata["stripe_payment_intent_id"] = payment_intent_id
            order.save(update_fields=["metadata"])

            OrderHistory.log_payment_update(
                order=order,
                previous_value={"payment_status": "pending"},
                new_value={
                    "payment_status": "completed",
                    "payment_id": payment_intent_id,
                    "checkout_session_id": session_id,
                },
            )

            logger.info(
                "Order %s marked as paid via checkout session %s",
                order_id,
                session_id,
            )

        elif payment_status == "unpaid":
            order.payment_status = PaymentStatus.PENDING
            order.save(update_fields=["payment_status"])

            OrderHistory.log_note(
                order=order,
                note=f"Checkout session completed but payment is unpaid: {session_id}",
            )

            logger.warning(
                "Checkout session completed but payment is unpaid: %s",
                session_id,
            )

    except Exception as e:
        logger.error(
            "Error handling checkout.session.completed: %s", e, exc_info=True
        )


@djstripe_receiver("checkout.session.expired")
def handle_stripe_checkout_expired(sender, **kwargs):
    """Handle Stripe checkout session expiration webhook."""
    logger.debug("Processing checkout.session.expired webhook")

    try:
        event: Event = kwargs.get("event")
        session_data = event.data["object"]
        session_id = session_data["id"]
        order_id = session_data.get("metadata", {}).get("order_id")

        logger.info("Checkout session expired: %s", session_id)

        if not order_id:
            return

        try:
            order = Order.objects.get(id=order_id)

            if not order.is_paid:
                if not order.metadata:
                    order.metadata = {}
                order.metadata["stripe_checkout_expired"] = True
                order.metadata["stripe_checkout_session_id"] = session_id
                order.save(update_fields=["metadata"])

                OrderHistory.log_note(
                    order=order,
                    note=f"Checkout session expired: {session_id}",
                )

                logger.info(
                    "Marked order %s checkout session as expired", order_id
                )

        except Order.DoesNotExist:
            logger.error(
                "Order %s not found for expired session %s",
                order_id,
                session_id,
            )

    except Exception as e:
        logger.error(
            "Error handling checkout.session.expired: %s", e, exc_info=True
        )
