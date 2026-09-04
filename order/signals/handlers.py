import logging
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from django_tenants.utils import schema_context
from djstripe.event_handlers import djstripe_receiver
from djstripe.models import Event

from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.status import (
    SETTLED_PAYMENT_STATUSES,
    OrderStatus,
    PaymentStatus,
)
from order.models.history import OrderHistory, OrderItemHistory
from order.models.item import OrderItem
from order.models.order import Order
from order.notifications import (
    notify_order_created_live,
    notify_order_refunded_live,
    notify_order_status_changed_live,
    notify_payment_confirmed_live,
    notify_payment_failed_live,
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
    order_shipment_dispatched,
    order_shipped,
    order_status_changed,
)
from order.signals._tenant import with_tenant_schema_from_event
from order.tasks import (
    generate_order_invoice,
    push_order_event_to_gateway,
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
    # Capture the active schema BEFORE registering on_commit callbacks.
    # A save inside a webhook's schema_context commits AFTER that
    # context exits, so the deferred signal emission (and every
    # receiver + task dispatch it triggers) would otherwise run against
    # the public schema. Re-entering schema_context here keeps the
    # whole downstream chain — ORM reads and TenantTask header
    # stamping — in the owning tenant's schema.
    _schema = connection.schema_name

    if created:

        def send_created_signal():
            with schema_context(_schema):
                order_created.send(sender=sender, order=instance)
                logger.debug(
                    "Sent order_created signal for new order %s", instance.id
                )

        # Defer to on_commit so the Celery task sees the committed row.
        transaction.on_commit(send_created_signal)
        return

    status_changed = (
        hasattr(instance, "_original_status")
        and instance._original_status != instance.status
    )
    if status_changed:
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
            with schema_context(_schema):
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
    # Fires only on the transition INTO having tracking: at least one
    # field was empty before and both are set now. That already rules out
    # a re-save with identical values — if the old value equalled the new
    # non-empty one, the old one was non-empty too, which the "not both
    # set before" term excludes. An extra equality check read as a second
    # safeguard but could never change the outcome.
    tracking_dispatched = (
        hasattr(instance, "_original_tracking_number")
        and hasattr(instance, "_original_shipping_carrier")
        and not (
            instance._original_tracking_number
            and instance._original_shipping_carrier
        )
        and bool(instance.tracking_number)
        and bool(instance.shipping_carrier)
    )
    if tracking_dispatched:

        def send_shipment_dispatched() -> None:
            with schema_context(_schema):
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

    # Agent-gateway order-event push: one event per save that changed
    # the order status, the payment status (which can move without a
    # status change, e.g. a refund) or set tracking info. The task
    # re-reads fresh values from the DB, so a single enqueue covers a
    # save that changed several of them at once. Gated on the gateway
    # URL so non-agent deployments never enqueue.
    payment_status_changed = (
        hasattr(instance, "_original_payment_status")
        and instance._original_payment_status != instance.payment_status
    )
    if settings.AGENT_GATEWAY_INTERNAL_URL and (
        status_changed or payment_status_changed or tracking_dispatched
    ):
        # ``_schema`` was captured at the top of this function and is
        # bound into the lambda below as a default — by the time
        # on_commit fires the schema context has exited and TenantTask
        # would stamp the public schema (same contract as every other
        # dispatch in this module). It is NOT re-read here: the three
        # closures above reference ``_schema`` as a free variable, so
        # rebinding the name would retroactively change what they see.
        transaction.on_commit(
            lambda oid=instance.id, s=_schema: (
                push_order_event_to_gateway.apply_async(
                    args=[oid], headers={"_schema_name": s}
                )
            )
        )


def _cart_for_order(order: Order):
    """The cart this order was built from, or None."""
    from cart.models import Cart

    if order.user:
        return Cart.objects.filter(user=order.user).first()

    # For guest orders, read the cart UUID from the cart snapshot both
    # creation paths write into order metadata
    # (OrderService.create_order_from_cart[_offline]). The integer PK is
    # internal only, so the lookup uses the UUID.
    cart_uuid = (
        order.metadata.get("cart_snapshot", {}).get("cart_uuid")
        if order.metadata
        else None
    )
    if not cart_uuid:
        logger.debug("Guest order %s - no cart_uuid in metadata", order.id)
        return None
    return Cart.objects.filter(uuid=cart_uuid, user__isnull=True).first()


def _clear_cart_for_order(order: Order) -> None:
    """Take THIS ORDER's lines out of the cart, and drop the cart if that
    empties it.

    Not "delete the cart": an online order deliberately leaves the cart
    standing until the payment lands (``handle_order_created`` skips the
    clear while ``awaits_online_payment``, so a shopper who presses Back
    still has a basket), and a hosted session can stay open for hours.
    Anything the shopper puts in the cart during that window is theirs,
    not part of the order being paid for, and deleting the row took it
    with no trace. The cart is a per-user singleton, so matching it by
    id would not have helped — only the LINES distinguish the two.

    Lines the order has but the cart does not (promotion gifts injected
    at creation) simply find nothing to remove.
    """
    try:
        cart = _cart_for_order(order)
        if cart is None:
            return

        for item in order.items.all():
            cart_item = cart.items.filter(product_id=item.product_id).first()
            if cart_item is None:
                continue
            if cart_item.quantity > item.quantity:
                cart_item.quantity -= item.quantity
                cart_item.save(update_fields=["quantity"])
            else:
                cart_item.delete()

        if cart.items.exists():
            logger.info(
                "Cart %s kept after order %s — it holds items the order "
                "did not include",
                cart.uuid,
                order.id,
            )
            return

        cart.delete()
        logger.debug("Cleared cart %s after order %s", cart.uuid, order.id)
    except Exception as e:
        logger.error(
            "Error clearing cart for order %s: %s", order.id, e, exc_info=True
        )


@receiver(order_paid, dispatch_uid="order.clear_cart_on_paid")
def handle_order_paid_clear_cart(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Clear the cart once a hosted payment actually completes.

    Counterpart to the ``Order.awaits_online_payment`` skip in
    ``handle_order_created``. Idempotent: cash-on-delivery orders have
    already had their cart removed at creation, so this is a no-op for
    them.
    """
    transaction.on_commit(lambda o=order: _clear_cart_for_order(o))


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
    # ``_schema`` is captured at lambda-build time (inside the active
    # schema_context); by the time on_commit fires the context has
    # exited and TenantTask would stamp the public schema.
    if order.user_id:
        _schema = connection.schema_name
        transaction.on_commit(
            lambda oid=order.id, s=_schema: (
                notify_order_created_live.apply_async(
                    args=[oid], headers={"_schema_name": s}
                )
            )
        )

    # Clear the cart — but NOT while the shopper still owes an online
    # payment. See ``Order.awaits_online_payment``.
    if not order.awaits_online_payment:
        transaction.on_commit(lambda o=order: _clear_cart_for_order(o))


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
    # email/toast and a second one ms later would feel like spam, e.g.
    # the DELIVERED → COMPLETED auto-advance). Gates both the email and
    # the WS toast below so they stay in lockstep.
    suppress_customer = bool(
        order.metadata
        and order.metadata.get(f"suppress_status_ws_{new_status}")
    )

    # Customer email dispatch policy — single source of truth.
    #   • PENDING / PROCESSING are internal milestones (covered by the
    #     order-received and payment-confirmed notifications) and never
    #     get their own email.
    #   • SHIPPED is owned by the dedicated shipping-notification email,
    #     which carries the tracking number and only sends once the
    #     parcel is genuinely in transit (the task self-gates on
    #     status == SHIPPED + tracking present, so an early fire at
    #     voucher-mint harmlessly defers).
    #   • Everything else (DELIVERED, CANCELED, COMPLETED, REFUNDED,
    #     RETURNED) uses the generic status-update template.
    # ``_schema`` captured at lambda-build time — on_commit fires after
    # any enclosing schema_context has exited (see the refund handler).
    _schema = connection.schema_name

    if not suppress_customer:
        if new_status == OrderStatus.SHIPPED.value:
            transaction.on_commit(
                lambda oid=order.id, sc=_schema: (
                    send_shipping_notification_email.apply_async(
                        args=[oid], headers={"_schema_name": sc}
                    )
                )
            )
        elif new_status not in (
            OrderStatus.PENDING.value,
            OrderStatus.PROCESSING.value,
        ):
            transaction.on_commit(
                lambda oid=order.id, s=new_status, sc=_schema: (
                    send_order_status_update_email.apply_async(
                        args=[oid, s], headers={"_schema_name": sc}
                    )
                )
            )

    # Live in-app notification. ``notify_order_status_changed_live``
    # filters internally for statuses we actually want to surface in the
    # bell (PROCESSING, SHIPPED, DELIVERED, COMPLETED, CANCELED), so
    # dispatching unconditionally is safe and centralises the policy in
    # one place (``order/notifications.py::_ORDER_STATUS_COPY``).
    if order.user_id and not suppress_customer:
        transaction.on_commit(
            lambda oid=order.id, s=new_status, sc=_schema: (
                notify_order_status_changed_live.apply_async(
                    args=[oid, s], headers={"_schema_name": sc}
                )
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
    dispatch_uid="order.email_shipment_dispatched",
)
def email_shipment_dispatched(
    sender: type[Order], order: Order, **kwargs: Any
) -> None:
    """Dispatch the customer's carrier-tracking email when tracking lands.

    Fires when the order transitions from "no tracking" to "has
    tracking + carrier" — i.e. a courier voucher minted. On a normal
    COD/online order that happens at voucher-mint while the order is
    still PROCESSING, so the shipping-notification task self-defers
    (it only sends once ``status == SHIPPED``). This receiver exists
    for the inverse ordering: an admin who flips the order to SHIPPED
    first and attaches the tracking number afterwards — here the
    SHIPPED transition deferred, and this fire is the one that
    actually sends.

    The task is idempotent on the ``shipping_notification_email_sent``
    metadata flag, so the two dispatch points (this signal and the
    SHIPPED status transition) collapse to a single email. Deferred to
    ``transaction.on_commit`` so the worker sees the persisted
    tracking_number.
    """
    _schema = connection.schema_name
    transaction.on_commit(
        lambda oid=order.id, s=_schema: (
            send_shipping_notification_email.apply_async(
                args=[oid], headers={"_schema_name": s}
            )
        )
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
        from order.stock import StockManager

        product = instance.product
        stock_difference = instance._original_quantity - instance.quantity
        StockManager.adjust_stock(
            product=product,
            delta=stock_difference,
            reason="admin order item edit",
            performed_by=None,
            order_id=instance.order_id,
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

    # ``is not None`` rather than truthiness: ``Money(0, "EUR")`` is
    # falsy, so correcting a line priced at zero — a free-gift line, or a
    # data-entry fix — produced no history entry at all.
    if (
        hasattr(instance, "_original_price")
        and instance._original_price is not None
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
    """Handle order shipped signal.

    The defensive ``update_order_status`` below exists only for callers
    that emit ``order_shipped`` directly (tests, admin tooling) without
    routing through the state machine. It must only *advance* a
    not-yet-shipped order — never regress one already past SHIPPED.

    When a carrier poll (ACS/BoxNow) sees a same-poll PROCESSING →
    DELIVERED jump it bridges by writing SHIPPED then DELIVERED on the
    same ``order`` instance inside one transaction. By the time the
    SHIPPED transition's ``on_commit`` hook re-emits ``order_shipped``,
    ``order.status`` already reads DELIVERED, so a blind
    ``update_order_status(order, SHIPPED)`` raised
    ``InvalidStatusTransitionError`` (DELIVERED → SHIPPED). That
    exception aborted the remaining commit hooks and silently dropped the
    customer's DELIVERED email + notification (prod orders, 2026-07-17).
    """
    pre_shipped = (OrderStatus.PENDING.value, OrderStatus.PROCESSING.value)
    if order.status in pre_shipped:
        logger.info("Updating order %s status to shipped", order.id)
        OrderService.update_order_status(order, OrderStatus.SHIPPED)
    else:
        # Already at/past SHIPPED (e.g. a carrier same-poll
        # PROCESSING→DELIVERED bridge). Skipping the defensive re-bump is
        # intentional — logged so the "why didn't order_shipped advance
        # the status?" question is answerable without re-deriving it.
        logger.debug(
            "handle_order_shipped: order %s already at %s (past SHIPPED) "
            "— skipping defensive status bump",
            order.id,
            order.status,
        )

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
            _schema = connection.schema_name
            transaction.on_commit(
                lambda oid=order.id, s=_schema: (
                    generate_order_invoice.apply_async(
                        args=[oid], headers={"_schema_name": s}
                    )
                )
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

        # Capture the active tenant schema BEFORE the on_commit lambdas.
        # The Stripe charge.refunded handler dispatches order_refunded.send
        # synchronously inside @with_tenant_schema_from_event's
        # schema_context; by the time these lambdas fire post-COMMIT, the
        # schema_context has exited and connection.schema_name is back
        # to public. Without explicit capture, TenantTask.apply_async
        # would stamp _schema_name=public on the email + live-notification
        # tasks and the worker would run against the wrong schema.
        _schema = connection.schema_name

        # Live notification so the shopper learns about the refund without
        # having to check email. ``notify_order_refunded_live`` silently
        # drops guest orders.
        if order.user_id:
            transaction.on_commit(
                lambda oid=order.id, s=_schema: (
                    notify_order_refunded_live.apply_async(
                        args=[oid], headers={"_schema_name": s}
                    )
                )
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
            lambda oid=order.id, s=_schema: (
                send_refund_confirmation_email.apply_async(
                    args=[oid], headers={"_schema_name": s}
                )
            )
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
@with_tenant_schema_from_event
def handle_stripe_payment_succeeded(sender, **kwargs):
    """Handle Stripe payment success webhook.

    This receiver runs inside dj-stripe's webhook ``transaction.atomic``
    block, so a failure here rolls back BOTH our idempotency mark and
    dj-stripe's ``Event`` row — Stripe then redelivers and we reprocess
    cleanly. We therefore must let processing errors PROPAGATE (G0231):
    swallowing them would commit the ``webhook_processed`` mark against a
    charged-but-unrecorded order that Stripe never retries (a redelivery
    early-returns on the existing Event id), stranding it at PENDING until
    ``auto_cancel_stuck_pending_orders`` cancels it 24h later with no
    refund and no alert.
    """
    logger.debug("Processing payment_intent.succeeded webhook")

    try:
        event: Event = kwargs["event"]
        payment_intent_id = event.data["object"]["id"]
        event_id = event.id
    except (KeyError, TypeError) as e:
        # Malformed event payload — a redelivery carries the same bad body,
        # so log and drop rather than 500-looping Stripe. Nothing has been
        # committed at this point.
        logger.error(
            "Malformed payment_intent.succeeded event: %s", e, exc_info=True
        )
        return

    logger.info("Stripe payment succeeded: %s", payment_intent_id)

    # Gift-card purchases are NOT orders — resolve them first and stop.
    # ``complete_purchase`` is idempotent (status guard), so webhook
    # redeliveries are harmless.
    from giftcard.models import GiftCardPurchase

    purchase = (
        GiftCardPurchase.objects.select_for_update()
        .filter(payment_id=payment_intent_id)
        .first()
    )
    if purchase:
        from giftcard.services import GiftCardService

        GiftCardService.complete_purchase(
            purchase, payment_id=payment_intent_id
        )
        logger.info(
            "Gift card purchase %s completed via webhook", purchase.uuid
        )
        return

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

    # NO try/except around the processing section: any error must
    # propagate so dj-stripe's outer atomic rolls back the mark + Event
    # row and Stripe redelivers (G0231).
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
        # Payment is confirmed — dispatch the confirmation email and live
        # toast on commit (G0230). Both fire only if dj-stripe's outer
        # transaction commits, so a later rollback discards them; the
        # worker also sees the committed row. Each task is independently
        # idempotent (metadata reservation / event-level guard).
        # ``_schema`` captured at lambda-build time: on_commit fires
        # after @with_tenant_schema_from_event's schema_context exits.
        _schema = connection.schema_name
        transaction.on_commit(
            lambda oid=order.id, s=_schema: (
                send_order_confirmation_email.apply_async(
                    args=[oid], headers={"_schema_name": s}
                )
            )
        )
        if order.user_id:
            transaction.on_commit(
                lambda oid=order.id, s=_schema: (
                    notify_payment_confirmed_live.apply_async(
                        args=[oid], headers={"_schema_name": s}
                    )
                )
            )


@djstripe_receiver("payment_intent.payment_failed")
@with_tenant_schema_from_event
def handle_stripe_payment_failed(sender, **kwargs):
    """Handle Stripe payment failure webhook."""
    logger.debug("Processing payment_intent.payment_failed webhook")

    try:
        event: Event = kwargs["event"]
        payment_intent_id = event.data["object"]["id"]
        event_id = event.id

        logger.info("Stripe payment failed: %s", payment_intent_id)

        # Gift-card purchases are NOT orders — flag them failed and stop.
        from giftcard.enum import GiftCardPurchaseStatus
        from giftcard.models import GiftCardPurchase

        purchase = GiftCardPurchase.objects.filter(
            payment_id=payment_intent_id,
            status=GiftCardPurchaseStatus.PENDING,
        ).first()
        if purchase:
            purchase.status = GiftCardPurchaseStatus.FAILED
            purchase.save(update_fields=["status"])
            logger.info("Gift card purchase %s failed", purchase.uuid)
            return

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
            # ``_schema`` is captured at lambda-build time so the task is
            # enqueued against the tenant schema this handler entered via
            # @with_tenant_schema_from_event — by the time on_commit fires,
            # connection.schema_name has reverted to public.
            _schema = connection.schema_name
            transaction.on_commit(
                lambda oid=order.id, s=_schema: (
                    send_payment_failed_email.apply_async(
                        args=[oid], headers={"_schema_name": s}
                    )
                )
            )

            # Parallel live notification — same idempotency story as
            # the succeeded branch above (guarded by the event-level
            # metadata flag).
            if order.user_id:
                transaction.on_commit(
                    lambda oid=order.id, s=_schema: (
                        notify_payment_failed_live.apply_async(
                            args=[oid], headers={"_schema_name": s}
                        )
                    )
                )

    except (KeyError, TypeError) as e:
        # Malformed payload only — see charge.refunded above. A failure in
        # the processing below must reach dj-stripe so the Event row rolls
        # back and Stripe redelivers.
        logger.error(
            "Malformed payment_intent.payment_failed payload: %s",
            e,
            exc_info=True,
        )


@djstripe_receiver("payment_intent.requires_action")
@with_tenant_schema_from_event
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
@with_tenant_schema_from_event
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

    except (KeyError, TypeError) as e:
        # Malformed payload only. Everything else must PROPAGATE (G0231):
        # this runs inside dj-stripe's webhook atomic, so swallowing
        # commits the Event row and Stripe never redelivers. The history
        # write and ``order_refunded`` above run AFTER the inner atomic
        # has committed the refund, so losing them leaves an order that
        # reads REFUNDED while the customer is never emailed about the
        # money coming back.
        logger.error("Malformed charge.refunded payload: %s", e, exc_info=True)


@djstripe_receiver("charge.dispute.created")
@with_tenant_schema_from_event
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
        # Look up by ``payment_intent`` (pi_…), NOT ``charge`` (ch_…):
        # every write to ``Order.payment_id`` in this codebase stores a
        # PaymentIntent id, so matching on the charge id never hits and
        # the whole dispute flow was silently dead (G0232). ``charge`` is
        # retained for the audit note / logs only.
        payment_intent_id = dispute_data.get("payment_intent") or ""
        charge_id = dispute_data.get("charge", "")
        dispute_id = dispute_data.get("id", "")
        reason = dispute_data.get("reason", "")

        logger.warning(
            "Stripe dispute created",
            extra={
                "payment_intent_id": payment_intent_id,
                "charge_id": charge_id,
                "dispute_id": dispute_id,
                "reason": reason,
            },
        )

        if not payment_intent_id:
            logger.error(
                "charge.dispute.created event missing payment_intent: %s",
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
                .filter(payment_id=payment_intent_id)
                .first()
            )
            if order is None:
                logger.warning(
                    "No order found for disputed payment_intent %s "
                    "(charge=%s, dispute=%s)",
                    payment_intent_id,
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

        # Capture the active tenant schema BEFORE the lambda is queued.
        # ``transaction.on_commit`` fires after the surrounding
        # @with_tenant_schema_from_event schema_context has exited, so
        # without capture the dispatcher would stamp _schema_name=public
        # and the worker would run against the wrong schema.
        _schema = connection.schema_name
        transaction.on_commit(
            lambda oid=order.id, did=dispute_id, s=_schema: (
                send_dispute_notification_email.apply_async(
                    args=[oid, did], headers={"_schema_name": s}
                )
            )
        )

    except (KeyError, TypeError) as e:
        # Malformed payload only — see charge.refunded above. A swallowed
        # failure here flags the dispute in the DB and then loses the
        # staff notification, so nobody works the chargeback before
        # Stripe's evidence deadline.
        logger.error(
            "Malformed charge.dispute.created payload: %s", e, exc_info=True
        )


@djstripe_receiver("checkout.session.completed")
@with_tenant_schema_from_event
def handle_stripe_checkout_completed(sender, **kwargs):
    """Handle Stripe checkout session completion webhook.

    This receiver runs inside dj-stripe's webhook ``transaction.atomic``
    block, so processing errors must PROPAGATE (G0231), exactly as in
    ``handle_stripe_payment_succeeded``. Swallowing them let dj-stripe
    commit the ``Event`` row for a session the customer had already paid
    for: the mutations here roll back to their savepoint, Stripe's
    redelivery early-returns on the existing event id, and the order sits
    at PENDING until ``auto_cancel_stuck_pending_orders`` cancels it a day
    later with no refund and no alert. The sibling
    ``payment_intent.succeeded`` event is not a safety net — it resolves
    the order by ``payment_id``, which only ``mark_as_paid`` writes, inside
    the block that rolled back.
    """
    logger.debug("Processing checkout.session.completed webhook")

    try:
        event: Event = kwargs["event"]
        session_data = event.data["object"]
        session_id = session_data["id"]
        payment_intent_id = session_data.get("payment_intent")
        payment_status = session_data.get("payment_status")
        event_id = event.id
    except (KeyError, TypeError) as e:
        # Malformed payload — a redelivery carries the same bad body, so
        # propagating would only spin. Drop it, like the sibling handler.
        logger.error(
            "Malformed checkout.session.completed payload: %s",
            e,
            exc_info=True,
        )
        return

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
            from shipping.services import ShippingService

            # Settled-state guard: Stripe does not guarantee event
            # delivery order, so a delayed checkout.session.completed
            # must never un-refund / un-cancel an order that already
            # reached a settled financial state. Mirrors
            # OrderService.handle_payment_succeeded.
            # COMPLETED is excluded on purpose: a "paid" event landing on
            # an already-completed order is a redelivery, and processing
            # it again is a harmless no-op rather than a regression.
            if order.payment_status in (
                SETTLED_PAYMENT_STATUSES - {PaymentStatus.COMPLETED}
            ):
                logger.warning(
                    "Ignoring checkout.session.completed for order %s: "
                    "payment_status already %s",
                    order.id,
                    order.payment_status,
                )
                # Persist the webhook_processed flag set above so a
                # Stripe redelivery short-circuits on the idempotency
                # check instead of re-running this guard (harmless, but
                # avoids duplicate warning logs on every retry).
                order.save(update_fields=["metadata"])
                return

            order.mark_as_paid(
                payment_id=payment_intent_id, payment_method="stripe"
            )

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

            if order.status == OrderStatus.CANCELED:
                # Payment landed for an already-CANCELED order (the
                # customer cancelled before the webhook, or the two
                # raced). Record the receipt for reconciliation and
                # page staff (ERROR is the monitored channel) for a
                # manual refund — but do NOT advance status or mint a
                # shipment for a cancelled order. Mirrors
                # handle_payment_succeeded (G0281).
                order.metadata["payment_after_cancel"] = {
                    "payment_id": payment_intent_id,
                    "recorded_at": timezone.now().isoformat(),
                }
                order.save(update_fields=["metadata"])
                logger.error(
                    "Payment %s received via checkout session for "
                    "CANCELED order %s — manual refund required; NOT "
                    "dispatching shipment creation",
                    payment_intent_id,
                    order.id,
                )
                publish_payment_status(order)
                return

            if order.status == OrderStatus.PENDING:
                OrderService.update_order_status(order, OrderStatus.PROCESSING)

            # Stripe's guidance is to fulfil hosted Checkout Sessions on
            # checkout.session.completed (not payment_intent.succeeded).
            # Dispatch the courier task here so a Stripe-Checkout order
            # isn't left paid-but-never-shipped when the PaymentIntent
            # event's payment_id lookup races/misses. Idempotent on the
            # shipment row; wrapped in on_commit by ShippingService.
            ShippingService.dispatch_create_shipment_task(order)

            publish_payment_status(order)

            logger.info(
                "Order %s marked as paid via checkout session %s",
                order_id,
                session_id,
            )

            # Payment confirmed via Stripe Checkout — send the
            # confirmation email now (idempotent).
            # ``_schema`` captured at lambda-build time, or the
            # worker would run against the wrong schema.
            _schema = connection.schema_name
            transaction.on_commit(
                lambda oid=order.id, s=_schema: (
                    send_order_confirmation_email.apply_async(
                        args=[oid], headers={"_schema_name": s}
                    )
                )
            )

        elif payment_status == "unpaid":
            # The same settled-state guard the "paid" arm carries, and
            # here it includes COMPLETED: writing PENDING over ANY
            # settled state is a regression. Stripe retries a failed
            # delivery 24 times — one attempt plus 23 hourly retries
            # until a 2xx — which reaches as far as
            # auto_cancel_stuck_pending_orders' own 24h default. A late
            # retry would otherwise put a cancelled order back to
            # "awaiting payment" and let the checkout endpoints open a
            # fresh provider session for it.
            if order.payment_status in SETTLED_PAYMENT_STATUSES:
                logger.warning(
                    "Ignoring unpaid checkout.session.completed for order "
                    "%s: payment_status already %s",
                    order.id,
                    order.payment_status,
                )
                order.save(update_fields=["metadata"])
                return

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


@djstripe_receiver("checkout.session.expired")
@with_tenant_schema_from_event
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
