import logging
from typing import Any

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from djstripe.event_handlers import djstripe_receiver
from djstripe.models import Event

from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import OrderStatus, PaymentStatus
from order.models.history import OrderHistory, OrderItemHistory
from order.models.item import OrderItem
from order.models.order import Order
from order.services import OrderService
from order.signals import (
    order_canceled,
    order_completed,
    order_created,
    order_delivered,
    order_paid,
    order_refunded,
    order_returned,
    order_shipment_dispatched,
    order_shipped,
    order_status_changed,
)
from order.notifications import (
    notify_order_created_live,
    notify_order_refunded_live,
    notify_order_shipment_dispatched_live,
    notify_order_status_changed_live,
    notify_payment_confirmed_live,
    notify_payment_failed_live,
)
from order.tasks import (
    generate_order_invoice,
    send_dispute_notification_email,
    send_order_confirmation_email,
    send_order_status_update_email,
    send_payment_failed_email,
)

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Order, dispatch_uid="order.handle_order_post_save")
def handle_order_post_save(
    sender: type[Order], instance: Order, created: bool, **kwargs: Any
) -> None:
    """Handle order post-save signal."""
    if created:

        def send_created_signal():
            order_created.send(sender=sender, order=instance)
            logger.debug(
                "Sent order_created signal for new order %s", instance.id
            )

        # Defer to on_commit so the Celery task sees the committed row.
        transaction.on_commit(send_created_signal)
        return

    if (
        hasattr(instance, "_original_status")
        and instance._original_status != instance.status
    ):
        # Defer to on_commit so the Celery task dispatched by the
        # signal handler sees the committed row (same pattern as
        # the `created` branch above).
        _old = instance._original_status
        _new = instance.status

        def send_status_changed_signal(
            _sender=sender,
            _instance=instance,
            _old=_old,
            _new=_new,
        ):
            order_status_changed.send(
                sender=_sender,
                order=_instance,
                old_status=_old,
                new_status=_new,
            )
            logger.debug(
                "Sent order_status_changed signal for order %s (%s -> %s)",
                _instance.id,
                _old,
                _new,
            )

        transaction.on_commit(send_status_changed_signal)

    # Detect the null → set transition on tracking info. We treat an
    # empty string the same as None because the field is declared with
    # ``blank=True`` and Django serializers happily round-trip "" as
    # "no value". Fire on commit to avoid a race where the Celery task
    # reads a not-yet-visible row.
    #
    # Additionally require the *value* to have actually changed between
    # original and current — protects against the clear-then-reset case
    # where an admin blanks the tracking, saves (post_save refreshes
    # the ``_original_*`` snapshot to ""), then re-enters the same
    # tracking number. Without the equality check the signal would
    # fire a second time and the shopper would get a duplicate
    # "Tracking available" notification.
    tracking_unchanged = (
        (
            instance.tracking_number == instance._original_tracking_number
            and instance.shipping_carrier == instance._original_shipping_carrier
        )
        if hasattr(instance, "_original_tracking_number")
        else False
    )

    if (
        hasattr(instance, "_original_tracking_number")
        and hasattr(instance, "_original_shipping_carrier")
        and not (
            instance._original_tracking_number
            and instance._original_shipping_carrier
        )
        and instance.tracking_number
        and instance.shipping_carrier
        and not tracking_unchanged
    ):

        def send_shipment_dispatched() -> None:
            order_shipment_dispatched.send(
                sender=sender,
                order=instance,
                tracking_number=instance.tracking_number,
                shipping_carrier=instance.shipping_carrier,
            )
            logger.debug(
                "Sent order_shipment_dispatched signal for order %s",
                instance.id,
            )

        transaction.on_commit(send_shipment_dispatched)


@receiver(order_created, dispatch_uid="order.handle_order_created")
def handle_order_created(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Handle order creation."""
    # Offline payments (COD, bank transfer) and already-paid orders get the
    # confirmation email immediately. Online payments (Stripe, Viva Wallet)
    # defer it to the payment-success webhook so the email only goes out
    # once the payment actually succeeds. Missing pay_way is treated as
    # offline to preserve the legacy fallback behavior.
    pay_way = order.pay_way
    is_online_pending = (
        pay_way is not None
        and pay_way.is_online_payment
        and order.payment_status != PaymentStatus.COMPLETED
    )
    if not is_online_pending:
        send_order_confirmation_email.delay(order.id)
    OrderHistory.log_note(order=order, note="Order created")

    # Live in-app notification for authenticated shoppers. The task
    # itself drops guests silently, so there's no is_guest check here.
    if order.user_id:
        transaction.on_commit(
            lambda oid=order.id: notify_order_created_live.delay(oid)
        )

    # Clear cart after successful order creation (both user and guest)
    from cart.models import Cart

    def clear_cart():
        """Clear cart after transaction commits."""
        try:
            if order.user:
                # For authenticated users, clear their cart
                cart = Cart.objects.filter(user=order.user).first()
                if cart:
                    cart.delete()  # Delete entire cart, not just items
                    logger.debug(
                        "Cleared cart for user %s after order %s creation",
                        order.user.id,
                        order.id,
                    )
            else:
                # For guest orders, try to get cart_id from order metadata
                cart_id = (
                    order.metadata.get("cart_id") if order.metadata else None
                )
                if cart_id:
                    cart = Cart.objects.filter(
                        id=cart_id, user__isnull=True
                    ).first()
                    if cart:
                        cart.delete()  # Delete entire cart
                        logger.info(
                            "Cleared guest cart %s after order %s creation",
                            cart_id,
                            order.id,
                        )
                else:
                    logger.debug(
                        "Guest order %s created - no cart_id in metadata",
                        order.id,
                    )
        except Exception as e:
            logger.error(
                "Error clearing cart for order %s: %s",
                order.id,
                e,
                exc_info=True,
            )

    # Clear cart after transaction commits
    transaction.on_commit(clear_cart)


@receiver(
    order_status_changed, dispatch_uid="order.handle_order_status_changed"
)
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

    transaction.on_commit(
        lambda oid=order.id, s=new_status: send_order_status_update_email.delay(
            oid, s
        )
    )

    # Live in-app notification. ``notify_order_status_changed_live``
    # filters internally for statuses we actually want to surface in the
    # bell (PROCESSING, SHIPPED, DELIVERED, COMPLETED, CANCELED), so
    # dispatching unconditionally is safe and centralises the policy in
    # one place (``order/notifications.py::_ORDER_STATUS_COPY``).
    if order.user_id:
        transaction.on_commit(
            lambda oid=order.id, s=new_status: (
                notify_order_status_changed_live.delay(oid, s)
            )
        )

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
        object.__setattr__(order, "_paid_signal_sent", True)
        logger.debug("Sent order_paid signal for order %s", order.id)

    logger.info(
        "Order %s status changed from %s to %s",
        order.id,
        old_status,
        new_status,
    )


@receiver(
    order_shipment_dispatched,
    dispatch_uid="order.notify_shipment_dispatched",
)
def notify_shipment_dispatched(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Forward the shipment-dispatched signal to the live notification task.

    The signal is already fired via ``transaction.on_commit`` in
    ``handle_order_post_save``, so we can call ``.delay`` directly — the
    row is guaranteed committed by the time we run.
    """
    if not order.user_id:
        return
    notify_order_shipment_dispatched_live.delay(order.id)


@receiver(
    pre_save, sender=OrderItem, dispatch_uid="order.handle_order_item_pre_save"
)
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


@receiver(
    post_save,
    sender=OrderItem,
    dispatch_uid="order.handle_order_item_post_save",
)
def handle_order_item_post_save(
    sender: type[OrderItem], instance: OrderItem, created: bool, **kwargs: Any
) -> None:
    """Handle order item changes and log history."""
    if created:
        # Stock is managed by StockManager (either via convert_reservation_to_sale
        # or decrement_stock in OrderService.create_order_from_cart).
        # We do NOT decrement stock here to avoid double-decrementing.
        # This signal handler only logs the order history.

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
        from django.db.models import F, Value
        from django.db.models.functions import Greatest

        product = instance.product
        stock_difference = instance._original_quantity - instance.quantity
        type(product).objects.filter(pk=product.pk).update(
            stock=Greatest(F("stock") + stock_difference, Value(0))
        )

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


@receiver(order_shipped, dispatch_uid="order.handle_order_shipped")
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
    # Email is sent by handle_order_status_changed via
    # send_order_status_update_email (uses the shipped template)


@receiver(order_delivered, dispatch_uid="order.handle_order_delivered")
def handle_order_delivered(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Handle order delivered signal."""
    OrderHistory.log_shipping_update(
        order=order,
        previous_value={"status": OrderStatus.SHIPPED.value},
        new_value={"status": OrderStatus.DELIVERED.value},
    )
    # Email is sent by handle_order_status_changed via
    # send_order_status_update_email (uses the delivered template)


@receiver(order_canceled, dispatch_uid="order.handle_order_canceled")
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
        # Email is sent by handle_order_status_changed via
        # send_order_status_update_email (uses the canceled template)

        logger.info(
            "Order %s canceled (previous status: %s)",
            order.id,
            previous_status,
        )

    except Exception as e:
        logger.error(
            "Error handling order_canceled signal: %s", e, exc_info=True
        )


@receiver(order_completed, dispatch_uid="order.handle_order_completed")
def handle_order_completed(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Handle order completed signal."""
    try:
        if order.document_type == OrderDocumentTypeEnum.INVOICE.value:
            # Generate the PDF invoice asynchronously. ``generate_order_invoice``
            # is idempotent via ``order.invoicing.generate_invoice`` — calling
            # twice returns the existing Invoice row, so the fact that
            # ``order_completed`` might fire again on a re-save is safe.
            transaction.on_commit(
                lambda oid=order.id: generate_order_invoice.delay(oid)
            )

        OrderHistory.log_note(order=order, note="Order completed")

        logger.info("Order %s marked as completed", order.id)

    except Exception as e:
        logger.error(
            "Error handling order_completed signal: %s", e, exc_info=True
        )


@receiver(order_refunded, dispatch_uid="order.handle_order_refunded")
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

        # Live notification so the shopper learns about the refund without
        # having to check email. ``notify_order_refunded_live`` silently
        # drops guest orders.
        if order.user_id:
            transaction.on_commit(
                lambda oid=order.id: notify_order_refunded_live.delay(oid)
            )

        logger.info("Order %s refunded", order.id)

    except Exception as e:
        logger.error(
            "Error handling order_refunded signal: %s", e, exc_info=True
        )


@receiver(order_returned, dispatch_uid="order.handle_order_returned")
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
        event: Event = kwargs["event"]
        payment_intent_id = event.data["object"]["id"]
        event_id = event.id

        logger.info("Stripe payment succeeded: %s", payment_intent_id)

        # Atomic idempotency check-and-mark with row lock to prevent
        # duplicate processing from parallel webhook deliveries.
        already_processed = False
        with transaction.atomic():
            order = (
                Order.objects.select_for_update()
                .filter(payment_id=payment_intent_id)
                .first()
            )
            if order:
                if order.metadata and order.metadata.get(
                    f"webhook_processed_{event_id}"
                ):
                    logger.info(
                        "Webhook %s already processed for order %s, skipping",
                        event_id,
                        order.id,
                    )
                    already_processed = True
                else:
                    if not order.metadata:
                        order.metadata = {}
                    order.metadata[f"webhook_processed_{event_id}"] = True
                    order.save(update_fields=["metadata"])

        if already_processed:
            return

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
            # Payment is confirmed — now the customer gets the
            # confirmation email. The task itself is idempotent via a
            # metadata reservation, so parallel webhook deliveries or a
            # subsequent checkout.session.completed event cannot cause
            # a duplicate send.
            send_order_confirmation_email.delay(order.id)

            # Live notification for the same event. The event-level
            # idempotency guard above (webhook_processed_{event_id})
            # already prevents duplicate dispatches from Stripe
            # redeliveries; the task itself is a single INSERT, so this
            # is safe at-most-once per event.
            if order.user_id:
                transaction.on_commit(
                    lambda oid=order.id: notify_payment_confirmed_live.delay(
                        oid
                    )
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
        event: Event = kwargs["event"]
        payment_intent_id = event.data["object"]["id"]
        event_id = event.id

        logger.info("Stripe payment failed: %s", payment_intent_id)

        # Event-level idempotency: Stripe may redeliver the same event.
        # A customer who has moved on to a retry (payment_id already
        # overwritten with the new intent) must not get a second
        # failure email from a late redelivery of the old one.
        already_processed = False
        with transaction.atomic():
            order_lookup = (
                Order.objects.select_for_update()
                .filter(payment_id=payment_intent_id)
                .first()
            )
            if order_lookup:
                if order_lookup.metadata and order_lookup.metadata.get(
                    f"webhook_processed_{event_id}"
                ):
                    logger.info(
                        "Webhook %s already processed for order %s, skipping",
                        event_id,
                        order_lookup.id,
                    )
                    already_processed = True
                else:
                    if not order_lookup.metadata:
                        order_lookup.metadata = {}
                    order_lookup.metadata[f"webhook_processed_{event_id}"] = (
                        True
                    )
                    order_lookup.save(update_fields=["metadata"])

        if already_processed:
            return

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
            # Notify the customer so they can retry instead of silently
            # sitting on a broken order.
            send_payment_failed_email.delay(order.id)

            # Parallel live notification — same idempotency story as
            # the succeeded branch above (guarded by the event-level
            # metadata flag).
            if order.user_id:
                transaction.on_commit(
                    lambda oid=order.id: notify_payment_failed_live.delay(oid)
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
        event: Event = kwargs["event"]
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
    """Handle Stripe dispute creation webhook.

    Looks up the order by charge/payment_id, flags it in metadata so
    staff can see the dispute state in the admin, and dispatches a staff
    notification email.  Order status is NOT changed automatically —
    that is a manual staff decision.
    """
    logger.debug("Processing charge.dispute.created webhook")

    try:
        event: Event = kwargs["event"]
        dispute_data = event.data["object"]
        charge_id = dispute_data.get("charge", "")
        dispute_id = dispute_data.get("id", "")
        reason = dispute_data.get("reason", "")

        logger.warning(
            "Stripe dispute created",
            extra={
                "charge_id": charge_id,
                "dispute_id": dispute_id,
                "reason": reason,
            },
        )

        if not charge_id:
            logger.error(
                "charge.dispute.created event missing charge id: %s",
                event.id,
            )
            return

        order = Order.objects.filter(payment_id=charge_id).first()
        if order is None:
            logger.warning(
                "No order found for disputed charge %s (dispute=%s)",
                charge_id,
                dispute_id,
            )
            return

        # Flag the order for staff review. Do NOT change order status —
        # refund/acceptance is a manual decision.
        if not order.metadata:
            order.metadata = {}
        order.metadata["disputed"] = True
        order.metadata["dispute_id"] = dispute_id
        order.metadata["dispute_reason"] = reason
        order.save(update_fields=["metadata"])

        OrderHistory.log_note(
            order=order,
            note=(
                f"Stripe dispute created: dispute_id={dispute_id}, "
                f"charge_id={charge_id}, reason={reason}"
            ),
        )

        logger.warning(
            "Order #%s flagged as disputed",
            order.id,
            extra={
                "order_id": order.id,
                "dispute_id": dispute_id,
                "charge_id": charge_id,
                "reason": reason,
            },
        )

        transaction.on_commit(
            lambda oid=order.id, did=dispute_id: (
                send_dispute_notification_email.delay(oid, did)
            )
        )

    except Exception as e:
        logger.error(
            "Error handling charge.dispute.created: %s", e, exc_info=True
        )


@djstripe_receiver("checkout.session.completed")
def handle_stripe_checkout_completed(sender, **kwargs):
    """Handle Stripe checkout session completion webhook."""
    logger.debug("Processing checkout.session.completed webhook")

    try:
        event: Event = kwargs["event"]
        session_data = event.data["object"]
        session_id = session_data["id"]
        payment_intent_id = session_data.get("payment_intent")
        payment_status = session_data.get("payment_status")
        event_id = event.id

        logger.info("Checkout session completed: %s", session_id)

        order_id = session_data.get("metadata", {}).get("order_id")

        if not order_id:
            logger.warning("No order_id in session metadata: %s", session_id)
            return

        # Atomic idempotency check-and-mark with row lock, then perform all
        # state mutations inside the same transaction to prevent double-save
        # and parallel duplicate processing.
        with transaction.atomic():
            try:
                order = Order.objects.select_for_update().get(id=order_id)
            except Order.DoesNotExist:
                logger.error(
                    "Order %s not found for session %s", order_id, session_id
                )
                return

            if order.metadata and order.metadata.get(
                f"webhook_processed_{event_id}"
            ):
                logger.info(
                    "Webhook %s already processed for order %s, skipping",
                    event_id,
                    order.id,
                )
                return

            if not order.metadata:
                order.metadata = {}
            order.metadata[f"webhook_processed_{event_id}"] = True

            if payment_status == "paid" and payment_intent_id:
                from order.payment_events import publish_payment_status

                order.mark_as_paid(
                    payment_id=payment_intent_id, payment_method="stripe"
                )

                if order.status == OrderStatus.PENDING:
                    OrderService.update_order_status(
                        order, OrderStatus.PROCESSING
                    )

                order.metadata["stripe_checkout_session_id"] = session_id
                order.metadata["stripe_payment_intent_id"] = payment_intent_id
                order.save(update_fields=["metadata"])

                publish_payment_status(order)

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

                # Payment confirmed via Stripe Checkout — send the
                # confirmation email now (idempotent).
                transaction.on_commit(
                    lambda oid=order.id: send_order_confirmation_email.delay(
                        oid
                    )
                )

            elif payment_status == "unpaid":
                order.payment_status = PaymentStatus.PENDING
                order.save(update_fields=["payment_status", "metadata"])

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
        event: Event = kwargs["event"]
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
