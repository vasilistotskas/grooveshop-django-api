import logging
from typing import Any

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
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
    send_admin_new_order_email,
    send_dispute_notification_email,
    send_order_confirmation_email,
    send_order_status_update_email,
    send_payment_failed_email,
    send_refund_confirmation_email,
    send_shipping_notification_email,
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
    # once the payment actually succeeds. Missing pay_way (the FK was
    # ``SET_NULL`` because someone deleted the PayWay row) is treated as
    # offline so the customer still receives a confirmation.
    pay_way = order.pay_way
    is_online_pending = (
        pay_way is not None
        and pay_way.is_online_payment
        and order.payment_status != PaymentStatus.COMPLETED
    )
    if not is_online_pending:
        send_order_confirmation_email.delay(order.id)
    send_admin_new_order_email.delay(order.id)
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

    # Customer-notifications suppression flag (set by
    # OrderService._suppress_customer_status_notifications for chained
    # transitions where the customer just got the previous status's
    # email/toast and a second one ms later would feel like spam).
    # The email task already short-circuits via its own metadata flag;
    # checking the WS-flag here keeps the toast in lockstep with that.
    suppress_customer = bool(
        order.metadata
        and order.metadata.get(f"suppress_status_ws_{new_status}")
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
    if order.user_id and not suppress_customer:
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
        order_canceled.send(
            sender=sender, order=order, previous_status=old_status
        )

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
    order_shipment_dispatched,
    dispatch_uid="order.email_shipment_dispatched",
)
def email_shipment_dispatched(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Send the customer their carrier-tracking email.

    Fires alongside the live in-app notification: when the order
    transitions from "no tracking" to "has tracking + carrier" — i.e.
    a courier voucher minted and the parcel ID is on the order. Both
    online (Stripe / Viva → handle_payment_succeeded → courier
    dispatch) and COD (offline create → courier dispatch) paths land
    here because both end in ``order.add_tracking_info(...)`` →
    post-save → ``order_shipment_dispatched`` signal.

    The shipping-notification task itself is idempotent on the
    ``shipping_notification_email_sent`` metadata flag, so a duplicate
    fire (e.g. tracking re-set by an admin correction) won't email
    twice. Deferred to ``transaction.on_commit`` for the same reason
    the live notification is — the worker must see the persisted
    tracking_number.
    """
    transaction.on_commit(
        lambda oid=order.id: send_shipping_notification_email.delay(oid)
    )


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
        from order.stock import StockManager  # noqa: PLC0415

        product = instance.product
        stock_difference = instance._original_quantity - instance.quantity
        StockManager.adjust_stock(
            product=product,
            delta=stock_difference,
            reason="admin order item edit",
            performed_by=None,
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
    """Handle order canceled signal.

    Single source of truth for cancellation side-effects beyond the
    status flip itself: history note, courier-voucher cascade, and
    backfilling ``metadata['cancellation']`` for entry points (like
    Django admin's form save) that bypass
    :meth:`OrderService.cancel_order`. Verified necessary on
    2026-05-16 after prod order 60 had ``status=CANCELED`` but
    ``metadata.cancellation = null`` and voucher 9771614856 still
    alive at ACS.
    """
    previous_status = kwargs.get("previous_status")
    # ``order_canceled`` is dispatched by ``handle_order_status_changed``
    # without forwarding the ``reason`` kwarg, so the programmatic path
    # (``OrderService.cancel_order`` with a meaningful reason) needs us
    # to recover it from the metadata that ``cancel_order`` writes
    # BEFORE the save that triggers this signal chain. Falls back to
    # "admin status change" for the admin-form-save path where neither
    # the kwarg nor the metadata is populated.
    metadata_reason = (
        (order.metadata or {}).get("cancellation", {}).get("reason")
    )
    cancellation_reason = (
        kwargs.get("reason") or metadata_reason or "admin status change"
    )

    try:
        # Defensive: ``OrderService.cancel_order`` writes a rich
        # ``metadata['cancellation']`` dict (reason / canceled_by /
        # canceled_at / previous_status) BEFORE saving the row, so
        # this block is a no-op on that path. For the admin form
        # path we initialise it here so the carrier cascade has a
        # parent dict to write its ``shipment_cancel`` outcome into.
        if not order.metadata:
            order.metadata = {}
        cancellation = order.metadata.setdefault("cancellation", {})
        cancellation.setdefault("canceled_at", timezone.now().isoformat())
        cancellation.setdefault("previous_status", previous_status)
        cancellation.setdefault("reason", cancellation_reason)

        if kwargs.get("reason"):
            OrderHistory.log_note(
                order=order,
                note=f"Order canceled. Reason: {kwargs['reason']}",
            )
        # Email is sent by handle_order_status_changed via
        # send_order_status_update_email (uses the canceled template)

        # Cascade to the courier voucher. Lives here as a safety net
        # so every path that flips ``order.status`` to CANCELED —
        # including admin form saves that go straight to Order.save()
        # — reaches the carrier. ``OrderService.cancel_order`` runs
        # the cascade synchronously itself; we detect that via the
        # ``shipment_cancel`` outcome breadcrumb and skip, so the
        # programmatic path doesn't double-fire and pre-existing
        # tests that rely on synchronous cascade behaviour keep
        # working.
        if "shipment_cancel" not in cancellation:
            OrderService.cancel_attached_shipment(order, cancellation_reason)

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

        # Email confirmation. Idempotent via the
        # ``refund_confirmation_email_sent`` reservation flag, so the
        # in-app refund path (OrderService.refund_order) and the
        # Stripe ``charge.refunded`` webhook both firing
        # ``order_refunded.send`` for the same order can't double-
        # email the customer. Guest orders DO get the email — unlike
        # the live notification which is account-bound, the email
        # uses ``order.email`` as the recipient.
        transaction.on_commit(
            lambda oid=order.id: send_refund_confirmation_email.delay(oid)
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
            # Direct .delay(): handle_payment_succeeded() has already
            # returned and committed its own @transaction.atomic block;
            # there is no outer transaction here so on_commit would be
            # a no-op wrapper.  Both task dispatches use the same pattern.
            send_order_confirmation_email.delay(order.id)

            # Live notification for the same event. The event-level
            # idempotency guard above (webhook_processed_{event_id})
            # already prevents duplicate dispatches from Stripe
            # redeliveries; the task itself is a single INSERT, so this
            # is safe at-most-once per event.
            if order.user_id:
                notify_payment_confirmed_live.delay(order.id)

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
            # Wrapped in on_commit: handle_payment_failed runs inside
            # @transaction.atomic; the worker must see the committed row.
            transaction.on_commit(
                lambda oid=order.id: send_payment_failed_email.delay(oid)
            )

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
    """Handle Stripe ``payment_intent.requires_action`` webhook.

    Stripe redelivers events on retry; this handler must be idempotent
    AND must not regress a terminal payment_status. A delayed
    ``requires_action`` arriving after a ``succeeded`` event would
    otherwise overwrite ``COMPLETED`` back to ``PENDING`` — leaving the
    order paid-but-not-paid and the customer with no signal.
    """
    logger.debug("Processing payment_intent.requires_action webhook")

    try:
        event: Event = kwargs["event"]
        payment_intent_id = event.data["object"]["id"]
        event_id = event.id

        logger.info("Stripe payment requires action: %s", payment_intent_id)

        with transaction.atomic():
            order = (
                Order.objects.select_for_update()
                .filter(payment_id=payment_intent_id)
                .first()
            )
            if order is None:
                logger.error(
                    "Order not found for payment_intent: %s", payment_intent_id
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

            # Only relax PROCESSING/PENDING back to PENDING — never
            # walk back COMPLETED/FAILED/REFUNDED. Webhook redelivery
            # ordering is not guaranteed.
            if order.payment_status not in (
                PaymentStatus.PROCESSING,
                PaymentStatus.PENDING,
            ):
                logger.warning(
                    "requires_action ignored: order=%s already in terminal "
                    "payment_status=%s (event=%s)",
                    order.id,
                    order.payment_status,
                    event_id,
                    extra={
                        "order_id": order.id,
                        "payment_status": order.payment_status,
                        "event_id": event_id,
                    },
                )
                return

            previous_payment_status = order.payment_status
            order.payment_status = PaymentStatus.PENDING
            if not order.metadata:
                order.metadata = {}
            order.metadata[f"webhook_processed_{event_id}"] = True
            order.save(update_fields=["payment_status", "metadata"])

        OrderHistory.log_note(
            order=order,
            note=(
                f"Payment requires additional action (3D Secure, etc.) - "
                f"Payment ID: {payment_intent_id}"
            ),
        )
        logger.info(
            "Stripe payment_intent.requires_action handled",
            extra={
                "order_id": order.id,
                "payment_intent_id": payment_intent_id,
                "previous_payment_status": str(previous_payment_status),
                "event_id": event_id,
            },
        )

    except Exception as e:
        logger.error(
            "Error handling payment_intent.requires_action: %s",
            e,
            exc_info=True,
        )


@djstripe_receiver("charge.refunded")
def handle_stripe_charge_refunded(sender, **kwargs):
    """Handle Stripe ``charge.refunded`` webhook (full + partial).

    Stripe fires this after a refund issued from the dashboard, the
    Stripe API, or our own ``OrderService.refund_order`` path. Without
    a handler, refunds initiated outside our admin would never hit the
    DB and the order would silently look paid.

    Mapping:
    * ``amount_refunded == amount`` → ``PaymentStatus.REFUNDED``
    * ``amount_refunded < amount``  → ``PaymentStatus.PARTIALLY_REFUNDED``

    ``Order.status`` is intentionally NOT changed — the canonical
    transition table only reaches ``REFUNDED`` from ``RETURNED``, and
    deciding whether a refund means the goods were also returned is a
    business call. Admin can drive that from the order page.

    Idempotency: ``webhook_processed_{event_id}`` flag mirrors the
    succeeded / failed handlers; redeliveries are no-ops.
    """
    logger.debug("Processing charge.refunded webhook")

    try:
        event: Event = kwargs["event"]
        charge = event.data["object"]
        event_id = event.id
        payment_intent_id = charge.get("payment_intent") or ""
        amount = int(charge.get("amount") or 0)
        amount_refunded = int(charge.get("amount_refunded") or 0)

        if not payment_intent_id:
            logger.warning(
                "charge.refunded event %s has no payment_intent — skipping",
                event_id,
            )
            return

        logger.info(
            "Stripe charge refunded: payment_intent=%s amount_refunded=%s/%s",
            payment_intent_id,
            amount_refunded,
            amount,
        )

        is_full_refund = amount_refunded >= amount > 0
        new_payment_status = (
            PaymentStatus.REFUNDED
            if is_full_refund
            else PaymentStatus.PARTIALLY_REFUNDED
        )

        with transaction.atomic():
            order = (
                Order.objects.select_for_update()
                .filter(payment_id=payment_intent_id)
                .first()
            )
            if order is None:
                logger.warning(
                    "No order found for refunded payment_intent %s "
                    "(charge event=%s)",
                    payment_intent_id,
                    event_id,
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

            previous_payment_status = order.payment_status
            order.payment_status = new_payment_status
            if not order.metadata:
                order.metadata = {}
            order.metadata[f"webhook_processed_{event_id}"] = True
            refunds = list(order.metadata.get("refunds") or [])
            refunds.append(
                {
                    "stripe_event_id": event_id,
                    "amount_refunded": amount_refunded,
                    "amount": amount,
                    "currency": (charge.get("currency") or "").lower(),
                    "is_full_refund": is_full_refund,
                    "payment_intent": payment_intent_id,
                }
            )
            order.metadata["refunds"] = refunds
            order.save(update_fields=["payment_status", "metadata"])

        OrderHistory.log_payment_update(
            order=order,
            previous_value={"payment_status": str(previous_payment_status)},
            new_value={
                "payment_status": str(new_payment_status),
                "amount_refunded": amount_refunded,
                "is_full_refund": is_full_refund,
            },
        )

        if is_full_refund:
            order_refunded.send(sender=Order, order=order)

    except Exception as e:
        logger.error("Error handling charge.refunded: %s", e, exc_info=True)


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

        event_id = event.id

        # Lock the order row + idempotency-check the event so Stripe
        # redeliveries don't (1) overwrite later dispute fields, or
        # (2) re-trigger the staff notification email.
        with transaction.atomic():
            order = (
                Order.objects.select_for_update()
                .filter(payment_id=charge_id)
                .first()
            )
            if order is None:
                logger.warning(
                    "No order found for disputed charge %s (dispute=%s)",
                    charge_id,
                    dispute_id,
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

            # Flag the order for staff review. Do NOT change order
            # status — refund/acceptance is a manual decision.
            if not order.metadata:
                order.metadata = {}
            order.metadata["disputed"] = True
            order.metadata["dispute_id"] = dispute_id
            order.metadata["dispute_reason"] = reason
            order.metadata[f"webhook_processed_{event_id}"] = True
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
                "event_id": event_id,
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
    """Handle Stripe ``checkout.session.expired`` webhook.

    Stripe redelivers expirations; lock the row and idempotency-check
    so redeliveries don't append duplicate "session expired" history
    rows or thrash the metadata flag.
    """
    logger.debug("Processing checkout.session.expired webhook")

    try:
        event: Event = kwargs["event"]
        session_data = event.data["object"]
        session_id = session_data["id"]
        event_id = event.id
        order_id = session_data.get("metadata", {}).get("order_id")

        logger.info("Checkout session expired: %s", session_id)

        if not order_id:
            return

        with transaction.atomic():
            order = (
                Order.objects.select_for_update().filter(id=order_id).first()
            )
            if order is None:
                logger.error(
                    "Order %s not found for expired session %s",
                    order_id,
                    session_id,
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

            if order.is_paid:
                return

            if not order.metadata:
                order.metadata = {}
            order.metadata["stripe_checkout_expired"] = True
            order.metadata["stripe_checkout_session_id"] = session_id
            order.metadata[f"webhook_processed_{event_id}"] = True
            order.save(update_fields=["metadata"])

        OrderHistory.log_note(
            order=order,
            note=f"Checkout session expired: {session_id}",
        )

        logger.info(
            "Marked order %s checkout session as expired",
            order_id,
            extra={
                "order_id": order.id,
                "session_id": session_id,
                "event_id": event_id,
            },
        )

    except Exception as e:
        logger.error(
            "Error handling checkout.session.expired: %s", e, exc_info=True
        )
